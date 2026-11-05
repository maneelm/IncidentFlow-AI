import argparse
import asyncio
import logging
import os
import sys

from app.ops.database import check, container_settings, migrate
from app.ops.runtime import configure_logging

WORKERS = {
    "detection-worker": "app.workers.detection_worker",
    "investigation-worker": "app.workers.investigation_worker",
    "resolution-worker": "app.workers.resolution_worker",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Container startup with a migration gate")
    parser.add_argument("role", choices=["api", "migrate", "check", "bootstrap", *WORKERS])
    parser.add_argument("--user-role", choices=["admin", "engineer", "viewer"])
    args = parser.parse_args()
    role = args.role
    if role == "bootstrap" and not args.user_role:
        parser.error("bootstrap requires --user-role")
    configure_logging()
    logger = logging.getLogger("incidentflow.startup")
    try:
        settings = container_settings()
        asyncio.run(migrate(settings) if role == "migrate" else check(settings))
        logger.info("startup_ready role=%s revision=0004", role)
    except Exception as exc:
        logger.error("startup_failed role=%s exception_class=%s", role, type(exc).__name__)
        raise SystemExit(1) from None
    if role in {"migrate", "check"}:
        return
    if role == "bootstrap":
        os.execv(sys.executable, [sys.executable, "-m", "app.bootstrap", "--role", args.user_role])
        return
    if role in WORKERS:
        os.execv(sys.executable, [sys.executable, "-m", WORKERS[role]])
    else:
        import uvicorn

        uvicorn.run(
            "app.main:create_app",
            factory=True,
            host="0.0.0.0",
            port=8000,
            proxy_headers=False,
            access_log=False,
            log_config=None,
            timeout_graceful_shutdown=20,
            limit_concurrency=100,
        )


if __name__ == "__main__":
    main()
