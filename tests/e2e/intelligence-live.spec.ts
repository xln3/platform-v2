import { expect, test } from '@playwright/test';

test('validated reviewer records a verdict and appeal through generated contracts', async ({
  page,
}) => {
  const writes: { url: string; body: unknown }[] = [];
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('requestfailed', (request) => failedRequests.push(request.url()));
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_live');
    localStorage.setItem('geo.session.actor', 'reviewer-intelligence-live');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_live',
        user_pub_id: 'usr_intelligence_live',
        role: 'reviewer',
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
            pub_id: 'prj_intelligence_live',
            tenant_pub_id: 'tnt_intelligence_live',
            name: '真实调查联调项目',
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
  await page.route('**/api/v2/intelligence/investigations**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'GET' && path.endsWith('/investigations')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: 'inv_live_safe',
              title: '真实调查案件',
              state: 'review',
              access_class: 'customer_private',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
              claim_count: 3,
              source_cluster_count: 2,
              probability: 0.73,
              latest_verdict: null,
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
      return;
    }
    if (request.method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          pub_id: 'inv_live_safe',
          scores: [
            {
              probability: 0.73,
              evidence_sufficiency: 0.82,
              uncertainty: 0.19,
              cookie: 'SESSION=intelligence-detail-canary',
            },
          ],
          token: 'Bearer intelligence-detail-canary',
          profile_path: '/secret/profile/intelligence-detail-canary',
          otp: 824911,
        }),
      });
      return;
    }
    writes.push({ url: request.url(), body: request.postDataJSON() });
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ pub_id: `receipt_${writes.length}` }),
    });
  });

  await page.goto('/platform/intelligence/');
  await page.getByRole('button', { name: /裁决与申诉/ }).click();
  await expect(page.getByText('真实 intelligence API')).toBeVisible();
  await expect(page.getByText('0.73', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '确认高风险表述' }).click();
  await expect(page.getByText('真实人工裁决已记录')).toBeVisible();
  await page.getByLabel('申诉理由').fill('补充新的独立来源并申请重新复核');
  await page.getByRole('button', { name: '提交申诉' }).click();
  await expect(page.getByText('真实申诉已登记')).toBeVisible();

  expect(writes).toHaveLength(2);
  expect(new URL(writes[0]!.url).pathname).toBe(
    '/api/v2/intelligence/investigations/inv_live_safe/verdicts',
  );
  expect(writes[0]?.body).toMatchObject({ verdict: 'confirmed' });
  expect(new URL(writes[1]!.url).pathname).toBe(
    '/api/v2/intelligence/investigations/inv_live_safe/appeals',
  );
  expect(writes[1]?.body).toEqual({ reason: '补充新的独立来源并申请重新复核' });
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /intelligence-detail-canary|SESSION=|Bearer |824911|\/secret\/profile/i,
  );
  expect(JSON.stringify(writes)).not.toMatch(/Cookie|Bearer|otp|profile_path/i);
  expect(consoleErrors).toEqual([]);
  expect(failedRequests).toEqual([]);
});
