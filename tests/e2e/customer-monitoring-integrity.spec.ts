import { expect, test, type Page } from './runtime-fixture';
import { expectAccessible } from './accessibility';

async function installMonitoringExperience(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_monitoring_integrity');
    localStorage.setItem('geo.session.actor', 'customer-monitoring-integrity');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_customer_monitoring_integrity',
        user_pub_id: 'usr_customer_monitoring_integrity',
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
            pub_id: 'prj_customer_monitoring_integrity',
            tenant_pub_id: 'tnt_customer_monitoring_integrity',
            name: '客户监测完整性项目',
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
}

const overviewRow = (metric: string, value: number) => ({
  metric,
  value,
  numerator: 2,
  denominator: 4,
  state: 'ready',
  metric_version: 'metric-v1',
  scorer_version: 'scorer-v1',
  filter_hash: 'safe',
  trace_tokens: [],
});

const breakdownRow = (
  groupBy: string,
  dimensions: Record<string, string | null>,
  extension: Record<string, unknown> = {},
) => ({
  group_by: groupBy,
  day: null,
  model: null,
  region: null,
  mode: null,
  question_pub_id: null,
  question_text: null,
  ...dimensions,
  answer_count: 4,
  mentioned_count: 2,
  mention_rate: 0.5,
  average_rank: 2,
  citation_coverage: 0.5,
  ...extension,
});

test('oversized monitoring facts stay bounded and disclose malformed rows', async ({ page }) => {
  await installMonitoringExperience(page);
  await page.route('**/api/v2/analytics/overview**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          ...overviewRow('mention_rate', 0.5),
          profile_path: '/secret/profile/overview-visible-canary',
        },
        overviewRow('average_rank', 2),
        overviewRow('top3_rate', 1.5),
        overviewRow('citation_coverage', 0.25),
        { ...overviewRow('mention_rate', 0.75), token: 'Bearer overview-limit-canary' },
      ]),
    }),
  );
  await page.route('**/api/v2/analytics/delta**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        mention_rate: {
          current: 0.5,
          previous: 0.4,
          delta: 0.1,
          otp: '824911',
          proxy_password: 'monitoring-delta-proxy-password',
        },
        average_rank: {
          current: 2,
          previous: 'Cookie=monitoring-delta-previous-canary',
          delta: 0.25,
        },
        cookie: 'SESSION=monitoring-delta-root-canary',
      }),
    }),
  );
  await page.route('**/api/v2/analytics/competitors**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        Array.from({ length: 51 }, (_, index) => ({
          competitor: index === 49 ? 'Bearer competitor-canary' : `安全竞品 ${index}`,
          mention_rate: 0.5,
          mention_count: 2,
          answer_count: 4,
          ...(index === 0
            ? {
                proxy_password: 'monitoring-competitor-proxy-password',
                profile_path: '/secret/profile/competitor-visible-canary',
              }
            : {}),
        })),
      ),
    }),
  );
  await page.route('**/api/v2/analytics/breakdown**', (route) => {
    const groupBy = new URL(route.request().url()).searchParams.get('group_by') ?? '';
    const length =
      groupBy === 'day' ? 91 : groupBy === 'model' ? 21 : groupBy === 'region_mode' ? 51 : 101;
    const invalidIndex = length - 2;
    const data = Array.from({ length }, (_, index) => {
      if (groupBy === 'day') {
        const uniqueDay = new Date(Date.UTC(2026, 0, 1 + index)).toISOString().slice(0, 10);
        return breakdownRow(groupBy, {
          day: index === invalidIndex ? '2026-02-30' : uniqueDay,
        });
      }
      if (groupBy === 'model') {
        return breakdownRow(groupBy, {
          model: index === invalidIndex ? 'Cookie=model-canary' : `model-${index}`,
        });
      }
      if (groupBy === 'region_mode') {
        return breakdownRow(groupBy, {
          region: index === invalidIndex ? 'Bearer region-canary' : `region-${index}`,
          mode: `mode-${index}`,
        });
      }
      return breakdownRow(
        groupBy,
        {
          question_pub_id:
            index === invalidIndex ? 'Cookie=question-canary' : `qry_monitoring_${index}`,
          question_text: `安全问题 ${index}`,
        },
        index === invalidIndex ? { profile_path: '/secret/profile/monitoring-canary' } : {},
      );
    });
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(data),
    });
  });

  const started = Date.now();
  await page.goto('/platform/customer/?section=monitoring');
  await expect(page.getByText('KPI 概览：服务返回 5 条，浏览器安全视图展示 3 条')).toBeVisible();
  await expect(page.getByText('确认竞品：服务返回 51 条，浏览器安全视图展示 49 条')).toBeVisible();
  await expect(page.getByText('逐日趋势：服务返回 91 条，浏览器安全视图展示 89 条')).toBeVisible();
  await expect(page.getByText('模型表现：服务返回 21 条，浏览器安全视图展示 19 条')).toBeVisible();
  await expect(
    page.getByText('地域与模式：服务返回 51 条，浏览器安全视图展示 49 条'),
  ).toBeVisible();
  await expect(
    page.getByText('问题级表现：服务返回 101 条，浏览器安全视图展示 99 条'),
  ).toBeVisible();
  const incompleteProjection = page.getByRole('alert').filter({ hasText: '安全投影不完整' });
  await expect(incompleteProjection).toContainText('窗口差值');
  expect(Date.now() - started).toBeLessThan(10_000);
  await expectAccessible(page);
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    )
    .toBe(true);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /overview-(?:limit|visible)-canary|competitor-(?:visible-)?canary|model-canary|region-canary|question-canary|monitoring-(?:canary|delta-root-canary|delta-proxy-password|competitor-proxy-password)|824911|SESSION=|Bearer |Cookie=|\/secret\/profile/i,
  );
});

test('a wholly invalid delta fails only its local panel instead of claiming an empty window', async ({
  page,
}) => {
  await installMonitoringExperience(page);
  await page.route('**/api/v2/analytics/overview**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([overviewRow('mention_rate', 0.5)]),
    }),
  );
  await page.route('**/api/v2/analytics/delta**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        mention_rate: {
          current: 'Cookie=delta-invalid-current-canary',
          previous: 0.4,
          delta: 0.1,
          token: 'Bearer delta-invalid-token-canary',
        },
        profile_path: '/secret/profile/delta-invalid-canary',
      }),
    }),
  );
  await page.route('**/api/v2/analytics/competitors**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          competitor: '安全竞品',
          mention_rate: 0.5,
          mention_count: 2,
          answer_count: 4,
        },
      ]),
    }),
  );
  await page.route('**/api/v2/analytics/breakdown**', (route) => {
    const groupBy = new URL(route.request().url()).searchParams.get('group_by') ?? '';
    const dimensions =
      groupBy === 'day'
        ? { day: '2026-07-25' }
        : groupBy === 'model'
          ? { model: '安全模型' }
          : groupBy === 'region_mode'
            ? { region: '安全地域', mode: '安全模式' }
            : { question_pub_id: 'qry_safe', question_text: '安全问题' };
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([breakdownRow(groupBy, dimensions)]),
    });
  });

  await page.goto('/platform/customer/?section=monitoring');
  const deltaPanel = page
    .locator('section.panel')
    .filter({ has: page.getByRole('heading', { name: '窗口对比' }) });
  const competitorPanel = page
    .locator('section.panel')
    .filter({ has: page.getByRole('heading', { name: '竞品表现' }) });
  await expect(deltaPanel.getByText('加载失败', { exact: true })).toBeVisible();
  await expect(deltaPanel.getByText('暂无数据', { exact: true })).toHaveCount(0);
  await expect(competitorPanel.getByText('安全竞品')).toBeVisible();
  await expect(page.getByRole('alert').filter({ hasText: '安全投影不完整' })).toContainText(
    '窗口差值',
  );
  await expectAccessible(page);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /delta-invalid-(?:current|token)-canary|delta-invalid-canary|Cookie=|Bearer |\/secret\/profile/i,
  );
});

test('a zero-row metric export receipt fails locally without a false artifact claim', async ({
  page,
}) => {
  const exportBodies: unknown[] = [];
  await installMonitoringExperience(page);
  await page.route('**/api/v2/analytics/overview**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([overviewRow('mention_rate', 0.5)]),
    }),
  );
  await page.route('**/api/v2/analytics/delta**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        mention_rate: { current: 0.5, previous: 0.4, delta: 0.1 },
      }),
    }),
  );
  await page.route('**/api/v2/analytics/competitors**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  );
  await page.route('**/api/v2/analytics/breakdown**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  );
  await page.route('**/api/v2/exports/metrics', async (route) => {
    exportBodies.push(route.request().postDataJSON());
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        export_pub_id: 'exp_zero_rows',
        evidence_pub_id: 'evd_zero_rows',
        format: 'xlsx',
        row_count: 0,
        filter_hash: 'd'.repeat(64),
        fact_snapshot_hash: 'e'.repeat(64),
        metric_version: 'metric-v1',
        scorer_version: 'scorer-v1',
        token: 'Bearer zero-row-export-canary',
        profile_path: '/secret/profile/zero-row-export-canary',
      }),
    });
  });

  await page.goto('/platform/customer/?section=monitoring');
  const exportButton = page.getByRole('button', { name: '导出当前筛选 XLSX' });
  await expect(exportButton).toBeVisible();
  await exportButton.click();

  await expect(page.getByText('导出服务暂不可用；未生成本地伪造文件。')).toBeVisible();
  await expect(page.getByText('真实 XLSX 导出已冻结并进入证据存储')).toHaveCount(0);
  expect(exportBodies).toHaveLength(1);
  expect(exportBodies[0]).toMatchObject({
    project_pub_id: 'prj_customer_monitoring_integrity',
    dimensions: {},
  });
  await expectAccessible(page);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /zero-row-export-canary|\/secret\/profile|Bearer |exp_zero_rows|evd_zero_rows/i,
  );
});

test('filter changes discard slower monitoring auxiliary and breakdown responses', async ({
  page,
}) => {
  let oldRequests = 0;
  let deltaRequests = 0;
  let releaseOldRequests: (() => void) | undefined;
  let releaseCurrentOverview: (() => void) | undefined;
  let currentOverviewRequests = 0;
  const oldGate = new Promise<void>((resolve) => {
    releaseOldRequests = resolve;
  });
  const currentOverviewGate = new Promise<void>((resolve) => {
    releaseCurrentOverview = resolve;
  });
  await installMonitoringExperience(page);
  await page.route('**/api/v2/analytics/overview**', async (route) => {
    const current = new URL(route.request().url()).searchParams.get('model') === 'doubao';
    if (current) {
      currentOverviewRequests += 1;
      await currentOverviewGate;
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([overviewRow('mention_rate', current ? 0.95 : 0.5)]),
    });
  });
  const waitWhenOld = async (pageUrl: string) => {
    const current = new URL(pageUrl).searchParams.get('model') === 'doubao';
    if (!current) {
      oldRequests += 1;
      await oldGate;
    }
    return current;
  };
  await page.route('**/api/v2/analytics/delta**', async (route) => {
    deltaRequests += 1;
    const current = deltaRequests > 1;
    if (!current) {
      oldRequests += 1;
      await oldGate;
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        mention_rate: {
          current: current ? 0.9 : 0.1,
          previous: 0,
          delta: current ? 0.9 : 0.1,
        },
      }),
    });
  });
  await page.route('**/api/v2/analytics/competitors**', async (route) => {
    const current = await waitWhenOld(route.request().url());
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          competitor: current ? '当前竞品' : '过期竞品',
          mention_rate: current ? 0.9 : 0.1,
          mention_count: 1,
          answer_count: 1,
          token: current ? undefined : 'Bearer stale-monitoring-canary',
        },
      ]),
    });
  });
  await page.route('**/api/v2/analytics/breakdown**', async (route) => {
    const url = new URL(route.request().url());
    const groupBy = url.searchParams.get('group_by') ?? '';
    const current = await waitWhenOld(route.request().url());
    const label = current ? '当前' : '过期';
    const dimensions =
      groupBy === 'day'
        ? { day: current ? '2026-07-25' : '2026-07-01' }
        : groupBy === 'model'
          ? { model: `${label}模型` }
          : groupBy === 'region_mode'
            ? { region: `${label}地域`, mode: `${label}模式` }
            : {
                question_pub_id: current ? 'qry_current' : 'qry_stale',
                question_text: `${label}问题`,
              };
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([breakdownRow(groupBy, dimensions)]),
    });
  });

  await page.goto('/platform/customer/?section=monitoring');
  await expect(page.getByText('50.0%')).toBeVisible();
  await expect.poll(() => oldRequests).toBe(6);
  await page.getByLabel('模型').selectOption('doubao');
  await expect(page.getByText('正在加载', { exact: true })).toBeVisible();
  await expect(page.getByText('50.0%', { exact: true })).toHaveCount(0);
  expect(currentOverviewRequests).toBe(1);
  releaseCurrentOverview?.();
  await expect(page.getByText('95.0%', { exact: true })).toBeVisible();
  await expect(page.getByText('当前竞品')).toBeVisible();
  await expect(page.getByText('当前问题')).toBeVisible();
  await expect(page.getByText('90.0%').first()).toBeVisible();
  releaseOldRequests?.();
  await page.waitForTimeout(800);
  await expect(page.getByText('当前竞品')).toBeVisible();
  await expect(page.getByText('当前问题')).toBeVisible();
  await expect(page.getByText('过期竞品')).toHaveCount(0);
  await expect(page.getByText('过期问题')).toHaveCount(0);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/stale-monitoring-canary|Bearer /i);
  await expectAccessible(page);
});
