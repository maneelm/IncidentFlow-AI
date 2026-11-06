# Docker operations (Windows PowerShell)

Run from the repository root with Docker Desktop in Linux-container mode. These commands use the existing private `.env`; never replace it to follow this guide. New checkouts can run `uv run --locked python scripts/init_env.py` once. Do not print `docker compose config` or inspect container environments: resolved configuration contains secrets. `config --quiet` is safe.

## Complete application

```powershell
Set-Location C:\Users\manee\Desktop\incidentflow
docker compose config --quiet
docker compose build api frontend
docker compose up -d --wait --wait-timeout 180
Start-Process http://127.0.0.1:8080
```

Or use one startup command: `docker compose up --build -d --wait --wait-timeout 180`. Set `FRONTEND_PORT` in `.env` to change 8080; the binding remains 127.0.0.1. Use the configured port in subsequent HTTP commands. All three workers run independently. There is no default account and no automatic demo seeding.

Provision an account explicitly after the API starts (existing accounts remain valid):

```powershell
$env:BOOTSTRAP_EMAIL = Read-Host 'Email'
$env:BOOTSTRAP_DISPLAY_NAME = Read-Host 'Display name'
$env:BOOTSTRAP_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'Password (12+ characters)' -AsSecureString)).Password
try {
    docker compose exec -T -e BOOTSTRAP_EMAIL -e BOOTSTRAP_DISPLAY_NAME -e BOOTSTRAP_PASSWORD api python -m app.ops.launch bootstrap --user-role admin
    if ($LASTEXITCODE -ne 0) { throw 'Account creation failed; inspect safe startup logs.' }
} finally {
    Remove-Item Env:BOOTSTRAP_EMAIL, Env:BOOTSTRAP_DISPLAY_NAME, Env:BOOTSTRAP_PASSWORD -ErrorAction SilentlyContinue
}
```

Use `--user-role engineer` or `viewer` with a different email. Duplicate emails are rejected. Synthetic mutations need a proposer and a distinct engineer/admin reviewer; no administrator override was added. The launcher matters: `docker compose exec` starts a new process and does not inherit the URL calculated inside PID 1. Direct `python -m app.bootstrap` inside the container would miss that calculation.

## Status, readiness, logs and shutdown

```powershell
docker compose ps -a
Invoke-RestMethod http://127.0.0.1:8080/api/v1/health
Invoke-RestMethod http://127.0.0.1:8080/api/v1/ready
Invoke-WebRequest http://127.0.0.1:8080/healthz -UseBasicParsing
docker compose logs --tail 100 migrate api
docker compose logs --tail 100 detection-worker investigation-worker resolution-worker
docker compose logs --follow --tail 50 investigation-worker
docker compose restart detection-worker
docker compose exec -T api python -m app.ops.launch check
docker compose down
```

`down` stops/removes the project's containers and networks; the named `incidentflow_postgres_data` volume remains. **`down -v` also deletes named volumes and application data. It is not a normal shutdown command.** `stop` leaves containers in place; `start` resumes them. Use `up --wait` after configuration/image changes. Docker does not restart a container merely because its health status is unhealthy: inspect its logs, database state and heartbeat, then intervene. An exited service uses `unless-stopped` restart policy. The migration one-shot intentionally has no restart loop.

Logs contain structured timestamps, levels, controlled events, IDs and error classes, not request bodies, headers, database URLs or Groq exception messages. JSON-file rotation caps each service at three 10 MB files. Do not enable HTTP/SDK/SQL debug logging. Frontend access logging is disabled. Nginx critical operational errors use stderr. `ps` distinguishes API health from independent worker health.

## Migrations and updates

```powershell
# Before an update: take and verify a backup using the next section.
docker compose stop api detection-worker investigation-worker resolution-worker frontend
docker compose build api frontend
docker compose run --rm migrate
if ($LASTEXITCODE -ne 0) { throw 'Migration gate failed. Keep application services stopped.' }
docker compose up -d --wait --wait-timeout 180
```

Initial `up` waits for PostgreSQL, runs `migrate` once, and starts API/workers only after success. It uses a transaction-scoped PostgreSQL advisory lock, Alembic `upgrade head`, `check` for model drift, and exact revision 0004 verification. Repeated successful runs preserve existing rows. Every application restart also checks the revision. Running API processes are not a substitute for a migration maintenance window. `restart` alone does not rerun the one-shot migration; use `run --rm migrate` for an explicit recheck.

On failure, inspect `docker compose logs --tail 100 migrate`; startup emits only a safe exception class. For detailed schema-only Alembic diagnostics use the migration container with an operator-reviewed configuration. Do not reset the volume, stamp a revision to hide drift, downgrade populated data, or run migrations independently in every worker. Migrations 0001–0004 are unchanged.

## Back up without corrupting binary output in PowerShell

The database runs internally, so use its native tools. Write binary output inside the container and copy it; Windows PowerShell 5 redirection can corrupt a binary archive.

```powershell
New-Item -ItemType Directory -Force ./backups | Out-Null
$backupStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
docker compose exec -T postgres sh -c 'umask 077; pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/incidentflow-backup.dump'
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
docker compose cp postgres:/tmp/incidentflow-backup.dump "./backups/incidentflow-$backupStamp.dump"
if ($LASTEXITCODE -ne 0) { throw 'Backup copy failed.' }
docker compose exec -T postgres rm /tmp/incidentflow-backup.dump
```

Backups contain password hashes, incident data and audit history. Restrict filesystem ACLs, encrypt off-host copies, define retention/RPO/RTO, and test restoration regularly. `backups/`, `*.dump`, `.env` and `.tools/` are ignored by Git and excluded from image contexts. A Docker volume alone is not a backup; Docker Desktop reset or disk failure can destroy it.

## Restore into a separate, new database

This deliberately does not overwrite the application's database or change its connection URL. Choose an existing backup file and create a unique target; `createdb` fails if that target already exists. Do not add `--clean`, automatic DROP commands or a destructive restore to the normal startup path.

```powershell
$backupFile = Read-Host 'Full path to the verified .dump backup'
$restoreDb = 'incidentflow_restore_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
if ($restoreDb -notmatch '^incidentflow_restore_[0-9_]+$') { throw 'Unsafe restore target.' }
docker compose cp $backupFile postgres:/tmp/incidentflow-restore.dump
if ($LASTEXITCODE -ne 0) { throw 'Restore copy failed.' }
docker compose exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" "$1"' -- $restoreDb
if ($LASTEXITCODE -ne 0) { throw 'Target creation failed; nothing was overwritten.' }
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" --exit-on-error --no-owner --no-privileges -d "$1" /tmp/incidentflow-restore.dump' -- $restoreDb
if ($LASTEXITCODE -ne 0) { throw 'Restore failed; retain the target for inspection.' }
'SELECT version_num FROM alembic_version;' | docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$1" -At -v ON_ERROR_STOP=1' -- $restoreDb
docker compose exec -T postgres rm /tmp/incidentflow-restore.dump
```

Check counts, representative reports, audit history and application behavior on an isolated deployment before any planned cutover. Restoring into an unrelated server may require an explicitly provisioned application role/ownership review; `--no-owner --no-privileges` intentionally avoids recreating source roles. Do not copy a live PostgreSQL data directory as a substitute for a consistent dump.

## Existing host development and tests

The volume name is unchanged. To keep the old local Python/Vite workflow, explicitly publish only the development database:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml up -d --wait postgres
docker compose --profile test up -d --wait postgres-test
uv sync --locked
uv run --locked alembic -c backend/alembic.ini upgrade head
uv run --locked alembic -c backend/alembic.ini check
uv run --locked pytest -q
uv run --locked ruff check backend scripts
uv run --locked ruff format --check backend scripts
```

Then use the existing local API/worker/Vite commands. Normal `compose.yaml` does not publish 55432; `compose.dev.yaml` adds that loopback port only when selected. Test infrastructure retains its `test` profile, isolated tmpfs database and fixed loopback 55433 contract. Targeting `postgres-test` starts only that service, rather than the entire application. Do not run host and container worker sets against the same development data during demonstrations; stop the unused set deliberately.

```powershell
Push-Location frontend
try {
    npm ci
    npm run typecheck
    npm run lint
    npm test
    npm run build
    npm run api:check
    npx playwright install chromium
    npm run test:e2e
} finally { Pop-Location }
uv run --locked python scripts/verify_containers.py
```

Use the supported Node runtime described in the Phase 5 guide. The container verifier runs the six Chromium workflows (including the original five) through Nginx plus crash/restart/backup/secret checks. It creates a random `incidentflow-verify-*` project, separate runtime credentials and named volume; it never addresses the development volume. It refuses pre-existing resources in the new namespace, then cleans its own containers and networks. It removes only the exact temporary PostgreSQL volume after validating its project and volume-role labels, and deletes its generated private `.tools` credential file. It never uses unchecked Compose volume deletion. Cleanup failures are reported as failures; no broad prune or deletion of older verification projects is performed. No Groq call is made.

## Troubleshooting

- Engine unavailable: start Docker Desktop, select Linux containers, and check `docker version` before starting Compose.
- Frontend connection refused: inspect `frontend` health, configured loopback port and port collisions. The publishing `web` network is a normal bridge; database traffic uses an `internal` network.
- API 503: distinguish `/health` (process) from `/ready` (database/revision); inspect the migration one-shot and database health. Groq absence does not make either unhealthy.
- Worker unhealthy: check its heartbeat/read-only `/tmp` tmpfs, PostgreSQL and revision. A heartbeat means the event loop is alive, not that every queue is empty or job has succeeded. Job status and safe investigation step diagnostics remain authoritative.
- Changed database password: editing `.env` does not update credentials inside an existing volume. Restore matching configuration or perform an operator-controlled PostgreSQL password rotation; never delete data to fix authentication.
- Pending investigation: check provider capability, safe error classes/statuses/request IDs, Retry-After delay, call/token budget and deadline. Do not use a live Groq demo as an ordinary smoke test.
- Wrong CORS: the dashboard and API are same-origin through Nginx; `CONTAINER_CORS_ORIGINS=[]` is intentional. Host Vite keeps its existing `CORS_ORIGINS` setting. An external ingress requires its own explicit HTTPS-origin/trust configuration.

## Final image tags and screenshots

The current Compose configuration builds `incidentflow-backend:local` and `incidentflow-frontend:local`. This changes artifact labels only; it does not rename the project or PostgreSQL volume. Rebuild/up commands remain unchanged. Approved portfolio images are captured only from isolated synthetic fixtures using [the screenshot workflow](screenshots/README.md), never from the existing development data.
