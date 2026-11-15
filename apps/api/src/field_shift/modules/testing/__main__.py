"""Explicit CLI; import and application startup never seed or fetch data."""

import argparse
import fcntl
import json
import os
import secrets
import stat
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from field_shift.db import build_engine

from .cases import VERSION, dataset
from .seed import plan_hash, seed_workspace


def _private_file(path: Path):
    if not path.is_absolute():
        raise ValueError("Credentials must use an absolute path outside the source checkout.")
    parent = path.parent.resolve(strict=True)
    if any((p / ".git").exists() for p in (parent, *parent.parents)):
        raise ValueError("Credentials cannot be stored inside a Git checkout.")
    if parent.stat().st_uid != os.getuid() or stat.S_IMODE(parent.stat().st_mode) & 0o077:
        raise ValueError("Credential parent directory must be owned by you with mode 0700.")
    fd = os.open(parent / path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ValueError("Credential file must be an owned, unlinked regular mode 0600 file.")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return os.fdopen(fd, "r+", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


def seed_database(database_url: str, credentials: Path, season: int) -> dict:
    if not 2001 <= season <= 2100:
        raise ValueError("Season must be 2001..2100.")
    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() == "sqlite" and (
        not parsed_url.database or not Path(parsed_url.database).is_absolute()
    ):
        raise ValueError("The isolated SQLite database must use an absolute persistent path.")
    identity = sha256(
        make_url(database_url).render_as_string(hide_password=True).encode()
    ).hexdigest()
    engine = build_engine(database_url)
    try:
        with _private_file(credentials) as file:
            content = file.read()
            if content:
                receipt = json.loads(content)
                if (
                    receipt["version"] != VERSION
                    or receipt["database_identity"] != identity
                    or receipt["season"] != season
                ):
                    raise ValueError("Receipt belongs to another version, database or season.")
            else:
                seed_id = str(uuid4())
                receipt = {
                    "version": VERSION,
                    "created_at": datetime.now(UTC).isoformat(),
                    "seed_id": seed_id,
                    "database_identity": identity,
                    "season": season,
                    "plan_sha256": plan_hash(season),
                    "email": f"global-tester-{seed_id}@example.invalid",
                    "password": secrets.token_urlsafe(32),
                    "bootstrap_token": secrets.token_urlsafe(32),
                    "dataset": dataset(season),
                    "field_ids": {
                        case["id"]: f"F-{i:02d}"
                        for i, case in enumerate(dataset(season)["cases"], 1)
                    },
                }
                # Durable before any DB write. An interrupted/rolled-back seed can retry
                # with the same identity; a lost post-commit response cannot lose its password.
                file.write(json.dumps(receipt, indent=2) + "\n")
                file.flush()
                os.fsync(file.fileno())
                directory = os.open(credentials.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            with engine.begin() as connection:
                tables = inspect(connection).get_table_names()
                if not {"farms", "fields", "access_users", "access_memberships"} <= set(tables):
                    raise ValueError("Run repository migrations on the isolated database first.")
                farms = Table("farms", MetaData(), autoload_with=connection)
                if connection.execute(select(farms.c.id).limit(1)).first() is None:
                    for name in tables:
                        if name == "alembic_version":
                            continue
                        table = Table(name, MetaData(), autoload_with=connection)
                        if connection.execute(select(table).limit(1)).first() is not None:
                            raise ValueError(
                                "Refusing nonempty database; no existing data adopted."
                            )
                # Domain services may commit; savepoint joining keeps those commits
                # within this outer all-or-nothing transaction (including field history).
                with Session(connection, join_transaction_mode="create_savepoint") as session:
                    result = seed_workspace(session, receipt)
            return {
                **result,
                "version": VERSION,
                "credentials_file": str(credentials),
                "plan_sha256": receipt["plan_sha256"],
            }
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--confirm-isolated-database", action="store_true", required=True)
    args = parser.parse_args()
    url = os.environ.get("FIELD_SHIFT_TESTER_DATABASE_URL")
    if not url:
        parser.error("Set FIELD_SHIFT_TESTER_DATABASE_URL explicitly; no default DB is used.")
    try:
        result = seed_database(url, args.credentials, args.season)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Tester seed refused: {error}\n")
    except SQLAlchemyError:
        parser.exit(
            1,
            "Tester seed database operation failed; transaction rolled back. "
            "Inspect the isolated database without logging credentials.\n",
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
