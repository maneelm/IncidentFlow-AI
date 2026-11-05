# Detection rules, correlation and worker semantics

## Rule contract

Admins create rules with POST `/api/v1/detection-rules`, read them with GET, and change/enable/disable them with PATCH `/{id}`. Engineers/viewers may only read. Rules are retained rather than hard-deleted, because detection history refers to them.

```json
{
  "service_id": "00000000-0000-0000-0000-000000000001",
  "name": "Payment Database Failures",
  "enabled": true,
  "level": "error",
  "error_code": "DB_CONNECTION_TIMEOUT",
  "threshold": 5,
  "window_seconds": 60,
  "incident_severity": "high",
  "cooldown_seconds": 60
}
```

Rules have UUID identity, optimistic `version`, creation/update times and an `effective_from` receipt-time boundary. There are at most **25 rules per service**, including disabled rules. Service UUID is immutable. Names are unique within a service. Level matching is exact, not “at least this severity”; a rule for error does not match warning or critical. A null/omitted error code matches any code; non-null codes match exactly after uppercase normalization.

Thresholds accept 1–1000; windows 5–3600 seconds; cooldowns 0–86400 seconds. PATCH requires `expected_version` and at least one change. Only `error_code` may explicitly be null. Configuration changes or reactivation reset `effective_from`, so historical accepted logs and older queued jobs are not backfilled. Renaming alone does not reset the boundary. Disabled rules ignore work; already-created incidents remain under operator control.

Criteria/severity/cooldown changes are rejected with `409 active_detection` while the current correlated incident is triggered/investigating/mitigated. Disable a rule, resolve its incident, edit it, then re-enable if changing live criteria is necessary. Name and enabled status can change while an incident is active. Rule version can also advance when the worker saves a post-resolution eligibility boundary; always fetch its latest value before an admin edit.

## Time semantics and numerical examples

Detection deliberately uses **server ingestion time** (`ingested_at`), not source `timestamp`, worker start time or “now” when a backlog drains. A whole accepted batch receives one PostgreSQL wall-clock timestamp. Source timestamps are independently validated and remain available for forensic queries.

For each matching queued log with receipt time **t**, the count window is **(t − W, t]**, further restricted to receipts strictly after `effective_from` and any post-resolution cooldown boundary. The lower endpoint is exclusive and the upper endpoint inclusive.

For a 60-second window and threshold 5:

- Receipts at 12:00:01, :10, :20, :30 and :59 qualify at 12:00:59.
- At 12:01:01, the receipt at 12:00:01 is exactly on the lower edge and does not count.
- Four matching receipts never trigger; threshold 1 deliberately allows one.
- Five logs accepted in one batch count together at that batch's receipt time, even if only the first job is currently executing.
- A worker processing that batch ten minutes later still evaluates its stored receipt-time window, not an empty window ending ten minutes later.

Late/out-of-order source events are admitted within the configured source-time tolerance and counted at delivery time. A log reporting an error 23 hours ago delivered now contributes to the current receipt window. This avoids retroactive window revision and watermarks, but cannot distinguish a recent failure burst from a delayed producer flush. Producer clock skew up to the future tolerance is accepted and does not shift the detection window. These are explicit trade-offs, not an event-time stream processor.

Receipts for one service serialize through a service row lock and committed job positions. Only that service's earliest unfinished job is claimable. Given unchanged rules, lifecycle decisions and the same committed receipt dataset, evaluation order and threshold decisions are repeatable across workers. Rule edits and manual resolution are additional inputs to the outcome; changing their timing can change which receipts remain eligible. An eventual failed job can be explicitly retried; historical requeues are repeatable but do not backfill a new rule epoch or bypass lifecycle gates.

The query returns at most `threshold` matching log UUIDs from the indexed receipt-time range. `matching_count` is the **threshold witness count**, a lower bound (“at least N”), not the total count of all errors in the window. This caps initial evidence work at 1000 records per trigger. Later matching jobs add their own evidence with conflict-safe inserts.

## Deduplication and incident lifecycle

The stable key is **service UUID + rule UUID**. Trace ID, error-code dimensions beyond the rule's own filter, deployment version and arbitrary metadata grouping are not additional keys in Phase 2. Two independent rules can legitimately create separate incidents from overlapping logs.

A `detection_occurrences` row links a rule, incident, rule version/snapshot, initial window, threshold witness count and retirement time. A partial unique index allows one `is_current=true` occurrence per rule. `is_current` is a correlation reservation, not a second incident lifecycle status. A resolved incident may retain the reservation until the next matching job retires it. Read the associated incident's status for lifecycle truth.

Repeated matching logs correlate to the current incident while it is triggered, investigating or mitigated. Initial threshold witnesses and later matching logs received after the initial window's lower bound become evidence; duplicate association inserts do nothing. Adding evidence does not alter an incident's status/version and creates no misleading incident mutation event. The original created event remains immutable.

The worker locks the rule before checking correlation and locks the incident while inspecting its lifecycle. It never silently resolves, closes, assigns or reopens an incident. If an operator resolves/closes the current incident, the next matching job retires that reservation and saves:

```text
eligible_after = terminal timestamp + cooldown_seconds
```

The terminal timestamp is closing time when closed, otherwise resolution time. A new occurrence requires a fresh threshold strictly after that boundary. Receipts during cooldown and old queued receipts do not count toward the next incident. Cooldown zero means fresh post-terminal receipts may immediately begin a new threshold. The system creates a new triggered incident; it does not reuse/reopen the terminal one.

Operator reopening is still the Phase 1 audited workflow. Reopening an incident that still owns the current reservation resumes correlation to it. Reopening a historical incident after a newer occurrence owns the reservation does not take that reservation back or merge incidents. An operator can therefore manually have historical and current incidents active; the detector itself never creates a second current occurrence for one rule.

## Reliable background work

`detection_jobs` has one row per accepted log, a monotonically allocated position, status, attempts, lifetime total attempts, explicit requeue count, availability time, lease token/expiry and sanitized last error code. Statuses are pending, processing, completed and failed.

The claim transaction:

1. Finds a ready pending job or an expired processing lease, excluding jobs with earlier unfinished work for the same service.
2. Uses `FOR UPDATE SKIP LOCKED` to let concurrent workers claim other services without waiting on a locked job.
3. Increments bounded attempts, generates a new fencing token and stores a lease, then commits promptly.

The processing transaction:

1. Locks/rechecks that job and its token. A stale owner exits without effects.
2. Sets a PostgreSQL statement timeout and runs inside an overall asyncio timeout.
3. Locks eligible rules, evaluates bounded witness queries, correlates evidence or invokes `IncidentService.create` with `SystemActor("detection_worker")`.
4. Commits incident + audit + detection + evidence + completed job together.

If processing or COMMIT fails, that transaction rolls back. A separate fenced failure transaction records a safe error code and schedules another attempt. Backoff is `min(60, WORKER_BACKOFF_SECONDS × 2^(attempt−1))`; default retries wait 2, 4, 8, 16 seconds before the fifth/final attempt. At the attempt limit, the job becomes failed. An expired final lease becomes failed with `lease_expired`; it cannot disappear or block its service forever. Failed/completed jobs do not block later positions.

If the process crashes after claiming, the durable processing row remains. After its lease expires another worker reclaims it with a new token. If a live processor still holds the job row lock, SKIP LOCKED will not steal it even if the lease timestamp passes. If effects already committed, job completion committed with them, so the job is no longer eligible. Ambiguous commit outcomes are safely rechecked by token/status. This is **at-least-once claim/attempt delivery with transactional, idempotent effects**, not a claim of exactly-once execution.

No transaction remains open while the worker sleeps. Default settings: batch 10, poll 1 second, lease 60 seconds, processing timeout 10 seconds, max attempts 5, base backoff 2 seconds. Lease must exceed processing timeout by at least five seconds. Batch accepts 1–100; attempts 1–10. Max attempts is captured when each job is created; changing configuration does not silently rewrite existing jobs.

Ctrl+C/SIGTERM sets a stop flag; the worker finishes its current bounded attempt, stops claiming and disposes the engine. A forced kill is recovered through leases. `--once` makes a bounded number of attempts and exits; logs report processing failures, and the admin API is the durable source of job status. It does not wait for future retries.

## Failed-job operations

Only admins may GET `/api/v1/detection-jobs`, GET `/{id}`, or POST `/{id}/retry`. Filter listing by service and/or status, with ordinary bounded pagination. Lease tokens and raw exceptions are never returned. Failure codes include `constraint_error`, `database_error`, `processing_timeout`, `processing_error` and `lease_expired`.

After fixing the cause, retry a failed job explicitly. The action resets that run's attempt count, retains `total_attempts`, increments `requeues`, and makes it pending now. Retrying a nonfailed job is a 409. No automatic infinite retries or automatic destructive remediation occur. Monitor failed jobs: a final failure can prevent that log's own evaluation until an operator retries it, although subsequent valid receipts may also satisfy a window.

## Limits and alternatives

One in-flight job per service preserves deterministic receipt ordering and simplifies reasoning, but limits hot-service throughput and allows a retry backoff to pause that service's later jobs. Other services continue. No Redis/Celery infrastructure, leases heartbeats, event-time watermarking, scheduler for backfills, retention partitioning, dead-letter archive outside PostgreSQL, or alert notifications are included. Database indexes and bounded queries support a local production-inspired workload; no unmeasured load-capacity claim is made.
