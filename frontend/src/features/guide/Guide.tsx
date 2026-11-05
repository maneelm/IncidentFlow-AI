import { Link } from "react-router-dom";
import { PageTitle, Panel, Notice } from "../../components/ui";
export function Guide() {
  return (
    <>
      <PageTitle
        title="Workflow guide"
        description="Understand the boundaries before taking action."
      />
      <Notice tone="warning">
        This console supports isolated simulated recovery. It has no production
        infrastructure tools.
      </Notice>
      <div className="two-columns">
        <Panel title="1 · Detect and investigate">
          <p>
            Use the existing Phase 2 producer/demo CLI to ingest a synthetic
            failure with a scoped key. Inspect the detected incident, logs and
            stored rule snapshot. Open Investigations to start an analysis.
          </p>
          <p>
            The fake provider produces labeled deterministic reports without a
            Groq key. A configured Groq provider sends bounded redacted evidence
            to the hosted service. Hypotheses remain unverified in both modes.
          </p>
          <Link to="/incidents">Open incidents →</Link>
        </Panel>
        <Panel title="2 · Propose and review">
          <p>
            An engineer explicitly chooses a catalog action and cites usable
            investigation evidence. The immutable draft must be submitted.
            Read-only actions may be self-reviewed; synthetic changes need
            another authorized human, including when the proposer is an
            administrator.
          </p>
          <p>
            Sign out and have a different user sign in to review a synthetic
            change. Never share accounts to bypass separation of duties.
          </p>
          <Link to="/resolutions">Open resolution center →</Link>
        </Panel>
        <Panel title="3 · Execute and verify">
          <p>
            Approval queues the proposal atomically. The separate resolution
            worker checks approval, versions, evidence and deadline, then
            records its synthetic before/after state and verification.
          </p>
          <p>
            Simulation success does not resolve the incident. An engineer must
            separately request the original lifecycle transition with a reason.
          </p>
        </Panel>
        <Panel title="Developer setup">
          <p>
            Account provisioning and log ingestion setup remain operator CLI
            tasks. No permanent demo credentials or unsafe browser ingestion key
            storage are provided.
          </p>
          <pre className="json">
            {"uv run python -m app.simulations.resolution_demo --timeout 120"}
          </pre>
          <p>
            See repository docs/resolution_workflows.md for private environment
            credentials and API/detection/investigation/resolution worker
            startup. Admins can initialize isolated scenarios from Services.
          </p>
        </Panel>
      </div>
    </>
  );
}
