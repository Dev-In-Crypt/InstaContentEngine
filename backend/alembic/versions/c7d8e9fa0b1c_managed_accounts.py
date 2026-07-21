"""Phase 7: managed_accounts (agency multi-account) + post/user links

Revision ID: c7d8e9fa0b1c
Revises: b6c7d8e9fa0b
Create Date: 2026-07-21 16:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c7d8e9fa0b1c'
down_revision = 'b6c7d8e9fa0b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'managed_accounts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('owner_user_id', sa.String(length=36), nullable=True),
        sa.Column('name', sa.String(length=120), nullable=True),
        sa.Column('brand_voice_preset', sa.String(length=30), nullable=True),
        sa.Column('brand_voice_custom', sa.Text(), nullable=True),
        sa.Column('niche', sa.String(length=120), nullable=True),
        sa.Column('target_audience', sa.String(length=120), nullable=True),
        sa.Column('brand_name', sa.String(length=120), nullable=True),
        sa.Column('slide_accent_color', sa.String(length=7), nullable=True),
        sa.Column('slide_text_box_color', sa.String(length=7), nullable=True),
        sa.Column('logo_path', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('managed_accounts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_managed_accounts_owner_user_id'),
                              ['owner_user_id'], unique=False)

    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('managed_account_id', sa.String(length=36), nullable=True))
        batch_op.create_index(batch_op.f('ix_posts_managed_account_id'),
                              ['managed_account_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('active_account_id', sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('active_account_id')
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_posts_managed_account_id'))
        batch_op.drop_column('managed_account_id')
    op.drop_table('managed_accounts')
