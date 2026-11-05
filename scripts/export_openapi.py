"""Export public contracts without loading private .env or connecting to PostgreSQL."""

import json
import sys
from pathlib import Path
from secrets import token_urlsafe

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def main():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://contract:contract@127.0.0.1/contract",
        jwt_secret=token_urlsafe(48),
        environment="test",
        cors_origins=[],
    )
    target = ROOT / "frontend" / ".cache" / "openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(create_app(settings).openapi(), sort_keys=True), encoding="utf-8")
    print("Public OpenAPI contract exported")


if __name__ == "__main__":
    main()
