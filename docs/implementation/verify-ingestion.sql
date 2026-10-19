-- Read-only ingestion inspection. Run with:
-- docker compose exec -T db psql -U zhitong -d zhitong < docs/implementation/verify-ingestion.sql

\pset pager off
\set ON_ERROR_STOP on

-- 1. Recent files and their durable pipeline state.
SELECT
    f.id AS file_id,
    f.original_filename AS filename,
    f.upload_status,
    i.status AS ingestion_status,
    i.parser_name,
    i.embedding_provider,
    i.embedding_model,
    i.embedding_dimensions,
    i.attempts,
    i.error_code,
    i.completed_at
FROM file_assets AS f
LEFT JOIN document_ingestions AS i ON i.file_asset_id = f.id
ORDER BY f.created_at DESC
LIMIT 20;

-- 2. Page/chunk/vector facts for recent ingestions. Change the LIMIT or add
--    `WHERE i.file_asset_id = '<file-id>'` when inspecting one upload.
SELECT
    i.file_asset_id,
    i.status,
    count(DISTINCT p.id) AS pages,
    count(DISTINCT c.id) AS chunks,
    min(vector_dims(c.embedding)) AS min_vector_dims,
    max(vector_dims(c.embedding)) AS max_vector_dims,
    round(min(vector_norm(c.embedding))::numeric, 6) AS min_vector_norm,
    round(max(vector_norm(c.embedding))::numeric, 6) AS max_vector_norm
FROM document_ingestions AS i
LEFT JOIN document_pages AS p ON p.ingestion_id = i.id
LEFT JOIN document_chunks AS c ON c.ingestion_id = i.id
GROUP BY i.id, i.file_asset_id, i.status, i.completed_at
ORDER BY i.completed_at DESC NULLS LAST
LIMIT 20;

-- 3. Human-readable chunk samples. This verifies parser metadata, structure
--    output, ACL scope, and a non-empty vector without doing retrieval.
SELECT
    i.file_asset_id,
    c.chunk_index,
    c.page_from,
    c.page_to,
    c.heading_path,
    c.workspace_id,
    c.team_id,
    coalesce(c.issue_id, c.project_id) AS resource_id,
    c.token_count,
    c.chunk_metadata->>'strategy' AS chunk_strategy,
    vector_dims(c.embedding) AS vector_dims,
    round(vector_norm(c.embedding)::numeric, 6) AS vector_norm,
    left(c.content, 160) AS content_sample
FROM document_chunks AS c
JOIN document_ingestions AS i ON i.id = c.ingestion_id
ORDER BY c.created_at DESC, c.chunk_index
LIMIT 30;

-- 4. Acceptance invariant. A successful ingestion must never appear here.
SELECT
    i.file_asset_id,
    i.status,
    count(DISTINCT p.id) AS pages,
    count(DISTINCT c.id) AS chunks,
    count(c.embedding) AS vectors
FROM document_ingestions AS i
LEFT JOIN document_pages AS p ON p.ingestion_id = i.id
LEFT JOIN document_chunks AS c ON c.ingestion_id = i.id
WHERE i.status = 'ready'
GROUP BY i.id, i.file_asset_id, i.status
HAVING count(DISTINCT p.id) = 0
    OR count(DISTINCT c.id) = 0
    OR count(c.embedding) <> count(DISTINCT c.id);

-- 5. Recent append-only knowledge events (delivery state lives in Redpanda).
SELECT
    id,
    aggregate_type AS source_type,
    aggregate_id AS source_id,
    event_type,
    partition_key,
    created_at
FROM outbox_events
ORDER BY created_at DESC;
