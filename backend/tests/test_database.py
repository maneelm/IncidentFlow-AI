import asyncio
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, Sandbox
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.models import Incident, IncidentEvent, Role, Service, User
from app.schemas.incidents import IncidentUpdate, TransitionInput
from app.services.incidents import IncidentService

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE incidents SET version = 0",
        "UPDATE incidents SET severity = 'urgent'",
        "UPDATE incidents SET status = 'unknown'",
        "UPDATE incidents SET status = 'resolved'",
        "UPDATE incidents SET closed_at = now()",
        "UPDATE incidents SET title = ' '",
        "UPDATE incidents SET service_id = '00000000-0000-0000-0000-000000000001'",
        "UPDATE incidents SET assignee_id = '00000000-0000-0000-0000-000000000001'",
        "UPDATE services SET name = 'UPPERCASE'",
        "UPDATE users SET email = 'UPPER@example.com'",
        "UPDATE users SET role = 'owner'",
        "UPDATE incident_events SET metadata = '{}'::jsonb",
        "DELETE FROM incident_events",
        "DELETE FROM services",
    ],
)
async def test_database_constraints(sandbox: Sandbox, incident: Incident, sql: str) -> None:
    with pytest.raises(IntegrityError):
        async with sandbox.engine.begin() as connection:
            await connection.execute(text(sql))
    async with sandbox.sessions() as session:
        assert (await session.get(Incident, incident.id)).version == 1
        assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 1


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate_email",
        "duplicate_service",
        "duplicate_number",
        "no_actor",
        "two_actors",
        "missing_incident",
        "duplicate_event_version",
    ],
)
async def test_insert_constraints(sandbox: Sandbox, incident: Incident, kind: str) -> None:
    with pytest.raises(IntegrityError):
        async with sandbox.sessions() as session, session.begin():
            if kind == "duplicate_email":
                session.add(
                    User(
                        email=sandbox.users[Role.ADMIN].email,
                        password_hash="unused",
                        display_name="duplicate",
                        role=Role.VIEWER,
                    )
                )
            elif kind == "duplicate_service":
                session.add(Service(name="checkout", description="duplicate", environment="prod"))
            elif kind == "duplicate_number":
                session.add(
                    Incident(
                        number=incident.number,
                        title="Duplicate",
                        description="duplicate",
                        severity="low",
                        status="triggered",
                        service_id=sandbox.service.id,
                    )
                )
            else:
                session.add(
                    IncidentEvent(
                        incident_id=uuid4() if kind == "missing_incident" else incident.id,
                        event_type="updated",
                        details={},
                        incident_version=1 if kind == "duplicate_event_version" else 2,
                        actor_id=None if kind == "no_actor" else sandbox.users[Role.ADMIN].id,
                        system_actor="system" if kind == "two_actors" else None,
                    )
                )
            await session.flush()


@pytest.mark.parametrize("operation", ["update", "transition"])
async def test_database_version_check_when_both_writers_read_old_version(
    sandbox: Sandbox,
    incident: Incident,
    operation: str,
) -> None:
    # A barrier proves both transactions loaded version 1 before either UPDATE.
    # This exercises SQLAlchemy's actual version predicate, not only our preflight check.
    both_read = asyncio.Event()
    count = 0

    async def write(title: str) -> int:
        nonlocal count
        try:
            async with sandbox.sessions() as session, session.begin():
                service = IncidentService(session)
                loaded = await service.get(incident.id)
                assert loaded.version == 1
                count += 1
                if count == 2:
                    both_read.set()
                await both_read.wait()
                if operation == "update":
                    await service.update(
                        incident.id,
                        IncidentUpdate(expected_version=1, title=title),
                        sandbox.users[Role.ADMIN],
                    )
                else:
                    await service.transition(
                        incident.id,
                        TransitionInput(expected_version=1, status="investigating", reason=title),
                        sandbox.users[Role.ADMIN],
                    )
            return 200
        except StaleDataError:
            return 409

    async with asyncio.timeout(10):
        results = await asyncio.gather(write("First operator"), write("Second operator"))
    assert sorted(results) == [200, 409]
    async with sandbox.sessions() as session:
        assert (await session.get(Incident, incident.id)).version == 2
        assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 2


async def test_migration_downgrade_and_upgrade(sandbox: Sandbox) -> None:
    # Destructive migration check is confined to this fixture's disposable schema.
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    async with sandbox.engine.begin() as connection:

        def migrate(sync_connection: object) -> None:
            config.attributes["connection"] = sync_connection
            command.downgrade(config, "base")
            command.upgrade(config, "head")
            command.check(config)

        await connection.run_sync(migrate)
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        assert await connection.scalar(text("SELECT count(*) FROM incidents")) == 0
