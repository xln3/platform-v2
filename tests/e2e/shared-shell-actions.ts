import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';

export function verifySharedShellActions({
  product,
  path,
  role,
  liveNavLabelsWithoutBadges = [],
  internalLink,
}: {
  product: string;
  path: string;
  role: 'customer' | 'operator' | 'analyst' | 'reviewer';
  liveNavLabelsWithoutBadges?: readonly string[];
  internalLink?: { label: string; href: string; projectAware?: boolean };
}) {
  test(`${product} shared shell actions are functional and secret-free`, async ({ page }) => {
    await page.addInitScript(
      ({ productRole }) => {
        localStorage.setItem('geo.session.tenant', 'tnt_shared_shell');
        localStorage.setItem('geo.session.actor', `${productRole}-shared-shell`);
        localStorage.setItem('geo.session.role', productRole);
      },
      { productRole: role },
    );
    await page.route('**/api/v2/identity/session', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tenant_pub_id: 'tnt_shared_shell',
          user_pub_id: `usr_${role}_shared_shell`,
          role,
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
              pub_id: 'prj_shared_shell',
              tenant_pub_id: 'tnt_shared_shell',
              name:
                '１３８００１３８０００ · 824-911 · Bearer%2520encoded-session-canary · ' +
                String.raw`profile_dir=C:\Users\runner\Chromium\User Data\Profile 1`,
              state: 'active',
              created_at: '2026-07-24T00:00:00Z',
              updated_at: '2026-07-24T00:00:00Z',
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      }),
    );
    await page.route('**/api/v2/health', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v1' }),
      }),
    );
    await page.route('**/api/v2/analytics/overview**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
    );
    for (const endpoint of ['**/api/v2/reports?**', '**/api/v2/intelligence/investigations?**']) {
      await page.route(endpoint, (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
        }),
      );
    }
    await page.goto(path);
    if (internalLink) {
      const link = page.getByRole('link', { name: internalLink.label, exact: true });
      const expected = new URL(internalLink.href, page.url());
      if (internalLink.projectAware !== false) {
        expected.searchParams.set('project', 'prj_shared_shell');
      }
      await expect(link).toHaveAttribute('href', `${expected.pathname}${expected.search}`);
      const projected = new URL(await link.getAttribute('href')!, page.url());
      expect([...projected.searchParams]).toEqual(
        internalLink.projectAware === false ? [] : [['project', 'prj_shared_shell']],
      );
      expect(projected.hash).toBe('');
    }
    for (const label of liveNavLabelsWithoutBadges) {
      const navigationEntry = page
        .getByRole('link', { name: new RegExp(label) })
        .or(page.getByRole('button', { name: new RegExp(label) }));
      await expect(navigationEntry.locator('em')).toHaveCount(0);
    }

    await page.locator('.project-switcher').click();
    const contextDialog = page.getByRole('dialog', { name: '当前项目上下文' });
    await expect(contextDialog).toContainText('已验证 live session');
    await expect(contextDialog).toContainText('未命名项目');
    await expect(contextDialog.locator('input')).toHaveCount(0);
    await expectAccessible(page);
    await page.getByRole('button', { name: '关闭项目上下文' }).click();

    await page.getByRole('button', { name: '通知' }).click();
    const notificationDialog = page.getByRole('dialog', { name: '通知中心' });
    await expect(notificationDialog).toContainText(
      '当前安全投影未提供通知集合；不会推断数据窗口、账号、待人工任务或其数量',
    );
    await expect(notificationDialog.getByText('数据窗口已冻结')).toHaveCount(0);
    await expect(notificationDialog.getByText('有一项待人工确认')).toHaveCount(0);
    await expectAccessible(page);
    await page.getByRole('button', { name: '关闭通知中心' }).click();

    await expect(page.getByRole('button', { name: '导出视图' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '创建任务' })).toHaveCount(0);

    const browserSurfaces = await page.evaluate(() =>
      JSON.stringify({
        dom: document.documentElement.outerHTML,
        url: location.href,
        localStorage: { ...localStorage },
        sessionStorage: { ...sessionStorage },
      }),
    );
    expect(browserSurfaces).not.toMatch(
      /１３８|824-911|encoded-session-canary|profile_dir|User Data|Profile 1/i,
    );
  });
}
