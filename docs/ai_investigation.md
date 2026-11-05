# Investigation operations

## Configuration and availability

The default provider is `groq`. Missing key/model does not stop the application or the detection worker. Starting an AI investigation returns `503 investigation_provider_unavailable` until hosted configuration exists, or until an operator explicitly selects the development fake provider. Read-only investigation APIs remain available. An already-queued hosted job whose key disappears fails locally with a safe provider code.

| Setting | Default / bound |
| --- | --- |
| GROQ_API_KEY | Optional secret; no default credential |
| INVESTIGATION_LLM_PROVIDER | groq; fake permitted only in development/test |
| INVESTIGATION_LLM_MODEL | Empty; configure an account-accessible hosted chat model; fake defaults to fake-v1 |
| INVESTIGATION_LLM_TIMEOUT_SECONDS | 20; 1–60 |
| INVESTIGATION_MAX_LLM_CALLS | 4 across all attempts; 2–8 |
| INVESTIGATION_MAX_INPUT_TOKENS | 65536 reserved across attempts; 1024–262144 |
| INVESTIGATION_MAX_OUTPUT_TOKENS | 8192 reserved across attempts; 1024–16384 |
| INVESTIGATION_MAX_DURATION_SECONDS | 180 from first claim, including retry delays; 10–600, greater than call timeout |
| INVESTIGATION_LEASE_SECONDS | 90; 15–180, at least call timeout plus 10 seconds |
| INVESTIGATION_MAX_ATTEMPTS | 3; 1–5 |
| INVESTIGATION_MAX_LOGS | 20 selected logs; 2–50 |
| INVESTIGATION_MAX_KNOWLEDGE | 4 runbook chunks; 1–8; up to 3 historical/note hits additionally |

Policies and provider/model identifiers are captured when the investigation is created. Keys are never captured. A worker reads its current private Groq key when performing hosted work. The Groq SDK uses a fixed HTTPS base URL, timeout and zero internal retries; the durable worker owns retries. GPT-OSS 20B/120B use strict JSON Schema with low reasoning effort; other configured chat models retain JSON-object mode. Local Pydantic, grounding, redaction and readonly-action validation remain mandatory.

In Phase 6, Compose supplies the key only to API and investigation-worker services at runtime, never to image builds or frontend variables. Missing hosted credentials do not affect API readiness or normal incident workflows. `docker compose logs --tail 100 investigation-worker` shows safe worker codes; the existing steps API includes screened condition/field/citations, SDK exception class, HTTP status, request ID and bounded retry metadata when available. See [container operations](docker_operations.md) and [the preserved Groq fixes](groq_investigation_debugging.md).

Input accounting reserves UTF-8 prompt bytes plus framing as a conservative token upper bound for supported byte-level chat tokenizers. It is not an exact model tokenizer. Each call also reserves up to 2048 completion tokens, enforced by the SDK request, within the total output budget. Actual provider usage is stored separately and checked against reservations. Failed/ambiguous calls do not refund reservations because they may have been charged. Prompts also have a hard 32768-byte/framing bound. Small budgets fail explicitly rather than silently dropping required context or making extra calls.

The `.env.example` model example comes from [Groq's supported-model list](https://console.groq.com/docs/models). Model availability and access can change. A key/model that is configured is not a guarantee of provider reachability or a valid schema response; those failures are recorded independently.

## API and authorization

All routes are under `/api/v1`, use existing bearer JWTs, and return the existing error envelope. Machine ingestion keys do not grant these permissions.

| Endpoint | Roles / behavior |
| --- | --- |
| GET `/investigation-capabilities` | All human roles; provider/model/configured/synthetic metadata |
| POST `/incidents/{id}/investigations` | Admin/engineer; JSON `{}` or `client_request_id` UUID; 202 with Location |
| GET `/incidents/{id}/investigations` | All roles; limit/offset page |
| GET `/investigations/{id}` | Status, stage, requesting actor, timestamps, budgets/usage, safe error |
| GET `/investigations/{id}/evidence` | Paginated redacted excerpts and source provenance |
| GET `/investigations/{id}/steps` | Paginated attempt/stage/status/timestamp metadata |
| GET `/investigations/{id}/report` | Completed/inconclusive structured report; 409 until one exists |
| POST `/investigations/{id}/cancel` | Admin/engineer; idempotent when cancelled; 409 for other terminal states |
| POST `/runbooks`, `/runbooks/upload` | Admin-only controlled JSON/text ingestion |
| GET `/runbooks`, `/runbooks/{id}` | All roles; page/current document |
| PATCH `/runbooks/{id}` | Admin; expected_version plus changes, immutable new document version |
| POST `/runbooks/{id}/reindex` | Admin; expected_version, new version/chunks even with unchanged text |
| POST `/runbooks/search` | All roles; bounded query, optional service ID, limit 1–20 |
| GET `/runbooks/{id}/versions/{version}` | All roles; immutable stored version |
| GET `/runbook-chunks/{id}` | All roles; chunk text/offset/hash/version ID |
| POST `/incidents/{id}/resolution-notes` | Admin/engineer; append human note only to resolved/closed incidents |
| GET `/incidents/{id}/resolution-notes` | All roles; page |
| POST `/incidents/{id}/similar` | All roles; excludes current incident; lexical/structured historical search |

Collection pages use limit 20 by default, maximum 100, and offset 0–100000. Evidence selection is independently capped; pagination never expands what the model received. The report is bounded to 60000 serialized bytes before persistence, with database JSONB size checks as a final boundary. All actions require human review and have read-only risk classification.

Admission allows one queued/running investigation per incident, five per requester and twenty per database schema. A schema-scoped PostgreSQL advisory transaction lock makes the caps race-safe; a partial unique index independently protects each incident. Reusing the same actor/client_request_id returns the original investigation without resetting cost or work. Reusing it for another incident returns 409. New IDs against an active incident return 409; capacity exhaustion returns 429. There is no automatic investigation for every log or detection.

## Lifecycle, workers and cancellation

Investigation lifecycle: queued → running → completed/inconclusive/failed, with retryable failures returning to queued. Queued/running work can become cancelled. Reports and failed status remain in PostgreSQL after restarts. Investigating does not change incident severity, assignee, status, version or audit events.

Start the dedicated worker from repository root:

```powershell
$env:PYTHONPATH = 'backend'
uv run python -m app.workers.investigation_worker
```

The worker claims `investigation_jobs` with SKIP LOCKED in a short transaction, persists a fresh fencing token and attempt number, then runs the graph. It refreshes its lease during guarded short stage/checkpoint/budget transactions. No live session is stored in graph state or held open during a hosted call. Lease expiry permits recovery; stale/cancelled owners cannot update checkpoints or reports. Claim exhaustion becomes failed and inspectable instead of disappearing.

Transient failures use bounded backoff and honor valid provider Retry-After seconds/HTTP dates; 429 defaults to 30 seconds when no valid delay exists. Permanent credentials/parameters/oversize/quota failures, exhausted call/output reservations, fixed-cap truncation and retries beyond the deadline terminate. Successful stage checkpoints are reused, so failure at final persistence does not call the model again. A crash after a remote response but before checkpoint commit may repeat a charge. This is not exactly-once remote execution.

Cancellation immediately persists cancelled status and invalidates the job token. It prevents later checkpoints/report writes. An already-in-flight remote call may take until its timeout to return and may still be charged. Ctrl+C/SIGTERM stops new claims and lets the bounded current operation finish. A forced process kill relies on leases. `--once` attempts one bounded batch; it does not wait for retry backoffs or guarantee the queue is empty.

## Complete synthetic demonstration

Use an existing bootstrapped admin; see [README](../README.md). Run all terminals from the repository root.

Setup:

```powershell
uv sync --locked
docker compose -f compose.yaml -f compose.dev.yaml up -d --wait postgres
docker compose --profile test up -d --wait postgres-test
uv run alembic -c backend/alembic.ini upgrade head
```

API terminal:

```powershell
$env:INVESTIGATION_LLM_PROVIDER = 'fake'
$env:INVESTIGATION_LLM_MODEL = 'fake-v1'
uv run uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000
```

Detection terminal:

```powershell
$env:PYTHONPATH = 'backend'
uv run python -m app.workers.detection_worker
```

Investigation terminal:

```powershell
$env:PYTHONPATH = 'backend'
$env:INVESTIGATION_LLM_PROVIDER = 'fake'
$env:INVESTIGATION_LLM_MODEL = 'fake-v1'
uv run python -m app.workers.investigation_worker
```

Demo terminal:

```powershell
$env:PYTHONPATH = 'backend'
$env:DEMO_ADMIN_EMAIL = Read-Host 'Admin email'
$env:DEMO_ADMIN_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'Admin password' -AsSecureString)).Password
try {
    uv run python -m app.simulations.investigation_demo --scenario all
} finally {
    Remove-Item Env:DEMO_ADMIN_EMAIL, Env:DEMO_ADMIN_PASSWORD -ErrorAction SilentlyContinue
}
```

The CLI first checks provider capabilities, then creates clearly labeled services/rules, ingests Phase 2 fixture logs, waits for real detection jobs, creates matching runbooks, requests investigations, waits for reports, checks citation scope and verifies incident data stayed unchanged. It never inserts directly into the database. Database/API/latency/conflicting/malicious cases complete with tentative alternatives; missing evidence is inconclusive without model calls. The malicious source is quarantined before prompt construction. Temporary ingestion keys are revoked; synthetic resources remain for inspection.

To demonstrate missing-provider isolation, run the API with `INVESTIGATION_LLM_PROVIDER=groq`, empty `GROQ_API_KEY` and empty model, then use the same credential setup and:

```powershell
uv run python -m app.simulations.investigation_demo --scenario unavailable
```

It expects a clear 503 and verifies normal readiness/log access. It refuses to run this case when hosted configuration is present.

## Opt-in live Groq demonstration

One explicitly authorized bounded live investigation completed before Phase 6; [the diagnostic report](groq_investigation_debugging.md) records its two requests, checked report and remaining uncertainties. Phase 6 automated tests make no further Groq calls. Adapter regressions exercise the actual SDK through mocked HTTP, while container/browser workflows select the deterministic fake provider explicitly.

In each API/investigation-worker terminal, configure private environment values before restarting that process:

```powershell
$env:INVESTIGATION_LLM_PROVIDER = 'groq'
$env:INVESTIGATION_LLM_MODEL = 'openai/gpt-oss-120b'
$env:GROQ_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Groq API key' -AsSecureString)).Password
```

Start the API and investigation worker with the preceding commands; keep the detection worker running. Provide admin environment credentials in the demo terminal, then explicitly opt in:

```powershell
uv run python -m app.simulations.investigation_demo --scenario database --live-groq --timeout 180
```

This transmits bounded redacted evidence to Groq and may incur charges. Choose a currently accessible model and appropriate budgets; rate limits, refusals, truncation or schema/grounding failures can yield retries/failed investigations. Inspect status/steps and safe errors. A completed live report must show `synthetic_provider=false` and valid citations. Remove the key from the shell when finished with `Remove-Item Env:GROQ_API_KEY -ErrorAction SilentlyContinue`. No key is returned by capability, investigation or report endpoints.
