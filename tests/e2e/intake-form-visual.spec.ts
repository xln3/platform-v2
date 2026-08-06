import { expect, test } from './runtime-fixture';
import { expectSafePageScreenshot } from './screenshot-safety';
import { prepareVisualPage } from './visual-regression';

// 免登录填表页是确定性视觉面：token 域响应全部本地打桩（与 operations media-prices
// fixture 同一惯例），基线不依赖外部 API。真实邀请链路（identity/bootstrap → 签发
// invite → #t= 打开）由集成侧在 playwright.config 登记第五 app 项目后另行覆盖。
const contextFixture = {
  form: {
    title: 'GEO 客户信息收集表（通用版）',
    note: '填写约需 10 分钟。标注 ★ 为必填项。本表信息用于制定后续 GEO 方案，我方承担保密义务。',
    sections: [
      {
        id: 'promo',
        title: '第一部分　宣传内容与目标',
        fields: [
          {
            key: 'company_name',
            label: '公司 / 品牌名称',
            type: 'text',
            required: true,
            hint: null,
            options: [],
            items: [],
          },
          {
            key: 'review_category',
            label: '行业广告审查分类',
            type: 'radio',
            required: true,
            hint: null,
            options: [
              { value: 'A', label: 'A 类（特殊行业）' },
              { value: 'none', label: '无需审查' },
            ],
            items: [],
          },
          {
            key: 'pre_review_required',
            label: '是否属于法定前置审查行业',
            type: 'bool',
            required: true,
            hint: '选「是」须提供广告审查批准文件',
            options: [],
            items: [],
          },
          {
            key: 'contact_person',
            label: '联系人',
            type: 'text',
            required: true,
            hint: null,
            options: [],
            items: [],
          },
          {
            key: 'goals',
            label: '推广目标',
            type: 'chips',
            required: true,
            hint: null,
            options: [
              { value: 'brand', label: '品牌曝光' },
              { value: 'leads', label: '线索获客' },
            ],
            items: [],
          },
          {
            key: 'platforms',
            label: '目标 AI 平台',
            type: 'chips',
            required: false,
            hint: null,
            options: [
              { value: 'doubao', label: '豆包' },
              { value: 'deepseek', label: 'DeepSeek' },
            ],
            items: [],
          },
          {
            key: 'regions',
            label: '重点地域',
            type: 'tags',
            required: false,
            hint: '全国 或 重点区域',
            options: [],
            items: [],
          },
          {
            key: 'selling_points',
            label: '核心卖点',
            type: 'textarea',
            required: true,
            hint: '每一条卖点需有出处',
            options: [],
            items: [],
          },
          {
            key: 'evidence_links',
            label: '可公开引用的佐证材料',
            type: 'textarea',
            required: false,
            hint: '每行一条链接或说明',
            options: [],
            items: [],
          },
        ],
      },
      {
        id: 'qualification',
        title: '第二部分　资质',
        fields: [
          {
            key: 'business_license_code',
            label: '营业执照 · 统一社会信用代码',
            type: 'text',
            required: true,
            hint: '18 位（0-9/A-Z）',
            options: [],
            items: [],
          },
          {
            key: 'licenses',
            label: '行业许可证',
            type: 'subform',
            required: false,
            hint: '证照名称 / 编号 / 有效期至',
            options: [],
            items: [],
          },
          {
            key: 'truth_confirmed',
            label: '信息真实性确认',
            type: 'confirm',
            required: true,
            hint: null,
            options: [],
            items: [
              '所填信息真实、准确、完整',
              '核心卖点均有可公开引用的佐证',
              '授权服务方将本表用于 GEO 方案设计',
              '知悉虚假宣传的合规风险',
              '愿意配合补充证明材料',
            ],
          },
          {
            key: 'filler_name',
            label: '填表人',
            type: 'text',
            required: false,
            hint: '网页版以勾选提交代替签字',
            options: [],
            items: [],
          },
        ],
      },
    ],
  },
  brand: {
    exists: true,
    pub_id: 'brd_visualsafe',
    name: '演示品牌',
    website: 'https://brand.example.cn',
    aliases: ['演示牌'],
  },
  competitors: [
    {
      pub_id: 'cmp_visualsafe',
      name: '竞品示例',
      website: null,
      created_at: '2026-08-01T02:03:04Z',
    },
  ],
  profile: {
    project_pub_id: 'prj_visualsafe',
    exists: true,
    prefilled: {},
    updated_at: '2026-08-01T02:03:04Z',
    contact_person: '林演示',
    contact_info: null,
    website: 'https://brand.example.cn',
    wechat: null,
    douyin: null,
    social_media: null,
    audience_desc: null,
    business_license_code: null,
    selling_points: '连续三年行业复购率领先（年报口径）',
    filler_name: null,
    ad_review_no: null,
    ad_review_authority: null,
    ad_review_expiry: null,
    review_category: 'none',
    pre_review_required: false,
    truth_confirmed: null,
    goals: ['brand'],
    audience_type: [],
    platforms: ['doubao'],
    regions: ['全国'],
    trademarks: [],
    ad_review_doc_types: [],
    evidence_links: [],
    licenses: [],
  },
  invite: {
    pub_id: 'itv_visualsafe',
    expires_at: '2026-08-09T00:00:00Z',
    submitted: false,
    submitted_at: null,
    ai_quota: 3,
    ai_used: 0,
    ai_remaining: 3,
  },
};

const json = (body: unknown) => JSON.stringify(body);

async function routeIntakeFormApi(page: Parameters<typeof prepareVisualPage>[0]) {
  await page.route('**/api/v2/intake-form/**', (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === 'GET' && url.pathname === '/api/v2/intake-form/context') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: json(contextFixture),
      });
    }
    if (route.request().method() === 'GET' && url.pathname === '/api/v2/intake-form/promos') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: json({
          items: [
            {
              pub_id: 'prm_visualsafe',
              kind: 'product',
              payload: { name: '演示产品', category: 'SaaS 软件', features: ['稳定', '易用'] },
              created_at: '2026-08-01T02:03:04Z',
              updated_at: '2026-08-01T02:03:04Z',
            },
          ],
        }),
      });
    }
    if (
      route.request().method() === 'GET' &&
      url.pathname === '/api/v2/intake-form/trigger-questions'
    ) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: json({
          items: [
            {
              pub_id: 'trq_visualsafe_a',
              text: '演示品牌和竞品示例怎么选',
              status: 'draft',
              created_at: '2026-08-01T02:03:04Z',
            },
            {
              pub_id: 'trq_visualsafe_b',
              text: '演示产品适合中小企业吗',
              status: 'draft',
              created_at: '2026-08-01T02:03:04Z',
            },
          ],
        }),
      });
    }
    return route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: json({
        error: {
          code: 'not_found',
          message: 'not_found',
          request_id: 'req_visualsafe',
          details: {},
        },
      }),
    });
  });
}

const workspaces = [
  { section: 'brand', snapshot: 'intake-form-brand.png', ready: '品牌信息' },
  { section: 'research', snapshot: 'intake-form-research.png', ready: 'AI 联网调研' },
  { section: 'profile', snapshot: 'intake-form-profile.png', ready: '客户信息表' },
  { section: 'questions', snapshot: 'intake-form-questions.png', ready: '期望问法' },
  { section: 'submit', snapshot: 'intake-form-submit.png', ready: '确认并提交' },
] as const;

for (const workspace of workspaces) {
  test(`intake-form ${workspace.section} visual baseline has no page overflow`, async ({
    page,
  }) => {
    await routeIntakeFormApi(page);
    await prepareVisualPage(
      page,
      `/platform/intake-form/?section=${workspace.section}#t=e2eVisualSafe`,
    );
    await page.getByRole('heading', { name: workspace.ready, exact: true }).waitFor();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
      .toBe(true);
    await expectSafePageScreenshot(page, workspace.snapshot, {
      fullPage: true,
      animations: 'disabled',
      maxDiffPixelRatio: 0.005,
    });
  });
}
