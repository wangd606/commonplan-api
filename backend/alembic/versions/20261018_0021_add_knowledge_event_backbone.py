"""add durable knowledge event backbone

Revision ID: 20261018_0021
Revises: 20261009_0020
"""

from alembic import op


revision = "20261018_0021"
down_revision = "20261009_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- The CDC outbox is an append-only event log. Delivery state belongs
        -- to Kafka consumer offsets and the dead-letter topic, not this table.
        DELETE FROM outbox_events;

        ALTER TABLE outbox_events
            DROP COLUMN status,
            DROP COLUMN attempts,
            DROP COLUMN available_at,
            DROP COLUMN locked_at,
            DROP COLUMN locked_by,
            DROP COLUMN last_error,
            DROP COLUMN published_at,
            ADD COLUMN schema_version integer NOT NULL DEFAULT 1,
            ADD COLUMN partition_key varchar(200);

        UPDATE outbox_events
        SET partition_key = workspace_id || ':' || aggregate_type || ':' || aggregate_id;

        ALTER TABLE outbox_events ALTER COLUMN partition_key SET NOT NULL;
        CREATE INDEX ix_outbox_events_partition_key ON outbox_events (partition_key);

        CREATE TABLE knowledge_sources (
            id varchar(36) PRIMARY KEY,
            workspace_id varchar(36) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            team_id varchar(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            source_type varchar(40) NOT NULL,
            source_id varchar(36) NOT NULL,
            project_id varchar(36) REFERENCES projects(id) ON DELETE CASCADE,
            issue_id varchar(36) REFERENCES issues(id) ON DELETE CASCADE,
            source_version bigint NOT NULL DEFAULT 1,
            content_hash varchar(64) NOT NULL,
            renderer_version varchar(40) NOT NULL,
            chunker_version varchar(40) NOT NULL,
            embedding_provider varchar(40) NOT NULL,
            embedding_model varchar(160) NOT NULL,
            embedding_dimensions integer NOT NULL,
            status varchar(24) NOT NULL DEFAULT 'pending',
            error_code varchar(80),
            error_message text,
            indexed_at timestamptz,
            deleted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_knowledge_sources_identity UNIQUE (source_type, source_id)
        );

        CREATE INDEX ix_knowledge_sources_workspace_id ON knowledge_sources (workspace_id);
        CREATE INDEX ix_knowledge_sources_team_id ON knowledge_sources (team_id);
        CREATE INDEX ix_knowledge_sources_source_type ON knowledge_sources (source_type);
        CREATE INDEX ix_knowledge_sources_source_id ON knowledge_sources (source_id);
        CREATE INDEX ix_knowledge_sources_project_id ON knowledge_sources (project_id);
        CREATE INDEX ix_knowledge_sources_issue_id ON knowledge_sources (issue_id);
        CREATE INDEX ix_knowledge_sources_status ON knowledge_sources (status);

        CREATE TABLE knowledge_chunks (
            id varchar(36) PRIMARY KEY,
            knowledge_source_id varchar(36) NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
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
            CONSTRAINT uq_knowledge_chunks_source_index UNIQUE (knowledge_source_id, chunk_index)
        );

        CREATE INDEX ix_knowledge_chunks_source_id ON knowledge_chunks (knowledge_source_id);
        CREATE INDEX ix_knowledge_chunks_workspace_id ON knowledge_chunks (workspace_id);
        CREATE INDEX ix_knowledge_chunks_team_id ON knowledge_chunks (team_id);
        CREATE INDEX ix_knowledge_chunks_project_id ON knowledge_chunks (project_id);
        CREATE INDEX ix_knowledge_chunks_issue_id ON knowledge_chunks (issue_id);

        -- Rebuild derivative indexes from authoritative records. Debezium will
        -- publish these inserts after its initial consistent snapshot.
        INSERT INTO outbox_events (
            id, workspace_id, aggregate_type, aggregate_id, event_type,
            payload, schema_version, partition_key, created_at
        )
        SELECT gen_random_uuid()::text, issue.workspace_id, 'issue', issue.id,
               'knowledge.source.upserted',
               jsonb_build_object(
                   'schema_version', 1, 'event_type', 'knowledge.source.upserted',
                   'workspace_id', issue.workspace_id, 'source_type', 'issue',
                   'source_id', issue.id, 'source_version', issue.version,
                   'operation', 'upsert', 'metadata', '{}'::jsonb
               ),
               1, issue.workspace_id || ':issue:' || issue.id, now()
        FROM issues AS issue
        WHERE issue.archived_at IS NULL;

        INSERT INTO outbox_events (
            id, workspace_id, aggregate_type, aggregate_id, event_type,
            payload, schema_version, partition_key, created_at
        )
        SELECT gen_random_uuid()::text, team.workspace_id, 'project', project.id,
               'knowledge.source.upserted',
               jsonb_build_object(
                   'schema_version', 1, 'event_type', 'knowledge.source.upserted',
                   'workspace_id', team.workspace_id, 'source_type', 'project',
                   'source_id', project.id, 'source_version', 1,
                   'operation', 'upsert', 'metadata', '{}'::jsonb
               ),
               1, team.workspace_id || ':project:' || project.id, now()
        FROM projects AS project
        JOIN teams AS team ON team.id = project.team_id
        WHERE project.archived_at IS NULL;

        INSERT INTO outbox_events (
            id, workspace_id, aggregate_type, aggregate_id, event_type,
            payload, schema_version, partition_key, created_at
        )
        SELECT gen_random_uuid()::text, issue.workspace_id, 'issue_comment', comment.id,
               'knowledge.source.upserted',
               jsonb_build_object(
                   'schema_version', 1, 'event_type', 'knowledge.source.upserted',
                   'workspace_id', issue.workspace_id, 'source_type', 'issue_comment',
                   'source_id', comment.id, 'source_version', 1,
                   'operation', 'upsert', 'metadata', '{}'::jsonb
               ),
               1, issue.workspace_id || ':issue_comment:' || comment.id, now()
        FROM issue_comments AS comment
        JOIN issues AS issue ON issue.id = comment.issue_id
        WHERE comment.deleted_at IS NULL;

        INSERT INTO outbox_events (
            id, workspace_id, aggregate_type, aggregate_id, event_type,
            payload, schema_version, partition_key, created_at
        )
        SELECT gen_random_uuid()::text, team.workspace_id, 'project_update', update.id,
               'knowledge.source.upserted',
               jsonb_build_object(
                   'schema_version', 1, 'event_type', 'knowledge.source.upserted',
                   'workspace_id', team.workspace_id, 'source_type', 'project_update',
                   'source_id', update.id, 'source_version', 1,
                   'operation', 'upsert', 'metadata', '{}'::jsonb
               ),
               1, team.workspace_id || ':project_update:' || update.id, now()
        FROM project_updates AS update
        JOIN projects AS project ON project.id = update.project_id
        JOIN teams AS team ON team.id = project.team_id
        WHERE project.archived_at IS NULL;

        INSERT INTO outbox_events (
            id, workspace_id, aggregate_type, aggregate_id, event_type,
            payload, schema_version, partition_key, created_at
        )
        SELECT gen_random_uuid()::text, file.workspace_id, 'attachment', file.id,
               'knowledge.source.upserted',
               jsonb_build_object(
                   'schema_version', 1, 'event_type', 'knowledge.source.upserted',
                   'workspace_id', file.workspace_id, 'source_type', 'attachment',
                   'source_id', file.id, 'source_version', 1,
                   'operation', 'upsert', 'metadata', '{}'::jsonb
               ),
               1, file.workspace_id || ':attachment:' || file.id, now()
        FROM file_assets AS file
        WHERE file.upload_status = 'ready'
          AND file.deleted_at IS NULL
          AND (
              EXISTS (SELECT 1 FROM issue_attachments WHERE file_asset_id = file.id)
              OR EXISTS (SELECT 1 FROM project_attachments WHERE file_asset_id = file.id)
          );

        UPDATE outbox_events
        SET payload = payload || jsonb_build_object(
            'event_id', id,
            'occurred_at', created_at
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE knowledge_chunks;
        DROP TABLE knowledge_sources;
        DROP INDEX ix_outbox_events_partition_key;

        ALTER TABLE outbox_events
            DROP COLUMN schema_version,
            DROP COLUMN partition_key,
            ADD COLUMN status varchar(24) NOT NULL DEFAULT 'pending',
            ADD COLUMN attempts integer NOT NULL DEFAULT 0,
            ADD COLUMN available_at timestamptz NOT NULL DEFAULT now(),
            ADD COLUMN locked_at timestamptz,
            ADD COLUMN locked_by varchar(120),
            ADD COLUMN last_error text,
            ADD COLUMN published_at timestamptz;

        ALTER TABLE outbox_events
            ADD CONSTRAINT ck_outbox_status
                CHECK (status IN ('pending', 'processing', 'published', 'failed')),
            ADD CONSTRAINT ck_outbox_attempts CHECK (attempts >= 0);
        CREATE INDEX ix_outbox_pending
            ON outbox_events (status, available_at, created_at);
        """
    )
