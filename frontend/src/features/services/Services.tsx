import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { useAuth } from "../auth/context";
import { api, ApiError } from "../../lib/api/client";
import { useApi, useWrite } from "../../lib/api/query";
import { canAdmin } from "../../lib/permissions";
import { date } from "../../lib/formatting";
import { ServiceLink } from "../../components/ResourceLinks";
import { Identifier } from "../../components/RecordDetails";
import type { Page, Schema, Service, SyntheticState } from "../../types/models";
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

function ServiceForm({
  current,
  close,
}: {
  current?: Service;
  close: () => void;
}) {
  const mutation = useWrite<Service, Schema<"ServiceCreate">>((body) =>
    current
      ? api.patch(`/services/${current.id}`, body)
      : api.post("/services", body),
  );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    try {
      await mutation.mutateAsync({
        name: String(values.get("name")),
        description: String(values.get("description")),
        environment: String(values.get("environment")),
      });
      close();
    } catch {
      /* Display the backend error. */
    }
  }
  return (
    <Dialog
      title={current ? "Edit service" : "Register service"}
      onClose={close}
    >
      <form onSubmit={submit}>
        <ErrorPanel error={mutation.error} />
        <Field
          label="Service name"
          hint="Lowercase letters, numbers, dots, hyphens or underscores."
        >
          <input
            name="name"
            pattern="[a-z0-9][a-z0-9._\-]*"
            required
            maxLength={100}
            defaultValue={current?.name}
          />
        </Field>
        <Field label="Environment">
          <input
            name="environment"
            required
            maxLength={50}
            defaultValue={current?.environment}
            placeholder="simulation"
          />
        </Field>
        <Field label="Description">
          <textarea
            name="description"
            maxLength={10000}
            defaultValue={current?.description}
          />
        </Field>
        <Submit pending={mutation.isPending} />
      </form>
    </Dialog>
  );
}
export function SyntheticStatePanel({ serviceId }: { serviceId: string }) {
  const { user } = useAuth();
  const state = useApi<SyntheticState>(
    `/services/${serviceId}/synthetic-state`,
  );
  const [setup, setSetup] = useState(false);
  const write = useWrite<SyntheticState, Schema<"ScenarioInput">>((body) =>
    api.post(`/services/${serviceId}/synthetic-state`, body),
  );
  const missing = state.error instanceof ApiError && state.error.status === 404;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    try {
      await write.mutateAsync({
        scenario: String(
          values.get("scenario"),
        ) as Schema<"ScenarioInput">["scenario"],
        expected_version: state.data?.version ?? 0,
      });
      setSetup(false);
    } catch {
      /* Preserve conflict details. */
    }
  }
  return (
    <Panel
      title="Synthetic operational state"
      aside={<Badge value="synthetic" synthetic />}
    >
      <Notice>
        Isolated fixture data. This does not measure or change the registered
        service's real infrastructure.
      </Notice>
      {state.isPending && <Loading />}
      {!missing && <ErrorPanel error={state.error} />}
      <ErrorPanel error={write.error} />
      {missing && (
        <p>No synthetic scenario has been initialized for this service.</p>
      )}
      {state.data && (
        <>
          <div className="actions">
            <Badge value={state.data.snapshot.health} />
            <span className="small muted">
              Fixture version {state.data.version} · Updated{" "}
              {date(state.data.updated_at)}
            </span>
          </div>
          <Json value={state.data.snapshot} />
        </>
      )}
      {canAdmin(user) && (
        <Button onClick={() => setSetup(true)}>
          {missing
            ? "Initialize synthetic scenario"
            : "Reset synthetic scenario"}
        </Button>
      )}
      {setup && (
        <Dialog title="Set synthetic scenario" onClose={() => setSetup(false)}>
          <Notice tone="warning">
            Resetting changes the fixture version and invalidates outstanding
            approvals. Real service configuration is unaffected.
          </Notice>
          <form onSubmit={submit}>
            <Field label="Scenario">
              <select name="scenario">
                {[
                  "database_exhaustion",
                  "service_unhealthy",
                  "latency_degradation",
                  "healthy",
                  "dependency_outage",
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Submit pending={write.isPending}>Confirm synthetic setup</Submit>
          </form>
        </Dialog>
      )}
    </Panel>
  );
}
export function Services() {
  const { user } = useAuth();
  const [offset, setOffset] = useState(0);
  const [create, setCreate] = useState(false);
  const query = useApi<Page<Service>>("/services", { limit: 20, offset });
  return (
    <>
      <PageTitle
        title="Services"
        description="Registered service ownership and isolated synthetic scenarios."
        actions={
          canAdmin(user) && (
            <Button variant="primary" onClick={() => setCreate(true)}>
              <Plus size={15} />
              Register service
            </Button>
          )
        }
      />
      <ErrorPanel error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {query.data && (
        <div className="panel">
          {query.data.items.length ? (
            <DataTable
              label="Services"
              headers={["Service", "Environment", "Description", "Registered"]}
            >
              {query.data.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <ServiceLink id={item.id} service={item} />
                    <Identifier value={item.id} name="service ID" />
                  </td>
                  <td>
                    <Badge value={item.environment} />
                  </td>
                  <td className="muted">{item.description || "—"}</td>
                  <td className="muted nowrap">{date(item.created_at)}</td>
                </tr>
              ))}
            </DataTable>
          ) : (
            <Empty title="No registered services" />
          )}
          <Pagination
            offset={offset}
            total={query.data.total}
            onChange={setOffset}
          />
        </div>
      )}
      {create && <ServiceForm close={() => setCreate(false)} />}
    </>
  );
}
export function ServiceDetail() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const [edit, setEdit] = useState(false);
  const query = useApi<Service>(`/services/${id}`);
  return (
    <>
      <ErrorPanel error={query.error} />
      {query.isPending && <Loading />}
      {query.data && (
        <>
          <PageTitle
            title={query.data.name}
            description={query.data.description}
            actions={
              canAdmin(user) && (
                <Button onClick={() => setEdit(true)}>Edit service</Button>
              )
            }
          />
          <div className="actions section-gap">
            <Badge value={query.data.environment} />
            <Identifier value={query.data.id} name="service ID" />
            <Link to={`/incidents?service_id=${id}`} className="inline-link">
              View service incidents →
            </Link>
          </div>
          <SyntheticStatePanel serviceId={id} />
          {edit && (
            <ServiceForm current={query.data} close={() => setEdit(false)} />
          )}
        </>
      )}
    </>
  );
}
