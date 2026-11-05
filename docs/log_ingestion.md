# Structured log ingestion

## Contract and security

`POST /api/v1/logs/ingest` requires `X-Ingestion-Key`. A human JWT alone never authorizes ingestion. The envelope contains one `service_id` and 1–100 `logs`; a service-scoped credential cannot submit to a different service. Unknown fields are rejected. A batch is atomic: any invalid record or conflicting source ID rejects the whole submission, including otherwise-new rows.

```json
{
  "service_id": "00000000-0000-0000-0000-000000000001",
  "logs": [{
    "source_event_id": "payment-timeout-0001",
    "level": "error",
    "message": "SYNTHETIC database connection pool exhausted",
    "timestamp": "2026-11-02T10:00:00Z",
    "trace_id": "checkout-trace-1",
    "request_id": "request-1",
    "error_code": "DB_CONNECTION_TIMEOUT",
    "metadata": {"synthetic": true, "pool_size": 20}
  }]
}
```

Replace example IDs and timestamps with your actual service ID and current timezone-aware time. Message text is trimmed and must be nonblank. Levels accept debug/info/warning/error/critical and normalize casing. Error codes normalize to uppercase. Source timestamps normalize to UTC. Optional trace/request/error fields may be omitted or null.

| Bound | Actual enforcement |
| --- | --- |
| Raw request body | Default 524288 bytes (512 KiB); checked before JSON decoding, including chunked bodies |
| Batch | 1–100 records |
| Message | 1–4000 characters |
| Source event ID | 1–128 ASCII slug characters: letters, digits, `. _ : / -`; starts alphanumeric |
| Trace/request IDs | Optional 1–128 characters |
| Error code | Optional 1–100 characters, uppercase letters/digits/`_ . : -` |
| Metadata | JSON object, at most 8192 serialized UTF-8 bytes, depth 5, 256 nodes |
| Source timestamp | Between DB wall clock minus 86400 seconds and plus 120 seconds, inclusive |
| Raw JSON nesting | At most 16 containers; guards parser recursion before Pydantic validation |
| Content encoding | Uncompressed JSON; nonidentity encoding is rejected with 415 |

Metadata depth starts at zero for the root object; each child increases it by one. Keys and values must be valid PostgreSQL JSON text; NULs, invalid UTF-8 surrogate text and nonfinite numbers are rejected. No object deserialization, pickle, expressions or code execution is used.

Normalized metadata key names containing `password`, `passwd`, `pwd`, `secret`, `token`, `authorization`, `cookie`, `credential` or `apikey` are rejected recursively, including objects inside arrays. Punctuation/casing do not hide names such as `accessToken` or `api-key`. Recognizable bearer strings, JWT-shaped tokens and IncidentFlow ingestion keys are rejected as metadata values and log message/trace/request text. The API rejects instead of silently persisting a modified secret-bearing record. These checks cannot identify every arbitrary secret under an innocuous key; source applications must sanitize sensitive content before sending it. The server never logs submitted bodies, headers, credential strings or raw driver exceptions.

`INGESTION_MAX_REQUEST_BYTES`, `LOG_MAX_AGE_SECONDS`, and `LOG_MAX_FUTURE_SECONDS` are typed settings documented in `.env.example`. Upper bounds prevent disabling safeguards through extreme configuration. Already-stored duplicates still pass input/timestamp validation on retry; retries older than the accepted source-time tolerance are rejected rather than bypassing that policy.

## Issuing, rotating and revoking keys

Only JWT-authenticated admins can use these endpoints:

| Method/path | Behavior |
| --- | --- |
| POST `/api/v1/ingestion-credentials` | Body: `service_id`, `name`, optional `expires_in_days` (1–365, default 30); returns 201 and token once |
| GET `/api/v1/ingestion-credentials` | Paginated, optionally service-filtered metadata; no token/hash |
| GET `/api/v1/ingestion-credentials/{id}` | Metadata only |
| POST `/api/v1/ingestion-credentials/{id}/revoke` | Idempotently marks revoked; returns metadata |

A key is `iflow_<public UUID hex>.<256-bit random secret>`. The full token is hashed with SHA-256; only the digest is stored. Unlike human-chosen passwords, a random 256-bit machine secret has enough entropy for a fast cryptographic digest. Verification uses a constant-time digest comparison. Issuance responses use `Cache-Control: no-store` and `Pragma: no-cache`. Revoked or expired credentials return a generic 401 with an ApiKey challenge.

Rotation: issue a second key for the same service, update the producer's secret storage, confirm ingestion, then revoke the original ID. Replaying already-delivered events with the new key remains idempotent because the source identity is scoped to the service, not the credential. There is no retrieval/reset API for a lost plaintext key; issue a replacement and revoke it. Database rows preserve issuance actor/time, expiry and revocation time. Authentication holds a SHARE lock until the ingestion commits, so a successful revocation commits after an already-authorized ingestion finishes and blocks all subsequently authorized submissions.

## Persistence and idempotency

PostgreSQL stores normalized fields in `log_entries`; metadata alone uses JSONB. A unique `(service_id, source_event_id)` constraint defines source identity. A canonical content hash distinguishes an identical replay from accidental ID reuse with different content. IDs should be generated once by the source and reused for transport retries.

New logs use `INSERT ... ON CONFLICT DO NOTHING`. The service compares stored hashes after conflict resolution. Same ID and same normalized payload is a duplicate; changed content is `409 source_event_conflict`. Duplicate IDs repeated inside the same batch follow the same rule. Ingestion takes a service row lock to serialize receipt timestamps, commit visibility and job positions for that service. This is a deliberate throughput trade-off for deterministic processing.

Each accepted log creates one uniquely linked `detection_jobs` row in the **same request transaction**. The response is emitted after commit. A queue insert failure rolls back the log; a committed log cannot lose its pending job on an API process restart. Duplicate delivery creates neither another log nor another job.

The 202 response is:

```json
{
  "accepted": 1,
  "duplicates": 0,
  "items": [{
    "source_event_id": "payment-timeout-0001",
    "log_id": "00000000-0000-0000-0000-000000000002",
    "job_id": "00000000-0000-0000-0000-000000000003",
    "created": true
  }]
}
```

Items are unique source IDs sorted lexically, bounded by 100. `duplicates` includes repetitions within the submitted list. 202 means logs/work were accepted, not that detection has completed. Invalid shape/content/timestamps return 422; conflicting source content returns 409; oversized bodies return 413; wrong service scope returns 403; missing/revoked/expired/wrong credentials return 401.

## Querying logs and evidence

All human roles can GET `/api/v1/logs` and `/api/v1/logs/{id}` using their bearer JWT. Ingestion keys do not grant access to these human endpoints. There are no log update/delete endpoints.

Filters combine with AND: `service_id`, `level`, timezone-aware inclusive `timestamp_from`/`timestamp_to` (source time), `trace_id`, `request_id`, `incident_id`, `detection_id`, and `search`. Search is PostgreSQL simple-configuration full-text search using `plainto_tsquery`, bounded to 2–100 characters and at most 10 whitespace-delimited terms. It is token-based AND search, not arbitrary SQL, regex or substring search. A functional GIN index supports it without a PostgreSQL extension.

`limit` defaults to 20, accepts 1–100; `offset` accepts 0–100000. Source timestamp descending is default; `sort_order=asc` reverses the timestamp order. UUID ascending breaks ties. Count and page run in the database. `incident_id`/`detection_id` use an EXISTS association query so a log matching multiple detections is not duplicated. Read-committed totals/pages can move during concurrent writes.

GET `/api/v1/detections/{id}/logs?limit=20&offset=0` is a convenient evidence endpoint. A detection record gives the incident UUID, initial rule snapshot, threshold witness count and rolling-window boundaries. Logs expose `metadata`; the ORM attribute is `details` because SQLAlchemy reserves `metadata`.

## Complete local demo: PowerShell

From repository root, after [bootstrapping an admin](../README.md#create-a-local-account):

```powershell
uv sync --locked
docker compose -f compose.yaml -f compose.dev.yaml up -d --wait postgres
docker compose --profile test up -d --wait postgres-test
uv run alembic -c backend/alembic.ini upgrade head
```

Terminal 1:

```powershell
uv run uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000
```

Terminal 2:

```powershell
$env:PYTHONPATH = 'backend'
uv run python -m app.workers.detection_worker
```

Terminal 3:

```powershell
$env:PYTHONPATH = 'backend'
$env:DEMO_ADMIN_EMAIL = Read-Host 'Administrator email'
$env:DEMO_ADMIN_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'Password' -AsSecureString)).Password
try {
    uv run python -m app.simulations.demo --scenario database --seed 42
    # Or exercise all five fixtures:
    uv run python -m app.simulations.demo --scenario all --seed 42
} finally {
    Remove-Item Env:DEMO_ADMIN_EMAIL, Env:DEMO_ADMIN_PASSWORD -ErrorAction SilentlyContinue
}
```

The database scenario verifies 10 accepted logs, 10 completed jobs, one triggered incident, 10 evidence logs, one system-created audit event, then zero accepted logs and 10 duplicates on replay. The demo revokes its temporary key in a finally block and prints the resource IDs. It retains synthetic data for browsing. Each run creates independent services; no existing catalog/incident data is cleared.

`--scenario all` adds order HTTP 500, latency, normal and duplicate fixtures. Normal includes 20 successful INFO records plus only 4 errors against a threshold of 5; it creates no incident. Latency logs explicitly declare `LATENCY_BREACH` with `latency_ms >= 1500` and a producer threshold of 1000 ms. The engine counts those well-defined warning events; it does not calculate latency percentiles from metrics.

For an already-configured service and rule, send just a simulator batch with a scoped key from your secret store:

```powershell
$env:PYTHONPATH = 'backend'
$serviceId = Read-Host 'Service UUID'
$env:INGESTION_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Scoped ingestion key' -AsSecureString)).Password
try {
    uv run python -m app.simulations.scenarios --scenario database --service-id $serviceId --seed 42 --repeat 2
} finally {
    Remove-Item Env:INGESTION_KEY -ErrorAction SilentlyContinue
}
```

`--repeat 2` reuses exactly the same generated payload, including timestamps. Across separate processes, a fixed `--run-id`, `--seed`, and timezone-aware `--anchor-time` reproduce it; the anchor must still satisfy ingestion tolerance. Reusing only an event ID with a newly generated timestamp is a content conflict, not a retry. Optional `SIM_API_URL` accepts local HTTP or a remote HTTPS origin without embedded credentials. The simulator makes actual HTTP calls and never inserts directly into PostgreSQL.
