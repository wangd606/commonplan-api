"""add project planning

Revision ID: 20260921_0010
Revises: 20260920_0009
Create Date: 2026-09-21 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260921_0010"
down_revision: Union[str, None] = "20260920_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE projects (
            id VARCHAR(36) PRIMARY KEY,
            team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            slug VARCHAR(80) NOT NULL,
            summary VARCHAR(500) NULL,
            description TEXT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'planned'
                CHECK (status IN ('planned', 'in_progress', 'paused', 'completed', 'canceled')),
            lead_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
            target_date DATE NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_at TIMESTAMPTZ NULL,
            CONSTRAINT uq_projects_team_slug UNIQUE (team_id, slug)
        );
        CREATE INDEX ix_projects_team_status ON projects (team_id, status);
        CREATE INDEX ix_projects_lead ON projects (lead_user_id);

        CREATE TABLE project_objectives (
            id VARCHAR(36) PRIMARY KEY,
            project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            kind VARCHAR(24) NOT NULL CHECK (kind IN ('objective', 'success_criterion')),
            body TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            is_met BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_project_objectives_project_position
            ON project_objectives (project_id, kind, position);

        CREATE TABLE project_milestones (
            id VARCHAR(36) PRIMARY KEY,
            project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            description TEXT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'planned'
                CHECK (status IN ('planned', 'in_progress', 'completed', 'canceled')),
            target_date DATE NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_project_milestones_project_position
            ON project_milestones (project_id, position);

        CREATE TABLE project_updates (
            id VARCHAR(36) PRIMARY KEY,
            project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            author_user_id INTEGER NOT NULL REFERENCES users(id),
            body TEXT NOT NULL,
            health VARCHAR(24) NULL CHECK (health IN ('on_track', 'at_risk', 'off_track')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            edited_at TIMESTAMPTZ NULL
        );
        CREATE INDEX ix_project_updates_project_created
            ON project_updates (project_id, created_at DESC, id);

        ALTER TABLE issues
            ADD COLUMN project_id VARCHAR(36) NULL REFERENCES projects(id) ON DELETE SET NULL,
            ADD COLUMN milestone_id VARCHAR(36) NULL REFERENCES project_milestones(id) ON DELETE SET NULL;
        CREATE INDEX ix_issues_project ON issues (project_id);
        CREATE INDEX ix_issues_milestone ON issues (milestone_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX ix_issues_milestone;
        DROP INDEX ix_issues_project;
        ALTER TABLE issues DROP COLUMN milestone_id, DROP COLUMN project_id;
        DROP TABLE project_updates;
        DROP TABLE project_milestones;
        DROP TABLE project_objectives;
        DROP TABLE projects;
        """
    )
