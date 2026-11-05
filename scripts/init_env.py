"""Generate local secrets without displaying them or replacing existing configuration."""

from pathlib import Path
from secrets import token_urlsafe


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    password, test_password, jwt_secret = (token_urlsafe(48) for _ in range(3))
    contents = (
        "DB_USER=incidentflow\n"
        f"DB_PASSWORD={password}\n"
        "DB_NAME=incidentflow\n"
        f"DATABASE_URL=postgresql+asyncpg://incidentflow:{password}@127.0.0.1:55432/incidentflow\n"
        f"TEST_DB_PASSWORD={test_password}\n"
        f"TEST_DATABASE_URL=postgresql+asyncpg://incidentflow_test:{test_password}"
        "@127.0.0.1:55433/incidentflow_test\n"
        f"JWT_SECRET={jwt_secret}\n"
        "JWT_ACCESS_TOKEN_MINUTES=15\n"
        "ENVIRONMENT=development\n"
        'CORS_ORIGINS=["http://localhost:5173"]\n'
    )
    try:
        with (root / ".env").open("x", encoding="utf-8") as target:
            target.write(contents)
    except FileExistsError:
        raise SystemExit(".env already exists; left unchanged.") from None
    print("Created private .env with random local credentials. Do not commit it.")


if __name__ == "__main__":
    main()
