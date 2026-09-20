"""add workflow states, cycles, issues, and labels

Revision ID: 20260920_0008
Revises: 20260920_0007
Create Date: 2026-09-20 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260920_0008"
down_revision: Union[str, None] = "20260920_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workflow_states (
            id VARCHAR(36) PRIMARY KEY,
            team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            name VARCHAR(80) NOT NULL,
            category VARCHAR(24) NOT NULL,
            position INTEGER NOT NULL,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (team_id, name),
            UNIQUE (team_id, position),
            CONSTRAINT ck_workflow_state_category CHECK (
                category IN ('backlog', 'todo', 'in_progress', 'done', 'canceled')
            )
        );

        INSERT INTO workflow_states (id, team_id, name, category, position, is_default)
        SELECT md5(id || '-backlog'), id, 'Backlog', 'backlog', 0, FALSE FROM teams
        UNION ALL SELECT md5(id || '-todo'), id, 'Todo', 'todo', 1, TRUE FROM teams
        UNION ALL SELECT md5(id || '-in-progress'), id, 'In Progress', 'in_progress', 2, FALSE FROM teams
        UNION ALL SELECT md5(id || '-done'), id, 'Done', 'done', 3, FALSE FROM teams;

        CREATE TABLE cycles (
            id VARCHAR(36) PRIMARY KEY,
            team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            name VARCHAR(120) NOT NULL,
            starts_on DATE NOT NULL,
            ends_on DATE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_cycle_dates CHECK (starts_on < ends_on),
            UNIQUE (team_id, name)
        );
        CREATE INDEX ix_cycles_team_dates ON cycles (team_id, starts_on, ends_on);

        CREATE TABLE labels (
            id VARCHAR(36) PRIMARY KEY,
            team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            name VARCHAR(80) NOT NULL,
            color VARCHAR(16) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (team_id, name)
        );

        CREATE TABLE issues (
            id VARCHAR(36) PRIMARY KEY,
            workspace_id VARCHAR(36) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            number BIGINT NOT NULL,
            key VARCHAR(32) NOT NULL,
            title VARCHAR(500) NOT NULL,
            description TEXT NULL,
            workflow_state_id VARCHAR(36) NOT NULL REFERENCES workflow_states(id),
            priority SMALLINT NOT NULL DEFAULT 0,
            creator_user_id INTEGER NOT NULL REFERENCES users(id),
            assignee_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
            cycle_id VARCHAR(36) NULL REFERENCES cycles(id) ON DELETE SET NULL,
            due_date DATE NULL,
            position NUMERIC(20, 6) NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_at TIMESTAMPTZ NULL,
            UNIQUE (team_id, number),
            UNIQUE (workspace_id, key),
            CONSTRAINT ck_issue_priority CHECK (priority BETWEEN 0 AND 4)
        );
        CREATE INDEX ix_issues_team_state_position ON issues (team_id, workflow_state_id, position, id);
        CREATE INDEX ix_issues_assignee_archive_due ON issues (assignee_user_id, archived_at, due_date);
        CREATE INDEX ix_issues_cycle_archive ON issues (cycle_id, archived_at);

        CREATE TABLE issue_labels (
            issue_id VARCHAR(36) NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            label_id VARCHAR(36) NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
            PRIMARY KEY (issue_id, label_id)
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE issue_labels;
        DROP TABLE issues;
        DROP TABLE labels;
        DROP TABLE cycles;
        DROP TABLE workflow_states;
        """
    )
