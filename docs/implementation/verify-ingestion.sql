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
    ep.provider AS embedding_provider,
    ep.model AS embedding_model,
    ep.dimensions AS embedding_dimensions,
    i.attempts,
    i.error_code,
    i.completed_at
FROM file_assets AS f
LEFT JOIN document_ingestions AS i ON i.file_asset_id = f.id
LEFT JOIN embedding_profiles AS ep ON ep.id = i.embedding_profile_id
ORDER BY f.created_at DESC
LIMIT 20;

-- 2. Page/chunk/vector facts for recent ingestions. Change the LIMIT or add
--    `WHERE i.file_asset_id = '<file-id>'` when inspecting one upload.
SELECT
    i.file_asset_id,
    i.status,
    count(DISTINCT p.id) AS pages,
    count(DISTINCT n.id) FILTER (WHERE n.indexable) AS chunks,
    min(vector_dims(e.embedding)) AS min_vector_dims,
    max(vector_dims(e.embedding)) AS max_vector_dims,
    round(min(vector_norm(e.embedding))::numeric, 6) AS min_vector_norm,
    round(max(vector_norm(e.embedding))::numeric, 6) AS max_vector_norm
FROM document_ingestions AS i
LEFT JOIN document_pages AS p ON p.ingestion_id = i.id
LEFT JOIN knowledge_nodes AS n ON n.knowledge_source_id = i.knowledge_source_id
LEFT JOIN knowledge_embeddings AS e
  ON e.knowledge_node_id = n.id AND e.embedding_profile_id = i.embedding_profile_id
GROUP BY i.id, i.file_asset_id, i.status, i.completed_at
ORDER BY i.completed_at DESC NULLS LAST
LIMIT 20;

-- 3. Human-readable chunk samples. This verifies parser metadata, structure
--    output, ACL scope, and a non-empty vector without doing retrieval.
SELECT
    i.file_asset_id,
    n.ordinal AS chunk_index,
    n.page_from,
    n.page_to,
    n.heading_path,
    n.workspace_id,
    n.team_id,
    coalesce(n.issue_id, n.project_id) AS resource_id,
    n.token_count,
    n.node_metadata->>'strategy' AS chunk_strategy,
    vector_dims(e.embedding) AS vector_dims,
    round(vector_norm(e.embedding)::numeric, 6) AS vector_norm,
    left(n.content, 160) AS content_sample
FROM knowledge_nodes AS n
JOIN document_ingestions AS i ON i.knowledge_source_id = n.knowledge_source_id
JOIN knowledge_embeddings AS e
  ON e.knowledge_node_id = n.id AND e.embedding_profile_id = i.embedding_profile_id
WHERE n.indexable
ORDER BY n.created_at DESC, n.ordinal
LIMIT 30;

-- 4. Acceptance invariant. A successful ingestion must never appear here.
SELECT
    i.file_asset_id,
    i.status,
    count(DISTINCT p.id) AS pages,
    count(DISTINCT n.id) FILTER (WHERE n.indexable) AS chunks,
    count(e.embedding) AS vectors
FROM document_ingestions AS i
LEFT JOIN document_pages AS p ON p.ingestion_id = i.id
LEFT JOIN knowledge_nodes AS n
  ON n.knowledge_source_id = i.knowledge_source_id AND n.indexable
LEFT JOIN knowledge_embeddings AS e
  ON e.knowledge_node_id = n.id AND e.embedding_profile_id = i.embedding_profile_id
WHERE i.status = 'ready'
GROUP BY i.id, i.file_asset_id, i.status
HAVING count(DISTINCT p.id) = 0
    OR count(DISTINCT n.id) = 0
    OR count(e.embedding) <> count(DISTINCT n.id);

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
