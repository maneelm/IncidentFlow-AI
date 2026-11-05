import { useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useInfiniteQuery } from "@tanstack/react-query";
import { ArrowLeft, Pencil, RefreshCw } from "lucide-react";
import { useAuth } from "../auth/context";
import { api } from "../../lib/api/client";
import { useApi, useWrite } from "../../lib/api/query";
import { canWrite, transitions } from "../../lib/permissions";
import { date, label } from "../../lib/formatting";
import type { Incident, TimelinePage } from "../../types/models";
import { AssigneeSelect } from "../../components/Selectors";
import { ServiceLink, PersonName } from "../../components/ResourceLinks";
import { Identifier, Timestamp } from "../../components/RecordDetails";
import {
  Badge,
  Button,
  Dialog,
  Empty,
  ErrorPanel,
  Field,
  Json,
  Loading,
  Notice,
  PageTitle,
  Panel,
  Submit,
} from "../../components/ui";
import { IncidentForm } from "./IncidentList";
import { Logs } from "../logs/Logs";
import { DetectionList } from "../detections/Detections";
import { InvestigationList } from "../investigations/Investigations";
import { ProposalList } from "../resolutions/Resolutions";

export function Timeline({ incidentId }: { incidentId: string }) {
  const query = useInfiniteQuery<TimelinePage>({
    queryKey: ["api", `/incidents/${incidentId}/timeline`],
    queryFn: ({ pageParam, signal }) =>
      api.get(
        `/incidents/${incidentId}/timeline`,
        { limit: 10, cursor: pageParam as string | undefined },
        signal,
      ),
    initialPageParam: undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return (
    <Panel
      title="Unified incident timeline"
      className="incident-timeline"
      aside={
        <Button
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCw size={14} />
          Refresh
        </Button>
      }
    >
      <p className="small muted">
        Actual recorded events across incident management, detection,
        investigation and resolution. Cursor pagination preserves source
        identities.
      </p>
      <ErrorPanel error={query.error} />
      {query.isPending && <Loading />}
      <div className="timeline">
        {query.data?.pages
          .flatMap((page) => page.items)
          .map((item) => (
            <div
              className="timeline-item"
              key={`${item.source_type}:${item.source_id}:${item.event_type}`}
            >
              <span className="timeline-dot" />
              <div>
                <div className="timeline-heading">
                  <div className="actions">
                    <strong>{label(item.event_type)}</strong>
                    <Badge value={item.source_type} />
                  </div>
                  <span className="small muted">
                    <Timestamp value={item.occurred_at} />
                  </span>
                </div>
                <div className="timeline-context small muted">
                  <Identifier value={item.source_id} name="event source ID" />
                  {typeof item.details.actor_id === "string" ? (
                    <span>
                      Human · <PersonName id={item.details.actor_id} />
                    </span>
                  ) : typeof item.details.system_actor === "string" ? (
                    <span>System · {label(item.details.system_actor)}</span>
                  ) : null}
                </div>
                <div className="timeline-tools">
                  {item.source_type === "investigation" && (
                    <Link to={`/investigations/${item.source_id}`}>
                      Open investigation →
                    </Link>
                  )}
                  {item.source_type === "detection" && (
                    <Link to={`/detections/${item.source_id}`}>
                      Open detection →
                    </Link>
                  )}
                  {item.source_type === "resolution_event" &&
                    typeof item.details.proposal_id === "string" && (
                      <Link to={`/resolutions/${item.details.proposal_id}`}>
                        Open proposal →
                      </Link>
                    )}
                  <details>
                    <summary>Recorded metadata</summary>
                    <Json value={item.details} />
                  </details>
                </div>
              </div>
            </div>
          ))}
      </div>
      {query.data && !query.data.pages[0].items.length && (
        <Empty title="No recorded timeline events" />
      )}
      {query.hasNextPage && (
        <Button
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {query.isFetchingNextPage ? "Loading…" : "Load more activity"}
        </Button>
      )}
    </Panel>
  );
}
function IncidentOperation({
  incident,
  operation,
  close,
}: {
  incident: Incident;
  operation: "assign" | "transition";
  close: () => void;
}) {
  const write = useWrite<
    Incident,
    {
      assignee_id?: string | null;
      status?: Incident["status"];
      reason?: string;
    }
  >((body) =>
    operation === "assign"
      ? api.put(`/incidents/${incident.id}/assignee`, {
          expected_version: incident.version,
          ...body,
        })
      : api.post(`/incidents/${incident.id}/transitions`, {
          expected_version: incident.version,
          ...body,
        }),
  );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    try {
      await write.mutateAsync(
        operation === "assign"
          ? { assignee_id: String(values.get("assignee_id")) || null }
          : {
              status: String(values.get("status")) as Incident["status"],
              reason: String(values.get("reason")),
            },
      );
      close();
    } catch {
      /* Explicitly retain reviewed version after conflict. */
    }
  }
  return (
    <Dialog
      title={operation === "assign" ? "Assign incident" : "Transition incident"}
      onClose={close}
    >
      <p className="small muted">
        Reviewed incident version {incident.version}. A synthetic action never
        performs this transition for you.
      </p>
      {operation === "transition" && (
        <p className="small muted">
          Current state: {label(incident.status)}. Only transitions allowed by
          the incident lifecycle are offered. Reopening requires a recorded
          reason.
        </p>
      )}
      <form onSubmit={submit}>
        <ErrorPanel error={write.error} />
        {operation === "assign" ? (
          <AssigneeSelect current={incident.assignee_id} />
        ) : (
          <>
            <Field label="New lifecycle status">
              <select name="status" required>
                {transitions[incident.status].map((value) => (
                  <option key={value} value={value}>
                    {label(value)}
                    {value === "investigating" &&
                    ["resolved", "closed"].includes(incident.status)
                      ? " (reopen)"
                      : ""}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Transition reason">
              <textarea
                name="reason"
                required
                minLength={1}
                maxLength={2000}
                rows={4}
              />
            </Field>
            <Notice>
              Incident lifecycle changes are separate from investigation reports
              and synthetic verification.
            </Notice>
          </>
        )}
        <Submit pending={write.isPending}>
          {operation === "assign" ? "Update assignment" : "Confirm transition"}
        </Submit>
      </form>
    </Dialog>
  );
}
export function IncidentDetail() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "overview";
  const [edit, setEdit] = useState<Incident | null>(null);
  const [operation, setOperation] = useState<{
    kind: "assign" | "transition";
    incident: Incident;
  } | null>(null);
  const query = useApi<Incident>(`/incidents/${id}`);
  const incident = query.data;
  return (
    <>
      <Link className="back-link" to="/incidents">
        <ArrowLeft size={14} />
        All incidents
      </Link>
      <ErrorPanel error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {incident && (
        <>
          <PageTitle
            eyebrow={incident.incident_number}
            title={incident.title}
            description={`Created ${date(incident.created_at)} · Last updated ${date(incident.updated_at)}`}
            actions={
              <>
                <Button onClick={() => void query.refetch()}>
                  <RefreshCw size={14} />
                  Refresh
                </Button>
                {canWrite(user) && (
                  <Button onClick={() => setEdit(incident)}>
                    <Pencil size={14} />
                    Edit incident
                  </Button>
                )}
              </>
            }
          />
          <div className="resource-summary">
            <Badge value={incident.severity} />
            <Badge value={incident.status} />
            <ServiceLink id={incident.service_id} />
            <span className="grow" />
            <span className="small muted">Version {incident.version}</span>
            {canWrite(user) && (
              <>
                <Button
                  onClick={() => setOperation({ kind: "assign", incident })}
                >
                  Assign
                </Button>
                <Button
                  variant="primary"
                  onClick={() => setOperation({ kind: "transition", incident })}
                >
                  Change status
                </Button>
              </>
            )}
          </div>
          <div className="tabs" aria-label="Incident workspace sections">
            {[
              "overview",
              "logs",
              "detections",
              "investigations",
              "resolutions",
              "timeline",
            ].map((value) => (
              <button
                key={value}
                className={tab === value ? "active" : ""}
                aria-current={tab === value ? "page" : undefined}
                onClick={() => {
                  const next = new URLSearchParams(params);
                  next.set("tab", value);
                  setParams(next);
                }}
              >
                {label(value)}
              </button>
            ))}
          </div>
          {tab === "logs" ? (
            <Logs incidentId={id} />
          ) : tab === "detections" ? (
            <DetectionList incidentId={id} />
          ) : tab === "investigations" ? (
            <InvestigationList incidentId={id} />
          ) : tab === "resolutions" ? (
            <ProposalList incident={incident} />
          ) : tab === "timeline" ? (
            <Timeline incidentId={id} />
          ) : (
            <div className="two-columns">
              <Panel title="Incident description">
                <p className="log-full">{incident.description}</p>
                <p className="small muted">
                  Incident metadata is authored by humans or deterministic
                  detection. AI investigation reports remain separate.
                </p>
              </Panel>
              <Panel title="Response context">
                <dl className="detail-grid">
                  <dt>Incident number</dt>
                  <dd className="mono">{incident.incident_number}</dd>
                  <dt>Assignee</dt>
                  <dd>
                    <PersonName id={incident.assignee_id} />
                  </dd>
                  <dt>Affected service</dt>
                  <dd>
                    <ServiceLink id={incident.service_id} />
                  </dd>
                  <dt>Resolved</dt>
                  <dd>{date(incident.resolved_at)}</dd>
                  <dt>Closed</dt>
                  <dd>{date(incident.closed_at)}</dd>
                  <dt>Record ID</dt>
                  <dd>
                    <Identifier value={incident.id} name="incident ID" />
                  </dd>
                </dl>
              </Panel>
            </div>
          )}
          {edit && <IncidentForm current={edit} close={() => setEdit(null)} />}{" "}
          {operation && (
            <IncidentOperation
              incident={operation.incident}
              operation={operation.kind}
              close={() => setOperation(null)}
            />
          )}
        </>
      )}
    </>
  );
}
