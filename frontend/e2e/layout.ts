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
  return layout;
}
