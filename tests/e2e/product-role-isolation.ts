import { expect, test } from '@playwright/test';

type BrowserRole = 'customer' | 'operator' | 'analyst' | 'reviewer' | 'admin' | 'worker';

export function verifyWrongProductRole({
  product,
  path,
  wrongRole,
  protectedText,
}: {
  product: string;
  path: string;
  wrongRole: BrowserRole;
  protectedText: string;
}) {
  test(`${product} rejects a valid identity with the wrong product role before business data loads`, async ({
    page,
  }) => {
    let accountRequests = 0;
    const consoleErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.on('requestfailed', (request) => failedRequests.push(request.url()));
    page.on('request', (request) => {
      if (/platform-accounts|interventions|leases/.test(request.url())) accountRequests += 1;
    });
    await page.addInitScript(
      ({ role }) => {
        localStorage.setItem('geo.session.tenant', 'tnt_cross_role_safe');
        localStorage.setItem('geo.session.actor', 'subject-cross-role');
        localStorage.setItem('geo.session.role', role);
      },
      { role: wrongRole },
    );
    await page.route('**/api/v2/identity/session', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tenant_pub_id: 'tnt_cross_role_safe',
          user_pub_id: 'usr_cross_role_safe',
          role: wrongRole,
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
              pub_id: 'prj_cross_role_hidden',
              tenant_pub_id: 'tnt_cross_role_safe',
              name: '越权项目不可见',
              state: 'active',
              created_at: '2026-07-24T00:00:00Z',
              updated_at: '2026-07-24T00:00:00Z',
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      }),
    );

    await page.goto(path);
    await expect(page.getByText('无权查看')).toBeVisible();
    await expect(page.getByText(protectedText, { exact: false })).toHaveCount(0);
    await expect(page.getByText('越权项目不可见')).toHaveCount(0);
    await expect(page.getByText('尾号 · 4821')).toHaveCount(0);
    await expect(page.getByText('fixture-***42')).toHaveCount(0);
    expect(accountRequests).toBe(0);
    expect(consoleErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
  });
}
