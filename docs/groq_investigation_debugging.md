# Groq investigation failure analysis

## Historical evidence and limits

The original persisted Groq investigation stopped at `recommendations`:

| Attempt | Recorded failure | Calls reserved cumulatively |
| --- | --- | --- |
| 1 | `unsupported_model_claim` | 2: hypotheses and recommendations |
| 2 | `provider_unavailable` | 3: recommendations only |
| 3 | `provider_unavailable` | 4: recommendations only |

The checkpoint contains completed context, evidence, knowledge, analysis and hypotheses, but no accepted actions. Its saved hypotheses still validate. There are 17 persisted evidence sources and none are quarantined. No historical record was modified by this work.

`unsupported_model_claim` originated in `investigation/analysis.py:validate_generation`, after JSON parsing and Pydantic validation. Unknown citation membership has its own `invalid_evidence_reference` code, so that membership check passed. The possible rejecting conditions were an unknown UUID in free text, assertive language/percentages, a numerical measurement, instruction-like output, or text requiring redaction. The old step contained only the error code and timestamp: no rejecting field, condition, cited action references or generated output was retained. **The exact historical condition, involved citations and genuine-claim-versus-false-positive classification cannot be established.** The 17 available source references are not evidence of which references the rejected action actually cited.

A reproducible false positive exists independently: the old regex matches `confirmed` in "The cause is not confirmed" and `proven` in "A causal link has not been proven." It also matches `root cause is` in "The root cause is not confirmed." The new check exempts local negated certainty and explicitly uncertain cause phrases; affirmative claims elsewhere in the text still fail. This is a verified validator defect, not a claim that those phrases appeared in the discarded historical response.

`provider_unavailable` combined `groq.APIConnectionError` and `groq.RateLimitError` in one catch block. The latter means HTTP 429; a connection failure can have no HTTP response at all. `APITimeoutError` was caught separately as `provider_timeout`, and other HTTP errors used `provider_request_failed`. Missing configuration also used `provider_unavailable`, but configuration was unchanged and earlier calls succeeded. The original SDK exception class, HTTP status, request IDs and headers were discarded. **The underlying cause of attempts 2/3 cannot be established retrospectively.** Fast retries after two substantial successful requests are consistent with rate limiting, but do not prove it.

The original run reserved 38,887 input tokens conservatively and 8,192 output tokens, against limits of 65,536 and 8,192. Recorded usage from the two returned completions was 6,011 input and 2,125 completion tokens. All four allowed calls were reserved; the last two had no recorded usage. Reservations count unsuccessful/ambiguous calls and are not refunded. The stored error was not budget exhaustion. The retry gaps were approximately two and four seconds and ignored provider retry headers.

## Changes

- `backend/app/investigation/analysis.py`: local negation handling; separate diagnostic condition/field/item and known citation IDs; case-insensitive UUID matching; Pydantic error counts without raw input or extra keys.
- `backend/app/investigation/errors.py`: bounded diagnostic metadata and optional provider-directed retry delay.
- `backend/app/investigation/providers.py`: distinct SDK/network/HTTP failure classification, screened `x-request-id`, bounded numeric/HTTP-date `Retry-After`, permanent oversized/quota failures, strict GPT-OSS JSON schema and low reasoning effort. Other chat models retain JSON-object mode; SDK retries stay disabled.
- `backend/app/investigation/graph.py`: safe validation feedback, prompt/output/usage diagnostics, and usage accounting before rejecting truncated output. Pydantic and citation validation remain mandatory.
- `backend/app/investigation/prompts.py`: concise alternatives/checks, deterministic numerical observations, and bounded validation feedback.
- `backend/app/workers/investigation_worker.py`: persist safe failure details through the existing API; schedule transient retries after the provider delay; stop permanent, exhausted-budget and out-of-deadline retries; preserve valid stage checkpoints.
- `backend/tests/phase3/test_groq_adapter.py`, `test_groq_failures.py`, `test_groq_retry.py`: SDK transport, validator, graph and PostgreSQL regressions. The replay reproduces the observed error sequence with representative test output; it does not reconstruct the discarded historical response.
- Documentation describes diagnostic limits and the live verification. No migration, dependency, public schema or frontend source changes are required.

## Structured output, sizes and safety

Prompts still have a 32,768-byte ceiling, a conservative UTF-8 byte-based input reservation with framing, a 2,048-token per-call completion cap, a 16,384-byte response ceiling and cumulative persisted budgets. Byte reservations are not calibrated model-token estimates. GPT-OSS has a much larger model context window, but account token-per-minute limits are separate and can be much smaller. A short direct completion does not exercise either full investigation prompt sizes or successive calls. See Groq's [model limits](https://console.groq.com/docs/models), [rate limits and retry headers](https://console.groq.com/docs/rate-limits), [strict structured output support](https://console.groq.com/docs/structured-outputs), [reasoning controls](https://console.groq.com/docs/reasoning) and [SDK exception handling](https://github.com/groq/groq-python#handling-errors).

Strict schema decoding enforces shape, not truth. Grounding still requires real, nonquarantined citations; invented UUIDs, affirmative certainty, generated measurements/percentages, output needing redaction, injection-like text and mutating recommendations fail. Redaction, quarantine, disabled external tracing, read-only tools policy, final citation foreign keys and human review remain in effect. Valid references do not establish semantic entailment. The remaining language checks are conservative heuristics and may still reject benign wording or miss subtle unsupported claims.

## One bounded live verification

On 2026-11-03, the existing local key was used without displaying it. A new investigation was created through the existing service for the original incident and its original requesting writer. It ran the production LangGraph, provider adapter and database workflow with one worker attempt, two maximum calls, 4,096 total reserved output tokens and a 90-second absolute deadline. No additional direct probe or live retry was made.

Investigation: `eb4a6313-58d5-4c4c-85da-756f5ea55484` (dashboard route `/investigations/eb4a6313-58d5-4c4c-85da-756f5ea55484`).

| Stage | HTTP status | Input tokens | Completion tokens | Finish reason |
| --- | --- | --- | --- | --- |
| Hypotheses | 200 | 3,094 | 785 | stop |
| Recommendations | 200 | 3,638 | 433 | stop |

The live result completed all eight stages and saved a report with two hypotheses and two read-only recommendations grounded in the 17 selected sources. Both citations and final report validation passed. It reserved 19,420 input and 4,096 output tokens; recorded usage was 6,732 input and 1,218 completion tokens. Incident version, timestamp, status and assignee were unchanged. Historical failed rows were preserved.

Safe request IDs: `req_01m6g48931ezvtwpd54yp11pxw` and `req_01m6g48btbehgb8c8wp7n33fgd`. These identify only the new successful verification requests, not the historical failures. This single successful run does not establish future account capacity or historical error causes.

## Validation

- Latest focused Phase 3 suite: **98 passed in 192.26 seconds**, including actual SDK requests against mocked HTTP, real LangGraph execution and migrated PostgreSQL schemas.
- Latest complete backend unit selection (`-m 'not integration'`): **124 passed, 318 deselected in 2.10 seconds**.
- Ruff: `check backend scripts` and `format --check backend scripts` passed; 136 files already formatted.
- Frontend: TypeScript, ESLint, **49 unit tests**, production build and OpenAPI contract validation passed.
- Playwright Chromium with the real API, separate workers and migrated test PostgreSQL: **5 passed in 22.2 seconds**. These browser workflows use the explicit fake investigation adapter and make no additional hosted calls.
- Full backend regression: **436 passed in 777.26 seconds**, covering Phases 1–5, real PostgreSQL, migrations, APIs and workers. This run was collected before the final six guard cases were added; the latest 124-unit and 98-Phase-3 runs above cover those cases and the final local-negation refinement. The latest collected suite therefore contains 442 unique tests; these overlapping runs are not added together as independent tests.
