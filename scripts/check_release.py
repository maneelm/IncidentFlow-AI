"""Read-only publication checks. Never prints secret values or stages files."""

import argparse
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    "backups",
    "node_modules",
    ".venv",
    ".tools",
    ".git",
    "__pycache__",
    ".cache",
    "dist",
    ".pytest_cache",
    ".ruff_cache",
    "test-results",
    "playwright-report",
}
TOP_LEVEL = {
    "README.md",
    "LICENSE",
    ".gitignore",
    ".dockerignore",
    ".env.example",
    "pyproject.toml",
    "uv.lock",
    "compose.yaml",
    "compose.dev.yaml",
    "backend",
    "frontend",
    "scripts",
    "docs",
    ".github",
}
PATTERNS = {
    "groq_key": re.compile(rb"gsk_[A-Za-z0-9]{20,}"),
    "github_token": re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    "aws_access_key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def forbidden_path(path: str) -> bool:
    value = Path(path.replace("\\", "/"))
    return (
        any(part in EXCLUDED for part in value.parts)
        or (value.name.startswith(".env") and value.name != ".env.example")
        or value.suffix.lower() in {".dump", ".log", ".pyc", ".tsbuildinfo"}
    )


def findings(path: str, content: bytes, secrets: list[bytes]) -> list[str]:
    result = ["local_runtime_secret"] if any(secret in content for secret in secrets) else []
    for name, pattern in PATTERNS.items():
        for match in pattern.finditer(content):
            suffix = match[0].removeprefix(b"gsk_")
            sequential_test = (
                "tests/" in path.replace("\\", "/")
                and name == "groq_key"
                and suffix == b"abcdefghijklmnopqrstuvwxyz"[: len(suffix)]
            )
            if not sequential_test:
                result.append(name)
    return sorted(set(result))


def git(*args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError("git_check_failed")
    return result.stdout


def publication_files(staged: bool) -> list[tuple[Path, bytes]]:
    if staged:
        root = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
        if root != ROOT:
            raise ValueError("initialize_git_in_project_directory_before_staging")
        paths = git("ls-files", "--cached", "-z").decode().split("\x00")
        if not any(paths):
            raise ValueError("no_staged_files")
        return [(ROOT / path, git("show", f":{path}")) for path in paths if path]
    result = []
    for name in sorted(TOP_LEVEL):
        source = ROOT / name
        if source.is_file():
            result.append((source, source.read_bytes()))
        elif source.is_dir():
            for path in source.rglob("*"):
                if not forbidden_path(path.relative_to(ROOT).as_posix()) and path.is_file():
                    result.append((path, path.read_bytes()))
    return result


def missing_links(path: Path, content: str) -> list[str]:
    missing = []
    for target in re.findall(r"\]\(([^)]+)\)", content):
        if ":" in target or target.startswith("#"):
            continue
        local = unquote(target.split("#", 1)[0])
        if local and not (path.parent / local).exists():
            missing.append(local)
    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged", action="store_true", help="Also verify repository root and exact staged blobs"
    )
    args = parser.parse_args()
    values = dotenv_values(ROOT / ".env")
    secrets = [
        value.encode()
        for key, value in values.items()
        if key in {"GROQ_API_KEY", "JWT_SECRET", "DB_PASSWORD", "TEST_DB_PASSWORD"}
        and value
        and len(value) >= 12
    ]
    failures = []
    try:
        files = publication_files(args.staged)
        for path, content in files:
            relative = path.relative_to(ROOT).as_posix()
            if forbidden_path(relative):
                failures.append((relative, "private_or_generated_path"))
            failures.extend((relative, code) for code in findings(relative, content, secrets))
            if path.suffix == ".md":
                failures.extend(
                    (relative, f"missing_local_link:{target}")
                    for target in missing_links(path, content.decode("utf-8"))
                )
        for key in (
            "DATABASE_URL",
            "JWT_SECRET",
            "GROQ_API_KEY",
            "DB_PASSWORD",
            "TEST_DB_PASSWORD",
        ):
            if dotenv_values(ROOT / ".env.example").get(key):
                failures.append((".env.example", "usable_secret_or_database_url"))
        if not (ROOT / "LICENSE").exists():
            failures.append(("LICENSE", "owner_license_decision_required"))
    except (ValueError, OSError, UnicodeError) as exc:
        # These controlled ValueError codes contain no input values.
        code = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(f"FAIL publication check: {code}")
        raise SystemExit(1) from None
    for path, reason in failures:
        print(f"FAIL {path}: {reason}")
    if failures:
        raise SystemExit(1)
    print(
        "PASS publication paths, secret patterns, runtime secrets and local links "
        f"({len(files)} files)."
    )
    print(
        "Review screenshots, rights, GitHub settings and external URLs manually before publication."
    )


if __name__ == "__main__":
    main()
