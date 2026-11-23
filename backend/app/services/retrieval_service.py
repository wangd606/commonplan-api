from dataclasses import asdict
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
from app.retrieval.agentic import (
    AgenticAnswerModel,
    ModelGatewayAgenticAnswerModel,
    run_agentic_answer,
)
from app.retrieval.types import RetrievalScope, SearchHit
from app.retrieval.workflow import (
    HybridRetrievalOptions,
    RepositoryHybridRetrievalRuntime,
    run_hybrid_retrieval,
)
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
        agentic_model: AgenticAnswerModel | None = None,
    ):
        self.repository = repository
        self.workspaces = workspaces
        self.embedding_provider = embedding_provider
        self.answer_provider = answer_provider
        self.agentic_model = agentic_model

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
        scope = self._scope(user, workspace_id, team_ids, project_id, issue_id)
        if not scope.team_ids:
            return self._empty_hybrid_result(
                scope, candidate_limit, neighbor_window, include_parent,
            )
        runtime = self._hybrid_runtime(
            scope,
            limit=limit,
            candidate_limit=candidate_limit,
            neighbor_window=neighbor_window,
            include_parent=include_parent,
        )
        try:
            return run_hybrid_retrieval(query, runtime).retrieval
        except (httpx.HTTPError, ValueError) as exc:
            raise RetrievalUnavailable("Hybrid retrieval is unavailable") from exc

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
        if settings.rag_answer_strategy == "agentic":
            return self._agentic_answer(
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
                max_citations=max_citations,
            )
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
        if self.answer_provider.output_mode == "evidence_only":
            draft = self.answer_provider.generate(query, citations)
        else:
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
        evidence_only = self.answer_provider.output_mode == "evidence_only" and bool(citations)
        insufficient = not citations or (
            self.answer_provider.output_mode == "answer" and not draft.claims
        )
        status = "evidence_only" if evidence_only else (
            "insufficient_evidence" if insufficient else "answered"
        )
        rendered = render_answer(draft) if draft.claims else None
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
                "status": status,
                "grounding_status": (
                    "citation_validated" if draft.claims else "not_generated"
                ),
                "grounded": bool(draft.claims),
                "insufficient_evidence": insufficient,
                "timings_ms": {
                    "retrieval": round(retrieval_ms, 2),
                    "generation": round(generation_ms, 2),
                    "total": round(retrieval_ms + generation_ms, 2),
                },
            },
            "retrieval": retrieval,
            "agent": None,
        }

    def _agentic_answer(
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
        if self.agentic_model is None:
            raise RetrievalUnavailable("Agentic answer generation is not configured")

        started = perf_counter()
        scope = self._scope(user, workspace_id, team_ids, project_id, issue_id)
        if not scope.team_ids:
            raise RetrievalForbidden
        runtime = self._hybrid_runtime(
            scope,
            limit=limit,
            candidate_limit=candidate_limit,
            neighbor_window=neighbor_window,
            include_parent=include_parent,
        )

        try:
            result = run_agentic_answer(
                query=query,
                retrieval_runtime=runtime,
                model=self.agentic_model,
                max_citations=min(max_citations, settings.rag_max_citations),
                max_claims=settings.rag_max_claims,
                max_query_rewrites=settings.rag_max_query_rewrites,
                max_answer_retries=settings.rag_max_answer_retries,
            )
        except (httpx.HTTPError, ValueError, AnswerGenerationError) as exc:
            raise RetrievalUnavailable("Agentic answer generation is unavailable") from exc

        total_ms = (perf_counter() - started) * 1000
        retrieval_ms = result.retrieval_ms
        prompt_tokens = sum(item.prompt_tokens for item in result.usage)
        completion_tokens = sum(item.completion_tokens for item in result.usage)
        total_tokens = sum(item.total_tokens for item in result.usage)
        supported = result.answer_supported and bool(result.draft.claims)
        return {
            "query": query,
            "answer": render_answer(result.draft) if supported else None,
            "claims": [
                {"text": claim.text, "citation_ids": list(claim.citation_ids)}
                for claim in result.draft.claims
            ] if supported else [],
            "citations": [asdict(citation) for citation in result.citations],
            "generation": {
                "provider": self.agentic_model.provider,
                "model": self.agentic_model.model,
                "status": "answered" if supported else "insufficient_evidence",
                "grounding_status": "agent_validated" if supported else "not_generated",
                "grounded": supported,
                "insufficient_evidence": not supported,
                "timings_ms": {
                    "retrieval": round(retrieval_ms, 2),
                    "generation": round(max(0.0, total_ms - retrieval_ms), 2),
                    "total": round(total_ms, 2),
                },
            },
            "retrieval": result.retrieval,
            "agent": {
                "strategy": "agentic-answer-langgraph-v3",
                "final_query": result.final_query,
                "rewrites": result.rewrites,
                "answer_retries": result.answer_retries,
                "model_calls": len(result.usage),
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                },
                "steps": result.steps,
            },
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

    def _hybrid_runtime(
        self,
        scope: RetrievalScope,
        *,
        limit: int,
        candidate_limit,
        neighbor_window,
        include_parent,
    ) -> RepositoryHybridRetrievalRuntime:
        return RepositoryHybridRetrievalRuntime(
            self.repository,
            self.embedding_provider,
            HybridRetrievalOptions(
                scope=scope,
                limit=limit,
                candidate_limit=candidate_limit,
                neighbor_window=neighbor_window,
                include_parent=include_parent,
            ),
        )

    @staticmethod
    def _empty_hybrid_result(
        scope: RetrievalScope,
        candidate_limit: int,
        neighbor_window: int,
        include_parent: bool,
    ) -> dict:
        return {
            "scope": {
                "workspace_id": scope.workspace_id,
                "team_ids": list(scope.team_ids),
                "project_id": scope.project_id,
                "issue_id": scope.issue_id,
            },
            "embedding_profile_key": None,
            "vector_results": [],
            "lexical_results": [],
            "fused_results": [],
            "pipeline": {
                "orchestrator": "langgraph-retrieval-v1",
                "fusion": "rrf-k60",
                "reranker": "postgres-lexical-rank-v2",
                "candidate_limit": candidate_limit,
                "neighbor_window": neighbor_window,
                "include_parent": include_parent,
                "timings_ms": {
                    "lexical": 0.0,
                    "embedding": 0.0,
                    "vector": 0.0,
                    "fusion": 0.0,
                    "context": 0.0,
                    "rerank": 0.0,
                    "expansion_rerank": 0.0,
                    "total": 0.0,
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


def get_agentic_answer_model() -> AgenticAnswerModel | None:
    if settings.rag_answer_strategy != "agentic":
        return None
    return ModelGatewayAgenticAnswerModel(
        settings.model_gateway_base_url,
        settings.model_gateway_api_key,
        settings.model_gateway_generation_alias,
        timeout_seconds=settings.rag_generation_timeout_seconds,
    )


AgenticAnswerModelDep = Annotated[
    AgenticAnswerModel | None,
    Depends(get_agentic_answer_model),
]


def get_retrieval_service(
    repository: RetrievalRepositoryDep,
    workspaces: WorkspaceServiceDep,
    embedding_provider: QueryEmbeddingProviderDep,
    answer_provider: GroundedAnswerProviderDep,
    agentic_model: AgenticAnswerModelDep,
) -> RetrievalService:
    return RetrievalService(
        repository, workspaces, embedding_provider, answer_provider, agentic_model,
    )


RetrievalServiceDep = Annotated[RetrievalService, Depends(get_retrieval_service)]
