import type { ReactNode } from "react";
import type { Schema } from "../../types/models";
import { Badge, Json, Notice } from "../../components/ui";

type Metric = {
  key: keyof Schema<"SyntheticSnapshot">;
  name: string;
  kind: "health" | "condition" | "rate" | "milliseconds" | "count" | "boolean";
};

const metrics: Metric[] = [
  { key: "health", name: "Service health", kind: "health" },
  { key: "request_error_rate", name: "Request error rate", kind: "rate" },
  { key: "p50_ms", name: "p50 latency", kind: "milliseconds" },
  { key: "p95_ms", name: "p95 latency", kind: "milliseconds" },
  {
    key: "active_connections",
    name: "Active database connections",
    kind: "count",
  },
  { key: "waiting_requests", name: "Waiting requests", kind: "count" },
  { key: "pool_size", name: "Connection-pool size", kind: "count" },
  { key: "timeout_indicator", name: "Database timeout", kind: "boolean" },
  {
    key: "dependency_condition",
    name: "Dependency condition",
    kind: "condition",
  },
];
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const percent = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumSignificantDigits: 3,
});

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function metricValue(metric: Metric, value: unknown): ReactNode | null {
  if (metric.kind === "boolean")
    return typeof value === "boolean" ? (value ? "Yes" : "No") : null;
  if (metric.kind === "health" || metric.kind === "condition") {
    const allowed =
      metric.kind === "health"
        ? ["healthy", "degraded", "unavailable"]
        : ["healthy", "database_pressure", "slow", "unavailable"];
    return typeof value === "string" && allowed.includes(value) ? (
      <Badge value={value} />
    ) : null;
  }
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0)
    return null;
  if (metric.kind === "rate") return value <= 1 ? percent.format(value) : null;
  if (metric.kind === "count" && !Number.isInteger(value)) return null;
  return `${number.format(value)}${metric.kind === "milliseconds" ? " ms" : ""}`;
}

// Read only known snapshot fields; other action shapes remain inspectable as raw data.
export function ExecutionComparison({ result }: { result: unknown }) {
  const execution = record(result);
  const before = record(execution.before);
  const after = record(execution.after);
  const rows = metrics.map((metric) => ({
    ...metric,
    before: metricValue(metric, before[metric.key]),
    after: metricValue(metric, after[metric.key]),
  }));
  const missing = <span className="muted">Not recorded</span>;
  return (
    <div className="execution-result">
      <Notice>
        These results describe synthetic service state only. No production
        infrastructure was modified.
      </Notice>
      {rows.some((row) => row.before !== null || row.after !== null) ? (
        <table className="execution-comparison">
          <caption>Synthetic service state comparison</caption>
          <thead>
            <tr>
              <th scope="col">Metric</th>
              <th scope="col">Before</th>
              <th scope="col">After</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <th scope="row">{row.name}</th>
                <td>{row.before ?? missing}</td>
                <td>{row.after ?? missing}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="small muted">
          No comparable synthetic state metrics were recorded for this action.
          Inspect the raw execution data for available results.
        </p>
      )}
      <details className="execution-raw">
        <summary>View raw execution data</summary>
        <Json value={result} />
      </details>
    </div>
  );
}
