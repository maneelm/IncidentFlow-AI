import { expect, type Page } from "@playwright/test";

// Structural geometry only: never capture text, form values, IDs, URLs or HTML.
export async function layoutSnapshot(page: Page) {
  return page.evaluate(() => {
    const viewportWidth = innerWidth;
    const clientWidth = document.documentElement.clientWidth;
    const documentWidth = document.documentElement.scrollWidth;
    const bodyWidth = document.body.scrollWidth;
    const round = (value: number) => Math.round(value * 100) / 100;
    const box = (element: Element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        tag: element.tagName.toLowerCase(),
        classes: Array.from(element.classList)
          .filter((name) => /^[a-z][a-z0-9_-]{0,45}$/i.test(name))
          .slice(0, 6),
        left: round(rect.left),
        right: round(rect.right),
        top: round(rect.top),
        bottom: round(rect.bottom),
        width: round(rect.width),
        height: round(rect.height),
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        display: style.display,
        position: style.position,
        minWidth: style.minWidth,
        maxWidth: style.maxWidth,
        overflowX: style.overflowX,
        flexShrink: style.flexShrink,
        whiteSpace: style.whiteSpace,
      };
    };
    const candidates = Array.from(document.querySelectorAll("body *"))
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.right > innerWidth + 0.5 || rect.left < -0.5;
      })
      .map((element) => {
        let ancestor = element.parentElement;
        let locallyClipped = false;
        const absolute = getComputedStyle(element).position === "absolute";
        const containingBlock =
          element instanceof HTMLElement ? element.offsetParent : null;
        while (ancestor && ancestor !== document.body) {
          if (
            ["auto", "scroll", "hidden", "clip"].includes(
              getComputedStyle(ancestor).overflowX,
            )
          ) {
            const rect = ancestor.getBoundingClientRect();
            if (
              rect.left >= 0 &&
              rect.right <= innerWidth + 0.5 &&
              (!absolute ||
                (containingBlock !== null &&
                  ancestor.contains(containingBlock)))
            ) {
              locallyClipped = true;
              break;
            }
          }
          ancestor = ancestor.parentElement;
        }
        return {
          ...box(element),
          locallyClipped,
          containingBlock: containingBlock ? box(containingBlock) : null,
          parent: element.parentElement ? box(element.parentElement) : null,
        };
      });
    return {
      viewportWidth,
      clientWidth,
      documentWidth,
      bodyWidth,
      scrollX: round(scrollX),
      fontsLoaded: document.fonts.status === "loaded",
      offenders: candidates.filter((item) => !item.locallyClipped).slice(0, 12),
      localScrollElements: candidates.filter((item) => item.locallyClipped)
        .length,
    };
  });
}

export async function expectNoHorizontalOverflow(page: Page) {
  const layout = await layoutSnapshot(page);
  // Keep the original immediate document-width assertion; no polling or retries.
  if (layout.documentWidth > layout.viewportWidth)
    console.log(`LAYOUT_DIAGNOSTIC ${JSON.stringify(layout)}`);
  expect(layout.documentWidth <= layout.viewportWidth).toBe(true);
  await expectHeaderFits(page);
  return layout;
}

export async function expectHeaderFits(page: Page) {
  const geometry = await page.locator(".topbar").evaluate((header) => {
    const bounds = header.getBoundingClientRect();
    const css = getComputedStyle(header);
    const contentLeft = bounds.left + parseFloat(css.paddingLeft);
    const contentRight = bounds.right - parseFloat(css.paddingRight);
    const boxes = [
      ".breadcrumb",
      ".breadcrumb strong",
      ".topbar-right",
      ".connection",
      ".user-avatar",
      ".mobile-toggle",
      ".topbar-right button",
    ]
      .map((selector) => {
        const element = header.querySelector(selector)!;
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName.toLowerCase(),
          classes: Array.from(element.classList),
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height,
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
        };
      })
      .filter((box) => box.width > 0 && box.height > 0);
    return {
      contentLeft,
      contentRight,
      top: bounds.top,
      bottom: bounds.bottom,
      boxes,
    };
  });
  const outside = geometry.boxes.filter(
    (box) =>
      box.left < geometry.contentLeft - 0.5 ||
      box.right > geometry.contentRight + 0.5 ||
      box.top < geometry.top - 0.5 ||
      box.bottom > geometry.bottom + 0.5 ||
      box.scrollWidth > box.clientWidth + 1,
  );
  if (outside.length) {
    const layout = await layoutSnapshot(page);
    console.log(
      `LAYOUT_DIAGNOSTIC ${JSON.stringify({ ...layout, offenders: outside })}`,
    );
  }
  expect(
    outside,
    "Header content must stay inside its padded bounds without clipping",
  ).toEqual([]);
  const breadcrumb = geometry.boxes.find((box) =>
    box.classes.includes("breadcrumb"),
  );
  const controls = geometry.boxes.find((box) =>
    box.classes.includes("topbar-right"),
  );
  expect(breadcrumb!.right).toBeLessThanOrEqual(controls!.left);
}
