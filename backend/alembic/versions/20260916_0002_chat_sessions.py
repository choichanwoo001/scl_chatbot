"""Shared expiring chat state for API processes."""

import sqlalchemy as sa
from alembic import context, op
from app.config import settings

revision = "20260916_0002"
down_revision = "20260913_0001"
branch_labels = None
depends_on = None


def upgrade():
    schema = context.config.attributes.get("schema", settings.database_schema)
    op.create_table(
        "chat_sessions",
        sa.Column("session_id", sa.String(80), primary_key=True),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    op.create_index("ix_chat_sessions_expires_at", "chat_sessions", ["expires_at"], schema=schema)
    op.create_index("ix_chat_sessions_updated_at", "chat_sessions", ["updated_at"], schema=schema)
    op.execute(f'REVOKE ALL ON TABLE "{schema}".chat_sessions FROM PUBLIC')
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON TABLE "{schema}".chat_sessions FROM "{role}";
            END IF;
        END $$""")

    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scl_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{schema}".chat_sessions TO scl_app;
        END IF;
    END $$""")


def downgrade():
    schema = context.config.attributes.get("schema", settings.database_schema)
    op.drop_table("chat_sessions", schema=schema)
