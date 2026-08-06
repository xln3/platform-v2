import { expect, type Page } from '@playwright/test';

export async function prepareVisualPage(page: Page, path: string) {
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
  await page.addStyleTag({
    content:
      '*,*::before,*::after{animation-duration:0s!important;transition-duration:0s!important;caret-color:transparent!important}',
  });
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))
    .toBe(true);
}
