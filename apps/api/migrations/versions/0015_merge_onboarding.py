"""Join published onboarding and planning histories without changing either lineage."""

revision = "0015_merge_onboarding"
down_revision = ("0014_planning_inputs", "0011_field_onboarding")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
