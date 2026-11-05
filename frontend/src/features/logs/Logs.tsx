import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import { useApi } from "../../lib/api/query";
import { date, inputDate } from "../../lib/formatting";
import { Identifier, Timestamp } from "../../components/RecordDetails";
import type { Log, Page, Params } from "../../types/models";
import { ServiceSelect } from "../../components/Selectors";
import { ServiceLink } from "../../components/ResourceLinks";
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
  PageTitle,
  Pagination,
} from "../../components/ui";

export function Logs({
  incidentId,
  detectionId,
}: {
  incidentId?: string;
  detectionId?: string;
}) {
  const [filters, setFilters] = useState<Params>({
    limit: 20,
    offset: 0,
    incident_id: incidentId,
    detection_id: detectionId,
  });
  const [selected, setSelected] = useState<Log | null>(null);
  const query = useApi<Page<Log>>("/logs", filters);
  function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    setFilters({
      ...(Object.fromEntries(values) as Record<string, string>),
      limit: 20,
      offset: 0,
      incident_id: incidentId,
      detection_id: detectionId,
      timestamp_from: inputDate(values.get("timestamp_from")),
      timestamp_to: inputDate(values.get("timestamp_to")),
    });
  }
  return (
    <>
      {!incidentId && !detectionId && (
        <PageTitle
          title="Log explorer"
          description="Explore bounded structured logs and trace correlated failures."
        />
      )}
      <form className="filter-bar log-filters" onSubmit={filter}>
        <ServiceSelect />
        <Field label="Level">
          <select name="level">
            <option value="">All levels</option>
            {["debug", "info", "warning", "error", "critical"].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </Field>
        <Field label="Message search">
          <input
            name="search"
            minLength={2}
            maxLength={100}
            placeholder="Search log messages"
          />
        </Field>
        <Field label="Trace ID">
          <input name="trace_id" maxLength={128} />
        </Field>
        <Field label="Request ID">
          <input name="request_id" maxLength={128} />
        </Field>
        <Field label="From">
          <input name="timestamp_from" type="datetime-local" />
        </Field>
        <Field label="To">
          <input name="timestamp_to" type="datetime-local" />
        </Field>
        <Button type="submit">
          <Search size={15} />
          Apply filters
        </Button>
      </form>
      <p className="small muted">
        Times use your local timezone. Error codes appear in results; this API
        has no error-code filter.
      </p>
      <ErrorPanel error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {query.data && (
        <div className="panel">
          {query.data.items.length ? (
            <DataTable
              label="Logs"
              headers={[
                "Timestamp",
                "Level",
                "Service",
                "Message",
                "Correlation",
              ]}
            >
              {query.data.items.map((item) => (
                <tr key={item.id}>
                  <td className="nowrap muted">
                    <Timestamp value={item.timestamp} />
                  </td>
                  <td>
                    <Badge value={item.level} />
                    {item.metadata.synthetic === true && (
                      <Badge value="synthetic" synthetic />
                    )}
                  </td>
                  <td>
                    <ServiceLink id={item.service_id} />
                  </td>
                  <td className="log-message">
                    <button
                      className="text-button"
                      onClick={() => setSelected(item)}
                    >
                      {item.error_code && (
                        <span className="mono code-label">
                          {item.error_code}
                        </span>
                      )}
                      <span>{item.message}</span>
                    </button>
                  </td>
                  <td className="mono small muted">
                    {item.trace_id && (
                      <span className="correlation">
                        <span>Trace</span>
                        <Identifier value={item.trace_id} name="trace ID" />
                      </span>
                    )}
                    {item.request_id && (
                      <span className="correlation">
                        <span>Request</span>
                        <Identifier value={item.request_id} name="request ID" />
                      </span>
                    )}
                    {!item.trace_id && !item.request_id && "—"}
                  </td>
                </tr>
              ))}
            </DataTable>
          ) : (
            <Empty title="No logs found">
              Adjust the filters or ingest logs using the scoped producer API.
            </Empty>
          )}
          <Pagination
            offset={Number(filters.offset)}
            total={query.data.total}
            onChange={(offset) => setFilters({ ...filters, offset })}
          />
        </div>
      )}
      {selected && (
        <Dialog title="Log detail" onClose={() => setSelected(null)}>
          <div className="actions">
            <Badge value={selected.level} />
            {selected.metadata.synthetic === true && (
              <Badge value="synthetic" synthetic />
            )}
          </div>
          <div className="section-gap">
            <Identifier value={selected.id} name="log ID" />
          </div>
          <p className="log-full">{selected.message}</p>
          <dl className="detail-grid">
            <dt>Error code</dt>
            <dd>{selected.error_code ?? "—"}</dd>
            <dt>Trace ID</dt>
            <dd className="mono wrap">{selected.trace_id ?? "—"}</dd>
            <dt>Request ID</dt>
            <dd className="mono wrap">{selected.request_id ?? "—"}</dd>
            <dt>Source timestamp</dt>
            <dd>{date(selected.timestamp)}</dd>
            <dt>Ingested</dt>
            <dd>{date(selected.ingested_at)}</dd>
          </dl>
          <details>
            <summary>Structured metadata</summary>
            <Json value={selected.metadata} />
          </details>
          <Link to={`/services/${selected.service_id}`}>Open service</Link>
        </Dialog>
      )}
    </>
  );
}
