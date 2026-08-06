import { expect, test, type Page } from './runtime-fixture';
import { expectAccessible } from './accessibility';

const projectPubId = 'prj_customer_governance_integrity';

async function installCustomerExperience(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_governance_integrity');
    localStorage.setItem('geo.session.actor', 'customer-governance-integrity');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_customer_governance_integrity',
        user_pub_id: 'usr_customer_governance_integrity',
        role: 'customer',
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
            pub_id: projectPubId,
            tenant_pub_id: 'tnt_customer_governance_integrity',
            name: '客户治理历史完整性项目',
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
  await page.route('**/api/v2/projects/*/resources/*', (route) => {
    const kind = new URL(route.request().url()).pathname.split('/').at(-1);
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          pub_id: kind === 'competitors' ? 'cmp_governance_safe' : 'brd_governance_safe',
          project_pub_id: projectPubId,
          resource_kind: kind,
          version: 1,
          data: {
            name: kind === 'competitors' ? '安全确认竞品' : '安全确认品牌',
            website: 'https://safe.example.test',
          },
        },
      ]),
    });
  });
}

const profileRow = (
  revision: number,
  extension: Record<string, unknown> = {},
): Record<string, unknown> => ({
  pub_id: `cpv_governance_${revision}`,
  project_pub_id: projectPubId,
  revision,
  company_name: `安全资料企业 ${revision}`,
  contact_role: '品牌负责人',
  audience: '需要可验证知识服务的企业采购团队',
  public_statement: '该企业资料来自客户确认并可公开核验。',
  created_at: `2026-07-${String(20 + revision).padStart(2, '0')}T00:00:00Z`,
  ...extension,
});

const assetRow = (
  revision: number,
  extension: Record<string, unknown> = {},
): Record<string, unknown> => ({
  pub_id: `acv_governance_${revision}`,
  project_pub_id: projectPubId,
  revision,
  brand_name: `安全确认品牌 ${revision}`,
  website: 'https://safe.example.test',
  product_name: '安全确认产品',
  competitor_name: '安全确认竞品',
  prohibited_claim: '未经证明的行业第一',
  created_at: `2026-07-${String(20 + revision).padStart(2, '0')}T00:00:00Z`,
  ...extension,
});

test('profile and asset history stay project-bound, bounded and cursor-safe', async ({ page }) => {
  await installCustomerExperience(page);
  await page.route('**/api/v2/projects/*/client-profile/versions**', (route) => {
    const limit = new URL(route.request().url()).searchParams.get('limit');
    const data =
      limit === '1'
        ? [profileRow(5)]
        : [
            profileRow(5),
            profileRow(4, {
              project_pub_id: 'prj_other_customer',
              audience: 'Bearer cross-project-profile-e2e-canary',
            }),
            profileRow(3, { profile_path: '/secret/profile/history-e2e-canary' }),
          ];
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, next_cursor: limit === '1' ? '5' : '4' }),
    });
  });
  await page.route('**/api/v2/projects/*/asset-confirmations**', (route) => {
    const limit = new URL(route.request().url()).searchParams.get('limit');
    const data =
      limit === '1'
        ? [assetRow(5)]
        : [
            assetRow(5),
            assetRow(4, {
              project_pub_id: 'prj_other_customer',
              prohibited_claim: 'Cookie=asset-cross-project-e2e-canary',
            }),
            assetRow(3, { token: 'Bearer asset-history-limit-e2e-canary' }),
          ];
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, next_cursor: limit === '1' ? '5' : '4' }),
    });
  });

  await page.goto('/platform/customer/?section=profile');
  await expect(page.getByLabel('企业全称')).toHaveValue('安全资料企业 5');
  await expect(
    page.getByText('客户声明历史：服务返回 3 条，浏览器安全视图展示 1 条'),
  ).toBeVisible();
  await expect(page.getByText(/客户声明历史包含跨项目、乱序、游标不一致/)).toBeVisible();
  await expect(page.getByRole('button', { name: '下一页' })).toBeDisabled();

  await page.goto('/platform/customer/?section=assets');
  await expect(page.getByText('安全确认品牌', { exact: true })).toBeVisible();
  await expect(
    page.getByText('客户资产确认历史：服务返回 3 条，浏览器安全视图展示 1 条'),
  ).toBeVisible();
  await expect(page.getByText(/资产确认历史包含跨项目、乱序、游标不一致/)).toBeVisible();
  await expect(page.getByText(/v5 · 安全确认品牌 5/)).toBeVisible();
  await expect(page.getByRole('button', { name: '下一页' })).toBeDisabled();

  await expectAccessible(page);
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    )
    .toBe(true);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /cross-project-profile-e2e-canary|history-e2e-canary|asset-cross-project-e2e-canary|asset-history-limit-e2e-canary|Bearer |Cookie=|\/secret\/profile/i,
  );
});

test('project catalog rows stay kind-bound and secret extensions never cross browser surfaces', async ({
  page,
}) => {
  await installCustomerExperience(page);
  await page.route('**/api/v2/projects/*/resources/*', (route) => {
    const kind = new URL(route.request().url()).pathname.split('/').at(-1);
    const common = {
      project_pub_id: projectPubId,
      version: 1,
    };
    const rows =
      kind === 'brands'
        ? [
            {
              ...common,
              pub_id: 'ent_brand_catalog_safe',
              resource_kind: 'brands',
              data: {
                name: '浏览器安全品牌',
                website: 'https://brand.safe.example.test',
                token: 'Bearer nested-brand-catalog-canary',
              },
              cookie: 'SESSION=brand-row-extension-canary',
            },
            {
              ...common,
              pub_id: 'ent_brand_cross_project',
              project_pub_id: 'prj_other_customer',
              resource_kind: 'brands',
              data: {
                name: '跨项目隐藏品牌',
                profile_path: '/secret/profile/brand-cross-project-canary',
              },
            },
          ]
        : kind === 'competitors'
          ? [
              {
                ...common,
                pub_id: 'ent_competitor_catalog_safe',
                resource_kind: 'competitors',
                data: {
                  name: '浏览器安全竞品',
                  website: 'https://competitor.safe.example.test',
                },
              },
              {
                ...common,
                pub_id: 'ent_competitor_wrong_kind',
                resource_kind: 'brands',
                data: { name: '种类错配隐藏竞品', otp: '824911' },
              },
            ]
          : kind === 'query-items'
            ? [
                {
                  ...common,
                  pub_id: 'ent_query_catalog_safe',
                  resource_kind: 'query-items',
                  data: {
                    parent_pub_id: 'ent_query_group_safe',
                    text: '浏览器安全问题是什么？',
                    priority: 10,
                    proxy_password: 'query-catalog-canary',
                  },
                },
                {
                  ...common,
                  pub_id: 'ent_query_cross_project',
                  project_pub_id: 'prj_other_customer',
                  resource_kind: 'query-items',
                  data: {
                    parent_pub_id: 'ent_query_group_safe',
                    text: '跨项目隐藏问题',
                    priority: 10,
                  },
                },
              ]
            : [
                {
                  ...common,
                  pub_id: 'ent_goal_catalog_safe',
                  resource_kind: 'goals',
                  data: {
                    metric: 'mention_rate',
                    payload: { target: 0.8, token: 'Bearer goal-payload-canary' },
                    state: 'active',
                  },
                },
                {
                  ...common,
                  pub_id: 'ent_goal_wrong_kind',
                  resource_kind: 'query-items',
                  data: {
                    metric: 'mention_rate',
                    payload: { target: 0.7 },
                    state: 'active',
                  },
                },
              ];
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(rows),
    });
  });
  await page.route('**/api/v2/projects/*/asset-confirmations**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: [], next_cursor: null }),
    }),
  );

  await page.goto('/platform/customer/?section=assets');
  await expect(page.getByText('浏览器安全品牌', { exact: true })).toBeVisible();
  await expect(page.getByText('浏览器安全竞品', { exact: true })).toBeVisible();
  await expect(page.getByText(/品牌与竞品目录包含跨项目、种类错配/)).toBeVisible();

  await page.goto('/platform/customer/?section=questions');
  await expect(page.getByText('浏览器安全问题是什么？', { exact: true })).toBeVisible();
  await expect(
    page.locator('.request-list strong').filter({ hasText: '品牌提及率' }),
  ).toBeVisible();
  await expect(page.getByText(/问题与目标目录包含跨项目、种类错配/)).toBeVisible();

  await expectAccessible(page);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /nested-brand-catalog-canary|brand-row-extension-canary|brand-cross-project-canary|824911|query-catalog-canary|goal-payload-canary|Bearer |SESSION=|profile_path|proxy_password|otp/i,
  );
});

test('browser history discards slower superseded profile and asset pages', async ({ page }) => {
  let profileOldRequests = 0;
  let assetOldRequests = 0;
  let releaseProfile: (() => void) | undefined;
  let releaseAssets: (() => void) | undefined;
  const profileGate = new Promise<void>((resolve) => {
    releaseProfile = resolve;
  });
  const assetGate = new Promise<void>((resolve) => {
    releaseAssets = resolve;
  });
  await installCustomerExperience(page);
  await page.route('**/api/v2/projects/*/client-profile/versions**', async (route) => {
    const url = new URL(route.request().url());
    const cursor = url.searchParams.get('cursor');
    const limit = url.searchParams.get('limit');
    if (cursor === '4') {
      profileOldRequests += 1;
      await profileGate;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            profileRow(3, {
              company_name: '过期资料企业',
              token: 'Bearer stale-profile-history-canary',
            }),
          ],
          next_cursor: null,
        }),
      });
    }
    const data = limit === '1' ? [profileRow(5)] : [profileRow(5), profileRow(4)];
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, next_cursor: limit === '1' ? '5' : '4' }),
    });
  });
  await page.route('**/api/v2/projects/*/asset-confirmations**', async (route) => {
    const url = new URL(route.request().url());
    const cursor = url.searchParams.get('cursor');
    const limit = url.searchParams.get('limit');
    if (cursor === '4') {
      assetOldRequests += 1;
      await assetGate;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            assetRow(3, {
              brand_name: '过期确认品牌',
              token: 'Cookie=stale-asset-history-canary',
            }),
          ],
          next_cursor: null,
        }),
      });
    }
    const data = limit === '1' ? [assetRow(5)] : [assetRow(5), assetRow(4)];
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, next_cursor: limit === '1' ? '5' : '4' }),
    });
  });

  await page.goto('/platform/customer/?section=profile');
  await expect(page.getByLabel('企业全称')).toHaveValue('安全资料企业 5');
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/declaration_cursor=rev_4/);
  await expect.poll(() => profileOldRequests).toBe(1);
  await expect(page.getByText('正在加载', { exact: true })).toBeVisible();
  await expect(page.getByLabel('企业全称')).toHaveCount(0);
  await page.goBack();
  await expect(page).not.toHaveURL(/declaration_cursor/);
  await expect(page.getByLabel('企业全称')).toHaveValue('安全资料企业 5');
  releaseProfile?.();
  await page.waitForTimeout(500);
  await expect(page.getByLabel('企业全称')).toHaveValue('安全资料企业 5');
  await expect(page.getByText('过期资料企业')).toHaveCount(0);

  await page.goto('/platform/customer/?section=assets');
  await expect(page.getByText(/v5 · 安全确认品牌 5/)).toBeVisible();
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/asset_history_cursor=rev_4/);
  await expect.poll(() => assetOldRequests).toBe(1);
  await expect(page.getByText('正在加载', { exact: true })).toBeVisible();
  await expect(page.getByText(/v5 · 安全确认品牌 5/)).toHaveCount(0);
  await page.goBack();
  await expect(page).not.toHaveURL(/asset_history_cursor/);
  await expect(page.getByText(/v5 · 安全确认品牌 5/)).toBeVisible();
  releaseAssets?.();
  await page.waitForTimeout(500);
  await expect(page.getByText(/v5 · 安全确认品牌 5/)).toBeVisible();
  await expect(page.getByText('过期确认品牌')).toHaveCount(0);

  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /stale-profile-history-canary|stale-asset-history-canary|Bearer |Cookie=/i,
  );
  await expectAccessible(page);
});
