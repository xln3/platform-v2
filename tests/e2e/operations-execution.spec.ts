import { expect, test } from './runtime-fixture';
import path from 'node:path';
import { captureSafeScreenshot } from './screenshot-safety';

test('operations execution uses real lifecycle APIs without secret leakage', async ({
  page,
  request,
}, testInfo) => {
  const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
  const subject = `operations-e2e-${suffix}`;
  const bootstrap = await request.post('/api/v2/identity/bootstrap', {
    headers: { 'X-Bootstrap-Secret': 'development-bootstrap' },
    data: {
      tenant_name: `Operations E2E ${suffix}`,
      subject,
      display_name: 'Operations E2E Admin',
    },
  });
  expect(bootstrap.ok()).toBeTruthy();
  const identity = (await bootstrap.json()) as {
    tenant_pub_id: string;
    user_pub_id: string;
  };
  const apiHeaders = {
    'X-Tenant-Id': identity.tenant_pub_id,
    'X-Actor-Id': subject,
    'X-Actor-Role': 'admin',
  };
  const accountMask = `e2e-***${suffix.slice(-4)}`;
  const accountResponse = await request.post('/api/v2/platform-accounts', {
    headers: apiHeaders,
    data: {
      platform_slug: 'fixed',
      platform_name: 'Auditable Fixed Adapter',
      account_mask: accountMask,
      owner_pub_id: identity.user_pub_id,
      purpose: 'e2e-health',
      responsible_pub_id: identity.user_pub_id,
      custody_mode: 'server',
      region: 'CN-BJ',
    },
  });
  expect(accountResponse.ok()).toBeTruthy();
  const account = (await accountResponse.json()) as { pub_id: string };
  const authorization = await request.post(
    `/api/v2/platform-accounts/${account.pub_id}/authorizations`,
    {
      headers: apiHeaders,
      data: {
        scopes: ['read', 'query'],
        forbidden_actions: ['publish', 'payment', 'security_settings'],
        regions: ['CN-BJ'],
        valid_from: new Date(Date.now() - 60_000).toISOString(),
        valid_until: new Date(Date.now() + 60 * 60_000).toISOString(),
      },
    },
  );
  expect(authorization.ok()).toBeTruthy();
  const profile = await request.post(
    `/api/v2/platform-accounts/${account.pub_id}/profiles/enroll`,
    {
      headers: apiHeaders,
      data: {
        profile_payload: JSON.stringify({ fixture: true }),
        custody_mode: 'server',
        constraints: ['READ_ONLY'],
        expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
      },
    },
  );
  expect(profile.ok()).toBeTruthy();
  const session = {
    tenant: identity.tenant_pub_id,
    actor: subject,
    role: 'admin',
  };
  await page.addInitScript((context) => {
    localStorage.setItem('geo.session.tenant', context.tenant);
    localStorage.setItem('geo.session.actor', context.actor);
    localStorage.setItem('geo.session.role', context.role);
  }, session);
  await page.goto('/platform/operations/execution');
  await expect(page.getByRole('heading', { name: '执行与账号控制面' })).toBeVisible();
  await expect(page.getByRole('heading', { name: accountMask })).toBeVisible();
  await expect(page.getByText('adapter_ready').first()).toBeVisible();
  await expect(page.getByText('尚未 live 验证').first()).toBeVisible();
  await expect(page.getByRole('heading', { name: '运行与任务矩阵' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '人工接管队列' })).toBeVisible();
  const healthResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      response.url().includes('/health-checks') &&
      !response.url().includes('live_canary=true'),
  );
  await page.getByRole('button', { name: 'L0–L3 健康检查' }).click();
  const health = await healthResponse;
  expect(health.ok()).toBeTruthy();
  expect((await health.json()).levels.L0).toBe('passed');
  await expect(page.getByText('health_check.completed').first()).toBeVisible();

  const surfaces = await page.evaluate(() => ({
    url: location.href,
    body: document.body.textContent ?? '',
    localStorage: JSON.stringify(localStorage),
    sessionStorage: JSON.stringify(sessionStorage),
  }));
  const rendered = JSON.stringify(surfaces);
  for (const canary of [
    'sid=secret',
    'Bearer secret',
    '/tmp/browser-profile',
    'proxy-password',
    'human_verified_token',
  ]) {
    expect(rendered).not.toContain(canary);
  }
  await captureSafeScreenshot(page, {
    path: path.resolve(
      process.cwd(),
      `tests/visual-evidence/s01/operations-execution-${testInfo.project.name}.png`,
    ),
    fullPage: true,
  });
});
