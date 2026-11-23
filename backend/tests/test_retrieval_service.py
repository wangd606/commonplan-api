from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.main import app
from app.config import settings
from app.models import User
from app.repositories.retrieval_repository import SqlAlchemyRetrievalRepository
from app.retrieval.answering import (
    AnswerDraft,
    ExtractiveGroundedAnswerProvider,
    GroundedClaim,
)
from app.retrieval.agentic import AnswerGrade, EvidenceGrade, ModelUsage, QueryPlan
from app.retrieval.types import ContextPassage, RetrievalScope, SearchHit
from app.schemas import (
    GroundedAnswerResponse,
    HybridRetrievalSearchResponse,
    RetrievalSearchResponse,
)
from app.services.retrieval_service import RetrievalForbidden, RetrievalService


class FakeTeam:
    def __init__(self, team_id):
        self.id = team_id


class FakeWorkspaces:
    def __init__(self, team_ids):
        self.team_ids = team_ids

    def get_workspace(self, _user, _workspace_id):
        return object(), object()

    def list_teams(self, _user, _workspace_id):
        return [(FakeTeam(team_id), None) for team_id in self.team_ids]


class FakeEmbeddingProvider:
    provider = "model_gateway"
    model = "commonplan-embedding-v1:Qwen/Qwen3-Embedding-0.6B@revision"
    dimensions = 3

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return [[1.0, 0.0, 0.0] for _text in texts]


class FakeAnswerProvider:
    provider = "test"
    model = "test-grounded-v1"
    output_mode = "answer"

    def generate(self, query, citations):
        assert query == "Why did TA-1 fail?"
        assert citations[0].target == "issue:issue-1"
        return AnswerDraft((GroundedClaim(
            "TA-1 failed after the ONNX runtime upgrade.", ("S1",),
        ),))


class FakeAgenticModel:
    provider = "model_gateway"
    model = "commonplan-generation-v1"

    def plan_query(self, query, available_tools, feedback, prior_evidence):
        return QueryPlan((query,), ("internal_knowledge",), "direct lookup"), ModelUsage(4, 1, 5)

    def rerank_evidence(self, query, citations, limit):
        return [item.id for item in citations[:limit]], ModelUsage(4, 1, 5)

    def grade_evidence(self, query, citations):
        return EvidenceGrade(True, "enough"), ModelUsage(10, 2, 12)

    def rewrite_query(self, query, citations):
        raise AssertionError("rewrite should not be called")

    def generate_answer(self, query, citations, feedback):
        return AnswerDraft((GroundedClaim(
            "TA-1 failed after the ONNX runtime upgrade.", ("S1",),
        ),)), ModelUsage(20, 5, 25)

    def grade_answer(self, query, citations, draft):
        return AnswerGrade("supported", "supported"), ModelUsage(12, 2, 14)


class FakeRepository:
    def __init__(self):
        self.vector_calls = []
        self.lexical_calls = []
        self.context_calls = []

    def vector_search(self, vector, profile_key, scope, limit):
        self.vector_calls.append((vector, profile_key, scope, limit))
        return [hit("vector", scope)]

    def lexical_search(self, query, scope, limit):
        self.lexical_calls.append((query, scope, limit))
        return [hit("lexical", scope)]

    def expand_context(self, node_ids, scope, neighbor_window, include_parent):
        self.context_calls.append((node_ids, scope, neighbor_window, include_parent))
        return {
            node_id: [ContextPassage(
                passage_id=f"context-{node_id}", relation="next",
                content="TA-1 failed after the ONNX runtime upgrade", ordinal=2,
            )]
            for node_id in node_ids
        }


def hit(method, scope):
    return SearchHit(
        node_id=f"{method}-node",
        source_id="issue-1",
        source_type="issue",
        content="TA-1 login failure",
        method=method,
        score=0.8,
        workspace_id=scope.workspace_id,
        team_id=scope.team_ids[0],
        issue_id="issue-1",
    )


def user():
    now = datetime.now(timezone.utc)
    return User(
        id=1,
        email="owner@example.com",
        name="Owner",
        auth_issuer="issuer",
        auth_subject="subject",
        is_deleted=False,
        created_at=now,
        updated_at=now,
    )


def test_both_search_channels_share_server_derived_scope_and_profile():
    repository = FakeRepository()
    embedding = FakeEmbeddingProvider()
    service = RetrievalService(
        repository,
        FakeWorkspaces(["team-b", "team-a"]),
        embedding,
    )

    result = service.search(
        user(),
        "workspace-1",
        query="Why did TA-1 fail?",
        mode="both",
        team_ids=["team-a"],
        project_id=None,
        issue_id="issue-1",
        limit=5,
    )

    assert result["scope"] == {
        "workspace_id": "workspace-1",
        "team_ids": ["team-a"],
        "project_id": None,
        "issue_id": "issue-1",
    }
    assert [row.method for row in result["vector_results"]] == ["vector"]
    assert [row.method for row in result["lexical_results"]] == ["lexical"]
    assert embedding.calls == [["Why did TA-1 fail?"]]
    assert repository.vector_calls[0][1].endswith(":3")
    assert repository.vector_calls[0][2] == repository.lexical_calls[0][1]
    serialized = RetrievalSearchResponse.model_validate(result).model_dump()
    assert serialized["vector_results"][0]["citation_target"] == "issue:issue-1"


def test_lexical_only_search_does_not_call_embedding_model():
    repository = FakeRepository()
    embedding = FakeEmbeddingProvider()
    service = RetrievalService(repository, FakeWorkspaces(["team-a"]), embedding)

    result = service.search(
        user(), "workspace-1", query="TA-1", mode="lexical",
        team_ids=None, project_id=None, issue_id=None, limit=10,
    )

    assert embedding.calls == []
    assert repository.vector_calls == []
    assert len(result["lexical_results"]) == 1
    assert result["embedding_profile_key"] is None


def test_requested_team_must_be_authorized_before_repository_call():
    repository = FakeRepository()
    service = RetrievalService(
        repository,
        FakeWorkspaces(["team-a"]),
        FakeEmbeddingProvider(),
    )

    with pytest.raises(RetrievalForbidden):
        service.search(
            user(), "workspace-1", query="private", mode="both",
            team_ids=["team-b"], project_id=None, issue_id=None, limit=10,
        )

    assert repository.vector_calls == []
    assert repository.lexical_calls == []


def test_hybrid_search_fuses_channels_and_expands_context():
    repository = FakeRepository()
    service = RetrievalService(
        repository, FakeWorkspaces(["team-a"]), FakeEmbeddingProvider(),
    )

    result = service.hybrid_search(
        user(), "workspace-1", query="TA-1 ONNX failure",
        team_ids=None, project_id=None, issue_id=None, limit=5,
        candidate_limit=20, neighbor_window=1, include_parent=True,
    )

    assert len(result["vector_results"]) == 1
    assert len(result["lexical_results"]) == 1
    assert len(result["fused_results"]) == 2
    assert result["fused_results"][0]["context"][0]["relation"] == "next"
    assert result["pipeline"]["orchestrator"] == "langgraph-retrieval-v1"
    assert result["pipeline"]["fusion"] == "rrf-k60"
    assert result["pipeline"]["reranker"] == "postgres-lexical-rank-v2"
    serialized = HybridRetrievalSearchResponse.model_validate(result).model_dump()
    assert serialized["fused_results"][0]["citation_target"] == "issue:issue-1"
    assert repository.vector_calls[0][3] == 20
    assert repository.lexical_calls[0][2] == 20
    assert repository.context_calls[0][2:] == (1, True)


def test_grounded_answer_uses_r2_evidence_and_exposes_structured_citations():
    repository = FakeRepository()
    service = RetrievalService(
        repository,
        FakeWorkspaces(["team-a"]),
        FakeEmbeddingProvider(),
        FakeAnswerProvider(),
    )

    result = service.answer(
        user(), "workspace-1", query="Why did TA-1 fail?",
        team_ids=None, project_id=None, issue_id=None, limit=5,
        candidate_limit=20, neighbor_window=1, include_parent=True,
        max_citations=6,
    )

    assert result["answer"].endswith("[S1]")
    assert result["claims"] == [{
        "text": "TA-1 failed after the ONNX runtime upgrade.",
        "citation_ids": ["S1"],
    }]
    assert result["citations"][0]["target"] == "issue:issue-1"
    assert result["generation"]["grounded"] is True
    assert result["generation"]["status"] == "answered"
    assert result["generation"]["grounding_status"] == "citation_validated"
    assert result["retrieval"]["pipeline"]["fusion"] == "rrf-k60"
    serialized = GroundedAnswerResponse.model_validate(result).model_dump()
    assert serialized["citations"][0]["id"] == "S1"


def test_extractive_mode_returns_evidence_without_presenting_it_as_an_answer():
    service = RetrievalService(
        FakeRepository(),
        FakeWorkspaces(["team-a"]),
        FakeEmbeddingProvider(),
        ExtractiveGroundedAnswerProvider(),
    )

    result = service.answer(
        user(), "workspace-1", query="An unrelated question",
        team_ids=None, project_id=None, issue_id=None, limit=5,
        candidate_limit=20, neighbor_window=1, include_parent=True,
        max_citations=6,
    )

    assert result["answer"] is None
    assert result["claims"] == []
    assert result["citations"]
    assert result["generation"]["status"] == "evidence_only"
    assert result["generation"]["grounding_status"] == "not_generated"
    assert result["generation"]["grounded"] is False
    assert result["generation"]["insufficient_evidence"] is False
    GroundedAnswerResponse.model_validate(result)


def test_agentic_answer_exposes_bounded_trace_and_usage(monkeypatch):
    monkeypatch.setattr(settings, "rag_answer_strategy", "agentic")
    service = RetrievalService(
        FakeRepository(),
        FakeWorkspaces(["team-a"]),
        FakeEmbeddingProvider(),
        FakeAnswerProvider(),
        FakeAgenticModel(),
    )

    result = service.answer(
        user(), "workspace-1", query="Why did TA-1 fail?",
        team_ids=None, project_id=None, issue_id=None, limit=5,
        candidate_limit=20, neighbor_window=1, include_parent=True,
        max_citations=6,
    )

    assert result["generation"]["status"] == "answered"
    assert result["generation"]["grounding_status"] == "agent_validated"
    assert result["agent"]["strategy"] == "agentic-answer-langgraph-v3"
    assert result["agent"]["model_calls"] == 5
    assert result["agent"]["usage"]["total_tokens"] == 61
    assert [step["node"] for step in result["agent"]["steps"]] == [
        "plan_query", "execute_tools", "lexical_retrieve", "embed_query",
        "vector_retrieve", "fuse_rrf", "expand_context", "rerank_results",
        "merge_evidence", "rerank_evidence",
        "grade_evidence", "generate_answer", "grade_answer",
    ]
    GroundedAnswerResponse.model_validate(result)


class EmptyRows:
    def all(self):
        return []


class CapturingSession:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return EmptyRows()


def compiled(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def test_repository_places_acl_and_lifecycle_filters_inside_both_queries():
    session = CapturingSession()
    repository = SqlAlchemyRetrievalRepository(session)
    scope = RetrievalScope(
        workspace_id="workspace-1",
        team_ids=("team-a", "team-b"),
        project_id="project-1",
    )

    repository.vector_search([1.0, 0.0, 0.0], "profile", scope, 5)
    repository.lexical_search("TA-1", scope, 5)

    iterative_sql = compiled(session.statements[0])
    vector_sql = compiled(session.statements[1])
    lexical_sql = compiled(session.statements[2])
    assert "hnsw.iterative_scan = 'strict_order'" in iterative_sql
    for sql in (vector_sql, lexical_sql):
        assert "knowledge_nodes.workspace_id" in sql
        assert "knowledge_nodes.team_id IN" in sql
        assert "knowledge_sources.workspace_id" in sql
        assert "knowledge_sources.team_id IN" in sql
        assert "knowledge_nodes.project_id" in sql
        assert "knowledge_nodes.indexable IS true" in sql
        assert "knowledge_sources.status" in sql
        assert "knowledge_sources.deleted_at IS NULL" in sql
    assert "embedding_profiles.profile_key" in vector_sql
    assert "knowledge_embeddings.embedding_profile_key" in vector_sql
    assert "knowledge_sources.active_embedding_profile_id" in vector_sql
    assert "vector_dims" in vector_sql
    assert "to_tsvector" in lexical_sql
    assert "similarity" in lexical_sql


def test_search_hit_exposes_stable_citation_target():
    scope = RetrievalScope("workspace-1", ("team-a",))
    assert hit("vector", scope).citation_target == "issue:issue-1"


def test_retrieval_route_is_fail_closed_without_access_token():
    response = TestClient(app).post(
        "/api/v1/workspaces/workspace-1/retrieval/search",
        json={"query": "TA-1", "mode": "lexical"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_hybrid_retrieval_route_is_fail_closed_without_access_token():
    response = TestClient(app).post(
        "/api/v1/workspaces/workspace-1/retrieval/hybrid-search",
        json={"query": "TA-1"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_grounded_answer_route_is_fail_closed_without_access_token():
    response = TestClient(app).post(
        "/api/v1/workspaces/workspace-1/retrieval/answer",
        json={"query": "TA-1"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
