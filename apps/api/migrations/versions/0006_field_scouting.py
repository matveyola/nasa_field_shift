"""Store field observation categories, locations and JPEG attachments."""

import sqlalchemy as sa
from alembic import op

revision = "0006_field_scouting"
down_revision = "0005_agent_reply_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("observations") as batch:
        batch.add_column(sa.Column("category", sa.String(24), nullable=True))
        batch.add_column(sa.Column("latitude", sa.Float(), nullable=True))
        batch.add_column(sa.Column("longitude", sa.Float(), nullable=True))
        batch.add_column(sa.Column("photo_jpeg", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("client_id", sa.String(36), nullable=True))
        batch.create_unique_constraint("uq_observations_client_id", ["client_id"])
        batch.create_check_constraint(
            op.f("ck_observations_coordinate_pair"),
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL AND "
            "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
        )


def downgrade() -> None:
    with op.batch_alter_table("observations") as batch:
        batch.drop_constraint(op.f("ck_observations_coordinate_pair"), type_="check")
        batch.drop_constraint("uq_observations_client_id", type_="unique")
        for column in ("client_id", "photo_jpeg", "longitude", "latitude", "category"):
            batch.drop_column(column)
