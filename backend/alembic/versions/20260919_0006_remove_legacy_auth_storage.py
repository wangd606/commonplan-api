"""remove legacy authentication storage from the business database

Revision ID: 20260919_0006
Revises: 20260910_0005
Create Date: 2026-09-19 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260919_0006"
down_revision: Union[str, None] = "20260910_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS refresh_tokens;

        DROP INDEX IF EXISTS ix_users_google_sub;

        ALTER TABLE users
            DROP COLUMN IF EXISTS google_sub,
            DROP COLUMN IF EXISTS password_hash;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN google_sub VARCHAR(255) NULL,
            ADD COLUMN password_hash VARCHAR(512) NULL;

        CREATE UNIQUE INDEX ix_users_google_sub
            ON users (google_sub);

        CREATE TABLE refresh_tokens (
            id SERIAL PRIMARY KEY,
            token_hash VARCHAR(64) NOT NULL UNIQUE,
            family_id VARCHAR(36) NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ NULL,
            replaced_by_hash VARCHAR(64) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX ix_refresh_tokens_family_id ON refresh_tokens (family_id);
        CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);
        CREATE INDEX ix_refresh_tokens_expires_at ON refresh_tokens (expires_at);
        """
    )
