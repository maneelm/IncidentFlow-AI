import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { ApiError } from "../lib/api/client";
import { reviewEligibility } from "../lib/permissions";
import { IncidentForm, IncidentList } from "../features/incidents/IncidentList";
import { Timeline } from "../features/incidents/IncidentDetail";
import { Logs } from "../features/logs/Logs";
import { Detections } from "../features/detections/Detections";
import {
  InvestigationDetail,
  StartInvestigation,
} from "../features/investigations/Investigations";
import { RunbookDetail } from "../features/runbooks/Runbooks";
import { ProposalForm } from "../features/resolutions/ProposalForm";
import { ProposalDetail } from "../features/resolutions/Resolutions";
import { IncidentChart, Overview } from "../features/overview/Overview";
import { ErrorPanel } from "../components/ui";
import { failure, mockApi, renderPage } from "./helpers";
import type { Proposal } from "../types/models";
import {
  catalog,
  evidence,
  id,
  incident,
  now,
  otherId,
  overview,
  pageOf,
  proposal,
  report,
  service,
  user,
} from "./fixtures";

const state = {
  id: otherId,
  service_id: otherId,
  version: 1,
  snapshot: { synthetic: true, health: "degraded" },
};
function common(path: string) {
  if (path.includes("/directory/users"))
    return pageOf([{ id, display_name: user.display_name, role: "engineer" }]);
  if (path.includes("/services/") && path.includes("synthetic-state"))
    return state;
  if (path.includes("/services/")) return service;
  if (path.startsWith("/api/v1/services")) return pageOf([service]);
  if (path.endsWith("/action-catalog")) return catalog;
  if (path.includes("/investigations/") && path.includes("/evidence"))
    return pageOf([evidence]);
  if (path.includes("/investigations/") && path.endsWith("/report"))
    return report;
  if (path.includes("/investigations")) return pageOf([]);
  return pageOf([]);
}

describe("role-aware incident forms", () => {
  it("creates through the exact backend contract with a service and nullable assignment", async () => {
    const fetch = mockApi((path, options) =>
      options?.method === "POST" ? incident : common(path),
    );
    const close = vi.fn();
    renderPage(<IncidentForm close={close} />);
    fireEvent.change(screen.getByLabelText("Incident title"), {
      target: { value: "Database unavailable" },
    });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Observed timeouts" },
    });
    await screen.findByRole("option", { name: service.name });
    fireEvent.change(screen.getByLabelText("Service", { exact: true }), {
      target: { value: otherId },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create incident" }));
    await waitFor(() => expect(close).toHaveBeenCalled());
    const call = fetch.mock.calls.find(
      ([, options]) => options?.method === "POST",
    );
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      title: "Database unavailable",
      description: "Observed timeouts",
      service_id: otherId,
      assignee_id: null,
      severity: "medium",
    });
  });
  it("retains the reviewed version and shows a conflict without overwriting", async () => {
    const fetch = mockApi(() => failure(409, "version_conflict"));
    const close = vi.fn();
    renderPage(
      <IncidentForm current={{ ...incident, version: 3 }} close={close} />,
    );
    fireEvent.change(screen.getByLabelText("Incident title"), {
      target: { value: "My reviewed edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "record changed",
    );
    expect(close).not.toHaveBeenCalled();
    expect(
      JSON.parse(String(fetch.mock.calls[0][1]?.body)).expected_version,
    ).toBe(3);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("hides incident creation from viewers and applies server filters", async () => {
    const fetch = mockApi((path) =>
      path.includes("/incidents") ? pageOf([]) : common(path),
    );
    renderPage(<IncidentList />, { actor: { ...user, role: "viewer" } });
    await screen.findByText("No incidents match these filters");
    expect(
      screen.queryByRole("button", { name: "Create incident" }),
    ).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Severity", { exact: true }), {
      target: { value: "critical" },
    });
    fireEvent.change(screen.getByLabelText("Direction"), {
      target: { value: "asc" },
    });
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(([path]) =>
          String(path).includes("severity=critical"),
        ),
      ).toBe(true),
    );
  });
  it("renders backend validation locations instead of dropping them", () => {
    renderPage(
      <ErrorPanel
        error={
          new ApiError(422, "validation_error", "Invalid input", [
            { location: ["body", "service_id"], message: "Invalid field" },
          ])
        }
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("service_id");
  });
});
describe("logs and deterministic detection", () => {
  it("sends log filters and paginates on the server", async () => {
    const fetch = mockApi((path) =>
      path.includes("/logs") ? pageOf([], 25) : common(path),
    );
    renderPage(<Logs />);
    await screen.findByRole("button", { name: "Next page" });
    fireEvent.change(screen.getByLabelText("Trace ID"), {
      target: { value: "trace-123" },
    });
    fireEvent.change(screen.getByLabelText("Message search"), {
      target: { value: "timeout" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(([path]) =>
          String(path).includes("trace_id=trace-123"),
        ),
      ).toBe(true),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(([path]) => String(path).includes("offset=20")),
      ).toBe(true),
    );
  });
  it("admin rule creation respects threshold/window/version contracts", async () => {
    const fetch = mockApi((path, options) =>
      path.includes("/detection-rules")
        ? options?.method === "POST"
          ? { id }
          : pageOf([])
        : common(path),
    );
    renderPage(<Detections />, { actor: { ...user, role: "admin" } });
    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Rule name"), {
      target: { value: "Connection failures" },
    });
    await screen.findByRole("option", { name: service.name });
    fireEvent.change(
      within(dialog).getByLabelText("Service", { exact: true }),
      { target: { value: otherId } },
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save changes" }),
    );
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(([, options]) => options?.method === "POST"),
      ).toBe(true),
    );
    const call = fetch.mock.calls.find(
      ([, options]) => options?.method === "POST",
    );
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      threshold: 5,
      window_seconds: 60,
      cooldown_seconds: 60,
      enabled: true,
    });
  });
});
describe("investigation workspace", () => {
  it("blocks starts when the provider is unavailable", async () => {
    mockApi(() => ({
      provider: "groq",
      model: "",
      configured: false,
      synthetic: false,
    }));
    renderPage(<StartInvestigation incidentId={id} />);
    expect(
      await screen.findByText(/Investigation provider unavailable/),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Start investigation" }),
    ).toBeDisabled();
  });
  it("starts with a stable client UUID and labels the fake provider", async () => {
    const fetch = mockApi((_path, options) =>
      options?.method === "POST"
        ? { id }
        : {
            provider: "fake",
            model: "fake-v1",
            configured: true,
            synthetic: true,
          },
    );
    renderPage(<StartInvestigation incidentId={id} />);
    expect(await screen.findByText(/Fake provider enabled/)).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "Start investigation" }),
    );
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(([, options]) => options?.method === "POST"),
      ).toBe(true),
    );
    const call = fetch.mock.calls.find(
      ([, options]) => options?.method === "POST",
    );
    expect(JSON.parse(String(call?.[1]?.body)).client_request_id).toMatch(
      /^[0-9a-f-]{36}$/,
    );
  });
  it("renders uncertainty and safely opens actual evidence references", async () => {
    mockApi((path) =>
      path.endsWith(`/investigations/${id}`)
        ? {
            id,
            incident_id: id,
            status: "completed",
            provider: "fake",
            model: "fake-v1",
            llm_calls: 2,
            created_at: now,
          }
        : path.includes("/steps")
          ? pageOf([
              {
                attempt: 1,
                stage: "final_report",
                status: "completed",
                details: {},
                created_at: now,
              },
            ])
          : common(path),
    );
    renderPage(
      <Routes>
        <Route path="/investigations/:id" element={<InvestigationDetail />} />
      </Routes>,
      { route: `/investigations/${id}` },
    );
    expect(await screen.findByText(report.summary)).toBeVisible();
    expect(screen.getByText(/Synthetic provider report/)).toBeVisible();
    expect(
      screen.getByText("No connection counters are available."),
    ).toBeVisible();
    fireEvent.click(screen.getAllByRole("button", { name: /log:22222222/ })[0]);
    const dialog = screen.getByRole("dialog", { name: "Evidence source" });
    expect(within(dialog).getByText(evidence.excerpt)).toBeVisible();
    expect(document.querySelector("script")).toBeNull();
  });
  it("polls running work then stops after a completed report", async () => {
    vi.useFakeTimers();
    let status = "running";
    const fetch = mockApi((path) =>
      path.endsWith(`/investigations/${id}`)
        ? {
            id,
            incident_id: id,
            status,
            provider: "fake",
            model: "fake-v1",
            llm_calls: 2,
            created_at: now,
          }
        : path.includes("/steps")
          ? pageOf([])
          : path.includes("/evidence") && status === "running"
            ? pageOf([])
            : common(path),
    );
    renderPage(
      <Routes>
        <Route path="/investigations/:id" element={<InvestigationDetail />} />
      </Routes>,
      { route: `/investigations/${id}` },
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    status = "completed";
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(screen.getByText(report.summary)).toBeVisible();
    expect(
      screen.getByRole("button", { name: /Source timeout record/ }),
    ).toBeVisible();
    const count = fetch.mock.calls.filter(([path]) =>
      String(path).endsWith(`/investigations/${id}`),
    ).length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(
      fetch.mock.calls.filter(([path]) =>
        String(path).endsWith(`/investigations/${id}`),
      ),
    ).toHaveLength(count);
  });
});
describe("knowledge and overview", () => {
  it("shows plain runbook content without executing HTML and reads immutable versions", async () => {
    const doc = {
      id,
      title: "Pool documentation",
      service_id: null,
      tags: [],
      current_version: 2,
      created_at: now,
      updated_at: now,
      format: "markdown",
      content: "<img src=x onerror=alert(1)> safe source text",
      content_hash: "hash",
      chunk_ids: [],
    };
    const fetch = mockApi(() => doc);
    renderPage(
      <Routes>
        <Route path="/runbooks/:id" element={<RunbookDetail />} />
      </Routes>,
      { route: `/runbooks/${id}` },
    );
    expect(await screen.findByText(doc.content)).toBeVisible();
    expect(document.querySelector("img")).toBeNull();
    fireEvent.change(screen.getByLabelText("Document version"), {
      target: { value: "1" },
    });
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(([path]) => String(path).endsWith("/versions/1")),
      ).toBe(true),
    );
  });
  it("handles no-data charts without fabricating a dataset", () => {
    renderPage(<IncidentChart groups={[]} total={0} />);
    expect(screen.getByText("No incident distribution yet")).toBeVisible();
    expect(screen.queryByRole("img")).toBeNull();
  });
  it("renders actual aggregate values and no invented production health", async () => {
    mockApi(() => overview);
    renderPage(<Overview />);
    await screen.findByText("No incident distribution yet");
    expect(screen.getByText("No detections yet")).toBeVisible();
    expect(screen.getByText("Review queue is clear")).toBeVisible();
    expect(
      screen.getByText(/not live production health metrics/),
    ).toBeVisible();
  });
});
describe("approval policy and resolution forms", () => {
  it.each([
    [{ ...user, role: "viewer" as const }, proposal, false],
    [user, proposal, false],
    [{ ...user, role: "admin" as const }, proposal, false],
    [{ ...user, id: otherId }, proposal, true],
    [user, { ...proposal, risk: "read_only" }, true],
    [
      { ...user, id: otherId },
      { ...proposal, approval_deadline: "2020-01-01T00:00:00Z" },
      false,
    ],
    [
      { ...user, id: otherId },
      { ...proposal, status: "succeeded" as const },
      false,
    ],
  ])("enforces review eligibility %#", (actor, record, allowed) =>
    expect(reviewEligibility(actor, record).allowed).toBe(allowed),
  );
  it("creates only typed catalog parameters and retries the same UUID only by explicit request", async () => {
    let attempts = 0;
    const bodies: unknown[] = [];
    mockApi((path, options) => {
      if (options?.method === "POST") {
        bodies.push(JSON.parse(String(options.body)));
        if (++attempts === 1) throw new TypeError("Network interrupted");
        return proposal;
      }
      return common(path);
    });
    renderPage(<ProposalForm incident={incident} close={vi.fn()} />);
    await screen.findByRole("option", {
      name: "Simulate Connection Pool Recovery",
    });
    fireEvent.change(screen.getByLabelText(/Catalog action/), {
      target: { value: "simulate_connection_pool_recovery" },
    });
    fireEvent.change(screen.getByLabelText("Human explanation"), {
      target: {
        value: "I reviewed the isolated state and request synthetic recovery.",
      },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Create immutable proposal" }),
    );
    expect(await screen.findByText(/create outcome is unknown/)).toBeVisible();
    expect(attempts).toBe(1);
    fireEvent.click(
      screen.getByRole("button", { name: "Retry identical request" }),
    );
    await waitFor(() => expect(attempts).toBe(2));
    expect(bodies[0]).toEqual(bodies[1]);
    expect(
      Object.keys((bodies[0] as { parameters: object }).parameters).sort(),
    ).toEqual(["expected_state_version", "state_id"]);
    expect(document.querySelector('input[name="command"]')).toBeNull();
  });
  it("submits the expected proposal version and prevents self-approval", async () => {
    let current: Proposal = { ...proposal, status: "draft" };
    const fetch = mockApi((path) => {
      if (path.endsWith(`/${id}/submit`)) {
        current = { ...proposal, status: "pending_approval" };
        return current;
      }
      if (path.endsWith(`/resolution-proposals/${id}`)) return current;
      return pageOf([]);
    });
    renderPage(
      <Routes>
        <Route path="/resolutions/:id" element={<ProposalDetail />} />
      </Routes>,
      { route: `/resolutions/${id}` },
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Submit for approval" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Approve proposal" }),
      ).toBeDisabled(),
    );
    expect(
      screen.getByText(/A different engineer or administrator must review/),
    ).toBeVisible();
    const call = fetch.mock.calls.find(
      ([path, options]) =>
        String(path).endsWith("/submit") && options?.method === "POST",
    );
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      expected_version: 2,
    });
  });
  it.each(["approve", "reject"])(
    "captures a real review reason for %s with the reviewed version",
    async (operation) => {
      const fetch = mockApi((path, options) =>
        path.endsWith(`/resolution-proposals/${id}`)
          ? proposal
          : options?.method === "POST"
            ? {
                ...proposal,
                status: operation === "approve" ? "queued" : "rejected",
              }
            : pageOf([]),
      );
      renderPage(
        <Routes>
          <Route path="/resolutions/:id" element={<ProposalDetail />} />
        </Routes>,
        { route: `/resolutions/${id}`, actor: { ...user, id: otherId } },
      );
      fireEvent.click(
        await screen.findByRole("button", {
          name:
            operation === "approve" ? "Approve proposal" : "Reject proposal",
        }),
      );
      fireEvent.change(screen.getByLabelText("Review reason"), {
        target: {
          value: "Independent human review of the exact synthetic target.",
        },
      });
      fireEvent.click(
        screen.getByRole("button", {
          name:
            operation === "approve" ? "Confirm approval" : "Confirm rejection",
        }),
      );
      await waitFor(() =>
        expect(
          fetch.mock.calls.some(([, options]) => options?.method === "POST"),
        ).toBe(true),
      );
      const call = fetch.mock.calls.find(
        ([, options]) => options?.method === "POST",
      );
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        expected_version: 2,
        reason: "Independent human review of the exact synthetic target.",
      });
    },
  );
});
describe("timeline cursor semantics", () => {
  it("uses returned cursors rather than offsets and preserves source UUIDs", async () => {
    const fetch = mockApi((path) => ({
      items: [
        {
          occurred_at: now,
          source_type: "incident_event",
          source_id: path.includes("cursor=") ? otherId : id,
          event_type: path.includes("cursor=") ? "transitioned" : "created",
          details: {},
        },
      ],
      next_cursor: path.includes("cursor=") ? null : "cursor-abc",
      until: now,
    }));
    renderPage(<Timeline incidentId={id} />);
    expect(await screen.findByText("Created", { exact: true })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Load more activity" }));
    await screen.findByText("Transitioned", { exact: true });
    expect(
      fetch.mock.calls.some(([path]) =>
        String(path).includes("cursor=cursor-abc"),
      ),
    ).toBe(true);
    expect(
      fetch.mock.calls.some(([path]) => String(path).includes("offset=")),
    ).toBe(false);
    expect(
      screen.queryByRole("button", { name: "Load more activity" }),
    ).not.toBeInTheDocument();
  });
});
