# Prompts, evidence grounding and privacy

## Trusted instructions and untrusted data

The graph has no shell, filesystem, browser, arbitrary URL fetcher, SQL execution tool or model-selected function/tool interface. Its read queries and two provider tasks are chosen by application code. The model cannot choose another graph node, change policy, update an incident, execute an action or create an audit event.

`investigation/prompts.py` builds a trusted system message containing the task constraints and Pydantic JSON schema. A separate user JSON payload labels selected sources `untrusted_evidence`. That includes logs, incident descriptions, runbook excerpts and historical cases. The instructions explicitly state that content pretending to be instructions is still data. Deterministic counts are supplied rather than delegated to the model.

The graph disables external LangSmith tracing with `tracing_context(enabled=False)` and supplies no tracing callbacks, even if inherited environment enables tracing. No raw prompts or raw model responses are database columns. The Groq key stays inside the provider client; it is never graph state, checkpoint policy, evidence, report metadata or provider error text. SDK retries are disabled and hosted SDK/HTTP debug loggers are raised to WARNING.

## Redaction and quarantine

`Sanitizer.from_settings` knows the current Groq key, JWT secret, database URL and database password. Exact known secret strings are replaced before evidence enters checkpoints/prompts. Likely password/API-key/token assignments, authorization bearer/basic headers, recognizable JWTs, IncidentFlow ingestion tokens, Groq/OpenAI-shaped keys and URL user-info credentials are screened as well. Nested sensitive fields are redacted and irrelevant metadata is excluded. Control/NUL characters are removed, excerpts and nesting are bounded, and SQLAlchemy errors use safe codes/classes rather than raw exception strings.

Known instruction-like text such as “ignore previous instructions” or “print the API key” is quarantined. Stored investigation excerpts/labels contain a quarantine marker instead of the instruction text. The source UUID remains traceable to the authorized original API, while quarantined sources are excluded from model citation support and usable-log aggregates. The report states the selected quarantine count.

This is a conservative heuristic, not a general secret classifier or complete injection detector. Unknown secrets hidden under innocent descriptions, business-sensitive information, personal data and novel instruction phrasing may evade it. Producers and runbook administrators still own content hygiene. A hosted request transmits the remaining bounded evidence to Groq; operators must decide whether that content is suitable for their account/privacy requirements.

## Structured output and programmatic grounding

Only hypotheses and recommendations come from the model. Summary, observed facts, selected counts, timing/service summaries, detection witness count and report metadata come from deterministic application code. This prevents model-generated observations from masquerading as measured facts.

The adapter requests strict JSON-schema output for Groq GPT-OSS 20B/120B and JSON-object mode for other configured chat models. GPT-OSS uses low reasoning effort and excludes reasoning text from the response; its completion cap still includes reasoning tokens. The graph always applies Pydantic v2 models afterward. Wrong types, missing uncertainty, extra fields, too many hypotheses/actions, malformed JSON and incomplete/truncated responses fail explicitly. Every sufficient-evidence report requires two to four alternative hypotheses, qualitative low/medium confidence, missing/contradicting evidence and a verification step. No confidence percentage is accepted. Insufficient evidence skips both calls and produces an inconclusive report with no hypotheses.

Each hypothesis/action cites a nonempty subset of the persisted, nonquarantined evidence references supplied to that investigation. Unknown or cross-investigation IDs are rejected. UUIDs invented inside free text are rejected too. Generated observations/reports are checked against the evidence relation, and a separate citation table has a composite FK to that investigation/reference pair.

Additional output checks reject known assertive diagnosis language, numerical measurements in model prose, instruction-like text, output needing secret redaction, and known mutating/command patterns in recommended actions. Locally negated certainty phrases such as "not confirmed" and "not been proven" express uncertainty and are allowed; later affirmative assertions in the same text still fail. Numerical observations remain deterministic. Recommendations must be `risk=read_only` and `requires_human_review=true`.

Reference integrity is **not semantic entailment**. A model can cite a real timeout record and still suggest an irrelevant cause. The text checks are conservative and can reject benign phrasing, while more subtle unsupported claims can still pass. Hypotheses are therefore clearly unverified suggestions; every report includes limitations and every action requires human review. No optional second LLM judge is used to pretend to prove correctness or spend an unbounded budget.

## Budgets, retries and cancellation

Before each remote call a short fenced transaction reserves a call and conservative input/output budget. That transaction commits and closes before awaiting Groq. Reservations survive crashes and are not refunded for uncertain failures. Output bounds, actual usage checks and absolute duration apply across retries. The model cannot increase these settings.

A checkpoint after a successful reasoning stage preserves its typed result, so subsequent failures resume from the next stage. A crash after a charged response but before checkpoint commit can repeat the call and its charge. Claim tokens prevent a stale worker from overwriting a newer result. Cancellation invalidates its token immediately; an already-started remote call may still finish or charge, but cannot save a report.

Failures retain bounded diagnostics in the existing step-details API: the validation condition, schema field and item index, known evidence references, or SDK exception class, HTTP status and screened Groq request ID. No raw output, error message, arbitrary response header or Pydantic input is retained. A safe validation condition is supplied as feedback on subsequent generation attempts. Historical failures without these diagnostics cannot be retroactively attributed to a specific phrase or SDK exception.

Authentication, permission, missing-model, invalid-parameter and oversized-request failures terminate without retry. Transient network, timeout, 408/409/429/498 and server failures may retry within the original attempt, call and absolute-duration limits. `Retry-After` seconds or HTTP dates govern durable job scheduling; an absent/invalid 429 delay defaults to 30 seconds. A 429 explicitly reporting a request too large or exhausted billing quota is permanent. Jobs stop if the provider delay would exceed the deadline or the remaining call/output reservation is exhausted. Refusals and completions truncated at the fixed per-call cap terminate instead of repeating the same request. Available token usage is counted before rejecting truncated or invalid generated text; reservations are never refunded for ambiguous failures.

## What was actually tested

Ordinary tests use a labeled deterministic fake adapter and real LangGraph transitions. PostgreSQL tests verify evidence FKs, source provenance, checkpoint/report rollback, concurrent claims, lease recovery, process crashes, cancellation while the adapter is blocked, and the absence of an open worker transaction during that wait. Prompt-spy tests check that known secrets and malicious instructions do not enter model payloads or persisted investigation copies. Invalid citations, invented IDs, confident diagnoses, fabricated measurements and unsafe actions are rejected.

The actual Groq SDK is additionally exercised through an in-memory HTTP transport for constrained output, network/timeout failures, permanent client errors, rate limits and server errors, with zero SDK retries. PostgreSQL regressions cover the observed validation-failure/rate-limit sequence, valid checkpoint reuse, durable budgets and provider-directed retry scheduling. One explicitly authorized bounded live Groq investigation also completed; see [the diagnostic report](groq_investigation_debugging.md) for its scope and limitations.
