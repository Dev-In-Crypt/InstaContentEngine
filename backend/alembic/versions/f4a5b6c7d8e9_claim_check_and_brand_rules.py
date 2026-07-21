"""Business Phase 4: posts.claim_check + brand_rules table

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-07-21 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'f4a5b6c7d8e9'
down_revision = 'e3f4a5b6c7d8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('claim_check', sa.JSON(), nullable=True))

    op.create_table(
        'brand_rules',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=True),
        sa.Column('forbidden', sa.JSON(), nullable=True),
        sa.Column('required_disclaimers', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('brand_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_brand_rules_workspace_id'),
                              ['workspace_id'], unique=True)


def downgrade() -> None:
    op.drop_table('brand_rules')
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.drop_column('claim_check')
