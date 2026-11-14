"""Track field boundary revisions and seasonal planted footprints."""

import sqlalchemy as sa
from alembic import op

revision = "0004_field_plantings"
down_revision = "0003_naip_demo_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fields",
        sa.Column("boundary_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_table(
        "field_plantings",
        sa.Column("field_id", sa.String(24), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("geometry", sa.JSON(), nullable=False),
        sa.Column("area_ha", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["fields.id"],
            name=op.f("fk_field_plantings_field_id_fields"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("field_id", "season", name=op.f("pk_field_plantings")),
    )


def downgrade() -> None:
    op.drop_table("field_plantings")
    op.drop_column("fields", "boundary_revision")
