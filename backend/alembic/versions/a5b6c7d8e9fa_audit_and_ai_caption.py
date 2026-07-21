"""Business Phase 5: posts.ai_caption + audit_entries table

Revision ID: a5b6c7d8e9fa
Revises: f4a5b6c7d8e9
Create Date: 2026-07-21 14:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a5b6c7d8e9fa'
down_revision = 'f4a5b6c7d8e9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ai_caption', sa.Text(), nullable=True))

    op.create_table(
        'audit_entries',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=True),
        sa.Column('post_id', sa.String(length=36), nullable=True),
        sa.Column('lead_id', sa.String(length=36), nullable=True),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('ai_draft', sa.Text(), nullable=True),
        sa.Column('human_edits', sa.Text(), nullable=True),
        sa.Column('approved_by', sa.String(length=36), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('published_url', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('audit_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_entries_workspace_id'),
                              ['workspace_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_entries_post_id'),
                              ['post_id'], unique=False)
        batch_op.create_index('ix_audit_ws_created', ['workspace_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_table('audit_entries')
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.drop_column('ai_caption')
