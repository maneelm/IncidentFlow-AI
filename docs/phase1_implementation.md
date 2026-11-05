# Phase 1 implementation guide

## 1. What was built

IncidentFlow AI now has a FastAPI backend for people managing production incidents. An admin creates a service such as `checkout`. An engineer creates an incident affecting that service, assigns an eligible user, and moves the incident through investigation, mitigation, resolution, and closure. Viewers can inspect the same information without changing it. Every accepted incident change creates an audit event that explains who changed what.

This phase establishes reliable backend behavior. There is no log ingestion, detection engine, AI investigator, RAG store, remediation executor, React dashboard, or application container image. PostgreSQL is the only Docker Compose dependency. Future work should build on these transaction and authorization boundaries.

## 2. Important modules

| Location | Responsibility |
| --- | --- |
| `pyproject.toml` | Python requirement, runtime/development dependencies, pytest and Ruff settings |
| `uv.lock` | Exact resolved dependency graph used by `uv sync --locked` |
| `.env.example` | Names and shapes of configuration fields without usable credentials |
| `scripts/init_env.py` | Generates a private ignored `.env` using cryptographic randomness; refuses overwrite |
| `compose.yaml` | Persistent development PostgreSQL and separate disposable test PostgreSQL |
| `backend/app/main.py` | Validates settings through an app factory, creates engine/factory, installs CORS/error handling, mounts routes, disposes engine |
| `api/dependencies.py` | One session/transaction per request, JWT authentication, role checks, service construction |
| `api/v1/router.py` | Shared `/api/v1` prefix and documented error responses |
| `api/v1/auth.py` | Form login and current-user HTTP endpoints |
| `api/v1/services.py` | Catalog HTTP endpoints; validates responses and creates Location headers |
| `api/v1/incidents.py` | Incident, assignment, transition, query, and history HTTP endpoints |
| `api/v1/health.py` | Liveness and database/migration readiness checks |
| `core/config.py` | Typed environment settings, secret/driver/origin/lifetime validation |
| `core/security.py` | Argon2id hashing and JWT creation/validation; no application permissions |
| `core/errors.py` | HTTP-independent domain exceptions carrying a safe status/code/message |
| `db/base.py` | Declarative metadata, deterministic constraint naming, UUID/timestamp mixins |
| `db/session.py` | Async engine and session factory construction; SQL parameter hiding |
| `models/enums.py` | Roles, incident severity/status, audit event types |
| `models/entities.py` | Four ORM entities, relational constraints/indexes, sequence, relationships, version counter |
| `schemas/common.py` | Strict input model, ORM-readable response model, page/error schemas |
| `schemas/auth.py` | Validated login/bootstrap inputs and safe token/user outputs |
| `schemas/services.py` | Catalog create/update/read boundaries |
| `schemas/incidents.py` | Incident mutation/query/read schemas and cross-field validation |
| `repositories/users.py` | User lookup by UUID/email |
| `repositories/services.py` | Service retrieval and paginated catalog queries |
| `repositories/incidents.py` | Incident lookup, filtered/sorted paging, version-ordered events |
| `services/auth.py` | Login, database-user resolution, safe bootstrap creation; expensive hashing in worker threads |
| `services/catalog.py` | Catalog creation, retrieval, updates, and page construction |
| `services/incidents.py` | References, assignment eligibility, version checks, state machine, audit generation |
| `bootstrap.py` | Operator-only environment-driven user creation CLI |
| `backend/alembic.ini` | Migration path and safe logging configuration; no database credentials |
| `backend/alembic/env.py` | Async-to-sync Alembic connection bridge and test-supplied connection support |
| `backend/alembic/versions/0001_initial_incident_management_schema.py` | Frozen initial DDL, sequence and append-only trigger; reversible in disposable schemas |
| `backend/tests/conftest.py` | Dedicated target guard, random schema migration, random passwords, users, HTTP client and sessions |
| `backend/tests/test_api.py` | Authentication, permission matrix, CRUD scope, validation, rollback, lifecycle, concurrency and health tests |
| `backend/tests/test_database.py` | Real constraints, forced concurrent writer race, migration round trip and model drift check |
| `backend/tests/test_unit.py` | Configuration, test safety guards and exact lifecycle graph |

Repositories primarily encapsulate queries. Services use the same session to persist ORM objects directly. Adding an abstraction for every `session.add()` would not improve this small application's architecture. Type hints document interfaces throughout; there is no separate static type-checking tool configured in this phase.

## 3. Following a request from HTTP to PostgreSQL

Consider `POST /api/v1/incidents`:

1. FastAPI selects the incident route and parses JSON into `IncidentCreate`. Invalid severity, missing description, unknown fields, or malformed UUIDs produce a safe 422 response.
2. The dependency graph creates a fresh async session and begins its transaction. All dependencies resolving that same session share it within the request.
3. The bearer dependency reads the token. `decode_token()` checks its signature, algorithm and claims, then returns a UUID. `AuthService.current_user()` looks up that user in PostgreSQL.
4. The writer permission dependency allows only admin/engineer roles. Role information comes from the database, not a stale token role claim.
5. The thin router passes the validated input and actor to `IncidentService.create()`.
6. The service verifies that the service exists and any target assignee is an admin or engineer. It creates an ORM incident with triggered status.
7. `record()` flushes the incident INSERT. PostgreSQL allocates the sequence number, and SQLAlchemy has the UUID, timestamps, and starting version.
8. The service adds a created event with the actor, incident UUID, matching version and JSON creation snapshot. It flushes that event and refreshes the incident.
9. The router builds `IncidentRead` and sets a Location header. The request dependency exits and commits **before** the HTTP response is emitted. The client receives 201 only after that commit succeeds.
10. On any failure, the transaction context rolls back. The error handler returns a consistent error envelope without SQL parameters or credentials.

`AsyncSession` lets the process yield while PostgreSQL performs network I/O. It does not make one session safe to share between requests or concurrent tasks. Independent requests receive independent sessions. Hashing is CPU/memory work and goes to the thread pool rather than blocking the event loop.

## 4. Why SQLAlchemy and Alembic both exist

SQLAlchemy maps Python entities to database rows, builds parameterized SQL, tracks changed fields, manages relationships and transactions, and performs version-aware updates. It is used during normal requests.

Alembic records how the schema changes over time. Revision `0001` creates tables, constraints, indexes, a number sequence, and the audit trigger from an empty database. `upgrade head` applies outstanding revisions; `current` reports the stored revision. `check` detects modeled schema changes that need a new revision. It does not replace review of handwritten objects such as sequences/triggers.

The API never calls `Base.metadata.create_all()` on startup. Automatic creation cannot describe controlled upgrades for an existing deployment. Tests apply the real migrations, including a downgrade/upgrade round trip in a disposable schema, so they validate the actual deployed schema rather than a second schema assembled for tests.

## 5. Authentication and role authorization

Authentication answers, “Which user sent this request?” Authorization answers, “May that user perform this action?”

The operator provisions users with `python -m app.bootstrap --role ...`. Credentials come from `BOOTSTRAP_EMAIL`, `BOOTSTRAP_PASSWORD`, and `BOOTSTRAP_DISPLAY_NAME`; no password command-line argument or default admin exists. Pydantic validates inputs, the email is lowercased, and pwdlib stores an Argon2id hash. Creating a duplicate account fails without changing the original account.

Login verifies the hash and issues a signed JWT with the subject UUID, issued-at/expiration, issuer, audience and access-token type. JWT payloads are readable and therefore contain no passwords or other secrets. The signature protects against alteration; TLS protects the token in transit in a deployment. The secret is required, random, and never logged. Validation fixes the permitted algorithm to HS256 and rejects missing/invalid claims. The default token lifetime is 15 minutes.

The bearer token identifies a database user on every request. The permission dependencies then check that user's current role. Admins manage the catalog and incidents; engineers manage incidents; viewers read. Only admins/engineers may be assignees. Public health and login do not require an existing bearer token. No API can modify history. The complete matrix is documented and parametrized in tests.

Passwords and request input are excluded from validation responses. Database errors are reduced to safe conflict messages. Unknown emails go through an expensive dummy password verification to reduce the most obvious timing difference from a known email with a wrong password. This is not a distributed login rate limiter; a real deployment should provide one at its ingress.

## 6. Lifecycle rules

`TRANSITIONS` is a typed mapping in `services/incidents.py`:

- triggered → investigating
- investigating → mitigated or resolved
- mitigated → resolved
- resolved → closed or investigating
- closed → investigating

The latter two transitions to investigating are explicit reopen operations. A required reason explains every transition. On resolution the service sets `resolved_at`. Closure sets `closed_at` and retains the resolution timestamp. Reopen clears both, records `reopened`, and preserves the prior transitions in history. An invalid jump, same-state transition, or stale version produces 409 and leaves no event behind.

Lifecycle status is excluded from the ordinary PATCH schema, so clients cannot bypass these rules through a general edit. Assignment also has its own endpoint to capture its specific meaning. Ordinary metadata edits remain allowed at every lifecycle state and are audited. No-op edits and repeated assignments are conflicts rather than successful mutations.

## 7. Transactions and audit consistency

A transaction is a unit of database work that either commits together or rolls back together. Flushing issues SQL without making it independently permanent. Committing makes the transaction's work visible to later transactions.

The session dependency owns a single transaction around the request. `record()` first flushes the incident, then flushes its event in that same session. It never commits. A failure during event insertion rolls back the already-flushed incident. A failure deferred until COMMIT also rolls back and returns an error before any success response. Tests install both immediate and deferred rejecting triggers to prove these behaviors against PostgreSQL.

History is append-only through the public API. A database trigger additionally rejects event row UPDATE/DELETE. Exactly one actor representation is required, and the `(incident_id, incident_version)` unique index prevents duplicate events for one version. There is no database trigger that automatically creates events: the service is responsible for creating a meaningful event, atomically with its mutation.

UUID foreign keys prevent references to absent services/users/incidents. RESTRICT deletion preserves references. Timestamp/status checks prevent a resolved incident without a resolution time and a nonclosed incident with a closing time. Input validation gives earlier, clearer feedback; database constraints remain necessary for races and writes outside normal validation.

## 8. Concurrent edits

Imagine two engineers reading incident version 4. Both submit a change with `expected_version: 4`.

If the second request reads after the first commits, its service sees version 5 and immediately rejects the stale expected version. If both read version 4 before either commits, the ORM's `WHERE version = 4` condition is essential. PostgreSQL serializes the row writes; after the winner commits, the losing UPDATE affects zero rows. SQLAlchemy raises `StaleDataError`, the session rolls back, and the API returns `409 version_conflict`.

The winner's incident becomes version 5 and receives one version-5 event. The loser creates neither an incident change nor an event. Retrying without review could overwrite intent, so the client must fetch current state and decide again. This handles updates, assignments, and transitions with one mechanism. It applies to ORM row updates, not arbitrary bulk SQL; bulk writes are not exposed here.

The concurrency tests include simultaneous HTTP requests and a lower-level barrier that guarantees both sessions have read the old version before either writes. The latter proves the database/ORM race path rather than relying on scheduling luck.

## 9. Querying, validation and errors

Incident listing combines status, severity, service, assignee and timezone-aware creation-time bounds. `unassigned=true` selects null assignees. It cannot be combined with an assignee UUID. Date bounds are inclusive and must be ordered. Pagination is bounded to a maximum page size of 100 and offset of 100000.

Sort choices are allowlisted: creation time, update time, numeric incident number or explicitly ranked severity. No user-provided SQL identifiers are interpolated into sort expressions. A UUID tie breaker makes tied sort values deterministic. Read-committed count and item queries can see different concurrent changes; a consistent historical snapshot or cursor API is outside Phase 1.

Request input models reject extra fields and invalid enum/UUID values. Explicit null is allowed for assignment removal but rejected for ordinary PATCH fields. Validation errors include a location and type, never an echoed password or raw exception context. Domain errors, HTTP errors and database conflicts use the same `error` object shape. Internal failures return a generic 500; readiness dependency failures return 503.

## 10. What the tests validate

Tests use a dedicated Compose PostgreSQL service with a separate user, port and tmpfs. Before connecting, the fixture validates the exact local database identity and rejects the application database. Each test creates a random schema, sets a schema-only search path, applies Alembic, and seeds random-password users. It uses actual committed transactions and independent sessions. Cleanup removes only that fixture's generated schema. Missing PostgreSQL fails setup instead of silently skipping integration tests.

The suite checks:

- Successful/failed login, Argon2id hashes, expired/malformed/incorrectly signed tokens, issuer-related token boundaries, absent users, token algorithm/type/audience, and database-based role changes.
- Every role across catalog and incident mutation operations, all read operations, and anonymous rejection.
- Catalog create/list/retrieve/update, duplicate names and unsupported deletes.
- Incident creation/retrieval, numbered identity, all mutation audit shapes and version ordering.
- Pagination, combined filters, timezone/date validation and severity/numeric sorting.
- Both resolution paths, mitigation, closure, reopening from resolved/closed, and invalid jumps.
- Assignment/unassignment, rejected viewer targets and absent users.
- Rollback after audit insertion fails, including deferred commit failure, without mocks.
- Simultaneous HTTP edits and deterministic old-version races for updates and transitions.
- PostgreSQL foreign keys, enum checks, positive versions, timestamp rules, uniqueness, actor constraints and append-only enforcement.
- Empty-database migrations, migration downgrade/upgrade and model drift detection.
- Liveness, ready/unmigrated/unavailable dependencies, OpenAPI, CORS, configuration guards and secret-safe errors/logs.

These checks validate backend behavior, not load capacity, a deployed reverse proxy, external identity providers, or future AI workflows.

## 11. Decisions and trade-offs

1. **One process and one PostgreSQL database.** The initial workload does not justify microservices or extra infrastructure. Module boundaries leave room for later capabilities.
2. **App factory.** Settings are validated when the application is constructed; tests can inject isolated settings/engines without global database sessions.
3. **Simple services/repositories.** Services hold rules and tracked writes; repositories hold queries. A generic domain framework/unit-of-work class would duplicate the session context manager here.
4. **Optimistic incident versioning.** It rejects stale intent and avoids locking an incident while a human reads it. Clients must explicitly handle conflicts.
5. **Integer number sequence plus UUID identity.** Numbers are readable and concurrency-safe; UUIDs are stable resource identifiers. Sequence gaps are expected after rollback.
6. **CHECK-backed string enums.** The database validates values while migrations remain simpler than PostgreSQL native enum alterations.
7. **Strict audit append-only behavior.** History preserves who changed what. Database-owner maintenance can bypass triggers; this is not tamper-proof archival storage.
8. **Local HS256 access tokens.** This fits a single API with a mandatory secret. No refresh-token store, logout revocation, OIDC integration or key-rotation service is implemented.
9. **Offset pagination.** It is understandable and supports bounded browsing. Deep pagination/snapshot consistency require another design if real workloads justify it.
10. **Real PostgreSQL tests.** They require Docker and take longer than in-memory tests, but expose the transaction/sequence/JSONB/trigger/concurrency behavior the project promises.
11. **Local database owner roles.** These simplify development and schema-isolated testing. Production should separate runtime and migration privileges, add TLS, backups, rate limiting and operational monitoring.
12. **No delete endpoints.** Phase 1 requested create/list/retrieve/update. Preserving incidents/history also avoids unrequested destructive lifecycle semantics. “CRUD” tests cover the specified operations and verify that unsupported deletion is rejected.

The repository itself is the requested top-level project directory. Extra health/catalog/bootstrap modules have concrete responsibilities. Root dependency configuration and a shared lock file keep PowerShell/Linux uv commands consistent.

## 12. Interview questions with explained answers

### 1. Why put rules in a service instead of a FastAPI route?

A route translates HTTP. A service describes allowed operations independently of HTTP. Keeping transitions and audit writes in `IncidentService` makes them reusable and testable with independent database sessions, and keeps an alternate transport from needing to duplicate rules.

### 2. Why use Pydantic schemas as well as SQLAlchemy models?

They protect different boundaries. Pydantic validates client input and controls output fields. SQLAlchemy maps persistent state. Reusing the user ORM model as an unrestricted output could expose the password hash; the separate `UserRead` schema excludes it.

### 3. Why must the database also validate references and enum values?

An earlier application check can race with another transaction, and some writes may come from operator tools. Foreign keys, uniqueness and CHECK constraints enforce invariants at the storage boundary even when application validation is bypassed.

### 4. What is the difference between `flush()` and `commit()`?

Flush sends pending SQL inside the current transaction. Commit makes the whole transaction permanent. The service flushes an incident to obtain its number/version, then inserts its event. Only the request-level commit persists both together.

### 5. What happens if an event INSERT fails after the incident UPDATE succeeds?

The UPDATE has only been flushed. The exception leaves the session's transaction context, which rolls back both operations. The test suite forces this exact failure through a PostgreSQL trigger and verifies unchanged incident state and event count.

### 6. Why commit before returning a successful response?

A deferred constraint or database outage can make COMMIT fail after earlier statements succeeded. Sending 200 first would mislead the client. The function-scoped session dependency completes its transaction before FastAPI emits the response; a deferred-trigger test verifies it.

### 7. Why is `expected_version` alone insufficient for concurrency safety?

Two transactions can both read and validate the same old version. The database UPDATE must also include a version predicate. SQLAlchemy's mapped version counter detects that the second writer updated zero rows and raises a conflict.

### 8. Why not automatically retry a conflicting human edit?

A retry against the new version can erase another operator's decision. Returning 409 asks the client to fetch the latest state and review its intended change, preserving user intent rather than merely making the database accept a write.

### 9. Why are incident numbers allowed to have gaps?

PostgreSQL sequences allocate numbers outside transaction rollback semantics. That provides efficient concurrent uniqueness. Human-readable identifiers do not require gapless accounting; requiring it would add contention and complexity without a Phase 1 benefit.

### 10. Why use Argon2id rather than encrypting passwords?

Login needs to verify a supplied password, not recover the original. Argon2id is a salted, memory-hard password hash designed to make offline guesses expensive. Reversible encryption would create an unnecessary password-recovery risk if its key were compromised.

### 11. Does a signed JWT hide its contents?

No. Its payload is readable; the signature proves it was not altered without the signing key. This implementation includes identity and validation claims, never credentials. TLS protects bearer tokens in transit outside local development.

### 12. Why load the role from PostgreSQL on every request?

A token containing an old admin role could continue granting access after demotion. The token here identifies the user; the database supplies the current role. A deleted user is rejected even if the token's signature and expiration remain valid.

### 13. What makes reopening different from an ordinary edit?

It changes lifecycle meaning and resets active resolution timestamps. The dedicated transition rule permits only resolved/closed → investigating, requires a reason, and emits `reopened`, so history explicitly distinguishes renewed investigation from the original response.

### 14. Why does history sort by incident version instead of timestamp alone?

Timestamps may tie and PostgreSQL `now()` is a transaction timestamp. Successful versions form a monotonic order for one incident. The unique `(incident_id, incident_version)` index both prevents duplicate version events and supports reliable ordering.

### 15. What does “append-only” guarantee here?

Clients can retrieve events but have no API for changing/deleting them. The migration also installs a trigger rejecting row UPDATE/DELETE. A database owner could remove that trigger or truncate storage, so it is an application/database protection, not cryptographic tamper evidence.

### 16. Why use a real PostgreSQL test dependency instead of SQLite?

SQLite does not reproduce PostgreSQL sequences, JSONB, constraint triggers, locking, asyncpg behavior or concurrent version updates. The important promises concern those features, so integration tests run the actual database engine with isolated schemas.

### 17. How do tests avoid damaging development data?

They require a dedicated local database/user on a different port, reject the application's database, and use random schemas with schema-only search paths. Cleanup drops only the schema generated by that fixture. There is no truncate-public or drop-database shortcut.

### 18. How do you test a concurrency race deterministically?

Start two independent sessions, have both read version 1, and block each at an async barrier until both reads finish. Release them to perform versioned writes. Exactly one commits; the loser raises `StaleDataError`. Verify one increment and one additional event afterward.

### 19. How does async database access improve this API?

While a request waits for PostgreSQL, the event loop can run other requests. It does not make CPU-heavy hashing nonblocking or permit sharing one session across concurrent tasks. The code uses thread-pool hashing and a separate session per request.

### 20. Why are readiness and liveness separate?

The process may be alive while PostgreSQL is down or migrations are missing. Liveness reports that the HTTP process responds; readiness checks its required database and migration revision. This distinction lets an operator diagnose dependency failures without confusing them with a crashed process.

### 21. Why is sorting allowlisted?

An arbitrary sort string should not become SQL syntax. The repository maps validated choices to SQLAlchemy column expressions, with a special numeric rank for severity. Invalid choices fail validation rather than being concatenated into a query.

### 22. What should Phase 2 add?

Log ingestion with validated inputs, deduplication/idempotency semantics, retention decisions, and deterministic detection rules is a natural next step. New detection work should invoke established incident/audit transaction rules after defining a system-actor workflow, rather than bypassing them with direct writes.

## 13. Upstream references used

The security implementation follows the library choices documented in [FastAPI's password hashing/JWT tutorial](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/). SQLAlchemy's [async session guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) explains the per-task session boundary. Its [version-counter documentation](https://docs.sqlalchemy.org/en/20/orm/versioning.html) describes the version predicate used during ORM flush. Alembic's [asyncio cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic) describes bridging async connections to its synchronous migration machinery.

## 14. Actual validation record

Verified on **2 November 2026**, from the repository root in Windows PowerShell, with Python 3.12.4, uv 0.11.28, Docker Desktop's Linux engine, and PostgreSQL 17 in Compose. The lock selected FastAPI 0.162.0, Pydantic 2.14.2, SQLAlchemy 2.0.55, asyncpg 0.32.0, Alembic 1.20.1, pwdlib 0.3.1, and PyJWT 2.15.1.

| Acceptance criterion | Observed result |
| --- | --- |
| Dependencies install | `uv sync` installed successfully; `uv sync --locked` confirmed the environment |
| PostgreSQL starts through Compose | Both development/test services reported healthy on their separate loopback ports |
| Empty-database migrations | Revision 0001 applied to the fresh development database; every integration fixture migrated a new schema |
| Secure local user authentication | Real bootstrap CLI created admin/engineer/viewer users with generated credentials; all three logged in over real HTTP |
| Authorized incident management | Live HTTP created a service/incident, assigned it, completed lifecycle and reopen operations |
| Unauthorized operations | Live viewer mutation returned 403 and anonymous access returned 401; full role matrix passed integration tests |
| Correct transitions | Both resolution paths and reopen paths passed; invalid jumps were rejected |
| Audit for every accepted mutation | Tests checked version/event pairing; live workflow ended at version 9 with 9 events |
| No partial state after failure | Immediate audit failure and deferred-at-commit failure rolled back database changes in PostgreSQL |
| Pagination/filtering | Integration tests passed combined filters, date bounds, pages and sorting; live closed/assignee/service filter returned the incident |
| Full test suite | **136 passed in 70.45 seconds**: 117 PostgreSQL integration checks and 19 unit checks; no skipped tests |
| Ruff | `uv run ruff check .` passed; `uv run ruff format --check .` reported all 46 Python files formatted |
| Swagger accessible | `/docs` and `/openapi.json` returned 200 from a real Uvicorn process on 127.0.0.1:8000 |
| README setup verified | Ran dependency install, local secret generation, Compose startup, migration, environment-driven bootstrap, Uvicorn, tests and lint checks |

`alembic check` also reported **No new upgrade operations detected**. The migration round-trip test checked downgrade-to-base then upgrade-to-head in its own disposable schema. A source/documentation scan found the generated database/JWT secrets only in the ignored `.env`.

The first full test run found one test using an invalid role longer than the column width, which raised a length error before the intended CHECK constraint. Its input was corrected to exercise the intended constraint; the final full suite above passes. IPv4 connection URLs also removed a measured Windows localhost fallback delay (about 2.1 seconds versus 0.05 seconds per connection during diagnosis).

The live check retained clearly labeled development verification data: three generated-credential users, one service, one closed incident and nine events. The generated verification passwords were not printed or saved; use the documented bootstrap command to create your own account. The private local `.env` and installed `.venv` already exist on this machine. PostgreSQL containers remain running; the temporary validation Uvicorn process was stopped. Start the API using the README command.

All requested Phase 1 functionality is implemented and its acceptance checks were exercised. Deployment hardening described in README remains outside this local Phase 1 delivery. No later-phase features were implemented. The next suggested phase is validated log ingestion and deterministic detection rules.
