import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';

test('oversized report detail stays bounded, explicit and write-locked', async ({ page }) => {
  const writes: string[] = [];
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_report_performance');
    localStorage.setItem('geo.session.actor', 'admin-report-performance');
    localStorage.setItem('geo.session.role', 'admin');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_report_performance',
        user_pub_id: 'usr_report_performance',
        role: 'admin',
        permissions: ['project:read', 'report:write', 'report:review', 'report:publish'],
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
            pub_id: 'prj_report_performance',
            tenant_pub_id: 'tnt_report_performance',
            name: '大型报告性能项目',
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
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      writes.push(`${request.method()} ${path}`);
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ pub_id: 'unexpected_write' }),
      });
    }
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: 'rpt_report_performance',
              project_pub_id: 'prj_report_performance',
              title: '大型有界报告',
              state: 'review',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    const currentComponents = Array.from({ length: 101 }, (_, index) => ({
      pub_id: `rptc_report_performance_003_${index}`,
      report_version_pub_id: 'rptv_report_performance_003',
      component_type: 'section',
      ordinal: index,
      source: index % 2 ? 'human' : 'ai',
      payload: {
        title: `大型章节 ${index}`,
        body: `第 ${index} 章。${'受控长文本。'.repeat(180)}`,
        evidence_pub_ids: Array.from(
          { length: 101 },
          (__, evidenceIndex) => `evd_report_${index}_${evidenceIndex}`,
        ),
      },
      created_at: new Date(Date.UTC(2026, 6, 25, 0, 0, index)).toISOString(),
    }));
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pub_id: 'rpt_report_performance',
        project_pub_id: 'prj_report_performance',
        title: '大型有界报告',
        state: 'review',
        created_at: '2026-07-25T00:00:00Z',
        updated_at: '2026-07-25T01:00:00Z',
        versions: [
          {
            pub_id: 'rptv_report_performance_001',
            version_number: 1,
            window_start: '2026-06-01T00:00:00Z',
            window_end: '2026-06-30T23:59:59Z',
            filters: { region: 'global' },
            metric_version: 'metric-v2',
            scorer_version: 'scorer-v2',
            fact_snapshot_hash: '1'.repeat(64),
            status: 'review',
            components: [],
            frozen_facts: [],
            artifacts: [],
            evidence_bindings: [],
            reviews: [],
            comments: [],
            events: [],
          },
          {
            pub_id: 'rptv_report_performance_002',
            version_number: 2,
            window_start: '2026-06-01T00:00:00Z',
            window_end: '2026-06-30T23:59:59Z',
            filters: { region: 'global' },
            metric_version: 'metric-v2',
            scorer_version: 'scorer-v2',
            fact_snapshot_hash: '2'.repeat(64),
            status: 'review',
            components: [
              {
                pub_id: 'rptc_report_performance_002_0',
                report_version_pub_id: 'rptv_report_performance_002',
                component_type: 'section',
                ordinal: 0,
                source: 'human',
                payload: { title: '大型章节 0', body: '上一版受控正文。' },
                created_at: '2026-07-25T00:00:00Z',
              },
            ],
            frozen_facts: [],
            artifacts: [],
            evidence_bindings: [],
            reviews: [],
            comments: [],
            events: [],
          },
          {
            pub_id: 'rptv_report_performance_003',
            version_number: 3,
            window_start: '2026-06-01T00:00:00Z',
            window_end: '2026-06-30T23:59:59Z',
            filters: { region: 'global' },
            metric_version: 'metric-v2',
            scorer_version: 'scorer-v2',
            fact_snapshot_hash: '3'.repeat(64),
            status: 'review',
            frozen_facts: Array.from({ length: 501 }, (_, index) => ({
              pub_id: `rptf_report_performance_003_${index}`,
              report_version_pub_id: 'rptv_report_performance_003',
              ordinal: index,
              payload: { metric: `metric_${index}`, value: index },
              payload_hash: 'a'.repeat(64),
              created_at: new Date(Date.UTC(2026, 6, 25, 0, 0, index)).toISOString(),
            })),
            components: currentComponents,
            artifacts: [
              {
                pub_id: 'rpta_report_performance_003_html',
                report_version_pub_id: 'rptv_report_performance_003',
                format: 'html',
                evidence_pub_id: 'evd_report_performance_003_html',
                mime_type: 'text/html',
                byte_size: 4096,
                sha256: 'c'.repeat(64),
                created_at: '2026-07-25T00:20:00Z',
              },
              {
                pub_id: 'rpta_report_performance_003_pdf',
                report_version_pub_id: 'rptv_report_performance_003',
                format: 'pdf',
                evidence_pub_id: 'evd_report_performance_003_pdf',
                mime_type: 'application/pdf',
                byte_size: 8192,
                sha256: 'd'.repeat(64),
                created_at: '2026-07-25T00:21:00Z',
              },
            ],
            evidence_bindings: Array.from({ length: 501 }, (_, index) => ({
              pub_id: `rptev_report_performance_003_${index}`,
              report_version_pub_id: 'rptv_report_performance_003',
              evidence_pub_id: `evd_binding_${index}`,
              purpose: 'frozen_fact_or_component',
              kind: 'answer_screenshot',
              access_class: 'customer_private',
              mime_type: 'image/png',
              byte_size: 2048 + index,
              sha256: 'b'.repeat(64),
              anchor_count: index,
              capture_time: new Date(Date.UTC(2026, 6, 24, 0, 0, index)).toISOString(),
              created_at: new Date(Date.UTC(2026, 6, 25, 0, 0, index)).toISOString(),
            })),
            comments: Array.from({ length: 501 }, (_, index) => ({
              pub_id: `cmt_report_${index}`,
              report_version_pub_id: 'rptv_report_performance_003',
              parent_pub_id: null,
              author_pub_id: 'usr_report_performance',
              body:
                index === 500 ? 'Cookie=oversized-report-comment-canary' : `待审核评论 ${index}`,
              resolved_at: null,
              created_at: new Date(Date.UTC(2026, 6, 25, 0, 0, index)).toISOString(),
            })),
            reviews: [],
            events: [],
          },
        ],
        optimization_actions: Array.from({ length: 201 }, (_, index) => ({
          pub_id: `act_report_${index}`,
          description: `优化行动 ${index}`,
          owner_pub_id: null,
          state: 'done',
          baseline: { version_number: 2 },
          outcome: { completed: true },
          created_at: new Date(Date.UTC(2026, 6, 25, 0, 0, index)).toISOString(),
          updated_at: new Date(Date.UTC(2026, 6, 26, 0, 0, index)).toISOString(),
          effect_retests:
            index === 200
              ? Array.from({ length: 201 }, (__, retestIndex) => ({
                  pub_id: `rts_report_200_${retestIndex}`,
                  action_pub_id: retestIndex === 200 ? 'act_report_cross_action' : 'act_report_200',
                  measured_at: new Date(Date.UTC(2026, 7, 1, 0, 0, retestIndex)).toISOString(),
                  result:
                    retestIndex === 200
                      ? {
                          delta: retestIndex / 10,
                          token: 'Bearer oversized-report-retest-canary',
                        }
                      : { delta: retestIndex / 10 },
                  recorded_by_pub_id: 'usr_report_performance',
                  created_at: new Date(Date.UTC(2026, 7, 2, 0, 0, retestIndex)).toISOString(),
                }))
              : [],
        })),
        token: 'Bearer oversized-report-root-canary',
      }),
    });
  });

  const started = Date.now();
  await page.goto('/platform/reports/');
  await expect(page.getByText('大型有界报告', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'KPI Trace' }).click();
  await expect(
    page.getByRole('region', { name: '报告冻结事实' }).getByRole('table').locator('tbody tr'),
  ).toHaveCount(500);
  await expect(
    page.getByText('冻结事实：服务返回 501 条，浏览器安全视图展示 500 条'),
  ).toBeVisible();
  expect(Date.now() - started).toBeLessThan(10_000);

  await page.getByRole('button', { name: '章节编辑' }).click();
  await expect(
    page.getByText('版本章节：服务返回 101 条，浏览器安全视图展示 100 条'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '保存不可变报告版本' })).toBeDisabled();

  await page.getByRole('button', { name: /审核发布/ }).click();
  await expect(
    page.getByText('审核评论：服务返回 501 条，浏览器安全视图展示 499 条'),
  ).toBeVisible();
  await expect(page.getByText('安全投影不完整')).toBeVisible();
  await expect(page.getByRole('button', { name: '批准发布' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '确认 AI 草稿已人工复核' })).toBeDisabled();

  await page.getByRole('button', { name: '效果复盘' }).click();
  await expect(
    page.getByText('优化行动：服务返回 201 条，浏览器安全视图展示 200 条'),
  ).toBeVisible();
  await expect(
    page.getByText('效果复测：服务返回 201 条，浏览器安全视图展示 199 条'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '开始执行' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '记录复测效果' })).toBeDisabled();

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
  expect(surfaces).not.toMatch(/oversized-report-(?:comment|retest|root)-canary|Bearer |Cookie=/i);
  expect(writes).toEqual([]);
});
