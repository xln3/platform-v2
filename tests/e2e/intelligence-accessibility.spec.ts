import { expect, test } from '@playwright/test';
import { expectAccessible, prepareApp } from './accessibility';

test('intelligence graph and table alternative are WCAG AA clean', async ({ page }) => {
  const expectCleanRuntime = await prepareApp(page, '/platform/intelligence/');
  const skipLink = page.getByRole('link', { name: '跳到主要内容' });
  await skipLink.focus();
  await expect(skipLink).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();
  await expectAccessible(page);
  for (const workspace of [
    'Claim 矩阵',
    '多源证据',
    '传播关系',
    '页面历史',
    '裁决与申诉',
    '证据包',
  ]) {
    await page.getByRole('button', { name: workspace, exact: false }).click();
    if (workspace === '多源证据') {
      await page.getByRole('button', { name: '检查证据锚点' }).first().click();
      await expectAccessible(page);
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
    }
    if (workspace === '传播关系') await page.locator('.react-flow__node').first().waitFor();
    await expectAccessible(page);
  }
  expectCleanRuntime();
});
