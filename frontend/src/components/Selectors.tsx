import { useState } from "react";
import { useApi } from "../lib/api/query";
import type { Assignee, Page, Service } from "../types/models";
import { Button, ErrorPanel, Field } from "./ui";

export function ServiceSelect({
  value,
  onChange,
  required = false,
  name = "service_id",
}: {
  value?: string;
  onChange?: (value: string) => void;
  required?: boolean;
  name?: string;
}) {
  const [offset, setOffset] = useState(0);
  const services = useApi<Page<Service>>("/services", { limit: 100, offset });
  return (
    <>
      <Field label="Service">
        <select
          name={name}
          value={value}
          onChange={
            onChange ? (event) => onChange(event.target.value) : undefined
          }
          required={required}
        >
          <option value="">
            {required ? "Select a service" : "All services"}
          </option>
          {value &&
            !services.data?.items.some((service) => service.id === value) && (
              <option value={value}>Selected service · {value}</option>
            )}
          {services.data?.items.map((service) => (
            <option key={service.id} value={service.id}>
              {service.name}
            </option>
          ))}
        </select>
      </Field>
      {(services.data?.total ?? 0) > 100 && (
        <div className="actions small">
          <Button
            type="button"
            disabled={!offset}
            onClick={() => setOffset(offset - 100)}
          >
            Previous services
          </Button>
          <Button
            type="button"
            disabled={offset + 100 >= (services.data?.total ?? 0)}
            onClick={() => setOffset(offset + 100)}
          >
            More services
          </Button>
        </div>
      )}
      <ErrorPanel error={services.error} />
    </>
  );
}
export function AssigneeSelect({
  current,
  name = "assignee_id",
}: {
  current?: string | null;
  name?: string;
}) {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState(current ?? "");
  const users = useApi<Page<Assignee>>("/directory/users", {
    limit: 100,
    offset,
  });
  return (
    <>
      <Field label="Assignee">
        <select
          name={name}
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
        >
          <option value="">Unassigned</option>
          {selected &&
            !users.data?.items.some((user) => user.id === selected) && (
              <option value={selected}>{selected}</option>
            )}
          {users.data?.items.map((user) => (
            <option key={user.id} value={user.id}>
              {user.display_name} · {user.role}
            </option>
          ))}
        </select>
      </Field>
      {(users.data?.total ?? 0) > 100 && (
        <div className="actions small">
          <Button
            type="button"
            disabled={!offset}
            onClick={() => setOffset(offset - 100)}
          >
            Previous people
          </Button>
          <Button
            type="button"
            disabled={offset + 100 >= (users.data?.total ?? 0)}
            onClick={() => setOffset(offset + 100)}
          >
            More people
          </Button>
        </div>
      )}
      <ErrorPanel error={users.error} />
    </>
  );
}
