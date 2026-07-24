import { expect, test } from '@playwright/test';
import { expectAccessible } from './accessibility';

export function verifySharedShellActions({
  product,
  path,
  role,
  targetLabel,
  targetSection,
}: {
  product: string;
  path: string;
  role: 'customer' | 'operator' | 'analyst' | 'reviewer';
  targetLabel: string;
  targetSection: string;
}) {
  test(`${product} shared shell actions are functional and secret-free`, async ({ page }) => {
    const consoleErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.on('requestfailed', (request) => failedRequests.push(request.url()));
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
              name: 'Cookie=session-canary · OTP: 824911 · 13800138000',
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

    await page.locator('.project-switcher').click();
    const contextDialog = page.getByRole('dialog', { name: '当前项目上下文' });
    await expect(contextDialog).toContainText('已验证 live session');
    await expect(contextDialog).toContainText('未命名项目');
    await expect(contextDialog.locator('input')).toHaveCount(0);
    await expectAccessible(page);
    await page.getByRole('button', { name: '关闭项目上下文' }).click();

    await page.getByRole('button', { name: '通知' }).click();
    await expect(page.getByRole('dialog', { name: '通知中心' })).toContainText(
      '只显示安全摘要，不披露账号是否存在',
    );
    await expectAccessible(page);
    await page.getByRole('button', { name: '关闭通知中心' }).click();

    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出视图' }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/-view\.json$/);
    const stream = await download.createReadStream();
    let exported = '';
    for await (const chunk of stream) exported += chunk.toString();
    const payload = JSON.parse(exported) as Record<string, unknown>;
    expect(payload).toMatchObject({ product, section: expect.any(String) });
    expect(exported).not.toMatch(
      /cookie|bearer|access_token|refresh_token|otp|proxy_password|profile_path|biometric|13800138000|824911/i,
    );

    await page.getByRole('button', { name: '创建任务' }).click();
    await expect(page.getByRole('dialog', { name: '创建任务或申请' })).toContainText(
      '共享壳不会绕过审批或伪造统一任务',
    );
    await expectAccessible(page);
    await page.getByRole('button', { name: `前往${targetLabel}` }).click();
    await expect(page).toHaveURL(new RegExp(`section=${targetSection}`));

    const browserSurfaces = await page.evaluate(() =>
      JSON.stringify({
        dom: document.documentElement.outerHTML,
        url: location.href,
        localStorage: { ...localStorage },
        sessionStorage: { ...sessionStorage },
      }),
    );
    expect(browserSurfaces).not.toMatch(/session-canary|13800138000|824911|Cookie=session-canary/i);
    expect(consoleErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
  });
}
