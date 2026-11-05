"""Private Playwright fixture server; only the dedicated local test DB is accepted.

Ready credentials travel over the parent-owned stdout pipe, never a repository file.
EOF on stdin stops owned workers/server and drops only this generated test schema.
"""

import asyncio
import json
import os
import re
import socket
import sys
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import make_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.simulations.demo import demonstrate  # noqa: E402
from app.workers.detection_worker import DetectionWorker  # noqa: E402
from app.workers.investigation_worker import InvestigationWorker  # noqa: E402
from app.workers.resolution_worker import ResolutionWorker  # noqa: E402


async def main():
    private = dotenv_values(ROOT / ".env")
    value = os.environ.get("TEST_DATABASE_URL") or private.get("TEST_DATABASE_URL")
    if not value:
        raise RuntimeError("Start the dedicated Compose test database first")
    target = make_url(value)
    if (target.drivername, target.username, target.database, target.port) != (
        "postgresql+asyncpg",
        "incidentflow_test",
        "incidentflow_test",
        55433,
    ) or target.host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("Browser fixtures require the dedicated local test database")
    url = target.set(host="127.0.0.1").render_as_string(hide_password=False)
    schema = f"test_{uuid4().hex}"
    admin = create_async_engine(url, poolclass=NullPool, hide_parameters=True)
    engine = create_async_engine(
        url, hide_parameters=True, connect_args={"server_settings": {"search_path": schema}}
    )
    stop = asyncio.Event()
    tasks = []
    server = None
    created = False
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
        config = Config(str(ROOT / "backend" / "alembic.ini"))
        async with engine.begin() as connection:

            def migrate(sync):
                config.attributes["connection"] = sync
                command.upgrade(config, "head")

            await connection.run_sync(migrate)
        settings = Settings(
            _env_file=None,
            database_url=url,
            jwt_secret=token_urlsafe(48),
            environment="test",
            cors_origins=[],
            investigation_llm_provider="fake",
            investigation_llm_model="fake-v1",
            worker_poll_seconds=0.1,
        )
        app = create_app(settings)
        await app.state.engine.dispose()
        app.state.engine = engine
        sessions = make_session_factory(engine)
        app.state.session_factory = sessions
        credentials = {}
        async with sessions() as session, session.begin():
            for role in Role:
                password = token_urlsafe(30)
                email = f"browser-{role}-{uuid4().hex[:8]}@example.com"
                session.add(
                    User(
                        email=email,
                        display_name=f"Browser {role}",
                        role=role,
                        password_hash=hash_password(password),
                    )
                )
                credentials[str(role)] = {"email": email, "password": password}
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        tasks.append(asyncio.create_task(server.serve(sockets=[listener])))

        async def work(worker):
            while not stop.is_set():
                await worker.run_once(stop)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.1)
                except TimeoutError:
                    pass

        for worker in (
            DetectionWorker(sessions, settings),
            InvestigationWorker(sessions, settings),
            ResolutionWorker(sessions, settings),
        ):
            tasks.append(asyncio.create_task(work(worker)))
        async with asyncio.timeout(20):
            while not server.started:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.05)
                except TimeoutError:
                    pass

        def seed():
            with httpx.Client(base_url=origin, timeout=10) as client:
                account = credentials["admin"]
                token = client.post(
                    "/api/v1/auth/token",
                    data={"username": account["email"], "password": account["password"]},
                ).json()["access_token"]
                client.headers["Authorization"] = f"Bearer {token}"
                context = demonstrate(client, "database", 42, 60)
                response = client.post(
                    f"/api/v1/services/{context['service_id']}/synthetic-state",
                    json={"scenario": "database_exhaustion", "expected_version": 0},
                )
                assert response.status_code == 200
                runbook = client.post(
                    "/api/v1/runbooks",
                    json={
                        "title": "Browser database pool runbook",
                        "service_id": context["service_id"],
                        "content": "DB_CONNECTION_TIMEOUT: inspect active connections, "
                        "pool capacity and waiting requests using read-only diagnostics. "
                        "Human review is required before synthetic recovery.",
                    },
                )
                assert runbook.status_code == 201
                # More than one page of real logs supports browser pagination assertions.
                key = client.post(
                    "/api/v1/ingestion-credentials",
                    json={
                        "service_id": context["service_id"],
                        "name": "browser pagination producer",
                        "expires_in_days": 1,
                    },
                ).json()
                from datetime import UTC, datetime

                try:
                    logs = [
                        {
                            "source_event_id": f"pagination-{index}",
                            "level": "info",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "message": f"SYNTHETIC pagination observation {index}",
                            "metadata": {"synthetic": True},
                        }
                        for index in range(25)
                    ]
                    assert (
                        client.post(
                            "/api/v1/logs/ingest",
                            headers={"X-Ingestion-Key": key["token"]},
                            json={"service_id": context["service_id"], "logs": logs},
                        ).status_code
                        == 202
                    )
                finally:
                    client.post(f"/api/v1/ingestion-credentials/{key['id']}/revoke")
                return context | {"runbook_id": runbook.json()["id"]}

        fixture = await asyncio.to_thread(seed)
        print(
            json.dumps({"type": "ready", "api_url": origin, "accounts": credentials, **fixture}),
            flush=True,
        )
        await asyncio.to_thread(sys.stdin.readline)
    finally:
        stop.set()
        if server:
            server.should_exit = True
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()
        if created:
            if not re.fullmatch(r"test_[0-9a-f]{32}", schema):
                raise RuntimeError("Unsafe fixture schema")
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(f"Browser fixture server failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1) from None
