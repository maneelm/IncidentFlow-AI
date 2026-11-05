import asyncio
import json
import logging
import os
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Call sites log only controlled events, codes, IDs and exception classes.
        # Never append exception tracebacks or arbitrary logging extras.
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if os.getenv("LOG_FORMAT") == "json"
        else logging.Formatter("%(levelname)s %(name)s %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("httpx", "httpcore", "groq", "groq._base_client"):
        logging.getLogger(name).setLevel(logging.WARNING)


async def heartbeat_loop() -> None:
    target = os.getenv("WORKER_HEARTBEAT_PATH")
    if not target:
        return
    while True:
        await asyncio.to_thread(Path(target).write_text, str(time.time()), encoding="ascii")
        await asyncio.sleep(5)


async def stop_heartbeat(task: asyncio.Task) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def heartbeat_current(path: Path, *, now: float | None = None) -> bool:
    try:
        age = (time.time() if now is None else now) - float(path.read_text(encoding="ascii"))
        return 0 <= age <= 30
    except (OSError, ValueError):
        return False
