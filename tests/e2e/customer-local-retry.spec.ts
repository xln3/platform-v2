import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';
import { installSyntheticHttpResponses, syntheticHttpResponseCount } from './synthetic-http';

test('customer live workspace retries locally after a transient read failure', async ({ page }) => {
  let successfulDashboardRequests = 0;
  let successfulEvidenceRequests = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_retry');
    localStorage.setItem('geo.session.actor', 'customer-retry');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await installSyntheticHttpResponses(page, [
    {
      id: 'customer-dashboard-transient',
      path: '/api/v2/customer-dashboard/projects/prj_customer_retry',
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
  const dashboardMetric = (
    code: string,
    label: string,
    group: string,
    format: 'percentage' | 'score',
    value: number,
  ) => ({
    code,
    label,
    group,
    format,
    direction: 'higher',
    value,
    state: 'ready',
    version: 'customer-metrics-v1',
  });
  const dashboardMetrics = [
    dashboardMetric('geo_visibility_index', 'GEO 可见度指数', 'composite', 'score', 0),
    dashboardMetric('competitive_power_index', '竞争力指数', 'composite', 'score', 0),
    dashboardMetric('source_authority_index', '信源权威指数', 'composite', 'score', 0),
    dashboardMetric('content_readiness_index', '内容准备度指数', 'composite', 'score', 0),
    dashboardMetric('reputation_index', 'AI 口碑指数', 'composite', 'score', 0),
    dashboardMetric('cognition_consistency_index', 'AI 认知一致性指数', 'composite', 'score', 0),
    dashboardMetric('mention_rate', '品牌提及率', 'visibility', 'percentage', 0),
  ];
  await page.route('**/api/v2/customer-dashboard/metrics/catalog**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ schema_version: 'customer-metric-catalog-v1', metrics: [] }),
    }),
  );
  await page.route('**/api/v2/customer-dashboard/projects/**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/answers')) {
      const offset = Number(url.searchParams.get('offset') ?? '0');
      const limit = Number(url.searchParams.get('limit') ?? '20');
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          schema_version: 'customer-answer-page-v1',
          project_pub_id: 'prj_customer_retry',
          data: [],
          page: { total: 0, offset, limit, has_more: false },
        }),
      });
    }
    successfulDashboardRequests += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        schema_version: 'customer-dashboard-v1',
        metric_version: 'customer-metrics-v1',
        project_pub_id: 'prj_customer_retry',
        brand_name: '客户局部重试品牌',
        state: 'ready',
        generated_at: '2026-07-25T01:00:00Z',
        as_of: '2026-07-25T00:00:00Z',
        window: { start: '2026-07-01', end: '2026-07-25', filters: {} },
        metrics: dashboardMetrics,
        models: [],
        competitors: [],
        questions: [],
        sources: [],
        regions: [],
        modes: [],
        trends: [],
        risk: { metrics: [], by_model: [] },
        source_audit: { metrics: [], verdicts: {} },
        snapshot_hash: 'a'.repeat(64),
      }),
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

  await expect(
    page.getByRole('heading', { name: '客户局部重试品牌 · AI 认知资产总览' }),
  ).toBeVisible();
  await expect(
    page.locator('.geo-kpi-card').filter({ hasText: '品牌提及率' }).getByText('0.0%'),
  ).toBeVisible();
  expect(await syntheticHttpResponseCount(page, 'customer-dashboard-transient')).toBe(1);
  expect(successfulDashboardRequests).toBe(1);
  expect(await page.evaluate(() => Reflect.get(window, '__geoLocalRetrySentinel'))).toBe(
    'preserved',
  );

  await page.getByRole('button', { name: '证据中心', exact: true }).click();
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
