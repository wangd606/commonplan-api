from dataclasses import asdict, dataclass, replace
import operator
from time import perf_counter
from typing import Annotated, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from app.ingestion.embedding_profiles import embedding_profile_key
from app.ingestion.embeddings import EmbeddingProvider
from app.repositories.retrieval_repository import RetrievalRepository
from app.retrieval.fusion import (
    FusionCandidate,
    postgres_lexical_rerank,
    reciprocal_rank_fusion,
)
from app.retrieval.types import ContextPassage, FusedHit, RetrievalScope, SearchHit


@dataclass(frozen=True)
class HybridRetrievalOptions:
    scope: RetrievalScope
    limit: int
    candidate_limit: int
    neighbor_window: int
    include_parent: bool


class HybridRetrievalRuntime(Protocol):
    """Side-effect boundary used by the LangGraph retrieval nodes."""

    def lexical_retrieve(self, query: str) -> list[SearchHit]: ...

    def embed_query(self, query: str) -> tuple[list[float], str]: ...

    def vector_retrieve(
        self, query_embedding: list[float], profile_key: str,
    ) -> list[SearchHit]: ...

    def fuse(
        self, vector_hits: list[SearchHit], lexical_hits: list[SearchHit],
    ) -> list[FusionCandidate]: ...

    def expand_context(
        self, candidates: list[FusionCandidate],
    ) -> dict[str, list[ContextPassage]]: ...

    def rerank(
        self,
        candidates: list[FusionCandidate],
        contexts: dict[str, list[ContextPassage]],
    ) -> list[FusedHit]: ...

    def build_result(
        self,
        profile_key: str,
        vector_hits: list[SearchHit],
        lexical_hits: list[SearchHit],
        fused_hits: list[FusedHit],
        timings: dict[str, float],
    ) -> dict: ...


class RepositoryHybridRetrievalRuntime:
    """Connect graph stages to providers and repositories without owning SQL."""

    def __init__(
        self,
        repository: RetrievalRepository,
        embedding_provider: EmbeddingProvider,
        options: HybridRetrievalOptions,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.options = options

    def lexical_retrieve(self, query: str) -> list[SearchHit]:
        return self.repository.lexical_search(
            query, self.options.scope, self.options.candidate_limit,
        )

    def embed_query(self, query: str) -> tuple[list[float], str]:
        vectors = self.embedding_provider.embed([query])
        if len(vectors) != 1:
            raise ValueError("Query embedding returned an invalid result")
        profile_key = embedding_profile_key(
            self.embedding_provider.provider,
            self.embedding_provider.model,
            self.embedding_provider.dimensions,
        )
        return vectors[0], profile_key

    def vector_retrieve(
        self, query_embedding: list[float], profile_key: str,
    ) -> list[SearchHit]:
        return self.repository.vector_search(
            query_embedding,
            profile_key,
            self.options.scope,
            self.options.candidate_limit,
        )

    def fuse(
        self, vector_hits: list[SearchHit], lexical_hits: list[SearchHit],
    ) -> list[FusionCandidate]:
        return reciprocal_rank_fusion(vector_hits, lexical_hits)

    def expand_context(
        self, candidates: list[FusionCandidate],
    ) -> dict[str, list[ContextPassage]]:
        contexts = self.repository.expand_context(
            [candidate.hit.node_id for candidate in candidates],
            self.options.scope,
            self.options.neighbor_window,
            self.options.include_parent,
        )
        return {
            node_id: [
                replace(passage, content=self._bounded(passage.content))
                for passage in passages
            ]
            for node_id, passages in contexts.items()
        }

    def rerank(
        self,
        candidates: list[FusionCandidate],
        contexts: dict[str, list[ContextPassage]],
    ) -> list[FusedHit]:
        return postgres_lexical_rerank(candidates, contexts)[:self.options.limit]

    def build_result(
        self,
        profile_key: str,
        vector_hits: list[SearchHit],
        lexical_hits: list[SearchHit],
        fused_hits: list[FusedHit],
        timings: dict[str, float],
    ) -> dict:
        fused_results = [{
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
        } for row in fused_hits]
        scope = self.options.scope
        return {
            "scope": {
                "workspace_id": scope.workspace_id,
                "team_ids": list(scope.team_ids),
                "project_id": scope.project_id,
                "issue_id": scope.issue_id,
            },
            "embedding_profile_key": profile_key,
            "vector_results": vector_hits[:self.options.limit],
            "lexical_results": lexical_hits[:self.options.limit],
            "fused_results": fused_results,
            "pipeline": {
                "orchestrator": "langgraph-retrieval-v1",
                "fusion": "rrf-k60",
                "reranker": "postgres-lexical-rank-v2",
                "candidate_limit": self.options.candidate_limit,
                "neighbor_window": self.options.neighbor_window,
                "include_parent": self.options.include_parent,
                "timings_ms": {
                    key: round(value, 2) for key, value in timings.items()
                },
            },
        }

    @staticmethod
    def _bounded(content: str, limit: int = 8000) -> str:
        return content if len(content) <= limit else f"{content[:limit]}\n…"


class RetrievalWorkflowState(TypedDict, total=False):
    current_query: str
    retrieval_attempt: int
    attempt_started_at: float
    lexical_hits: list[SearchHit]
    query_embedding: list[float]
    embedding_profile_key: str
    vector_hits: list[SearchHit]
    fusion_candidates: list[FusionCandidate]
    contexts: dict[str, list[ContextPassage]]
    fused_hits: list[FusedHit]
    retrieval: dict
    lexical_ms: float
    embedding_ms: float
    vector_ms: float
    fusion_ms: float
    context_ms: float
    rerank_ms: float
    steps: Annotated[list[dict], operator.add]
    retrieval_timings_ms: Annotated[list[float], operator.add]


RETRIEVAL_ENTRY = "lexical_retrieve"
RETRIEVAL_EXIT = "rerank_results"


class RetrievalWorkflowNodes:
    """LangGraph nodes shared by hybrid search and the agentic answer graph."""

    def __init__(self, runtime: HybridRetrievalRuntime) -> None:
        self.runtime = runtime

    def lexical_retrieve(self, state: RetrievalWorkflowState) -> dict:
        started = perf_counter()
        hits = self.runtime.lexical_retrieve(state["current_query"])
        return {
            "attempt_started_at": started,
            "lexical_hits": hits,
            "lexical_ms": (perf_counter() - started) * 1000,
            "steps": [{
                "node": "lexical_retrieve",
                "query": state["current_query"],
                "attempt": state.get("retrieval_attempt", 0),
                "decision": f"{len(hits)} candidates",
            }],
        }

    def embed_query(self, state: RetrievalWorkflowState) -> dict:
        started = perf_counter()
        vector, profile_key = self.runtime.embed_query(state["current_query"])
        return {
            "query_embedding": vector,
            "embedding_profile_key": profile_key,
            "embedding_ms": (perf_counter() - started) * 1000,
            "steps": [{
                "node": "embed_query",
                "query": state["current_query"],
                "attempt": state.get("retrieval_attempt", 0),
                "decision": f"{len(vector)} dimensions",
            }],
        }

    def vector_retrieve(self, state: RetrievalWorkflowState) -> dict:
        started = perf_counter()
        hits = self.runtime.vector_retrieve(
            state["query_embedding"], state["embedding_profile_key"],
        )
        return {
            "vector_hits": hits,
            "vector_ms": (perf_counter() - started) * 1000,
            "steps": [{
                "node": "vector_retrieve",
                "query": state["current_query"],
                "attempt": state.get("retrieval_attempt", 0),
                "decision": f"{len(hits)} candidates",
            }],
        }

    def fuse_rrf(self, state: RetrievalWorkflowState) -> dict:
        started = perf_counter()
        candidates = self.runtime.fuse(
            state["vector_hits"], state["lexical_hits"],
        )
        return {
            "fusion_candidates": candidates,
            "fusion_ms": (perf_counter() - started) * 1000,
            "steps": [{
                "node": "fuse_rrf",
                "query": state["current_query"],
                "attempt": state.get("retrieval_attempt", 0),
                "decision": f"{len(candidates)} fused candidates",
            }],
        }

    def expand_context(self, state: RetrievalWorkflowState) -> dict:
        started = perf_counter()
        contexts = self.runtime.expand_context(state["fusion_candidates"])
        passage_count = sum(len(passages) for passages in contexts.values())
        return {
            "contexts": contexts,
            "context_ms": (perf_counter() - started) * 1000,
            "steps": [{
                "node": "expand_context",
                "query": state["current_query"],
                "attempt": state.get("retrieval_attempt", 0),
                "decision": f"{passage_count} context passages",
            }],
        }

    def rerank_results(self, state: RetrievalWorkflowState) -> dict:
        started = perf_counter()
        hits = self.runtime.rerank(
            state["fusion_candidates"], state["contexts"],
        )
        rerank_ms = (perf_counter() - started) * 1000
        total_ms = (perf_counter() - state["attempt_started_at"]) * 1000
        timings = {
            "lexical": state["lexical_ms"],
            "embedding": state["embedding_ms"],
            "vector": state["vector_ms"],
            "fusion": state["fusion_ms"],
            "context": state["context_ms"],
            "rerank": rerank_ms,
            "expansion_rerank": (
                state["fusion_ms"] + state["context_ms"] + rerank_ms
            ),
            "total": total_ms,
        }
        retrieval = self.runtime.build_result(
            state["embedding_profile_key"],
            state["vector_hits"],
            state["lexical_hits"],
            hits,
            timings,
        )
        return {
            "fused_hits": hits,
            "rerank_ms": rerank_ms,
            "retrieval": retrieval,
            "retrieval_timings_ms": [total_ms],
            "steps": [{
                "node": "rerank_results",
                "query": state["current_query"],
                "attempt": state.get("retrieval_attempt", 0),
                "decision": f"{len(hits)} final candidates",
            }],
        }

    def add_to(self, graph: StateGraph) -> None:
        graph.add_node(RETRIEVAL_ENTRY, self.lexical_retrieve)
        graph.add_node("embed_query", self.embed_query)
        graph.add_node("vector_retrieve", self.vector_retrieve)
        graph.add_node("fuse_rrf", self.fuse_rrf)
        graph.add_node("expand_context", self.expand_context)
        graph.add_node(RETRIEVAL_EXIT, self.rerank_results)
        graph.add_edge(RETRIEVAL_ENTRY, "embed_query")
        graph.add_edge("embed_query", "vector_retrieve")
        graph.add_edge("vector_retrieve", "fuse_rrf")
        graph.add_edge("fuse_rrf", "expand_context")
        graph.add_edge("expand_context", RETRIEVAL_EXIT)


@dataclass(frozen=True)
class HybridRetrievalGraphResult:
    retrieval: dict
    steps: list[dict]


def run_hybrid_retrieval(
    query: str,
    runtime: HybridRetrievalRuntime,
) -> HybridRetrievalGraphResult:
    graph = StateGraph(RetrievalWorkflowState)
    RetrievalWorkflowNodes(runtime).add_to(graph)
    graph.add_edge(START, RETRIEVAL_ENTRY)
    graph.add_edge(RETRIEVAL_EXIT, END)
    result = graph.compile().invoke({
        "current_query": query,
        "retrieval_attempt": 0,
        "steps": [],
        "retrieval_timings_ms": [],
    })
    return HybridRetrievalGraphResult(
        retrieval=result["retrieval"],
        steps=result["steps"],
    )
