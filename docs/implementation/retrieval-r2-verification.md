# Verify R2 hybrid retrieval

R2 is stacked on R1. It keeps the two candidate channels inspectable, then adds deterministic
reciprocal-rank fusion, authorization-scoped parent/neighbor expansion, and an explainable local
reranking pass. It does not generate an answer or run a LangGraph loop.

## Pipeline

1. PostgreSQL lexical search and pgvector search independently retrieve up to `candidate_limit` rows.
2. Reciprocal Rank Fusion (`k=60`) combines ranks without comparing incompatible raw scores.
3. The repository loads the authorized document page and adjacent chunks for each fused candidate.
4. `postgres-lexical-rank-v2` uses PostgreSQL's normalized `ts_rank_cd` cover-density rank (with
   `pg_trgm` similarity as the bilingual/exact-substring fallback) to refine the RRF order. The
   bounded 0–1 database score can boost an RRF result by at most 25%. This remains transparent and
   local; it is not presented as BM25 or as a neural cross-encoder.
5. The API returns the raw channels, final ranking, score evidence, expanded context, and timings.

The endpoint is:

```text
POST /api/v1/workspaces/{workspace_id}/retrieval/hybrid-search
```

Example request:

```json
{
  "query": "Why did Google login fail?",
  "team_ids": null,
  "limit": 10,
  "candidate_limit": 40,
  "neighbor_window": 1,
  "include_parent": true
}
```

`team_ids: null` means every Team the caller can access. Supplying IDs can only narrow that set.
Parent pages and neighbors are loaded only after the seed candidates pass the same workspace, Team,
source-status, and optional Project/Issue predicates.

## Automated checks

```bash
PYTHONPATH=backend python -m pytest backend/tests -q

TEST_DATABASE_URL=postgresql+psycopg://zhitong:zhitong@localhost:5432/zhitong \
PYTHONPATH=backend python -m pytest backend/tests/test_retrieval_postgres.py -q
```

The PostgreSQL test includes a better-matching unauthorized Team row and a same-dimension competing
embedding profile. It also asks context expansion for an unauthorized seed and verifies that only the
authorized neighbor is returned.

The bilingual fixture at `backend/tests/fixtures/retrieval_quality_cases.json` establishes the
Recall@K/MRR contract. Replace fixture node IDs with stable IDs from a seeded evaluation corpus before
using the numbers as a product-quality gate.

## UI verification

The Web repository adds the workspace-level **Retrieval Lab**. It can:

- scope a query to all accessible Teams or selected Teams;
- switch among fused/reranked, vector, and lexical rankings;
- display channel ranks, raw scores, RRF score, local rerank relevance, and timings;
- reveal parent-page and neighboring-chunk context;
- run English and Chinese queries without exposing an access token or database connection.

R3 will use the same final evidence contract to generate a grounded response with citations. R4 adds
bounded planning, rewrite, retrieve, and grade loops in LangGraph.
