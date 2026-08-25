import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from './runtime-fixture';
import { captureSafeScreenshot } from './screenshot-safety';
import { prepareVisualPage } from './visual-regression';

type JsonRecord = Record<string, unknown>;

type SbaqFixture = {
  provenance: {
    source: string;
    captured_at: string;
    project_pub_id: string;
    tenant_pub_id: string;
  };
  project: {
    pub_id: string;
    name: string;
    state: string;
    brandrank_domain: string;
    created_at: string;
    updated_at: string;
  };
  config: {
    pub_id: string;
    revision: number;
    effective_at: string;
    frozen_at: string;
    snapshot_hash: string;
    snapshot: JsonRecord;
  };
  sampling_pages: Record<string, JsonRecord>;
  runs: {
    summary: {
      run_count: number;
      active_run_count: number;
      total_tasks: number;
      completed_tasks: number;
      failed_tasks: number;
    };
    pages: Record<string, JsonRecord[]>;
  };
};

// Frozen from the real 盛邦 production project through read-only SQL on 2026-08-24.
// The JSON keeps the real IDs, 136-question baseline, six-leg matrix and run facts.
const fixture = JSON.parse(
  readFileSync(
    path.resolve(process.cwd(), 'tests/e2e/fixtures/sbaq-readonly-pagination-20260824.json'),
    'utf8',
  ),
) as SbaqFixture;
const projectPubId = fixture.provenance.project_pub_id;
const tenantPubId = fixture.provenance.tenant_pub_id;

function json(body: unknown, headers: Record<string, string> = {}) {
  return {
    status: 200,
    contentType: 'application/json',
    headers,
    body: JSON.stringify(body),
  };
}

function isoTimestamp(value: string): string {
  return new Date(value).toISOString();
}

test('AI ranking uses real 盛邦 data with read-only config and full numbered pagination', async ({
  page,
}, testInfo) => {
  const samplingRequestedPages: string[] = [];
  const runRequestedPages: string[] = [];
  await page.addInitScript(
    ({ tenant }) => {
      localStorage.setItem('geo.session.tenant', tenant);
      localStorage.setItem('geo.session.actor', 'operator-readonly-acceptance');
      localStorage.setItem('geo.session.role', 'operator');
    },
    { tenant: tenantPubId },
  );
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill(
      json({
        tenant_pub_id: tenantPubId,
        user_pub_id: 'usr_acceptance_readonly',
        role: 'operator',
        permissions: ['project:read', 'account:read'],
      }),
    ),
  );
  await page.route(/\/api\/v2\/projects(?:\?.*)?$/u, (route) =>
    route.fulfill(
      json({
        data: [
          {
            ...fixture.project,
            tenant_pub_id: tenantPubId,
            created_at: isoTimestamp(fixture.project.created_at),
            updated_at: isoTimestamp(fixture.project.updated_at),
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    ),
  );
  await page.route(`**/api/v2/projects/${projectPubId}/config/current`, (route) =>
    route.fulfill(
      json({
        effective: {
          ...fixture.config,
          effective_at: isoTimestamp(fixture.config.effective_at),
          frozen_at: isoTimestamp(fixture.config.frozen_at),
          question_groups: fixture.config.snapshot.query_groups,
        },
        next_pending: null,
      }),
    ),
  );
  await page.route('**/api/v2/analytics/sampling-progress**', (route) => {
    const requestedPage = new URL(route.request().url()).searchParams.get('page') ?? '1';
    samplingRequestedPages.push(requestedPage);
    const response = fixture.sampling_pages[requestedPage];
    if (!response) return route.abort('failed');
    return route.fulfill(json(response));
  });
  await page.route('**/api/v2/collection/runs/summary**', (route) =>
    route.fulfill(
      json({
        project_pub_id: projectPubId,
        ...fixture.runs.summary,
      }),
    ),
  );
  await page.route(/\/api\/v2\/collection\/runs(?:\?.*)?$/u, (route) => {
    const url = new URL(route.request().url());
    const requestedPage = url.searchParams.get('page') ?? '1';
    runRequestedPages.push(requestedPage);
    const rows = fixture.runs.pages[requestedPage]?.map((run) => ({
      ...run,
      created_at: isoTimestamp(String(run.created_at)),
      updated_at: isoTimestamp(String(run.updated_at)),
    }));
    if (!rows) return route.abort('failed');
    const totalPages = Math.ceil(fixture.runs.summary.run_count / 4);
    return route.fulfill(
      json(rows, {
        'X-Page': requestedPage,
        'X-Page-Size': '4',
        'X-Total-Count': String(fixture.runs.summary.run_count),
        'X-Page-Count': String(totalPages),
        'X-Has-More': Number(requestedPage) < totalPages ? 'true' : 'false',
      }),
    );
  });
  for (const endpoint of ['overview', 'breakdown', 'competitors']) {
    await page.route(`**/api/v2/analytics/${endpoint}**`, (route) => route.fulfill(json([])));
  }
  await page.route(`**/api/v2/projects/${projectPubId}/brand-visibility**`, (route) =>
    route.fulfill(
      json({
        project_pub_id: projectPubId,
        project_name: fixture.project.name,
        window_days: 30,
        domain: fixture.project.brandrank_domain,
        result: { overall: { merged: [] } },
      }),
    ),
  );

  await prepareVisualPage(page, `/platform/operations/service-visibility?project=${projectPubId}`);
  const projectPicker = page.getByRole('combobox', { name: '项目' });
  await expect(projectPicker).toHaveValue(projectPubId);
  await expect(projectPicker.locator('option:checked')).toHaveText('盛邦安全-GEO验证');
  await expect(page.getByRole('heading', { name: '本次评测配置' })).toBeVisible();

  const config = page.locator('.readonly-config-summary');
  await expect(config.getByText('34 / 136')).toBeVisible();
  await expect(config.getByText('频率', { exact: true })).toHaveCount(0);
  await expect(config.locator('textarea,select,input:not([aria-label="跳转页码"])')).toHaveCount(0);
  await expect(config.getByRole('button', { name: /冻结|启动|建周期/u })).toHaveCount(0);

  const samplingTable = page.getByRole('table', { name: '问题采样进度总览' });
  await expect(samplingTable.locator('tbody tr')).toHaveCount(4);
  await expect(page.getByText('136 问')).toBeVisible();
  await expect(page.getByText('已观测 555/816 格')).toBeVisible();
  await expect(page.getByText('共 1143 条有效回答')).toBeVisible();
  const samplingPager = page.getByRole('navigation', { name: '采样进度问题分页' });
  await expect(samplingPager.getByText('第 1 / 34 页')).toBeVisible();
  await expect(samplingPager.getByText(/共 136 条/u)).toBeVisible();

  await expect(page.locator('.runs-panel tbody tr')).toHaveCount(4);
  await expect(page.getByText('项目共 474 个 run')).toBeVisible();
  const runPager = page.getByRole('navigation', { name: '采样记录分页' });
  await expect(runPager.getByText('第 1 / 119 页')).toBeVisible();
  await expect(runPager.getByText(/共 474 条/u)).toBeVisible();

  await samplingPager.getByRole('spinbutton', { name: '跳转页码' }).fill('34');
  await samplingPager.getByRole('button', { name: '跳转' }).click();
  await expect(
    page.getByText('网证这个事儿哪些安全公司在参与？有没有官方合作的厂商？'),
  ).toBeVisible();
  expect(samplingRequestedPages).toContain('34');
  await page
    .getByRole('navigation', { name: '采样进度问题分页' })
    .getByRole('spinbutton', { name: '跳转页码' })
    .fill('1');
  await page
    .getByRole('navigation', { name: '采样进度问题分页' })
    .getByRole('button', { name: '跳转' })
    .click();
  await expect(
    samplingTable.getByText('高校双非资产排查可以找什么公司做', { exact: true }).first(),
  ).toBeVisible();

  await runPager.getByRole('spinbutton', { name: '跳转页码' }).fill('119');
  await runPager.getByRole('button', { name: '跳转' }).click();
  await expect(page.getByText('run_4E01D5PXTRE089N1XTNKSSHE32')).toBeVisible();
  expect(runRequestedPages).toContain('119');
  await page
    .getByRole('navigation', { name: '采样记录分页' })
    .getByRole('spinbutton', { name: '跳转页码' })
    .fill('1');
  await page
    .getByRole('navigation', { name: '采样记录分页' })
    .getByRole('button', { name: '跳转' })
    .click();
  await expect(page.getByText('run_1CBHC7S6GP4H3HEPD233CRSGEV')).toBeVisible();

  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    .toBe(true);

  await captureSafeScreenshot(page, {
    path: path.resolve(
      process.cwd(),
      `tests/visual-evidence/s02/ai-ranking-readonly-${testInfo.project.name}.png`,
    ),
    fullPage: true,
    animations: 'disabled',
  });
});
