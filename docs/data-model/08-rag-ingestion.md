# Knowledge ingestion foundation

This milestone turns attachments and high-context business records into durable, access-scoped
retrieval data without coupling API latency to parsing or model availability.

## Runtime flow

```text
Issue/Project/Comment/File mutation
  -> business row + append-only outbox event, one PostgreSQL transaction
  -> PostgreSQL WAL -> Debezium -> Redpanda commonplan.knowledge.v1
  -> ingestion-worker consumes with commonplan-knowledge-indexer-v1
  -> attachment: object storage -> checksum -> Docling -> HybridChunker
  -> database source: repository snapshot -> versioned Markdown renderer -> word-window chunker
  -> authenticated model client calls commonplan-embedding-v1 on private LiteLLM Gateway
  -> gateway routes only to the pinned TEI/Qwen revision
  -> normalized 1024-dimensional Qwen vectors return to the worker
  -> source-specific parse artifacts plus the canonical knowledge corpus
  -> repository commit -> Kafka offset commit
```

API requests return before parsing or embeddings run. Consumer-group offsets replace the old database
lease. Processing is at-least-once: content hashes and versioned source identities make re-delivery
idempotent. A failing delivery receives bounded exponential retries and is then copied to
`commonplan.knowledge.dlq.v1`. Delete events remove chunks and retain a source tombstone.

## Tables

| Table | Responsibility |
| --- | --- |
| `outbox_events` | Append-only transaction bridge captured from WAL by Debezium; no delivery status is updated here |
| `document_ingestions` | One current pipeline result per file, including checksum, parser/chunker versions, canonical source and active embedding profile |
| `document_pages` | Parent-page text used to expand the context around a matching chunk |
| `knowledge_sources` | Canonical identity, version, lifecycle, content hash and ACL scope for attachments and database records |
| `knowledge_nodes` | Versioned text/image/audio/table nodes with parent-child structure, provenance, modality and denormalized ACL scope |
| `embedding_profiles` | Stable vector-space contract: provider, physical model/revision, dimensions, metric and normalization policy |
| `knowledge_embeddings` | A node/profile pair and its variable-dimension pgvector value; no similarity index in this phase |

Comments are ingested independently from their Issue, and Project Updates independently from their
Project. This avoids re-embedding an entire aggregate for one small edit. Parent scope is retained in
metadata and denormalized `workspace_id`, `team_id`, `issue_id`/`project_id` columns so later retrieval can
apply authorization in the same query that selects candidates.

`knowledge_nodes` is deliberately source-agnostic. Today the worker writes text `chunk` nodes. The
same table can later represent a document/section/chunk parent-child hierarchy or non-text nodes
without changing source-specific parser tables. `document_pages` remains the Docling parse artifact;
it is not duplicated for Issues and other database records. `knowledge_embeddings` is separate from
nodes because changing an embedding model changes a derived representation, not the canonical node.
Several profiles may therefore coexist while a re-embedding rollout is in progress.

Operational fields such as assignee, workflow state, priority and cycle are stored as filter metadata.
Human-readable context such as Issue title/description, Project summary/objectives and Comment body is
rendered into the text sent to the embedding model. Secrets, raw auth state and permission records are
never rendered.

## Adapters and production evolution

### Event backbone choice

The application contract is the Kafka protocol, not a hard dependency on one broker vendor. Local
development uses single-node Redpanda because it is operationally smaller than a local Kafka plus
ZooKeeper/KRaft stack while preserving topics, partitions, consumer groups, replay, offsets and the
client ecosystem. Production may use Redpanda Cloud, Confluent Cloud, Aiven or AWS MSK without
changing publisher or consumer domain interfaces.

NATS JetStream is lighter for small deployments, but switching to it would require a different
consumer/offset adapter and would give up the direct Debezium-to-Kafka path used here. Cloud-native
queues such as SQS/Pub/Sub are economical managed options, but their replay, ordering and CDC
connectors are provider-specific. They remain possible behind the event-bus adapter if deployment
economics justify the extra implementation. The current choice keeps local operation inexpensive,
preserves a managed-cloud exit path, and avoids broker calls inside business transactions.

Docling 2.135.0 handles PDF, CSV, DOCX, XLSX, PPTX, and supported images in the dedicated worker image. It preserves structured Markdown, per-page provenance, headings, reading order, and tables. Plain text, Markdown, and JSON retain a lightweight parser because their source representation is already canonical. Legacy binary `.doc` uploads remain downloadable but are not ingestible; callers receive `unsupported_content_type` instead of a misleading partial parse.

### Chunk overlap policy

The two chunking paths do not currently have identical overlap behavior:

- Plain-text fallback chunks use a 300-word sliding window with 50 words of overlap.
- Docling documents use structure-aware `HybridChunker` output without synthetic overlap. The
  current Docling API splits oversized structural units and merges compatible peers, but does not
  expose a `chunk_overlap` option.

CommonPlan must not add hidden overlap only to the text sent to the embedding model. Doing so would
make the persisted chunk text differ from the content represented by its vector, obscure provenance,
and make re-indexing and retrieval debugging harder. The canonical rule is that the contextualized
text persisted for a chunk is the same text used to create its embedding.

The first retrieval implementation should expand a hit with its parent page or explicitly selected
neighbor chunks, then rerank and deduplicate that context. This recovers boundary context without
duplicating every structure-aware chunk in the index. If the retrieval regression dataset later shows
a measurable boundary-recall problem, overlap may be introduced as a versioned chunker change only
when all of the following are stored explicitly: the overlapped canonical text, source chunk/span IDs,
the union of source-page provenance, and the new chunker version. Search must then collapse duplicate
neighbor hits before assembling context. A cross-heading, cross-table, or cross-resource overlap is
never permitted.

The canonical embedding profile is `Qwen/Qwen3-Embedding-0.6B` at revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, served privately by TEI 1.9 behind the `commonplan-embedding-v1` LiteLLM alias. The worker authenticates to the gateway, sends bounded OpenAI-compatible batches, validates indexes/count/dimensions, rejects zero/non-finite vectors, normalizes defensively, and persists a 1,024-dimensional value in `knowledge_embeddings.embedding`. The durable profile records the provider plus immutable physical model/revision and dimensions, so repointing an alias cannot make incompatible vectors look current. Local CPU defaults use three chunks per client batch and a 1,024-token scheduler budget because CommonPlan chunks are capped at 300 tokens; GPU deployments can raise both through environment variables. Direct `tei` and deterministic `local_hash` adapters remain explicit diagnostic/test options; the factory never silently falls back.

The model weights and Docling assets use persistent Docker volumes. After the first successful download, restarts do not fetch them again. ARM64 local development uses TEI's `cpu-arm64-1.9` image; x86_64 deployments must set `TEI_IMAGE=ghcr.io/huggingface/text-embeddings-inference:cpu-1.9`.

Migration `20261025_0022` separates canonical nodes from model-specific embeddings. The pgvector
column is intentionally dimension-flexible; `embedding_profiles.dimensions` and worker validation
define compatibility. A new profile can be populated beside the active profile and switched only
after re-embedding is complete. Source objects, parsed pages and authorization metadata remain
authoritative.

This phase intentionally creates no HNSW/IVFFlat index, lexical-search index or similarity-search
API. The B-tree indexes present here support foreign keys, ingestion ownership and operational
inspection only. Authorization-filtered candidate selection, index/operator-class choice, parent or
neighbor expansion, fusion, reranking and citations belong to the separately reviewed retrieval phase.

## API and operation

- `GET /api/v1/workspaces/{workspace_id}/files/{file_id}/ingestion` returns `not_started`, current stage, counts, adapter versions, or failure details after normal resource authorization.
- `python -m app.ingestion.worker` runs continuously.
- `python -m app.ingestion.worker --once` consumes at most one broker event for deterministic local verification.
- Docker Compose starts `embedding-service`, `model-gateway`, and the heavier `ingestion-worker` image separately from the Business API. Docling and gateway dependencies never enter the latency-sensitive API image.
- [`../implementation/ingestion-verification.md`](../implementation/ingestion-verification.md) provides the upload, status, SQL inspection, invariant, and recovery checks used to accept this phase before retrieval begins.

The service layer never receives a SQLAlchemy session. File operations stay in `IngestionRepository`;
database-source snapshots and writes stay in `KnowledgeRepository`. The worker orchestrates the two
repository-backed handlers and commits the broker offset only after the selected repository succeeds.
