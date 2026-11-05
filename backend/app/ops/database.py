import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import URL

from app.core.config import Settings
from app.db.session import make_engine

EXPECTED_HEAD = "0004"


def container_settings() -> Settings:
    explicit = os.environ.get("CONTAINER_DATABASE_URL")
    if explicit:
        os.environ["DATABASE_URL"] = explicit
    else:
        required = ["DB_USER", "DB_PASSWORD", "DB_NAME"]
        if not all(os.environ.get(key) for key in required):
            raise ValueError("container_database_configuration_missing")
        os.environ["DATABASE_URL"] = URL.create(
            "postgresql+asyncpg",
            username=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            host=os.environ.get("DB_HOST", "postgres"),
            port=5432,
            database=os.environ["DB_NAME"],
        ).render_as_string(hide_password=False)
    return Settings(_env_file=None)


async def verify_head(engine) -> None:
    async with asyncio.timeout(4), engine.connect() as connection:
        revisions = list(await connection.scalars(text("SELECT version_num FROM alembic_version")))
        if revisions != [EXPECTED_HEAD]:
            raise ValueError("migration_revision_mismatch")


async def check(settings: Settings) -> None:
    engine = make_engine(settings)
    try:
        await verify_head(engine)
    finally:
        await engine.dispose()


async def migrate(settings: Settings) -> None:
    engine = make_engine(settings)
    try:
        async with asyncio.timeout(120), engine.begin() as connection:
            locked = await connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtext('incidentflow-schema-migration'))")
            )
            if not locked:
                raise ValueError("migration_already_running")

            def apply(sync_connection):
                config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
                # ops/database.py -> app -> backend: parents[2] is backend.
                config.set_main_option(
                    "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
                )
                config.attributes["connection"] = sync_connection
                command.upgrade(config, "head")
                command.check(config)

            await connection.run_sync(apply)
        await verify_head(engine)
    finally:
        await engine.dispose()
