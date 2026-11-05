import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Plus, SlidersHorizontal } from "lucide-react";
import { useAuth } from "../auth/context";
import { api } from "../../lib/api/client";
import { useApi, useWrite } from "../../lib/api/query";
import { canWrite } from "../../lib/permissions";
import { date, label } from "../../lib/formatting";
import type {
  Incident,
  IncidentCreate,
  Page,
  Params,
} from "../../types/models";
import { AssigneeSelect, ServiceSelect } from "../../components/Selectors";
import { ServiceLink, PersonName } from "../../components/ResourceLinks";
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  Empty,
  ErrorPanel,
  Field,
  Loading,
  PageTitle,
  Pagination,
  Submit,
} from "../../components/ui";

export function IncidentForm({
  current,
  close,
}: {
  current?: Incident;
  close: () => void;
}) {
  const navigate = useNavigate();
  const mutation = useWrite<
    Incident,
    | IncidentCreate
    | {
        expected_version: number;
        title: string;
        description: string;
        severity: Incident["severity"];
      }
  >((body) =>
    current
      ? api.patch(`/incidents/${current.id}`, body)
      : api.post("/incidents", body),
  );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const shared = {
      title: String(values.get("title")),
      description: String(values.get("description")),
      severity: String(values.get("severity")) as Incident["severity"],
    };
    try {
      const result = await mutation.mutateAsync(
        current
          ? { ...shared, expected_version: current.version }
          : {
              ...shared,
              service_id: String(values.get("service_id")),
              assignee_id: values.get("assignee_id")
                ? String(values.get("assignee_id"))
                : null,
            },
      );
      close();
      if (!current) navigate(`/incidents/${result.id}`);
    } catch {
      /* The error panel retains validation/conflict details. */
    }
  }
  return (
    <Dialog
      title={current ? `Edit ${current.incident_number}` : "Create incident"}
      onClose={close}
    >
      <form onSubmit={submit}>
        <ErrorPanel error={mutation.error} />
        {current && (
          <p className="small muted">
            Editing reviewed version {current.version}. Conflicts require
            closing, refreshing and reopening this form.
          </p>
        )}
        <Field label="Incident title">
          <input
            name="title"
            required
            maxLength={200}
            defaultValue={current?.title}
            placeholder="Describe the observed problem"
          />
        </Field>
        <Field label="Description">
          <textarea
            name="description"
            required
            maxLength={50000}
            rows={5}
            defaultValue={current?.description}
          />
        </Field>
        <Field label="Severity">
          <select name="severity" defaultValue={current?.severity ?? "medium"}>
            {["critical", "high", "medium", "low"].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </Field>
        {!current && (
          <>
            <ServiceSelect required />
            <AssigneeSelect />
          </>
        )}
        <div className="form-footer">
          <Button type="button" onClick={close}>
            Cancel
          </Button>
          <Submit pending={mutation.isPending}>
            {current ? "Save changes" : "Create incident"}
          </Submit>
        </div>
      </form>
    </Dialog>
  );
}
export function IncidentList() {
  const { user } = useAuth();
  const [params] = useSearchParams();
  const [create, setCreate] = useState(false);
  const [filters, setFilters] = useState<Params>({
    offset: 0,
    limit: 20,
    sort_by: "created_at",
    sort_order: "desc",
    service_id: params.get("service_id") ?? undefined,
  });
  const query = useApi<Page<Incident>>("/incidents", filters);
  const change = (name: string, value: string) =>
    setFilters({ ...filters, [name]: value, offset: 0 });
  return (
    <>
      <PageTitle
        title="Incidents"
        description="Track impact, investigate evidence, and coordinate the response."
        actions={
          canWrite(user) && (
            <Button variant="primary" onClick={() => setCreate(true)}>
              <Plus size={16} />
              Create incident
            </Button>
          )
        }
      />
      <div className="filter-bar">
        <span className="filter-icon">
          <SlidersHorizontal size={16} />
        </span>
        <Field label="Status">
          <select
            value={String(filters.status ?? "")}
            onChange={(event) => change("status", event.target.value)}
          >
            <option value="">All statuses</option>
            {[
              "triggered",
              "investigating",
              "mitigated",
              "resolved",
              "closed",
            ].map((value) => (
              <option key={value} value={value}>
                {label(value)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Severity">
          <select
            value={String(filters.severity ?? "")}
            onChange={(event) => change("severity", event.target.value)}
          >
            <option value="">All severities</option>
            {["critical", "high", "medium", "low"].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </Field>
        <ServiceSelect
          value={String(filters.service_id ?? "")}
          onChange={(value) => change("service_id", value)}
        />
        <Field label="Assignee UUID">
          <input
            placeholder="Exact user UUID"
            type="text"
            value={String(filters.assignee_id ?? "")}
            onChange={(event) => change("assignee_id", event.target.value)}
          />
        </Field>
        <Field label="Sort">
          <select
            value={String(filters.sort_by)}
            onChange={(event) => change("sort_by", event.target.value)}
          >
            <option value="created_at">Created time</option>
            <option value="updated_at">Updated time</option>
            <option value="severity">Severity</option>
            <option value="incident_number">Incident number</option>
          </select>
        </Field>
        <Field label="Direction">
          <select
            value={String(filters.sort_order)}
            onChange={(event) => change("sort_order", event.target.value)}
          >
            <option value="desc">Descending</option>
            <option value="asc">Ascending</option>
          </select>
        </Field>
      </div>
      <p className="small muted">
        Server-side filters · The incident API supports exact fields, not
        free-text title search.
      </p>
      <ErrorPanel error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {query.data && (
        <div className="panel">
          {query.data.items.length ? (
            <DataTable
              label="Incidents"
              headers={[
                "Incident",
                "Severity",
                "Status",
                "Service",
                "Assignee",
                "Created",
              ]}
            >
              {query.data.items.map((item) => (
                <tr key={item.id}>
                  <td className="primary-cell">
                    <Link to={`/incidents/${item.id}`}>
                      <span className="mono muted small">
                        {item.incident_number}
                      </span>
                      <strong>{item.title}</strong>
                    </Link>
                  </td>
                  <td>
                    <Badge value={item.severity} />
                  </td>
                  <td>
                    <Badge value={item.status} />
                  </td>
                  <td>
                    <ServiceLink id={item.service_id} />
                  </td>
                  <td>
                    <PersonName id={item.assignee_id} />
                  </td>
                  <td className="muted nowrap">{date(item.created_at)}</td>
                </tr>
              ))}
            </DataTable>
          ) : (
            <Empty title="No incidents match these filters" />
          )}
          <Pagination
            offset={Number(filters.offset)}
            total={query.data.total}
            onChange={(offset) => setFilters({ ...filters, offset })}
          />
        </div>
      )}
      {create && <IncidentForm close={() => setCreate(false)} />}
    </>
  );
}
