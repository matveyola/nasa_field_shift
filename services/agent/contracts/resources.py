"""Read-only JSON resources shipped with the public Python contract package."""

from importlib.resources import files

FIXTURES = files("contracts").joinpath("fixtures")
SCHEMAS = files("contracts").joinpath("schema")


def field_context_fixtures():
    return sorted(
        (
            path
            for path in FIXTURES.iterdir()
            if path.name.startswith("field_context.") and path.name.endswith(".json")
        ),
        key=lambda path: path.name,
    )
