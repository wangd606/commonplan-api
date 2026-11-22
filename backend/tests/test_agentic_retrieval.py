from app.retrieval.agentic import (
    AnswerGrade,
    EvidenceGrade,
    ModelGatewayAgenticAnswerModel,
    ModelUsage,
    run_agentic_answer,
)
from app.retrieval.answering import AnswerCitation, AnswerDraft, GroundedClaim


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


class RewriteThenAnswerModel:
    provider = "model_gateway"
    model = "commonplan-generation-v1"

    def __init__(self):
        self.grade_calls = 0

    def grade_evidence(self, query, citations):
        self.grade_calls += 1
        return EvidenceGrade(self.grade_calls > 1, "needs a focused query"), ModelUsage(10, 2, 12)

    def rewrite_query(self, query, citations):
        return "focused retrieval query", ModelUsage(8, 3, 11)

    def generate_answer(self, query, citations, feedback):
        return AnswerDraft((GroundedClaim("Supported answer", ("S1",)),)), ModelUsage(20, 5, 25)

    def grade_answer(self, query, citations, draft):
        return AnswerGrade(True, "all claims are supported"), ModelUsage(15, 2, 17)


def test_langgraph_rewrites_once_then_returns_a_graded_answer():
    result = run_agentic_answer(
        query="original query",
        retrieve=retrieval,
        model=RewriteThenAnswerModel(),
        max_citations=4,
        max_claims=3,
        max_query_rewrites=1,
        max_answer_retries=1,
    )

    assert result.final_query == "focused retrieval query"
    assert result.rewrites == 1
    assert result.answer_supported is True
    assert result.retrieval_ms == 24.0
    assert result.draft.claims[0].citation_ids == ("S1",)
    assert [step["node"] for step in result.steps] == [
        "retrieve", "grade_evidence", "rewrite_query", "retrieve",
        "grade_evidence", "generate_answer", "grade_answer",
    ]
    assert sum(item.total_tokens for item in result.usage) == 77


class NeverEnoughModel(RewriteThenAnswerModel):
    def grade_evidence(self, query, citations):
        return EvidenceGrade(False, "not enough evidence"), ModelUsage(5, 1, 6)


def test_langgraph_stops_after_the_bounded_rewrite():
    result = run_agentic_answer(
        query="unsupported",
        retrieve=retrieval,
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
        retrieve=retrieval,
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
