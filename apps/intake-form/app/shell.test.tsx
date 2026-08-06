// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router';

type JsonBody = Record<string, unknown> | unknown[] | string | number | boolean | null;
type RouteHandler = (request: Request) => Promise<Response> | Response;
type SeenRequest = { method: string; path: string; body: unknown; token: string | null };

// projected wrapper 的 client 测试位只要求「带 HTTP 方法的对象」；这里实现一个
// 最小传输层：路径参数替换 + 查询串 + 请求记录，转发到各用例的路由表打桩，
// 不经真实网络（模块级默认 client 的相对 base 依赖浏览器 URL 解析，Node 下不可用）。
const mockState = vi.hoisted(() => ({
  routes: {} as Record<string, RouteHandler>,
  seen: [] as SeenRequest[],
}));

const invokeRaw = async (
  method: string,
  template: string,
  init: {
    params?: {
      path?: Record<string, string>;
      query?: Record<string, string>;
      header?: Record<string, string>;
    };
    body?: unknown;
  } = {},
) => {
  let pathname = template;
  for (const [key, value] of Object.entries(init.params?.path ?? {})) {
    pathname = pathname.replace(`{${key}}`, encodeURIComponent(value));
  }
  const url = new URL(pathname, 'http://127.0.0.1:45999');
  for (const [key, value] of Object.entries(init.params?.query ?? {})) {
    url.searchParams.set(key, value);
  }
  const header = init.params?.header ?? {};
  mockState.seen.push({
    method,
    path: url.pathname,
    body: init.body ?? null,
    token: header['X-Intake-Token'] ?? null,
  });
  const handler = mockState.routes[`${method} ${url.pathname}`];
  const status = handler ? 200 : 404;
  const request = new Request(url, { method, headers: header });
  const response = handler
    ? await handler(request)
    : new Response(
        JSON.stringify({
          error: { code: 'not_found', message: 'not_found', request_id: 'req_safe', details: {} },
        }),
        { status, headers: { 'Content-Type': 'application/json' } },
      );
  const payload = await response.json();
  return {
    data: response.ok ? payload : undefined,
    error: response.ok ? undefined : payload,
    response,
  };
};

vi.mock('@geo/api-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@geo/api-client')>();
  const stubTransport = {
    GET: (path: string, init?: never) => invokeRaw('GET', path, init),
    POST: (path: string, init?: never) => invokeRaw('POST', path, init),
    PUT: (path: string, init?: never) => invokeRaw('PUT', path, init),
    PATCH: (path: string, init?: never) => invokeRaw('PATCH', path, init),
    DELETE: (path: string, init?: never) => invokeRaw('DELETE', path, init),
  };
  const rebind =
    <Args extends unknown[], Result>(
      fn: (...args: [...Args, object]) => Result,
    ): ((...args: Args) => Result) =>
    (...args: Args) =>
      fn(...args, stubTransport);
  return {
    ...actual,
    createIntakeFormCompetitor: rebind(actual.createIntakeFormCompetitor),
    createIntakeFormPromo: rebind(actual.createIntakeFormPromo),
    createIntakeFormTriggers: rebind(actual.createIntakeFormTriggers),
    deleteIntakeFormCompetitor: rebind(actual.deleteIntakeFormCompetitor),
    deleteIntakeFormPromo: rebind(actual.deleteIntakeFormPromo),
    deleteIntakeFormTrigger: rebind(actual.deleteIntakeFormTrigger),
    getIntakeFormContext: rebind(actual.getIntakeFormContext),
    getIntakeFormSiliconCandidates: rebind(actual.getIntakeFormSiliconCandidates),
    getIntakeFormSiliconTemplateQuestions: rebind(actual.getIntakeFormSiliconTemplateQuestions),
    listIntakeFormPromos: rebind(actual.listIntakeFormPromos),
    listIntakeFormTriggers: rebind(actual.listIntakeFormTriggers),
    patchIntakeFormBrand: rebind(actual.patchIntakeFormBrand),
    putIntakeFormProfile: rebind(actual.putIntakeFormProfile),
    runIntakeFormAiResearch: rebind(actual.runIntakeFormAiResearch),
    submitIntakeForm: rebind(actual.submitIntakeForm),
    suggestIntakeFormQuestions: rebind(actual.suggestIntakeFormQuestions),
  };
});

const { default: IntakeFormShell, readIntakeFormToken, splitLinesInput } = await import('./shell');

const jsonResponse = (status: number, body: JsonBody) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

const errorBody = (code: string) => ({
  error: { code, message: code, request_id: 'req_safe', details: {} },
});

const profileFixture = {
  project_pub_id: 'prj_safe',
  exists: true,
  prefilled: {},
  updated_at: '2026-08-01T01:02:03Z',
  contact_person: null,
  contact_info: null,
  website: null,
  wechat: null,
  douyin: null,
  social_media: null,
  audience_desc: null,
  business_license_code: null,
  selling_points: null,
  filler_name: null,
  ad_review_no: null,
  ad_review_authority: null,
  ad_review_expiry: null,
  review_category: null,
  pre_review_required: null,
  truth_confirmed: null,
  goals: [],
  audience_type: [],
  platforms: [],
  regions: [],
  trademarks: [],
  ad_review_doc_types: [],
  evidence_links: [],
  licenses: [],
};

const contextFixture = {
  form: {
    title: 'GEO 客户信息收集表（通用版）',
    note: '填写约需 10 分钟。',
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
              { value: 'A', label: 'A 类' },
              { value: 'none', label: '无需审查' },
            ],
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
            options: [{ value: 'brand', label: '品牌曝光' }],
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
            hint: null,
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
            items: ['确认所述属实', '确认卖点有据', '确认授权使用', '确认知悉用途', '确认愿意配合'],
          },
          {
            key: 'filler_name',
            label: '填表人',
            type: 'text',
            required: false,
            hint: null,
            options: [],
            items: [],
          },
        ],
      },
    ],
  },
  brand: { exists: true, pub_id: 'brd_safe', name: '测试品牌', website: null, aliases: [] },
  competitors: [],
  profile: profileFixture,
  invite: {
    pub_id: 'itv_safe',
    expires_at: '2026-08-09T00:00:00Z',
    submitted: false,
    submitted_at: null,
    ai_quota: 3,
    ai_used: 0,
    ai_remaining: 3,
  },
};

const readyRoutes = (): Record<string, RouteHandler> => ({
  'GET /api/v2/intake-form/context': () => jsonResponse(200, contextFixture),
  'GET /api/v2/intake-form/promos': () => jsonResponse(200, { items: [] }),
  'GET /api/v2/intake-form/trigger-questions': () => jsonResponse(200, { items: [] }),
});

const renderShell = (entry: string) =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <IntakeFormShell />
    </MemoryRouter>,
  );

beforeEach(() => {
  mockState.routes = readyRoutes();
  mockState.seen = [];
  window.location.hash = '#t=safe-invite';
});

afterEach(cleanup);

describe('readIntakeFormToken', () => {
  it('parses the invite credential from the URL fragment only', () => {
    expect(readIntakeFormToken('#t=abc.def-123_~')).toBe('abc.def-123_~');
    expect(readIntakeFormToken('#t=a%20b')).toBeNull();
    expect(readIntakeFormToken('#other=1')).toBeNull();
    expect(readIntakeFormToken('')).toBeNull();
    expect(readIntakeFormToken('#t=')).toBeNull();
  });

  it('splits batch question input into lines', () => {
    expect(splitLinesInput('问题一\n\n问题二\r\n问题三')).toEqual(['问题一', '问题二', '问题三']);
  });
});

describe('IntakeFormShell token gate', () => {
  it('requires the invite credential fragment', async () => {
    window.location.hash = '';
    renderShell('/');
    expect(await screen.findByText('缺少邀请凭证')).toBeTruthy();
    expect(mockState.seen).toHaveLength(0);
  });

  it('maps an expired invite to a non-disclosing state', async () => {
    mockState.routes['GET /api/v2/intake-form/context'] = () =>
      jsonResponse(403, errorBody('invite_token_expired'));
    renderShell('/');
    expect(await screen.findByText('邀请链接已过期')).toBeTruthy();
  });

  it('maps an invalid invite to a non-disclosing state', async () => {
    mockState.routes['GET /api/v2/intake-form/context'] = () =>
      jsonResponse(403, errorBody('invite_token_invalid'));
    renderShell('/');
    expect(await screen.findByText('邀请链接无效')).toBeTruthy();
  });

  it('sends the invite credential as a header, never in the URL', async () => {
    renderShell('/');
    expect(await screen.findByRole('heading', { name: '品牌信息' })).toBeTruthy();
    expect(mockState.seen.length).toBeGreaterThan(0);
    for (const seen of mockState.seen) {
      expect(seen.token).toBe('safe-invite');
      expect(seen.path.startsWith('/api/v2/intake-form/')).toBe(true);
    }
  });
});

describe('IntakeFormShell sections', () => {
  it('renders the schema-driven profile form', async () => {
    renderShell('/?section=profile');
    expect(await screen.findByRole('heading', { name: '客户信息表' })).toBeTruthy();
    expect(screen.getByLabelText(/联系人/)).toBeTruthy();
    expect(screen.getByText('A 类')).toBeTruthy();
    expect(screen.getByText('品牌曝光')).toBeTruthy();
    expect(screen.getByLabelText(/营业执照/)).toBeTruthy();
  });

  it('blocks saving when a field contains a client-secret-shaped value', async () => {
    renderShell('/?section=profile');
    expect(await screen.findByRole('heading', { name: '客户信息表' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/联系人/), {
      target: { value: 'SESSION=unsafe-value' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存本部分（宣传内容与目标）' }));
    expect(await screen.findByText(/不允许的字符序列/)).toBeTruthy();
    expect(mockState.seen.some((seen) => seen.method === 'PUT')).toBe(false);
  });
});

describe('IntakeFormShell submit gate', () => {
  it('keeps submit disabled until confirmations are checked, saved and a filler is named', async () => {
    mockState.routes['PUT /api/v2/intake-form/profile'] = () =>
      jsonResponse(200, { ...profileFixture, truth_confirmed: true, filler_name: '王测试' });
    mockState.routes['POST /api/v2/intake-form/submit'] = () =>
      jsonResponse(200, { submitted: true, submitted_at: '2026-08-03T02:03:04Z', replay: false });
    renderShell('/?section=submit');
    expect(await screen.findByRole('heading', { name: '确认并提交' })).toBeTruthy();

    const submitButton = screen.getByRole('button', { name: '提交信息表' });
    expect(submitButton).toHaveProperty('disabled', true);

    for (const checkbox of screen.getAllByRole('checkbox')) {
      fireEvent.click(checkbox);
    }
    fireEvent.change(screen.getByLabelText(/填表人/), { target: { value: '王测试' } });
    // 未先保存确认信息时仍不可提交
    expect(submitButton).toHaveProperty('disabled', true);

    fireEvent.click(screen.getByRole('button', { name: '保存确认信息' }));
    await waitFor(() => expect(submitButton).toHaveProperty('disabled', false));

    fireEvent.click(submitButton);
    expect(await screen.findByText(/本表已于 .* 提交/)).toBeTruthy();
    const submitRequest = mockState.seen.find((seen) => seen.path.endsWith('/submit'));
    expect(submitRequest?.token).toBe('safe-invite');
  });
});

describe('IntakeFormShell AI suggestions', () => {
  it('collects only checked AI-expansion candidates into trigger drafts', async () => {
    mockState.routes['POST /api/v2/intake-form/query-suggestions'] = () =>
      jsonResponse(200, {
        questions: [
          { question: '测试品牌值得选吗', core_word: '品牌', heat: 88 },
          { question: '测试品牌和竞品怎么比', core_word: '品牌', heat: 41 },
        ],
        candidate_only: true,
        ai_used: 1,
        ai_remaining: 2,
      });
    mockState.routes['POST /api/v2/intake-form/trigger-questions'] = () =>
      jsonResponse(201, {
        items: [
          {
            pub_id: 'trq_safe',
            text: '测试品牌值得选吗',
            status: 'draft',
            created_at: '2026-08-03T01:00:00Z',
          },
        ],
        skipped_duplicates: [],
      });
    renderShell('/?section=questions');
    expect(await screen.findByRole('heading', { name: '期望问法' })).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/核心词/), { target: { value: '品牌' } });
    fireEvent.click(screen.getByRole('button', { name: 'AI 扩写' }));
    expect(await screen.findByText('测试品牌值得选吗')).toBeTruthy();
    expect(screen.getByText('热度 88')).toBeTruthy();

    // 勾选第一条后收录
    const [firstCheckbox] = screen.getAllByRole('checkbox');
    fireEvent.click(firstCheckbox!);
    fireEvent.click(screen.getByRole('button', { name: /收录所选/ }));
    await waitFor(() => {
      const createRequest = mockState.seen.find(
        (seen) => seen.method === 'POST' && seen.path.endsWith('/trigger-questions'),
      );
      expect(createRequest).toBeTruthy();
      expect((createRequest!.body as { text: string }).text).toBe('测试品牌值得选吗');
    });
    expect(await screen.findByText(/已保存收录/)).toBeTruthy();
  });
});
