"""Real, isolated Compose acceptance test. Never loads the development database.

Requires built images, uv dependencies, npm ci and Playwright Chromium. Generates
private runtime credentials under ignored .tools; prints only safe stage results.
Final cleanup removes only this invocation's label-verified temporary resources.
"""

import argparse
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.simulations.demo import checked, demonstrate, wait_for_jobs  # noqa: E402

PRIVATE_VALUES: set[str] = set()


def safe_value(value):
    """Bound diagnostics and redact credentials, never forward command output/bodies."""
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if re.search(r"password|secret|token|authorization|api.key", str(key), re.I)
                else safe_value(item)
            )
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, (list, tuple)):
        return [safe_value(item) for item in value[:20]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, str):
        return type(value).__name__
    for secret in sorted(PRIVATE_VALUES, key=len, reverse=True):
        value = value.replace(secret, "[redacted]")
    value = re.sub(r"(?i)Bearer\s+\S+", "Bearer [redacted]", value)
    value = re.sub(r"gsk_[A-Za-z0-9_-]+", "[redacted]", value)
    return value[:200]


class VerificationFailure(RuntimeError):
    def __init__(self, check, expected, observed):
        self.check = check
        self.expected = expected
        self.observed = observed
        super().__init__(check)

    def __str__(self):
        return json.dumps(
            safe_value({"check": self.check, "expected": self.expected, "observed": self.observed})
        )


def expect_equal(check, observed, expected):
    matches = observed is expected if isinstance(expected, bool) else observed == expected
    if not matches:
        raise VerificationFailure(check, expected, observed)


def expect_true(check, observed):
    expect_equal(check, bool(observed), True)


def expect_contains(check, observed, expected):
    if observed is None or expected not in observed:
        raise VerificationFailure(check, {"contains": expected}, observed)


def layout_diagnostic(outputs):
    """Allow only bounded geometry/styles from the browser's overflow diagnostic."""
    numbers = {"left", "right", "top", "bottom", "width", "height", "clientWidth", "scrollWidth"}
    styles = {
        "display": {
            "block",
            "inline",
            "inline-block",
            "flex",
            "inline-flex",
            "grid",
            "inline-grid",
            "table",
            "table-row",
            "table-cell",
            "none",
            "contents",
            "list-item",
            "table-row-group",
            "table-header-group",
        },
        "position": {"static", "relative", "absolute", "fixed", "sticky"},
        "overflowX": {"visible", "hidden", "auto", "scroll", "clip"},
        "whiteSpace": {"normal", "nowrap", "pre", "pre-wrap", "pre-line", "break-spaces"},
    }

    def number(value):
        return type(value) in {int, float} and abs(value) <= 10_000_000 and math.isfinite(value)

    def box(value):
        if not isinstance(value, dict):
            return {}
        result = {key: value[key] for key in numbers if key in value and number(value[key])}
        tag = value.get("tag")
        if isinstance(tag, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,20}", tag):
            result["tag"] = tag
        classes = value.get("classes")
        if isinstance(classes, list):
            result["classes"] = [
                item
                for item in classes[:6]
                if isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,45}", item, re.I)
            ]
        for key, allowed in styles.items():
            if isinstance(value.get(key), str) and value[key] in allowed:
                result[key] = value[key]
        for key in ("minWidth", "maxWidth", "flexShrink"):
            item = value.get(key)
            if isinstance(item, str) and re.fullmatch(
                r"auto|none|min-content|max-content|fit-content|[0-9.]{1,12}(?:px|%)?", item
            ):
                result[key] = item
        return result

    for output in outputs:
        text = output.get("text", "") if isinstance(output, dict) else ""
        if not isinstance(text, str) or not text.startswith("LAYOUT_DIAGNOSTIC "):
            continue
        try:
            value = json.loads(text[len("LAYOUT_DIAGNOSTIC ") :])
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        result = {
            key: value[key]
            for key in (
                "viewportWidth",
                "clientWidth",
                "documentWidth",
                "bodyWidth",
                "scrollX",
                "localScrollElements",
            )
            if key in value and number(value[key])
        }
        if "viewportWidth" not in result or "documentWidth" not in result:
            continue
        if isinstance(value.get("fontsLoaded"), bool):
            result["fontsLoaded"] = value["fontsLoaded"]
        result["offenders"] = []
        offenders = value.get("offenders")
        for offender in offenders[:12] if isinstance(offenders, list) else []:
            item = box(offender)
            if isinstance(offender, dict):
                for key in ("parent", "containingBlock"):
                    if isinstance(offender.get(key), dict):
                        item[key] = box(offender[key])
            result["offenders"].append(item)
        return result
    return None


def browser_failures(report):
    """Retain assertion locations and primitive comparisons, never raw Playwright errors."""
    failures = []

    def visit(suite):
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                for result in test.get("results", []):
                    if result.get("status") in {"passed", "skipped"}:
                        continue
                    for error in result.get("errors", []) or [{}]:
                        location = error.get("location", {})
                        message = re.sub(r"\x1b\[[0-9;]*m", "", error.get("message", ""))
                        failure = {
                            "file": Path(location.get("file", spec.get("file", "unknown"))).name,
                            "line": location.get("line", spec.get("line")),
                            "expected": "passed",
                            "observed": result.get("status", "failed"),
                        }
                        for key, label in (("expected", "Expected"), ("observed", "Received")):
                            match = re.search(rf"^{label}: (true|false|\d+)\s*$", message, re.M)
                            if match:
                                failure[key] = json.loads(match[1])
                        layout = layout_diagnostic(result.get("stdout", []))
                        if layout is not None:
                            failure["layout"] = layout
                        failures.append(failure)
        for child in suite.get("suites", []):
            visit(child)

    for suite in report.get("suites", []):
        visit(suite)
    return failures


def run(args, *, env=None, data=None, timeout=240, required=True, check="external_command"):
    try:
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
    except subprocess.TimeoutExpired:
        raise VerificationFailure(check, f"finish within {timeout}s", "timed_out") from None
    if required and result.returncode:
        raise VerificationFailure(check, 0, result.returncode)
    return result


def wait_until(check, seconds=60, *, name="verification_deadline"):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.25)
    raise VerificationFailure(name, f"ready within {seconds}s", "deadline_exceeded")


def image_secrets_absent(images, needles, directory):
    for index, image in enumerate(images):
        inspect = json.loads(run(["docker", "image", "inspect", image]).stdout)[0]
        expect_true(
            "image.non_root_user", inspect["Config"]["User"] not in {"", "0", "root", "0:0"}
        )
        exposed = [
            item.split("=", 1)[0]
            for item in inspect["Config"]["Env"]
            if item.startswith(("JWT_SECRET=", "GROQ_API_KEY=", "DB_PASSWORD="))
        ]
        expect_equal("image.no_baked_credentials", exposed, [])
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
                    expect_equal(
                        "image.no_literal_secrets", any(needle in data for needle in needles), False
                    )
                    tail = data[-overlap:]
        finally:
            archive.unlink(missing_ok=True)


class Verification:
    def __init__(self):
        self.project = f"incidentflow-verify-{uuid4().hex[:12]}"
        self._owned_project = self.project
        self._claimed = False
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
        self.stage = "configuration"
        PRIVATE_VALUES.update(
            self.values[key] for key in ("DB_PASSWORD", "JWT_SECRET", "TEST_DB_PASSWORD")
        )
        PRIVATE_VALUES.update(account["password"] for account in self.accounts.values())
        local = dotenv_values(ROOT / ".env")
        PRIVATE_VALUES.update(
            local[key] for key in ("GROQ_API_KEY", "JWT_SECRET", "DB_PASSWORD") if local.get(key)
        )
        self.write_env()

    def validate_scope(self):
        expect_true(
            "scope.project_name", re.fullmatch(r"incidentflow-verify-[0-9a-f]{12}", self.project)
        )
        expect_equal("scope.original_project", self.project == self._owned_project, True)
        expected = (ROOT / ".tools" / self.project).resolve()
        expect_equal("scope.private_parent", expected.parent == (ROOT / ".tools").resolve(), True)
        expect_equal("scope.directory", self.directory.resolve() == expected, True)
        expect_equal("scope.envfile", self.envfile.resolve() == expected / ".env", True)

    def claim_project(self):
        self.validate_scope()
        for kind in ("container", "volume", "network"):
            command = (
                ["docker", "ps", "-aq"] if kind == "container" else ["docker", kind, "ls", "-q"]
            )
            existing = run(
                command + ["--filter", f"label=com.docker.compose.project={self.project}"],
                check=f"scope.list_{kind}",
            ).stdout.split()
            expect_equal(f"scope.no_existing_{kind}", existing, [])
        self._claimed = True

    def cleanup(self):
        self.stage = "cleanup"
        self.validate_scope()
        if self._claimed:
            result = self.compose("down", "--remove-orphans", required=False)
            expect_equal("cleanup.compose_down", result.returncode, 0)
            remaining = run(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    f"label=com.docker.compose.project={self.project}",
                ],
                check="cleanup.list_containers",
            ).stdout.split()
            expect_equal("cleanup.no_owned_containers", remaining, [])
            volumes = run(
                [
                    "docker",
                    "volume",
                    "ls",
                    "--filter",
                    f"label=com.docker.compose.project={self.project}",
                    "--format",
                    "{{.Name}}",
                ],
                check="cleanup.list_volumes",
            ).stdout.split()
            for volume in volumes:
                expect_equal("cleanup.exact_volume_name", volume, f"{self.project}_postgres_data")
                info = json.loads(
                    run(
                        ["docker", "volume", "inspect", volume], check="cleanup.inspect_volume"
                    ).stdout
                )[0]
                labels = info.get("Labels") or {}
                expect_equal(
                    "cleanup.volume_project_label",
                    labels.get("com.docker.compose.project"),
                    self.project,
                )
                expect_equal(
                    "cleanup.volume_role_label",
                    labels.get("com.docker.compose.volume"),
                    "postgres_data",
                )
                run(["docker", "volume", "rm", volume], check="cleanup.remove_owned_volume")
            remaining = run(
                [
                    "docker",
                    "network",
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={self.project}",
                ],
                check="cleanup.list_networks",
            ).stdout.split()
            expect_equal("cleanup.no_owned_networks", remaining, [])
        self.envfile.unlink(missing_ok=True)
        if not any(self.directory.iterdir()):
            self.directory.rmdir()

    def verify_probe_owner(self, name):
        self.validate_scope()
        expect_equal("probe.exact_name", name, f"{self.project}-claim-probe")
        info = json.loads(run(["docker", "inspect", name], check="probe.inspect").stdout)[0]
        labels = info.get("Config", {}).get("Labels") or {}
        expect_equal("probe.project_label", labels.get("com.docker.compose.project"), self.project)

    def write_env(self):
        self.envfile.write_text(
            "".join(f"{key}={value}\n" for key, value in self.values.items()), encoding="utf-8"
        )
        self.envfile.chmod(0o600)

    def compose(self, *args, data=None, extra=None, **kwargs):
        self.validate_scope()
        expect_equal(
            "scope.no_unchecked_volume_deletion",
            any(arg in {"-v", "--volumes"} for arg in args),
            False,
        )
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
            check=f"compose.{args[0]}",
            **kwargs,
        )

    def sql(self, query, *, database=None):
        target = database or self.values["DB_NAME"]
        # Fixed/owned database identifiers only; no SQL is built from API input.
        expect_true("sql.owned_database_identifier", re.fullmatch(r"[a-z_]+", target))
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
            self.stage = f"provision.{role}"
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
        self.stage = "browser"
        node = shutil.which("node")
        local = ROOT / ".tools/node_modules/node/bin/node.exe"
        if os.name == "nt" and local.exists():
            node = str(local)
        expect_true("browser.supported_node_available", node)
        result = subprocess.run(
            [node, "node_modules/@playwright/test/cli.js", "test", "--reporter=json"],
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
        # JSON errors can contain response data/tokens. Emit only safe locations,
        # statuses and primitive assertion values; never forward raw output.
        try:
            report = json.loads(result.stdout)
        except ValueError:
            raise VerificationFailure(
                "browser.json_report", "valid JSON", "missing_or_invalid"
            ) from None
        failures = browser_failures(report)
        expect_equal("browser.assertions", failures, [])
        expect_equal("browser.exit_code", result.returncode, 0)
        stats = report.get("stats", {})
        for key in ("unexpected", "flaky", "skipped"):
            expect_equal(f"browser.{key}", stats.get(key), 0)
        passed = stats.get("expected", 0)
        expect_true("browser.all_workflows_ran", isinstance(passed, int) and passed >= 9)
        print(f"PASS {passed} Chromium workflows through Nginx", flush=True)

    def crash_recovery(self, client, fixture, key):
        self.stage = "crash_recovery"
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
        self.verify_probe_owner(probe)
        try:
            wait_until(lambda: queued in run(["docker", "logs", probe]).stdout, 20)
            run(["docker", "kill", "--signal", "KILL", probe])
            self.compose("start", "detection-worker")
            jobs = wait_for_jobs(client, fixture["service_id"], 60)
            recovered = next(job for job in jobs["items"] if job["id"] == queued)
            expect_equal("crash_recovery.attempts", recovered["attempts"], 2)
            expect_equal("crash_recovery.status", recovered["status"], "completed")
        finally:
            self.verify_probe_owner(probe)
            run(["docker", "rm", "-f", probe], check="probe.remove_owned_container")
        print("PASS killed worker claim recovered after lease expiry", flush=True)

    def verify(self):
        self.stage = "configuration"
        self.claim_project()
        config = json.loads(self.compose("config", "--format", "json").stdout)
        ports = {name: service.get("ports", []) for name, service in config["services"].items()}
        expect_equal(
            "compose.only_frontend_published",
            sorted(name for name, values in ports.items() if values),
            ["frontend"],
        )
        expect_equal("compose.loopback_binding", ports["frontend"][0]["host_ip"], "127.0.0.1")
        for name in ("detection-worker", "resolution-worker", "migrate", "frontend"):
            expect_equal(
                f"compose.{name}.no_provider_key",
                "GROQ_API_KEY" in config["services"][name].get("environment", {}),
                False,
            )
        self.stage = "empty_database_startup"
        self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
        print("PASS empty database migration and six healthy services", flush=True)
        self.provision()
        print("PASS three isolated role accounts provisioned", flush=True)
        with httpx.Client(base_url=self.base, timeout=10, trust_env=False) as client:
            self.stage = "http_boundaries"
            expect_equal("http.health.status", client.get("/api/v1/health").status_code, 200)
            readiness = client.get("/api/v1/ready")
            expect_equal("http.ready.status", readiness.status_code, 200)
            expect_equal(
                "http.ready.cache_control", readiness.headers.get("cache-control"), "no-store"
            )
            expect_equal(
                "http.anonymous_incidents.status", client.get("/api/v1/incidents").status_code, 401
            )
            expect_equal(
                "http.invalid_token.status",
                client.get(
                    "/api/v1/auth/me", headers={"Authorization": "Bearer invalid"}
                ).status_code,
                401,
            )
            response = client.get("/incidents/refresh-check")
            expect_equal("http.spa_deep_link.status", response.status_code, 200)
            expect_contains(
                "http.spa_deep_link.no_store",
                response.headers.get("cache-control"),
                "no-store",
            )
            expect_contains(
                "http.spa.frame_ancestors",
                response.headers.get("content-security-policy"),
                "frame-ancestors 'none'",
            )
            asset = re.search(r'src="(/assets/[^" ]+\.js)"', response.text)
            expect_true("http.spa.fingerprinted_script", asset)
            asset_response = client.get(asset[1])
            expect_equal("http.asset.status", asset_response.status_code, 200)
            expect_contains(
                "http.asset.immutable",
                asset_response.headers.get("cache-control"),
                "immutable",
            )
            expect_equal(
                "http.asset.nosniff",
                asset_response.headers.get("x-content-type-options"),
                "nosniff",
            )
            expect_equal(
                "http.oversized_body.status",
                client.post("/api/v1/auth/token", content=b"x" * (2097152 + 1)).status_code,
                413,
            )
            print(
                "PASS proxy health, auth, SPA, cache/security headers and body limits", flush=True
            )
            self.stage = "seed"
            self.login(client)
            fixture, key = self.seed(client)
            expect_equal(
                "provider.fake.synthetic",
                checked(client.get("/api/v1/investigation-capabilities"), 200)["synthetic"],
                True,
            )
            print(
                "PASS real ingestion, deterministic detection and fake-provider capability",
                flush=True,
            )
            self.browser(fixture)
            self.crash_recovery(client, fixture, key)
            checked(client.post(f"/api/v1/ingestion-credentials/{key['id']}/revoke"), 200)
            before = self.counts()
            expect_equal("records.isolated_users", before["users"], 3)
            expect_true(
                "records.investigation_report_present", before["investigation_reports"] >= 1
            )
            expect_true("records.resolution_audit_present", before["resolution_events"] > 0)
            self.stage = "restart_persistence"
            self.compose("restart", "investigation-worker", "resolution-worker")
            self.compose("down")  # Never -v: retain the owned named volume.
            self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
            expect_equal("records.after_restart", self.counts(), before)
            self.login(client)
            print("PASS populated migration, down/up persistence and worker restarts", flush=True)
            self.stage = "backup_restore"
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
            expect_equal(
                "records.restored_backup", self.counts("incidentflow_restorecheck"), before
            )
            print("PASS native PostgreSQL backup restored to a separate database", flush=True)
            self.stage = "provider_isolation"
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
            expect_true("production.fake_provider_rejected", rejected.returncode != 0)
            expect_true("production.fake_provider_validation", "ValidationError" in rejected.stderr)
            self.values.update(INVESTIGATION_LLM_PROVIDER="groq", INVESTIGATION_LLM_MODEL="")
            self.write_env()
            self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
            expect_equal("provider.absent.ready", client.get("/api/v1/ready").status_code, 200)
            expect_equal(
                "provider.absent.normal_api", client.get("/api/v1/incidents").status_code, 200
            )
            expect_equal(
                "provider.absent.investigation",
                client.post(
                    f"/api/v1/incidents/{fixture['incident_id']}/investigations", json={}
                ).status_code,
                503,
            )
            print("PASS missing Groq isolation and production fake-provider rejection", flush=True)
        self.stage = "image_security"
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
    failed = False
    try:
        test.verify()
    except Exception as exc:
        failed = True
        report_failure(test, exc)
    finally:
        try:
            test.cleanup()
            print(
                "PASS owned verification containers, networks, volume and credentials cleaned",
                flush=True,
            )
        except Exception as exc:
            failed = True
            report_failure(test, exc)
    if failed:
        raise SystemExit(1)
    print("PASS container acceptance verification", flush=True)


def report_failure(test, error):
    if isinstance(error, subprocess.TimeoutExpired):
        error = VerificationFailure(
            "subprocess.deadline", f"finish within {error.timeout}s", "timed_out"
        )
    elif not isinstance(error, VerificationFailure):
        frames = traceback.extract_tb(error.__traceback__)
        frame = frames[-1] if frames else None
        location = f"{Path(frame.filename).name}:{frame.lineno}" if frame else "external"
        error = VerificationFailure(location, "check succeeds", type(error).__name__)
    print(f"FAIL container verification stage={test.stage} {error}", flush=True)


if __name__ == "__main__":
    main()
