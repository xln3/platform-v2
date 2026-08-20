import AxeBuilder from '@axe-core/playwright';
import { expect, type Locator, type Page } from '@playwright/test';

const textSpacingOverride = `
  html *:not(svg):not(svg *) {
    line-height: 1.5 !important;
    letter-spacing: 0.12em !important;
    word-spacing: 0.16em !important;
  }
  html p {
    margin-block-end: 2em !important;
  }
`;

async function waitForLayout(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}

function projectReflowSafety() {
  const root = document.documentElement;
  const overflowingTargets = [...document.querySelectorAll<HTMLElement>('body *')]
    .flatMap((element, index) => {
      const bounds = element.getBoundingClientRect();
      const computed = getComputedStyle(element);
      const visible =
        computed.display !== 'none' &&
        computed.visibility !== 'hidden' &&
        Number.parseFloat(computed.opacity) !== 0 &&
        bounds.width > 0 &&
        bounds.height > 0;
      const outsideViewport = bounds.right > root.clientWidth + 1 && bounds.left < root.scrollWidth;
      let ancestor = element.parentElement;
      let clippedByOverflowAncestor = false;
      while (ancestor && ancestor !== document.body) {
        const ancestorStyle = getComputedStyle(ancestor);
        const ancestorBounds = ancestor.getBoundingClientRect();
        if (
          ['auto', 'scroll', 'hidden', 'clip'].includes(ancestorStyle.overflowX) &&
          ancestorBounds.right <= root.clientWidth + 1 &&
          bounds.right > ancestorBounds.right + 1
        ) {
          clippedByOverflowAncestor = true;
          break;
        }
        ancestor = ancestor.parentElement;
      }
      if (!visible || !outsideViewport || clippedByOverflowAncestor) return [];
      return [
        {
          index,
          element: element.tagName.toLowerCase(),
          className: typeof element.className === 'string' ? element.className : '',
          display: computed.display,
          position: computed.position,
          left: Number(bounds.left.toFixed(2)),
          right: Number(bounds.right.toFixed(2)),
          width: Number(bounds.width.toFixed(2)),
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
        },
      ];
    })
    .slice(0, 25);
  const clippedTargets = [
    ...document.querySelectorAll<HTMLElement>(
      [
        'button:not([disabled])',
        'a[href]',
        'select:not([disabled])',
        'summary',
        '[role="button"]:not([aria-disabled="true"])',
      ].join(','),
    ),
  ]
    .flatMap((element, index) => {
      const bounds = element.getBoundingClientRect();
      const computed = getComputedStyle(element);
      const visuallyHiddenSkipLink =
        element.classList.contains('skip-link') && !element.matches(':focus');
      const visible =
        !visuallyHiddenSkipLink &&
        computed.display !== 'none' &&
        computed.visibility !== 'hidden' &&
        Number.parseFloat(computed.opacity) !== 0 &&
        bounds.width > 0 &&
        bounds.height > 0;
      const measurable = element.clientWidth > 0 && element.clientHeight > 0;
      const clipped =
        element.scrollWidth > element.clientWidth + 1 ||
        element.scrollHeight > element.clientHeight + 1;
      if (!visible || !measurable || !clipped) return [];
      return [
        {
          index,
          element: element.tagName.toLowerCase(),
          className: typeof element.className === 'string' ? element.className : '',
          clientWidth: element.clientWidth,
          clientHeight: element.clientHeight,
          scrollWidth: element.scrollWidth,
          scrollHeight: element.scrollHeight,
        },
      ];
    })
    .slice(0, 25);
  return {
    rootClientWidth: root.clientWidth,
    rootScrollWidth: root.scrollWidth,
    overflowingTargets,
    clippedTargets,
  };
}

async function expectTextSpacingReflow(page: Page) {
  const style = await page.addStyleTag({ content: textSpacingOverride });
  await waitForLayout(page);
  const projection = await page.evaluate(projectReflowSafety);
  await style.evaluate((element) => element.remove());
  expect(
    projection.rootScrollWidth,
    `WCAG text-spacing overrides must not create root-page horizontal overflow. Diagnostics intentionally exclude labels, values and identifiers: ${JSON.stringify(projection.overflowingTargets)}`,
  ).toBeLessThanOrEqual(projection.rootClientWidth + 1);
  expect(
    projection.clippedTargets,
    'WCAG text-spacing overrides must not clip interactive labels. Diagnostics intentionally exclude labels, values and identifiers.',
  ).toEqual([]);
}

async function expectMobileNarrowReflow(page: Page) {
  const viewport = page.viewportSize();
  if (!viewport || viewport.width > 390) return;
  const measure = () => page.evaluate(projectReflowSafety);
  const projection = await (async () => {
    try {
      await page.setViewportSize({ width: 320, height: viewport.height });
      await waitForLayout(page);
      return await measure();
    } finally {
      await page.setViewportSize(viewport);
      await waitForLayout(page);
    }
  })();
  expect(
    projection.rootScrollWidth,
    `WCAG 1.4.10 narrow reflow must not create root-page horizontal overflow. Diagnostics intentionally exclude labels, values and identifiers: ${JSON.stringify(projection.overflowingTargets)}`,
  ).toBeLessThanOrEqual(projection.rootClientWidth + 1);
  expect(
    projection.clippedTargets,
    'WCAG 1.4.10 narrow reflow must not clip interactive labels. Diagnostics intentionally exclude labels, values and identifiers.',
  ).toEqual([]);
}

export async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(
    results.violations,
    results.violations
      .map(
        (violation) =>
          `${violation.id}: ${violation.help}\n${violation.nodes.map((node) => node.target.join(' ')).join('\n')}`,
      )
      .join('\n\n'),
  ).toEqual([]);

  const undersizedTargets = await page
    .locator(
      [
        'button:not([disabled])',
        'a[href]',
        'input:not([disabled]):not([type="hidden"])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        'summary',
        '[role="button"]:not([aria-disabled="true"])',
      ].join(','),
    )
    .evaluateAll((elements) =>
      elements
        .flatMap((element, index) => {
          const style = getComputedStyle(element);
          const bounds = element.getBoundingClientRect();
          const visuallyHiddenSkipLink =
            element.classList.contains('skip-link') && !element.matches(':focus');
          const visible =
            !visuallyHiddenSkipLink &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            Number.parseFloat(style.opacity) !== 0 &&
            bounds.width > 0 &&
            bounds.height > 0;
          if (!visible || (bounds.width >= 24 && bounds.height >= 24)) return [];
          return [
            {
              index,
              element: element.tagName.toLowerCase(),
              width: Number(bounds.width.toFixed(2)),
              height: Number(bounds.height.toFixed(2)),
            },
          ];
        })
        .slice(0, 25),
    );
  expect(
    undersizedTargets,
    'Every visible enabled interactive target must be at least 24 by 24 CSS pixels. Diagnostics intentionally exclude labels, values and identifiers.',
  ).toEqual([]);
  await expectTextSpacingReflow(page);
  await expectMobileNarrowReflow(page);
}

async function expectFocusRing(locator: Locator) {
  await locator.focus();
  await expect(locator).toBeFocused();
  await expect
    .poll(() =>
      locator.evaluate((element) => {
        const style = getComputedStyle(element);
        return {
          focusVisible: element.matches(':focus-visible'),
          outlineStyle: style.outlineStyle,
          outlineWidth: Number.parseFloat(style.outlineWidth),
        };
      }),
    )
    .toMatchObject({
      focusVisible: true,
      outlineStyle: 'solid',
      outlineWidth: 3,
    });
}

async function expectMinimumTarget(locator: Locator) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box?.width ?? 0).toBeGreaterThanOrEqual(24);
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(24);
}

export async function expectSharedInteractionAccessibility(page: Page) {
  const projectSwitcher = page.getByRole('button', { name: /·/ }).first();
  const notifications = page.getByRole('button', { name: '通知' });

  await expectMinimumTarget(projectSwitcher);
  await expectMinimumTarget(notifications);
  await expectFocusRing(projectSwitcher);
  await expectFocusRing(notifications);

  await page.emulateMedia({ forcedColors: 'active' });
  await expectFocusRing(projectSwitcher);
  await expectFocusRing(notifications);
  await page.emulateMedia({ forcedColors: 'none' });

  await page.emulateMedia({ reducedMotion: 'reduce' });
  const reducedMotion = await page.evaluate(() => {
    const probe = document.createElement('div');
    probe.style.animation = 'geo-a11y-motion-probe 5s linear infinite';
    probe.style.transition = 'opacity 5s linear';
    document.body.append(probe);
    const style = getComputedStyle(probe);
    const projection = {
      animationName: style.animationName,
      animationDuration: style.animationDuration,
      transitionDuration: style.transitionDuration,
    };
    probe.remove();
    return projection;
  });
  expect(reducedMotion).toEqual({
    animationName: 'none',
    animationDuration: '0s',
    transitionDuration: '0s',
  });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
}

export async function prepareApp(page: Page, path: string) {
  await page.route('**/api/v2/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'mock-ready',
        service: 'geo-platform-v2',
        version: 'contract-v1',
      }),
    }),
  );
  await page.goto(path);
  await page.locator('main').waitFor();
}
