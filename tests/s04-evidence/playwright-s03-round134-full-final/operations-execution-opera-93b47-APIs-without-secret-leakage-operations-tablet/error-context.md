# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: operations-execution.spec.ts >> operations execution uses real lifecycle APIs without secret leakage
- Location: tests/e2e/operations-execution.spec.ts:5:5

# Error details

```
Error: expect(received).toBeTruthy()

Received: false
```

# Test source

```ts
  1   | import { expect, test } from './runtime-fixture';
  2   | import path from 'node:path';
  3   | import { captureSafeScreenshot } from './screenshot-safety';
  4   | 
  5   | test('operations execution uses real lifecycle APIs without secret leakage', async ({
  6   |   page,
  7   |   request,
  8   | }, testInfo) => {
  9   |   const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
  10  |   const subject = `operations-e2e-${suffix}`;
  11  |   const bootstrap = await request.post('/api/v2/identity/bootstrap', {
  12  |     headers: { 'X-Bootstrap-Secret': 'development-bootstrap' },
  13  |     data: {
  14  |       tenant_name: `Operations E2E ${suffix}`,
  15  |       subject,
  16  |       display_name: 'Operations E2E Admin',
  17  |     },
  18  |   });
> 19  |   expect(bootstrap.ok()).toBeTruthy();
      |                          ^ Error: expect(received).toBeTruthy()
  20  |   const identity = (await bootstrap.json()) as {
  21  |     tenant_pub_id: string;
  22  |     user_pub_id: string;
  23  |   };
  24  |   const apiHeaders = {
  25  |     'X-Tenant-Id': identity.tenant_pub_id,
  26  |     'X-Actor-Id': subject,
  27  |     'X-Actor-Role': 'admin',
  28  |   };
  29  |   const accountMask = `e2e-***${suffix.slice(-4)}`;
  30  |   const accountResponse = await request.post('/api/v2/platform-accounts', {
  31  |     headers: apiHeaders,
  32  |     data: {
  33  |       platform_slug: 'fixed',
  34  |       platform_name: 'Auditable Fixed Adapter',
  35  |       account_mask: accountMask,
  36  |       owner_pub_id: identity.user_pub_id,
  37  |       purpose: 'e2e-health',
  38  |       responsible_pub_id: identity.user_pub_id,
  39  |       custody_mode: 'server',
  40  |       region: 'CN-BJ',
  41  |     },
  42  |   });
  43  |   expect(accountResponse.ok()).toBeTruthy();
  44  |   const account = (await accountResponse.json()) as { pub_id: string };
  45  |   const authorization = await request.post(
  46  |     `/api/v2/platform-accounts/${account.pub_id}/authorizations`,
  47  |     {
  48  |       headers: apiHeaders,
  49  |       data: {
  50  |         scopes: ['read', 'query'],
  51  |         forbidden_actions: ['publish', 'payment', 'security_settings'],
  52  |         regions: ['CN-BJ'],
  53  |         valid_from: new Date(Date.now() - 60_000).toISOString(),
  54  |         valid_until: new Date(Date.now() + 60 * 60_000).toISOString(),
  55  |       },
  56  |     },
  57  |   );
  58  |   expect(authorization.ok()).toBeTruthy();
  59  |   const profile = await request.post(
  60  |     `/api/v2/platform-accounts/${account.pub_id}/profiles/enroll`,
  61  |     {
  62  |       headers: apiHeaders,
  63  |       data: {
  64  |         profile_payload: JSON.stringify({ fixture: true }),
  65  |         custody_mode: 'server',
  66  |         constraints: ['READ_ONLY'],
  67  |         expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
  68  |       },
  69  |     },
  70  |   );
  71  |   expect(profile.ok()).toBeTruthy();
  72  |   const session = {
  73  |     tenant: identity.tenant_pub_id,
  74  |     actor: subject,
  75  |     role: 'admin',
  76  |   };
  77  |   await page.addInitScript((context) => {
  78  |     localStorage.setItem('geo.session.tenant', context.tenant);
  79  |     localStorage.setItem('geo.session.actor', context.actor);
  80  |     localStorage.setItem('geo.session.role', context.role);
  81  |   }, session);
  82  |   await page.goto('/platform/operations/execution');
  83  |   await expect(page.getByRole('heading', { name: '执行与账号控制面' })).toBeVisible();
  84  |   await expect(page.getByRole('heading', { name: accountMask })).toBeVisible();
  85  |   await expect(page.getByText('adapter_ready').first()).toBeVisible();
  86  |   await expect(page.getByText('尚未 live 验证').first()).toBeVisible();
  87  |   await expect(page.getByRole('heading', { name: '运行与任务矩阵' })).toBeVisible();
  88  |   await expect(page.getByRole('heading', { name: '人工接管队列' })).toBeVisible();
  89  |   const healthResponse = page.waitForResponse(
  90  |     (response) =>
  91  |       response.request().method() === 'POST' &&
  92  |       response.url().includes('/health-checks') &&
  93  |       !response.url().includes('live_canary=true'),
  94  |   );
  95  |   await page.getByRole('button', { name: 'L0–L3 健康检查' }).click();
  96  |   const health = await healthResponse;
  97  |   expect(health.ok()).toBeTruthy();
  98  |   expect((await health.json()).levels.L0).toBe('passed');
  99  |   await expect(page.getByText('health_check.completed').first()).toBeVisible();
  100 | 
  101 |   const surfaces = await page.evaluate(() => ({
  102 |     url: location.href,
  103 |     body: document.body.textContent ?? '',
  104 |     localStorage: JSON.stringify(localStorage),
  105 |     sessionStorage: JSON.stringify(sessionStorage),
  106 |   }));
  107 |   const rendered = JSON.stringify(surfaces);
  108 |   for (const canary of [
  109 |     'sid=secret',
  110 |     'Bearer secret',
  111 |     '/tmp/browser-profile',
  112 |     'proxy-password',
  113 |     'human_verified_token',
  114 |   ]) {
  115 |     expect(rendered).not.toContain(canary);
  116 |   }
  117 |   await captureSafeScreenshot(page, {
  118 |     path: path.resolve(
  119 |       process.cwd(),
```