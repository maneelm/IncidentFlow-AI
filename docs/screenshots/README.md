# Real application screenshots

The PNG files in this directory are actual Playwright captures of IncidentFlow, not generated mockups. Every image uses a 1440 × 1000 viewport. Records, users, runbooks, logs and synthetic execution results belong to an isolated migrated test schema; no development database data is used. The model adapter is explicitly fake. Display names such as “Browser engineer” are generated test personas, not private accounts.

| Image | Actual state |
| --- | --- |
| [Overview](operations-overview.png) | SQL record counts, severity distribution and deterministic detections |
| [Incident](incident-details.png) | Detected synthetic failure, real service, state/version and response context |
| [Logs](log-explorer.png) | Bounded synthetic timeout records, service and correlation IDs |
| [Investigation summary](ai-investigation-summary.png) | Stored provider/model, completed stages and deterministic observations |
| [Investigation hypotheses](ai-investigation-report.png) | Unverified alternatives, uncertainty, citations and diagnostic suggestions |
| [Resolution proposal](resolution-center.png) | Allowlisted action, risk, target, human author and approval context |
| [Synthetic verification](resolution-verification.png) | Nine recorded metrics in Before/After columns, expandable raw execution data and explicit separation from incident resolution |
| [Incident timeline](incident-timeline.png) | Compact chronological audit entries, timestamps, human/system actors, source links and expandable metadata |

The verification image was refreshed and the timeline image added for the final targeted refinement on November 5, 2026. The other portfolio images remain unchanged.

## Reproduce safely

From the repository root, use the supported Node runtime, installed frontend dependencies and Chromium:

```powershell
docker compose --profile test up -d --wait postgres-test
Push-Location frontend
try {
    $env:CAPTURE_PORTFOLIO = '1'
    npm run test:e2e -- --grep 'portfolio workflow'
    if ($LASTEXITCODE -ne 0) { throw 'Capture failed; do not publish partial screenshots.' }
} finally {
    Remove-Item Env:CAPTURE_PORTFOLIO -ErrorAction SilentlyContinue
    Pop-Location
}
```

The harness creates generated credentials in memory, starts a real API and workers, and removes its own schema/processes afterward. It does not call Groq or reset the development volume. Screenshots are written only when `CAPTURE_PORTFOLIO=1`; ordinary CI/browser/container checks run the workflow without rewriting approved images. `PORTFOLIO_SCREENSHOT_DIR` optionally changes the capture destination relative to `frontend/`.

Inspect every image before staging: no sign-in form, password, producer token, developer tools, error overlay, private account or sensitive log should appear. The fixture's safe source IDs and synthetic numerical state remain visible because they explain provenance and verification. Screenshots are not evidence that a live Groq run, managed hosting or production recovery occurred.
