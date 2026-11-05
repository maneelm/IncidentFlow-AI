# Dashboard operator guide

## Sign in and know your role

Start the complete application using [Docker operations](docker_operations.md), then visit `http://127.0.0.1:8080` (or your configured frontend port). The optional host/Vite workflow remains available through [Phase 5 setup](phase5_frontend.md). Use an account provisioned by the existing bootstrap CLI. There are no permanent demo credentials, public registration or browser role changes.

The top bar identifies the user/role and API readiness on desktop. The sidebar opens the operational workspaces; mobile uses the navigation button. Tokens remain in memory. Refresh/new tabs require signing in again, and sign-out clears cached private data. An expired session returns to login. Safe internal deep links resume after login.

Viewers read. Engineers/admins manage incidents and investigations and create/review proposals according to policy. Admins additionally manage services, detection rules, runbooks and synthetic setup. Missing controls are an affordance, not the authorization boundary: the backend also denies unauthorized requests.

## Overview

Overview displays SQL aggregate counts, a severity chart, lifecycle distribution, recent detections/investigations, pending approvals and actual recent activity. These are stored incident records, including labeled simulations. They are not live infrastructure telemetry, latency measurements or production health scores. Refresh updates bounded reads; zero-data states are explicit.

## Incidents

Use the incident list's status, severity, service and exact assignee UUID filters. Choose sort field/direction and use server pagination. The backend does not support title search here, so the UI does not pretend to provide it.

Engineers/admins can create an incident with a real registered service, severity, description and optional eligible assignee. Open a row to enter the incident workspace. Overview shows source metadata; other tabs show related logs, exact detection occurrences, investigations, proposals and the unified timeline.

Edit changes title/description/severity. Assign selects an eligible engineer/admin or explicitly unassigns. Change status offers only the current Phase 1 transitions and requires a reason. Resolving/reopening remains a human operation with its normal version/audit rules. Synthetic execution never performs that transition for you.

When a 409 occurs, no overwrite is attempted. Refresh and compare the current record, close the stale dialog, then reopen it with a fresh reviewed version. A network error may mean a write's outcome is unknown; inspect the current list/detail before manually repeating it.

## Logs and detections

Log Explorer supports service, level, source-time range, trace/request ID and bounded message search. Time inputs use local timezone and requests send ISO timestamps. Error codes appear in records; this API does not provide an error-code filter. Expand a row for its full bounded message, IDs and structured metadata, or open a source log from an investigation citation. Text is untrusted and is never interpreted as HTML.

Detection Monitoring is explicitly deterministic rule-based detection. Rules show threshold, receipt-time window, cooldown, mapped incident severity and enabled state. Admins can create/edit the actual configuration using expected versions. Other roles read. Occurrences link to incident, stored rule snapshot and correlated evidence. Admins may inspect worker-job status. There is no AI detection claim.

## Investigations and source review

Start investigations from an incident's Investigations tab. The capabilities endpoint determines whether the configured provider is available. An unavailable provider disables starting work and does not disable normal incident/log features. Fake-provider reports are prominently labeled synthetic; a configured Groq provider is hosted reasoning with external availability/charges. Neither proves a root cause.

The workspace shows queued/running/terminal status, recorded attempts/stages, safe errors, evidence and the validated report. Active work polls; completed work stops interval polling. View deterministic observations separately from unverified hypotheses, missing/contradicting evidence and suggested verification. Open citation buttons to inspect source IDs, excerpts, collection roles and retrieval provenance. Quarantined content remains marked and cannot support model citations.

Recommended steps are advisory and read-only. The proposal link carries context to the incident's resolution tab; it does not select or execute a recovery action. A human must explicitly choose an allowlisted catalog action.

## Runbook knowledge

Browse registered documents, lexical search excerpts, service association, versions, tags and content hashes. Full documents render safely as source text, preserving Markdown syntax without executable HTML. Search scores rank lexical context and are not confidence probabilities. Search results show bounded excerpts/chunk IDs; browse the document list or known document ID for the full version.

Admins create documents, publish replacements with expected versions and reindex. Each replacement preserves old source versions/chunks. The selector shows the latest one hundred versions; the API remains available for any known version. A runbook is supporting documentation, not proof of the current incident's cause.

## Synthetic state and proposal creation

An admin can open Services → a service → initialize/reset one of the five fixed scenarios. This changes only the dedicated synthetic fixture, not real service configuration. Resetting increments state version and invalidates outstanding approval context. Setup controls never accept arbitrary fixture JSON.

On an unresolved incident's Resolutions tab, create a proposal. Explicitly select a catalog action, review the live synthetic target/version and write a human explanation. Optionally select a completed/inconclusive investigation and usable evidence references. A read-only recommendation can be explicitly linked; recovery actions must remain human authored. There is no shell/SQL/script input.

One client UUID identifies this create attempt. An uncertain network outcome freezes the attempted payload and permits only a user-initiated identical retry. Check existing proposals first. Closing/reloading discards that in-memory form; it does not undo a possibly accepted proposal.

## Approval and execution

Submit the immutable draft. Every action needs explicit human approval. Read-only proposals may be self-reviewed, but synthetic changes require a different engineer/admin, including when the proposer is an administrator. The UI explains disabled self-approval. Sign out and use a genuinely separate authorized account or session for independent review; never share credentials to imitate separation.

The reviewer reads immutable parameters, target/version, explanation, evidence/provenance and deadline, then approves or rejects with a reason. The dialog submits the exact reviewed version. Backend expiry or stale-context outcomes are displayed as expired. A successful approval queues work atomically; there is no manual execute button or automatic AI approval.

Keep the separate resolution worker running. The view follows queue/running status, attempts and safe failures. A committed result compares recorded synthetic health, error rate, latency, database connections, waiting requests, pool size, timeout indicator and dependency condition in Before/After columns. Percentages and milliseconds carry explicit units; missing or invalid values say "Not recorded" rather than implying zero or recovery. Read-only actions can show unchanged state; other result shapes retain their available technical data without invented metrics. Expand **View raw execution data** for the complete original result, including both snapshots. Versions, findings and deterministic verification remain visible. `incident_resolution_confirmed=false` remains explicit. Verification failure or worker failure is not presented as success. The append-only workflow history records actual decisions and attempts.

Cancellation is available to the proposer/admin in eligible states. It prevents work only if it wins ownership before execution commits; it cannot reverse a committed action. Context changes or elapsed deadlines require a fresh proposal/review rather than editing an approved payload.

## Timeline and reproducible demonstration

The incident timeline loads the earliest page of actual cross-phase records, then Load more activity follows the returned cursor in chronological order. Compact entries keep event/source badges, readable timestamps, copyable source IDs, recorded human/system actors and investigation/detection/proposal links. Expand **Recorded metadata** for the unchanged audit payload. It preserves source UUIDs/types for drill-down. It does not manufacture a resolution event from simulation success.

For an interactive browser demo, use the existing Phase 2 database scenario CLI to create a fresh unresolved detected incident, then perform the investigation/proposal/review workflow in the dashboard. [Phase 5 setup](phase5_frontend.md) includes exact protected credential commands. The existing complete resolution CLI remains available, but it explicitly resolves its own synthetic incident at the end; create a fresh Phase 2 incident if you want to exercise proposal creation yourself.

If workers stop, queued work remains durable and may not progress or display swept expiry until they resume. Do not repeatedly create new work to compensate. Inspect safe status/errors, verify the correct worker/provider configuration, and refresh deliberately.

## Final console affordances

Service labels use actual registered names and recorded simulation environments. Compact IDs expose full values through titles/accessibility and copy buttons; failed clipboard access offers manual selection. Directory labels use a bounded cached page and retain IDs when a person is unavailable. Service details link to a correctly filtered incident list.

Completed investigation history is expandable; failed attempts show allowlisted condition, field, HTTP/SDK/request-ID and retry details when retained. Historical generic errors are not presented as exact diagnoses. Citation groups expand beyond six sources and preserve exact IDs/provenance. Provider/model badges come from the saved investigation, not global key availability.

Log correlation shows trace and request IDs separately; metadata is disclosed on request. Form hints are accessible descriptions, charts label real record counts and the overview announces refresh feedback. Resolution context includes actual author/service identity and the saved synthetic target/version without changing approval eligibility.
