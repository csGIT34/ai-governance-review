"""assignee, carry forward, evidence queries

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06 21:21:26.031037
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    # server_default so existing rows get '' (the columns are NOT NULL)
    op.add_column("assessment_items", sa.Column("evidence_query", sa.Text(), nullable=False,
                                                server_default=""))
    op.add_column("assessment_items", sa.Column("assignee", sa.String(length=320), nullable=False,
                                                server_default=""))
    op.add_column("library_items", sa.Column("evidence_query", sa.Text(), nullable=False,
                                             server_default=""))
    with op.batch_alter_table("responses") as batch:
        batch.add_column(sa.Column("carried_from_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_responses_carried_from_id", "responses",
                                 ["carried_from_id"], ["id"])


def downgrade():
    with op.batch_alter_table("responses") as batch:
        batch.drop_constraint("fk_responses_carried_from_id", type_="foreignkey")
        batch.drop_column("carried_from_id")
    op.drop_column("library_items", "evidence_query")
    op.drop_column("assessment_items", "assignee")
    op.drop_column("assessment_items", "evidence_query")
