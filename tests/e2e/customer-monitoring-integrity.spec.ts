import { expect, test, type Page } from './runtime-fixture';
import { expectAccessible } from './accessibility';
import { buildCustomerDashboardFixture } from './customer-dashboard-fixture';

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
}

const dashboardRoute = '**/api/v2/customer-dashboard/projects/*';

test('oversized atomic dashboard collections fail closed without exposing rejected rows', async ({
  page,
}) => {
  await installMonitoringExperience(page);
  await page.route(dashboardRoute, (route) => {
    const projectPubId = new URL(route.request().url()).pathname.split('/').at(-1) ?? '';
    const fixture = buildCustomerDashboardFixture(projectPubId);
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...fixture,
        models: Array.from({ length: 101 }, (_, index) => ({
          ...fixture.models[0]!,
          key: `model-${index}`,
          label: index === 100 ? 'Bearer atomic-dashboard-oversize-canary' : `安全模型 ${index}`,
        })),
      }),
    });
  });

  await page.goto('/platform/customer/?section=monitoring');
  await expect(page.getByText('加载失败', { exact: true })).toBeVisible();
  await expect(page.getByText('暂无数据', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: /品牌可见度与模型表现/ })).toHaveCount(0);
  await expectAccessible(page);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/atomic-dashboard-oversize-canary|Bearer /i);
});

test('operational fields fail the atomic customer dashboard snapshot closed', async ({ page }) => {
  await installMonitoringExperience(page);
  await page.route(dashboardRoute, (route) => {
    const projectPubId = new URL(route.request().url()).pathname.split('/').at(-1) ?? '';
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...buildCustomerDashboardFixture(projectPubId),
        workflow_id: 'wf_customer_dashboard_forbidden',
        profile_path: '/secret/profile/customer-dashboard-canary',
        token: 'Bearer customer-dashboard-token-canary',
      }),
    });
  });

  await page.goto('/platform/customer/?section=monitoring');
  await expect(page.getByText('加载失败', { exact: true })).toBeVisible();
  await expect(page.getByText('暂无数据', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: /品牌可见度与模型表现/ })).toHaveCount(0);
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
    /customer-dashboard-(?:canary|token-canary)|Bearer |\/secret\/profile/i,
  );
});

test('a malformed nested dimension fails atomically instead of claiming an empty window', async ({
  page,
}) => {
  await installMonitoringExperience(page);
  await page.route(dashboardRoute, (route) => {
    const projectPubId = new URL(route.request().url()).pathname.split('/').at(-1) ?? '';
    const fixture = buildCustomerDashboardFixture(projectPubId);
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...fixture,
        models: [{ ...fixture.models[0], key: '' }],
      }),
    });
  });

  await page.goto('/platform/customer/?section=monitoring');
  await expect(page.getByText('加载失败', { exact: true })).toBeVisible();
  await expect(page.getByText('暂无数据', { exact: true })).toHaveCount(0);
  await expectAccessible(page);
});

test('filter changes discard an older customer dashboard snapshot response', async ({ page }) => {
  let releaseOldRequest: (() => void) | undefined;
  let oldRequestCount = 0;
  let currentRequestCount = 0;
  const oldGate = new Promise<void>((resolve) => {
    releaseOldRequest = resolve;
  });
  await installMonitoringExperience(page);
  await page.route(dashboardRoute, async (route) => {
    const url = new URL(route.request().url());
    const projectPubId = url.pathname.split('/').at(-1) ?? '';
    const model = url.searchParams.get('model');
    if (model === 'doubao') {
      oldRequestCount += 1;
      await oldGate;
    }
    if (model === 'DeepSeek') currentRequestCount += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        buildCustomerDashboardFixture(projectPubId, {
          mentionRate: model === 'DeepSeek' ? 0.95 : model === 'doubao' ? 0.1 : 0.5,
          model: model ?? 'doubao',
        }),
      ),
    });
  });

  await page.goto('/platform/customer/?section=monitoring');
  await expect(page.getByText('50.0%', { exact: true }).first()).toBeVisible();
  await page.getByLabel('AI 模型').selectOption('doubao');
  await expect.poll(() => oldRequestCount).toBe(1);
  await page.evaluate(() => {
    const url = new URL(location.href);
    url.searchParams.set('model', 'DeepSeek');
    history.pushState(null, '', url);
    dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect.poll(() => currentRequestCount).toBe(1);
  await expect(page.getByText('95.0%', { exact: true }).first()).toBeVisible();
  releaseOldRequest?.();
  await page.waitForTimeout(500);
  await expect(page.getByText('95.0%', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('10.0%', { exact: true })).toHaveCount(0);
  await expectAccessible(page);
});
