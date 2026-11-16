import pytest

from app.retrieval.answering import (
    AnswerCitation,
    AnswerDraft,
    AnswerGenerationError,
    ExtractiveGroundedAnswerProvider,
    GroundedClaim,
    ModelGatewayGroundedAnswerProvider,
    assemble_citations,
    render_answer,
    validate_draft,
)


def citation(citation_id: str = "S1") -> AnswerCitation:
    return AnswerCitation(
        id=citation_id,
        target="issue:issue-1",
        source_id="issue-1",
        source_type="issue",
        excerpt="Google login failed because the redirect URI used the wrong port.",
    )


def test_citation_assembly_deduplicates_target_and_bounds_content():
    rows = [{
        "citation_target": "issue:issue-1",
        "source_id": "issue-1",
        "source_type": "issue",
        "content": "A" * 2000,
        "heading_path": None,
        "page_from": None,
        "page_to": None,
        "context": [],
    }] * 2

    result = assemble_citations(rows, limit=6, excerpt_limit=80)

    assert len(result) == 1
    assert result[0].id == "S1"
    assert result[0].target == "issue:issue-1"
    assert len(result[0].excerpt) == 81


def test_invalid_or_missing_model_citations_are_rejected():
    with pytest.raises(AnswerGenerationError):
        validate_draft(
            AnswerDraft((GroundedClaim("Unsupported", ("S9",)),)),
            [citation()],
            max_claims=5,
        )
    with pytest.raises(AnswerGenerationError):
        validate_draft(
            AnswerDraft((GroundedClaim("Uncited", ()),)),
            [citation()],
            max_claims=5,
        )


def test_extractive_answer_is_composed_only_from_evidence():
    provider = ExtractiveGroundedAnswerProvider()

    draft = validate_draft(
        provider.generate("Why did Google login fail?", [citation()]),
        [citation()],
        max_claims=5,
    )

    assert draft.claims[0].text in citation().excerpt
    assert render_answer(draft).endswith("[S1]")


def test_model_gateway_requires_structured_known_citations(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": (
                    '{"claims":[{"text":"The callback used the wrong port.",'
                    '"citation_ids":["S1"]}]}'
                )}}]
            }

    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr("app.retrieval.answering.httpx.post", fake_post)
    provider = ModelGatewayGroundedAnswerProvider(
        "http://gateway/v1", "secret", "commonplan-generation-v1",
        timeout_seconds=10,
    )

    draft = provider.generate("Why?", [citation()])
    validated = validate_draft(draft, [citation()], max_claims=5)

    assert validated.claims[0].citation_ids == ("S1",)
    assert captured["url"] == "http://gateway/v1/chat/completions"
    assert captured["json"]["temperature"] == 0
    assert "untrusted data" in captured["json"]["messages"][0]["content"]
