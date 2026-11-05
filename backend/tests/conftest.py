"""Real PostgreSQL fixtures. Each test owns a random, migrated schema, never public."""

import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.core.security import create_token, hash_password
from app.db.session import make_session_factory
from app.main import create_app
from app.models import Incident, Role, Service, Severity, User
from app.schemas.incidents import IncidentCreate
from app.services.incidents import IncidentService

ROOT = Path(__file__).resolve().parents[2]


def validate_test_url(test_url: str, normal_url: str | None) -> str:
    url = make_url(test_url)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.database != "incidentflow_test"
        or url.username != "incidentflow_test"
        or url.host not in {"localhost", "127.0.0.1"}
        or url.port != 55433
    ):
        raise ValueError("Tests require the dedicated Compose test database on localhost:55433")
    if normal_url:
        normal = make_url(normal_url)
        normal_host = "127.0.0.1" if normal.host == "localhost" else normal.host
        if ("127.0.0.1", url.port, url.database) == (normal_host, normal.port, normal.database):
            raise ValueError("Test and application databases must be different")
    # Compose binds IPv4 only. Avoid Windows' slow IPv6 localhost fallback.
    return url.set(host="127.0.0.1").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def test_url() -> str:
    env = dotenv_values(ROOT / ".env")
    value = os.environ.get("TEST_DATABASE_URL") or env.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("Generate .env and run: docker compose --profile test up -d --wait")
    return validate_test_url(value, os.environ.get("DATABASE_URL") or env.get("DATABASE_URL"))


@pytest.fixture(scope="session")
def password() -> str:
    return token_urlsafe(24)


@pytest.fixture(scope="session")
def password_hash(password: str) -> str:
    return hash_password(password)


@dataclass
class Sandbox:
    app: FastAPI
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    users: dict[Role, User]
    headers: dict[Role, dict[str, str]]
    service: Service
    settings: Settings


@pytest.fixture
async def sandbox(test_url: str, password_hash: str) -> AsyncIterator[Sandbox]:
    schema = f"test_{uuid4().hex}"
    admin_engine = create_async_engine(test_url, poolclass=NullPool, hide_parameters=True)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        test_url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        # Migrations, not metadata.create_all: tests exercise the deployed schema.
        config = Config(str(ROOT / "backend" / "alembic.ini"))
        async with engine.begin() as connection:

            def migrate(sync_connection: object) -> None:
                config.attributes["connection"] = sync_connection
                command.upgrade(config, "head")

            await connection.run_sync(migrate)
        settings = Settings(
            _env_file=None,
            database_url=test_url,
            jwt_secret=token_urlsafe(48),
            environment="test",
            cors_origins=["http://localhost:5173"],
        )
        app = create_app(settings)
        await app.state.engine.dispose()
        app.state.engine = engine
        app.state.session_factory = sessions = make_session_factory(engine)
        users = {
            role: User(
                email=f"{role}@example.com",
                password_hash=password_hash,
                display_name=f"Test {role}",
                role=role,
            )
            for role in Role
        }
        service = Service(name="checkout", description="Checkout API", environment="production")
        async with sessions() as session, session.begin():
            session.add_all([*users.values(), service])
        headers = {
            role: {"Authorization": f"Bearer {create_token(user.id, settings)}"}
            for role, user in users.items()
        }
        async with app.router.lifespan_context(app):
            yield Sandbox(app, engine, sessions, users, headers, service, settings)
    finally:
        await engine.dispose()
        # Only remove the schema generated and created by this fixture.
        if not re.fullmatch(r"test_[0-9a-f]{32}", schema):
            raise RuntimeError("Refusing unsafe schema cleanup")
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.fixture
async def client(sandbox: Sandbox) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=sandbox.app, raise_app_exceptions=False),
        base_url="http://test",
    ) as connection:
        yield connection


@pytest.fixture
async def incident(sandbox: Sandbox) -> Incident:
    async with sandbox.sessions() as session, session.begin():
        return await IncidentService(session).create(
            IncidentCreate(
                title="Checkout unavailable",
                description="HTTP 503s",
                severity=Severity.HIGH,
                service_id=sandbox.service.id,
            ),
            sandbox.users[Role.ENGINEER],
        )


@pytest.fixture
def incident_payload(sandbox: Sandbox) -> dict[str, str]:
    return {
        "title": "Database connections failing",
        "description": "Connection pool exhausted",
        "severity": "critical",
        "service_id": str(sandbox.service.id),
    }
