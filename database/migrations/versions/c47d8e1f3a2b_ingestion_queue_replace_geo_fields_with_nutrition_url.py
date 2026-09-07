"""ingestion queue: drop city/state/country/phone, rename official_url to
menu_url, add nutrition_url

Revision ID: c47d8e1f3a2b
Revises: b3f1a9c2d4e5
Create Date: 2026-09-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c47d8e1f3a2b'
down_revision = 'b3f1a9c2d4e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('ingestion_queue_items', 'official_url', new_column_name='menu_url')
    op.add_column(
        'ingestion_queue_items',
        sa.Column('nutrition_url', sa.String(length=2048), nullable=False, server_default=''),
    )
    op.alter_column('ingestion_queue_items', 'nutrition_url', server_default=None)
    op.drop_column('ingestion_queue_items', 'city')
    op.drop_column('ingestion_queue_items', 'state')
    op.drop_column('ingestion_queue_items', 'country')
    op.drop_column('ingestion_queue_items', 'phone')


def downgrade() -> None:
    op.add_column('ingestion_queue_items', sa.Column('city', sa.String(length=120), nullable=True))
    op.add_column('ingestion_queue_items', sa.Column('state', sa.String(length=120), nullable=True))
    op.add_column('ingestion_queue_items', sa.Column('country', sa.String(length=2), nullable=True))
    op.add_column('ingestion_queue_items', sa.Column('phone', sa.String(length=50), nullable=True))
    op.drop_column('ingestion_queue_items', 'nutrition_url')
    op.alter_column('ingestion_queue_items', 'menu_url', new_column_name='official_url')
