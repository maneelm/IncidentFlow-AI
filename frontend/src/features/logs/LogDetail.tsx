import { Link, useParams } from "react-router-dom";
import { useApi } from "../../lib/api/query";
import { date } from "../../lib/formatting";
import type { Log } from "../../types/models";
import { ServiceLink } from "../../components/ResourceLinks";
import { Identifier } from "../../components/RecordDetails";
import {
  Badge,
  ErrorPanel,
  Json,
  Loading,
  PageTitle,
  Panel,
} from "../../components/ui";
export function LogDetail() {
  const { id = "" } = useParams();
  const query = useApi<Log>(`/logs/${id}`);
  return (
    <>
      <Link className="back-link" to="/logs">
        ← Log explorer
      </Link>
      <PageTitle
        title="Structured log record"
        description="Source text is untrusted and rendered without HTML."
      />
      <ErrorPanel error={query.error} />
      {query.isPending && <Loading />}
      {query.data && (
        <Panel title={query.data.error_code ?? "Log observation"}>
          <div className="actions">
            <Identifier value={query.data.id} name="log ID" />
            <Badge value={query.data.level} />
            {query.data.metadata.synthetic === true && (
              <Badge value="synthetic" synthetic />
            )}
            <ServiceLink id={query.data.service_id} />
          </div>
          <p className="log-full">{query.data.message}</p>
          <p className="muted">
            Source time {date(query.data.timestamp)} · Ingested{" "}
            {date(query.data.ingested_at)}
          </p>
          <p className="mono small wrap">
            Trace: {query.data.trace_id ?? "—"} · Request:{" "}
            {query.data.request_id ?? "—"}
          </p>
          <details>
            <summary>Structured metadata</summary>
            <Json value={query.data.metadata} />
          </details>
        </Panel>
      )}
    </>
  );
}
