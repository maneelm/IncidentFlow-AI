import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExecutionComparison } from "../features/resolutions/ExecutionComparison";
import { Timeline } from "../features/incidents/IncidentDetail";
import type { Schema } from "../types/models";
import { id, otherId, now, pageOf } from "./fixtures";
import { mockApi, renderPage } from "./helpers";

const before: Schema<"SyntheticSnapshot"> = {
  synthetic: true,
  scenario: "database_exhaustion",
  health: "degraded",
  request_error_rate: 0.4,
  dependency_condition: "database_pressure",
  pool_size: 20,
  active_connections: 20,
  waiting_requests: 50,
  timeout_indicator: true,
  request_count: 1000,
  p50_ms: 800,
  p95_ms: 4000,
  endpoint: "/synthetic/payments",
  dependency_delay_ms: 500,
};
const after: Schema<"SyntheticSnapshot"> = {
  ...before,
  health: "healthy",
  request_error_rate: 0,
  dependency_condition: "healthy",
  active_connections: 4,
  waiting_requests: 0,
  timeout_indicator: false,
  p50_ms: 30,
  p95_ms: 80,
  dependency_delay_ms: 10,
};
const result: Schema<"ActionResult"> = {
  synthetic: true,
  action_id: "simulate_connection_pool_recovery",
  before,
  after,
  findings: ["Synthetic state only; no production action occurred."],
  observed_at: now,
};

function row(name: string) {
  return screen.getByRole("rowheader", { name }).closest("tr")!;
}
function values(name: string) {
  return within(row(name))
    .getAllByRole("cell")
    .map((cell) => cell.textContent);
}

describe("actual synthetic execution comparison", () => {
  it("renders all nine metrics with explicit before/after columns and units", () => {
    renderPage(<ExecutionComparison result={result} />);
    expect(screen.getByRole("table")).toHaveAccessibleName(
      "Synthetic service state comparison",
    );
    expect(screen.getByRole("columnheader", { name: "Before" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "After" })).toBeVisible();
    expect(values("Service health")).toEqual(["Degraded", "Healthy"]);
    expect(values("Request error rate")).toEqual(["40%", "0%"]);
    expect(values("p50 latency")).toEqual(["800 ms", "30 ms"]);
    expect(values("p95 latency")).toEqual(["4,000 ms", "80 ms"]);
    expect(values("Active database connections")).toEqual(["20", "4"]);
    expect(values("Waiting requests")).toEqual(["50", "0"]);
    expect(values("Connection-pool size")).toEqual(["20", "20"]);
    expect(values("Database timeout")).toEqual(["Yes", "No"]);
    expect(values("Dependency condition")).toEqual([
      "Database Pressure",
      "Healthy",
    ]);
    expect(
      screen.getByText(
        "These results describe synthetic service state only. No production infrastructure was modified.",
      ),
    ).toBeVisible();
  });

  it("keeps missing/null metrics distinct from zero and false", () => {
    renderPage(
      <ExecutionComparison
        result={{
          before: null,
          after: {
            request_error_rate: 0,
            timeout_indicator: false,
            active_connections: null,
          },
        }}
      />,
    );
    expect(values("Request error rate")).toEqual(["Not recorded", "0%"]);
    expect(values("Database timeout")).toEqual(["Not recorded", "No"]);
    expect(values("Active database connections")).toEqual([
      "Not recorded",
      "Not recorded",
    ]);
    expect(values("Service health")).toEqual(["Not recorded", "Not recorded"]);
  });

  it("does not coerce invalid values or label incomparable pool changes as improvements", () => {
    renderPage(
      <ExecutionComparison
        result={{
          before: { ...before, request_error_rate: "0.4", pool_size: 20 },
          after: {
            ...after,
            request_error_rate: 4,
            p50_ms: Infinity,
            active_connections: -1,
            waiting_requests: 1.5,
            timeout_indicator: "false",
            pool_size: 40,
          },
        }}
      />,
    );
    expect(values("Request error rate")).toEqual([
      "Not recorded",
      "Not recorded",
    ]);
    expect(values("p50 latency")[1]).toBe("Not recorded");
    expect(values("Active database connections")[1]).toBe("Not recorded");
    expect(values("Waiting requests")[1]).toBe("Not recorded");
    expect(values("Database timeout")[1]).toBe("Not recorded");
    expect(values("Connection-pool size")).toEqual(["20", "40"]);
    expect(
      screen.queryByText(/improved|better|recovered/i),
    ).not.toBeInTheDocument();
  });

  it("does not round a recorded small positive error rate to zero", () => {
    renderPage(
      <ExecutionComparison
        result={{
          before: { request_error_rate: 0.00001 },
          after: { request_error_rate: 0 },
        }}
      />,
    );
    expect(values("Request error rate")).toEqual(["0.001%", "0%"]);
  });

  it("renders unchanged read-only snapshots without suggesting recovery", () => {
    renderPage(
      <ExecutionComparison
        result={{
          ...result,
          action_id: "inspect_request_latency",
          after: before,
        }}
      />,
    );
    expect(values("Service health")).toEqual(["Degraded", "Degraded"]);
    expect(values("p95 latency")).toEqual(["4,000 ms", "4,000 ms"]);
    expect(screen.queryByText(/improved|recovered/i)).not.toBeInTheDocument();
  });

  it.each([
    null,
    [],
    { findings: ["Read-only diagnostic result"], samples: { latency: 120 } },
  ])(
    "handles another result shape without inventing snapshots: %j",
    (value) => {
      renderPage(<ExecutionComparison result={value} />);
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(
        screen.getByText(/No comparable synthetic state metrics/),
      ).toBeVisible();
      const disclosure = screen
        .getByText("View raw execution data")
        .closest("details")!;
      expect(disclosure).not.toHaveAttribute("open");
      fireEvent.click(within(disclosure).getByText("View raw execution data"));
      expect(disclosure).toHaveAttribute("open");
      expect(JSON.parse(disclosure.querySelector("pre")!.textContent!)).toEqual(
        value,
      );
    },
  );

  it("expands the complete unmodified raw result, including unlisted technical fields", () => {
    const raw = {
      ...result,
      findings: ["<img src=x onerror=alert(1)>"],
      extra: { diagnostic: "retained" },
    };
    renderPage(<ExecutionComparison result={raw} />);
    const disclosure = screen
      .getByText("View raw execution data")
      .closest("details")!;
    expect(disclosure).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("View raw execution data"));
    expect(disclosure).toHaveAttribute("open");
    expect(JSON.parse(disclosure.querySelector("pre")!.textContent!)).toEqual(
      raw,
    );
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("compact unified timeline audit information", () => {
  it("retains timestamps, actors, sources, drill-down links and expandable metadata", async () => {
    const fetch = mockApi((path) =>
      path.includes("/directory/users")
        ? pageOf([{ id, display_name: "Recorded reviewer" }])
        : {
            items: [
              {
                occurred_at: now,
                source_type: "resolution_event",
                source_id: id,
                event_type: "approval_requested",
                details: {
                  actor_id: id,
                  proposal_id: otherId,
                  reason: "Exact recorded reason",
                  extra: "<script>unsafe()</script>",
                },
              },
              {
                occurred_at: now,
                source_type: "investigation",
                source_id: otherId,
                event_type: "completed",
                details: { system_actor: "investigation_worker" },
              },
              {
                occurred_at: now,
                source_type: "detection",
                source_id: id,
                event_type: "triggered",
                details: {},
              },
            ],
            next_cursor: null,
            until: now,
          },
    );
    renderPage(<Timeline incidentId={id} />);
    expect(
      await screen.findByText("Approval Requested", { exact: true }),
    ).toBeVisible();
    expect(await screen.findByText("Recorded reviewer")).toBeVisible();
    expect(screen.getByText("System · Investigation Worker")).toBeVisible();
    expect(screen.getByText("Resolution Event")).toBeVisible();
    expect(document.querySelectorAll(`time[datetime="${now}"]`)).toHaveLength(
      3,
    );
    expect(
      screen.getByRole("link", { name: "Open proposal →" }),
    ).toHaveAttribute("href", `/resolutions/${otherId}`);
    expect(
      screen.getByRole("link", { name: "Open investigation →" }),
    ).toHaveAttribute("href", `/investigations/${otherId}`);
    expect(
      screen.getByRole("link", { name: "Open detection →" }),
    ).toHaveAttribute("href", `/detections/${id}`);
    const disclosure = screen
      .getAllByText("Recorded metadata")[0]
      .closest("details")!;
    expect(disclosure).not.toHaveAttribute("open");
    fireEvent.click(within(disclosure).getByText("Recorded metadata"));
    expect(disclosure).toHaveAttribute("open");
    expect(disclosure.querySelector("pre")).toHaveTextContent(
      "Exact recorded reason",
    );
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getAllByTitle(id).length).toBeGreaterThan(0);
    expect(
      fetch.mock.calls.every(
        ([, options]) => !options?.method || options.method === "GET",
      ),
    ).toBe(true);
  });

  it("appends the returned cursor page in recorded order without replacing earlier audit events", async () => {
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
      next_cursor: path.includes("cursor=") ? null : "cursor-final",
      until: now,
    }));
    renderPage(<Timeline incidentId={id} />);
    await screen.findByText("Created", { exact: true });
    fireEvent.click(screen.getByRole("button", { name: "Load more activity" }));
    await screen.findByText("Transitioned", { exact: true });
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Load more activity" }),
      ).not.toBeInTheDocument(),
    );
    expect(
      Array.from(document.querySelectorAll(".timeline-item strong")).map(
        (element) => element.textContent,
      ),
    ).toEqual(["Created", "Transitioned"]);
    expect(
      fetch.mock.calls.some(([path]) =>
        String(path).includes("cursor=cursor-final"),
      ),
    ).toBe(true);
    expect(
      fetch.mock.calls.every(([path]) => !String(path).includes("offset=")),
    ).toBe(true);
  });
});
