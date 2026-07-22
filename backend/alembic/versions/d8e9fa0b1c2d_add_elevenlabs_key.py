"""add per-user ElevenLabs API key (voiceover Reels, R1)

Revision ID: d8e9fa0b1c2d
Revises: c7d8e9fa0b1c
Create Date: 2026-07-22 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd8e9fa0b1c2d'
down_revision = 'c7d8e9fa0b1c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('user_credentials', schema=None) as batch_op:
        batch_op.add_column(sa.Column('elevenlabs_api_key_enc', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('user_credentials', schema=None) as batch_op:
        batch_op.drop_column('elevenlabs_api_key_enc')
