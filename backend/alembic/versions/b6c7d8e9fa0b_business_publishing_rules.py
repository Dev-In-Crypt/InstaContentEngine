"""Business Phase 6: workspace frequency caps + lead.sensitive

Revision ID: b6c7d8e9fa0b
Revises: a5b6c7d8e9fa
Create Date: 2026-07-21 15:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b6c7d8e9fa0b'
down_revision = 'a5b6c7d8e9fa'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('workspaces', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_per_day', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('max_per_week', sa.Integer(), nullable=True))
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sensitive', sa.Boolean(), nullable=True))
    op.execute("UPDATE leads SET sensitive = FALSE WHERE sensitive IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_column('sensitive')
    with op.batch_alter_table('workspaces', schema=None) as batch_op:
        batch_op.drop_column('max_per_week')
        batch_op.drop_column('max_per_day')
