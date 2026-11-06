import { expect, test } from "@playwright/test";
import { expectNoHorizontalOverflow } from "./layout";

interface Fixture {
  incident_id: string;
  accounts: Record<"viewer", { email: string; password: string }>;
}
const fixture = JSON.parse(
  process.env.E2E_FIXTURE_JSON ?? "null",
) as Fixture | null;
if (!fixture)
  throw new Error(
    "Use the isolated browser or container verification harness.",
  );
const data = fixture;

for (const state of ["pending", "ready", "unavailable"] as const) {
  test(`mobile header controls fit with API ${state}`, async ({ page }) => {
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    // Hold the actual readiness request pending until assertions finish; no sleeps
    // or geometry retries. Only this test's network response is controlled.
    await page.route("**/api/v1/ready", async (route) => {
      if (state === "pending") await gate;
      await route.fulfill({
        status: state === "unavailable" ? 503 : 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: state === "unavailable" ? "unavailable" : "ready",
        }),
      });
    });
    try {
      await page.goto("/investigations");
      await page.getByLabel("Email address").fill(data.accounts.viewer.email);
      await page
        .getByLabel("Password", { exact: true })
        .fill(data.accounts.viewer.password);
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await expect(
        page.getByRole("button", { name: "Sign out" }),
      ).toBeVisible();
      await expect(page.locator(".connection")).toHaveText(
        state === "pending"
          ? "Connecting"
          : state === "ready"
            ? "API ready"
            : "API unavailable",
      );
      const workspaces = [
        { navigation: "Investigations", heading: "Investigations" },
        { navigation: "Overview", heading: "Operations overview" },
        { navigation: "Incidents", heading: "Incidents" },
        { navigation: "Resolution center", heading: "Resolution center" },
      ];
      for (const workspace of workspaces) {
        await page.setViewportSize({ width: 430, height: 844 });
        await page.getByRole("button", { name: "Open navigation" }).click();
        await page
          .getByRole("dialog", { name: "Navigation" })
          .getByRole("link", { name: workspace.navigation, exact: true })
          .click();
        await expect(
          page.getByRole("dialog", { name: "Navigation" }),
        ).toHaveCount(0);
        await expect(
          page.getByRole("heading", { name: workspace.heading, exact: true }),
        ).toBeVisible();
        // Wider header-only font metrics exercise the same flex constraint as
        // different Linux system fonts; production typography is unchanged.
        for (const font of [null, "monospace"]) {
          const style = font
            ? await page.addStyleTag({
                content: ".topbar { font-family: monospace; }",
              })
            : null;
          for (const width of [320, 375, 390, 430]) {
            await page.setViewportSize({ width, height: 844 });
            await expectNoHorizontalOverflow(page);
            await expect(
              page.getByRole("button", { name: "Sign out" }),
            ).toBeInViewport();
            await expect(
              page.getByRole("button", { name: "Open navigation" }),
            ).toBeInViewport();
          }
          await style?.evaluate((element) =>
            element.parentNode?.removeChild(element),
          );
        }
      }
      if (state === "pending") {
        release();
        await expect(page.locator(".connection")).toHaveText("API ready");
      }
      await page.getByRole("button", { name: "Open navigation" }).focus();
      await page.keyboard.press("Enter");
      await expect(
        page.getByRole("dialog", { name: "Navigation" }),
      ).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(
        page.getByRole("button", { name: "Open navigation" }),
      ).toBeFocused();
      await page.getByRole("button", { name: "Sign out" }).focus();
      await page.keyboard.press("Enter");
      await expect(
        page.getByRole("heading", {
          name: "Sign in to IncidentFlow",
          exact: true,
        }),
      ).toBeVisible();
    } finally {
      release();
    }
  });
}
