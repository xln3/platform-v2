import { expect, test } from '@playwright/test';
import { prepareVisualPage } from './visual-regression';

const workspaces = [
  { section: 'overview', snapshot: 'operations-shell.png', ready: '运行时间线' },
  { section: 'sessions', snapshot: 'operations-session-health.png', ready: '授权、租约与会话健康' },
  { section: 'interventions', snapshot: 'operations-interventions.png', ready: '人工接管队列' },
  { section: 'events', snapshot: 'operations-events.png', ready: '账号生命周期事件' },
] as const;

for (const workspace of workspaces) {
  test(`operations ${workspace.section} visual baseline has no page overflow`, async ({ page }) => {
    const expectCleanRuntime = await prepareVisualPage(
      page,
      `/platform/operations/?section=${workspace.section}`,
    );
    await page.getByRole('heading', { name: workspace.ready, exact: true }).waitFor();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
      .toBe(true);
    await expect(page).toHaveScreenshot(workspace.snapshot, {
      fullPage: true,
      animations: 'disabled',
      maxDiffPixelRatio: 0.005,
    });
    expectCleanRuntime();
  });
}
