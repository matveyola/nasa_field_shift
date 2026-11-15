"""Join field onboarding and the agent's farmer planning constraints; no table changes."""

revision = "0012_merge_agent_constraints"
down_revision = ("0011_field_onboarding", "0011_planning_constraints")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
