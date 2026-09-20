"""add workspaces, teams, memberships, and invitations

Revision ID: 20260920_0007
Revises: 20260919_0006
Create Date: 2026-09-20 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260920_0007"
down_revision: Union[str, None] = "20260919_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspaces (
            id VARCHAR(36) PRIMARY KEY,
            slug VARCHAR(80) NOT NULL UNIQUE,
            name VARCHAR(255) NOT NULL,
            description TEXT NULL,
            created_by_user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_at TIMESTAMPTZ NULL
        );

        CREATE TABLE workspace_memberships (
            workspace_id VARCHAR(36) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, user_id),
            CONSTRAINT ck_workspace_membership_role CHECK (role IN ('owner', 'admin', 'member')),
            CONSTRAINT ck_workspace_membership_status CHECK (status IN ('active', 'suspended'))
        );
        CREATE INDEX ix_workspace_memberships_user_id ON workspace_memberships (user_id);

        CREATE TABLE teams (
            id VARCHAR(36) PRIMARY KEY,
            workspace_id VARCHAR(36) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            description TEXT NULL,
            issue_prefix VARCHAR(12) NOT NULL,
            next_issue_number BIGINT NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_at TIMESTAMPTZ NULL,
            UNIQUE (workspace_id, issue_prefix)
        );
        CREATE INDEX ix_teams_workspace_id ON teams (workspace_id);

        CREATE TABLE team_memberships (
            team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role VARCHAR(16) NOT NULL,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (team_id, user_id),
            CONSTRAINT ck_team_membership_role CHECK (role IN ('lead', 'member'))
        );
        CREATE INDEX ix_team_memberships_user_id ON team_memberships (user_id);

        CREATE TABLE workspace_invitations (
            id VARCHAR(36) PRIMARY KEY,
            workspace_id VARCHAR(36) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            email VARCHAR(320) NOT NULL,
            role VARCHAR(16) NOT NULL,
            team_id VARCHAR(36) NULL REFERENCES teams(id) ON DELETE SET NULL,
            token_hash VARCHAR(64) NOT NULL UNIQUE,
            invited_by_user_id INTEGER NOT NULL REFERENCES users(id),
            expires_at TIMESTAMPTZ NOT NULL,
            accepted_at TIMESTAMPTZ NULL,
            revoked_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_workspace_invitation_role CHECK (role IN ('admin', 'member'))
        );
        CREATE INDEX ix_workspace_invitations_workspace_id ON workspace_invitations (workspace_id);
        CREATE UNIQUE INDEX ux_workspace_invitations_active_email
            ON workspace_invitations (workspace_id, lower(email))
            WHERE accepted_at IS NULL AND revoked_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE workspace_invitations;
        DROP TABLE team_memberships;
        DROP TABLE teams;
        DROP TABLE workspace_memberships;
        DROP TABLE workspaces;
        """
    )
