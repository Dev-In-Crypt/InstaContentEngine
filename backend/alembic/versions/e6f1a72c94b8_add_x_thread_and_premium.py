"""add X thread parts and premium flag

Revision ID: e6f1a72c94b8
Revises: d5e2b90c7a13
Create Date: 2026-07-20 15:10:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e6f1a72c94b8'
down_revision = 'd5e2b90c7a13'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('thread_parts', sa.JSON(), nullable=True))
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('x_premium', sa.Boolean(), nullable=True))
    op.execute("UPDATE users SET x_premium = FALSE WHERE x_premium IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('x_premium')
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.drop_column('thread_parts')
