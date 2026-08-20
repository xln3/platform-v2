import { expect, test } from './runtime-fixture';
import {
  expectAccessible,
  expectSharedInteractionAccessibility,
  prepareApp,
} from './accessibility';

test('operations shell and account lifecycle view are WCAG AA clean', async ({ page }) => {
  await prepareApp(page, '/platform/operations/');
  const skipLink = page.getByRole('link', { name: '跳到主要内容' });
  await skipLink.focus();
  await expect(skipLink).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();
  await expectSharedInteractionAccessibility(page);
  await expectAccessible(page);
  await expect(page.getByRole('link', { name: '执行与账号' })).toHaveAttribute(
    'href',
    '/platform/operations/execution?project=prj_01K0CONTRACTFIXTURE0000000',
  );
  await expect(page.getByRole('link', { name: '媒体比价台' })).toHaveAttribute(
    'href',
    '/platform/operations/media-prices?project=prj_01K0CONTRACTFIXTURE0000000',
  );
  for (const workspace of [/会话健康/, /待人工/, /事件审计/]) {
    await page.getByRole('button', { name: workspace }).click();
    await expectAccessible(page);
  }
});
