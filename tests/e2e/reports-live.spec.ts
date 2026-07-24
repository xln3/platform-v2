import { expect, test } from '@playwright/test';

test('validated analyst reviews and publishes the latest generated-contract report', async ({
  page,
}) => {
  const writes: { method: string; url: string; body: unknown }[] = [];
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('requestfailed', (request) => {
    // Chromium reports Playwright's intercepted bodyless 204 as ERR_ABORTED even though fetch
    // receives the contract-success status. Keep every other transport failure as a hard gate.
    if (!request.url().endsWith('/publish')) failedRequests.push(request.url());
  });
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_reports_live');
    localStorage.setItem('geo.session.actor', 'analyst-reports-live');
    localStorage.setItem('geo.session.role', 'analyst');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_reports_live',
        user_pub_id: 'usr_reports_live',
        role: 'analyst',
        permissions: ['project:read', 'project:write'],
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
            pub_id: 'prj_reports_live',
            tenant_pub_id: 'tnt_reports_live',
            name: '真实报告联调项目',
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
  await page.route('**/api/v2/reports**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'GET' && path.endsWith('/reports')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: 'rpt_live_safe',
              project_pub_id: 'prj_reports_live',
              title: '真实季度报告',
              state: 'draft',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
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
          pub_id: 'rpt_live_safe',
          title: '真实季度报告',
          versions: [
            {
              pub_id: 'rpv_live_safe',
              version_number: 1,
              status: 'frozen',
              cookie: 'SESSION=report-detail-canary',
              token: 'Bearer report-detail-canary',
            },
          ],
          profile_path: '/secret/profile/report-detail-canary',
          otp: 824911,
        }),
      });
      return;
    }
    writes.push({
      method: request.method(),
      url: request.url(),
      body: request.postData() ? request.postDataJSON() : null,
    });
    if (path.endsWith('/publish')) {
      await route.fulfill({ status: 204 });
      return;
    }
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ pub_id: 'receipt_safe' }),
    });
  });

  await page.goto('/platform/reports/');
  await page.getByRole('button', { name: /审核发布/ }).click();
  await expect(page.getByText('真实 reports API')).toBeVisible();
  await page.getByText('标记已解决').click();
  await page.getByLabel('新增评论').fill('请记录真实合同评论');
  await page.getByRole('button', { name: '添加评论' }).click();
  await expect(page.getByText('真实审核评论已记录')).toBeVisible();
  await page.getByText('标记已解决').click();
  await page.getByRole('button', { name: '提交审核' }).click();
  await page.getByRole('button', { name: '批准发布' }).click();
  await expect(page.getByText('真实审核决定已记录')).toBeVisible();
  await page.getByRole('button', { name: '发布 v1.0' }).click();
  await expect(page.getByText('真实发布操作已完成')).toBeVisible();

  expect(writes).toHaveLength(3);
  expect(writes.map((write) => new URL(write.url).pathname)).toEqual([
    '/api/v2/reports/rpt_live_safe/versions/rpv_live_safe/comments',
    '/api/v2/reports/rpt_live_safe/versions/rpv_live_safe/reviews',
    '/api/v2/reports/rpt_live_safe/versions/rpv_live_safe/publish',
  ]);
  expect(writes[0]?.body).toEqual({ body: '请记录真实合同评论', parent_pub_id: null });
  expect(writes[1]?.body).toMatchObject({ decision: 'approved' });
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/report-detail-canary|SESSION=|Bearer |824911|\/secret\/profile/i);
  expect(JSON.stringify(writes)).not.toMatch(/Cookie|Bearer|otp|profile_path/i);
  expect(consoleErrors).toEqual([]);
  expect(failedRequests).toEqual([]);
});
