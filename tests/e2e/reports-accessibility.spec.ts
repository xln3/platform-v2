import { expect, test } from '@playwright/test';
import { expectAccessible, prepareApp } from './accessibility';

test('report editor and preview are WCAG AA clean', async ({ page }) => {
  const expectCleanRuntime = await prepareApp(page, '/platform/reports/');
  const skipLink = page.getByRole('link', { name: '跳到主要内容' });
  await skipLink.focus();
  await expect(skipLink).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();
  await expectAccessible(page);
  for (const workspace of [
    'KPI Trace',
    '章节编辑',
    '版本对比',
    '证据编排',
    'PDF 预览',
    '审核发布',
    '效果复盘',
  ]) {
    await page.getByRole('button', { name: workspace, exact: false }).click();
    if (workspace === 'KPI Trace') {
      await page.getByRole('button', { name: '打开贡献证据' }).click();
    }
    if (workspace === '证据编排') {
      await page.getByRole('button', { name: '调整锚点' }).click();
      await page.getByRole('button', { name: '右移' }).click();
    }
    if (workspace === 'PDF 预览') {
      await page.getByText('PDF.js 已渲染第 1 页').waitFor({ state: 'attached' });
      await page.getByRole('button', { name: '100%' }).click();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
        ),
      ).toBe(true);
    }
    await expectAccessible(page);
  }
  expectCleanRuntime();
});
