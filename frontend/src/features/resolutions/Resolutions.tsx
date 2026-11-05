import { useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/context";
import { api, ApiError } from "../../lib/api/client";
import { activeProposal, useApi, useWrite } from "../../lib/api/query";
import { canWrite, reviewEligibility } from "../../lib/permissions";
import { date, label, shortId } from "../../lib/formatting";
import type {
  Approval,
  CatalogAction,
  Execution,
  Incident,
  Page,
  Proposal,
  ResolutionEvent,
  Schema,
} from "../../types/models";
import {
  IncidentLink,
  ServiceLink,
  PersonName,
} from "../../components/ResourceLinks";
import { Identifier } from "../../components/RecordDetails";
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  Empty,
  ErrorPanel,
  Field,
  Json,
  Loading,
  Notice,
  PageTitle,
  Pagination,
  Panel,
  Submit,
} from "../../components/ui";
import { ProposalForm } from "./ProposalForm";
import { ExecutionComparison } from "./ExecutionComparison";

export function ProposalList({ incident }: { incident?: Incident }) {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const [offset, setOffset] = useState(0);
  const [create, setCreate] = useState(false);
  const status = incident ? "" : (params.get("status") ?? "");
  const query = useApi<Page<Proposal>>(
    incident
      ? `/incidents/${incident.id}/resolution-proposals`
      : "/dashboard/resolution-proposals",
    { limit: 20, offset, ...(!incident ? { status } : {}) },
  );
  const catalog = useApi<CatalogAction[]>("/action-catalog");
  return (
    <>
      {!incident && (
        <PageTitle
          title="Resolution center"
          description="Review immutable proposals and follow human-approved synthetic execution."
        />
      )}
      <Notice>
        All executions are synthetic. Every action requires human approval;
        changing synthetic state requires a different approver. A successful
        simulation does not resolve an incident.
      </Notice>
      <div className="filter-bar">
        {incident ? (
          canWrite(user) && (
            <Button variant="primary" onClick={() => setCreate(true)}>
              Create resolution proposal
            </Button>
          )
        ) : (
          <>
            <Field label="Proposal status">
              <select
                value={status}
                onChange={(event) => {
                  setParams(
                    event.target.value ? { status: event.target.value } : {},
                  );
                  setOffset(0);
                }}
              >
                <option value="">All statuses</option>
                {[
                  "draft",
                  "pending_approval",
                  "queued",
                  "running",
                  "succeeded",
                  "failed",
                  "rejected",
                  "cancelled",
                  "expired",
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Link to="/incidents">
              Choose an incident to create a proposal →
            </Link>
          </>
        )}
      </div>
      <ErrorPanel error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {query.data && (
        <div className="panel">
          {query.data.items.length ? (
            <DataTable
              label="Resolution proposals"
              headers={[
                "Proposal",
                "Action",
                "Incident",
                "Risk",
                "Status",
                "Deadline",
              ]}
            >
              {query.data.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link className="mono" to={`/resolutions/${item.id}`}>
                      {shortId(item.id)}
                    </Link>
                  </td>
                  <td>{label(item.action_id)}</td>
                  <td>
                    <IncidentLink id={item.incident_id} />
                  </td>
                  <td>
                    <Badge value={item.risk} />
                  </td>
                  <td>
                    <Badge value={item.status} />
                  </td>
                  <td className="nowrap muted">
                    {date(item.approval_deadline)}
                  </td>
                </tr>
              ))}
            </DataTable>
          ) : (
            <Empty title="No resolution proposals" />
          )}
          <Pagination
            offset={offset}
            total={query.data.total}
            onChange={setOffset}
          />
        </div>
      )}
      <details className="panel catalog-details">
        <summary>
          Reviewed action catalog · {catalog.data?.length ?? "…"} definitions
        </summary>
        <ErrorPanel error={catalog.error} />
        {catalog.data?.map((item) => (
          <div className="catalog-entry" key={item.id}>
            <div className="actions">
              <strong>{label(item.id)}</strong>
              <Badge value={item.risk} />
            </div>
            <p>{item.description}</p>
            <p className="small muted">
              Version {item.version} · Budget {item.timeout_seconds}s · Approval
              required
              {item.distinct_approver_required
                ? " · Different human reviewer required"
                : ""}
            </p>
            <details>
              <summary>Parameter schema</summary>
              <Json value={item.parameter_schema} />
            </details>
          </div>
        ))}
      </details>
      {create && incident && (
        <ProposalForm
          incident={incident}
          close={() => setCreate(false)}
          initialInvestigation={params.get("investigation") ?? ""}
          recommendation={Math.max(
            0,
            Math.min(4, Number(params.get("recommendation")) || 0),
          )}
        />
      )}
    </>
  );
}
export function ProposalDetail() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const [offset, setOffset] = useState(0);
  const [review, setReview] = useState<{
    decision: "approve" | "reject";
    version: number;
  } | null>(null);
  const query = useApi<Proposal>(`/resolution-proposals/${id}`, undefined, {
    poll: activeProposal,
  });
  const proposal = query.data;
  const events = useApi<Page<ResolutionEvent>>(
    `/resolution-proposals/${id}/events`,
    { limit: 20, offset },
    {
      enabled: !!proposal,
      poll: !!proposal && activeProposal(proposal),
      revision: proposal?.version,
    },
  );
  const decision = useApi<Approval>(
    `/resolution-proposals/${id}/approval`,
    undefined,
    {
      enabled:
        !!proposal && !["draft", "pending_approval"].includes(proposal.status),
    },
  );
  const job = useApi<Schema<"app__schemas__resolution__JobRead">>(
    `/resolution-proposals/${id}/execution`,
    undefined,
    {
      enabled:
        !!proposal &&
        !["draft", "pending_approval", "rejected"].includes(proposal.status),
      poll: !!proposal && activeProposal(proposal),
      revision: proposal?.version,
    },
  );
  const result = useApi<Execution>(
    `/resolution-proposals/${id}/result`,
    undefined,
    {
      enabled: !!proposal && ["succeeded", "failed"].includes(proposal.status),
    },
  );
  const action = useWrite<
    Proposal,
    { operation: string; version: number; reason?: string }
  >((input) =>
    api.post(`/resolution-proposals/${id}/${input.operation}`, {
      expected_version: input.version,
      ...(input.reason ? { reason: input.reason } : {}),
    }),
  );
  const eligible = proposal
    ? reviewEligibility(user, proposal)
    : { allowed: false, reason: "" };
  const own =
    canWrite(user) &&
    (proposal?.requested_by === user?.id || user?.role === "admin");
  async function submitReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!review) return;
    const reason = String(new FormData(event.currentTarget).get("reason"));
    try {
      await action.mutateAsync({
        operation: review.decision,
        version: review.version,
        reason,
      });
      setReview(null);
    } catch {
      /* Remain open with reviewed version/error. */
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="HUMAN-APPROVED WORKFLOW"
        title="Resolution proposal"
        description="Immutable intent, a real human decision, and an inspectable synthetic result."
        actions={
          <Button onClick={() => void query.refetch()}>Refresh proposal</Button>
        }
      />
      <ErrorPanel error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {proposal && (
        <>
          <div className="resource-summary">
            <div className="grow">
              <Identifier value={proposal.id} name="proposal ID" />
              <h2>{label(proposal.action_id)}</h2>
            </div>
            <Badge value={proposal.status} />
            <Badge value="synthetic" synthetic />
          </div>
          <Notice tone="warning">
            Execution affects isolated synthetic state only. Simulation success
            is not confirmation that a production incident recovered.
          </Notice>
          <ErrorPanel error={action.error} retry={() => void query.refetch()} />
          {proposal.error_code && (
            <Notice tone="warning">Workflow code: {proposal.error_code}</Notice>
          )}
          <div className="actions section-gap">
            <IncidentLink id={proposal.incident_id}>
              Open incident →
            </IncidentLink>
            {own && proposal.status === "draft" && (
              <Button
                variant="primary"
                disabled={action.isPending}
                onClick={() =>
                  action.mutate({
                    operation: "submit",
                    version: proposal.version,
                  })
                }
              >
                Submit for approval
              </Button>
            )}
            {proposal.status === "pending_approval" && canWrite(user) && (
              <>
                <Button
                  variant="primary"
                  disabled={!eligible.allowed || action.isPending}
                  onClick={() =>
                    setReview({
                      decision: "approve",
                      version: proposal.version,
                    })
                  }
                >
                  Approve proposal
                </Button>
                <Button
                  variant="danger"
                  disabled={!eligible.allowed || action.isPending}
                  onClick={() =>
                    setReview({ decision: "reject", version: proposal.version })
                  }
                >
                  Reject proposal
                </Button>
              </>
            )}
            {own &&
              ["draft", "pending_approval", "queued", "running"].includes(
                proposal.status,
              ) && (
                <Button
                  disabled={action.isPending}
                  onClick={() =>
                    action.mutate({
                      operation: "cancel",
                      version: proposal.version,
                    })
                  }
                >
                  Cancel proposal
                </Button>
              )}
          </div>
          {proposal.status === "pending_approval" && (
            <Notice tone={eligible.allowed ? "info" : "warning"}>
              {eligible.reason} Deadline: {date(proposal.approval_deadline)}
            </Notice>
          )}
          <div className="two-columns">
            <Panel title="Immutable proposal">
              <p>{proposal.explanation}</p>
              <dl className="detail-grid">
                <dt>Risk</dt>
                <dd>
                  <Badge value={proposal.risk} />
                </dd>
                <dt>Origin</dt>
                <dd>
                  {proposal.origin === "human"
                    ? "Human authored"
                    : "Human-selected read-only recommendation"}
                </dd>
                <dt>Proposal version</dt>
                <dd>{proposal.version}</dd>
                <dt>Incident version</dt>
                <dd>{proposal.incident_version}</dd>
                <dt>Requested by</dt>
                <dd>
                  <PersonName id={proposal.requested_by} />
                </dd>
                <dt>Target service</dt>
                <dd>
                  <ServiceLink id={proposal.service_id} />
                  <Identifier
                    value={proposal.service_id}
                    name="target service ID"
                  />
                </dd>
                <dt>Synthetic state</dt>
                <dd>
                  <Identifier
                    value={proposal.state_id}
                    name="synthetic state ID"
                  />{" "}
                  · v{proposal.state_version}
                </dd>
                <dt>Approval rule</dt>
                <dd>
                  {proposal.risk === "synthetic_mutation"
                    ? "Independent reviewer required for synthetic changes."
                    : "Explicit human approval required."}
                </dd>
              </dl>
              <h3>Immutable parameters</h3>
              <Json value={proposal.parameters} />
              <p className="mono small muted wrap">
                Payload hash: {proposal.payload_hash}
              </p>
            </Panel>
            <Panel title="Evidence & policy">
              <p>
                {proposal.investigation_id ? (
                  <Link to={`/investigations/${proposal.investigation_id}`}>
                    Open source investigation →
                  </Link>
                ) : (
                  "Human proposal without AI provenance"
                )}
              </p>
              <details>
                <summary>Stored provenance</summary>
                <Json value={proposal.provenance} />
              </details>
              <details>
                <summary>Snapshotted approval policy</summary>
                <Json value={proposal.policy} />
              </details>
            </Panel>
          </div>
          {decision.data && (
            <Panel title="Human review decision">
              <div className="actions">
                <Badge value={decision.data.decision} />
                <span>{date(decision.data.created_at)}</span>
              </div>
              <p>{decision.data.reason}</p>
              <div className="small muted">
                Reviewer: <PersonName id={decision.data.actor_id} />
              </div>
              <details>
                <summary>Exact reviewed snapshot</summary>
                <Json value={decision.data.snapshot} />
              </details>
            </Panel>
          )}
          {decision.error && decision.error.status !== 404 && (
            <ErrorPanel error={decision.error} />
          )}
          <Panel title="Execution status">
            {job.data ? (
              <div className="actions">
                <Badge value={job.data.status} />
                <span>
                  Attempt {job.data.attempts}/{job.data.max_attempts}
                </span>
                <span className="muted">
                  {job.data.last_error_code ?? "No recorded execution error"}
                </span>
              </div>
            ) : (
              <p className="muted">
                No execution job is available for this proposal.
              </p>
            )}
            {job.error && job.error.status !== 404 && (
              <ErrorPanel error={job.error} />
            )}
          </Panel>
          {result.data && (
            <Panel
              title="Synthetic execution & verification"
              aside={
                <Badge
                  value={
                    result.data.verification.contract_satisfied
                      ? "contract_satisfied"
                      : "verification_failed"
                  }
                />
              }
            >
              <Notice
                tone={
                  result.data.verification.contract_satisfied
                    ? "success"
                    : "warning"
                }
              >
                {result.data.verification.summary} Incident resolution remains a
                separate human decision.
              </Notice>
              <p className="small muted">
                Fixture version {result.data.before_version} →{" "}
                {result.data.after_version} · Attempt {result.data.attempt} ·{" "}
                {date(result.data.created_at)}
              </p>
              <ExecutionComparison result={result.data.result} />
              <ul>
                {result.data.result.findings.map((value) => (
                  <li key={value}>{value}</li>
                ))}
              </ul>
              <p className="small muted">
                Improved: {String(result.data.verification.improved)} · Incident
                resolution confirmed:{" "}
                {String(result.data.verification.incident_resolution_confirmed)}
              </p>
            </Panel>
          )}
          {result.error &&
            (result.error instanceof ApiError &&
            result.error.code === "result_unavailable" ? (
              <Notice>
                No committed execution result exists. The worker may have failed
                before applying any synthetic effect.
              </Notice>
            ) : (
              <ErrorPanel error={result.error} />
            ))}
          <Panel title="Append-only workflow history">
            <ErrorPanel error={events.error} />
            <div className="timeline">
              {events.data?.items.map((item) => (
                <div className="timeline-item" key={item.id}>
                  <span className="timeline-dot" />
                  <div>
                    <strong>{label(item.event_type)}</strong>
                    <p className="small muted">
                      {date(item.occurred_at)} · Version {item.proposal_version}{" "}
                      ·{" "}
                      {item.actor_id
                        ? `Human ${shortId(item.actor_id)}`
                        : item.system_actor}
                    </p>
                    <details>
                      <summary>Recorded metadata</summary>
                      <Json value={item.details} />
                    </details>
                  </div>
                </div>
              ))}
            </div>
            {events.data && (
              <Pagination
                offset={offset}
                total={events.data.total}
                onChange={setOffset}
              />
            )}
          </Panel>
        </>
      )}
      {review && (
        <Dialog
          title={
            review.decision === "approve"
              ? "Approve exact proposal"
              : "Reject proposal"
          }
          onClose={() => setReview(null)}
        >
          <Notice>
            Reviewing proposal version {review.version}. No approval can change
            the immutable action payload.
          </Notice>
          <ErrorPanel error={action.error} />
          <form onSubmit={submitReview}>
            <Field label="Review reason">
              <textarea
                name="reason"
                required
                minLength={5}
                maxLength={1000}
                rows={4}
              />
            </Field>
            <Submit pending={action.isPending}>
              {review.decision === "approve"
                ? "Confirm approval"
                : "Confirm rejection"}
            </Submit>
          </form>
        </Dialog>
      )}
    </>
  );
}
