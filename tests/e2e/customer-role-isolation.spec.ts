import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';
import { installSyntheticHttpResponses, syntheticHttpResponseCount } from './synthetic-http';

test('unauthorized browser session cannot infer projects or platform accounts', async ({
  page,
}) => {
  let projectRequests = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_unknown');
    localStorage.setItem('geo.session.actor', 'unknown@example.test');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await installSyntheticHttpResponses(page, [
    {
      id: 'customer-session-unauthorized',
      path: '/api/v2/identity/session',
      status: 401,
      body: { detail: { code: 'membership_invalid' } },
    },
  ]);
  await page.route('**/api/v2/projects**', (route) => {
    projectRequests += 1;
    return route.fulfill({ status: 403, body: '{}' });
  });

  await page.goto('/platform/customer/?section=accounts');
  await expect(page.getByText('无权查看')).toBeVisible();
  await expect(page.getByText('平台账号与授权')).toHaveCount(0);
  await expect(page.getByText('尾号 · 4821')).toHaveCount(0);
  expect(projectRequests).toBe(0);
  expect(await syntheticHttpResponseCount(page, 'customer-session-unauthorized')).toBe(1);
  const surfaces = await page.evaluate(() => ({
    body: document.body.textContent,
    url: location.href,
    sessionStorage: JSON.stringify(sessionStorage),
  }));
  expect(JSON.stringify(surfaces)).not.toContain('prj_01K0CONTRACTFIXTURE');
});

test('validated live session supplies tenant, project and role context', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_live_123456');
    localStorage.setItem('geo.session.actor', 'customer@example.test');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_live_123456',
        user_pub_id: 'usr_live_654321',
        role: 'customer',
        permissions: ['project:read', 'account:authorize'],
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
            pub_id: 'prj_live_abcdef',
            tenant_pub_id: 'tnt_live_123456',
            name: '真实联调项目',
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
  await page.goto('/platform/customer/');
  await expect(page.getByRole('button', { name: /真实联调项目/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: '云岫智能 · AI 认知资产总览' })).toBeVisible();
  await expect(page.getByText('50.0%', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('无权查看')).toHaveCount(0);
});

test('unsafe session hints are purged before any identity request', async ({ page }) => {
  let identityRequests = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_unsafe');
    localStorage.setItem('geo.session.actor', '13800138000');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) => {
    identityRequests += 1;
    return route.fulfill({ status: 500, body: '{}' });
  });

  await page.goto('/platform/customer/?section=accounts');
  await expect(page.getByText('无权查看')).toBeVisible();
  expect(identityRequests).toBe(0);
  const storage = await page.evaluate(() => JSON.stringify(localStorage));
  expect(storage).not.toContain('13800138000');
  expect(storage).not.toContain('geo.session.actor');
});

test('control-character session hints are purged before any identity request', async ({ page }) => {
  let identityRequests = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_safe\u0000actor_collision');
    localStorage.setItem('geo.session.actor', 'customer-control-safe');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) => {
    identityRequests += 1;
    return route.fulfill({ status: 500, body: '{}' });
  });

  await page.goto('/platform/customer/?section=accounts');
  await expect(page.getByText('无权查看')).toBeVisible();
  expect(identityRequests).toBe(0);
  const surfaces = await page.evaluate(() => ({
    body: document.body.textContent,
    url: location.href,
    storage: JSON.stringify(localStorage),
  }));
  expect(JSON.stringify(surfaces)).not.toContain('actor_collision');
  expect(surfaces.storage).not.toContain('geo.session.');
});

test('secret-shaped values in a successful identity projection never reach browser surfaces', async ({
  page,
}) => {
  const canaries = ['Cookie=session-canary', '13800138000', '/profiles/secret-profile'];
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_live_safe');
    localStorage.setItem('geo.session.actor', 'customer-safe');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_live_safe',
        user_pub_id: 'usr_live_safe',
        role: 'customer',
        permissions: ['project:read'],
        cookie: canaries[0],
        profile_path: canaries[2],
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
            pub_id: 'prj_live_safe',
            tenant_pub_id: 'tnt_live_safe',
            name: canaries[1],
            state: 'active',
            created_at: '2026-07-24T00:00:00Z',
            updated_at: '2026-07-24T00:00:00Z',
            proxy_password: 'proxy-canary',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );
  await page.route('**/api/v2/analytics/overview**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '[]',
    }),
  );

  await page.goto('/platform/customer/');
  await expect(page.getByRole('button', { name: /未命名项目/ })).toBeVisible();
  const surfaces = await page.evaluate(() => ({
    dom: document.documentElement.outerHTML,
    localStorage: JSON.stringify(localStorage),
    sessionStorage: JSON.stringify(sessionStorage),
    url: location.href,
  }));
  const serialized = JSON.stringify(surfaces);
  for (const canary of [...canaries, 'proxy-canary']) expect(serialized).not.toContain(canary);
});

test('cross-tenant or duplicate project bootstrap fails closed before business reads', async ({
  page,
}) => {
  let businessReads = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_bootstrap_integrity');
    localStorage.setItem('geo.session.actor', 'customer-bootstrap-integrity');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_bootstrap_integrity',
        user_pub_id: 'usr_bootstrap_integrity',
        role: 'customer',
        permissions: ['project:read', 'Bearer bootstrap-browser-permission-canary'],
        cookie: 'SESSION=bootstrap-browser-session-canary',
      }),
    }),
  );
  const safeProject = {
    pub_id: 'prj_bootstrap_integrity',
    tenant_pub_id: 'tnt_bootstrap_integrity',
    name: '不应显示的 bootstrap 项目',
    state: 'active',
    created_at: '2026-07-24T00:00:00Z',
    updated_at: '2026-07-24T00:00:00Z',
  };
  await page.route('**/api/v2/projects**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            ...safeProject,
            pub_id: 'prj_bootstrap_cross_tenant',
            tenant_pub_id: 'tnt_other',
            token: 'Bearer bootstrap-cross-tenant-canary',
          },
          safeProject,
          {
            ...safeProject,
            profile_path: '/secret/profile/bootstrap-duplicate-canary',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );
  await page.route('**/api/v2/analytics/**', (route) => {
    businessReads += 1;
    return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });

  await page.goto('/platform/customer/');
  await expect(page.getByRole('alert')).toContainText('加载失败');
  await expect(page.getByText('不应显示的 bootstrap 项目')).toHaveCount(0);
  expect(businessReads).toBe(0);
  await expectAccessible(page);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      localStorage,
      sessionStorage,
      href: location.href,
    }),
  );
  for (const canary of [
    'bootstrap-browser-permission-canary',
    'bootstrap-browser-session-canary',
    'bootstrap-cross-tenant-canary',
    'bootstrap-duplicate-canary',
    '/secret/profile',
  ]) {
    expect(surfaces).not.toContain(canary);
  }
});

test('an explicit session failure stays fail-closed and recovers only after user retry', async ({
  page,
}) => {
  let successfulIdentityRequests = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_retry_safe');
    localStorage.setItem('geo.session.actor', 'subject-retry-safe');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await installSyntheticHttpResponses(page, [
    {
      id: 'customer-session-transient',
      path: '/api/v2/identity/session',
      status: 503,
      body: {
        detail: 'OTP 394820 at /var/browser/profile/customer-a',
      },
      remaining: 1,
    },
  ]);
  await page.route('**/api/v2/identity/session', (route) => {
    successfulIdentityRequests += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_retry_safe',
        user_pub_id: 'usr_retry_safe',
        role: 'customer',
        permissions: ['project:read'],
      }),
    });
  });
  await page.route('**/api/v2/projects**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            pub_id: 'prj_retry_safe',
            tenant_pub_id: 'tnt_retry_safe',
            name: '重试恢复项目',
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
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '[]',
    }),
  );

  await page.goto('/platform/customer/');
  await expect(page.getByRole('alert')).toContainText('加载失败');
  await expect(page.getByRole('alert')).toHaveCount(1);
  await expect(page.getByText('品牌增长项目')).toHaveCount(0);
  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByRole('button', { name: /重试恢复项目/ })).toBeVisible();
  expect(await syntheticHttpResponseCount(page, 'customer-session-transient')).toBe(1);
  expect(successfulIdentityRequests).toBe(1);
  const surfaces = await page.evaluate(() => ({
    dom: document.documentElement.outerHTML,
    url: location.href,
    localStorage: JSON.stringify(localStorage),
    sessionStorage: JSON.stringify(sessionStorage),
  }));
  expect(JSON.stringify(surfaces)).not.toContain('394820');
  expect(JSON.stringify(surfaces)).not.toContain('/profile/customer-a');
});
