"""Create the Field Shift domain tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("next_field_number", sa.Integer(), nullable=False),
        sa.Column("irrigation_available", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.String(120), nullable=False),
        sa.Column("equipment", sa.String(240), nullable=True),
        sa.Column("budget", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_farms")),
    )
    op.create_table(
        "fields",
        sa.Column("id", sa.String(24), nullable=False),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("crop", sa.String(80), nullable=False),
        sa.Column("area_ha", sa.Float(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("geometry", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"], name=op.f("fk_fields_farm_id_farms")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fields")),
    )
    op.create_index(op.f("ix_fields_farm_id"), "fields", ["farm_id"])
    op.create_table(
        "observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["fields.id"],
            name=op.f("fk_observations_field_id_fields"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_observations")),
    )
    op.create_index(op.f("ix_observations_field_id"), "observations", ["field_id"])
    op.create_table(
        "rotation_preferences",
        sa.Column("field_id", sa.String(24), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("main_plan_id", sa.String(20), nullable=False),
        sa.Column("plan_a_crops", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("plan_b_crops", sa.JSON(none_as_null=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["field_id"], ["fields.id"], name=op.f("fk_rotation_preferences_field_id_fields")
        ),
        sa.PrimaryKeyConstraint("field_id", "season", name=op.f("pk_rotation_preferences")),
    )
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("field_id", sa.String(24), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("scenario", sa.String(20), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["field_id"], ["fields.id"], name=op.f("fk_agent_conversations_field_id_fields")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_conversations")),
    )
    op.create_index(op.f("ix_agent_conversations_field_id"), "agent_conversations", ["field_id"])
    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("demo", sa.Boolean(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            name=op.f("fk_agent_messages_conversation_id_agent_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_messages")),
    )
    op.create_index(
        op.f("ix_agent_messages_conversation_id"), "agent_messages", ["conversation_id"]
    )


def downgrade() -> None:
    op.drop_table("agent_messages")
    op.drop_table("agent_conversations")
    op.drop_table("rotation_preferences")
    op.drop_table("observations")
    op.drop_table("fields")
    op.drop_table("farms")
