import { expect, test } from './runtime-fixture';
import { readDownload, secretArtifactPattern } from './downloads';
import { captureSafeScreenshot, expectSafePageScreenshot } from './screenshot-safety';

test('customer pairs, verifies and revokes without secret leakage', async ({ page }, testInfo) => {
  const viewportName = testInfo.project.name.replace('customer-', '');
  await page.route('**/api/v2/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'mock-ready',
        service: 'geo-platform-v2',
        version: 'contract-v1',
        cookie: 'SESSION=dlp-canary',
        access_token: 'Bearer dlp-canary',
        otp: '824911',
        proxy_password: 'proxy-dlp-canary',
        full_phone: '13800138000',
        profile_path: '/secret/browser/profile',
        biometric_material: 'face-dlp-canary',
        nested: { authorization: 'Bearer nested-dlp-canary' },
        opaque_metadata: { challenge: 615204, subject: 13912345678 },
      }),
    }),
  );
  await page.route('**/api/v2/analytics/overview**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  );

  await page.goto('/platform/customer/');
  await page.getByRole('button', { name: /平台账号/ }).click();
  await expect(page.getByRole('heading', { name: '客户终端安全配对' })).toBeVisible();
  await page.getByRole('button', { name: '查看撤销流程' }).click();
  await expect(page.getByRole('dialog', { name: '客户撤销权与执行顺序' })).toContainText(
    '立即拒绝新租约与新动作',
  );
  await page.getByRole('button', { name: '关闭撤销流程' }).click();
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await page.getByLabel('账号 owner').fill('Cookie=SESSION-form-canary');
  await page.getByRole('button', { name: '登记授权' }).click();
  await expect(page.getByText(/请勿在普通表单粘贴验证码、Cookie、token、密码/)).toBeVisible();
  await expect(page.getByText('授权登记已更新；配对范围将采用当前安全投影。')).toHaveCount(0);
  await page.getByLabel('账号 owner').fill('顾清');
  await page.getByLabel('运营责任人').fill('周岚');
  await page.getByLabel('托管模式').selectOption('customer-device');
  await page.getByLabel('授权到期日').fill('2026-12-31');
  await page.getByLabel('授权地域').fill('中国大陆 · 华北');
  await page.getByLabel('draft', { exact: true }).check();
  await page.getByLabel('publish', { exact: true }).check();
  await page.getByRole('button', { name: '登记授权' }).click();
  await expect(page.getByText('授权登记已更新；配对范围将采用当前安全投影。')).toBeVisible();
  await expect(page.getByText(/账号 owner · 顾清 \/ 责任人 · 周岚/)).toBeVisible();
  await expect(page.getByText('2026-12-31', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('heading', { name: /客户终端托管 · read.*query.*draft.*publish/ }),
  ).toBeVisible();
  await page.getByRole('button', { name: '创建一次性配对' }).click();
  const pairing = page.getByRole('region', { name: '客户终端安全配对' });
  await expect(pairing.getByText('read / query / draft / publish', { exact: true })).toBeVisible();
  await expect(pairing.getByText('doubao.com', { exact: true })).toBeVisible();
  await expect(pairing.getByText('中国大陆 · 华北', { exact: true })).toBeVisible();
  await expect(pairing.getByText('尾号 · 4821', { exact: true })).toBeVisible();
  await expect(pairing.getByText('豆包', { exact: true })).toBeVisible();
  await expect(page.getByText(/请勿在聊天或普通表单粘贴验证码/)).toBeVisible();
  await page.getByRole('button', { name: '确认并进入配对演示' }).click();
  await expect(page.getByRole('img', { name: /一次性安全配对二维码占位/ })).toBeVisible();
  await page.getByRole('button', { name: '终端已连接' }).click();
  await expect(page.getByRole('heading', { name: '请在豆包原生页面完成验证' })).toBeVisible();
  await expect(
    page.getByText(/OTP、官方 App 扫码、Push MFA、passkey、人脸\/活体跳转和图形/),
  ).toBeVisible();
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await page.getByRole('button', { name: '模拟平台确认完成' }).click();
  await expect(page.getByText(/准入保持 read_verified/)).toBeVisible();
  await expect(
    page.getByText(/draft\/publish 不会因登记授权被描述为已完成 live 验证/),
  ).toBeVisible();
  await page.getByRole('button', { name: '撤销授权' }).click();
  await expect(page.getByRole('heading', { name: '撤销已执行' })).toBeVisible();
  await expect(page.getByText('删除托管秘密副本')).toBeVisible();

  const browserSurfaces = await page.evaluate(() => ({
    url: location.href,
    localStorage: JSON.stringify(localStorage),
    sessionStorage: JSON.stringify(sessionStorage),
    body: document.body.textContent ?? '',
  }));
  const exposedSurfaces = browserSurfaces;
  for (const secret of [
    'SESSION=',
    'Bearer ',
    'dlp-canary',
    '/secret/browser/profile',
    '13800138000',
    '824911',
    '615204',
    '13912345678',
  ]) {
    expect(JSON.stringify(exposedSurfaces)).not.toContain(secret);
  }
  await captureSafeScreenshot(page, {
    path: `tests/e2e-results/customer-account-${viewportName}.png`,
    fullPage: true,
  });
});

test('validated customer lifecycle writes stay single under synchronous duplicate activation', async ({
  page,
}) => {
  const writes: { url: string; body: unknown }[] = [];
  const pairingOutcomes = ['rejected', 'expired', 'failed', 'completed'] as const;
  let pairingPoll = 0;
  let revocationAccepted = false;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_account_live');
    localStorage.setItem('geo.session.actor', 'customer-account-live');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_customer_account_live',
        user_pub_id: 'usr_customer_account_live',
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
            pub_id: 'prj_customer_account_live',
            tenant_pub_id: 'tnt_customer_account_live',
            name: '客户账号联调项目',
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
      body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v2' }),
    }),
  );
  await page.route('**/api/v2/analytics/overview**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  );
  const account = {
    pub_id: 'pac_customer_live_safe',
    account_mask: '尾号 · 7391',
    platform_label: '豆包',
    owner_label: '当前客户',
    custody_mode: 'customer_device',
    admission_level: 'adapter_ready',
    scopes: ['read', 'query', 'draft', 'publish'],
    authorization_expires_at: '2026-12-31T15:59:59Z',
    region_label: '中国大陆 · 华北',
    session_health: 'challenge_required',
    last_verified_at: null,
    intervention_status: 'pending',
    revocation_receipt_pub_id: null,
    revoked_at: null,
    cookie: 'SESSION=account-api-canary',
    access_token: 'Bearer account-api-canary',
    profile_path: '/secret/profile/account-api-canary',
    full_phone: '13800138000',
  };
  await page.route('**/api/v2/customer/platform-accounts/responsible-members', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          user_pub_id: 'usr_01J00000000000000000000001',
          label: '成员 · 00000001',
          role: 'operator',
        },
        {
          user_pub_id: 'usr_01J00000000000000000000002',
          label: 'Bearer responsible-member-canary',
          role: 'operator',
        },
      ]),
    }),
  );
  await page.route('**/api/v2/customer/platform-accounts', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          revocationAccepted
            ? [
                {
                  ...account,
                  admission_level: 'suspended',
                  scopes: [],
                  session_health: 'revoked',
                  revocation_receipt_pub_id: 'rev_customer_live_safe',
                  revoked_at: '2026-07-25T06:00:00Z',
                  deletion_proof: 'Bearer revocation-receipt-canary',
                },
              ]
            : [],
        ),
      });
      return;
    }
    writes.push({ url: route.request().url(), body: route.request().postDataJSON() });
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        ...account,
        scopes: [],
        authorization_expires_at: null,
        session_health: 'degraded',
        intervention_status: 'none',
      }),
    });
  });
  await page.route('**/api/v2/customer/platform-accounts/*/authorizations', async (route) => {
    writes.push({ url: route.request().url(), body: route.request().postDataJSON() });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(account),
    });
  });
  await page.route('**/api/v2/customer/platform-accounts/*/events', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          pub_id: 'sev_safe',
          event_type: 'customer_authorization.updated',
          occurred_at: '2026-07-24T12:00:00Z',
          token: 'Bearer event-api-canary',
        },
        {
          pub_id: 'sev_unsafe',
          event_type: 'Cookie=event-api-canary',
          occurred_at: '2026-07-24T12:01:00Z',
        },
        {
          pub_id: 'sev_ambiguous_time',
          event_type: 'customer_authorization.ambiguous_time',
          occurred_at: '1',
        },
      ]),
    }),
  );
  await page.route('**/api/v2/customer/platform-accounts/*/pairings', async (route) => {
    const isRead = route.request().method() === 'GET';
    const pairing = {
      pub_id: 'int_customer_live_safe',
      account_pub_id: account.pub_id,
      account_mask: account.account_mask,
      allowed_domain: 'doubao.com',
      action: 'read',
      challenge_type: 'qr',
      state: isRead
        ? pairingOutcomes[Math.min(pairingPoll++, pairingOutcomes.length - 1)]
        : 'pending',
      expires_at: null,
      otp: '824911',
    };
    if (route.request().method() === 'POST')
      writes.push({ url: route.request().url(), body: route.request().postDataJSON() });
    await route.fulfill({
      status: isRead ? 200 : 201,
      contentType: 'application/json',
      body: JSON.stringify(isRead ? [pairing] : pairing),
    });
  });
  await page.route('**/api/v2/customer/platform-accounts/*/revoke', async (route) => {
    writes.push({ url: route.request().url(), body: null });
    revocationAccepted = true;
    await route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        workflow_id: 'account-revocation/tnt_customer_account_live/pac_customer_live_safe',
        run_id: 'run_customer_safe',
      }),
    });
  });

  await page.goto('/platform/customer/');
  const accountsNavigation = page.getByRole('button', { name: /平台账号/ });
  await expect(accountsNavigation.locator('em')).toHaveCount(0);
  await accountsNavigation.click();
  await expect(page.getByText('客户安全投影 · 真实 API')).toBeVisible();
  await expect(
    page
      .locator('section.panel')
      .filter({ has: page.getByRole('heading', { name: '平台账号与授权' }) })
      .getByText('暂无数据'),
  ).toBeVisible();
  await expect(page.getByLabel('账号掩码')).toHaveValue('');
  await expect(page.getByLabel('授权到期日')).toHaveValue('');
  await expect(page.getByLabel('授权地域')).toHaveValue('');
  await expect(page.getByLabel('read', { exact: true })).not.toBeChecked();
  await expect(page.getByLabel('query', { exact: true })).not.toBeChecked();
  await expect(page.getByRole('button', { name: '登记授权' })).toBeDisabled();
  const pairingPanel = page
    .locator('section.panel')
    .filter({ has: page.getByRole('heading', { name: '客户终端安全配对' }) });
  await expect(pairingPanel.getByText('暂无数据')).toBeVisible();
  await expect(pairingPanel.getByText(/不会从空账号列表或表单默认值推断托管模式/)).toBeVisible();
  await expect(page.getByRole('button', { name: '创建一次性配对' })).toHaveCount(0);
  await expect(pairingPanel.getByText('混合托管 · read / query')).toHaveCount(0);
  await page.getByLabel('账号掩码').fill('customer@example.test');
  await expect(page.getByText('只填写带 *、尾号或其他明确隐藏标记的账号掩码')).toBeVisible();
  await expect(page.getByRole('button', { name: '登记授权' })).toBeDisabled();
  await page.getByLabel('账号掩码').fill('尾号 · 7391');
  await expect(page.getByLabel('账号 owner')).toHaveValue('用户 · t_live');
  await expect(page.getByLabel('账号 owner')).toHaveAttribute('readonly');
  await expect(page.getByText(/由当前认证主体的安全投影在服务端绑定/)).toBeVisible();
  await page.getByLabel('运营责任人').selectOption('usr_01J00000000000000000000001');
  await page.getByLabel('托管模式').selectOption('customer-device');
  await page.getByLabel('授权到期日').fill('2026-01-01');
  await expect(page.getByText('授权到期日必须晚于当前时间')).toBeVisible();
  await expect(page.getByRole('button', { name: '登记授权' })).toBeDisabled();
  await page.getByLabel('授权到期日').fill('2026-12-31');
  await page.getByLabel('授权地域').fill('中国大陆 · 华北');
  await page.getByLabel('read', { exact: true }).check();
  await page.getByLabel('query', { exact: true }).check();
  await page.getByLabel('draft', { exact: true }).check();
  await page.getByLabel('publish', { exact: true }).check();
  const authorizationButton = page.getByRole('button', { name: '登记授权' });
  await authorizationButton.evaluate((button) => {
    button.addEventListener('click', () => (button as HTMLButtonElement).click(), { once: true });
  });
  await authorizationButton.click();
  await expect(
    page.getByText(/owner 由当前认证主体在服务端绑定，责任人来自当前租户有效成员/),
  ).toBeVisible();
  await expect(page.getByText('尾号 · 7391', { exact: true })).toBeVisible();
  await expect(page.getByText('当前客户', { exact: true })).toBeVisible();
  await expect(page.getByText('customer_authorization.updated')).toBeVisible();
  await expect(page.getByText('Cookie=event-api-canary')).toHaveCount(0);
  await expect(page.getByText('customer_authorization.ambiguous_time')).toHaveCount(0);

  await page.getByRole('button', { name: '创建一次性配对' }).click();
  await page.getByRole('button', { name: '拒绝', exact: true }).click();
  await expect(page.getByRole('heading', { name: '本次配对已拒绝' })).toBeVisible();
  await expect(page.getByText(/未创建配对请求或一次性通道/)).toBeVisible();
  await expect(page.getByText(/一次性令牌已销毁/)).toHaveCount(0);
  expect(writes).toHaveLength(2);
  await page.getByRole('button', { name: '重新开始' }).click();

  const createAndPollPairing = async () => {
    await page.getByRole('button', { name: '创建一次性配对' }).click();
    const pairingButton = page.getByRole('button', { name: '确认并创建配对请求' });
    await pairingButton.evaluate((button) => {
      button.addEventListener('click', () => (button as HTMLButtonElement).click(), { once: true });
    });
    await pairingButton.click();
    await expect(page.getByText(/真实 API 已创建待处理配对/)).toBeVisible();
    await expect(page.getByRole('button', { name: '终端已连接' })).toHaveCount(0);
    await page.getByRole('button', { name: '刷新真实配对状态' }).click();
  };

  await createAndPollPairing();
  await expect(page.getByRole('heading', { name: '本次配对已拒绝' })).toBeVisible();
  await page.getByRole('button', { name: '重新开始' }).click();
  await createAndPollPairing();
  await expect(page.getByRole('heading', { name: '一次性配对已超时' })).toBeVisible();
  await page.getByRole('button', { name: '重新开始' }).click();
  await createAndPollPairing();
  await expect(page.getByRole('heading', { name: '本次原生验证失败' })).toBeVisible();
  await expect(page.getByText(/未提升任何准入等级/)).toBeVisible();
  await page.getByRole('button', { name: '重新开始' }).click();
  await createAndPollPairing();
  await expect(page.getByRole('heading', { name: '配对与验证已完成' })).toBeVisible();
  const revokeButton = page.getByRole('button', { name: '撤销授权' });
  await revokeButton.evaluate((button) => {
    button.addEventListener('click', () => (button as HTMLButtonElement).click(), { once: true });
  });
  await revokeButton.click();
  await expect(page.getByRole('heading', { name: '等待真实撤销回执' })).toBeVisible();
  await page.getByRole('button', { name: '刷新撤销状态' }).click();
  await expect(page.getByRole('heading', { name: '撤销已执行' })).toBeVisible();
  await expect(page.getByText('rev_customer_live_safe')).toBeVisible();
  await expect(page.getByText('2026-07-25 06:00')).toBeVisible();
  const revocationReceipt = page.getByRole('article', {
    name: '撤销回执 rev_customer_live_safe',
  });
  await expect(revocationReceipt.getByText('未在客户安全投影中公开')).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    )
    .toBe(true);
  await expectSafePageScreenshot(page, 'customer-live-revocation-receipt.png', {
    fullPage: true,
    animations: 'disabled',
    // 跨渲染器 AA 残余兜底：本页内容全静态，但 jammy/noble 的 freetype 版本差在彩色
    // 文字（绿色徽标/标签）边缘产生 21~35px 覆盖值残余（run 31436523820 三档实测，
    // 页高一致、差异全部位于字形边缘）。缺省预算是 0 像素，故按实测两倍余量给 64。
    maxDiffPixels: 64,
  });

  expect(writes).toHaveLength(7);
  expect(writes[0]?.body).toMatchObject({
    platform_slug: 'doubao',
    account_mask: '尾号 · 7391',
    custody_mode: 'customer_device',
    responsible_member_pub_id: 'usr_01J00000000000000000000001',
  });
  expect(writes[1]?.body).toMatchObject({
    scopes: ['read', 'query', 'draft', 'publish'],
    regions: ['中国大陆 · 华北'],
    responsible_member_pub_id: 'usr_01J00000000000000000000001',
  });
  expect(writes.slice(2, 6).map((write) => write.body)).toEqual(
    Array.from({ length: 4 }, () => ({
      allowed_domain: 'doubao.com',
      action: 'read',
      challenge_type: 'qr',
    })),
  );
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /account-api-canary|event-api-canary|responsible-member-canary|SESSION=|Bearer |824911|13800138000|\/secret\/profile/i,
  );
  expect(JSON.stringify(writes)).not.toMatch(/Cookie|Bearer|otp|profile_path|13800138000/i);
});

test('customer pairing refusal and timeout destroy the one-time channel', async ({ page }) => {
  await page.route('**/api/v2/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'mock-ready',
        service: 'geo-platform-v2',
        version: 'contract-v1',
      }),
    }),
  );
  await page.goto('/platform/customer/');
  await page.getByRole('button', { name: /平台账号/ }).click();
  await page.getByRole('button', { name: '创建一次性配对' }).click();
  await page.getByRole('button', { name: '拒绝', exact: true }).click();
  await expect(page.getByRole('heading', { name: '本次配对已拒绝' })).toBeVisible();
  await expect(page.getByText(/通道和一次性令牌已销毁/)).toBeVisible();

  await page.getByRole('button', { name: '重新开始' }).click();
  await page.getByRole('button', { name: '创建一次性配对' }).click();
  await page.getByRole('button', { name: '确认并进入配对演示' }).click();
  await page.getByRole('button', { name: '模拟超时' }).click();
  await expect(page.getByRole('heading', { name: '一次性配对已超时' })).toBeVisible();
  await expect(page.getByText(/没有改变现有授权或会话/)).toBeVisible();
});

test('validated customer submits a project change request through the generated live contract', async ({
  page,
}) => {
  let capturedRequest: { headers: Record<string, string>; body: Record<string, unknown> } | null =
    null;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_live');
    localStorage.setItem('geo.session.actor', 'customer-live-subject');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_customer_live',
        user_pub_id: 'usr_customer_live',
        role: 'customer',
        permissions: ['project:read', 'project:write'],
      }),
    }),
  );
  await page.route('**/api/v2/projects?**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            pub_id: 'prj_customer_live',
            tenant_pub_id: 'tnt_customer_live',
            name: '客户真实项目',
            state: 'active',
            created_at: '2026-07-24T00:00:00Z',
            updated_at: '2026-07-24T00:00:00Z',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );
  await page.route('**/api/v2/projects/prj_customer_live/resources/*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '[]',
    }),
  );
  await page.route(
    '**/api/v2/projects/prj_customer_live/resources/change-requests',
    async (route) => {
      const request = route.request();
      const requestBody = request.postDataJSON() as Record<string, unknown>;
      capturedRequest = {
        headers: request.headers(),
        body: requestBody,
      };
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          pub_id: 'ent_change_safe',
          project_pub_id: 'prj_customer_live',
          resource_kind: 'change-requests',
          version: 1,
          data: {
            kind: requestBody.kind,
            payload: requestBody.payload,
            state: requestBody.state,
            reviewed_by: null,
            token: 'Bearer change-request-response-canary',
          },
          profile_path: '/secret/profile/change-request-response-canary',
        }),
      });
    },
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
  await page.getByRole('button', { name: '监测问题与目标' }).click();
  await expect(page.getByText(/生成的 OpenAPI client/)).toBeVisible();
  await page.getByLabel('关注问题').fill('请使用验证码 824911 查询企业知识库');
  await page.getByLabel('业务原因').fill('需要覆盖客户采购决策阶段的真实比较问题。');
  await page.getByRole('button', { name: '提交审核' }).click();
  await expect(page.getByText(/请勿在普通表单粘贴验证码、Cookie、token、密码/)).toBeVisible();
  expect(capturedRequest).toBeNull();

  await page.getByLabel('关注问题').fill('制造企业如何选择可信的私有化知识库？');
  await page.getByRole('button', { name: '提交审核' }).click();
  await expect(
    page.getByRole('status').filter({ hasText: '申请已进入待运营审核队列' }),
  ).toBeVisible();

  expect(capturedRequest).not.toBeNull();
  if (!capturedRequest) throw new Error('live change request was not captured');
  expect(capturedRequest.headers['x-tenant-id']).toBe('tnt_customer_live');
  expect(capturedRequest.headers['x-actor-id']).toBe('customer-live-subject');
  expect(capturedRequest.headers['x-actor-role']).toBe('customer');
  expect(capturedRequest.headers['idempotency-key']).toMatch(/^customer-change-/);
  expect(capturedRequest.headers['x-service-token']).toBeUndefined();
  expect(capturedRequest.body).toMatchObject({
    kind: 'add_query',
    state: 'pending',
    payload: {
      question: '制造企业如何选择可信的私有化知识库？',
      goal_metric: 'mention_rate',
      target_percent: 70,
    },
  });
  expect(JSON.stringify(capturedRequest)).not.toMatch(
    /cookie|bearer|otp|proxy_password|profile_path|biometric/i,
  );
  const browserSurfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(browserSurfaces).not.toMatch(
    /change-request-response-canary|Bearer |profile_path|\/secret\/profile/i,
  );
});

test('monitoring filters are URL-bound and restore through browser history', async ({ page }) => {
  await page.route('**/api/v2/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'mock-ready',
        service: 'geo-platform-v2',
        version: 'contract-v1',
      }),
    }),
  );
  await page.goto('/platform/customer/');
  await page.getByRole('button', { name: '品牌可见度' }).click();
  await expect(page).toHaveURL(/section=monitoring/);
  await expect(
    page.getByRole('heading', { name: '云岫智能 · 品牌可见度与模型表现' }),
  ).toBeVisible();
  await expect(
    page.getByRole('img', { name: '品牌提及率、Top3 率和引用覆盖率趋势' }),
  ).toBeVisible();
  await expect(page.getByLabel('模型表现数据表')).toBeVisible();
  await expect(page.getByLabel('地区表现数据表')).toBeVisible();
  await expect(page.getByLabel('回答模式表现数据表')).toBeVisible();
  const analysisFilters = page.getByLabel('分析筛选');
  const modelFilter = analysisFilters
    .locator('label')
    .filter({ hasText: /^AI 模型/ })
    .locator('select');
  const modeFilter = analysisFilters
    .locator('label')
    .filter({ hasText: /^回答模式/ })
    .locator('select');
  const windowFilter = analysisFilters
    .locator('label')
    .filter({ hasText: /^观察窗口/ })
    .locator('select');
  const regionFilter = analysisFilters
    .locator('label')
    .filter({ hasText: /^地区/ })
    .locator('select');
  await modelFilter.selectOption('DeepSeek');
  await expect.poll(() => new URL(page.url()).searchParams.get('model')).toBe('DeepSeek');
  await modeFilter.selectOption('深度回答');
  await expect.poll(() => new URL(page.url()).searchParams.get('mode')).toBe('深度回答');
  await windowFilter.selectOption('7d');
  await expect.poll(() => new URL(page.url()).searchParams.get('window')).toBe('7d');
  await regionFilter.selectOption('华东');
  await expect.poll(() => new URL(page.url()).searchParams.get('region')).toBe('华东');
  await expect(page.getByRole('heading', { name: '云岫智能 · 真实 AI 回答' })).toHaveCount(0);

  await page.goBack();
  await expect(regionFilter).toHaveValue('all');
  await expect(windowFilter).toHaveValue('7d');
  await page.goBack();
  await expect(windowFilter).toHaveValue('30d');
  await expect(modeFilter).toHaveValue('深度回答');
  await page.goBack();
  await expect(modeFilter).toHaveValue('all');
  await expect(modelFilter).toHaveValue('DeepSeek');
  await page.goBack();
  await expect(modelFilter).toHaveValue('all');
});

test('customer profile, brand assets and configuration requests validate and submit', async ({
  page,
}, testInfo) => {
  const viewportName = testInfo.project.name.replace('customer-', '');
  await page.route('**/api/v2/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'mock-ready',
        service: 'geo-platform-v2',
        version: 'contract-v1',
      }),
    }),
  );
  await page.goto('/platform/customer/');

  await page.getByRole('button', { name: '客户资料' }).click();
  await page.getByRole('button', { name: '保存并生成版本' }).click();
  await expect(page.getByText('提交前必须确认资料真实性')).toBeVisible();
  await page.getByRole('checkbox', { name: /我确认上述客户声明真实/ }).check();
  await page.getByRole('button', { name: '保存并生成版本' }).click();
  await expect(page.getByText(/客户声明 v3/)).toBeVisible();

  await page.getByRole('button', { name: '品牌产品与竞品' }).click();
  await page.getByLabel('品牌名称').fill('澄明云');
  await page.getByLabel('官方 HTTPS 网站').fill('https://example.test');
  await page.getByLabel('产品或服务').fill('可信知识助手');
  await page.getByLabel('客户指定竞品').fill('北辰智库');
  await page.getByLabel('禁止使用的表述').fill('未经证明的行业第一');
  await page.getByRole('checkbox', { name: /我确认品牌、产品、竞品与禁止表述真实/ }).check();
  await page.getByRole('button', { name: '登记资产' }).click();
  await expect(page.getByText('澄明云')).toBeVisible();

  await page.getByRole('button', { name: '监测问题与目标' }).click();
  await page.getByRole('button', { name: '提交审核' }).click();
  await expect(page.getByText('问题至少需要 8 个字')).toBeVisible();
  await page.getByLabel('关注问题').fill('制造企业如何选择可信的私有化知识库？');
  await page.getByLabel('业务原因').fill('需要覆盖客户采购决策阶段的真实比较问题。');
  await page.getByRole('button', { name: '提交审核' }).click();
  await expect(page.getByText('待运营审核', { exact: true })).toBeVisible();

  await captureSafeScreenshot(page, {
    path: `tests/e2e-results/customer-forms-${viewportName}.png`,
    fullPage: true,
  });
});

test('customer reviews evidence, exports, questions reports and manages members', async ({
  page,
}, testInfo) => {
  const viewportName = testInfo.project.name.replace('customer-', '');
  await page.route('**/api/v2/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'mock-ready',
        service: 'geo-platform-v2',
        version: 'contract-v1',
      }),
    }),
  );
  await page.goto('/platform/customer/');
  await page.getByRole('button', { name: '前往报告' }).click();
  await expect(page).toHaveURL(/section=reports/);
  await expect(page.getByRole('heading', { name: '2026 Q3 GEO 监测与优化建议' })).toBeVisible();

  await page.getByRole('button', { name: '证据中心' }).click();
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/answer_page=2/);
  await page.getByLabel('回答地域').selectOption('上海');
  await expect(page.getByText('企业知识库如何选择？')).toBeVisible();
  await expect(page.getByText('第 1 / 1 页')).toBeVisible();
  await expect(page).not.toHaveURL(/answer_page=/);
  await page.getByLabel('回答地域').selectOption('all');
  await page.getByLabel('回答模式筛选').selectOption('deep');
  await expect(page).toHaveURL(/answer_mode=deep/);
  await expect(page.getByText('企业知识库如何选择？')).toBeVisible();
  await page.getByRole('button', { name: '查看回答截图' }).first().click();
  await expect(page.getByRole('dialog', { name: '证据与历史差异' })).toBeVisible();
  await expect(page.getByRole('img', { name: /锚点高亮品牌提及/ })).toBeVisible();
  await page.getByRole('button', { name: '关闭证据弹窗' }).click();
  await page.getByRole('button', { name: '打开证据中心' }).first().click();
  await expect(page.getByRole('dialog', { name: '证据与历史差异' })).toBeVisible();
  await page.getByRole('button', { name: '关闭证据弹窗' }).click();
  await page.getByLabel('搜索问题').fill('OTP: 824911 · Cookie=session-dlp-canary');
  await expect(page).not.toHaveURL(/824911|session-dlp-canary/);
  const evidenceDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '生成证据包' }).click();
  const evidenceArtifact = await evidenceDownload;
  expect(evidenceArtifact.suggestedFilename()).toBe('evidence-package-manifest.json');
  const evidenceContent = await readDownload(evidenceArtifact);
  const evidenceManifest = JSON.parse(evidenceContent) as {
    version: string;
    answers: Array<{ id: string; question: string; model: string; capturedAt: string }>;
  };
  expect(evidenceManifest.version).toBe('1.0');
  expect(evidenceManifest.answers.length).toBeGreaterThan(0);
  expect(evidenceContent).not.toMatch(secretArtifactPattern);

  await page.getByRole('button', { name: '报告' }).click();
  await page.getByRole('button', { name: '在线预览' }).click();
  await expect(page.getByRole('dialog', { name: '2026 Q3 GEO 监测与优化建议' })).toContainText(
    '发布 hash 已核验',
  );
  await page.getByRole('button', { name: '关闭在线报告预览' }).click();
  const csvDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出筛选数据' }).click();
  const csvArtifact = await csvDownload;
  expect(csvArtifact.suggestedFilename()).toBe('geo-report-data.csv');
  const csvContent = await readDownload(csvArtifact);
  expect(csvContent).toContain('metric,value,numerator,denominator');
  expect(csvContent).toContain('mention_rate,0.684,26,38');
  expect(csvContent).not.toMatch(secretArtifactPattern);
  await page.getByRole('textbox', { name: '问题' }).fill('Cookie=SESSION-customer-question-canary');
  await expect(page.getByText(/请勿在普通表单粘贴验证码/)).toBeVisible();
  await expect(page.getByRole('button', { name: '提交问题' })).toBeDisabled();
  await page.getByRole('textbox', { name: '问题' }).fill('Top 3 目标值如何复算？');
  await page.getByRole('button', { name: '提交问题' }).click();
  await page.getByRole('button', { name: '确认收到 v1.2' }).click();
  await expect(page.getByText('已确认接收 v1.2')).toBeVisible();

  await page.getByRole('button', { name: '项目成员' }).click();
  await page.getByLabel('姓名').fill('周岚');
  await page.getByLabel('工作邮箱').fill('zhoulan@example.test');
  await page.getByRole('button', { name: '发送邀请' }).click();
  await expect(page.getByText('z***@example.test')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('zhoulan@example.test');
  await page.getByRole('button', { name: '管理 林澄' }).click();
  await expect(page.getByRole('button', { name: '改为客户成员' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '移出项目' })).toBeDisabled();
  await page.getByRole('button', { name: '关闭成员管理' }).click();
  await page.getByRole('button', { name: '管理 周岚' }).click();
  await page.getByRole('button', { name: '提升为客户管理员' }).click();
  await expect(page.getByRole('dialog')).toContainText('客户管理员');
  await page.getByRole('button', { name: '移出项目' }).click();
  await expect(page.getByRole('button', { name: '管理 周岚' })).toHaveCount(0);
  await expect(page.getByRole('status').filter({ hasText: '已移出项目' })).toBeVisible();

  await captureSafeScreenshot(page, {
    path: `tests/e2e-results/customer-delivery-${viewportName}.png`,
    fullPage: true,
  });
});
