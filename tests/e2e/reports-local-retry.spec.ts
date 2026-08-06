import { expect, test } from './runtime-fixture';
import { installSyntheticHttpResponses, syntheticHttpResponseCount } from './synthetic-http';

test('report catalog retries locally after a transient read failure', async ({ page }) => {
  let successfulReportRequests = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_reports_retry');
    localStorage.setItem('geo.session.actor', 'reviewer-retry');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await installSyntheticHttpResponses(page, [
    {
      id: 'report-catalog-transient',
      path: '/api/v2/reports',
      status: 503,
      body: { code: 'temporarily_unavailable' },
      remaining: 1,
    },
  ]);
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_reports_retry',
        user_pub_id: 'usr_reports_retry',
        role: 'reviewer',
        permissions: ['project:read', 'report:review'],
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
            pub_id: 'prj_reports_retry',
            tenant_pub_id: 'tnt_reports_retry',
            name: '报告局部重试项目',
            state: 'active',
            created_at: '2026-07-25T00:00:00Z',
            updated_at: '2026-07-25T00:00:00Z',
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
      body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v2' }),
    }),
  );
  await page.route('**/api/v2/reports**', (route) => {
    successfulReportRequests += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
    });
  });

  await page.goto('/platform/reports/');
  const retry = page.getByRole('button', { name: '重试此区域', exact: true });
  await expect(retry).toBeVisible();
  await page.evaluate(() => {
    Reflect.set(window, '__geoLocalRetrySentinel', 'preserved');
  });

  await retry.click();

  await expect(page.getByText('暂无数据')).toBeVisible();
  expect(await syntheticHttpResponseCount(page, 'report-catalog-transient')).toBe(1);
  expect(successfulReportRequests).toBe(1);
  expect(await page.evaluate(() => Reflect.get(window, '__geoLocalRetrySentinel'))).toBe(
    'preserved',
  );
});
