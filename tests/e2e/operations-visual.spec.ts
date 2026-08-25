import { expect, test } from './runtime-fixture';
import { expectSafePageScreenshot } from './screenshot-safety';
import { installOperationsMediaIdentity, routeReadyMediaPrices } from './media-prices-fixture';
import { prepareVisualPage } from './visual-regression';

const workspaces = [
  { section: 'overview', snapshot: 'operations-shell.png', ready: '项目组合' },
  { section: 'sessions', snapshot: 'operations-session-health.png', ready: '授权、租约与会话健康' },
  { section: 'interventions', snapshot: 'operations-interventions.png', ready: '人工接管队列' },
  { section: 'events', snapshot: 'operations-events.png', ready: '账号生命周期事件' },
] as const;

for (const workspace of workspaces) {
  test(`operations ${workspace.section} visual baseline has no page overflow`, async ({ page }) => {
    await prepareVisualPage(page, `/platform/operations/?section=${workspace.section}`);
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
