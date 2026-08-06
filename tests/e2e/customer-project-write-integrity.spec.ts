import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';

test('project form writes reject synchronous duplicate submits and retain no secret receipts', async ({
  page,
}) => {
  let releaseProfile: (() => void) | undefined;
  let releaseAsset: (() => void) | undefined;
  let releaseQuestion: (() => void) | undefined;
  const profileGate = new Promise<void>((resolve) => {
    releaseProfile = resolve;
  });
  const assetGate = new Promise<void>((resolve) => {
    releaseAsset = resolve;
  });
  const questionGate = new Promise<void>((resolve) => {
    releaseQuestion = resolve;
  });
  const projectPubId = 'prj_customer_project_write_integrity';
  let profileWrites = 0;
  let assetWrites = 0;
  let questionWrites = 0;
  let persistedProfile: Record<string, unknown> | null = null;
  let persistedAsset: Record<string, unknown> | null = null;
  let profileBody: Record<string, unknown> | null = null;
  let assetBody: Record<string, unknown> | null = null;
  let questionBody: Record<string, unknown> | null = null;
  let profileHeaders: Record<string, string> | null = null;
  let assetHeaders: Record<string, string> | null = null;
  let questionHeaders: Record<string, string> | null = null;

  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_project_write_integrity');
    localStorage.setItem('geo.session.actor', 'customer-project-write-integrity');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_customer_project_write_integrity',
        user_pub_id: 'usr_customer_project_write_integrity',
        role: 'customer',
        permissions: ['project:read', 'project:write'],
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
            pub_id: projectPubId,
            tenant_pub_id: 'tnt_customer_project_write_integrity',
            name: '项目写入完整性',
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
  await page.route('**/api/v2/projects/*/client-profile/versions**', async (route) => {
    const request = route.request();
    if (request.method() === 'POST') {
      profileWrites += 1;
      profileBody = request.postDataJSON() as Record<string, unknown>;
      profileHeaders = request.headers();
      await profileGate;
      persistedProfile = {
        pub_id: 'cpv_project_write_integrity_01',
        project_pub_id: projectPubId,
        revision: 1,
        ...profileBody,
        created_at: '2026-07-27T00:01:00Z',
        token: 'Bearer delayed-profile-write-canary',
        profile_path: '/secret/profile/delayed-profile-write-canary',
      };
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(persistedProfile),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: persistedProfile ? [persistedProfile] : [],
        next_cursor: null,
      }),
    });
  });
  await page.route('**/api/v2/projects/*/asset-confirmations**', async (route) => {
    const request = route.request();
    if (request.method() === 'POST') {
      assetWrites += 1;
      assetBody = request.postDataJSON() as Record<string, unknown>;
      assetHeaders = request.headers();
      await assetGate;
      persistedAsset = {
        pub_id: 'acv_project_write_integrity_01',
        project_pub_id: projectPubId,
        revision: 1,
        ...assetBody,
        created_at: '2026-07-27T00:02:00Z',
        authorization: 'Bearer delayed-asset-write-canary',
        profile_path: '/secret/profile/delayed-asset-write-canary',
      };
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(persistedAsset),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: persistedAsset ? [persistedAsset] : [],
        next_cursor: null,
      }),
    });
  });
  await page.route('**/api/v2/projects/*/resources/*', async (route) => {
    const request = route.request();
    if (request.method() === 'POST') {
      questionWrites += 1;
      questionBody = request.postDataJSON() as Record<string, unknown>;
      questionHeaders = request.headers();
      await questionGate;
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          pub_id: 'chg_project_write_integrity_01',
          project_pub_id: projectPubId,
          resource_kind: 'change-requests',
          version: 1,
          data: questionBody,
          cookie: 'SESSION=delayed-question-write-canary',
          profile_path: '/secret/profile/delayed-question-write-canary',
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '[]',
    });
  });

  await page.goto('/platform/customer/?section=profile');
  await expect(page.getByRole('heading', { name: '甲方资料' })).toBeVisible();
  await page.getByRole('checkbox', { name: /我确认上述客户声明真实/ }).check();
  await page
    .locator('form')
    .filter({ hasText: '甲方资料' })
    .evaluate((form) => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
  await expect.poll(() => profileWrites).toBe(1);
  await page.waitForTimeout(250);
  expect(profileWrites).toBe(1);
  await expect(page.getByRole('button', { name: '正在提交' })).toBeDisabled();
  releaseProfile?.();
  await expect(page.getByText('客户声明 v1 · 已保存')).toBeVisible();

  await page.goto('/platform/customer/?section=assets');
  await expect(page.getByRole('heading', { name: '品牌、产品与竞品' })).toBeVisible();
  await page.getByLabel('品牌名称').fill('完整性品牌');
  await page.getByLabel('官方 HTTPS 网站').fill('https://integrity.example.test');
  await page.getByLabel('产品或服务').fill('完整性产品');
  await page.getByLabel('客户指定竞品').fill('完整性竞品');
  await page.getByLabel('禁止使用的表述').fill('未经证明的行业第一');
  await page.getByRole('checkbox', { name: /我确认品牌、产品、竞品与禁止表述真实/ }).check();
  await page
    .locator('form')
    .filter({ hasText: '登记品牌资产' })
    .evaluate((form) => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
  await expect.poll(() => assetWrites).toBe(1);
  await page.waitForTimeout(250);
  expect(assetWrites).toBe(1);
  await expect(page.getByRole('button', { name: '正在登记…' })).toBeDisabled();
  releaseAsset?.();
  await expect(page.getByText('最新客户确认 v1')).toBeVisible();

  await page.goto('/platform/customer/?section=questions');
  await expect(page.getByRole('heading', { name: '问题、目标与配置申请' })).toBeVisible();
  await page.getByLabel('关注问题').fill('如何验证企业知识服务效果？');
  await page.getByLabel('业务原因').fill('需要增加一条可追溯的业务问题配置');
  await page
    .locator('form')
    .filter({ hasText: '问题、目标与配置申请' })
    .evaluate((form) => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
  await expect.poll(() => questionWrites).toBe(1);
  await page.waitForTimeout(250);
  expect(questionWrites).toBe(1);
  await expect(page.getByRole('button', { name: '正在提交…' })).toBeDisabled();
  releaseQuestion?.();
  await expect(page.getByText('申请已进入待运营审核队列')).toBeVisible();

  expect(profileBody).toMatchObject({ truth_confirmed: true });
  expect(assetBody).toMatchObject({
    brand_name: '完整性品牌',
    website: 'https://integrity.example.test',
    product_name: '完整性产品',
    competitor_name: '完整性竞品',
    prohibited_claim: '未经证明的行业第一',
    truth_confirmed: true,
  });
  expect(questionBody).toMatchObject({
    kind: 'add_query',
    state: 'pending',
    payload: {
      question: '如何验证企业知识服务效果？',
      priority: 'medium',
      goal_metric: 'mention_rate',
      target_percent: 70,
      reason: '需要增加一条可追溯的业务问题配置',
    },
  });
  for (const headers of [profileHeaders, assetHeaders, questionHeaders]) {
    expect(headers).toMatchObject({
      'x-tenant-id': 'tnt_customer_project_write_integrity',
      'x-actor-id': 'customer-project-write-integrity',
      'x-actor-role': 'customer',
    });
  }
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
    /delayed-profile-write-canary|delayed-asset-write-canary|delayed-question-write-canary|\/secret\/profile|Bearer |SESSION=/i,
  );
  await expectAccessible(page);
});
