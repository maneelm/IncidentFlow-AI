import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { useAuth } from "../auth/context";
import { api } from "../../lib/api/client";
import { useApi, useWrite } from "../../lib/api/query";
import { canAdmin } from "../../lib/permissions";
import { date } from "../../lib/formatting";
import type { Document, Page, Runbook, Schema } from "../../types/models";
import { ServiceSelect } from "../../components/Selectors";
import { ServiceLink } from "../../components/ResourceLinks";
import { Identifier } from "../../components/RecordDetails";
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

function RunbookForm({
  current,
  close,
}: {
  current?: Document;
  close: () => void;
}) {
  const write = useWrite<
    Document,
    Schema<"RunbookCreate"> | Schema<"RunbookUpdate">
  >((body) =>
    current
      ? api.patch(`/runbooks/${current.id}`, body)
      : api.post("/runbooks", body),
  );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const shared = {
      title: String(values.get("title")),
      content: String(values.get("content")),
      format: String(values.get("format")) as "markdown" | "text",
      tags: String(values.get("tags"))
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
    };
    try {
      await write.mutateAsync(
        current
          ? { ...shared, expected_version: current.current_version }
          : { ...shared, service_id: String(values.get("service_id")) || null },
      );
      close();
    } catch {
      /* Preserve errors. */
    }
  }
  return (
    <Dialog
      title={current ? "Publish new runbook version" : "Create runbook"}
      onClose={close}
    >
      <form onSubmit={submit}>
        <ErrorPanel error={write.error} />
        <Field label="Title">
          <input
            name="title"
            required
            maxLength={200}
            defaultValue={current?.title}
          />
        </Field>
        {!current && <ServiceSelect />}
        <Field label="Tags (comma separated)">
          <input
            name="tags"
            defaultValue={current?.tags.join(", ")}
            maxLength={409}
          />
        </Field>
        <Field label="Format">
          <select name="format" defaultValue={current?.format ?? "markdown"}>
            <option value="markdown">Markdown</option>
            <option value="text">Plain text</option>
          </select>
        </Field>
        <Field
          label="Document content"
          hint="UTF-8, up to 64,000 characters and 131,072 bytes. Never include secrets."
        >
          <textarea
            name="content"
            required
            rows={12}
            maxLength={64000}
            defaultValue={current?.content}
          />
        </Field>
        <Submit pending={write.isPending}>
          {current ? "Publish version" : "Create runbook"}
        </Submit>
      </form>
    </Dialog>
  );
}
export function Runbooks() {
  const { user } = useAuth();
  const [offset, setOffset] = useState(0);
  const [create, setCreate] = useState(false);
  const query = useApi<Page<Runbook>>("/runbooks", { limit: 20, offset });
  const search = useMutation({
    mutationFn: (query: string) =>
      api.post<Schema<"SearchHit">[]>("/runbooks/search", { query, limit: 10 }),
    retry: false,
  });
  return (
    <>
      <PageTitle
        title="Runbook knowledge base"
        description="Versioned operational documentation retrieved as context for investigations."
        actions={
          canAdmin(user) && (
            <Button variant="primary" onClick={() => setCreate(true)}>
              <Plus size={15} />
              Create runbook
            </Button>
          )
        }
      />
      <Notice>
        Runbooks provide supporting context, not verified evidence that a
        current incident has the same cause.
      </Notice>
      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault();
          search.mutate(String(new FormData(event.currentTarget).get("query")));
        }}
      >
        <Field label="Full-text search">
          <input
            name="query"
            required
            minLength={2}
            maxLength={200}
            placeholder="Connection pool troubleshooting"
          />
        </Field>
        <Button disabled={search.isPending} type="submit">
          <Search size={15} />
          Search
        </Button>
        {search.data && (
          <Button type="button" onClick={() => search.reset()}>
            Clear results
          </Button>
        )}
      </form>
      <ErrorPanel error={search.error} />
      {search.data && (
        <Panel title="Ranked knowledge excerpts">
          {search.data.length ? (
            search.data.map((item) => (
              <article className="search-hit" key={item.source_id}>
                <strong>{item.title}</strong>
                <p>{item.excerpt}</p>
                <span className="small muted">
                  Rank {item.rank} · {item.service_relevance} · Version{" "}
                  {item.document_version} · Score {item.score.toFixed(3)} (not a
                  probability)
                </span>
                <p className="mono small muted">
                  {item.source_type}:{item.source_id}
                </p>
              </article>
            ))
          ) : (
            <Empty title="No matching knowledge" />
          )}
        </Panel>
      )}
      <ErrorPanel error={query.error} />
      {query.isPending && <Loading />}
      {query.data && (
        <div className="panel">
          {query.data.items.length ? (
            <DataTable
              label="Runbooks"
              headers={["Document", "Service", "Version", "Tags", "Updated"]}
            >
              {query.data.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link to={`/runbooks/${item.id}`}>
                      <strong>{item.title}</strong>
                    </Link>
                  </td>
                  <td>
                    {item.service_id ? (
                      <ServiceLink id={item.service_id} />
                    ) : (
                      <span className="muted">Global</span>
                    )}
                  </td>
                  <td className="mono">v{item.current_version}</td>
                  <td className="small muted">{item.tags.join(", ") || "—"}</td>
                  <td>{date(item.updated_at)}</td>
                </tr>
              ))}
            </DataTable>
          ) : (
            <Empty title="No runbooks yet" />
          )}
          <Pagination
            offset={offset}
            total={query.data.total}
            onChange={setOffset}
          />
        </div>
      )}
      {create && <RunbookForm close={() => setCreate(false)} />}
    </>
  );
}
export function RunbookDetail() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const [edit, setEdit] = useState(false);
  const [version, setVersion] = useState(0);
  const current = useApi<Document>(`/runbooks/${id}`);
  const historic = useApi<Schema<"VersionRead">>(
    `/runbooks/${id}/versions/${version}`,
    undefined,
    { enabled: version > 0 },
  );
  const reindex = useWrite<Document, void>(() =>
    api.post(`/runbooks/${id}/reindex`, {
      expected_version: current.data?.current_version,
    }),
  );
  const document = version ? historic.data : current.data;
  return (
    <>
      <PageTitle
        title={current.data?.title ?? "Runbook document"}
        description="Immutable version references preserve the text each investigation reviewed."
        actions={
          canAdmin(user) &&
          current.data && (
            <>
              <Button onClick={() => setEdit(true)}>Publish new version</Button>
              <Button
                disabled={reindex.isPending}
                onClick={() => reindex.mutate()}
              >
                Reindex
              </Button>
            </>
          )
        }
      />
      <ErrorPanel error={current.error} />
      <ErrorPanel error={historic.error} />
      <ErrorPanel error={reindex.error} />
      {current.isPending && <Loading />}
      {current.data && (
        <div className="filter-bar">
          {current.data.service_id ? (
            <ServiceLink id={current.data.service_id} />
          ) : (
            <span className="muted">Global runbook</span>
          )}
          <Identifier value={current.data.id} name="runbook ID" />
          <Field label="Document version">
            <select
              value={version}
              onChange={(event) => setVersion(Number(event.target.value))}
            >
              <option value={0}>
                Current · v{current.data.current_version}
              </option>
              {Array.from(
                { length: Math.min(current.data.current_version, 100) },
                (_, index) => current.data!.current_version - index,
              ).map((value) => (
                <option value={value} key={value}>
                  Version {value}
                </option>
              ))}
            </select>
          </Field>
          <span className="small muted">
            Most recent 100 versions shown; any known version can be read
            through the API.
          </span>
        </div>
      )}
      {document && (
        <Panel title={document.title} aside={<Badge value={document.format} />}>
          <p className="mono small muted wrap">
            Stored content hash: {document.content_hash}
          </p>
          <div className="document-content">{document.content}</div>
          {"chunk_ids" in document && (
            <details>
              <summary>Immutable chunk identifiers</summary>
              <Json value={document.chunk_ids} />
            </details>
          )}
        </Panel>
      )}
      {edit && current.data && (
        <RunbookForm current={current.data} close={() => setEdit(false)} />
      )}
    </>
  );
}
