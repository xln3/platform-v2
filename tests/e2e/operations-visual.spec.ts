import { expect, test } from './runtime-fixture';
import { expectSafePageScreenshot } from './screenshot-safety';
import { installOperationsMediaIdentity, routeReadyMediaPrices } from './media-prices-fixture';
import { prepareVisualPage } from './visual-regression';

async function installOperationsExecutionVisualFixture(
  page: Parameters<typeof prepareVisualPage>[0],
) {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_operations_visual');
    localStorage.setItem('geo.session.actor', 'operator-operations-visual');
    localStorage.setItem('geo.session.role', 'operator');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_operations_visual',
        user_pub_id: 'usr_operations_visual',
        role: 'operator',
        permissions: ['project:read', 'account:read'],
      }),
    }),
  );
  const emptyCollection = (route: Parameters<Parameters<typeof page.route>[1]>[0]) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  await page.route('**/api/v2/platform-accounts**', emptyCollection);
  await page.route('**/api/v2/collection/runs**', emptyCollection);
  await page.route('**/api/v2/schedules**', emptyCollection);
  await page.route('**/api/v2/interventions**', emptyCollection);
  await page.route('**/api/v2/break-glass**', emptyCollection);
  await page.route('**/api/v2/platform-events**', emptyCollection);
  await page.route('**/api/v2/operations/platform-sla', emptyCollection);
  await page.route('**/api/v2/projects**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
    }),
  );
  await page.route('**/api/v2/collection/runs/summary**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        project_pub_id: null,
        run_count: 0,
        active_run_count: 0,
        total_tasks: 0,
        completed_tasks: 0,
        failed_tasks: 0,
      }),
    }),
  );
}

const workspaces = [
  {
    section: 'overview',
    path: '/platform/operations/',
    snapshot: 'operations-shell.png',
    ready: '项目组合',
  },
  {
    section: 'sessions',
    path: '/platform/operations/execution#platform-accounts',
    snapshot: 'operations-session-health.png',
    ready: '平台账号目录与 Profile 健康',
  },
  {
    section: 'interventions',
    path: '/platform/operations/execution#interventions',
    snapshot: 'operations-interventions.png',
    ready: '人工接管队列',
  },
  {
    section: 'events',
    path: '/platform/operations/execution#events',
    snapshot: 'operations-events.png',
    ready: '工作流与会话时间线',
  },
] as const;

for (const workspace of workspaces) {
  test(`operations ${workspace.section} visual baseline has no page overflow`, async ({ page }) => {
    if (workspace.section !== 'overview') await installOperationsExecutionVisualFixture(page);
    await prepareVisualPage(page, workspace.path);
    await page.getByRole('heading', { name: workspace.ready, exact: true }).waitFor();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
      .toBe(true);
    await expectSafePageScreenshot(page, workspace.snapshot, {
      fullPage: true,
      animations: 'disabled',
      maxDiffPixelRatio: 0.005,
    });
  });
}

test('operations media prices visual baseline has no page overflow', async ({ page }) => {
  await installOperationsMediaIdentity(page);
  await routeReadyMediaPrices(page);
  await prepareVisualPage(page, '/platform/operations/media-prices');
  await page.getByRole('heading', { name: '离线数据集快照', exact: true }).waitFor();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    .toBe(true);
  await expectSafePageScreenshot(page, 'operations-media-prices.png', {
    fullPage: true,
    animations: 'disabled',
    maxDiffPixelRatio: 0.005,
  });
});
