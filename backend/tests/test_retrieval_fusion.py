from app.retrieval.fusion import postgres_lexical_rerank, reciprocal_rank_fusion
from app.retrieval.types import ContextPassage, RetrievalScope, SearchHit


SCOPE = RetrievalScope("workspace-1", ("team-1",))


def _hit(node_id: str, method: str, score: float, content: str) -> SearchHit:
    return SearchHit(
        node_id=node_id,
        source_id=f"source-{node_id}",
        source_type="issue",
        content=content,
        method=method,
        score=score,
        workspace_id=SCOPE.workspace_id,
        team_id=SCOPE.team_ids[0],
        issue_id=f"issue-{node_id}",
    )


def test_rrf_rewards_evidence_from_both_channels_without_mixing_raw_scores():
    vector = [
        _hit("a", "vector", 0.99, "semantic result"),
        _hit("b", "vector", 0.70, "shared result"),
    ]
    lexical = [
        _hit("b", "lexical", 0.04, "shared result"),
        _hit("c", "lexical", 8.50, "keyword result"),
    ]

    fused = reciprocal_rank_fusion(vector, lexical)

    assert [row.hit.node_id for row in fused] == ["b", "a", "c"]
    assert fused[0].vector_rank == 2
    assert fused[0].lexical_rank == 1
    assert fused[0].vector_score == 0.70
    assert fused[0].lexical_score == 0.04


def test_reranker_uses_postgres_lexical_score_and_keeps_expanded_context():
    candidates = reciprocal_rank_fusion(
        [_hit("a", "vector", 0.9, "general project notes")],
        [_hit("b", "lexical", 0.8, "登录回调故障")],
    )
    contexts = {
        "a": [ContextPassage(
            passage_id="page-a", relation="parent_page",
            content="Google 登录回调失败，因为 redirect URI 配置错误。",
        )],
        "b": [],
    }

    reranked = postgres_lexical_rerank(candidates, contexts)

    assert reranked[0].hit.node_id == "b"
    assert reranked[0].rerank_score == 0.8
    vector_only = next(row for row in reranked if row.hit.node_id == "a")
    assert vector_only.rerank_score == 0.0
    assert vector_only.context[0].relation == "parent_page"


def test_reranker_uses_lexical_score_to_break_equal_rrf_scores():
    candidates = reciprocal_rank_fusion(
        [
            _hit("a", "vector", 0.9, "first vector result"),
            _hit("b", "vector", 0.8, "second vector result"),
        ],
        [
            _hit("b", "lexical", 0.8, "strong lexical result"),
            _hit("a", "lexical", 0.2, "weak lexical result"),
        ],
    )

    reranked = postgres_lexical_rerank(candidates, {})

    assert candidates[0].rrf_score == candidates[1].rrf_score
    assert [row.hit.node_id for row in reranked] == ["b", "a"]
    assert reranked[0].rerank_score == 0.8
    assert reranked[1].rerank_score == 0.2
