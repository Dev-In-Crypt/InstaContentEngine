"""add brand profile to users

Revision ID: b7c2e9a1f30d
Revises: f4ab42df24eb
Create Date: 2026-07-19 18:20:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c2e9a1f30d'
down_revision = 'f4ab42df24eb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('niche', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('target_audience', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('brand_name', sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('brand_name')
        batch_op.drop_column('target_audience')
        batch_op.drop_column('niche')
