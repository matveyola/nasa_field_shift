"""Validate the repository's development-agent configuration without Codex or dependencies."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLES = {
    "task_lead",
    "implementer",
    "researcher",
    "verifier",
    "test_runner",
    "integrator",
    "product_ceo",
    "product_cto",
    "ui_ux",
}


def validate(root: Path) -> list[str]:
    errors: list[str] = []

    def read_toml(path: Path) -> dict:
        try:
            return tomllib.loads(path.read_text())
        except (OSError, ValueError) as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
            return {}

    config = read_toml(root / ".codex/config.toml")
    agents = config.get("agents", {})
    if not isinstance(agents, dict):
        errors.append("agents must be a TOML table")
        agents = {}
    if agents.get("enabled") is not True:
        errors.append("agents.enabled must be true")
    limit = agents.get("max_concurrent_threads_per_session")
    if type(limit) is not int or not 1 <= limit <= 8:
        errors.append("agent concurrency must be an integer in 1..8 (project budget)")

    names: set[str] = set()
    for path in sorted((root / ".codex/agents").glob("*.toml")):
        role = read_toml(path)
        for key in ("name", "description", "developer_instructions"):
            if not isinstance(role.get(key), str) or not role[key].strip():
                errors.append(f"{path.name}: missing nonempty {key}")
        name = role.get("name")
        if isinstance(name, str):
            if name in names:
                errors.append(f"duplicate role: {name}")
            names.add(name)
            if path.stem != name:
                errors.append(f"{path.name}: filename must match role name {name}")
        forbidden = {"sandbox_mode", "approval_policy", "model_provider", "mcp_servers"}
        for key in sorted(forbidden & role.keys()):
            errors.append(f"{path.name}: {key} belongs to the parent session")
    if names != ROLES:
        errors.append(f"role inventory mismatch: missing={ROLES - names}, extra={names - ROLES}")

    skill = root / ".agents/skills/agent-tree/SKILL.md"
    documents = [
        root / "AGENTS.md",
        root / "docs/agent-tree.md",
        skill,
        skill.parent / "references/test-matrix.md",
    ]
    for path in documents:
        try:
            content = path.read_text()
        except OSError as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
            continue
        if path == skill:
            if not content.startswith("---\n"):
                errors.append("agent-tree skill must have YAML frontmatter")
            else:
                parts = content.split("---", 2)
                header = parts[1] if len(parts) == 3 else ""
                if not re.search(r"(?m)^name: agent-tree$", header):
                    errors.append("skill name must be agent-tree")
                if not re.search(r"(?m)^description: \S.+$", header):
                    errors.append("skill description is required")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
            if "://" in target or target.startswith("#"):
                continue
            relative = target.split("#", 1)[0]
            if not (path.parent / relative).exists():
                errors.append(f"{path.relative_to(root)}: broken link {target}")
    return errors


def main() -> int:
    errors = validate(ROOT)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Agent configuration, role inventory and local documentation links: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
