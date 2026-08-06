import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';
import { installSyntheticHttpResponses, syntheticHttpResponseCount } from './synthetic-http';

test('customer live workspace retries locally after a transient read failure', async ({ page }) => {
  let successfulOverviewRequests = 0;
  let successfulEvidenceRequests = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_retry');
    localStorage.setItem('geo.session.actor', 'customer-retry');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await installSyntheticHttpResponses(page, [
    {
      id: 'customer-overview-transient',
      path: '/api/v2/analytics/overview',
      status: 503,
      body: { code: 'temporarily_unavailable' },
      remaining: 1,
    },
    {
      id: 'customer-evidence-transient',
      path: '/api/v2/evidence/assets',
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
        tenant_pub_id: 'tnt_customer_retry',
        user_pub_id: 'usr_customer_retry',
        role: 'customer',
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
            pub_id: 'prj_customer_retry',
            tenant_pub_id: 'tnt_customer_retry',
            name: '客户局部重试项目',
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
  await page.route('**/api/v2/analytics/overview**', (route) => {
    successfulOverviewRequests += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          metric: 'mention_rate',
          value: 0,
          numerator: 0,
          denominator: 4,
          state: 'ready',
          metric_version: 'metric-v1',
          scorer_version: 'scorer-v1',
          filter_hash: 'safe',
          trace_tokens: [],
        },
      ]),
    });
  });
  await page.route('**/api/v2/analytics/answers**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
    }),
  );
  await page.route('**/api/v2/evidence/assets**', (route) => {
    successfulEvidenceRequests += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            pub_id: 'evd_customer_retry_safe',
            kind: 'answer_screenshot',
            mime_type: 'image/png',
            capture_time: '2026-07-25T01:00:00Z',
            sha256: 'a'.repeat(64),
            cookie: 'SESSION=local-retry-evidence-canary',
          },
        ],
        page: { next_cursor: null, has_more: false },
        token: 'Bearer local-retry-page-canary',
      }),
    });
  });

  await page.goto('/platform/customer/');
  const retry = page.getByRole('button', { name: '重试此区域', exact: true });
  await expect(retry).toBeVisible();
  await page.evaluate(() => {
    Reflect.set(window, '__geoLocalRetrySentinel', 'preserved');
  });

  await retry.click();

  await expect(page.getByText('0.0%')).toBeVisible();
  await expect(page.getByText('真实 0', { exact: true })).toBeVisible();
  await expect(page.getByText('暂无数据')).toHaveCount(0);
  expect(await syntheticHttpResponseCount(page, 'customer-overview-transient')).toBe(1);
  expect(successfulOverviewRequests).toBe(1);
  expect(await page.evaluate(() => Reflect.get(window, '__geoLocalRetrySentinel'))).toBe(
    'preserved',
  );

  await page.getByRole('button', { name: '回答证据', exact: true }).click();
  const evidenceRetry = page.getByRole('button', { name: '重试此区域', exact: true });
  await expect(evidenceRetry).toBeVisible();
  await evidenceRetry.click();

  await expect(page.getByRole('heading', { name: '证据中心' })).toBeVisible();
  await expect(page.getByText('evd_customer_retry_safe')).toBeVisible();
  expect(await syntheticHttpResponseCount(page, 'customer-evidence-transient')).toBe(1);
  expect(successfulEvidenceRequests).toBe(1);
  expect(await page.locator('body').innerText()).not.toMatch(
    /SESSION=local-retry-evidence-canary|Bearer local-retry-page-canary/,
  );
  expect(await page.evaluate(() => Reflect.get(window, '__geoLocalRetrySentinel'))).toBe(
    'preserved',
  );
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
  await expectAccessible(page);
});
