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
  await expect(page.getByRole('link', { name: /会话健康/ })).toHaveAttribute(
    'href',
    '/platform/operations/execution?project=prj_01K0CONTRACTFIXTURE0000000#platform-accounts',
  );
  await expect(page.getByRole('link', { name: /人工接管/ })).toHaveAttribute(
    'href',
    '/platform/operations/execution?project=prj_01K0CONTRACTFIXTURE0000000#interventions',
  );
  await expect(page.getByRole('link', { name: /事件审计/ })).toHaveAttribute(
    'href',
    '/platform/operations/execution?project=prj_01K0CONTRACTFIXTURE0000000#events',
  );
});
