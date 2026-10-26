"""normalize the ingestion corpus for versioned, multimodal embeddings

Revision ID: 20261025_0022
Revises: 20261018_0021
"""

from alembic import op


revision = "20261025_0022"
down_revision = "20261018_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE embedding_profiles (
            id varchar(36) PRIMARY KEY,
            profile_key varchar(240) NOT NULL UNIQUE,
            provider varchar(40) NOT NULL,
            model varchar(180) NOT NULL,
            dimensions integer NOT NULL,
            distance_metric varchar(24) NOT NULL DEFAULT 'cosine',
            normalized boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_embedding_profile_dimensions CHECK (dimensions > 0),
            CONSTRAINT ck_embedding_profile_metric CHECK (
                distance_metric IN ('cosine', 'inner_product', 'l2')
            )
        );

        -- A profile is a vector-space contract, not a retrieval index. Keeping
        -- it separate allows a new model/dimension to coexist with old data.
        INSERT INTO embedding_profiles (
            id, profile_key, provider, model, dimensions
        )
        SELECT gen_random_uuid()::text,
               profile.provider || ':' || profile.model || ':' || profile.dimensions,
               profile.provider, profile.model, profile.dimensions
        FROM (
            SELECT embedding_provider AS provider, embedding_model AS model,
                   embedding_dimensions AS dimensions
            FROM knowledge_sources
            UNION
            SELECT embedding_provider, embedding_model, embedding_dimensions
            FROM document_ingestions
        ) AS profile;

        ALTER TABLE knowledge_sources
            ALTER COLUMN renderer_version DROP NOT NULL,
            ADD COLUMN active_embedding_profile_id varchar(36)
                REFERENCES embedding_profiles(id);

        ALTER TABLE document_ingestions
            ADD COLUMN knowledge_source_id varchar(36)
                REFERENCES knowledge_sources(id) ON DELETE CASCADE,
            ADD COLUMN embedding_profile_id varchar(36)
                REFERENCES embedding_profiles(id);

        -- Attachments now participate in the same canonical source catalog as
        -- database records. Parsed pages remain attachment-specific artifacts.
        INSERT INTO knowledge_sources (
            id, workspace_id, team_id, source_type, source_id, project_id, issue_id,
            source_version, content_hash, renderer_version, chunker_version,
            embedding_provider, embedding_model, embedding_dimensions,
            status, error_code, error_message, indexed_at, deleted_at,
            created_at, updated_at
        )
        SELECT gen_random_uuid()::text,
               ingestion.workspace_id,
               COALESCE(issue.team_id, project.team_id),
               'attachment', ingestion.file_asset_id,
               project.id, issue.id,
               1, ingestion.content_sha256, NULL, ingestion.chunker_version,
               ingestion.embedding_provider, ingestion.embedding_model,
               ingestion.embedding_dimensions,
               CASE WHEN ingestion.status = 'ready' THEN 'ready'
                    WHEN ingestion.status = 'deleted' THEN 'deleted'
                    WHEN ingestion.status = 'failed' THEN 'failed'
                    ELSE 'pending' END,
               ingestion.error_code, ingestion.error_message,
               ingestion.completed_at, ingestion.deleted_at,
               ingestion.created_at, ingestion.updated_at
        FROM document_ingestions AS ingestion
        LEFT JOIN issue_attachments AS issue_link
          ON issue_link.file_asset_id = ingestion.file_asset_id
        LEFT JOIN issues AS issue ON issue.id = issue_link.issue_id
        LEFT JOIN project_attachments AS project_link
          ON project_link.file_asset_id = ingestion.file_asset_id
        LEFT JOIN projects AS project ON project.id = project_link.project_id
        WHERE COALESCE(issue.team_id, project.team_id) IS NOT NULL
        ON CONFLICT (source_type, source_id) DO NOTHING;

        UPDATE knowledge_sources AS source
        SET active_embedding_profile_id = profile.id
        FROM embedding_profiles AS profile
        WHERE profile.provider = source.embedding_provider
          AND profile.model = source.embedding_model
          AND profile.dimensions = source.embedding_dimensions;

        UPDATE document_ingestions AS ingestion
        SET knowledge_source_id = source.id,
            embedding_profile_id = profile.id
        FROM knowledge_sources AS source, embedding_profiles AS profile
        WHERE source.source_type = 'attachment'
          AND source.source_id = ingestion.file_asset_id
          AND profile.provider = ingestion.embedding_provider
          AND profile.model = ingestion.embedding_model
          AND profile.dimensions = ingestion.embedding_dimensions;

        ALTER TABLE knowledge_sources
            ALTER COLUMN active_embedding_profile_id SET NOT NULL;
        ALTER TABLE document_ingestions
            ALTER COLUMN knowledge_source_id SET NOT NULL,
            ALTER COLUMN embedding_profile_id SET NOT NULL,
            ADD CONSTRAINT uq_document_ingestion_knowledge_source
                UNIQUE (knowledge_source_id);

        CREATE TABLE knowledge_nodes (
            id varchar(36) PRIMARY KEY,
            knowledge_source_id varchar(36) NOT NULL
                REFERENCES knowledge_sources(id) ON DELETE CASCADE,
            parent_node_id varchar(36)
                REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
            document_page_id varchar(36)
                REFERENCES document_pages(id) ON DELETE SET NULL,
            workspace_id varchar(36) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            team_id varchar(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            project_id varchar(36) REFERENCES projects(id) ON DELETE CASCADE,
            issue_id varchar(36) REFERENCES issues(id) ON DELETE CASCADE,
            node_key varchar(160) NOT NULL,
            node_kind varchar(32) NOT NULL DEFAULT 'chunk',
            modality varchar(24) NOT NULL DEFAULT 'text',
            indexable boolean NOT NULL DEFAULT true,
            ordinal integer NOT NULL,
            content text NOT NULL,
            content_hash varchar(64) NOT NULL,
            token_count integer NOT NULL,
            page_from integer,
            page_to integer,
            heading_path text,
            node_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_knowledge_node_key UNIQUE (knowledge_source_id, node_key),
            CONSTRAINT ck_knowledge_node_scope CHECK (
                (issue_id IS NOT NULL AND project_id IS NULL)
                OR (issue_id IS NULL AND project_id IS NOT NULL)
            ),
            CONSTRAINT ck_knowledge_node_ordinal CHECK (ordinal >= 0),
            CONSTRAINT ck_knowledge_node_tokens CHECK (token_count >= 0),
            CONSTRAINT ck_knowledge_node_modality CHECK (
                modality IN ('text', 'image', 'audio', 'video', 'table')
            )
        );

        CREATE TABLE knowledge_embeddings (
            knowledge_node_id varchar(36) NOT NULL
                REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
            embedding_profile_id varchar(36) NOT NULL
                REFERENCES embedding_profiles(id),
            content_hash varchar(64) NOT NULL,
            embedding vector NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (knowledge_node_id, embedding_profile_id)
        );

        -- These B-tree indexes support ownership/FK maintenance and ingestion
        -- inspection only. Vector/lexical retrieval indexes are intentionally absent.
        CREATE INDEX ix_knowledge_sources_active_profile
            ON knowledge_sources (active_embedding_profile_id);
        CREATE INDEX ix_document_ingestions_knowledge_source
            ON document_ingestions (knowledge_source_id);
        CREATE INDEX ix_document_ingestions_embedding_profile
            ON document_ingestions (embedding_profile_id);
        CREATE INDEX ix_knowledge_nodes_source ON knowledge_nodes (knowledge_source_id);
        CREATE INDEX ix_knowledge_nodes_parent ON knowledge_nodes (parent_node_id);
        CREATE INDEX ix_knowledge_nodes_document_page ON knowledge_nodes (document_page_id);
        CREATE INDEX ix_knowledge_nodes_scope ON knowledge_nodes (workspace_id, team_id);
        CREATE INDEX ix_knowledge_nodes_project ON knowledge_nodes (project_id);
        CREATE INDEX ix_knowledge_nodes_issue ON knowledge_nodes (issue_id);
        CREATE INDEX ix_knowledge_embeddings_profile
            ON knowledge_embeddings (embedding_profile_id);

        INSERT INTO knowledge_nodes (
            id, knowledge_source_id, workspace_id, team_id, project_id, issue_id,
            node_key, node_kind, modality, indexable, ordinal,
            content, content_hash, token_count, node_metadata, created_at
        )
        SELECT chunk.id, chunk.knowledge_source_id, chunk.workspace_id, chunk.team_id,
               chunk.project_id, chunk.issue_id,
               'chunk:' || chunk.chunk_index, 'chunk', 'text', true, chunk.chunk_index,
               chunk.content, chunk.content_hash, chunk.token_count,
               chunk.chunk_metadata, chunk.created_at
        FROM knowledge_chunks AS chunk;

        INSERT INTO knowledge_embeddings (
            knowledge_node_id, embedding_profile_id, content_hash, embedding, created_at
        )
        SELECT chunk.id, source.active_embedding_profile_id,
               chunk.content_hash, chunk.embedding, chunk.created_at
        FROM knowledge_chunks AS chunk
        JOIN knowledge_sources AS source ON source.id = chunk.knowledge_source_id;

        INSERT INTO knowledge_nodes (
            id, knowledge_source_id, document_page_id,
            workspace_id, team_id, project_id, issue_id,
            node_key, node_kind, modality, indexable, ordinal,
            content, content_hash, token_count, page_from, page_to, heading_path,
            node_metadata, created_at
        )
        SELECT chunk.id, ingestion.knowledge_source_id, chunk.parent_page_id,
               chunk.workspace_id, chunk.team_id, chunk.project_id, chunk.issue_id,
               'chunk:' || chunk.chunk_index, 'chunk', 'text', true, chunk.chunk_index,
               chunk.content, chunk.content_hash, chunk.token_count,
               chunk.page_from, chunk.page_to, chunk.heading_path,
               chunk.chunk_metadata, chunk.created_at
        FROM document_chunks AS chunk
        JOIN document_ingestions AS ingestion ON ingestion.id = chunk.ingestion_id;

        INSERT INTO knowledge_embeddings (
            knowledge_node_id, embedding_profile_id, content_hash, embedding, created_at
        )
        SELECT chunk.id, ingestion.embedding_profile_id,
               chunk.content_hash, chunk.embedding, chunk.created_at
        FROM document_chunks AS chunk
        JOIN document_ingestions AS ingestion ON ingestion.id = chunk.ingestion_id;

        DROP TABLE document_chunks;
        DROP TABLE knowledge_chunks;

        ALTER TABLE knowledge_sources
            DROP COLUMN embedding_provider,
            DROP COLUMN embedding_model,
            DROP COLUMN embedding_dimensions;
        ALTER TABLE document_ingestions
            DROP COLUMN embedding_provider,
            DROP COLUMN embedding_model,
            DROP COLUMN embedding_dimensions;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM knowledge_embeddings AS embedding
                JOIN embedding_profiles AS profile
                  ON profile.id = embedding.embedding_profile_id
                WHERE profile.dimensions <> 1024
            ) THEN
                RAISE EXCEPTION 'Cannot downgrade non-1024-dimensional embeddings';
            END IF;
        END $$;

        ALTER TABLE knowledge_sources
            ADD COLUMN embedding_provider varchar(40),
            ADD COLUMN embedding_model varchar(160),
            ADD COLUMN embedding_dimensions integer;
        UPDATE knowledge_sources AS source
        SET embedding_provider = profile.provider,
            embedding_model = profile.model,
            embedding_dimensions = profile.dimensions
        FROM embedding_profiles AS profile
        WHERE profile.id = source.active_embedding_profile_id;

        ALTER TABLE document_ingestions
            ADD COLUMN embedding_provider varchar(40),
            ADD COLUMN embedding_model varchar(120),
            ADD COLUMN embedding_dimensions integer;
        UPDATE document_ingestions AS ingestion
        SET embedding_provider = profile.provider,
            embedding_model = profile.model,
            embedding_dimensions = profile.dimensions
        FROM embedding_profiles AS profile
        WHERE profile.id = ingestion.embedding_profile_id;

        CREATE TABLE knowledge_chunks (
            id varchar(36) PRIMARY KEY,
            knowledge_source_id varchar(36) NOT NULL
                REFERENCES knowledge_sources(id) ON DELETE CASCADE,
            workspace_id varchar(36) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            team_id varchar(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            project_id varchar(36) REFERENCES projects(id) ON DELETE CASCADE,
            issue_id varchar(36) REFERENCES issues(id) ON DELETE CASCADE,
            chunk_index integer NOT NULL,
            content text NOT NULL,
            content_hash varchar(64) NOT NULL,
            token_count integer NOT NULL,
            embedding vector(1024) NOT NULL,
            chunk_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_knowledge_chunks_source_index
                UNIQUE (knowledge_source_id, chunk_index)
        );

        CREATE TABLE document_chunks (
            id varchar(36) PRIMARY KEY,
            ingestion_id varchar(36) NOT NULL
                REFERENCES document_ingestions(id) ON DELETE CASCADE,
            parent_page_id varchar(36) NOT NULL
                REFERENCES document_pages(id) ON DELETE CASCADE,
            workspace_id varchar(36) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            team_id varchar(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            issue_id varchar(36) REFERENCES issues(id) ON DELETE CASCADE,
            project_id varchar(36) REFERENCES projects(id) ON DELETE CASCADE,
            chunk_index integer NOT NULL,
            content text NOT NULL,
            content_hash varchar(64) NOT NULL,
            token_count integer NOT NULL,
            page_from integer NOT NULL,
            page_to integer NOT NULL,
            heading_path text,
            embedding vector(1024) NOT NULL,
            chunk_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_document_chunk_index UNIQUE (ingestion_id, chunk_index),
            CONSTRAINT ck_document_chunk_scope CHECK (
                (issue_id IS NOT NULL AND project_id IS NULL)
                OR (issue_id IS NULL AND project_id IS NOT NULL)
            )
        );

        INSERT INTO knowledge_chunks (
            id, knowledge_source_id, workspace_id, team_id, project_id, issue_id,
            chunk_index, content, content_hash, token_count,
            embedding, chunk_metadata, created_at
        )
        SELECT node.id, node.knowledge_source_id, node.workspace_id, node.team_id,
               node.project_id, node.issue_id, node.ordinal,
               node.content, node.content_hash, node.token_count,
               embedding.embedding::vector(1024), node.node_metadata, node.created_at
        FROM knowledge_nodes AS node
        JOIN knowledge_sources AS source ON source.id = node.knowledge_source_id
        JOIN knowledge_embeddings AS embedding ON embedding.knowledge_node_id = node.id
          AND embedding.embedding_profile_id = source.active_embedding_profile_id
        WHERE source.source_type <> 'attachment' AND node.indexable;

        INSERT INTO document_chunks (
            id, ingestion_id, parent_page_id, workspace_id, team_id,
            issue_id, project_id, chunk_index, content, content_hash, token_count,
            page_from, page_to, heading_path, embedding, chunk_metadata, created_at
        )
        SELECT node.id, ingestion.id, node.document_page_id,
               node.workspace_id, node.team_id, node.issue_id, node.project_id,
               node.ordinal, node.content, node.content_hash, node.token_count,
               node.page_from, node.page_to, node.heading_path,
               embedding.embedding::vector(1024), node.node_metadata, node.created_at
        FROM knowledge_nodes AS node
        JOIN document_ingestions AS ingestion
          ON ingestion.knowledge_source_id = node.knowledge_source_id
        JOIN knowledge_embeddings AS embedding ON embedding.knowledge_node_id = node.id
          AND embedding.embedding_profile_id = ingestion.embedding_profile_id
        WHERE node.indexable AND node.document_page_id IS NOT NULL;

        CREATE INDEX ix_knowledge_chunks_source_id ON knowledge_chunks (knowledge_source_id);
        CREATE INDEX ix_knowledge_chunks_workspace_id ON knowledge_chunks (workspace_id);
        CREATE INDEX ix_knowledge_chunks_team_id ON knowledge_chunks (team_id);
        CREATE INDEX ix_knowledge_chunks_project_id ON knowledge_chunks (project_id);
        CREATE INDEX ix_knowledge_chunks_issue_id ON knowledge_chunks (issue_id);
        CREATE INDEX ix_document_chunks_ingestion ON document_chunks (ingestion_id);
        CREATE INDEX ix_document_chunks_parent ON document_chunks (parent_page_id);
        CREATE INDEX ix_document_chunks_scope ON document_chunks (workspace_id, team_id);
        CREATE INDEX ix_document_chunks_issue ON document_chunks (issue_id);
        CREATE INDEX ix_document_chunks_project ON document_chunks (project_id);

        ALTER TABLE document_ingestions
            DROP CONSTRAINT uq_document_ingestion_knowledge_source,
            DROP COLUMN knowledge_source_id,
            DROP COLUMN embedding_profile_id,
            ALTER COLUMN embedding_provider SET NOT NULL,
            ALTER COLUMN embedding_model SET NOT NULL,
            ALTER COLUMN embedding_dimensions SET NOT NULL;

        DROP TABLE knowledge_embeddings;
        DROP TABLE knowledge_nodes;

        DELETE FROM knowledge_sources WHERE source_type = 'attachment';
        ALTER TABLE knowledge_sources
            DROP COLUMN active_embedding_profile_id,
            ALTER COLUMN renderer_version SET NOT NULL,
            ALTER COLUMN embedding_provider SET NOT NULL,
            ALTER COLUMN embedding_model SET NOT NULL,
            ALTER COLUMN embedding_dimensions SET NOT NULL;

        DROP TABLE embedding_profiles;
        """
    )
