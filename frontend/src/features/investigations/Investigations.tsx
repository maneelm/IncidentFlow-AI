import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Play, SearchCode } from "lucide-react";
import { useAuth } from "../auth/context";
import { api } from "../../lib/api/client";
import { activeInvestigation, useApi, useWrite } from "../../lib/api/query";
import { canWrite } from "../../lib/permissions";
import { date, label, shortId, uuid } from "../../lib/formatting";
import type {
  Evidence,
  Investigation,
  Page,
  Report,
  Schema,
  Step,
} from "../../types/models";
import { IncidentLink } from "../../components/ResourceLinks";
import { Identifier, Timestamp } from "../../components/RecordDetails";
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
} from "../../components/ui";

export function StartInvestigation({ incidentId }: { incidentId: string }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const capability = useApi<Schema<"InvestigationCapability">>(
    "/investigation-capabilities",
  );
  const [requestId] = useState(uuid);
  const write = useWrite<Investigation, void>(() =>
    api.post(`/incidents/${incidentId}/investigations`, {
      client_request_id: requestId,
    }),
  );
  return (
    <>
      <ErrorPanel error={capability.error} />
      {capability.data && !capability.data.configured && (
        <Notice tone="warning">
          Investigation provider unavailable. Normal incident and log operations
          remain available.
        </Notice>
      )}
      {capability.data?.synthetic && (
        <Notice>
          Fake provider enabled. Reports are deterministic synthetic outputs,
          not hosted AI inference.
        </Notice>
      )}
      <ErrorPanel error={write.error} />
      {canWrite(user) && (
        <Button
          variant="primary"
          disabled={!capability.data?.configured || write.isPending}
          onClick={() => {
            void write
              .mutateAsync()
              .then((result) => navigate(`/investigations/${result.id}`))
              .catch(() => {});
          }}
        >
          <Play size={15} />
          Start investigation
        </Button>
      )}
    </>
  );
}
export function InvestigationList({ incidentId }: { incidentId?: string }) {
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const query = useApi<Page<Investigation>>(
    incidentId
      ? `/incidents/${incidentId}/investigations`
      : "/dashboard/investigations",
    { limit: 20, offset, ...(!incidentId ? { status } : {}) },
  );
  return (
    <>
      {!incidentId && (
        <PageTitle
          title="Investigations"
          description="Evidence-grounded analysis, uncertain hypotheses, and diagnostic suggestions."
        />
      )}
      {incidentId ? (
        <div className="section-gap">
          <StartInvestigation incidentId={incidentId} />
        </div>
      ) : (
        <div className="filter-bar">
          <Field label="Investigation status">
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">All statuses</option>
              {[
                "queued",
                "running",
                "completed",
                "inconclusive",
                "failed",
                "cancelled",
              ].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </Field>
          <Link to="/incidents">
            Open an incident to start an investigation →
          </Link>
        </div>
      )}
      <ErrorPanel error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {query.data && (
        <div className="panel">
          {query.data.items.length ? (
            <DataTable
              label="Investigations"
              headers={[
                "Investigation",
                "Incident",
                "Status",
                "Provider",
                "Stage",
                "Requested",
              ]}
            >
              {query.data.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link className="mono" to={`/investigations/${item.id}`}>
                      {shortId(item.id)}
                    </Link>
                  </td>
                  <td>
                    <IncidentLink id={item.incident_id} />
                  </td>
                  <td>
                    <Badge value={item.status} />
                  </td>
                  <td>
                    {item.provider === "fake" ? (
                      <Badge value="synthetic" synthetic />
                    ) : (
                      <span>{item.provider}</span>
                    )}
                  </td>
                  <td className="small muted">{label(item.stage)}</td>
                  <td className="nowrap muted">{date(item.created_at)}</td>
                </tr>
              ))}
            </DataTable>
          ) : (
            <Empty title="No investigations found" />
          )}
          <Pagination
            offset={offset}
            total={query.data.total}
            onChange={setOffset}
          />
        </div>
      )}
    </>
  );
}
export function EvidenceReferences({
  refs,
  onSelect,
}: {
  refs: string[];
  onSelect: (ref: string) => void;
}) {
  const chips = (references: string[]) =>
    references.map((reference) => (
      <button
        type="button"
        key={reference}
        className="citation mono"
        title={reference}
        onClick={() => onSelect(reference)}
        aria-label={`View ${reference} source`}
      >
        {label(reference.split(":")[0])} ·{" "}
        {reference.split(":")[1]?.slice(0, 8)}
      </button>
    ));
  return (
    <div className="evidence-references">
      <div className="citations">{chips(refs.slice(0, 6))}</div>
      {refs.length > 6 && (
        <details>
          <summary>Show {refs.length - 6} more sources</summary>
          <div className="citations">{chips(refs.slice(6))}</div>
        </details>
      )}
    </div>
  );
}

export function StepDiagnostics({ step }: { step: Step }) {
  if (step.status !== "failed") return null;
  const source = step.details.diagnostics;
  const diagnostics =
    source && typeof source === "object"
      ? (source as Record<string, unknown>)
      : {};
  const keys = [
    "condition",
    "field",
    "exception_class",
    "http_status",
    "request_id",
    "prompt_bytes_reserved",
    "max_completion_tokens",
    "input_tokens",
    "output_tokens",
  ];
  return (
    <details className="step-diagnostics">
      <summary>Failure details · attempt {step.attempt}</summary>
      <dl className="detail-grid">
        {typeof step.details.error_code === "string" && (
          <>
            <dt>Safe error code</dt>
            <dd>{step.details.error_code}</dd>
          </>
        )}
        {keys
          .filter(
            (key) =>
              typeof diagnostics[key] === "number" ||
              typeof diagnostics[key] === "string",
          )
          .map((key) => (
            <div className="detail-row" key={key}>
              <dt>{label(key)}</dt>
              <dd className="mono wrap">{String(diagnostics[key])}</dd>
            </div>
          ))}
        {typeof step.details.retry_delay_seconds === "number" && (
          <>
            <dt>Retry delay</dt>
            <dd>{step.details.retry_delay_seconds} seconds</dd>
          </>
        )}
        {typeof step.details.retry_scheduled === "boolean" && (
          <>
            <dt>Retry scheduled</dt>
            <dd>
              {step.details.retry_scheduled
                ? "Yes, within the saved budget"
                : "No"}
            </dd>
          </>
        )}
      </dl>
      {!keys.some((key) => key in diagnostics) && (
        <p className="small muted">
          This older attempt did not retain detailed diagnostics. Its exact
          cause cannot be reconstructed.
        </p>
      )}
    </details>
  );
}
export function InvestigationDetail() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const [source, setSource] = useState<string | null>(null);
  const query = useApi<Investigation>(`/investigations/${id}`, undefined, {
    poll: activeInvestigation,
  });
  const current = query.data;
  const report = useApi<Report>(`/investigations/${id}/report`, undefined, {
    enabled:
      !!current && ["completed", "inconclusive"].includes(current.status),
  });
  const evidence = useApi<Page<Evidence>>(
    `/investigations/${id}/evidence`,
    { limit: 100, offset: 0 },
    {
      enabled: !!current,
      poll: !!current && activeInvestigation(current),
      revision: current?.status,
    },
  );
  const steps = useApi<Page<Step>>(
    `/investigations/${id}/steps`,
    { limit: 100, offset: 0 },
    {
      enabled: !!current,
      poll: !!current && activeInvestigation(current),
      revision: current?.status,
    },
  );
  const cancel = useWrite<Investigation, void>(() =>
    api.post(`/investigations/${id}/cancel`),
  );
  const selected = evidence.data?.items.find(
    (item) => item.reference === source,
  );
  return (
    <>
      <PageTitle
        eyebrow="INVESTIGATION WORKSPACE"
        title="Investigation report"
        description="Trace every suggestion back to the evidence selected for this investigation."
        actions={
          current && (
            <IncidentLink id={current.incident_id}>
              Open incident →
            </IncidentLink>
          )
        }
      />
      <ErrorPanel error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {current && (
        <>
          <div className="resource-summary">
            <SearchCode size={22} />
            <div className="grow">
              <Identifier value={current.id} name="investigation ID" />
              <div className="actions">
                <Badge value={current.status} />
                <Badge
                  value={
                    current.provider === "fake" ? "synthetic" : current.provider
                  }
                  synthetic={current.provider === "fake"}
                />
                <span className="small muted">
                  <span className="mono">{current.model}</span> ·{" "}
                  <Timestamp value={current.created_at} />
                </span>
              </div>
            </div>
            {canWrite(user) && activeInvestigation(current) && (
              <Button
                onClick={() => cancel.mutate()}
                disabled={cancel.isPending}
              >
                Cancel investigation
              </Button>
            )}
          </div>
          <ErrorPanel error={cancel.error} />
          {current.provider === "fake" && (
            <Notice tone="warning">
              Synthetic provider report. This is deterministic test output, not
              a live Groq diagnosis.
            </Notice>
          )}
          {current.error_code && (
            <Notice tone="warning">
              Safe failure code: {current.error_code}. Review the recorded
              steps; no incident state was changed.
            </Notice>
          )}
          <Panel
            title="Workflow progress"
            aside={
              <span className="small muted">
                {current.llm_calls} model calls reserved
              </span>
            }
          >
            <ErrorPanel error={steps.error} />
            <details
              className="workflow-disclosure"
              open={activeInvestigation(current) || current.status === "failed"}
            >
              <summary>
                {steps.data?.items.filter((step) => step.status === "completed")
                  .length ?? 0}{" "}
                recorded stages completed · Review workflow history
              </summary>
              <div className="steps">
                {steps.data?.items.map((step, index) => (
                  <div key={`${step.attempt}:${step.stage}:${index}`}>
                    <span className={`step-mark ${step.status}`}>
                      {step.status === "completed"
                        ? "✓"
                        : step.status === "failed"
                          ? "!"
                          : "·"}
                    </span>
                    <div>
                      <strong>{label(step.stage)}</strong>
                      <span>
                        Attempt {step.attempt} · {step.status}
                      </span>
                      <StepDiagnostics step={step} />
                    </div>
                  </div>
                ))}
                {!steps.data?.items.length && (
                  <p className="muted">
                    Waiting for the investigation worker to record its first
                    stage.
                  </p>
                )}
              </div>
            </details>
          </Panel>
          <ErrorPanel error={report.error} />
          {report.isFetching && report.isEnabled && (
            <Loading text="Loading validated report…" />
          )}
          {report.data && (
            <>
              <Panel title="Observed problem">
                <p className="report-summary">{report.data.summary}</p>
                <p className="small muted">
                  {report.data.metadata.log_count} usable logs ·{" "}
                  {report.data.metadata.evidence_count} sources · Completed{" "}
                  {date(report.data.metadata.completed_at)}
                </p>
              </Panel>
              <Notice>
                Citations establish source membership, not proof of causality.
                Hypotheses and recommendations require independent human
                verification.
              </Notice>
              <Panel title="Observed evidence">
                <p className="small muted">
                  Recorded facts and application-computed counts, separate from
                  the model's hypotheses below.
                </p>
                <div className="report-list">
                  {report.data.observed_evidence.map((item, index) => (
                    <div key={index}>
                      <p>{item.statement}</p>
                      <EvidenceReferences
                        refs={item.evidence_ids}
                        onSelect={setSource}
                      />
                    </div>
                  ))}
                </div>
              </Panel>
              <h2 className="section-title">
                Unverified root-cause hypotheses
              </h2>
              <div className="hypothesis-grid">
                {report.data.hypotheses.length ? (
                  report.data.hypotheses.map((item, index) => (
                    <Panel
                      key={index}
                      title={`${index + 1}. ${item.title}`}
                      aside={<Badge value={`${item.confidence} confidence`} />}
                    >
                      <p>{item.explanation}</p>
                      <EvidenceReferences
                        refs={item.evidence_ids}
                        onSelect={setSource}
                      />
                      <h3>Missing or contradicting evidence</h3>
                      <ul className="muted">
                        {item.missing_or_contradicting_evidence.map((value) => (
                          <li key={value}>{value}</li>
                        ))}
                      </ul>
                      <h3>Suggested verification</h3>
                      <p>{item.verification_step}</p>
                    </Panel>
                  ))
                ) : (
                  <Notice tone="warning">
                    Evidence was insufficient. No root cause was inferred.
                  </Notice>
                )}
              </div>
              <h2 className="section-title">Diagnostic recommendations</h2>
              {report.data.recommended_actions.map((item, index) => (
                <Panel
                  key={index}
                  title={item.description}
                  aside={<Badge value="read_only" />}
                >
                  <p>{item.rationale}</p>
                  <EvidenceReferences
                    refs={item.evidence_ids}
                    onSelect={setSource}
                  />
                  <p className="small muted">
                    Human review required. This text cannot execute commands.
                  </p>
                  {canWrite(user) && (
                    <Link
                      className="inline-link"
                      to={`/incidents/${current.incident_id}?tab=resolutions&investigation=${id}&recommendation=${index}`}
                    >
                      Create a reviewed proposal — choose an action explicitly →
                    </Link>
                  )}
                </Panel>
              ))}
              <Panel title="Limitations">
                <ul className="muted">
                  {report.data.limitations.map((value) => (
                    <li key={value}>{value}</li>
                  ))}
                </ul>
              </Panel>
            </>
          )}
          <Panel title="Selected evidence & retrieval provenance">
            <ErrorPanel error={evidence.error} />
            {evidence.data?.items.length ? (
              <div className="record-list">
                {evidence.data.items.map((item) => (
                  <button
                    className="record-item text-left"
                    key={item.reference}
                    onClick={() => setSource(item.reference)}
                  >
                    <div>
                      <strong>{item.label}</strong>
                      <span className="mono small muted" title={item.reference}>
                        {label(item.source_type)} ·{" "}
                        {item.reference.split(":")[1]?.slice(0, 8)}
                      </span>
                      <span className="small muted">{item.role}</span>
                    </div>
                    <Badge
                      value={
                        item.quarantined ? "quarantined" : item.source_type
                      }
                    />
                  </button>
                ))}
              </div>
            ) : (
              <p className="muted">
                No selected evidence has been persisted yet.
              </p>
            )}
          </Panel>
        </>
      )}
      {source && (
        <Dialog title="Evidence source" onClose={() => setSource(null)}>
          {selected ? (
            <>
              <Badge value={selected.source_type} />
              {selected.quarantined && (
                <Notice tone="warning">
                  Quarantined source; it was excluded from model citations.
                </Notice>
              )}
              <h3>{selected.label}</h3>
              <p className="mono wrap small">{selected.reference}</p>
              <Identifier
                value={selected.reference}
                name="evidence reference"
              />
              <p className="log-full">{selected.excerpt}</p>
              <h3>Collection / retrieval provenance</h3>
              <Json value={selected.provenance} />
              {selected.source_type === "log" && (
                <Link to={`/logs/${selected.reference.split(":")[1]}`}>
                  Open original log →
                </Link>
              )}
            </>
          ) : (
            <Notice>
              The source is not available in the currently loaded evidence.
              Refresh the investigation and verify the reference.
            </Notice>
          )}
        </Dialog>
      )}
    </>
  );
}
