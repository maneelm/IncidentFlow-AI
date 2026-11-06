# CI verification

`.github/workflows/ci.yml` runs on push, pull request and manual dispatch with read-only repository permissions, no persisted checkout credentials, cancellation of superseded runs, and bounded job timeouts. Third-party actions are pinned to reviewed commit SHAs. Python 3.12, uv 0.11.28 and Node 22.23.4 match the local/container contracts. Lockfiles are mandatory; CI does not regenerate them to conceal a mismatch.

| Job | Actual checks |
| --- | --- |
| python (25 minutes) | `uv sync --locked`, Ruff lint/format, generated private test settings, isolated PostgreSQL 17 test service, publication audit, complete pytest suite; test service stopped even on failure |
| frontend (15 minutes) | `npm ci`, TypeScript, ESLint, component/API tests, production build, generated OpenAPI contract comparison using locked backend dependencies |
| browser (15 minutes) | Chromium installation with Linux dependencies, real FastAPI/workers, per-run migrated schema in the dedicated test database, all six Playwright workflows, preserving the original five; no hosted model |
| containers (25 minutes) | Both image builds, quiet Compose validation, complete six-service startup/migration, six browser workflows through Nginx, real ingestion/detection/fake investigation/two-person synthetic execution, worker crash recovery, restart/persistence, native backup/restore, absent-key behavior, production fake rejection, non-root and image-layer secret checks |

The jobs run on separate `ubuntu-24.04` GitHub-hosted runners, so their fixed test port/project names do not collide. Test `.env` files contain newly generated values and never production credentials. `GROQ_API_KEY` is empty; the investigation adapter is explicitly fake in browser/container fixtures. The backend adapter tests use the real SDK over an in-memory HTTP transport. No raw environment dumps, resolved Compose configuration, auth headers, provider messages, traces or credential-bearing fixture JSON are uploaded.

Each backend/browser test owns a random migrated schema. Fixtures validate that the target is `incidentflow_test` at loopback port 55433 under its dedicated user, and clean only their owned schema. The container verifier uses its own generated project and separate named volume; it never resets the default `incidentflow_postgres_data`. It first refuses any existing resources in its generated namespace. Final cleanup stops only that invocation's project, verifies the exact temporary volume name and Compose ownership labels before removing it, checks for remaining containers/networks, and removes its generated credential file. Shutdown or ownership-check failures remain failed checks; they never trigger broad volume deletion. Browser screenshots are ignored local artifacts, not automatic public uploads.

## Run the same checks locally

See [PowerShell operations](docker_operations.md) for exact backend/frontend/database commands. After building both images and installing Chromium:

```powershell
docker compose config --quiet
docker compose build api frontend
uv run --locked python scripts/verify_containers.py
```

Local workflow syntax/semantics were checked with the pinned actionlint image. This mounts only workflow files, read-only, without a network or Docker socket; it does not expose `.env` to the checker:

```powershell
$workflowPath = (Resolve-Path .github/workflows).Path
docker run --rm --network none --read-only --mount "type=bind,source=$workflowPath,target=/work,readonly" --workdir /work rhysd/actionlint:1.7.7 -shellcheck= -pyflakes= ci.yml
```

The optional actionlint shell/Python integrations are disabled in this command; Ruff and actual execution validate Python, while GitHub runs execute the shell steps with failure propagation. Local validation is not a GitHub-hosted run. No workflow has been reported as passing on GitHub without an actual run there.

## Delivery boundary

There is no registry login, image push, production credential, automatic deployment, remote SSH, Kubernetes cluster or release promotion. The pipeline establishes a repeatable verification boundary for a future explicitly configured delivery stage. Pin updates require review and rerunning the checks; version tags/digests are not an evergreen vulnerability guarantee. For deployment outside loopback, add reviewed HTTPS ingress, secret management, monitoring, backups and rollback ownership before any delivery automation.

Reference behavior: [GitHub Actions security](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions), [uv lock synchronization](https://docs.astral.sh/uv/concepts/projects/sync/), [Compose startup ordering](https://docs.docker.com/compose/how-tos/startup-order/).

The Python job also runs `scripts/check_release.py` against publication source. It screens known runtime-secret literals and credential patterns, private/generated paths, local Markdown links and the secret template. It does not claim to detect every obfuscated secret or validate external links. The new portfolio browser workflow adds targeted accessibility and full synthetic approval coverage; screenshot writing remains opt-in. See [publication/root safety](phase7_finalization.md#publication-audit-and-git-root-safety) before staging locally.

## Container failure diagnostics and the first hosted run

The [first hosted run](https://github.com/maneelm/IncidentFlow-AI/actions/runs/42150669530) at commit `31cc0314971ba6955ef704b05f0b88c8e34c0eee` passed Python, frontend and browser jobs, but its container step reported only `AssertionError` after the initial startup checkpoint. The downloaded job log contains no assertion location or captured Playwright failure. That historical assertion cannot be recovered from the saved log.

On November 6, 2026, the original verifier passed locally with both Windows Chromium and Ubuntu Chromium against fresh isolated stacks. These results do not establish the cause of the hosted failure or prove a timing/platform hypothesis. Production Compose, Nginx, APIs and application algorithms were therefore left unchanged. A fresh hosted run after review is still needed to establish whether the original failure recurs.

The verifier now names checks and reports bounded, redacted `expected`/`observed` values, the active stage, and safe source locations for unexpected exceptions. HTTP status/cache/security checks remain strict. Playwright JSON is parsed privately: only failed assertion file/line, result status, and primitive expected/received values are emitted. Raw errors, response bodies, credentials and fixture JSON are withheld. A nonzero browser exit, skipped/flaky/unexpected tests or fewer than six passed workflows fails verification. No test retries were enabled.

For example, a missing readiness response reports `stage=http_boundaries`, check `http.ready.status`, expected `200`, and the actual status. A browser assertion reports its spec filename and line rather than the former generic assertion class. Command failures report the command category and exit code; deadlines report their budget without echoing arguments or output. Cleanup failures also produce a nonzero exit.

Local validation of this diagnostic/cleanup change passed **165 unit tests (321 integration tests deselected)**, including **25 new verifier regressions**, Ruff lint/format, both image builds and the complete isolated acceptance command with **six Chromium workflows**, lease recovery, restart persistence, backup/restore, provider isolation and image-secret checks. The final temporary volume, containers, networks and credential file were removed successfully. An additional bounded Ubuntu mobile-layout probe passed **eight cases** without reproducing the earlier intermittent width failure. These passing runs are not evidence of the historical failing assertion.

The additional Ubuntu browser probe used the matching Playwright 1.64.1 Noble image under Docker Desktop, whose driver Node was 26.13.0; the standard local verifier used Node 22.23.4. This is not an exact reproduction of the GitHub-hosted Ubuntu 24.04 runner with Node 22.23.4, and the updated script has not run on GitHub. No remote rerun, commit or push was performed.

## Confirmed mobile overflow in the second hosted run

The [second run](https://github.com/maneelm/IncidentFlow-AI/actions/runs/42156246410), at `a06f05c`, identifies the immediate mobile-width assertion in `workflows.spec.ts`. Linux Chromium with Node **22.23.4** reproduced it after the assigned incident row rendered: viewport/body width **390px**, document scroll width **679px**. The offending box was the **1px, absolutely positioned `.sr-only` live-status span inside `Identifier`**, in the offscreen assignee column. Its containing block was the body, so the table's local scroll clipping did not contain it. Waiting for animation frames or changing container minimum widths did not fix it; fonts were already loaded.

The production fix is one declaration: `.identifier { position: relative; }`. It anchors the hidden live region to its owning identifier inside the table's scroller. The same case then reports **390px** for document width. No blanket horizontal-overflow suppression, content removal or accessibility-status removal was added. The table still scrolls locally to expose identifiers, controls and the final column.

The mobile workflow now creates its own assigned synthetic incident and waits for that row's live region before asserting geometry. Previously it waited only for the page heading, so a check before data arrived could miss the defect; an unassigned-only fixture could not expose it. The original immediate assertion remains strict, with no polling or retries. Coverage includes **320, 375, 390 and 430px**; the portfolio workflow also checks incident detail, investigation report, resolution verification (including expanded raw JSON) and timeline at these widths.

Overflow diagnostics include only bounded numeric geometry, structural tag/class names, layout styles, parent and containing-block boxes. They exclude page text, input values, IDs, HTML, request data and credentials. The container verifier allowlists these fields from Playwright JSON so an actual CI failure includes the offending boxes without forwarding raw browser output.

Validation of the mobile fix: the self-contained assigned-row regression **failed before the CSS fix** with document width 679px at a 390px viewport. After the fix, it passed **eight consecutive Linux Chromium runs** (retries disabled). A read-only four-page/four-width probe reported document widths equal to all 16 requested viewport/page combinations. A separate cold database/container run passed **all six browser workflows in 31.95 seconds**, using Node 22.23.4 and Playwright 1.64.1 Chromium on Ubuntu Noble. Frontend TypeScript, ESLint, **69 tests**, production build and OpenAPI comparison passed; Ruff and **172 Python unit tests** passed, including seven new diagnostic-sanitization cases.

The fix has not yet run on GitHub; a reviewed push is needed to validate the hosted runner. No tests were retried to obtain a passing result, no geometry assertion was relaxed, and no backend algorithm, migration, dependency lock, private environment or existing database record was changed.

To exercise the assigned-row regression repeatedly using the existing isolated browser harness (after its documented test-database/browser setup), run `npm run test:e2e -- --grep "real log pagination" --repeat-each=8` from `frontend/`. These are eight required executions, not retries; any failure fails the command. Normal CI still uses `retries: 0`.

The final **full isolated Linux container acceptance** also passed migration/startup, proxy/auth/cache/body boundaries, all six browser workflows, killed-claim recovery, normal restart/persistence, native backup/restore, absent-key behavior, production fake-provider rejection and non-root/literal-secret image scans. It removed only its own containers, networks, temporary database volume and generated credential file. Both investigation projects from this mobile fix were confirmed clean afterward. The local frontend was rebuilt/recreated to serve the fix; all 28 existing development tables retained identical content hashes and counts, and all 106 protected backend/migration/configuration inputs remained unchanged.
