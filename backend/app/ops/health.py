import asyncio
import os
import sys
from pathlib import Path
from urllib.request import urlopen

from app.ops.database import check, container_settings
from app.ops.runtime import heartbeat_current


def main() -> None:
    try:
        if sys.argv[1:] == ["api"]:
            with urlopen("http://127.0.0.1:8000/api/v1/ready", timeout=4) as response:
                if response.status != 200:
                    raise ValueError("api_not_ready")
        elif sys.argv[1:] == ["worker"]:
            if not heartbeat_current(Path(os.environ["WORKER_HEARTBEAT_PATH"])):
                raise ValueError("worker_heartbeat_stale")
            asyncio.run(check(container_settings()))
        else:
            raise ValueError("invalid_health_mode")
    except Exception:
        # Docker stores health output; never include URLs, headers or driver errors.
        raise SystemExit("not_ready") from None


if __name__ == "__main__":
    main()
