# Phase 5: React operations dashboard

This is the historical Phase 5 implementation record. Phase 6 now provides the container deployment described in [the deployment guide](phase6_deployment.md); its current commands supersede historical hosting limitations below.

## 1. What shipped

The existing backend now has a complete local React operations console. It authenticates real users and reads/writes actual IncidentFlow APIs for incidents, services, logs, rules, investigations, runbooks and human-approved synthetic resolution. There are no production-view mock arrays, fabricated health metrics, executable AI recommendations, frontend-only approvals or new deployment systems.

The visual system is dark-first, restrained and focused on operational records: a persistent desktop sidebar/top bar, bounded tables, severity/status badges, readable evidence, explicit synthetic labels, accessible dialogs and responsive mobile navigation. Desktop and mobile layouts were inspected through actual browser screenshots.

Backend additions are limited to authenticated read models, an eligible-user directory, incident-scoped detections and API no-store headers. No new database migration or backend dependency was required. Migrations 0001–0004, all 386 earlier tests and `uv.lock` are preserved.

## 2. Stack and organization

Installed/locked versions include React 19.3.1, TypeScript 5.9.3, Vite 8.3.5, React Router 7.18.5, TanStack Query 5.105.3, Tailwind 4.3.4, Lucide 1.66.0, Recharts 3.10.1, Vitest 5.1.0, RTL 16.3.3 and Playwright 1.64.1. npm dependencies are pinned in package.json/package-lock.json. No extra state-management library was introduced.

`src/app` owns route composition; `features` owns focused workspaces; shared `components` own layout/forms/tables/dialogs; `lib` owns API, memory session, formatting and permissions; generated `types` reflect real Pydantic contracts. Feature pages load lazily, including the chart bundle. See [frontend architecture](frontend_architecture.md) for every route, cache rule and data boundary.

## 3. Windows prerequisites and the local Node runtime

The tested runtime is Node **22.23.4**. The installed browser-test dependencies require at least 22.22.2 within the 22.x line; accepted alternative lines are declared in `frontend/package.json`. Current Vite's underlying runtime requirement is documented in its [official guide](https://vite.dev/guide/). A compatible standard Node/npm installation can run the ordinary npm commands directly.

This machine's existing system Node is 20.17.0. It was not replaced. A portable Node package is prepared in ignored `.tools`. To use it in a PowerShell terminal, run the following **inline** commands. They do not change system installation or PowerShell execution policy:

```powershell
Set-Location C:\Users\manee\Desktop\incidentflow
# Only needed if the ignored portable runtime is missing:
# npm install --prefix .tools --no-save node@22.23.4
$incidentFlowNode = (Resolve-Path '.tools\node_modules\node\bin\node.exe').Path
$incidentFlowNpm = Join-Path (Split-Path (Get-Command npm.cmd).Source) 'node_modules\npm\bin\npm-cli.js'
$env:Path = "$(Split-Path $incidentFlowNode);$env:Path"
function npm { & $incidentFlowNode $incidentFlowNpm @args }
node --version
npm --version
```

The helper is session-only; repeat it in a new frontend terminal if using the portable runtime. It was exercised through the equivalent explicit Node/npm CLI invocation during verification. No `.ps1` execution-policy change is necessary.

## 4. Exact startup commands

Run setup from repository root, preserving the existing private `.env`. For a fresh checkout, use the README's configuration generator first; it never overwrites an existing `.env`.

```powershell
Set-Location C:\Users\manee\Desktop\incidentflow
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

Resolution terminal:

```powershell
$env:PYTHONPATH = 'backend'
uv run python -m app.workers.resolution_worker
```

Frontend terminal, using a compatible Node or the inline portable setup above:

```powershell
Set-Location C:\Users\manee\Desktop\incidentflow\frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. Vite proxies `/api` to 127.0.0.1:8000, so no browser token is sent to an arbitrary development origin. `VITE_API_BASE_URL` is public configuration, not secret storage. `API_PROXY_TARGET` can override the proxy through the Vite process environment. No Phase 6 deployment or application containers were added.

## 5. Accounts and complete interactive demo

Use existing distinct admin, engineer and viewer accounts. [README bootstrap](../README.md) provisions them privately; no default credentials exist. To seed a fresh unresolved database incident through real Phase 2 ingestion/detection APIs:

```powershell
Set-Location C:\Users\manee\Desktop\incidentflow
$env:PYTHONPATH = 'backend'
$env:DEMO_ADMIN_EMAIL = Read-Host 'Administrator email'
$env:DEMO_ADMIN_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'Administrator password' -AsSecureString)).Password
try {
    uv run python -m app.simulations.demo --scenario database --seed 42
} finally {
    Remove-Item Env:DEMO_ADMIN_EMAIL, Env:DEMO_ADMIN_PASSWORD -ErrorAction SilentlyContinue
}
```

Then demonstrate the real dashboard:

1. Sign in as admin; inspect Overview, the registered synthetic service, rules and detected incident. Initialize that service's synthetic database-exhaustion scenario on its service detail page.
2. Sign in as engineer; open the detected incident, inspect its linked logs/detection snapshot and explicitly transition it to investigating if desired.
3. Start a fake-provider investigation; wait for its recorded stages/report. Open citations and read uncertainty/missing evidence.
4. From the incident's resolution tab, explicitly choose a synthetic recovery action, link usable investigation sources and write a human explanation. Submit the immutable draft.
5. Verify the proposer cannot self-approve a mutation. Sign out and have a different authorized human sign in at the proposal URL; approve with a reason.
6. Observe the separate worker's synthetic result, before/after versions and verification. The incident has not silently resolved.
7. Read the unified timeline, loading its continuation cursor. A human may separately request the original incident transition with a reason/version.
8. Sign in as viewer; inspect records and confirm mutation controls are absent. Backend authorization remains enforced even for manually crafted requests.

The existing complete CLI resolution demo remains available in [resolution operations](resolution_workflows.md), including protected three-account environment setup. It resolves its own synthetic incident explicitly at the end; seed a fresh Phase 2 incident when demonstrating new proposal creation interactively.

## 6. Authentication, APIs and state

A large frontend need not mean a large client-side database. TanStack Query owns server state, React owns temporary form/dialog state, and PostgreSQL remains authoritative. Login exchanges OAuth form credentials for a short-lived bearer token, then reads the user. Tokens/user/cache live only in memory and are cleared on logout/expiry. Safe deep links resume after sign-in; refresh/new tabs reauthenticate.

Generated OpenAPI types make actual fields visible during development. The fetch client handles bearer headers, no-store, aborts, query encoding and safe error envelopes. Queries have bounded safe retries; mutations never automatically replay. Role checks affect controls but cannot grant permission. A 403 remains a backend refusal.

Read caches are fresh for fifteen seconds. Successful writes invalidate dependent API views. A conflict fetches current data but leaves an open edit/review tied to its reviewed version. Status polling is two seconds while active and disabled in the background; terminal status stops the interval. Related evidence/steps/job/events take a final revision-specific snapshot, preventing a fast completion from stranding empty intermediate results. Readiness polling is fifteen seconds. No WebSocket or refresh-token endpoint is invented.

## 7. Incident, evidence and resolution UX

Incident listing supports actual backend fields and sort direction; no unsupported title search is claimed. Forms create, edit, assign, transition and reopen using real schemas/versions. Related detections are queried by incident rather than guessed from a truncated global page. Log search/time/correlation filters remain bounded.

Investigations separate deterministic facts from unverified hypotheses and read-only suggestions. Fake outputs have explicit synthetic labels. Citations open actual source references/provenance; source text is never executable HTML. Runbooks preserve version/hash/chunk identities and display text safely. Their lexical search score is a ranking signal, not a confidence probability.

Resolution forms require explicit catalog choices and known synthetic state parameters. Read-only recommendation linking is a human selection; recovery proposals remain human authored and require another reviewer. The form UUID remains stable for an attempt; uncertain network outcomes freeze the submitted payload for an explicit identical retry. Proposal versions and reasons accompany real review endpoints. Before/after snapshots and verification never become an automatic incident resolution.

Timeline pages follow returned cursors in chronological order and preserve source IDs. They are READ COMMITTED views with an upper time watermark, not permanent snapshots of late commits. Offset collection pages can move during concurrent writes.

## 8. Verification commands and actual results

From `frontend/`, using the supported runtime:

```powershell
npm ci
npm run api:check
npm run typecheck
npm run lint
npm test
npm run build
node node_modules/@playwright/test/cli.js install chromium
npm run test:e2e
```

`api:generate` intentionally refreshes tracked types after a reviewed backend schema change; `api:check` compares without overwriting them. Frontend lint includes tests and browser scripts. Build performs strict checking and produces the standalone `dist/` artifact, without deploying it.

From repository root:

```powershell
uv run pytest -q --tb=short
uv run ruff check .
uv run ruff format --check .
uv run alembic -c backend/alembic.ini current
uv run alembic -c backend/alembic.ini check
```

Verified on **2026-11-03**:

| Check | Actual result |
| --- | --- |
| Backend full regression | **395 passed in 721.65 seconds (12:01)**: 312 PostgreSQL integration tests and 83 unit tests, including all original 386 and nine new read-model/auth/cache tests |
| Frontend Vitest/RTL/API | **49 passed**, latest full run **6.71 seconds** |
| Browser end-to-end | **5 passed in 19.7 seconds**, actual Chromium + FastAPI + PostgreSQL, no hosted model calls |
| npm clean install | `npm ci` passed using the pinned lockfile and Node 22.23.4 |
| Strict TypeScript / ESLint | Both passed |
| OpenAPI contract comparison | Passed |
| Production frontend build | Passed, feature pages/chart bundle split into lazy chunks |
| Ruff lint / formatting | Passed |
| Alembic | Head remains **0004**, **No new upgrade operations detected** |

The browser suite covers real authentication/deep links, lifecycle changes, detected evidence/logs, fake investigation/report/citations, human-authored proposal, distinct-user approval, durable worker verification, unchanged incident lifecycle after simulation, viewer backend denial, token disappearance on refresh, optimistic conflict preservation, log pagination, runbook versions, desktop layout and mobile keyboard navigation/focus restoration. Screenshots were visually inspected. Earlier failed iterations exposed and corrected login redirect, final linked-data refresh and dialog focus issues; generated code alone was not treated as verification.

Final audits confirmed all original backend test files, migrations 0001–0004 and `uv.lock` byte-for-byte unchanged. Ruff reported **152 Python files already formatted**. All **66 relative documentation links** and UTF-8/fenced blocks passed; a credential scan of **224 project text files**, including **19 production build files**, found no configured private credentials. After browser/backend testing, **zero generated test schemas** remained. Validation API/frontend/worker processes were stopped; PostgreSQL remains running. Browser fixture records/accounts were removed with their owned schema; normal development demo records were not reset.

## 9. Test isolation and limits

Vitest uses mocked responses only in test files to exercise isolated behavior. Production pages read actual APIs. The browser command starts `scripts/browser_harness.py`, validates the dedicated local test DB identity, migrates its own random schema and provisions temporary human accounts. Actual ingestion APIs and worker implementations produce its records. The three worker loops run concurrently inside that fixture process; ordinary developer workers still launch separately. Credentials pass through parent-owned pipes/process memory, never fixture files/bundles. Normal teardown stops owned servers/workers and removes only that schema.

The hosted Groq adapter/provider quality is not reverified by fake tests. No browser engine besides Chromium is claimed. Testing mobile viewport/keyboard behavior is not a complete assistive-technology audit. No account provisioning UI, tenant ACL, production infrastructure executor, deployment, server-side cookie session or automatic refresh exists. Runbook content renders source text, search yields bounded lexical excerpts, and selector/version collections are paginated/capped rather than unbounded.

Phase 6 can consume the frontend build, backend proxy/API boundary, environment contracts and test commands to design deployment. This phase implements none of that hosting/container work.

## 10. Frontend/full-stack interview questions

### 1. Why use React Router instead of a single component switching on strings?

Routes make deep links, browser navigation, protected workspaces and lazy page boundaries explicit. Incident tabs also preserve their selection in the URL. Authentication can return to a validated internal route without trusting an arbitrary redirect string.

### 2. Why distinguish local form state from server state?

Form selections and open dialogs are temporary client interactions. Incidents, reports and approvals belong to PostgreSQL and must be fetched/revalidated. TanStack Query manages that lifecycle without making a second authoritative incident store in React.

### 3. Why keep access tokens only in memory?

It avoids persisting a reusable credential across refreshes or leaving it in browser storage. The trade-off is deliberate reauthentication on refresh/new tabs. It does not make an in-memory token immune to a compromised page, so untrusted content still must be rendered safely.

### 4. Why fetch the user after receiving a JWT?

The backend response supplies the actual current user and role. The UI does not infer authorization from an unverified payload. The backend still evaluates authority on each protected request, even if the UI's role affordance becomes stale later.

### 5. How do stale requests avoid logging out a newer session?

Requests capture a session revision before fetch. If login/logout changes that revision, an older response is rejected as stale rather than allowed to mutate the new session. Only a current authenticated 401 triggers expiry.

### 6. Why is there no refresh-token loop?

The existing API provides no refresh endpoint. Inventing one in the client would conceal a missing server capability and could cause repeated failures. The application uses the issued lifetime and explicit sign-in instead.

### 7. What does generated OpenAPI typing protect?

It makes actual request/response fields, enums and pagination shapes available to TypeScript. A regeneration comparison detects schema drift. It is not runtime validation of arbitrary servers; FastAPI/Pydantic remains the runtime boundary.

### 8. Why centralize fetch and errors?

Authentication headers, no-store, query encoding, cancellation and error parsing should behave consistently across every page. A shared ApiError preserves status/code/validation locations, allowing a conflict to differ from provider unavailability or a network failure.

### 9. Why not retry every failed request?

A lost POST response may follow a committed write. Automatic replay could create another incident or decision. Only safe GET reads get a bounded retry; proposal creation additionally retains its client UUID/payload for an explicit, reviewable identical retry.

### 10. How do idempotency and expected versions differ?

Idempotency identifies the same create attempt across delivery uncertainty. Expected versions identify the record the human reviewed. Both are needed: one prevents duplicate resource creation, while the other prevents silently approving or overwriting newer state.

### 11. Why not automatically resubmit a 409 with the new version?

That would replace the human's reviewed context with data they have not considered. The UI fetches current state and explains the conflict, while the open dialog retains its original version. The user must compare and reopen deliberately.

### 12. What belongs in a query key?

The resource path, filters/page position and any relevant workflow revision define the data snapshot. Omitting filters can show another page's results. Revision-specific child reads also prevent completed workflows from displaying stale intermediate evidence.

### 13. Why refresh evidence after terminal status?

A fast worker can finish between the initial empty evidence read and the next status poll. If polling stops immediately with a still-fresh empty cache, citations appear missing. Status/version keys force one final read before interval polling ends.

### 14. Why poll rather than use WebSockets?

The backend exposes durable status reads and no WebSocket contract. A two-second active-work interval is simple, bounded and cancelable, with background fetching disabled. Adding a fictitious real-time transport would not improve correctness.

### 15. Why calculate overview counts in PostgreSQL?

Fetching every incident to count it would be unbounded and wasteful. SQL grouping returns exact stored-record counts and small recent arrays. The UI clearly states those counts include simulations and are not production telemetry.

### 16. How are sparse charts kept truthful?

Missing status/severity groups mean zero records, not invented samples. Empty datasets get an explicit empty state instead of decorative fake bars. Axis/labels and text alternatives identify the actual count being shown.

### 17. Why is incident title search absent?

The audited incident endpoint supports structured filters but no text-search field. The UI exposes those real filters and says so. Sending a guessed search parameter or filtering one page while implying a global search would be misleading.

### 18. How does cursor pagination differ from offset pagination?

The timeline continuation encodes an ordering position and time watermark. The frontend forwards that cursor rather than calculating an offset. Ordinary lists use explicit limit/offset; mixing these models would skip or duplicate timeline records.

### 19. Why render logs and runbooks as text?

They are untrusted inputs that may contain HTML or instructions. React text escaping prevents them from becoming executable DOM. Source Markdown syntax is retained safely; no raw HTML renderer or executable document tool is included.

### 20. Why can AI recommendations not preselect recovery execution?

A descriptive suggestion is not an authorization decision. The human explicitly selects a hardcoded catalog ID and usable evidence; synthetic recovery remains human authored and needs another reviewer. The browser never turns prose into a callable.

### 21. How is separation of duties represented in the UI?

Eligibility considers role, pending state, deadline and proposer identity/risk. A self-authored synthetic mutation disables both review choices and explains that admins have no override. The backend independently enforces the same rule.

### 22. Why show before/after state without resolving the incident?

Those values describe only the isolated simulator contract. They do not measure production recovery. Keeping incident transition controls separate prevents a successful synthetic demonstration from becoming a false operational resolution.

### 23. What makes a dialog accessible beyond using a modal-looking box?

It needs a meaningful name, labeled fields, keyboard containment/Escape handling and restored trigger focus. Native dialogs provide much of the behavior, while explicit cleanup restores focus reliably when React unmounts them. Browser tests verify that interaction.

### 24. What do component tests prove that browser tests do not cover cheaply?

They can deterministically control network failures, exact request bodies, expiry timers, stale-response races and polling transitions. They are fast and isolate the client rule under test. They do not prove that the real API, database and workers integrate correctly.

### 25. Why use real PostgreSQL in browser end-to-end tests?

The important workflow includes actual detection, evidence, approvals, versions and durable execution. Mocking those APIs would bypass the constraints the UI must respect. A validated isolated schema makes realistic tests reproducible without resetting application data.

### 26. How does this prepare for deployment without implementing it?

The frontend already produces a separate build artifact and uses a centralized API origin boundary, pinned dependencies and repeatable checks. Those are inputs to a deployment design. They do not claim that Vite development proxying, TLS, production hosting or container orchestration has been deployed.
