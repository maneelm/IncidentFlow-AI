import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useApi } from "../lib/api/query";
import { shortId } from "../lib/formatting";
import type { Assignee, Page, Service } from "../types/models";
import { Identifier } from "./RecordDetails";
export function ServiceLink({
  id,
  service: supplied,
}: {
  id: string;
  service?: Service;
}) {
  const cache = useQueryClient();
  const page = cache
    .getQueriesData<Page<Service>>({ queryKey: ["api", "/services"] })
    .find(([, data]) => data?.items.some((item) => item.id === id));
  const initial = supplied ?? page?.[1]?.items.find((item) => item.id === id);
  const service = useApi<Service>(`/services/${id}`, undefined, {
    enabled: !supplied,
    initialData: initial,
    initialDataUpdatedAt: page
      ? cache.getQueryState(page[0])?.dataUpdatedAt
      : undefined,
  });
  const current = supplied ?? service.data;
  return (
    <Link
      className="service-link"
      to={`/services/${id}`}
      title={`${current?.name ?? "Service"} · ${id}`}
    >
      <span className="service-name">{current?.name ?? shortId(id)}</span>
      {current?.environment === "simulation" && (
        <span className="service-scope">Simulation</span>
      )}
    </Link>
  );
}
export function PersonName({ id }: { id: string | null }) {
  const directory = useApi<Page<Assignee>>(
    "/directory/users",
    { limit: 100, offset: 0 },
    { enabled: !!id },
  );
  if (!id) return <span className="muted">Unassigned</span>;
  const person = directory.data?.items.find((item) => item.id === id);
  return (
    <span className="person-name">
      {person && <span>{person.display_name}</span>}
      <Identifier value={id} name="user ID" />
    </span>
  );
}
export function IncidentLink({
  id,
  children,
}: {
  id: string;
  children?: React.ReactNode;
}) {
  return (
    <Link className="mono" to={`/incidents/${id}`} title={`Incident ${id}`}>
      {children ?? shortId(id)}
    </Link>
  );
}
