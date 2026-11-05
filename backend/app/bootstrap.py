"""Create one user from environment credentials; no default account and no password CLI flags."""

import argparse
import asyncio
import os

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.errors import DomainError
from app.db.session import make_engine, make_session_factory
from app.models import Role
from app.schemas.auth import BootstrapInput
from app.services.auth import AuthService


async def create_user(role: str) -> None:
    settings = Settings()
    try:
        data = BootstrapInput(
            email=os.environ.get("BOOTSTRAP_EMAIL", ""),
            password=os.environ.get("BOOTSTRAP_PASSWORD", ""),
            display_name=os.environ.get("BOOTSTRAP_DISPLAY_NAME", ""),
            role=role,
        )
    except ValidationError:
        raise SystemExit(
            "Set valid BOOTSTRAP_EMAIL, BOOTSTRAP_DISPLAY_NAME and BOOTSTRAP_PASSWORD "
            "(12-1024 characters). No user was created."
        ) from None
    engine = make_engine(settings)
    try:
        async with make_session_factory(engine)() as session, session.begin():
            user = await AuthService(session, settings).bootstrap(data)
            user_id = user.id
        print(f"Created {role} user with ID {user_id}.")
    except DomainError as exc:
        raise SystemExit(exc.message) from None
    except SQLAlchemyError:
        raise SystemExit(
            "Cannot create user; check database availability, migrations and uniqueness."
        ) from None
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=[role.value for role in Role], required=True)
    asyncio.run(create_user(parser.parse_args().role))


if __name__ == "__main__":
    main()
