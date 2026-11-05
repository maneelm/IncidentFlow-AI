import { fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { Identifier } from "../components/RecordDetails";
import { ServiceLink } from "../components/ResourceLinks";
import { Field } from "../components/ui";
import { IncidentList } from "../features/incidents/IncidentList";
import {
  EvidenceReferences,
  InvestigationDetail,
  StepDiagnostics,
} from "../features/investigations/Investigations";
import type { Investigation, Step } from "../types/models";
import {
  id,
  otherId,
  now,
  service,
  pageOf,
  report,
  evidence,
} from "./fixtures";
import { mockApi, renderPage } from "./helpers";

describe("record identity and bounded cached labels", () => {
  it("copies the exact identifier while displaying a compact label", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    renderPage(<Identifier value={id} name="investigation ID" />);
    fireEvent.click(
      screen.getByRole("button", { name: "Copy investigation ID" }),
    );
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("copied"),
    );
    expect(writeText).toHaveBeenCalledWith(id);
    expect(screen.getByTitle(id)).toHaveTextContent(id.slice(0, 8));
  });
  it("offers the full identifier for manual selection when clipboard access fails", async () => {
    vi.stubGlobal("navigator", { clipboard: undefined });
    renderPage(<Identifier value={id} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy record ID" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      `Select the full identifier: ${id}`,
    );
  });
  it("reuses a fresh service page without a duplicate detail request", async () => {
    const fetch = mockApi(() => {
      throw new Error("unexpected request");
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 15000 } },
    });
    client.setQueryData(
      ["api", "/services", { limit: 100, offset: 0 }, null],
      pageOf([service]),
    );
    renderPage(<ServiceLink id={otherId} />, { client });
    expect(screen.getByRole("link", { name: /payments-api/ })).toHaveAttribute(
      "href",
      `/services/${otherId}`,
    );
    expect(screen.getByText("Simulation")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetch).not.toHaveBeenCalled();
  });
  it("deduplicates simultaneous service lookups and retains the actual generated name", async () => {
    const named = {
      ...service,
      name: "sim-payment-03da3220b41d",
      environment: "staging",
    };
    const fetch = mockApi(() => named);
    renderPage(
      <>
        <ServiceLink id={otherId} />
        <ServiceLink id={otherId} />
      </>,
    );
    expect(await screen.findAllByText(named.name)).toHaveLength(2);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Simulation")).not.toBeInTheDocument();
  });
});

describe("readable reports and accessible context", () => {
  it("separates the field name from its accessible help text", () => {
    renderPage(
      <Field label="Service name" hint="Use the registered name.">
        <input />
      </Field>,
    );
    expect(
      screen.getByRole("textbox", { name: "Service name" }),
    ).toHaveAccessibleDescription("Use the registered name.");
  });
  it("expands grouped citations without changing the exact selected reference", () => {
    const refs = Array.from({ length: 9 }, (_, index) => `log:${index}-${id}`);
    const select = vi.fn();
    renderPage(<EvidenceReferences refs={refs} onSelect={select} />);
    expect(document.querySelector(".citations")?.children).toHaveLength(6);
    expect(
      screen.getByText("Show 3 more sources").closest("details"),
    ).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("Show 3 more sources"));
    fireEvent.click(
      screen.getByRole("button", { name: `View log:8-${id} source` }),
    );
    expect(select).toHaveBeenCalledWith(refs[8]);
  });
  it("shows allowlisted failure details and omits raw or unknown payload fields", () => {
    const step: Step = {
      attempt: 2,
      stage: "recommendations",
      status: "failed",
      created_at: now,
      details: {
        error_code: "provider_rate_limited",
        retry_scheduled: true,
        retry_delay_seconds: 45,
        diagnostics: {
          exception_class: "RateLimitError",
          http_status: 429,
          request_id: "req_test",
          raw_output: "private model text",
          authorization: "private token",
        },
      },
    };
    renderPage(<StepDiagnostics step={step} />);
    fireEvent.click(screen.getByText("Failure details · attempt 2"));
    expect(screen.getByText("RateLimitError")).toBeInTheDocument();
    expect(screen.getByText("45 seconds")).toBeInTheDocument();
    expect(screen.queryByText(/private/)).not.toBeInTheDocument();
  });
  it("uses the stored provider and model rather than global provider availability", async () => {
    const current: Investigation = {
      id,
      incident_id: id,
      client_request_id: otherId,
      requested_by: id,
      provider: "fake",
      model: "fake-v1",
      status: "completed",
      stage: "final_report",
      llm_calls: 2,
      input_tokens_reserved: 2000,
      output_tokens_reserved: 4096,
      actual_input_tokens: 0,
      actual_output_tokens: 0,
      created_at: now,
      updated_at: now,
      started_at: now,
      completed_at: now,
      cancelled_by: null,
      error_code: null,
    };
    mockApi((path) =>
      path.endsWith("/report")
        ? report
        : path.includes("/evidence")
          ? pageOf([evidence])
          : path.includes("/steps")
            ? pageOf([])
            : path.includes("capabilities")
              ? { provider: "groq", configured: true }
              : current,
    );
    renderPage(<InvestigationDetail />);
    expect(
      await screen.findByText(/Synthetic provider report/),
    ).toBeInTheDocument();
    expect(screen.getByText("fake-v1")).toBeInTheDocument();
    expect(screen.queryByText("Groq", { exact: true })).not.toBeInTheDocument();
  });
  it("applies the service drill-down filter to the server request", async () => {
    const fetch = mockApi((path) =>
      path.startsWith("/api/v1/services") ? pageOf([service]) : pageOf([]),
    );
    renderPage(<IncidentList />, { route: `/incidents?service_id=${otherId}` });
    await screen.findByText("No incidents match these filters");
    expect(
      fetch.mock.calls.some(([path]) =>
        String(path).includes(`service_id=${otherId}`),
      ),
    ).toBe(true);
    expect(screen.getByLabelText("Service", { exact: true })).toHaveValue(
      otherId,
    );
  });
});
