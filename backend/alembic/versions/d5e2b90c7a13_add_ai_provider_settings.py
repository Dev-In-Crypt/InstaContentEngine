"""add per-user AI provider settings and per-provider keys

Revision ID: d5e2b90c7a13
Revises: c3d81f5a44e2
Create Date: 2026-07-20 12:40:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5e2b90c7a13'
down_revision = 'c3d81f5a44e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('text_provider', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('text_model', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('image_provider', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('image_model', sa.String(length=120), nullable=True))
    with op.batch_alter_table('user_credentials', schema=None) as batch_op:
        batch_op.add_column(sa.Column('openai_api_key_enc', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('anthropic_api_key_enc', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('google_api_key_enc', sa.Text(), nullable=True))

    # Existing tenants already store an OpenRouter key — keep them working by
    # pointing both modalities at OpenRouter. The model stays NULL, so the UI
    # guard asks them to pick one instead of silently choosing for them.
    op.execute("""
        UPDATE users SET text_provider = 'openrouter', image_provider = 'openrouter'
        WHERE text_provider IS NULL
          AND id IN (SELECT user_id FROM user_credentials
                     WHERE openrouter_api_key_enc IS NOT NULL
                       AND openrouter_api_key_enc <> '')
    """)


def downgrade() -> None:
    with op.batch_alter_table('user_credentials', schema=None) as batch_op:
        batch_op.drop_column('google_api_key_enc')
        batch_op.drop_column('anthropic_api_key_enc')
        batch_op.drop_column('openai_api_key_enc')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('image_model')
        batch_op.drop_column('image_provider')
        batch_op.drop_column('text_model')
        batch_op.drop_column('text_provider')
