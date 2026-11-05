# Explaining IncidentFlow AI

This guide follows the shipped implementation, including the UI and release work. Development used Codex assistance. A credible interview explanation should describe the decisions, failure modes and code you can inspect or modify; it should not claim every line was manually authored.

## A 30-second introduction

“IncidentFlow is an incident response workbench. It ingests structured logs, uses deterministic rules to create incidents, and runs an evidence-grounded LangGraph investigation. A human can then propose an allowlisted synthetic action, with a different reviewer required for state changes. PostgreSQL stores the jobs, citations, approvals and audit history. The React console shows the real records, and Docker Compose plus CI make the complete workflow repeatable.”

## A 90-second technical explanation

“The browser reaches FastAPI through an Nginx same-origin proxy. Authentication verifies a short-lived JWT and reads the user's current database role. Each request owns an async SQLAlchemy transaction, so a state change and its audit event commit together. Optimistic versions prevent one operator from silently overwriting another.

“Structured log ingestion creates durable PostgreSQL jobs. A separate detection worker evaluates receipt-time windows and deduplicates correlated incidents. Investigations use eight LangGraph stages, persisted checkpoints, redacted source snapshots and PostgreSQL full-text retrieval. Groq is optional; only two reasoning stages call it. The results remain uncertain hypotheses, and citation membership is checked without pretending that it proves causality.

“Resolution is deliberately constrained. A person selects an action from a fixed synthetic catalog, reviews immutable context and requests approval. A different engineer or administrator must approve a synthetic mutation. The resolution worker revalidates that snapshot and commits only synthetic state and verification. It never resolves the real incident automatically.

“I can demonstrate the whole flow through the real UI and workers, explain the database fencing and transaction boundaries, and show tests for concurrency, crashes, malformed model output, authorization and deployment recovery.”

## Code paths to walk through

| Topic | Start here | Explain the boundary |
| --- | --- | --- |
| Browser request | [API client](../frontend/src/lib/api/client.ts), [query policy](../frontend/src/lib/api/query.ts) | Memory-only token, cancellation, server cache, no automatic mutation replay |
| Incident write | [Incident service](../backend/app/services/incidents.py) | Reviewed version, transaction, reference validation and audit |
| Durable detection | [Detection worker](../backend/app/workers/detection_worker.py) | Claim, lease, retry, atomic effects and idempotence |
| Investigation | [Graph](../backend/app/investigation/graph.py), [store](../backend/app/investigation/store.py) | Eight nodes, remote wait outside transactions, fenced checkpoints |
| Provider/grounding | [Provider](../backend/app/investigation/providers.py), [analysis](../backend/app/investigation/analysis.py) | Structured shape, safe errors, citations and uncertainty checks |
| Approval | [Resolution service](../backend/app/services/resolution.py), [worker](../backend/app/workers/resolution_worker.py) | Immutable intent, distinct reviewer, context revalidation and synthetic effects |
| Timeline | [Timeline query](../backend/app/services/incident_timeline.py) | Ordered union of real sources with bounded cursor pages |
| Deployment | [Launcher](../backend/app/ops/launch.py), [Compose](../compose.yaml) | Internal URL, migration gate, independent processes and persistence |
| Release | [Audit](../scripts/check_release.py), [container verifier](../scripts/verify_containers.py) | Index/path/secret checks and real isolated infrastructure verification |

## Technical questions and explained answers

### 1. Why did you choose a relational database for both records and jobs?

Incidents, evidence, approvals and audit records need relational integrity. PostgreSQL also lets a request commit a job in the same transaction as its application data, avoiding an extra broker's dual-write problem. This reduces moving parts; it is not a claim that a database queue is optimal at every scale.

### 2. What do the route, service and repository layers each do?

Routes parse the contract and apply authentication/role dependencies. Services enforce lifecycle, reference, approval and concurrency rules. Repositories contain bounded queries, while a request-scoped session owns the commit; duplicating commit ownership across layers would make atomicity harder to reason about.

### 3. Why does the service flush instead of independently committing?

Flush obtains generated IDs and versions without ending the request transaction. The service can then add the related audit record. The dependency commits before emitting the response, or rolls back both records if any later validation/constraint fails.

### 4. How do concurrent incident edits avoid lost updates?

The submitted `expected_version` is compared with the observed version, and SQLAlchemy includes its version predicate in the update. A competing commit can therefore raise a stale-data conflict even after the initial read. The UI shows 409, preserves the operator's reviewed intent and requires a refreshed review rather than silently retrying.

### 5. Why are read and mutation retry policies different?

A bounded retry of a safe read can recover a transient network failure. Retrying a mutation may duplicate a change whose response was lost. TanStack Query mutations disable automatic retries; the proposal form's explicit identical retry uses its stable request ID and the backend's idempotence rules.

### 6. How are ingestion credentials different from human JWTs?

Producer keys are scoped to a service, hashed in storage, expiring and revocable. They grant structured log ingestion, not human incident/approval permissions. Human requests instead validate a JWT and fetch the current role from the database.

### 7. Why evaluate detection using receipt time?

Source clocks can be wrong or delayed. Receipt time makes the persisted evaluation window reproducible for accepted events. The UI preserves source timestamps for investigation and explains that they are not the clock used for threshold evaluation.

### 8. Is the recorded threshold count a full traffic measurement?

No. Detection retains bounded witness evidence and a lower-bound matching count at the trigger. The interface therefore presents recorded matches and selected logs, not request rates or an invented production-health score.

### 9. What does idempotent log ingestion mean here?

Reusing a service/event identity does not create another accepted log or independent job. Database uniqueness protects concurrent deliveries too. It does not imply arbitrary clients can omit stable event IDs and still expect semantic deduplication.

### 10. How do deduplication and cooldown differ?

Deduplication correlates new matches with the relevant stored occurrence instead of opening duplicate incidents. Cooldown gates the next incident after resolution according to the rule's recorded policy. They solve different parts of repeated-error handling.

### 11. How does a worker claim work without blocking every other worker?

It uses a short transaction and row-level locking with SKIP LOCKED. The claim stores ownership, attempt and lease information before processing proceeds. Other workers can claim unlocked jobs rather than waiting behind one slow owner.

### 12. Why is a lease alone insufficient?

A slow process can resume after its lease expired. A fencing token must also match the current owner on guarded writes. That prevents an old owner from overwriting a recovered job's newer result.

### 13. What happens if the process dies halfway through a job?

Committed claims survive. Another worker can reclaim expired work within attempt and deadline limits. Effects that belong together use a transaction, and investigation stages reuse their durable checkpoints; ambiguous external model charges remain an explicit limitation.

### 14. Which eight LangGraph stages run?

Incident context, evidence collection, knowledge retrieval, deterministic evidence analysis, hypotheses, recommendations, validation and final report. Only hypotheses and recommendations call the provider when sufficient usable evidence exists. An inconclusive path can finish without inference.

### 15. Why not hold a database session while awaiting Groq?

Network latency and rate limits must not retain transaction locks or idle sessions. The graph reserves a bounded call/budget first, closes the transaction, awaits the provider and then persists usage/validated output through a fresh fenced transaction.

### 16. Does checkpointing guarantee exactly-once model calls?

No. A response can be charged before a crash prevents checkpoint commit. Reservations remain charged, successful checkpoints are reused, and absolute budgets bound retries; the design does not pretend to control an external provider's billing transaction.

### 17. What is RAG in this project?

The application chunks/version-stores controlled runbooks and retrieves relevant supporting text using PostgreSQL full-text queries and service/history relevance. Selected excerpts join the incident evidence in a bounded prompt. There is no vector database or embedding service in the shipped system.

### 18. Why keep immutable runbook versions and chunk identities?

A future edit must not change the source an earlier report cited. Versioned documents, chunk IDs, hashes and offsets let an operator inspect the exact retrieved context. Reindexing creates another retained version instead of rewriting old evidence.

### 19. What does citation validation actually prove?

It proves an item references supplied, persisted, nonquarantined evidence belonging to that investigation. Foreign keys reinforce report citation integrity. It does not prove that a suggested causal explanation follows from the source; independent human verification remains necessary.

### 20. How are prompt injection and secrets handled?

Known secrets, credential patterns and sensitive metadata are screened before snapshots/prompts. Known instruction-like sources are quarantined and excluded from supporting citations. The graph has no model-selected tools and disables external tracing, but conservative heuristics cannot detect every unknown secret or injection formulation.

### 21. Does strict JSON output make a model truthful?

No. GPT-OSS uses constrained JSON-schema generation, followed by local Pydantic validation and separate grounding/safety checks. Schema correctness and semantic correctness are distinct concerns.

### 22. What caused the historical Groq failure?

The persisted record identified a recommendations-stage guard rejection, followed by a generic provider code. Old diagnostics discarded the rejecting condition/output and combined connection failures with rate limits, so their exact historical causes cannot be reconstructed. The fix addresses reproducible negated-certainty false positives and preserves safe diagnostic metadata; it does not invent a historical explanation.

### 23. Which Groq failures are retried?

Transient network/timeouts, documented transient statuses and server failures may retry within the saved limits. Authentication, permissions, bad parameters, explicit request oversize/quota failure and fixed-cap truncation terminate. Retry-After, remaining call/output reservations and the absolute deadline determine whether waiting is useful.

### 24. Why does a successful short SDK probe not prove the graph will succeed?

The graph sends larger evidence/schema prompts in successive calls and validates the response's structure and claims. Account token-per-minute limits, completion caps and validation can fail even though the key/model accepts a tiny request. The prior bounded live verification exercised the complete graph once, not an unlimited availability guarantee.

### 25. Are the input reservations exact token counts?

No. Prompt bytes plus framing are used as a conservative reservation, with a hard size ceiling. Actual provider usage is tracked separately and checked; a calibrated tokenizer is not presented as implemented.

### 26. Why retain both observed evidence and hypotheses in the UI?

Application-computed counts and selected records have a different status from a model's tentative explanation. Separate sections, qualitative confidence, missing evidence, verification steps and explicit provider labels prevent a completed job badge from looking like a proven diagnosis.

### 27. How can a fake report coexist with a configured Groq key?

Provider/model identity is saved on the investigation itself. The UI uses that identity, not global key availability, to label a report. A fake report remains synthetic even if an operator later configures Groq.

### 28. Why can't a recommendation directly become a recovery command?

Generated text is advisory and read-only. A human must choose a fixed catalog action and typed parameters in a separate proposal. The model cannot submit approval, invoke a shell or choose an arbitrary executable endpoint.

### 29. How is approval separation enforced?

Every action needs approval; synthetic mutations require a different authorized human. The backend checks identity/risk/state/version/deadline, so hiding or disabling a frontend button is only an affordance. Administrators do not bypass the distinct-reviewer rule.

### 30. What exactly is immutable after a proposal is created?

The intended action, parameters, source context and approval-relevant snapshot are protected and hashed. Approval records retain the reviewed version/snapshot. Changing the target or incident context can invalidate the authorization rather than silently broadening it.

### 31. Why revalidate approval in the worker?

Queue delay can outlive an approval or make its synthetic-state/incident version stale. The worker must verify the exact authorized snapshot again before applying effects. A successful HTTP approval response alone is not an enduring execution capability.

### 32. Does a successful simulated action resolve the incident?

No. Verification checks an isolated synthetic contract and records before/after fixture state. `incident_resolution_confirmed=false` remains explicit, and a human uses the normal versioned lifecycle endpoint for any later incident transition.

### 33. What makes audit history more than a frontend activity list?

Services append actual events in the relevant transaction, and database triggers reject normal updates/deletes of protected history. The timeline reads those sources; it does not manufacture milestones from the UI's current status. A database owner still has privileges beyond ordinary application authorization, which is a deployment limitation.

### 34. How does timeline pagination handle timestamp ties?

The ordered union retains source type, source ID and event type alongside occurrence time. The cursor carries that stable ordering context and snapshot bound. Fetching the next page therefore does not depend on a timestamp alone or on downloading all history.

### 35. Why keep tokens in memory instead of localStorage?

The browser should not persist a bearer credential across reloads by default. In-memory sessions require reauthentication after refresh, clear private query caches on logout and avoid stale-session responses contaminating a later login. This does not replace XSS protection or create token revocation on the server.

### 36. How do readable service labels avoid lying about identifiers?

The UI uses the real registered name and environment from existing service records, with cached list/detail data and bounded fallback reads. It never guesses a friendly name by stripping a generated suffix. Compact IDs keep the exact full value available for copying and provenance.

### 37. What accessibility work was actually tested?

Field names and help descriptions are separate; native buttons/dialogs retain keyboard behavior and focus restoration. Chromium's accessibility tree checks names on interactive controls, and browser tests check modal Escape/focus, reduced-motion operation and narrow/720px reflow. These are targeted checks, not a complete screen-reader, contrast or WCAG certification.

### 38. Why are the API and database not published to host ports?

The browser needs only the loopback Nginx endpoint. API/database communication uses Docker networks, and an explicit development override supplies the old database host port when needed. An internal service listening on its container interface is not the same as a public host port.

### 39. Why replace forwarded headers at Nginx?

An untrusted client can supply fake forwarding information. The proxy overwrites peer/scheme fields and clears unrelated forwarding headers, while Uvicorn currently does not trust them. An external TLS ingress needs an explicitly reviewed trust configuration rather than a wildcard.

### 40. How are migration startup and drift handled?

One service waits for PostgreSQL, holds an advisory transaction lock, upgrades to the expected head and runs Alembic drift checks. Critical processes verify revision 0004 on launch. Normal startup never resets data or independently migrates from every worker.

### 41. What survives a Compose shutdown?

The named PostgreSQL volume retains accounts, jobs, evidence, reports and audit records. Normal `down` removes containers/networks, not that volume. Consistent native backups and separate-target restore drills are still necessary because a volume is not protection against host/disk loss.

### 42. What does CI verify, and what does it not do?

Four jobs run backend quality/tests, frontend quality/contracts, browser workflows and full container acceptance. They use pinned actions and generated test credentials without production access. They do not publish images, deploy remotely or prove a hosted run passed merely because local equivalents passed.

### 43. How do tests avoid changing existing application data?

Backend/browser fixtures enforce the dedicated loopback test database and own a random migrated schema. Container acceptance owns a different Compose project and named volume. Final restart validation separately compares existing data counts/content fingerprints and preserved configuration/migration files.

### 44. Why add a release audit instead of running git add blindly?

This workspace originally inherited a Git repository rooted in the user's home directory. The staged audit refuses that ancestor root and inspects exact index blobs, private/generated paths and known secret patterns. It supplements, rather than replaces, review of screenshots, rights and unknown/encoded secrets.

### 45. Which trade-offs would you explain rather than conceal?

PostgreSQL jobs simplify atomicity but need capacity/retention planning; lexical RAG is understandable but less semantically flexible than embeddings. Conservative claim guards can reject benign text and valid citations can still support weak reasoning. The local stack lacks managed HTTPS, SQL role separation, token revocation and account-aware distributed abuse controls; those are concrete future operational concerns, not completed features.

## Rehearsal exercises

Trace an incident edit from a React form through the request transaction and its audit row. Explain a recovered claim with a stale owner's token. Follow a known citation to its stored runbook/log version. Demonstrate why self-approval is rejected and why successful synthetic verification leaves the incident investigating. Run the release audit and describe the difference between a local passing check and a GitHub-hosted result.

For every claim, be prepared to show the source, the relevant test and the remaining limitation. Prefer explaining one real failure and its fix over claiming autonomous production remediation.
