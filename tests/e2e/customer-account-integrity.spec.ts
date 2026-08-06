import { expect, test, type Page } from './runtime-fixture';
import { expectAccessible } from './accessibility';

const accountPubId = 'pac_customer_account_integrity';
const pairingPubId = 'int_customer_account_integrity';

const accountView = {
  pub_id: accountPubId,
  account_mask: '尾号 · 7391',
  platform_label: '豆包',
  owner_label: '当前客户',
  custody_mode: 'customer_device',
  admission_level: 'read_verified',
  scopes: ['read', 'query'],
  authorization_expires_at: '2026-12-31T15:59:59Z',
  region_label: '中国大陆 · 华北',
  session_health: 'healthy',
  last_verified_at: '2026-07-25T06:00:00Z',
  intervention_status: 'none',
  revocation_receipt_pub_id: null,
  revoked_at: null,
};

async function installAccountExperience(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_account_integrity');
    localStorage.setItem('geo.session.actor', 'customer-account-integrity');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_customer_account_integrity',
        user_pub_id: 'usr_customer_account_integrity',
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
            pub_id: 'prj_customer_account_integrity',
            tenant_pub_id: 'tnt_customer_account_integrity',
            name: '客户账号完整性项目',
            state: 'active',
            created_at: '2026-07-25T00:00:00Z',
            updated_at: '2026-07-25T00:00:00Z',
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
}

const pairingView = (
  pubId: string,
  state: string,
  accountId = accountPubId,
  extension: Record<string, unknown> = {},
) => ({
  pub_id: pubId,
  account_pub_id: accountId,
  account_mask: '尾号 · 7391',
  allowed_domain: 'doubao.com',
  action: 'read',
  challenge_type: 'qr',
  state,
  expires_at: null,
  ...extension,
});

test('oversized account lifecycle collections stay bounded, account-bound and secret-free', async ({
  page,
}) => {
  await installAccountExperience(page);

  await page.route('**/api/v2/customer/platform-accounts/responsible-members', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        Array.from({ length: 101 }, (_, index) => ({
          user_pub_id: `usr_account_member_${String(index).padStart(3, '0')}`,
          label:
            index === 1
              ? 'Bearer responsible-limit-canary'
              : `成员 · ${String(index).padStart(8, '0')}`,
          role: 'operator',
        })),
      ),
    }),
  );
  await page.route('**/api/v2/customer/platform-accounts', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { ...accountView, cookie: 'SESSION=account-root-canary' },
        {
          ...accountView,
          pub_id: 'pac_customer_account_older',
          profile_path: '/secret/profile/account-over-limit-canary',
        },
        {
          ...accountView,
          pub_id: 'pac_customer_account_phone_leak',
          account_mask: 'account13800138000***',
          token: 'Bearer account-phone-leak-canary',
        },
      ]),
    }),
  );
  await page.route('**/api/v2/customer/platform-accounts/*/events', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        Array.from({ length: 102 }, (_, index) => ({
          pub_id: `sev_account_integrity_${String(index).padStart(3, '0')}`,
          event_type: index === 1 ? 'Cookie=event-limit-canary' : `customer_account.event_${index}`,
          occurred_at: new Date(Date.UTC(2026, 6, 26, 0, 0) - index * 60_000).toISOString(),
          ...(index === 0 ? { otp: '824911' } : {}),
        })),
      ),
    }),
  );
  await page.route('**/api/v2/customer/platform-accounts/*/pairings', (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(
          pairingView(pairingPubId, 'pending', accountPubId, {
            token: 'Bearer pairing-create-canary',
          }),
        ),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        pairingView(pairingPubId, 'completed', accountPubId, { otp: '318294' }),
        pairingView('int_customer_account_cross', 'completed', 'pac_customer_account_other', {
          profile_path: '/secret/profile/pairing-cross-account-canary',
        }),
        ...Array.from({ length: 50 }, (_, index) =>
          pairingView(
            `int_customer_account_candidate_${String(index).padStart(3, '0')}`,
            'pending',
          ),
        ),
      ]),
    });
  });

  await page.goto('/platform/customer/?section=accounts');
  await expect(page.getByText('客户安全投影 · 真实 API')).toBeVisible();
  await expect(
    page.getByText('客户账号候选：服务返回 3 条，浏览器安全视图展示 1 条'),
  ).toBeVisible();
  await expect(
    page.getByText('当前租户责任人：服务返回 101 条，浏览器安全视图展示 99 条'),
  ).toBeVisible();
  await expect(
    page.getByText('账号安全事件：服务返回 102 条，浏览器安全视图展示 99 条'),
  ).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('当前租户责任人、账号安全事件');

  await page.getByRole('button', { name: '创建一次性配对' }).click();
  await page.getByRole('button', { name: '确认并创建配对请求' }).click();
  await page.getByRole('button', { name: '刷新真实配对状态' }).click();
  await expect(page.getByRole('heading', { name: '配对与验证已完成' })).toBeVisible();
  await expect(
    page.getByText('配对状态候选：服务返回 52 条，浏览器安全视图展示 1 条'),
  ).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('配对状态候选');

  const exposedSurfaces = await page.evaluate(() => ({
    dom: document.documentElement.outerHTML,
    url: location.href,
    localStorage: { ...localStorage },
    sessionStorage: { ...sessionStorage },
  }));
  expect(JSON.stringify(exposedSurfaces)).not.toMatch(
    /responsible-limit-canary|event-limit-canary|account-root-canary|account-over-limit-canary|account-phone-leak-canary|13800138000|pairing-create-canary|pairing-cross-account-canary|SESSION=|Bearer |Cookie=|824911|318294|\/secret\/profile/i,
  );
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    )
    .toBe(true);
  await expectAccessible(page);
});

test('a same-account but input-mismatched pairing receipt fails locally without leakage', async ({
  page,
}) => {
  let pairingWrites = 0;
  await installAccountExperience(page);
  await page.route('**/api/v2/customer/platform-accounts/responsible-members', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          user_pub_id: 'usr_account_receipt_owner',
          label: '成员 · 00000001',
          role: 'operator',
        },
      ]),
    }),
  );
  await page.route('**/api/v2/customer/platform-accounts', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([accountView]),
    }),
  );
  await page.route('**/api/v2/customer/platform-accounts/*/events', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    }),
  );
  await page.route('**/api/v2/customer/platform-accounts/*/pairings', (route) => {
    if (route.request().method() === 'POST') {
      pairingWrites += 1;
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          ...pairingView(pairingPubId, 'pending'),
          allowed_domain: 'wrong.example',
          action: 'query',
          token: 'Bearer pairing-input-mismatch-canary',
          profile_path: '/secret/profile/pairing-input-mismatch-canary',
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  await page.goto('/platform/customer/?section=accounts');
  await page.getByRole('button', { name: '创建一次性配对' }).click();
  await page.getByRole('button', { name: '确认并创建配对请求' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '加载失败' })).toBeVisible();
  await expect(page.getByText(/真实 API 已创建待处理配对/)).toHaveCount(0);
  await expect(page.getByText('真实配对待受控终端处理')).toHaveCount(0);
  expect(pairingWrites).toBe(1);
  await expectAccessible(page);
  const exposedSurfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(exposedSurfaces).not.toMatch(
    /wrong\.example|pairing-input-mismatch-canary|Bearer |\/secret\/profile/i,
  );
});

test('revocation and newer refreshes discard slower pairing and event responses', async ({
  page,
}) => {
  let eventRequests = 0;
  let pairingReads = 0;
  await installAccountExperience(page);

  await page.route('**/api/v2/customer/platform-accounts/responsible-members', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          user_pub_id: 'usr_account_race_owner',
          label: '成员 · 00000001',
          role: 'operator',
        },
      ]),
    }),
  );
  await page.route('**/api/v2/customer/platform-accounts', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([accountView]),
    }),
  );
  await page.route('**/api/v2/customer/platform-accounts/*/events', async (route) => {
    eventRequests += 1;
    if (eventRequests === 1) {
      await new Promise((resolve) => setTimeout(resolve, 650));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            pub_id: 'sev_account_stale',
            event_type: 'customer_account.stale_event_canary',
            occurred_at: '2026-07-25T08:00:00Z',
            token: 'Bearer stale-event-response-canary',
          },
        ]),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          pub_id: 'sev_account_current',
          event_type: 'customer_pairing.current',
          occurred_at: '2026-07-25T08:01:00Z',
        },
      ]),
    });
  });
  await page.route('**/api/v2/customer/platform-accounts/*/pairings', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(pairingView(pairingPubId, 'pending')),
      });
      return;
    }
    pairingReads += 1;
    if (pairingReads === 1) {
      await new Promise((resolve) => setTimeout(resolve, 700));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          pairingView(pairingPubId, 'pending', accountPubId, {
            token: 'Bearer stale-pairing-response-canary',
          }),
        ]),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([pairingView(pairingPubId, 'completed')]),
    });
  });
  await page.route('**/api/v2/customer/platform-accounts/*/revoke', (route) =>
    route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        workflow_id:
          'account-revocation/tnt_customer_account_integrity/pac_customer_account_integrity',
        run_id: 'run_account_integrity',
      }),
    }),
  );

  await page.goto('/platform/customer/?section=accounts');
  await expect(page.getByText('尾号 · 7391', { exact: true })).toBeVisible();
  await expect.poll(() => eventRequests).toBe(1);
  await page.getByRole('button', { name: '创建一次性配对' }).click();
  await page.getByRole('button', { name: '确认并创建配对请求' }).click();
  await expect(page.getByText('customer_pairing.current')).toBeVisible();

  const refresh = page.getByRole('button', { name: '刷新真实配对状态' });
  await refresh.click();
  await expect.poll(() => pairingReads).toBe(1);
  await refresh.click();
  await expect(page.getByRole('heading', { name: '配对与验证已完成' })).toBeVisible();
  await page.getByRole('button', { name: '撤销授权' }).click();
  await expect(page.getByRole('heading', { name: '等待真实撤销回执' })).toBeVisible();
  await page.waitForTimeout(900);
  await expect(page.getByRole('heading', { name: '等待真实撤销回执' })).toBeVisible();
  await expect(page.getByText('customer_account.stale_event_canary')).toHaveCount(0);
  await expect(page.getByText(/受控终端仍在处理/)).toHaveCount(0);

  const exposedSurfaces = await page.evaluate(() => ({
    dom: document.documentElement.outerHTML,
    url: location.href,
    localStorage: { ...localStorage },
    sessionStorage: { ...sessionStorage },
  }));
  expect(JSON.stringify(exposedSurfaces)).not.toMatch(
    /stale-event-response-canary|stale-pairing-response-canary|SESSION=|Bearer |Cookie=/i,
  );
  await expectAccessible(page);
});
