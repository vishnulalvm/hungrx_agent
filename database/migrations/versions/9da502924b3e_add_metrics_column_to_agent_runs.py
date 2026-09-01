"""add metrics column to agent_runs

Revision ID: 9da502924b3e
Revises: 2e8de6675983
Create Date: 2026-09-01 09:25:59.566643

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '9da502924b3e'
down_revision = '2e8de6675983'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Note: checkpoints/checkpoint_migrations/checkpoint_blobs/checkpoint_writes
    # are intentionally NOT managed here — see the note in
    # 2e8de6675983_create_source_snapshots_table.py's upgrade().
    op.add_column(
        'agent_runs',
        sa.Column(
            'metrics',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # Drop the server_default after backfilling existing rows — the ORM
    # model always supplies a Python-side default=dict for new rows, the
    # column-level default here exists only to satisfy NOT NULL for rows
    # that already existed before this migration ran.
    op.alter_column('agent_runs', 'metrics', server_default=None)


def downgrade() -> None:
    op.drop_column('agent_runs', 'metrics')
