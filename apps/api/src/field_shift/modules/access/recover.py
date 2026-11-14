"""Server-operator password recovery; no remotely callable reset endpoint."""

import argparse
from getpass import getpass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from field_shift.config import get_settings
from field_shift.db import build_session_factory
from field_shift.modules.access.models import LoginSession, Membership, User
from field_shift.modules.access.passwords import password_hash
from field_shift.modules.access.service import lock_farm
from field_shift.modules.farm.models import FARM_ID


def reset_password(session: Session, email: str, password: str) -> None:
    if not 12 <= len(password) <= 128:
        raise ValueError("Use a password of 12–128 characters.")
    lock_farm(session)
    user = session.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            User.email == email.strip().lower(),
            Membership.farm_id == FARM_ID,
            Membership.active.is_(True),
        )
    )
    if user is None:
        raise ValueError("An active team member with this email was not found.")
    user.password_hash = password_hash(password)
    session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="Existing active team member email")
    args = parser.parse_args()
    password = getpass("New password (12–128 characters): ")
    if password != getpass("Repeat new password: "):
        raise SystemExit("Passwords did not match. Nothing changed.")
    factory = build_session_factory(get_settings().database_url)
    try:
        with factory() as session:
            reset_password(session, args.email, password)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    finally:
        factory.kw["bind"].dispose()
    print("Password changed; all previous sessions were revoked. Team role is unchanged.")


if __name__ == "__main__":
    main()
