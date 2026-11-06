import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import type { Execution } from "../src/types/models";
import { expectNoHorizontalOverflow } from "./layout";

interface Fixture {
  api_url: string;
  accounts: Record<
    "admin" | "engineer" | "viewer",
    { email: string; password: string }
  >;
}
const fixture = JSON.parse(
  process.env.E2E_FIXTURE_JSON ?? "null",
) as Fixture | null;
if (!fixture)
  throw new Error(
    "Use the isolated browser or container verification harness.",
  );
const data = fixture;

async function login(
  page: Page,
  role: keyof Fixture["accounts"],
  path: string,
) {
  await page.goto(path);
  await page.getByLabel("Email address").fill(data.accounts[role].email);
  await page
    .getByLabel("Password", { exact: true })
    .fill(data.accounts[role].password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

async function namedControls(page: Page) {
  const session = await page.context().newCDPSession(page);
  try {
    const tree = await session.send("Accessibility.getFullAXTree");
    const roles = ["button", "link", "textbox", "combobox", "checkbox"];
    const unnamed = tree.nodes.filter(
      (node) =>
        !node.ignored &&
        roles.includes(String(node.role?.value)) &&
        !String(node.name?.value ?? "").trim(),
    );
    expect(
      unnamed.map((node) => node.role?.value),
      "Interactive controls need accessible names",
    ).toEqual([]);
  } finally {
    await session.detach();
  }
}

async function capture(page: Page, filename: string) {
  await namedControls(page);
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.locator(".main-content")).toBeVisible();
  if (process.env.CAPTURE_PORTFOLIO !== "1") return;
  const directory = resolve(
    process.cwd(),
    process.env.PORTFOLIO_SCREENSHOT_DIR ?? "../docs/screenshots",
  );
  await mkdir(directory, { recursive: true });
  await page.mouse.move(1400, 10);
  await page.screenshot({
    path: resolve(directory, filename),
    fullPage: false,
    animations: "disabled",
  });
}

async function mobileWidths(page: Page) {
  for (const width of [320, 375, 390, 430]) {
    await page.setViewportSize({ width, height: 844 });
    await expectNoHorizontalOverflow(page);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
}

test("portfolio workflow, accessible controls and safe real screenshots", async ({
  page,
  browser,
  request,
}) => {
  test.setTimeout(150000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  const auth = await request.post(`${data.api_url}/api/v1/auth/token`, {
    form: {
      username: data.accounts.admin.email,
      password: data.accounts.admin.password,
    },
  });
  expect(auth.status()).toBe(200);
  const token = (await auth.json()).access_token as string;
  const headers = { Authorization: `Bearer ${token}` };
  async function post(
    path: string,
    body: object,
    status: number,
    client: APIRequestContext = request,
  ) {
    const response = await client.post(`${data.api_url}/api/v1${path}`, {
      headers,
      data: body,
    });
    expect(response.status()).toBe(status);
    return response.json();
  }
  // These are real records in the harness's isolated database, never production data.
  const service = await post(
    "/services",
    {
      name: `payment-api-${randomUUID().slice(0, 8)}`,
      environment: "simulation",
      description:
        "SYNTHETIC portfolio scenario: payment requests with database connection pressure.",
    },
    201,
  );
  await post(
    "/detection-rules",
    {
      service_id: service.id,
      name: "Database connection timeouts",
      level: "error",
      error_code: "DB_CONNECTION_TIMEOUT",
      threshold: 3,
      window_seconds: 60,
      cooldown_seconds: 60,
      incident_severity: "high",
    },
    201,
  );
  const key = await post(
    "/ingestion-credentials",
    { service_id: service.id, name: "temporary portfolio producer" },
    201,
  );
  try {
    const response = await request.post(`${data.api_url}/api/v1/logs/ingest`, {
      headers: { "X-Ingestion-Key": key.token },
      data: {
        service_id: service.id,
        logs: Array.from({ length: 12 }, (_, index) => ({
          source_event_id: `portfolio-${index}`,
          timestamp: new Date().toISOString(),
          level: "error",
          error_code: "DB_CONNECTION_TIMEOUT",
          message:
            "SYNTHETIC payment request could not acquire a database connection.",
          trace_id: `demo-payment-trace-${index}`,
          request_id: `demo-request-${index}`,
          metadata: { synthetic: true, scenario: "database_exhaustion" },
        })),
      },
    });
    expect(response.status()).toBe(202);
    await expect
      .poll(
        async () => {
          const result = await request.get(
            `${data.api_url}/api/v1/detection-jobs?service_id=${service.id}&limit=100`,
            { headers },
          );
          const jobs = await result.json();
          return (
            jobs.total === 12 &&
            jobs.items.every(
              (item: { status: string }) => item.status === "completed",
            )
          );
        },
        { timeout: 30000 },
      )
      .toBe(true);
  } finally {
    await post(`/ingestion-credentials/${key.id}/revoke`, {}, 200);
  }
  const detection = await (
    await request.get(
      `${data.api_url}/api/v1/detections?service_id=${service.id}`,
      { headers },
    )
  ).json();
  expect(detection.items).toHaveLength(1);
  const incidentId = detection.items[0].incident_id as string;
  await post(
    `/services/${service.id}/synthetic-state`,
    { scenario: "database_exhaustion", expected_version: 0 },
    200,
  );
  await post(
    "/runbooks",
    {
      title: "Payment database connection checks",
      service_id: service.id,
      content:
        "DB_CONNECTION_TIMEOUT: inspect the active connections, waiting requests and pool configuration in approved read-only views. Human review is required before any synthetic recovery.",
    },
    201,
  );

  await login(page, "engineer", `/incidents/${incidentId}`);
  await expect(
    page.getByRole("heading", { name: "Incident description", exact: true }),
  ).toBeVisible();
  await mobileWidths(page);
  await capture(page, "incident-details.png");
  await page
    .getByRole("button", { name: "Change status", exact: true })
    .click();
  await page.getByLabel("New lifecycle status").selectOption("investigating");
  await page
    .getByLabel("Transition reason")
    .fill("Human review of the isolated portfolio scenario.");
  await page.getByRole("button", { name: "Confirm transition" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Investigations", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Start investigation", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Observed problem", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await expect(page.getByText(/Synthetic provider report/)).toBeVisible();
  await mobileWidths(page);
  await capture(page, "ai-investigation-summary.png");
  await page
    .getByRole("heading", { name: "Unverified root-cause hypotheses" })
    .evaluate((element) => element.scrollIntoView({ block: "start" }));
  await capture(page, "ai-investigation-report.png");
  await page.locator(".citation").first().click();
  await expect(
    page.getByRole("dialog", { name: "Evidence source" }),
  ).toBeVisible();
  await namedControls(page);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".citation").first()).toBeFocused();
  await page
    .getByRole("link", { name: /Create a reviewed proposal/ })
    .first()
    .click();
  await page
    .getByRole("button", { name: "Create resolution proposal", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Create resolution proposal",
  });
  await dialog
    .getByLabel(/Catalog action/)
    .selectOption("simulate_connection_pool_recovery");
  await dialog.locator(".evidence-picker input").first().check();
  await dialog
    .getByLabel("Human explanation")
    .fill(
      "Reviewed the cited timeout records and explicitly selected bounded synthetic pool recovery.",
    );
  await namedControls(page);
  await dialog
    .getByRole("button", { name: "Create immutable proposal", exact: true })
    .click();
  await expect(page).toHaveURL(/\/resolutions\//);
  const proposalPath = new URL(page.url()).pathname;
  await page
    .getByRole("button", { name: "Submit for approval", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Approve proposal", exact: true }),
  ).toBeDisabled();
  const reviewer = await browser.newContext({
    baseURL: process.env.E2E_BASE_URL,
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
  });
  try {
    const second = await reviewer.newPage();
    await login(second, "admin", proposalPath);
    await second
      .getByRole("button", { name: "Approve proposal", exact: true })
      .click();
    await second
      .getByLabel("Review reason")
      .fill(
        "Independently reviewed the allowlisted action and its isolated synthetic target.",
      );
    await second
      .getByRole("button", { name: "Confirm approval", exact: true })
      .click();
    const verified = second.getByRole("heading", {
      name: "Synthetic execution & verification",
      exact: true,
    });
    await expect(verified).toBeVisible({ timeout: 30000 });
    const executionResponse = await request.get(
      `${data.api_url}/api/v1/resolution-proposals/${proposalPath.split("/").pop()}/result`,
      { headers },
    );
    expect(executionResponse.status()).toBe(200);
    const execution = (await executionResponse.json()) as Execution;
    const comparison = second.getByRole("table", {
      name: "Synthetic service state comparison",
    });
    const { before, after } = execution.result;
    const metricValues: [string, string, string][] = [
      ["Service health", before.health, after.health],
      [
        "Request error rate",
        `${before.request_error_rate * 100}%`,
        `${after.request_error_rate * 100}%`,
      ],
      [
        "p50 latency",
        `${before.p50_ms.toLocaleString("en-US")} ms`,
        `${after.p50_ms.toLocaleString("en-US")} ms`,
      ],
      [
        "p95 latency",
        `${before.p95_ms.toLocaleString("en-US")} ms`,
        `${after.p95_ms.toLocaleString("en-US")} ms`,
      ],
      [
        "Active database connections",
        String(before.active_connections),
        String(after.active_connections),
      ],
      [
        "Waiting requests",
        String(before.waiting_requests),
        String(after.waiting_requests),
      ],
      [
        "Connection-pool size",
        String(before.pool_size),
        String(after.pool_size),
      ],
      [
        "Database timeout",
        before.timeout_indicator ? "Yes" : "No",
        after.timeout_indicator ? "Yes" : "No",
      ],
      [
        "Dependency condition",
        before.dependency_condition.replaceAll("_", " "),
        after.dependency_condition.replaceAll("_", " "),
      ],
    ];
    for (const [name, previous, resulting] of metricValues) {
      const cells = comparison
        .getByRole("rowheader", { name, exact: true })
        .locator("..")
        .getByRole("cell");
      expect((await cells.nth(0).innerText()).toLowerCase()).toBe(
        previous.toLowerCase(),
      );
      expect((await cells.nth(1).innerText()).toLowerCase()).toBe(
        resulting.toLowerCase(),
      );
    }
    const raw = second.locator(".execution-raw");
    await expect(raw).not.toHaveAttribute("open");
    await raw.locator("summary").focus();
    await second.keyboard.press("Enter");
    await expect(raw).toHaveAttribute("open");
    expect(JSON.parse(await raw.locator("pre").innerText())).toEqual(
      execution.result,
    );
    await second.keyboard.press("Enter");
    await expect(raw).not.toHaveAttribute("open");
    await raw
      .locator("summary")
      .evaluate((element) => (element as HTMLElement).blur());
    for (const width of [720, 320, 375, 390, 430]) {
      await second.setViewportSize({ width, height: 900 });
      await expect(comparison).toBeVisible();
      await expectNoHorizontalOverflow(second);
      const bounds = await comparison.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
      await expect(
        comparison.getByRole("columnheader", { name: "Before" }),
      ).toBeVisible();
      await expect(
        comparison.getByRole("columnheader", { name: "After" }),
      ).toBeVisible();
      await raw.locator("summary").click();
      await expect(raw.locator("pre")).toBeVisible();
      await expectNoHorizontalOverflow(second);
      await raw.locator("summary").click();
    }
    await second.setViewportSize({ width: 1440, height: 1000 });
    await second.evaluate(() => scrollTo(0, 0));
    await capture(second, "resolution-center.png");
    await verified.evaluate((element) =>
      element.scrollIntoView({ block: "start" }),
    );
    await capture(second, "resolution-verification.png");
  } finally {
    await reviewer.close();
  }
  await page.locator(`a[href="/incidents/${incidentId}"]`).first().click();
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Unified incident timeline" }),
  ).toBeVisible();
  await namedControls(page);
  const timeline = page.locator(".incident-timeline");
  const firstEvent = timeline.locator(".timeline-item").first();
  const metadata = firstEvent.locator("details");
  await expect(firstEvent.locator("time")).toHaveAttribute(
    "datetime",
    /\d{4}-\d{2}-\d{2}T/,
  );
  await expect(
    firstEvent.getByRole("button", { name: "Copy event source ID" }),
  ).toBeVisible();
  expect((await firstEvent.boundingBox())!.height).toBeLessThanOrEqual(120);
  await expect(metadata).not.toHaveAttribute("open");
  await metadata.locator("summary").click();
  await expect(metadata.locator("pre")).toBeVisible();
  await metadata.locator("summary").click();
  let cursorPages = 0;
  while (
    await timeline
      .getByRole("button", { name: "Load more activity" })
      .isVisible()
  ) {
    expect(cursorPages++).toBeLessThan(10);
    const count = await timeline.locator(".timeline-item").count();
    await timeline.getByRole("button", { name: "Load more activity" }).click();
    await expect
      .poll(() => timeline.locator(".timeline-item").count())
      .toBeGreaterThan(count);
  }
  await expect(
    timeline.getByRole("link", { name: "Open investigation →" }).first(),
  ).toHaveAttribute("href", /\/investigations\//);
  await expect(
    timeline.getByRole("link", { name: "Open proposal →" }).first(),
  ).toHaveAttribute("href", /\/resolutions\//);
  await page
    .getByRole("heading", { name: "Unified incident timeline" })
    .evaluate((element) => element.scrollIntoView({ block: "start" }));
  await capture(page, "incident-timeline.png");
  // Reflow checks approximate a 200% desktop layout and a narrow phone viewport.
  for (const width of [720, 320, 375, 390, 430]) {
    await page.setViewportSize({ width, height: 900 });
    await expectNoHorizontalOverflow(page);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("link", { name: "Log explorer", exact: true }).click();
  await page.getByLabel("Service", { exact: true }).selectOption(service.id);
  await page
    .getByRole("button", { name: "Apply filters", exact: true })
    .click();
  await expect(
    page.getByRole("table", { name: "Logs", exact: true }),
  ).toBeVisible();
  await capture(page, "log-explorer.png");
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Operations overview" }),
  ).toBeVisible();
  await mobileWidths(page);
  await capture(page, "operations-overview.png");
});
