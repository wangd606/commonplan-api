from app.retrieval.agentic import (
    AnswerGrade,
    EvidenceGrade,
    ModelGatewayAgenticAnswerModel,
    ModelUsage,
    QueryPlan,
    run_agentic_answer,
)
from app.retrieval.answering import AnswerCitation, AnswerDraft, GroundedClaim
from app.retrieval.workflow import run_hybrid_retrieval


def retrieval(query: str) -> dict:
    return {
        "fused_results": [{
            "citation_target": "issue:issue-1",
            "source_id": "issue-1",
            "source_type": "issue",
            "content": f"Evidence selected for {query}",
            "context": [],
            "heading_path": None,
            "page_from": None,
            "page_to": None,
        }],
        "pipeline": {"timings_ms": {"total": 12.0}},
    }


class FakeRetrievalRuntime:
    def __init__(self):
        self.current_query = ""

    def lexical_retrieve(self, query):
        self.current_query = query
        return [f"lexical:{query}"]

    def embed_query(self, query):
        return [1.0, 0.0], "test-profile"

    def vector_retrieve(self, query_embedding, profile_key):
        return [f"vector:{self.current_query}"]

    def fuse(self, vector_hits, lexical_hits):
        return [f"fused:{self.current_query}"]

    def expand_context(self, candidates):
        return {}

    def rerank(self, candidates, contexts):
        return [f"reranked:{self.current_query}"]

    def build_result(
        self, profile_key, vector_hits, lexical_hits, fused_hits, timings,
    ):
        result = retrieval(self.current_query)
        result["pipeline"]["timings_ms"] = timings
        return result


def test_hybrid_retrieval_is_fully_orchestrated_by_langgraph():
    result = run_hybrid_retrieval("original query", FakeRetrievalRuntime())

    assert result.retrieval["fused_results"][0]["content"].endswith("original query")
    assert [step["node"] for step in result.steps] == [
        "lexical_retrieve", "embed_query", "vector_retrieve", "fuse_rrf",
        "expand_context", "rerank_results",
    ]


class RewriteThenAnswerModel:
    provider = "model_gateway"
    model = "commonplan-generation-v1"

    def __init__(self):
        self.grade_calls = 0

    def plan_query(self, query, available_tools, feedback, prior_evidence):
        planned = "focused retrieval query" if feedback else query
        return QueryPlan((planned,), ("internal_knowledge",), "test plan"), ModelUsage(8, 3, 11)

    def rerank_evidence(self, query, citations, limit):
        return [item.id for item in citations[:limit]], ModelUsage(3, 1, 4)

    def grade_evidence(self, query, citations):
        self.grade_calls += 1
        return EvidenceGrade(self.grade_calls > 1, "needs a focused query"), ModelUsage(10, 2, 12)

    def generate_answer(self, query, citations, feedback):
        return AnswerDraft((GroundedClaim("Supported answer", ("S1",)),)), ModelUsage(20, 5, 25)

    def grade_answer(self, query, citations, draft):
        return AnswerGrade("supported", "all claims are supported"), ModelUsage(15, 2, 17)


def test_langgraph_rewrites_once_then_returns_a_graded_answer():
    result = run_agentic_answer(
        query="original query",
        retrieval_runtime=FakeRetrievalRuntime(),
        model=RewriteThenAnswerModel(),
        max_citations=4,
        max_claims=3,
        max_query_rewrites=1,
        max_answer_retries=1,
    )

    assert result.final_query == "focused retrieval query"
    assert result.rewrites == 1
    assert result.answer_supported is True
    assert result.retrieval_ms > 0
    assert result.draft.claims[0].citation_ids == ("S1",)
    nodes = [step["node"] for step in result.steps]
    assert nodes.count("plan_query") == 2
    assert nodes.count("execute_tools") == 2
    assert nodes.count("rerank_evidence") == 2
    assert nodes[-2:] == ["generate_answer", "grade_answer"]


class NeverEnoughModel(RewriteThenAnswerModel):
    def grade_evidence(self, query, citations):
        return EvidenceGrade(False, "not enough evidence"), ModelUsage(5, 1, 6)


def test_langgraph_stops_after_the_bounded_rewrite():
    result = run_agentic_answer(
        query="unsupported",
        retrieval_runtime=FakeRetrievalRuntime(),
        model=NeverEnoughModel(),
        max_citations=4,
        max_claims=3,
        max_query_rewrites=1,
        max_answer_retries=1,
    )

    assert result.rewrites == 1
    assert result.answer_supported is False
    assert result.draft.claims == ()
    assert result.steps[-1]["node"] == "insufficient"
    assert all(step["node"] != "generate_answer" for step in result.steps)


class RetryInvalidAnswerModel(RewriteThenAnswerModel):
    def grade_evidence(self, query, citations):
        return EvidenceGrade(True, "enough evidence"), ModelUsage(4, 1, 5)

    def generate_answer(self, query, citations, feedback):
        if feedback is None:
            return AnswerDraft((GroundedClaim("Unsafe", ("UNKNOWN",)),)), ModelUsage(8, 2, 10)
        return AnswerDraft((GroundedClaim("Supported answer", ("S1",)),)), ModelUsage(8, 2, 10)


def test_langgraph_retries_an_invalid_citation_once_without_exposing_it():
    result = run_agentic_answer(
        query="question",
        retrieval_runtime=FakeRetrievalRuntime(),
        model=RetryInvalidAnswerModel(),
        max_citations=4,
        max_claims=3,
        max_query_rewrites=1,
        max_answer_retries=1,
    )

    assert result.answer_supported is True
    assert result.answer_retries == 1
    assert result.draft.claims[0].text == "Supported answer"
    assert result.draft.claims[0].citation_ids == ("S1",)
    assert [step["node"] for step in result.steps].count("generate_answer") == 2


class DecomposingModel(RewriteThenAnswerModel):
    def plan_query(self, query, available_tools, feedback, prior_evidence):
        return QueryPlan(
            ("TA-1 failure cause", "TA-1 remediation"),
            ("internal_knowledge",),
            "two independently searchable parts",
        ), ModelUsage(8, 3, 11)

    def grade_evidence(self, query, citations):
        return EvidenceGrade(True, "combined evidence is enough"), ModelUsage(4, 1, 5)


def test_answer_graph_decomposes_query_and_merges_tool_evidence():
    runtime = FakeRetrievalRuntime()
    result = run_agentic_answer(
        query="Why did TA-1 fail and how was it fixed?",
        retrieval_runtime=runtime,
        model=DecomposingModel(),
        max_citations=4,
        max_claims=3,
        max_query_rewrites=0,
        max_answer_retries=0,
    )

    assert result.final_query == "TA-1 failure cause | TA-1 remediation"
    execute = next(step for step in result.steps if step["node"] == "execute_tools")
    assert execute["decision"] == "2 tool calls"
    assert [step["node"] for step in result.steps].count("lexical_retrieve") == 2


class EvidenceProblemAfterAnswerModel(RewriteThenAnswerModel):
    def grade_evidence(self, query, citations):
        return EvidenceGrade(True, "appears sufficient"), ModelUsage(4, 1, 5)

    def grade_answer(self, query, citations, draft):
        if self.grade_calls == 0:
            self.grade_calls += 1
            return AnswerGrade("evidence_problem", "missing remediation evidence"), ModelUsage(4, 1, 5)
        return AnswerGrade("supported", "supported after replan"), ModelUsage(4, 1, 5)


def test_answer_evidence_problem_routes_back_to_planner():
    result = run_agentic_answer(
        query="question",
        retrieval_runtime=FakeRetrievalRuntime(),
        model=EvidenceProblemAfterAnswerModel(),
        max_citations=4,
        max_claims=3,
        max_query_rewrites=1,
        max_answer_retries=1,
    )

    assert result.answer_supported is True
    assert result.rewrites == 1
    assert result.answer_retries == 0
    assert [step["node"] for step in result.steps].count("plan_query") == 2


def test_model_gateway_agent_uses_structured_untrusted_evidence_contract(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": (
                    '{"sufficient":true,"reason":"direct support"}'
                )}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            }

    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr("app.retrieval.agentic.httpx.post", fake_post)
    model = ModelGatewayAgenticAnswerModel(
        "http://gateway/v1", "secret", "commonplan-generation-v1",
        timeout_seconds=30,
    )

    grade, usage = model.grade_evidence(
        "Why?",
        [AnswerCitation(
            id="S1", target="issue:issue-1", source_id="issue-1",
            source_type="issue", excerpt="Direct support",
        )],
    )

    assert grade.sufficient is True
    assert usage.total_tokens == 16
    assert captured["url"] == "http://gateway/v1/chat/completions"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert "untrusted data" in captured["json"]["messages"][0]["content"]
    assert "Terse field values are sufficient" in captured["json"]["messages"][0]["content"]
    assert captured["json"]["messages"][0]["content"].endswith("/no_think")
