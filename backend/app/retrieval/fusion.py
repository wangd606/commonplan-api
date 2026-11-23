from dataclasses import dataclass

from app.retrieval.types import ContextPassage, FusedHit, SearchHit


@dataclass
class FusionCandidate:
    hit: SearchHit
    rrf_score: float = 0.0
    vector_rank: int | None = None
    lexical_rank: int | None = None
    vector_score: float | None = None
    lexical_score: float | None = None


def reciprocal_rank_fusion(
    vector_hits: list[SearchHit],
    lexical_hits: list[SearchHit],
    *,
    rank_constant: int = 60,
) -> list[FusionCandidate]:
    """Fuse channel ranks without pretending their raw scores are comparable."""
    candidates: dict[str, FusionCandidate] = {}
    for method, hits in (("vector", vector_hits), ("lexical", lexical_hits)):
        for rank, hit in enumerate(hits, start=1):
            candidate = candidates.setdefault(hit.node_id, FusionCandidate(hit=hit))
            candidate.rrf_score += 1.0 / (rank_constant + rank)
            if method == "vector":
                candidate.vector_rank = rank
                candidate.vector_score = hit.score
            else:
                candidate.lexical_rank = rank
                candidate.lexical_score = hit.score
    return sorted(candidates.values(), key=lambda row: (-row.rrf_score, row.hit.node_id))


def postgres_lexical_rerank(
    candidates: list[FusionCandidate],
    contexts: dict[str, list[ContextPassage]],
) -> list[FusedHit]:
    """Use PostgreSQL lexical relevance to refine RRF without mixing raw channels."""
    rows: list[FusedHit] = []
    for candidate in candidates:
        passages = contexts.get(candidate.hit.node_id, [])
        # lexical_score is produced by PostgreSQL as the greater of normalized
        # ts_rank_cd cover-density rank and pg_trgm similarity. Vector-only hits
        # have no lexical evidence and therefore receive no lexical boost.
        rerank_score = min(1.0, max(0.0, candidate.lexical_score or 0.0))
        final_score = candidate.rrf_score * (1.0 + 0.25 * rerank_score)
        rows.append(FusedHit(
            hit=candidate.hit,
            rrf_score=candidate.rrf_score,
            rerank_score=rerank_score,
            final_score=final_score,
            vector_rank=candidate.vector_rank,
            lexical_rank=candidate.lexical_rank,
            vector_score=candidate.vector_score,
            lexical_score=candidate.lexical_score,
            context=tuple(passages),
        ))
    return sorted(rows, key=lambda row: (-row.final_score, row.hit.node_id))
