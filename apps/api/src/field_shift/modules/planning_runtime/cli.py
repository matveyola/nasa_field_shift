"""Local operator import; never exposed as a public arbitrary-artifact endpoint."""

import argparse
from pathlib import Path

from field_shift.config import get_settings
from field_shift.db import build_session_factory
from field_shift.modules.access.service import Principal

from .inputs import InputBundle, install_bundle
from .service import service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    raw = args.bundle.read_bytes()
    if len(raw) > 8_000_000:
        parser.error("Bundle exceeds 8 MB")
    bundle = InputBundle.model_validate_json(raw)
    principal = Principal(None, int(bundle.template.farm_id), "Local operator", None, "admin")
    with build_session_factory(get_settings().database_url).begin() as session:
        identifier = install_bundle(session, bundle, principal, service)
    print("Installed planning bundle", identifier)


if __name__ == "__main__":
    main()
