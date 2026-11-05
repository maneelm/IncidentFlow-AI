# Approval and execution security

## Authority and separation of duties

Authentication is the existing JWT flow. The current role is read from PostgreSQL; no request field can choose an approver or system identity. Ingestion keys are unrelated. Every action, including read-only inspection, requires an explicit human approval decision.

| Operation | Viewer | Engineer | Admin |
| --- | --- | --- | --- |
| Read catalog/state/proposals/decisions/results/history/timeline | Yes | Yes | Yes |
| Create a proposal | No | Yes | Yes |
| Submit/cancel own nonterminal proposal | No | Yes | Yes |
| Submit/cancel another person's proposal | No | No | Yes |
| Review read-only proposal, including own | No | Yes | Yes |
| Review synthetic mutation | No | Yes, different user | Yes, different user |
| Initialize/reset a scenario | No | No | Yes |
| Directly execute tools or edit approved payload | No | No | No |

The second-person rule has no administrator override. It applies to rejection as well as approval of mutating proposals. Trusted SystemActor identities cannot call human approval services. Worker events identify `resolution_worker`; approval events retain the real human UUID.

## Immutable authorization contract

A proposal is immutable from creation. Its action ID/definition fingerprint, incident/service/state/version, parameters, explanation/origin, provenance, requester, policy and client UUID are hashed into a canonical payload. The approval stores that exact snapshot/hash. A database trigger prohibits editing protected proposal fields, including draft payloads, and prevents extending an established approval deadline. Material changes require a new proposal with a new client UUID.

Report-origin proposals can only select read-only catalog actions. The user must explicitly select a real recommendation and matching evidence; the application never parses prose into an executable operation. Recovery actions require `origin=human` with a human explanation and separate reviewer, even when linked to an AI report. The Phase 3 RecommendedAction schema remains unchanged: read-only and human review required.

The API checks investigation ownership/completion, report provenance, source membership and quarantine status. Resolution evidence has actual report/source FKs, and an insertion trigger checks it matches the proposal's investigation. Original report payloads are not modified. Human-only proposals without an investigation are permitted, with no invented AI provenance.

## Revalidation and concurrency

Create uses schema-scoped advisory admission locking and actor/client uniqueness. Same-key exact replay returns the same proposal; differing content fails. Important transitions require expected versions, lock the proposal and record one new version/event per transition. Simultaneous approvals yield one decision/job and a conflicting second response.

Worker claims use SKIP LOCKED, tokens, leases and attempt bounds. Execution checks real human approval, current reviewer eligibility, snapshot equality/hash, catalog fingerprint, incident version/status/service, synthetic-state identity/version, report hash and usable evidence. The deadline is checked before work and again before completion. Stale tokens cannot write failure/results or synthetic effects.

Synthetic effects, unique execution receipt, verification, terminal job/proposal states and audit events share one PostgreSQL transaction. An exception or process death before commit rolls them all back; recovery can retry safely. After commit, a process can die before acknowledging success, but the terminal job/unique result prevent another application. A second approved proposal targeting an already-changed state expires. No cross-system exactly-once claim is made.

The lock order for existing jobs is job → proposal → incident → synthetic state. Approval has no job until it atomically queues one. Scenario initialization serializes through the service/state rows; resets increment versions even when restoring identical data. Cancellation races are resolved by database ownership, not a frontend button.

## Database and audit protections

Resolution events have one human/system actor, event-type/size/version checks, proposal FK and unique proposal/version. Approval snapshots, execution receipts and evidence links also have FKs/size/uniqueness constraints. UPDATE, DELETE and TRUNCATE triggers protect resolution events, approvals, executions and evidence. Approval insertion validates the current human role, pending state, deadline, payload hash and separation of duties. Job insertion requires the matching approved decision and queued live proposal.

These are defenses against application bugs and ordinary direct writes. A database owner can disable/drop triggers or alter tables. This is not cryptographic tamper evidence. Development Compose uses an owner role; production least-privilege deployment remains out of scope.

Approval and event writes are atomic. Failed execution attempts are visible through job counters and safe start/retry/failure events, while a committed result stores before/after versions and explicit synthetic verification. Human incident lifecycle history stays distinct; the timeline provides a read-only union of existing records.

## Command and data boundaries

The literal action registry is the entire dispatcher. Action parameters accept only state UUID and positive expected version; extra command/script/URL/callable fields fail validation. No request chooses an import, callable, SQL string or filesystem path. The runtime simulator contains no subprocess, network, filesystem or cloud administration operation. It computes bounded synthetic snapshots in memory, which trusted persistence code applies only to the dedicated table.

Explanation/review text is descriptive, bounded and passed through the existing secret redactor/injection quarantine before storage. Raw provider prompts do not enter workflow tables. Safe error codes and exception class names replace exception bodies in worker logs. Pattern screening can miss business-sensitive text or unknown secret formats, so humans should still avoid submitting them.

There is no direct execute endpoint and no LLM action tool. Successful simulation never updates the incident or confirms real recovery. Only a separate authenticated human request through the original versioned transition API can resolve it.

## Limits

Policies are fixed in trusted code and bounded configuration, snapshotted per proposal; no policy-management UI/API is implemented. There is no team/tenant ACL beyond the existing global roles, no approval delegation, multi-approver quorum, real infrastructure action, compensation engine, automatic retention or production deployment. Conservative context invalidation can require repeated approval after unrelated incident edits. Pending expiry status is asynchronous while the worker is stopped, and synthetic setup/reset history is represented by current state version/initializer rather than a separate setup-event ledger.

See [workflow operations](resolution_workflows.md) for exact statuses, cancellation semantics, commands and error responses, and [Phase 4 validation](phase4_implementation.md) for actual test/live evidence.
