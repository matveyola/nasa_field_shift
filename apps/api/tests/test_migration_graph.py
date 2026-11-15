"""The Alembic history stays one line: exactly one head, historical revisions untouched, merges
empty.

Existing databases are stamped with revision ids from four independent lines that all start at
0010_precision_plans (team onboarding, the agent's farmer constraints, and the two planning lines).
Renaming a revision makes those databases fail with "Can't locate revision"; re-parenting one makes
Alembic believe tables exist that were never created. So the ids, parents and bytes of every
historical migration are pinned here, and the lines are joined only by empty merge revisions (see
docs/database-migrations.md).
"""

import hashlib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

API_DIR = Path(__file__).resolve().parents[1]

# revision -> (sorted parents, sha256 of the file with LF line endings). Never edit an entry to make
# a test pass: a historical migration that changed is a new migration that should have been written
# instead.
HISTORY = {
    "0001_core": ((), "a918e1c3b1eed4f3a799d0d4fc219175ec3123d99607052324e755504b6b2e08"),
    "0002_jobs": (
        ("0001_core",),
        "e6056a67679711f635ea45140a8384f24e80df73dc81bec13ea2968edbfb31e4",
    ),
    "0003_naip_demo_fields": (
        ("0002_jobs",),
        "30fe530ffc287acc7d8184fc11f50587a4af36e29995ef8691d9ae48e3b5bc3a",
    ),
    "0004_field_plantings": (
        ("0003_naip_demo_fields",),
        "7fb9128830baa4597d88e1c216ffc03c38741f895f6e8fa83b1c201e989cef79",
    ),
    "0005_agent_reply_metadata": (
        ("0004_field_plantings",),
        "1cf4aba4b213f312ca5138cee05600c2b03c60113a0991d0a6558f5ecedcf3e1",
    ),
    "0006_field_scouting": (
        ("0005_agent_reply_metadata",),
        "14edb3307d9c9131e5158a5b6090db627847aa57bc6f51c3fce9ebe2e8212172",
    ),
    "0007_field_boundary_history": (
        ("0006_field_scouting",),
        "9bf54b11f864dd2fa92a15e128a3285dcfd596a7b8a04e318cf23a34545f870c",
    ),
    "0008_team_access": (
        ("0007_field_boundary_history",),
        "c7ee6457c7c708276da71c48680a8974cc42b7569d0b23237379a35470235a6a",
    ),
    "0009_rotation_planning": (
        ("0008_team_access",),
        "5a16a9d4012054f763d079d93292d28baf071875bc4eb9a727b52b246a48d7cc",
    ),
    "0010_precision_plans": (
        ("0009_rotation_planning",),
        "b7802b6a83cd519c79ae401ceb489f6740d1327506aba920630488cce4517256",
    ),
    "0011_field_onboarding": (
        ("0010_precision_plans",),
        "c9c9086cb8ac7e4d7116519dd3e9919703037dd482b92df0bf82adfd0dc0d717",
    ),
    "0011_field_profiles": (
        ("0010_precision_plans",),
        "b46737166432327499494e967581a7bbaf958aab5f8f09212085942d91329ebf",
    ),
    "0011_planning_constraints": (
        ("0010_precision_plans",),
        "ba38a32af1e333da9af5c08191a69e2ae2a50eb0e514d79b5fd5bcca463c7f9c",
    ),
    "0012_planning_runs": (
        ("0010_precision_plans",),
        "8d4885daa1c9bf0b1673614a2d0aded602a72b5555649b1758f653422d48791c",
    ),
    "0013_merge_planning": (
        ("0011_field_profiles", "0012_planning_runs"),
        "dfd5c8c0a3d74f1f0567810761e256925872388fb104381dddd69f71d7403ea6",
    ),
    "0014_planning_inputs": (
        ("0013_merge_planning",),
        "e450c2bb581e81514b6978c639d5e50295436df8076abb7a0f3fd07952106b96",
    ),
}
# The integration merges: each joins exactly these heads and changes no table.
MERGES = {
    "0012_merge_agent_constraints": (
        "0011_field_onboarding",
        "0011_planning_constraints",
    ),  # team main + agent
    "0015_merge_agent_planning": (
        "0011_planning_constraints",
        "0014_planning_inputs",
    ),  # nazar-jobs + agent
    "0016_merge_field_shift": (
        "0012_merge_agent_constraints",
        "0015_merge_agent_planning",
    ),  # both lines (#7)
}
# A line ships whole or not at all: part of one would leave a database half migrated.
LINES = (
    {"0011_field_onboarding"},
    {"0011_field_profiles", "0012_planning_runs", "0013_merge_planning", "0014_planning_inputs"},
)


def _script() -> ScriptDirectory:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "migrations"))
    return ScriptDirectory.from_config(config)


def _parents(revision) -> tuple[str, ...]:
    down = revision.down_revision
    return () if down is None else (down,) if isinstance(down, str) else tuple(sorted(down))


REVISIONS = {revision.revision: revision for revision in _script().walk_revisions()}


def test_there_is_exactly_one_head():
    assert len(_script().get_heads()) == 1, _script().get_heads()


def test_the_shared_history_and_the_agent_line_are_present():
    shared = {name for name in HISTORY if name < "0011"} | {"0011_planning_constraints"}
    assert shared <= set(REVISIONS)


def test_historical_revisions_keep_their_ids_parents_and_bytes():
    for name, (parents, digest) in HISTORY.items():
        if name not in REVISIONS:
            continue
        revision = REVISIONS[name]
        assert _parents(revision) == parents, f"{name} was re-parented"
        text = Path(revision.path).read_bytes().decode("utf-8").replace("\r\n", "\n")
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == digest, f"{name} was edited"


def test_each_line_is_present_whole_or_not_at_all():
    for line in LINES:
        present = line & set(REVISIONS)
        assert present in (set(), line), f"partial line: {sorted(present)}"


def test_merge_revisions_join_exactly_their_heads_and_change_no_table():
    for name, parents in MERGES.items():
        if name not in REVISIONS:
            continue
        revision = REVISIONS[name]
        assert _parents(revision) == tuple(sorted(parents))
        source = Path(revision.path).read_text(encoding="utf-8")
        assert "op." not in source and "sa." not in source and "execute" not in source


def test_every_merge_revision_present_is_one_this_history_knows():
    merges = {name for name, revision in REVISIONS.items() if len(_parents(revision)) > 1}
    assert merges <= set(MERGES) | {"0013_merge_planning"}, sorted(merges)
