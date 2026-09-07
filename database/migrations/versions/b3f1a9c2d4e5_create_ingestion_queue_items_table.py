"""create ingestion queue items table

Revision ID: b3f1a9c2d4e5
Revises: 9da502924b3e
Create Date: 2026-09-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3f1a9c2d4e5'
down_revision = '9da502924b3e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ingestion_queue_items',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('batch_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('official_url', sa.String(length=2048), nullable=False),
        sa.Column('city', sa.String(length=120), nullable=True),
        sa.Column('state', sa.String(length=120), nullable=True),
        sa.Column('country', sa.String(length=2), nullable=True),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column(
            'status',
            sa.Enum('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', name='ingestion_queue_status'),
            nullable=False,
        ),
        sa.Column('restaurant_seed_id', sa.String(length=64), nullable=True),
        sa.Column('error_message', sa.String(length=2000), nullable=True),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_ingestion_queue_items_batch_id'), 'ingestion_queue_items', ['batch_id'], unique=False
    )
    op.create_index(
        op.f('ix_ingestion_queue_items_created_by_user_id'),
        'ingestion_queue_items',
        ['created_by_user_id'],
        unique=False,
    )
    op.create_index(
        'ix_ingestion_queue_items_status_created_at',
        'ingestion_queue_items',
        ['status', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_ingestion_queue_items_status_created_at', table_name='ingestion_queue_items')
    op.drop_index(op.f('ix_ingestion_queue_items_created_by_user_id'), table_name='ingestion_queue_items')
    op.drop_index(op.f('ix_ingestion_queue_items_batch_id'), table_name='ingestion_queue_items')
    op.drop_table('ingestion_queue_items')
    op.execute('DROP TYPE ingestion_queue_status')
