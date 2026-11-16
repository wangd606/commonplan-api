from dataclasses import asdict, replace
from time import perf_counter
from typing import Annotated, Literal

import httpx
from fastapi import Depends

from app.config import settings
from app.ingestion.embedding_profiles import embedding_profile_key
from app.ingestion.embeddings import EmbeddingProvider, create_embedding_provider
from app.models import User
from app.repositories.retrieval_repository import RetrievalRepository, RetrievalRepositoryDep
from app.retrieval.answering import (
    AnswerGenerationError,
    GroundedAnswerProvider,
    assemble_citations,
    create_answer_provider,
    render_answer,
    validate_draft,
)
from app.retrieval.fusion import deterministic_rerank, reciprocal_rank_fusion
from app.retrieval.types import RetrievalScope, SearchHit
from app.services.workspace_service import WorkspaceService, WorkspaceServiceDep


class RetrievalNotFound(Exception):
    pass


class RetrievalForbidden(Exception):
    pass


class RetrievalUnavailable(Exception):
    pass


class RetrievalService:
    """Derive caller scope before delegating all database work to the repository."""

    def __init__(
        self,
        repository: RetrievalRepository,
        workspaces: WorkspaceService,
        embedding_provider: EmbeddingProvider,
        answer_provider: GroundedAnswerProvider | None = None,
    ):
        self.repository = repository
        self.workspaces = workspaces
        self.embedding_provider = embedding_provider
        self.answer_provider = answer_provider

    def search(
        self,
        user: User,
        workspace_id: str,
        *,
        query: str,
        mode: Literal["vector", "lexical", "both"],
        team_ids: list[str] | None,
        project_id: str | None,
        issue_id: str | None,
        limit: int,
    ) -> dict:
        scope = self._scope(user, workspace_id, team_ids, project_id, issue_id)
        if not scope.team_ids:
            return self._result(scope, None, [], [])

        lexical_hits: list[SearchHit] = []
        vector_hits: list[SearchHit] = []
        if mode in {"lexical", "both"}:
            lexical_hits = self.repository.lexical_search(query, scope, limit)

        profile_key = None
        if mode in {"vector", "both"}:
            try:
                vectors = self.embedding_provider.embed([query])
            except (httpx.HTTPError, ValueError) as exc:
                raise RetrievalUnavailable("Query embedding is unavailable") from exc
            if len(vectors) != 1:
                raise RetrievalUnavailable("Query embedding returned an invalid result")
            profile_key = embedding_profile_key(
                self.embedding_provider.provider,
                self.embedding_provider.model,
                self.embedding_provider.dimensions,
            )
            vector_hits = self.repository.vector_search(
                vectors[0], profile_key, scope, limit
            )
        return self._result(scope, profile_key, vector_hits, lexical_hits)

    def hybrid_search(
        self,
        user: User,
        workspace_id: str,
        *,
        query: str,
        team_ids: list[str] | None,
        project_id: str | None,
        issue_id: str | None,
        limit: int,
        candidate_limit: int,
        neighbor_window: int,
        include_parent: bool,
    ) -> dict:
        started = perf_counter()
        scope = self._scope(user, workspace_id, team_ids, project_id, issue_id)
        if not scope.team_ids:
            return self._hybrid_result(scope, None, [], [], [], {
                "total": 0.0, "lexical": 0.0, "embedding": 0.0,
                "vector": 0.0, "expansion_rerank": 0.0,
            }, candidate_limit, neighbor_window, include_parent)

        phase = perf_counter()
        lexical_hits = self.repository.lexical_search(query, scope, candidate_limit)
        lexical_ms = (perf_counter() - phase) * 1000

        phase = perf_counter()
        try:
            vectors = self.embedding_provider.embed([query])
        except (httpx.HTTPError, ValueError) as exc:
            raise RetrievalUnavailable("Query embedding is unavailable") from exc
        if len(vectors) != 1:
            raise RetrievalUnavailable("Query embedding returned an invalid result")
        embedding_ms = (perf_counter() - phase) * 1000
        profile_key = embedding_profile_key(
            self.embedding_provider.provider,
            self.embedding_provider.model,
            self.embedding_provider.dimensions,
        )

        phase = perf_counter()
        vector_hits = self.repository.vector_search(
            vectors[0], profile_key, scope, candidate_limit
        )
        vector_ms = (perf_counter() - phase) * 1000

        phase = perf_counter()
        fused = reciprocal_rank_fusion(vector_hits, lexical_hits)
        contexts = self.repository.expand_context(
            [candidate.hit.node_id for candidate in fused],
            scope,
            neighbor_window,
            include_parent,
        )
        bounded_contexts = {
            node_id: [
                replace(passage, content=self._bounded(passage.content))
                for passage in passages
            ]
            for node_id, passages in contexts.items()
        }
        reranked = deterministic_rerank(query, fused, bounded_contexts)[:limit]
        expansion_ms = (perf_counter() - phase) * 1000
        timings = {
            "lexical": lexical_ms,
            "embedding": embedding_ms,
            "vector": vector_ms,
            "expansion_rerank": expansion_ms,
            "total": (perf_counter() - started) * 1000,
        }
        return self._hybrid_result(
            scope,
            profile_key,
            vector_hits[:limit],
            lexical_hits[:limit],
            reranked,
            timings,
            candidate_limit,
            neighbor_window,
            include_parent,
        )

    def answer(
        self,
        user: User,
        workspace_id: str,
        *,
        query: str,
        team_ids: list[str] | None,
        project_id: str | None,
        issue_id: str | None,
        limit: int,
        candidate_limit: int,
        neighbor_window: int,
        include_parent: bool,
        max_citations: int,
    ) -> dict:
        if self.answer_provider is None:
            raise RetrievalUnavailable("Answer generation is not configured")
        retrieval_started = perf_counter()
        retrieval = self.hybrid_search(
            user,
            workspace_id,
            query=query,
            team_ids=team_ids,
            project_id=project_id,
            issue_id=issue_id,
            limit=limit,
            candidate_limit=candidate_limit,
            neighbor_window=neighbor_window,
            include_parent=include_parent,
        )
        retrieval_ms = (perf_counter() - retrieval_started) * 1000
        citations = assemble_citations(
            retrieval["fused_results"],
            limit=min(max_citations, settings.rag_max_citations),
        )
        generation_started = perf_counter()
        try:
            draft = self.answer_provider.generate(query, citations)
            draft = validate_draft(
                draft,
                citations,
                max_claims=settings.rag_max_claims,
            )
        except (httpx.HTTPError, ValueError, AnswerGenerationError) as exc:
            raise RetrievalUnavailable("Grounded answer generation is unavailable") from exc
        generation_ms = (perf_counter() - generation_started) * 1000
        insufficient = not draft.claims
        rendered = render_answer(draft) if draft.claims else (
            "I couldn't find enough authorized evidence to answer this question."
        )
        return {
            "query": query,
            "answer": rendered,
            "claims": [
                {"text": claim.text, "citation_ids": list(claim.citation_ids)}
                for claim in draft.claims
            ],
            "citations": [asdict(citation) for citation in citations],
            "generation": {
                "provider": self.answer_provider.provider,
                "model": self.answer_provider.model,
                "grounded": not insufficient,
                "insufficient_evidence": insufficient,
                "timings_ms": {
                    "retrieval": round(retrieval_ms, 2),
                    "generation": round(generation_ms, 2),
                    "total": round(retrieval_ms + generation_ms, 2),
                },
            },
            "retrieval": retrieval,
        }

    def _scope(
        self,
        user: User,
        workspace_id: str,
        requested_team_ids: list[str] | None,
        project_id: str | None,
        issue_id: str | None,
    ) -> RetrievalScope:
        try:
            self.workspaces.get_workspace(user, workspace_id)
            authorized_rows = self.workspaces.list_teams(user, workspace_id)
        except Exception as exc:
            if exc.__class__.__name__.endswith("NotFound"):
                raise RetrievalNotFound from exc
            raise RetrievalForbidden from exc

        authorized = {team.id for team, _membership in authorized_rows}
        requested = set(requested_team_ids or [])
        if requested and not requested.issubset(authorized):
            raise RetrievalForbidden
        selected = sorted(requested or authorized)
        return RetrievalScope(
            workspace_id=workspace_id,
            team_ids=tuple(selected),
            project_id=project_id,
            issue_id=issue_id,
        )

    @staticmethod
    def _result(scope, profile_key, vector_hits, lexical_hits) -> dict:
        return {
            "scope": {
                "workspace_id": scope.workspace_id,
                "team_ids": list(scope.team_ids),
                "project_id": scope.project_id,
                "issue_id": scope.issue_id,
            },
            "embedding_profile_key": profile_key,
            "vector_results": vector_hits,
            "lexical_results": lexical_hits,
        }

    @staticmethod
    def _bounded(content: str, limit: int = 8000) -> str:
        return content if len(content) <= limit else f"{content[:limit]}\n…"

    @staticmethod
    def _hybrid_result(
        scope,
        profile_key,
        vector_hits,
        lexical_hits,
        fused_hits,
        timings,
        candidate_limit,
        neighbor_window,
        include_parent,
    ) -> dict:
        fused_results = []
        for row in fused_hits:
            fused_results.append({
                **asdict(row.hit),
                "citation_target": row.hit.citation_target,
                "rrf_score": row.rrf_score,
                "rerank_score": row.rerank_score,
                "final_score": row.final_score,
                "vector_rank": row.vector_rank,
                "lexical_rank": row.lexical_rank,
                "vector_score": row.vector_score,
                "lexical_score": row.lexical_score,
                "context": [asdict(passage) for passage in row.context],
            })
        return {
            "scope": {
                "workspace_id": scope.workspace_id,
                "team_ids": list(scope.team_ids),
                "project_id": scope.project_id,
                "issue_id": scope.issue_id,
            },
            "embedding_profile_key": profile_key,
            "vector_results": vector_hits,
            "lexical_results": lexical_hits,
            "fused_results": fused_results,
            "pipeline": {
                "fusion": "rrf-k60",
                "reranker": "deterministic-coverage-v1",
                "candidate_limit": candidate_limit,
                "neighbor_window": neighbor_window,
                "include_parent": include_parent,
                "timings_ms": {
                    key: round(value, 2) for key, value in timings.items()
                },
            },
        }


def get_query_embedding_provider() -> EmbeddingProvider:
    return create_embedding_provider()


QueryEmbeddingProviderDep = Annotated[
    EmbeddingProvider,
    Depends(get_query_embedding_provider),
]


def get_grounded_answer_provider() -> GroundedAnswerProvider:
    return create_answer_provider()


GroundedAnswerProviderDep = Annotated[
    GroundedAnswerProvider,
    Depends(get_grounded_answer_provider),
]


def get_retrieval_service(
    repository: RetrievalRepositoryDep,
    workspaces: WorkspaceServiceDep,
    embedding_provider: QueryEmbeddingProviderDep,
    answer_provider: GroundedAnswerProviderDep,
) -> RetrievalService:
    return RetrievalService(repository, workspaces, embedding_provider, answer_provider)


RetrievalServiceDep = Annotated[RetrievalService, Depends(get_retrieval_service)]
