import { expect, test } from '@playwright/test';

export function verifyHostileUrlBoundary({
  product,
  path,
  role,
  heading,
  defaultSection,
}: {
  product: string;
  path: string;
  role: 'customer' | 'operator' | 'analyst' | 'reviewer';
  heading: string;
  defaultSection: string;
}) {
  test(`${product} removes hostile long URL values before rendering business content`, async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.on('requestfailed', (request) => failedRequests.push(request.url()));
    await page.addInitScript(
      ({ productRole }) => {
        localStorage.setItem('geo.session.tenant', 'tnt_hostile_url');
        localStorage.setItem('geo.session.actor', `${productRole}-hostile-url`);
        localStorage.setItem('geo.session.role', productRole);
      },
      { productRole: role },
    );
    await page.route('**/api/v2/identity/session', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tenant_pub_id: 'tnt_hostile_url',
          user_pub_id: `usr_${role}_hostile_url`,
          role,
          permissions: ['project:read'],
        }),
      }),
    );
    await page.route('**/api/v2/projects**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: 'prj_hostile_url',
              tenant_pub_id: 'tnt_hostile_url',
              name: 'URL 边界项目',
              state: 'active',
              created_at: '2026-07-24T00:00:00Z',
              updated_at: '2026-07-24T00:00:00Z',
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      }),
    );
    await page.route('**/api/v2/health', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v1' }),
      }),
    );
    for (const endpoint of ['**/api/v2/reports?**', '**/api/v2/intelligence/investigations?**']) {
      await page.route(endpoint, (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
        }),
      );
    }
    const hostileSection = `${'超长路由'.repeat(100)} Bearer dlp-shared-url-canary`;
    const started = Date.now();
    await page.goto(
      `${path}?section=${encodeURIComponent(hostileSection)}&access_token=token-dlp-canary&safe_note=retained`,
    );
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: defaultSection, exact: true })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await expect
      .poll(() => page.url())
      .not.toMatch(/dlp-shared-url-canary|access_token|token-dlp-canary/);
    expect(page.url()).toContain('safe_note=retained');
    expect(page.url().length).toBeLessThan(300);
    expect(Date.now() - started).toBeLessThan(10_000);
    const surfaces = await page.evaluate(() =>
      JSON.stringify({
        url: location.href,
        text: document.body.textContent,
        localStorage: { ...localStorage },
        sessionStorage: { ...sessionStorage },
      }),
    );
    expect(surfaces).not.toMatch(/dlp-shared-url-canary|token-dlp-canary/);
    expect(consoleErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
  });
}
