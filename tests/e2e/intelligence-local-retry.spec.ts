import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';
import { installSyntheticHttpResponses, syntheticHttpResponseCount } from './synthetic-http';

test('investigation catalog retries locally after a transient read failure', async ({ page }) => {
  let successfulInvestigationRequests = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_retry');
    localStorage.setItem('geo.session.actor', 'reviewer-retry');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await installSyntheticHttpResponses(page, [
    {
      id: 'intelligence-catalog-transient',
      path: '/api/v2/intelligence/investigations',
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
        tenant_pub_id: 'tnt_intelligence_retry',
        user_pub_id: 'usr_intelligence_retry',
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
            pub_id: 'prj_intelligence_retry',
            tenant_pub_id: 'tnt_intelligence_retry',
            name: '调查局部重试项目',
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
    successfulInvestigationRequests += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
    });
  });

  await page.goto('/platform/intelligence/');
  const retry = page.getByRole('button', { name: '重试此区域', exact: true });
  await expect(retry).toBeVisible();
  await page.evaluate(() => {
    Reflect.set(window, '__geoLocalRetrySentinel', 'preserved');
  });

  await retry.click();

  await expect(page.getByText('暂无数据')).toBeVisible();
  expect(await syntheticHttpResponseCount(page, 'intelligence-catalog-transient')).toBe(1);
  expect(successfulInvestigationRequests).toBe(1);
  expect(await page.evaluate(() => Reflect.get(window, '__geoLocalRetrySentinel'))).toBe(
    'preserved',
  );
});

test('browser back discards a slower superseded investigation response', async ({ page }) => {
  let delayedPageRequested = false;
  let delayedPageResolved = false;
  let secondPageDetailRequests = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_race');
    localStorage.setItem('geo.session.actor', 'reviewer-race');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_race',
        user_pub_id: 'usr_intelligence_race',
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
            pub_id: 'prj_intelligence_race',
            tenant_pub_id: 'tnt_intelligence_race',
            name: '调查竞态项目',
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
    const requestUrl = new URL(route.request().url());
    const path = requestUrl.pathname;
    if (path.endsWith('/investigations')) {
      const secondPage = requestUrl.searchParams.get('cursor') === 'inv_race_cursor_02';
      if (secondPage) {
        delayedPageRequested = true;
        await new Promise((resolve) => setTimeout(resolve, 700));
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: secondPage ? 'inv_race_page_02' : 'inv_race_page_01',
              title: secondPage ? '不应覆盖的第二页案件' : '当前第一页案件',
              state: 'review',
              access_class: 'customer_private',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
              claim_count: 1,
              source_cluster_count: 1,
              probability: '0.7',
              latest_verdict: null,
            },
          ],
          page: {
            next_cursor: secondPage ? null : 'inv_race_cursor_02',
            has_more: !secondPage,
            token: 'Bearer stale-list-canary',
          },
        }),
      });
      if (secondPage) delayedPageResolved = true;
      return;
    }
    if (path.endsWith('/page-history') || path.endsWith('/visual-diffs')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    }
    const secondPage = path.includes('inv_race_page_02');
    if (secondPage) secondPageDetailRequests += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pub_id: secondPage ? 'inv_race_page_02' : 'inv_race_page_01',
        scores: [
          {
            pub_id: secondPage ? 'score_race_page_02' : 'score_race_page_01',
            probability: secondPage ? '0.2' : '0.7',
            evidence_sufficiency: '0.8',
            uncertainty: '0.2',
            rule_version: 'race-v1',
            explanation: { basis: secondPage ? '第二页解释' : '第一页解释' },
            created_at: '2026-07-25T00:00:00Z',
          },
        ],
        claims: [
          {
            pub_id: secondPage ? 'clm_race_page_02' : 'clm_race_page_01',
            normalized_text: secondPage ? '不应覆盖的第二页 Claim' : '当前第一页 Claim',
            verifiability: 'verifiable',
          },
        ],
        evidence_matrix: [],
        source_independence: [],
        graph: [],
        appeals: [],
        verdicts: [],
        cookie: 'SESSION=stale-detail-canary',
      }),
    });
  });

  await page.goto('/platform/intelligence/');
  await expect(page.getByText('当前第一页案件', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/case_page=2/);
  await expect(page.getByText('数据正在安全获取，请稍候。')).toBeVisible();
  await expect(page.getByText('当前第一页案件', { exact: true })).toHaveCount(0);
  await expect.poll(() => delayedPageRequested).toBe(true);
  await page.goBack();
  await expect(page).not.toHaveURL(/case_(?:page|cursor)=/);
  await expect(page.getByText('当前第一页案件', { exact: true })).toBeVisible();
  await expect.poll(() => delayedPageResolved).toBe(true);
  await page.waitForTimeout(100);
  expect(secondPageDetailRequests).toBe(0);
  await expect(page.getByText('不应覆盖的第二页案件', { exact: true })).toHaveCount(0);

  await page.getByRole('button', { name: 'Claim 矩阵' }).click();
  await expect(page.getByText('当前第一页 Claim')).toBeVisible();
  await expect(page.getByText('不应覆盖的第二页 Claim')).toHaveCount(0);
  await expectAccessible(page);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/stale-(?:list|detail)-canary|SESSION=|Bearer /i);
});

test('case navigation discards a slower superseded verdict receipt', async ({ page }) => {
  let delayedWriteRequested = false;
  let delayedWriteResolved = false;
  let detailReadsAfterWriteReceipt = 0;
  const writePaths: string[] = [];
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_write_scope');
    localStorage.setItem('geo.session.actor', 'reviewer-write-scope');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_write_scope',
        user_pub_id: 'usr_intelligence_write_scope',
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
            pub_id: 'prj_intelligence_write_scope',
            tenant_pub_id: 'tnt_intelligence_write_scope',
            name: '调查写回执隔离项目',
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
    const requestUrl = new URL(request.url());
    const path = requestUrl.pathname;
    if (request.method() === 'POST') {
      writePaths.push(path);
      delayedWriteRequested = true;
      await new Promise((resolve) => setTimeout(resolve, 900));
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          verdict_pub_id: 'vrd_write_scope_page_02',
          token: 'Bearer superseded-write-receipt-canary',
        }),
      });
      delayedWriteResolved = true;
      return;
    }
    if (path.endsWith('/investigations')) {
      const secondPage = requestUrl.searchParams.get('cursor') === 'inv_write_scope_cursor_02';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: secondPage ? 'inv_write_scope_page_02' : 'inv_write_scope_page_01',
              title: secondPage ? '等待旧裁决的第二页案件' : '当前第一页既有裁决案件',
              state: secondPage ? 'review' : 'decided',
              access_class: 'customer_private',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
              claim_count: 0,
              source_cluster_count: 0,
              probability: '0.7',
              latest_verdict: secondPage ? null : 'unlikely',
            },
          ],
          page: {
            next_cursor: secondPage ? null : 'inv_write_scope_cursor_02',
            has_more: !secondPage,
          },
        }),
      });
      return;
    }
    if (path.endsWith('/page-history') || path.endsWith('/visual-diffs')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
      return;
    }
    const secondPage = path.includes('inv_write_scope_page_02');
    if (delayedWriteResolved) detailReadsAfterWriteReceipt += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pub_id: secondPage ? 'inv_write_scope_page_02' : 'inv_write_scope_page_01',
        scores: [
          {
            pub_id: secondPage ? 'score_write_scope_page_02' : 'score_write_scope_page_01',
            probability: '0.7',
            evidence_sufficiency: '0.8',
            uncertainty: '0.2',
            rule_version: 'write-scope-v1',
            explanation: { basis: '当前案件的安全解释' },
            created_at: '2026-07-25T00:00:00Z',
          },
        ],
        claims: [],
        evidence_matrix: [],
        source_independence: [],
        graph: [],
        appeals: [],
        verdicts: secondPage
          ? []
          : [
              {
                pub_id: 'vrd_write_scope_page_01',
                verdict: 'unlikely',
                reviewer_pub_id: 'usr_write_scope_reviewer',
                rationale: '当前第一页既有人工裁决理由。',
                supersedes_pub_id: null,
                created_at: '2026-07-25T01:00:00Z',
              },
            ],
      }),
    });
  });

  await page.goto('/platform/intelligence/');
  await expect(page.getByText('当前第一页既有裁决案件', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page.getByText('等待旧裁决的第二页案件', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /裁决与申诉/ }).click();
  await page.getByRole('button', { name: '确认高风险表述' }).click();
  await expect.poll(() => delayedWriteRequested).toBe(true);

  await page.goBack();
  await page.goBack();
  await expect(page).not.toHaveURL(/case_(?:page|cursor)=/);
  await expect(page.getByText('rejected', { exact: true })).toBeVisible();
  await expect.poll(() => delayedWriteResolved).toBe(true);
  await page.waitForTimeout(100);
  await expect(page.getByText('rejected', { exact: true })).toBeVisible();
  await expect(page.getByText('confirmed', { exact: true })).toHaveCount(0);
  expect(writePaths).toEqual([
    '/api/v2/intelligence/investigations/inv_write_scope_page_02/verdicts',
  ]);
  expect(detailReadsAfterWriteReceipt).toBe(0);
  await expectAccessible(page);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/superseded-write-receipt-canary|Bearer /i);
});
