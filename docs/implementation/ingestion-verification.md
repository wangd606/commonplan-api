# Verify document ingestion before retrieval

This runbook deliberately stops before similarity search. Its acceptance
boundary is: a completed attachment is parsed, chunked, embedded, and stored as
authorized `vector(1024)` rows that can be inspected independently of any
retrieval or answer-generation behavior.

## 1. Start the ingestion stack

```bash
docker compose up --build db redis redpanda debezium auth-service embedding-service model-gateway backend ingestion-worker
```

The database image includes pgvector. Alembic enables the extension and changes
`document_chunks.embedding` from the temporary JSONB representation to the
canonical `vector(1024)` type. The first start downloads the pinned Qwen model
and Docling assets into persistent volumes. Wait for both runtime boundaries:

```bash
curl --fail http://localhost:8080/health
curl --fail http://localhost:4000/health/liveliness
```

Both local model endpoints are bound to `127.0.0.1` only. The worker calls LiteLLM at
`http://model-gateway:4000/v1`, not TEI directly. The conservative CPU defaults (`3` inputs and
`1,024` scheduled tokens per batch) keep Qwen warm-up practical on a development laptop; tune
`INGESTION_EMBEDDING_BATCH_SIZE`, `TEI_MAX_BATCH_TOKENS`, `TEI_MAX_CONCURRENT_REQUESTS`, and
`TEI_TOKENIZATION_WORKERS` for a GPU deployment.

Verify the event backbone before producing data:

```bash
curl --fail http://localhost:8083/connectors/commonplan-outbox/status
docker compose exec -T redpanda rpk topic list
```

The connector must be `RUNNING`, and `commonplan.knowledge.v1` should exist after the first outbox
insert. Port `19092` is the local Kafka endpoint; application containers use `redpanda:9092`.

Apple Silicon uses the default ARM64 image. On x86_64 set:

```bash
TEI_IMAGE=ghcr.io/huggingface/text-embeddings-inference:cpu-1.9 docker compose up --build
```

## 2. Produce one ingestion

Open the web application, enter an Issue or Project detail page, and upload a
small Markdown, text, PDF, CSV, JSON, DOCX, XLSX, PPTX, or image file. Upload completion writes the ready
file and its `knowledge.source.upserted` outbox event in one transaction. The worker handles
parsing and embeddings asynchronously.

Creating or editing an Issue, Project or Comment follows the same broker path without Docling. The
worker loads the authoritative record through `KnowledgeRepository`, renders canonical Markdown and
writes `knowledge_sources` plus `knowledge_chunks`.

The authenticated status endpoint is:

```text
GET /api/v1/workspaces/{workspace_id}/files/{file_id}/ingestion
```

The expected terminal response has `status: "ready"`, non-zero `page_count` and
`chunk_count`, the configured embedding provider/model, and no error fields.

## 3. Inspect the durable result with SQL

Run the repository's read-only inspection queries:

```bash
docker compose exec -T db psql -U zhitong -d zhitong \
  < docs/implementation/verify-ingestion.sql
```

For a successful upload, verify:

- `file_assets.upload_status = 'ready'`;
- `document_ingestions.status = 'ready'`;
- page and chunk counts are greater than zero;
- every chunk reports `vector_dims = 1024` and `vector_norm` close to `1.0`;
- structured formats report `parser_name = 'docling'` and `chunk_metadata.strategy = 'docling_hybrid'`;
- `workspace_id`, `team_id`, and exactly one resource ID are present;
- the acceptance-invariant query returns zero rows;
- the Redpanda consumer group has no unexplained lag and the DLQ is empty.

Inspect database-source embeddings:

```sql
SELECT source_type, source_id, status, source_version, renderer_version,
       embedding_model, indexed_at
FROM knowledge_sources
ORDER BY indexed_at DESC;

SELECT source.source_type, source.source_id, chunk.chunk_index,
       left(chunk.content, 160), vector_dims(chunk.embedding)
FROM knowledge_chunks AS chunk
JOIN knowledge_sources AS source ON source.id = chunk.knowledge_source_id
ORDER BY source.indexed_at DESC, chunk.chunk_index;
```

To inspect one file, copy its `file_id` from the first result and add
`WHERE i.file_asset_id = '<file-id>'` to the second query. The third query shows
the actual chunk text, page/heading metadata, ACL scope, and vector facts without
performing nearest-neighbor search.

## 4. Exercise failure and recovery

Upload a legacy binary `.doc` file to confirm that the ingestion becomes
`failed` while the original attachment remains downloadable. Its error code and
message should appear in both the status endpoint and inspection query.

After fixing a transient worker/configuration problem, replay the DLQ record to
`commonplan.knowledge.v1`, or edit/upload the source again to create a newer event. Consumer offsets
and lag are visible with `rpk group describe commonplan-knowledge-indexer-v1`; outbox rows are
append-only and are never manually changed back to `pending`.

## 5. Verify the gateway alias

```bash
curl http://localhost:4000/v1/embeddings \
  -H 'Authorization: Bearer sk-local-commonplan-change-me' \
  -H 'Content-Type: application/json' \
  -d '{"model":"commonplan-embedding-v1","input":["CommonPlan links issues to project knowledge."],"encoding_format":"float"}' \
  | python -c 'import json,sys; v=json.load(sys.stdin)["data"][0]["embedding"]; print(len(v), round(sum(x*x for x in v), 6))'
```

Expected output is `1024 1.0` (subject only to floating-point rounding).

To prove the gateway has not changed vector semantics relative to the pinned direct runtime, run:

```bash
python scripts/verify-model-gateway.py
```

The command requires 1,024 dimensions, normalized output, and cosine similarity of at least
`0.99999` between direct TEI and the gateway path. CI/runtime suites can additionally enable
`test_gateway_embedding_is_conformant_with_direct_tei` with `TEI_RUNTIME_URL`,
`MODEL_GATEWAY_RUNTIME_URL`, and `MODEL_GATEWAY_API_KEY`.

Production must replace the local shared key with a restricted LiteLLM virtual/service key stored in
Secret Manager. The master key must not be issued to application workloads.

## What this does not validate

This proves that production parsing and semantic vectors are durable. It does
not prove retrieval quality. The later retrieval phase will add the vector
index, authorization-filtered nearest-neighbor query, lexical search, fusion,
reranking, citations, and a bilingual quality regression dataset.
