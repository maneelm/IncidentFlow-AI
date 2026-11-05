import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { useAuth } from "../auth/context";
import { api } from "../../lib/api/client";
import { useApi, useWrite } from "../../lib/api/query";
import { canAdmin } from "../../lib/permissions";
import { date } from "../../lib/formatting";
import type { Detection, Page, Rule, Schema } from "../../types/models";
import { ServiceSelect } from "../../components/Selectors";
import { IncidentLink, ServiceLink } from "../../components/ResourceLinks";
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
import { Logs } from "../logs/Logs";

function RuleForm({ current, close }: { current?: Rule; close: () => void }) {
  const write = useWrite<Rule, Schema<"RuleCreate"> | Schema<"RuleUpdate">>(
    (body) =>
      current
        ? api.patch(`/detection-rules/${current.id}`, body)
        : api.post("/detection-rules", body),
  );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const shared = {
      name: String(values.get("name")),
      level: String(values.get("level")) as Rule["level"],
      error_code: String(values.get("error_code")) || null,
      threshold: Number(values.get("threshold")),
      window_seconds: Number(values.get("window_seconds")),
      cooldown_seconds: Number(values.get("cooldown_seconds")),
      incident_severity: String(
        values.get("incident_severity"),
      ) as Rule["incident_severity"],
      enabled: values.get("enabled") === "on",
    };
    try {
      await write.mutateAsync(
        current
          ? { ...shared, expected_version: current.version }
          : { ...shared, service_id: String(values.get("service_id")) },
      );
      close();
    } catch {
      /* Render validation. */
    }
  }
  return (
    <Dialog
      title={current ? "Edit detection rule" : "Create detection rule"}
      onClose={close}
    >
      <form onSubmit={submit}>
        <ErrorPanel error={write.error} />
        {current && (
          <p className="small muted">
            Reviewed rule version {current.version}; a conflict requires
            reopening this form.
          </p>
        )}
        <Field label="Rule name">
          <input
            name="name"
            required
            maxLength={120}
            defaultValue={current?.name}
          />
        </Field>
        {!current && <ServiceSelect required />}
        <div className="form-grid">
          <Field label="Log level">
            <select name="level" defaultValue={current?.level ?? "error"}>
              {["debug", "info", "warning", "error", "critical"].map(
                (value) => (
                  <option key={value}>{value}</option>
                ),
              )}
            </select>
          </Field>
          <Field label="Error code (optional)">
            <input
              name="error_code"
              defaultValue={current?.error_code ?? ""}
              maxLength={100}
              pattern="[A-Za-z0-9_.:\-]+"
            />
          </Field>
          <Field label="Threshold">
            <input
              type="number"
              name="threshold"
              min={1}
              max={1000}
              required
              defaultValue={current?.threshold ?? 5}
            />
          </Field>
          <Field label="Rolling window (seconds)">
            <input
              type="number"
              name="window_seconds"
              min={5}
              max={3600}
              required
              defaultValue={current?.window_seconds ?? 60}
            />
          </Field>
          <Field label="Cooldown (seconds)">
            <input
              type="number"
              name="cooldown_seconds"
              min={0}
              max={86400}
              required
              defaultValue={current?.cooldown_seconds ?? 60}
            />
          </Field>
          <Field label="Incident severity">
            <select
              name="incident_severity"
              defaultValue={current?.incident_severity ?? "high"}
            >
              {["critical", "high", "medium", "low"].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </Field>
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            name="enabled"
            defaultChecked={current?.enabled ?? true}
          />
          Rule enabled
        </label>
        <Submit pending={write.isPending} />
      </form>
    </Dialog>
  );
}
export function DetectionList({ incidentId }: { incidentId?: string }) {
  const [offset, setOffset] = useState(0);
  const query = useApi<Page<Detection>>(
    incidentId ? `/incidents/${incidentId}/detections` : "/detections",
    { limit: 20, offset },
  );
  return (
    <>
      <ErrorPanel error={query.error} />
      {query.isPending && <Loading />}
      {query.data && (
        <div className="panel">
          {query.data.items.length ? (
            <DataTable
              label="Detections"
              headers={[
                "Detection",
                "Incident",
                "Service",
                "Matches",
                "Created",
              ]}
            >
              {query.data.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link className="mono" to={`/detections/${item.id}`}>
                      {item.id.slice(0, 8)}
                    </Link>
                  </td>
                  <td>
                    <IncidentLink id={item.incident_id} />
                  </td>
                  <td>
                    <ServiceLink id={item.service_id} />
                  </td>
                  <td>{item.matching_count}</td>
                  <td>{date(item.created_at)}</td>
                </tr>
              ))}
            </DataTable>
          ) : (
            <Empty title="No detection occurrences" />
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
export function Detections() {
  const { user } = useAuth();
  const [tab, setTab] = useState("rules");
  const [offset, setOffset] = useState(0);
  const [edit, setEdit] = useState<Rule | "new" | null>(null);
  const query = useApi<Page<Rule>>(
    "/detection-rules",
    { limit: 20, offset },
    { enabled: tab === "rules" },
  );
  const jobs = useApi<Page<Schema<"app__schemas__detection__JobRead">>>(
    "/detection-jobs",
    { limit: 20, offset },
    { enabled: tab === "jobs" && canAdmin(user) },
  );
  return (
    <>
      <PageTitle
        title="Detection monitoring"
        description="Deterministic rules over accepted structured logs."
        actions={
          canAdmin(user) && (
            <Button variant="primary" onClick={() => setEdit("new")}>
              <Plus size={15} />
              Create rule
            </Button>
          )
        }
      />
      <Notice>
        Threshold counts matching unique logs in a receipt-time rolling window.
        Existing occurrences deduplicate correlated incidents; cooldown gates
        the next incident after resolution. Detection does not use AI.
      </Notice>
      <details className="context-help">
        <summary>How thresholds, windows and cooldowns work</summary>
        <dl className="detail-grid">
          <dt>Threshold</dt>
          <dd>
            The minimum number of unique matching log records needed to trigger.
          </dd>
          <dt>Rolling window</dt>
          <dd>
            A moving interval evaluated using the time logs were received, not
            their source timestamps.
          </dd>
          <dt>Deduplication</dt>
          <dd>
            Correlated matches reuse the recorded occurrence rather than opening
            duplicate incidents.
          </dd>
          <dt>Cooldown</dt>
          <dd>
            The rule's waiting period before another incident can be opened
            after resolution.
          </dd>
        </dl>
      </details>
      <div className="tabs" aria-label="Detection views">
        {["rules", "occurrences", ...(canAdmin(user) ? ["jobs"] : [])].map(
          (value) => (
            <button
              key={value}
              className={tab === value ? "active" : ""}
              aria-pressed={tab === value}
              onClick={() => {
                setTab(value);
                setOffset(0);
              }}
            >
              {value === "jobs"
                ? "Worker jobs"
                : value === "rules"
                  ? "Detection rules"
                  : "Recent detections"}
            </button>
          ),
        )}
      </div>
      {tab === "occurrences" ? (
        <DetectionList />
      ) : tab === "jobs" ? (
        <>
          <ErrorPanel error={jobs.error} />
          {jobs.isPending && <Loading />}
          {jobs.data && (
            <div className="panel">
              <DataTable
                label="Detection jobs"
                headers={["Job", "Status", "Attempts", "Service", "Safe error"]}
              >
                {jobs.data.items.map((item) => (
                  <tr key={item.id}>
                    <td className="mono">{item.id.slice(0, 8)}</td>
                    <td>
                      <Badge value={item.status} />
                    </td>
                    <td>
                      {item.attempts}/{item.max_attempts}
                    </td>
                    <td>
                      <ServiceLink id={item.service_id} />
                    </td>
                    <td>{item.last_error_code ?? "—"}</td>
                  </tr>
                ))}
              </DataTable>
              <Pagination
                offset={offset}
                total={jobs.data.total}
                onChange={setOffset}
              />
            </div>
          )}
        </>
      ) : (
        <>
          <ErrorPanel error={query.error} />
          {query.isPending && <Loading />}
          {query.data && (
            <div className="panel">
              {query.data.items.length ? (
                <DataTable
                  label="Detection rules"
                  headers={[
                    "Rule",
                    "Service",
                    "Trigger",
                    "Window",
                    "Cooldown",
                    "Severity",
                    "State",
                    "Actions",
                  ]}
                >
                  {query.data.items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <strong>{item.name}</strong>
                        <span className="small muted block">
                          {item.error_code ?? "Any error code"}
                        </span>
                      </td>
                      <td>
                        <ServiceLink id={item.service_id} />
                      </td>
                      <td>
                        {item.threshold} × {item.level}
                      </td>
                      <td>{item.window_seconds}s</td>
                      <td>{item.cooldown_seconds}s</td>
                      <td>
                        <Badge value={item.incident_severity} />
                      </td>
                      <td>
                        <Badge value={item.enabled ? "enabled" : "disabled"} />
                      </td>
                      <td>
                        {canAdmin(user) ? (
                          <Button onClick={() => setEdit(item)}>Edit</Button>
                        ) : (
                          <span className="muted small">Read only</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </DataTable>
              ) : (
                <Empty title="No detection rules" />
              )}
              <Pagination
                offset={offset}
                total={query.data.total}
                onChange={setOffset}
              />
            </div>
          )}
        </>
      )}
      {edit && (
        <RuleForm
          current={edit === "new" ? undefined : edit}
          close={() => setEdit(null)}
        />
      )}
    </>
  );
}
export function DetectionDetail() {
  const { id = "" } = useParams();
  const query = useApi<Detection>(`/detections/${id}`);
  return (
    <>
      <PageTitle
        title="Detection evidence"
        description="Stored trigger configuration and correlated log evidence."
      />
      <ErrorPanel error={query.error} />
      {query.isPending && <Loading />}
      {query.data && (
        <>
          <Panel title="Recorded occurrence">
            <div className="actions">
              <Badge value="deterministic" />
              <IncidentLink id={query.data.incident_id} />
              <span>{query.data.matching_count} matching logs</span>
            </div>
            <p className="muted">
              Window {date(query.data.window_start)} →{" "}
              {date(query.data.window_end)} · Rule version{" "}
              {query.data.rule_version}
            </p>
            <details>
              <summary>Immutable rule snapshot</summary>
              <Json value={query.data.rule_snapshot} />
            </details>
          </Panel>
          <h2 className="section-title">Linked logs</h2>
          <Logs detectionId={id} />
        </>
      )}
    </>
  );
}
