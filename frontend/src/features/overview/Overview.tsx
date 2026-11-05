import {
  Activity,
  ArrowRight,
  Boxes,
  CheckCheck,
  Clock3,
  RefreshCw,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  BarChart,
  Bar,
  LabelList,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useApi } from "../../lib/api/query";
import { date, label, shortId } from "../../lib/formatting";
import type { Overview as OverviewData } from "../../types/models";
import {
  Badge,
  Button,
  DataTable,
  Empty,
  ErrorPanel,
  Loading,
  PageTitle,
  Panel,
} from "../../components/ui";
import { IncidentLink, ServiceLink } from "../../components/ResourceLinks";
import { Timestamp } from "../../components/RecordDetails";

export function IncidentChart({
  groups,
  total,
}: {
  groups: { name: string; count: number }[];
  total: number;
}) {
  if (!total)
    return (
      <Empty title="No incident distribution yet">
        Charts will appear when incidents are recorded.
      </Empty>
    );
  const colors: Record<string, string> = {
    critical: "#f27885",
    high: "#e9a45f",
    medium: "#e8ce79",
    low: "#7b9dca",
  };
  const data = ["critical", "high", "medium", "low"].map((name) => ({
    name: label(name),
    count: groups.find((item) => item.name === name)?.count ?? 0,
    color: colors[name],
  }));
  return (
    <>
      <div
        className="chart"
        role="img"
        aria-label={`Incident severity distribution. ${data.map((item) => `${item.name}: ${item.count}`).join(", ")}`}
      >
        <ResponsiveContainer width="100%" height={190} minWidth={0}>
          <BarChart
            data={data}
            margin={{ top: 15, right: 10, left: -22, bottom: 0 }}
          >
            <XAxis
              dataKey="name"
              tick={{ fill: "#a3aab7", fontSize: 12 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              allowDecimals={false}
              tick={{ fill: "#7f8999", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: "#ffffff05" }}
              contentStyle={{
                background: "#20262e",
                border: "1px solid #39414c",
                borderRadius: 6,
              }}
            />
            <Bar
              dataKey="count"
              name="Incidents"
              barSize={42}
              radius={[4, 4, 0, 0]}
              isAnimationActive={false}
            >
              <LabelList
                dataKey="count"
                position="top"
                fill="#b7c1ce"
                fontSize={12}
              />
              {data.map((item) => (
                <Cell key={item.name} fill={item.color} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}
export function Overview() {
  const query = useApi<OverviewData>("/dashboard/overview");
  const data = query.data;
  return (
    <>
      <PageTitle
        title="Operations overview"
        description="Your incident response workspace, at a glance."
        actions={
          <Button
            onClick={() => void query.refetch()}
            disabled={query.isFetching}
          >
            <RefreshCw size={15} />
            {query.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />
      <ErrorPanel error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {data && (
        <>
          <div className="overview-context">
            <span className="live-dot" />
            Database records{" "}
            <span className="muted" role="status">
              · Updated <Timestamp value={data.observed_at} /> · Includes
              labeled simulations
            </span>
          </div>
          <div className="stats-grid">
            {[
              {
                title: "Total incidents",
                value: data.total_incidents,
                note: "All recorded incidents",
                icon: Activity,
              },
              {
                title: "Active incidents",
                value: data.active_incidents,
                note: "Triggered, investigating or mitigated",
                icon: Clock3,
              },
              {
                title: "Registered services",
                value: data.total_services,
                note: "Services in the catalog",
                icon: Boxes,
              },
              {
                title: "Awaiting approval",
                value: data.pending_approvals,
                note: "Human review required",
                icon: CheckCheck,
              },
            ].map(({ title, value, note, icon: Icon }) => (
              <div className="stat-card" key={title}>
                <div className="stat-label">
                  {title}
                  <Icon size={17} />
                </div>
                <strong>{value.toLocaleString()}</strong>
                <small>{note}</small>
              </div>
            ))}
          </div>
          <div className="overview-charts">
            <Panel
              title="Incidents by severity"
              aside={
                <span className="small muted">All recorded incidents</span>
              }
            >
              <IncidentChart
                groups={data.severity}
                total={data.total_incidents}
              />
            </Panel>
            <Panel
              title="Incident lifecycle"
              aside={<span className="small muted">Current status</span>}
            >
              <div className="status-bars">
                {[
                  "triggered",
                  "investigating",
                  "mitigated",
                  "resolved",
                  "closed",
                ].map((status) => {
                  const count =
                    data.statuses.find((item) => item.name === status)?.count ??
                    0;
                  return (
                    <div key={status}>
                      <div>
                        <span>
                          <i className={`status-dot ${status}`} />
                          {label(status)}
                        </span>
                        <strong>{count}</strong>
                      </div>
                      <div className="bar-track">
                        <span
                          className={status}
                          style={{
                            width: `${data.total_incidents ? (count / data.total_incidents) * 100 : 0}%`,
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </Panel>
          </div>
          <div className="overview-lower">
            <Panel
              title="Recent detections"
              aside={
                <Link className="small inline-link" to="/detections">
                  View all <ArrowRight size={13} />
                </Link>
              }
            >
              {data.recent_detections.length ? (
                <DataTable
                  label="Recent detections"
                  headers={["Incident", "Service", "Matches", "Detected"]}
                >
                  {data.recent_detections.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <IncidentLink id={item.incident_id} />
                      </td>
                      <td>
                        <ServiceLink id={item.service_id} />
                      </td>
                      <td>
                        <span className="count-pill">
                          {item.matching_count}
                        </span>
                      </td>
                      <td className="muted nowrap">
                        <Timestamp value={item.created_at} />
                      </td>
                    </tr>
                  ))}
                </DataTable>
              ) : (
                <Empty title="No detections yet">
                  Rules produce records when accepted logs cross their
                  thresholds.
                </Empty>
              )}
            </Panel>
            <Panel
              title="Approval queue"
              aside={
                <Link
                  className="small"
                  to="/resolutions?status=pending_approval"
                >
                  View all
                </Link>
              }
            >
              {data.approval_queue.length ? (
                <div className="record-list">
                  {data.approval_queue.map((item) => (
                    <Link
                      className="record-item"
                      to={`/resolutions/${item.id}`}
                      key={item.id}
                    >
                      <div>
                        <strong>{label(item.action_id)}</strong>
                        <span className="muted small">
                          Deadline {date(item.approval_deadline)}
                        </span>
                      </div>
                      <Badge value="pending_approval" />
                    </Link>
                  ))}
                </div>
              ) : (
                <Empty title="Review queue is clear">
                  No proposals currently need a human decision. New submitted
                  proposals appear here.
                </Empty>
              )}
            </Panel>
          </div>
          <div className="overview-lower">
            <Panel
              title="Investigation activity"
              aside={
                <Link className="small" to="/investigations">
                  View all
                </Link>
              }
            >
              {data.recent_investigations.length ? (
                <div className="record-list">
                  {data.recent_investigations.map((item) => (
                    <Link
                      className="record-item"
                      to={`/investigations/${item.id}`}
                      key={item.id}
                    >
                      <div>
                        <strong className="mono">{shortId(item.id)}</strong>
                        <span className="small muted">
                          {item.provider === "fake"
                            ? "Synthetic provider"
                            : `${item.provider} / ${item.model}`}
                        </span>
                      </div>
                      <Badge value={item.status} />
                    </Link>
                  ))}
                </div>
              ) : (
                <Empty title="No investigations yet" />
              )}
            </Panel>
            <Panel title="Latest recorded activity">
              <div className="activity-list">
                {data.recent_activity.length ? (
                  data.recent_activity.slice(0, 7).map((item) => (
                    <Link
                      to={`/incidents/${item.incident_id}?tab=timeline`}
                      key={`${item.source_type}:${item.source_id}`}
                    >
                      <span className="activity-mark" />
                      <div>
                        <strong>{label(item.event_type)}</strong>
                        <span>
                          {item.source_type} · {date(item.occurred_at)}
                        </span>
                      </div>
                      <ArrowRight size={14} />
                    </Link>
                  ))
                ) : (
                  <Empty title="No recorded activity" />
                )}
              </div>
            </Panel>
          </div>
          <p className="small muted">
            Counts are read-only PostgreSQL aggregates. These are incident
            records, not live production health metrics.
          </p>
        </>
      )}
    </>
  );
}
