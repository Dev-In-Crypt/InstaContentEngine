"""add per-tenant brand logo path

Revision ID: a1f2c3d4e5b6
Revises: e6f1a72c94b8
Create Date: 2026-07-20 17:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1f2c3d4e5b6'
down_revision = 'e6f1a72c94b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('logo_path', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('logo_path')
