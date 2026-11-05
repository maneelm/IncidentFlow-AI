"""Real, isolated Compose acceptance test. Never loads the development database.

Requires built images, uv dependencies, npm ci and Playwright Chromium. Generates
private runtime credentials under ignored .tools; prints only safe stage results.
The owned named volume is retained after `down` for inspection, never deleted.
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.simulations.demo import checked, demonstrate, wait_for_jobs  # noqa: E402


def run(args, *, env=None, data=None, timeout=240, required=True):
    result = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        input=data,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        encoding="utf-8",
    )
    if required and result.returncode:
        raise RuntimeError("external_command_failed")
    return result


def wait_until(check, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.25)
    raise RuntimeError("verification_deadline_exceeded")


def image_secrets_absent(images, needles, directory):
    for index, image in enumerate(images):
        inspect = json.loads(run(["docker", "image", "inspect", image]).stdout)[0]
        assert inspect["Config"]["User"] not in {"", "0", "root", "0:0"}
        assert not any(
            item.startswith(("JWT_SECRET=", "GROQ_API_KEY=", "DB_PASSWORD="))
            for item in inspect["Config"]["Env"]
        )
        archive = directory / f"image-{index}.tar"
        run(["docker", "image", "save", "-o", str(archive), image])
        # Docker save stores config and uncompressed layer tar members. Preserve
        # overlap so a literal secret split between read blocks cannot evade this.
        overlap = max(len(needle) for needle in needles)
        tail = b""
        try:
            with archive.open("rb") as source:
                while block := source.read(1024 * 1024):
                    data = tail + block
                    assert not any(needle in data for needle in needles), "image_secret_found"
                    tail = data[-overlap:]
        finally:
            archive.unlink(missing_ok=True)


class Verification:
    def __init__(self):
        self.project = f"incidentflow-verify-{uuid4().hex[:12]}"
        self.directory = ROOT / ".tools" / self.project
        self.directory.mkdir(parents=True)
        self.envfile = self.directory / ".env"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.values = {
            "DB_USER": "incidentflow",
            "DB_PASSWORD": token_urlsafe(48),
            "DB_NAME": "incidentflow",
            "JWT_SECRET": token_urlsafe(48),
            "TEST_DB_PASSWORD": token_urlsafe(48),
            "ENVIRONMENT": "development",
            "CONTAINER_CORS_ORIGINS": "[]",
            "FRONTEND_PORT": str(self.port),
            "GROQ_API_KEY": "",
            "INVESTIGATION_LLM_PROVIDER": "fake",
            "INVESTIGATION_LLM_MODEL": "fake-v1",
            "WORKER_LEASE_SECONDS": "15",
            "WORKER_TIMEOUT_SECONDS": "5",
            "WORKER_POLL_SECONDS": "0.2",
        }
        prefixes = (
            "DB_",
            "TEST_",
            "JWT_",
            "GROQ_",
            "WORKER_",
            "INVESTIGATION_",
            "RESOLUTION_",
            "COMPOSE_",
            "CONTAINER_",
            "CORS_",
            "ENVIRONMENT",
            "FRONTEND_",
            "DATABASE_URL",
        )
        self.env = {key: value for key, value in os.environ.items() if not key.startswith(prefixes)}
        self.accounts = {
            role: {
                "email": f"browser-{role}-{uuid4().hex[:8]}@example.com",
                "password": token_urlsafe(30),
            }
            for role in ("admin", "engineer", "viewer")
        }
        self.write_env()

    def write_env(self):
        self.envfile.write_text(
            "".join(f"{key}={value}\n" for key, value in self.values.items()), encoding="utf-8"
        )
        self.envfile.chmod(0o600)

    def compose(self, *args, data=None, extra=None, **kwargs):
        return run(
            [
                "docker",
                "compose",
                "-p",
                self.project,
                "--env-file",
                str(self.envfile),
                "-f",
                str(ROOT / "compose.yaml"),
                *args,
            ],
            data=data,
            env=self.env | (extra or {}),
            **kwargs,
        )

    def sql(self, query, *, database=None):
        target = database or self.values["DB_NAME"]
        # Fixed/owned database identifiers only; no SQL is built from API input.
        assert re.fullmatch(r"[a-z_]+", target)
        return self.compose(
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            f'exec psql -U "$POSTGRES_USER" -d {target} -At -v ON_ERROR_STOP=1',
            data=query,
        ).stdout.strip()

    def counts(self, database=None):
        return json.loads(
            self.sql(
                "SELECT json_build_object("
                + ",".join(
                    f"'{table}',(SELECT count(*) FROM {table})"
                    for table in (
                        "users",
                        "incidents",
                        "investigation_reports",
                        "resolution_events",
                    )
                )
                + ");",
                database=database,
            )
        )

    def provision(self):
        for role, account in self.accounts.items():
            self.compose(
                "exec",
                "-T",
                "-e",
                "BOOTSTRAP_EMAIL",
                "-e",
                "BOOTSTRAP_PASSWORD",
                "-e",
                "BOOTSTRAP_DISPLAY_NAME",
                "api",
                "python",
                "-m",
                "app.ops.launch",
                "bootstrap",
                "--user-role",
                role,
                extra={
                    "BOOTSTRAP_EMAIL": account["email"],
                    "BOOTSTRAP_PASSWORD": account["password"],
                    "BOOTSTRAP_DISPLAY_NAME": f"Browser {role}",
                },
            )

    def login(self, client):
        account = self.accounts["admin"]
        result = checked(
            client.post(
                "/api/v1/auth/token",
                data={"username": account["email"], "password": account["password"]},
            ),
            200,
        )
        client.headers["Authorization"] = f"Bearer {result['access_token']}"

    def seed(self, client):
        context = demonstrate(client, "database", 42, 60)
        checked(
            client.post(
                f"/api/v1/services/{context['service_id']}/synthetic-state",
                json={"scenario": "database_exhaustion", "expected_version": 0},
            ),
            200,
        )
        book = checked(
            client.post(
                "/api/v1/runbooks",
                json={
                    "title": "Browser database pool runbook",
                    "service_id": context["service_id"],
                    "content": "DB_CONNECTION_TIMEOUT: inspect active connections and "
                    "pool capacity with read-only diagnostics. Human review is required.",
                },
            ),
            201,
        )
        key = checked(
            client.post(
                "/api/v1/ingestion-credentials",
                json={"service_id": context["service_id"], "name": "container producer"},
            ),
            201,
        )
        checked(
            client.post(
                "/api/v1/logs/ingest",
                headers={"X-Ingestion-Key": key["token"]},
                json={
                    "service_id": context["service_id"],
                    "logs": [
                        {
                            "source_event_id": f"container-page-{i}",
                            "level": "info",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "message": f"SYNTHETIC container pagination {i}",
                            "metadata": {"synthetic": True},
                        }
                        for i in range(25)
                    ],
                },
            ),
            202,
        )
        wait_for_jobs(client, context["service_id"], 60)
        return context | {"runbook_id": book["id"]}, key

    def browser(self, fixture):
        node = shutil.which("node")
        local = ROOT / ".tools/node_modules/node/bin/node.exe"
        if os.name == "nt" and local.exists():
            node = str(local)
        assert node, "supported_node_required"
        result = subprocess.run(
            [node, "node_modules/@playwright/test/cli.js", "test"],
            cwd=ROOT / "frontend",
            env=self.env
            | {
                "E2E_BASE_URL": self.base,
                "E2E_FIXTURE_JSON": json.dumps(
                    fixture | {"api_url": self.base, "accounts": self.accounts}
                ),
            },
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            encoding="utf-8",
        )
        # Failures can contain response data/screenshots: do not forward raw output.
        assert result.returncode == 0, "container_browser_workflow_failed"
        passed = re.search(r"(\d+) passed", result.stdout)
        assert passed, "browser_result_count_missing"
        print(f"PASS {passed[1]} Chromium workflows through Nginx", flush=True)

    def crash_recovery(self, client, fixture, key):
        self.compose("stop", "detection-worker")
        queued = checked(
            client.post(
                "/api/v1/logs/ingest",
                headers={"X-Ingestion-Key": key["token"]},
                json={
                    "service_id": fixture["service_id"],
                    "logs": [
                        {
                            "source_event_id": f"crash-{uuid4().hex}",
                            "level": "info",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "message": "SYNTHETIC recovery witness",
                        }
                    ],
                },
            ),
            202,
        )["items"][0]["job_id"]
        probe = f"{self.project}-claim-probe"
        program = """import asyncio
from app.ops.database import container_settings
from app.db.session import make_engine,make_session_factory
from app.workers.detection_worker import DetectionWorker
async def main():
 s=container_settings();e=make_engine(s)
 claim=await DetectionWorker(make_session_factory(e),s).claim_one()
 assert claim
 print(str(claim.job_id),flush=True)
 await asyncio.Event().wait()
asyncio.run(main())"""
        self.compose(
            "run",
            "-d",
            "--no-deps",
            "--name",
            probe,
            "--entrypoint",
            "python",
            "detection-worker",
            "-c",
            program,
        )
        try:
            wait_until(lambda: queued in run(["docker", "logs", probe]).stdout, 20)
            run(["docker", "kill", "--signal", "KILL", probe])
            self.compose("start", "detection-worker")
            jobs = wait_for_jobs(client, fixture["service_id"], 60)
            recovered = next(job for job in jobs["items"] if job["id"] == queued)
            assert recovered["attempts"] == 2 and recovered["status"] == "completed"
        finally:
            run(["docker", "rm", "-f", probe], required=False)
        print("PASS killed worker claim recovered after lease expiry", flush=True)

    def verify(self):
        config = json.loads(self.compose("config", "--format", "json").stdout)
        ports = {name: service.get("ports", []) for name, service in config["services"].items()}
        assert all(not value for key, value in ports.items() if key != "frontend")
        assert ports["frontend"][0]["host_ip"] == "127.0.0.1"
        for name in ("detection-worker", "resolution-worker", "migrate", "frontend"):
            assert "GROQ_API_KEY" not in config["services"][name].get("environment", {})
        self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
        print("PASS empty database migration and six healthy services", flush=True)
        self.provision()
        with httpx.Client(base_url=self.base, timeout=10, trust_env=False) as client:
            assert client.get("/api/v1/health").status_code == 200
            assert client.get("/api/v1/ready").status_code == 200
            assert client.get("/api/v1/ready").headers["cache-control"] == "no-store"
            assert client.get("/api/v1/incidents").status_code == 401
            assert (
                client.get(
                    "/api/v1/auth/me", headers={"Authorization": "Bearer invalid"}
                ).status_code
                == 401
            )
            response = client.get("/incidents/refresh-check")
            assert response.status_code == 200 and "no-store" in response.headers["cache-control"]
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            asset = re.search(r'src="(/assets/[^" ]+\.js)"', response.text)
            assert asset, "fingerprinted_script_missing"
            asset_response = client.get(asset[1])
            assert asset_response.status_code == 200
            assert "immutable" in asset_response.headers["cache-control"]
            assert asset_response.headers["x-content-type-options"] == "nosniff"
            assert (
                client.post("/api/v1/auth/token", content=b"x" * (2097152 + 1)).status_code == 413
            )
            self.login(client)
            fixture, key = self.seed(client)
            assert (
                checked(client.get("/api/v1/investigation-capabilities"), 200)["synthetic"] is True
            )
            self.browser(fixture)
            self.crash_recovery(client, fixture, key)
            checked(client.post(f"/api/v1/ingestion-credentials/{key['id']}/revoke"), 200)
            before = self.counts()
            assert before["users"] == 3 and before["investigation_reports"] >= 1
            assert before["resolution_events"] > 0
            self.compose("restart", "investigation-worker", "resolution-worker")
            self.compose("down")  # Never -v: retain the owned named volume.
            self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
            assert self.counts() == before
            self.login(client)
            print("PASS populated migration, down/up persistence and worker restarts", flush=True)
            self.compose(
                "exec",
                "-T",
                "postgres",
                "sh",
                "-c",
                'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/verify.dump && '
                'createdb -U "$POSTGRES_USER" incidentflow_restorecheck && '
                'pg_restore -U "$POSTGRES_USER" --exit-on-error --no-owner --no-privileges '
                "-d incidentflow_restorecheck /tmp/verify.dump",
            )
            assert self.counts("incidentflow_restorecheck") == before
            print("PASS native PostgreSQL backup restored to a separate database", flush=True)
            rejected = self.compose(
                "run",
                "--rm",
                "--no-deps",
                "-e",
                "ENVIRONMENT=production",
                "-e",
                "CORS_ORIGINS=[]",
                "api",
                "check",
                required=False,
            )
            assert rejected.returncode != 0 and "ValidationError" in rejected.stderr
            self.values.update(INVESTIGATION_LLM_PROVIDER="groq", INVESTIGATION_LLM_MODEL="")
            self.write_env()
            self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
            assert client.get("/api/v1/ready").status_code == 200
            assert client.get("/api/v1/incidents").status_code == 200
            assert (
                client.post(
                    f"/api/v1/incidents/{fixture['incident_id']}/investigations", json={}
                ).status_code
                == 503
            )
            print("PASS missing Groq isolation and production fake-provider rejection", flush=True)
        local = dotenv_values(ROOT / ".env")
        needles = [
            value.encode()
            for key, value in (local | self.values).items()
            if value and key in {"GROQ_API_KEY", "JWT_SECRET", "DB_PASSWORD"} and len(value) >= 12
        ]
        # Also screen the real local key independently of the synthetic environment.
        needles += [
            local[key].encode()
            for key in ("GROQ_API_KEY", "JWT_SECRET", "DB_PASSWORD")
            if local.get(key) and len(local[key]) >= 12
        ]
        image_secrets_absent(
            [config["services"][name]["image"] for name in ("api", "frontend")],
            needles,
            self.directory,
        )
        print("PASS non-root images and literal secret scan of saved image layers", flush=True)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    test = Verification()
    print(f"Isolated verification project: {test.project}", flush=True)
    try:
        test.verify()
        print("PASS container acceptance verification", flush=True)
    except Exception as exc:
        print(
            f"FAIL container verification ({type(exc).__name__}); owned project {test.project}",
            flush=True,
        )
        raise SystemExit(1) from None
    finally:
        test.compose("down", required=False)


if __name__ == "__main__":
    main()
