import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';

test('oversized propagation graph stays bounded, explicit and secret-free', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_performance');
    localStorage.setItem('geo.session.actor', 'reviewer-intelligence-performance');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_performance',
        user_pub_id: 'usr_intelligence_performance',
        role: 'reviewer',
        permissions: ['intelligence:read', 'intelligence:review'],
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
            pub_id: 'prj_intelligence_performance',
            tenant_pub_id: 'tnt_intelligence_performance',
            name: '传播图性能项目',
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
  await page.route('**/api/v2/intelligence/investigations**', (route) => {
    const requestUrl = new URL(route.request().url());
    const path = requestUrl.pathname;
    if (path.endsWith('/investigations')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: 'inv_intelligence_performance',
              title: '大型传播关系性能案件',
              state: 'review',
              access_class: 'customer_private',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
              claim_count: 1,
              source_cluster_count: 50,
              probability: '0.70',
              latest_verdict: null,
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/page-history') || path.endsWith('/visual-diffs')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pub_id: 'inv_intelligence_performance',
        scores: [
          {
            pub_id: 'score_intelligence_performance',
            probability: 0.7,
            evidence_sufficiency: 0.8,
            uncertainty: 0.2,
            rule_version: 'large-graph-v1',
            explanation: { basis: '大型传播图使用受控浏览器投影' },
            created_at: '2026-07-25T00:00:00Z',
          },
        ],
        claims: [
          {
            pub_id: 'clm_intelligence_performance',
            normalized_text: '大型传播关系需要受控展示',
            verifiability: 'verifiable',
          },
        ],
        evidence_matrix: [],
        source_independence: [],
        graph: Array.from({ length: 500 }, (_, index) => ({
          from_pub_id: `src_performance_${index}`,
          to_pub_id: `dst_performance_${index}`,
          relation: index === 2 ? 'Bearer large-graph-canary' : 'mentions',
          weight: 0.8,
          evidence_pub_id: `evd_performance_${index}`,
        })),
        appeals: [],
        verdicts: [],
        token: 'Bearer large-graph-root-canary',
      }),
    });
  });

  const started = Date.now();
  await page.goto('/platform/intelligence/');
  await expect(page.getByText('大型传播关系性能案件', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '传播关系' }).click();
  const graphTable = page.getByRole('table', { name: '传播图节点与关系' });
  await expect(graphTable.locator('tbody tr')).toHaveCount(119);
  await expect(
    page.getByText('传播关系：服务返回 500 条，浏览器安全视图展示 119 条'),
  ).toBeVisible();
  await expect(page.getByText('安全投影不完整', { exact: true })).toBeVisible();
  await expect(page.locator('.flow-canvas .react-flow')).toBeVisible();
  expect(Date.now() - started).toBeLessThan(10_000);

  await expectAccessible(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/large-graph-(?:canary|root-canary)|Bearer /i);
});
