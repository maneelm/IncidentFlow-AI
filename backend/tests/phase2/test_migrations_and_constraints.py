from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, Sandbox
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Incident, LogEntry

from .conftest import ingest, make_log

pytestmark = pytest.mark.integration


async def test_phase2_round_trip_preserves_populated_phase1(
    sandbox: Sandbox,
    incident: Incident,
    credential: dict,
    rule: dict,
    client: AsyncClient,
) -> None:
    await ingest(client, sandbox, credential, [make_log()])
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    async with sandbox.engine.begin() as connection:

        def down(sync_connection: object) -> None:
            config.attributes["connection"] = sync_connection
            command.downgrade(config, "0001")

        await connection.run_sync(down)
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
        assert (
            await connection.scalar(
                text("SELECT title FROM incidents WHERE id=:id"), {"id": incident.id}
            )
            == incident.title
        )
        assert await connection.scalar(text("SELECT count(*) FROM incident_events")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM users")) == 3
        assert await connection.scalar(text("SELECT to_regclass('log_entries')")) is None

        def up(sync_connection: object) -> None:
            config.attributes["connection"] = sync_connection
            command.upgrade(config, "head")
            command.check(config)

        await connection.run_sync(up)
        assert await connection.scalar(text("SELECT count(*) FROM incidents")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM incident_events")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM log_entries")) == 0
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE detection_rules SET threshold=0",
        "UPDATE detection_rules SET window_seconds=1",
        "UPDATE detection_rules SET cooldown_seconds=-1",
        "UPDATE log_entries SET level='fatal'",
        "UPDATE log_entries SET metadata='[]'::jsonb",
        "UPDATE detection_jobs SET attempts=-1",
        "UPDATE detection_jobs SET status='processing'",
        "UPDATE detection_jobs SET status='completed'",
    ],
)
async def test_new_database_constraints(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    statement: str,
) -> None:
    await ingest(client, sandbox, credential, [make_log()])
    with pytest.raises(IntegrityError):
        async with sandbox.engine.begin() as connection:
            await connection.execute(text(statement))


async def test_service_source_uniqueness_is_database_enforced(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
) -> None:
    result = await ingest(client, sandbox, credential, [make_log()])
    with pytest.raises(IntegrityError):
        async with sandbox.sessions() as session, session.begin():
            original = await session.get(LogEntry, UUID(result["items"][0]["log_id"]))
            session.add(
                LogEntry(
                    service_id=original.service_id,
                    source_event_id=original.source_event_id,
                    credential_id=original.credential_id,
                    level=original.level,
                    message=original.message,
                    timestamp=original.timestamp,
                    ingested_at=original.ingested_at,
                    details={},
                    content_hash=original.content_hash,
                )
            )
            await session.flush()
