# CI verification

`.github/workflows/ci.yml` runs on push, pull request and manual dispatch with read-only repository permissions, no persisted checkout credentials, cancellation of superseded runs, and bounded job timeouts. Third-party actions are pinned to reviewed commit SHAs. Python 3.12, uv 0.11.28 and Node 22.23.4 match the local/container contracts. Lockfiles are mandatory; CI does not regenerate them to conceal a mismatch.

| Job | Actual checks |
| --- | --- |
| python (25 minutes) | `uv sync --locked`, Ruff lint/format, generated private test settings, isolated PostgreSQL 17 test service, publication audit, complete pytest suite; test service stopped even on failure |
| frontend (15 minutes) | `npm ci`, TypeScript, ESLint, component/API tests, production build, generated OpenAPI contract comparison using locked backend dependencies |
| browser (15 minutes) | Chromium installation with Linux dependencies, real FastAPI/workers, per-run migrated schema in the dedicated test database, all six Playwright workflows, preserving the original five; no hosted model |
| containers (25 minutes) | Both image builds, quiet Compose validation, complete six-service startup/migration, six browser workflows through Nginx, real ingestion/detection/fake investigation/two-person synthetic execution, worker crash recovery, restart/persistence, native backup/restore, absent-key behavior, production fake rejection, non-root and image-layer secret checks |

The jobs run on separate `ubuntu-24.04` GitHub-hosted runners, so their fixed test port/project names do not collide. Test `.env` files contain newly generated values and never production credentials. `GROQ_API_KEY` is empty; the investigation adapter is explicitly fake in browser/container fixtures. The backend adapter tests use the real SDK over an in-memory HTTP transport. No raw environment dumps, resolved Compose configuration, auth headers, provider messages, traces or credential-bearing fixture JSON are uploaded.

Each backend/browser test owns a random migrated schema. Fixtures validate that the target is `incidentflow_test` at loopback port 55433 under its dedicated user, and clean only their owned schema. The container verifier uses its own generated project and separate named volume; it never resets the default `incidentflow_postgres_data`. It retains its owned volume locally after stopping containers; a hosted runner's disk is disposable. Browser screenshots are ignored local artifacts, not automatic public uploads.

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
