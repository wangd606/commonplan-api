"""add issue comments and activity events

Revision ID: 20260920_0009
Revises: 20260920_0008
Create Date: 2026-09-20 18:45:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260920_0009"
down_revision: Union[str, None] = "20260920_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE issue_comments (
            id VARCHAR(36) PRIMARY KEY,
            issue_id VARCHAR(36) NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            author_user_id INTEGER NOT NULL REFERENCES users(id),
            body TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            edited_at TIMESTAMPTZ NULL,
            deleted_at TIMESTAMPTZ NULL
        );
        CREATE INDEX ix_issue_comments_issue_created
            ON issue_comments (issue_id, created_at, id);

        CREATE TABLE issue_events (
            id VARCHAR(36) PRIMARY KEY,
            issue_id VARCHAR(36) NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            actor_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
            event_type VARCHAR(64) NOT NULL,
            changes JSONB NOT NULL DEFAULT CAST('{}' AS JSONB),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_issue_events_issue_created
            ON issue_events (issue_id, created_at, id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE issue_events;
        DROP TABLE issue_comments;
        """
    )
