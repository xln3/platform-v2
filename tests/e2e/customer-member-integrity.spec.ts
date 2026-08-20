import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';

test('tenant member writes stay serialized and bound to the initiating member', async ({
  page,
}) => {
  let releaseBind: (() => void) | undefined;
  const bindGate = new Promise<void>((resolve) => {
    releaseBind = resolve;
  });
  let bindingAccepted = false;
  let writeResponseSent = false;
  let memberReads = 0;
  let oidcReads = 0;
  const writes: Array<{
    method: string;
    url: string;
    body: unknown;
    headers: Record<string, string>;
  }> = [];

  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_member_integrity');
    localStorage.setItem('geo.session.actor', 'tenant-admin-integrity');
    localStorage.setItem('geo.session.role', 'admin');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_member_integrity',
        user_pub_id: 'usr_member_integrity_admin',
        role: 'admin',
        permissions: ['*'],
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
            pub_id: 'prj_member_integrity',
            tenant_pub_id: 'tnt_member_integrity',
            name: '成员完整性项目',
            state: 'active',
            created_at: '2026-07-27T00:00:00Z',
            updated_at: '2026-07-27T00:00:00Z',
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
      body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v2' }),
    }),
  );
  await page.route('**/api/v2/analytics/overview**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '[]',
    }),
  );
  await page.route('**/api/v2/identity/oidc-bindings', (route) => {
    oidcReads += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        bindingAccepted
          ? [
              {
                user_pub_id: 'usr_member_integrity_alpha',
                active: true,
                created_at: '2026-07-27T00:05:00Z',
                revoked_at: null,
              },
            ]
          : [],
      ),
    });
  });
  await page.route('**/api/v2/identity/members**', async (route) => {
    const request = route.request();
    if (request.method() === 'GET') {
      memberReads += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            pub_id: 'mbr_member_integrity_admin',
            user_pub_id: 'usr_member_integrity_admin',
            subject: 'admin@example.test',
            display_name: '租户管理员',
            role: 'admin',
            state: 'active',
            service_account: false,
          },
          {
            pub_id: 'mbr_member_integrity_alpha',
            user_pub_id: 'usr_member_integrity_alpha',
            subject: 'alpha@example.test',
            display_name: '成员甲',
            role: 'customer',
            state: 'active',
            service_account: false,
          },
          {
            pub_id: 'mbr_member_integrity_beta',
            user_pub_id: 'usr_member_integrity_beta',
            subject: 'beta@example.test',
            display_name: '成员乙',
            role: 'customer',
            state: 'active',
            service_account: false,
          },
        ]),
      });
    }
    writes.push({
      method: request.method(),
      url: request.url(),
      body: request.postData() ? request.postDataJSON() : null,
      headers: request.headers(),
    });
    await bindGate;
    bindingAccepted = true;
    writeResponseSent = true;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        user_pub_id: 'usr_member_integrity_alpha',
        active: true,
        created_at: '2026-07-27T00:05:00Z',
        revoked_at: null,
        token: 'Bearer delayed-member-binding-canary',
        profile_path: '/secret/profile/delayed-member-binding-canary',
      }),
    });
  });

  await page.goto('/platform/customer/?section=members');
  await expect(page.getByText('成员甲', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '管理 成员甲' }).click();
  await page.getByLabel('IdP opaque subject').fill('opaque-idp-member-alpha');
  const bindButton = page.getByRole('button', { name: '建立 OIDC 绑定' });
  await bindButton.evaluate((element) => {
    element.addEventListener('click', () => (element as HTMLButtonElement).click(), { once: true });
  });
  await bindButton.click();
  await expect.poll(() => writes.length).toBe(1);

  await expect(page.getByRole('button', { name: '移出项目' })).toBeDisabled();
  await expect(page.getByText('成员治理写入处理中；完成前其他成员写操作已锁定。')).toBeAttached();
  await page.getByRole('button', { name: '关闭成员管理' }).click();
  await expect(page.getByRole('button', { name: '管理 成员乙' })).toBeDisabled();
  await expect(page.getByLabel('姓名')).toBeDisabled();
  await expect(page.getByRole('button', { name: '发送邀请' })).toBeDisabled();

  await page.getByRole('button', { name: '经营总览', exact: true }).click();
  releaseBind?.();
  await expect.poll(() => writeResponseSent).toBe(true);
  await expect(page.getByText('成员甲 的 OIDC 标识已哈希绑定；原始 subject 未保留')).toHaveCount(0);
  expect(memberReads).toBe(1);
  expect(oidcReads).toBe(1);

  await page.getByRole('button', { name: '项目成员', exact: true }).click();
  await expect(page.getByText('成员甲', { exact: true })).toBeVisible();
  expect(memberReads).toBe(2);
  expect(oidcReads).toBe(2);
  await expect(page.getByRole('button', { name: '管理 成员乙' })).toBeEnabled();

  await page.getByRole('button', { name: '管理 成员乙' }).click();
  await expect(
    page.getByText('输入只用于一次哈希绑定；请勿粘贴 token、Cookie 或验证码。'),
  ).toBeVisible();
  await expect(page.getByText('已绑定；数据库和审计仅保存哈希，不返回原始 subject。')).toHaveCount(
    0,
  );
  await page.getByRole('button', { name: '关闭成员管理' }).click();

  await page.getByRole('button', { name: '管理 成员甲' }).click();
  await expect(
    page.getByText('已绑定；数据库和审计仅保存哈希，不返回原始 subject。'),
  ).toBeVisible();
  await page.getByRole('button', { name: '关闭成员管理' }).click();

  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({
    method: 'PUT',
    body: { subject: 'opaque-idp-member-alpha' },
  });
  expect(writes[0]?.url).toMatch(
    /\/api\/v2\/identity\/members\/usr_member_integrity_alpha\/oidc-binding$/,
  );
  expect(writes[0]?.headers).toMatchObject({
    'x-tenant-id': 'tnt_member_integrity',
    'x-actor-id': 'tenant-admin-integrity',
    'x-actor-role': 'admin',
  });
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
      historyState: history.state,
    }),
  );
  expect(surfaces).not.toMatch(
    /opaque-idp-member-alpha|delayed-member-binding-canary|\/secret\/profile|Bearer /i,
  );
  await expectAccessible(page);
});
