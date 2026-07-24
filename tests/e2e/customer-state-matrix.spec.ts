import { expect, test } from '@playwright/test';
import { expectAccessible, prepareApp } from './accessibility';

test('shared data states remain distinct, accessible and responsive', async ({
  page,
}, testInfo) => {
  const expectCleanRuntime = await prepareApp(page, '/platform/customer/experience-states');
  for (const state of [
    'normal',
    'loading',
    'empty',
    'real-zero',
    'insufficient',
    'failed',
    'delayed',
    'forbidden',
  ]) {
    await expect(page.getByText(state, { exact: true })).toBeVisible();
  }
  await expect(page.getByText('结果为 0')).toBeVisible();
  await expect(page.getByText('样本不足')).toBeVisible();
  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('正在加载')).toHaveCount(2);
  await expectAccessible(page);
  expectCleanRuntime();
  const viewportName = testInfo.project.name.replace('customer-', '');
  await page.screenshot({
    path: `tests/e2e-results/state-matrix-${viewportName}.png`,
    fullPage: true,
  });
});
