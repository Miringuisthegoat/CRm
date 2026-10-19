"""Remove fuel logs."""

from alembic import op
import sqlalchemy as sa


revision = "2f3c8a7d91be"
down_revision = "9d5aef655220"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("fuel_logs")


def downgrade():
    op.create_table(
        "fuel_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("liters", sa.Float(), nullable=False),
        sa.Column("cost_kes", sa.Float(), nullable=False),
        sa.Column("odometer_km", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )