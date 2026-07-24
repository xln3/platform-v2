import { expect, test } from '@playwright/test';
import { expectAccessible, prepareApp } from './accessibility';

test('operations shell and account lifecycle view are WCAG AA clean', async ({ page }) => {
  const expectCleanRuntime = await prepareApp(page, '/platform/operations/');
  const skipLink = page.getByRole('link', { name: '跳到主要内容' });
  await skipLink.focus();
  await expect(skipLink).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();
  await expectAccessible(page);
  await expect(page.getByRole('link', { name: '执行任务' })).toHaveAttribute(
    'href',
    '/platform/operations/execution',
  );
  for (const workspace of [/会话健康/, /待人工/, /事件审计/]) {
    await page.getByRole('button', { name: workspace }).click();
    await expectAccessible(page);
  }
  expectCleanRuntime();
});
