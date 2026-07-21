"""link posts to Business leads/workspaces (lead_id, workspace_id, source_kind)

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-07-21 13:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e3f4a5b6c7d8'
down_revision = 'd2e3f4a5b6c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lead_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('workspace_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('source_kind', sa.String(length=20), nullable=True))
        batch_op.create_index(batch_op.f('ix_posts_lead_id'), ['lead_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_posts_workspace_id'), ['workspace_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_posts_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_posts_lead_id'))
        batch_op.drop_column('source_kind')
        batch_op.drop_column('workspace_id')
        batch_op.drop_column('lead_id')
