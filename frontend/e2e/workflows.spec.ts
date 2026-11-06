import { expect, test, type Page } from "@playwright/test";
import { expectNoHorizontalOverflow } from "./layout";
interface Fixture {
  incident_id: string;
  service_id: string;
  runbook_id: string;
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
    "Use npm run test:e2e so the isolated PostgreSQL fixture is created.",
  );
const data = fixture;
async function login(
  page: Page,
  role: "admin" | "engineer" | "viewer",
  target = "/",
) {
  await page.goto(target);
  await page.getByLabel("Email address").fill(data.accounts[role].email);
  await page
    .getByLabel("Password", { exact: true })
    .fill(data.accounts[role].password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}
async function token(page: Page, role: "admin" | "engineer" | "viewer") {
  const response = await page.request.post(
    `${data.api_url}/api/v1/auth/token`,
    {
      form: {
        username: data.accounts[role].email,
        password: data.accounts[role].password,
      },
    },
  );
  expect(response.status()).toBe(200);
  return (await response.json()).access_token as string;
}

test("A–D: real detected incident, fake investigation, two-person approval and synthetic verification", async ({
  page,
  browser,
}) => {
  await login(page, "engineer", `/incidents/${data.incident_id}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(
    "Detected: Payment Database Failures",
  );
  await page
    .getByRole("button", { name: "Change status", exact: true })
    .click();
  await page.getByLabel("New lifecycle status").selectOption("investigating");
  await page
    .getByLabel("Transition reason")
    .fill("Human engineer begins reviewing the detected synthetic failure.");
  await page
    .getByRole("button", { name: "Confirm transition", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "Logs", exact: true }).click();
  await expect(
    page.getByRole("table", { name: "Logs", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("table").getByText("DB_CONNECTION_TIMEOUT").first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Detections", exact: true }).click();
  await expect(
    page.getByRole("table", { name: "Detections", exact: true }),
  ).toBeVisible();
  await page.getByRole("table").getByRole("link").first().click();
  await expect(
    page.getByRole("heading", { name: "Detection evidence", exact: true }),
  ).toBeVisible();
  await page
    .locator(`a[href="/incidents/${data.incident_id}"]`)
    .first()
    .click();
  await page
    .getByRole("button", { name: "Investigations", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Start investigation", exact: true })
    .click();
  await expect(page).toHaveURL(/\/investigations\//);
  await expect(
    page.getByRole("heading", { name: "Observed problem", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    page.getByText("Synthetic provider report.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Unverified root-cause hypotheses" }),
  ).toBeVisible();
  await page.locator(".citation").first().click();
  await expect(
    page.getByRole("dialog", { name: "Evidence source" }),
  ).toBeVisible();
  await expect(page.getByRole("dialog").locator(".mono").first()).toContainText(
    ":",
  );
  await page.getByRole("button", { name: "Close dialog" }).click();
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
      "I reviewed the real investigation sources and explicitly request isolated synthetic pool recovery.",
    );
  const request = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith("/resolution-proposals"),
  );
  await dialog
    .getByRole("button", { name: "Create immutable proposal", exact: true })
    .click();
  const payload = (await request).postDataJSON();
  expect(Object.keys(payload.parameters).sort()).toEqual([
    "expected_state_version",
    "state_id",
  ]);
  expect(payload.origin).toBe("human");
  expect(payload.evidence_refs.length).toBeGreaterThan(0);
  await expect(page).toHaveURL(/\/resolutions\//);
  const proposalPath = new URL(page.url()).pathname;
  await page
    .getByRole("button", { name: "Submit for approval", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Approve proposal", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText(
      /A different engineer or administrator must review this synthetic change/,
    ),
  ).toBeVisible();
  const reviewer = await browser.newContext({
    baseURL: process.env.E2E_BASE_URL,
  });
  const second = await reviewer.newPage();
  try {
    await login(second, "admin", proposalPath);
    await second
      .getByRole("button", { name: "Approve proposal", exact: true })
      .click();
    await second
      .getByLabel("Review reason")
      .fill(
        "I independently reviewed the immutable action, target and selected evidence.",
      );
    await second
      .getByRole("button", { name: "Confirm approval", exact: true })
      .click();
    await expect(
      second.getByRole("heading", {
        name: "Synthetic execution & verification",
        exact: true,
      }),
    ).toBeVisible({ timeout: 30000 });
    await expect(
      second.getByText("Incident resolution confirmed: false", {
        exact: false,
      }),
    ).toBeVisible();
  } finally {
    await reviewer.close();
  }
  await page
    .locator(`a[href="/incidents/${data.incident_id}"]`)
    .first()
    .click();
  await expect(
    page
      .locator(".resource-summary")
      .getByText("Investigating", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Unified incident timeline" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Load more activity" }).click();
  await expect(
    page.getByText("Execution Succeeded", { exact: true }),
  ).toBeVisible();
});

test("incident create, assignment, edit and optimistic conflict preserve the other writer", async ({
  page,
}) => {
  await login(page, "admin", "/incidents");
  await page
    .getByRole("button", { name: "Create incident", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Incident title")
    .fill("Browser operator-created incident");
  await dialog
    .getByLabel("Description", { exact: true })
    .fill("An incident authored through the real React form.");
  await dialog
    .getByRole("combobox", { name: "Service", exact: true })
    .selectOption(data.service_id);
  await dialog
    .getByRole("button", { name: "Create incident", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Browser operator-created incident",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Assign", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Assignee", exact: true })
    .selectOption({ label: "Browser engineer · engineer" });
  await page
    .getByRole("button", { name: "Update assignment", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Edit incident", exact: true })
    .click();
  const edit = page.getByRole("dialog");
  await edit.getByLabel("Incident title").fill("Local unsaved title");
  const id = new URL(page.url()).pathname.split("/").pop();
  const access = await token(page, "admin");
  const current = await (
    await page.request.get(`${data.api_url}/api/v1/incidents/${id}`, {
      headers: { Authorization: `Bearer ${access}` },
    })
  ).json();
  const changed = await page.request.patch(
    `${data.api_url}/api/v1/incidents/${id}`,
    {
      headers: { Authorization: `Bearer ${access}` },
      data: {
        expected_version: current.version,
        title: "Concurrent reviewed title",
      },
    },
  );
  expect(changed.status()).toBe(200);
  await edit.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(edit.getByRole("alert")).toContainText("record changed");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "Concurrent reviewed title",
      exact: true,
    }),
  ).toBeVisible();
});

test("E: viewer controls are absent, backend writes denied, and tokens disappear on reload", async ({
  page,
}) => {
  await login(page, "viewer", "/incidents");
  await expect(
    page.getByRole("button", { name: "Create incident", exact: true }),
  ).toHaveCount(0);
  await page
    .locator(`a[href="/incidents/${data.incident_id}"]`)
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Edit incident", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Investigations", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Start investigation", exact: true }),
  ).toHaveCount(0);
  const access = await token(page, "viewer");
  const denied = await page.request.post(
    `${data.api_url}/api/v1/incidents/${data.incident_id}/investigations`,
    { headers: { Authorization: `Bearer ${access}` }, data: {} },
  );
  expect(denied.status()).toBe(403);
  expect(
    await page.evaluate(() => ({
      local: localStorage.length,
      session: sessionStorage.length,
    })),
  ).toEqual({ local: 0, session: 0 });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Sign in to IncidentFlow" }),
  ).toBeVisible();
});

test("real log pagination, runbook version browsing, and mobile navigation", async ({
  page,
}) => {
  // An assigned row renders Identifier's live region in an offscreen table cell.
  // Own this fixture so the regression also runs independently of earlier tests.
  const access = await token(page, "admin");
  const headers = { Authorization: `Bearer ${access}` };
  const directory = await page.request.get(
    `${data.api_url}/api/v1/directory/users`,
    { headers },
  );
  expect(directory.status()).toBe(200);
  const assignee = (await directory.json()).items.find(
    (item: { role: string }) => item.role === "engineer",
  );
  expect(assignee).toBeTruthy();
  const created = await page.request.post(`${data.api_url}/api/v1/incidents`, {
    headers,
    data: {
      service_id: data.service_id,
      assignee_id: assignee.id,
      title:
        `SYNTHETIC mobile layout: ${"long-technical-identifier-".repeat(7)}`.slice(
          0,
          200,
        ),
      description:
        "SYNTHETIC assigned incident for responsive table and copy-status coverage.",
      severity: "medium",
    },
  });
  expect(created.status()).toBe(201);
  const assignedIncident = await created.json();
  await login(page, "viewer", "/logs");
  await expect(
    page.getByRole("table", { name: "Logs", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.locator(".pagination")).toContainText("21–");
  await page.getByRole("link", { name: "Runbooks", exact: true }).click();
  await page.locator(`a[href="/runbooks/${data.runbook_id}"]`).click();
  await expect(
    page
      .getByRole("heading", {
        name: "Browser database pool runbook",
        exact: true,
      })
      .first(),
  ).toBeVisible();
  await page.getByLabel("Document version").selectOption("1");
  await expect(page.locator(".document-content")).toContainText(
    "DB_CONNECTION_TIMEOUT",
  );
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "Operations overview" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Navigation" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Open navigation" }),
  ).toBeFocused();
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page
    .getByRole("dialog", { name: "Navigation" })
    .getByRole("link", { name: "Incidents", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Incidents", exact: true }),
  ).toBeVisible();
  const assignedRow = page.locator("tr").filter({
    has: page.locator(`a[href="/incidents/${assignedIncident.id}"]`),
  });
  await expect(assignedRow.locator(".identifier .sr-only")).toHaveCount(1);
  await expectNoHorizontalOverflow(page);
  const region = page.getByRole("region", {
    name: "Incidents table",
    exact: true,
  });
  for (const width of [320, 375, 390, 430]) {
    await page.setViewportSize({ width, height: 844 });
    await expectNoHorizontalOverflow(page);
    expect(
      await region.evaluate(
        (element) => element.scrollWidth > element.clientWidth,
      ),
    ).toBe(true);
    await region.evaluate((element) => {
      element.scrollLeft = element.scrollWidth;
    });
    await expect(
      page.getByRole("columnheader", { name: "Created", exact: true }),
    ).toBeInViewport();
    await expect(
      assignedRow.getByRole("button", { name: "Copy user ID" }),
    ).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await region.evaluate((element) => {
      element.scrollLeft = 0;
    });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "test-results/mobile-incidents.png",
    fullPage: true,
  });
});

test("desktop overview is real and responsive", async ({ page }) => {
  await page.setViewportSize({ width: 1512, height: 1050 });
  await login(page, "admin");
  await expect(
    page.getByRole("heading", { name: "Operations overview" }),
  ).toBeVisible();
  await expect(page.locator(".stat-card").first()).not.toContainText("0");
  await expect(
    page.getByText("Database records", { exact: false }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/overview-desktop.png",
    fullPage: true,
  });
});
