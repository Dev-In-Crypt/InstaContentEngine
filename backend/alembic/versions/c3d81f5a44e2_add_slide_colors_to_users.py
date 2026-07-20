"""add slide colours to users

Revision ID: c3d81f5a44e2
Revises: b7c2e9a1f30d
Create Date: 2026-07-20 10:05:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d81f5a44e2'
down_revision = 'b7c2e9a1f30d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('slide_accent_color', sa.String(length=7), nullable=True))
        batch_op.add_column(sa.Column('slide_text_box_color', sa.String(length=7), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('slide_text_box_color')
        batch_op.drop_column('slide_accent_color')
