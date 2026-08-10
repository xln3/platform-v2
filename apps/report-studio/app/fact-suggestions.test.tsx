// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router';

vi.mock('react-konva', () => ({
  Stage: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="konva-stage">{children}</div>
  ),
  Layer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Rect: () => <span />,
  Text: () => <span />,
}));
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument: () => ({ promise: new Promise(() => undefined), destroy: async () => undefined }),
}));

const getReportFactSuggestionsMock = vi.fn();
const createReportMock = vi.fn();
vi.mock('@geo/api-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@geo/api-client')>();
  return {
    ...actual,
    getReportFactSuggestions: (...args: unknown[]) => getReportFactSuggestionsMock(...args),
    createReport: (...args: unknown[]) => createReportMock(...args),
  };
});
vi.mock('@geo/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@geo/auth')>();
  return {
    ...actual,
    getValidatedIdentityHeaders: () => ({
      'X-Tenant-Id': 'tnt_test',
      'X-Actor-Id': 'usr_test',
      'X-Actor-Role': 'operator',
    }),
  };
});

import { CreateReportWorkspace } from './shell';
import {
  buildExtendedFactPayload,
  buildSuggestionFactPayload,
  computeSuggestionWindowDays,
  projectExtendedFactSections,
  validCompareWindows,
  type ExtendedFactRow,
  type SuggestionEdit,
} from './fact-suggestions';
import type { ReportFactSuggestionRow, ReportFactSuggestions } from '@geo/api-client';

const WINDOW = { start: '2026-08-03T00:00:00+00:00', end: '2026-08-09T12:00:00+00:00' };

const suggestionRow = (
  metric: ReportFactSuggestionRow['metric'],
  value: number | null,
  extra: ReportFactSuggestionRow['extra'] = null,
): ReportFactSuggestionRow => ({
  metric,
  value,
  unit: metric === 'rank_distribution' ? 'rank' : 'percent',
  numerator: value === null ? 0 : 1,
  denominator: 2,
  dimensions: { platform: 'doubao', region: '北京', query: '保险公司推荐' },
  source: 'system_computed',
  method: 'brandrank-llm-v1',
  domain: 'insurance',
  window: WINDOW,
  extra,
});

const suggestionsData = (
  overrides: Partial<ReportFactSuggestions> = {},
): ReportFactSuggestions => ({
  projectPubId: 'prj_test',
  domain: 'insurance',
  windowDays: 7,
  window: WINDOW,
  targetBrand: '中意人寿',
  competitors: ['中国平安'],
  insufficient: false,
  insufficientReasons: [],
  truncated: false,
  coverage: { nAnswers: 2, nWithExtract: 2, nGroups: 1 },
  factRows: [
    suggestionRow('brand_appearance_rate', 100),
    suggestionRow('top1_appearance_rate', 50, { of_mentions: 100 }),
  ],
  ...overrides,
});

const renderWorkspace = (onCreated = vi.fn()) =>
  render(
    <MemoryRouter>
      <CreateReportWorkspace projectPubId="prj_test" canAuthor={true} onCreated={onCreated} />
    </MemoryRouter>,
  );

describe('报告事实预填（分析链路 → fact_rows 草稿 → 人工确认提交）', () => {
  beforeEach(() => {
    getReportFactSuggestionsMock.mockReset();
    createReportMock.mockReset();
    createReportMock.mockResolvedValue({
      kind: 'ready',
      data: {
        reportPubId: 'rpt_test',
        reportVersionPubId: 'rptv_test',
        state: 'draft',
        factSnapshotHash: 'a'.repeat(64),
      },
    });
  });
  afterEach(() => cleanup());

  it('window_days 由报告窗口换算（含首尾日、越界拒绝）', () => {
    expect(computeSuggestionWindowDays('2026-08-03', '2026-08-09')).toBe(7);
    expect(computeSuggestionWindowDays('2026-08-09', '2026-08-09')).toBe(1);
    expect(computeSuggestionWindowDays('2025-01-01', '2026-08-09')).toBe(366);
    expect(computeSuggestionWindowDays('2026-08-09', '2026-08-03')).toBeNull();
    expect(computeSuggestionWindowDays('08-03', '2026-08-09')).toBeNull();
  });

  it('草稿行映射：人工改值留痕 value_edited/original_value，零提及允许空值', () => {
    const row = suggestionRow('brand_appearance_rate', 100);
    const untouched = buildSuggestionFactPayload(row, { removed: false, value: '100' });
    expect(untouched.invalid).toBe(false);
    expect(untouched.payload).toMatchObject({
      metric: 'brand_appearance_rate',
      value: 100,
      source: 'system_computed',
      method: 'brandrank-llm-v1',
      human_confirmed: true,
    });
    expect('value_edited' in untouched.payload).toBe(false);
    const edited = buildSuggestionFactPayload(row, { removed: false, value: '88.5' });
    expect(edited.invalid).toBe(false);
    expect(edited.payload).toMatchObject({
      value: 88.5,
      value_edited: true,
      original_value: 100,
    });
    const bad: SuggestionEdit = { removed: false, value: 'abc' };
    expect(buildSuggestionFactPayload(row, bad).invalid).toBe(true);
    const nullRow = suggestionRow('rank_distribution', null, { best_rank: null, ranks: [] });
    expect(buildSuggestionFactPayload(nullRow, { removed: false, value: '' })).toMatchObject({
      invalid: false,
      payload: { value: null },
    });
    expect(buildSuggestionFactPayload(row, { removed: false, value: '' }).invalid).toBe(true);
  });

  it('预填按钮拉取建议并按窗口换算 window_days', async () => {
    const user = userEvent.setup();
    getReportFactSuggestionsMock.mockResolvedValue({ kind: 'ready', data: suggestionsData() });
    renderWorkspace();
    await user.click(screen.getByRole('button', { name: '从分析链路预填事实' }));
    expect(await screen.findByText('品牌提及率')).toBeTruthy();
    expect(screen.getByText('Top1 出现率')).toBeTruthy();
    expect(screen.getByText(/2 条有效回答 · 2 条已抽取 · 1 组/)).toBeTruthy();
    expect(getReportFactSuggestionsMock).toHaveBeenCalledOnce();
    const [projectPubId, windowDays, headers] = getReportFactSuggestionsMock.mock.calls[0]!;
    expect(projectPubId).toBe('prj_test');
    expect(windowDays).toBe(7);
    expect(headers).toMatchObject({ 'X-Tenant-Id': 'tnt_test' });
  });

  it('可编辑列表：移除一行、修订一行，确认后随既有表单提交', async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    getReportFactSuggestionsMock.mockResolvedValue({ kind: 'ready', data: suggestionsData() });
    renderWorkspace(onCreated);
    await user.click(screen.getByRole('button', { name: '从分析链路预填事实' }));
    await screen.findByText('品牌提及率');
    // 移除第二行（Top1），修订第一行数值 100 → 88.5
    await user.click(screen.getByLabelText('包含第 2 行事实'));
    const valueInput = screen.getByLabelText('第 1 行事实数值');
    await user.clear(valueInput);
    await user.type(valueInput, '88.5');
    await user.click(screen.getByRole('button', { name: '创建首份报告' }));
    await waitFor(() => expect(createReportMock).toHaveBeenCalledOnce());
    const [body] = createReportMock.mock.calls[0]!;
    expect(body.fact_rows).toHaveLength(2); // 既有手工行 + 仅被接受的草稿行
    expect(body.fact_rows[0]).toMatchObject({ metric: '监测结论', source: 'manual_confirmed' });
    expect(body.fact_rows[1]).toMatchObject({
      metric: 'brand_appearance_rate',
      value: 88.5,
      value_edited: true,
      original_value: 100,
      numerator: 1,
      denominator: 2,
      source: 'system_computed',
      method: 'brandrank-llm-v1',
      human_confirmed: true,
      dimensions: { platform: 'doubao', region: '北京', query: '保险公司推荐' },
      window: WINDOW,
    });
    expect(onCreated).toHaveBeenCalledOnce();
  });

  it('domain_unset：给出可操作提示，不渲染草稿行', async () => {
    const user = userEvent.setup();
    getReportFactSuggestionsMock.mockResolvedValue({ kind: 'domain_unset' });
    renderWorkspace();
    await user.click(screen.getByRole('button', { name: '从分析链路预填事实' }));
    expect(await screen.findByText(/尚未配置品牌分析域/)).toBeTruthy();
    expect(screen.queryByLabelText('事实建议清单')).toBeNull();
  });

  it('insufficient：诚实空结构（原因披露、零编造）', async () => {
    const user = userEvent.setup();
    getReportFactSuggestionsMock.mockResolvedValue({
      kind: 'ready',
      data: suggestionsData({
        insufficient: true,
        insufficientReasons: ['no_extraction_coverage'],
        factRows: [],
      }),
    });
    renderWorkspace();
    await user.click(screen.getByRole('button', { name: '从分析链路预填事实' }));
    expect(await screen.findByText(/窗口内回答尚无品牌抽取覆盖/)).toBeTruthy();
    expect(screen.queryByLabelText('事实建议清单')).toBeNull();
  });

  it('非法修订值阻止提交并提示', async () => {
    const user = userEvent.setup();
    getReportFactSuggestionsMock.mockResolvedValue({ kind: 'ready', data: suggestionsData() });
    renderWorkspace();
    await user.click(screen.getByRole('button', { name: '从分析链路预填事实' }));
    await screen.findByText('品牌提及率');
    const valueInput = screen.getByLabelText('第 1 行事实数值');
    await user.clear(valueInput);
    await user.type(valueInput, 'abc');
    expect(await screen.findByText(/无法解析为有限数字/)).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: '创建首份报告' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(createReportMock).not.toHaveBeenCalled();
  });
});

// ── 扩展组（报价单服务 2/3/4）：W3 风险核查 / W2 官网能效 / 优化前后对比 ──────
const extendedRow = (
  metric: string,
  value: number | null,
  unit: string,
  extra: Record<string, unknown> = {},
): ExtendedFactRow => ({
  metric,
  value,
  unit,
  numerator: 1,
  denominator: 2,
  dimensions: { platform: 'doubao', region: '', query: '' },
  source: 'system_computed',
  method: 'w3-disparagement-v1',
  domain: 'cybersecurity',
  window: WINDOW,
  extra,
});

const extendedResponseBody = {
  w3_disparagement: {
    status: 'ok',
    insufficient_reasons: [],
    n_judgments: 3,
    n_disparagement: 2,
    n_undirected: 0,
    judgments_truncated: false,
    cases_truncated: false,
    fact_check_available: true,
    fact_rows: [
      {
        ...extendedRow('disparagement_rate', 50, 'percent', {
          direction: 'smear_on_own',
        }),
        numerator: 1,
        denominator: 2,
      },
      {
        ...extendedRow('disparagement_case', null, 'case', {
          judgment_pub_id: 'jdg_1',
          direction: 'smear_on_own',
          subject_brand: '奇安信',
          target_brand: '盛邦安全',
          evidence_quote: '盛邦安全的产品存在严重漏洞',
          confidence: 0.9,
          judge_method: 'llm_judge',
          source_url: 'https://src.example.com/p',
          answer_ref: 'col_x',
          fact_check: {
            verdict: 'supported',
            summary: '官网公告可证实',
            source_url: 'https://www.example.com/a',
          },
        }),
        denominator: 2,
      },
      // 坏行（缺 metric）：逐行容错丢弃，不炸整组
      { value: 1 },
    ],
  },
  w2_site_audit: {
    status: 'ok',
    insufficient_reasons: [],
    own_site_host: 'www.webray.com.cn',
    documents_total: 40,
    own_site_documents: 1,
    suggestions_available: true,
    suggestion_batch_pub_id: 'sab_1',
    suggestions_truncated: false,
    fact_rows: [
      {
        ...extendedRow('own_site_citation_share', 2.5, 'percent', {
          own_site_host: 'www.webray.com.cn',
        }),
        method: 'w2-site-audit-v1',
        numerator: 1,
        denominator: 40,
      },
      {
        ...extendedRow('own_site_adoption_rate', 80, 'percent', {
          own_site_host: 'www.webray.com.cn',
        }),
        method: 'w2-site-audit-v1',
        numerator: 8,
        denominator: 10,
      },
      {
        ...extendedRow('site_audit_suggestion', null, 'suggestion', {
          category: 'citability',
          severity: 'high',
          title: '缺少结构化数据',
          detail: '产品页未提供 JSON-LD',
          batch_pub_id: 'sab_1',
        }),
        method: 'w2-site-audit-v1',
        denominator: 1,
      },
    ],
  },
  before_after: {
    status: 'ok',
    insufficient_reasons: [],
    window: { start: '2026-07-01', end: '2026-08-07' },
    windows: {
      before_start: '2026-07-01',
      before_end: '2026-07-07',
      after_start: '2026-08-01',
      after_end: '2026-08-07',
    },
    coverage: {
      before_answers: 2,
      before_with_extract: 2,
      after_answers: 2,
      after_with_extract: 2,
      before_truncated: false,
      after_truncated: false,
    },
    fact_rows: [
      {
        ...extendedRow('before_after_metric', 50, 'percent', {
          metric_name: 'mention_rate',
          before: 50,
          after: 100,
          denominators: { before_n: 2, after_n: 2 },
          windows: {
            before_start: '2026-07-01',
            before_end: '2026-07-07',
            after_start: '2026-08-01',
            after_end: '2026-08-07',
          },
        }),
        method: 'brandrank-llm-v1',
        numerator: 2,
        denominator: 2,
      },
    ],
  },
};

const stubExtendedFetch = () => {
  const fetchMock = vi.fn(async (_url: string) => ({
    ok: true,
    json: async () => extendedResponseBody,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
};

describe('扩展组（W3/W2/前后对比）', () => {
  beforeEach(() => {
    getReportFactSuggestionsMock.mockReset();
    createReportMock.mockReset();
    createReportMock.mockResolvedValue({
      kind: 'ready',
      data: {
        reportPubId: 'rpt_test',
        reportVersionPubId: 'rptv_test',
        state: 'draft',
        factSnapshotHash: 'a'.repeat(64),
      },
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it('对比窗参数：四参齐全且不倒置才有效', () => {
    const valid = {
      beforeStart: '2026-07-01',
      beforeEnd: '2026-07-07',
      afterStart: '2026-08-01',
      afterEnd: '2026-08-07',
    };
    expect(validCompareWindows(valid)).toEqual(valid);
    expect(validCompareWindows({ ...valid, afterStart: '' })).toBeNull();
    expect(validCompareWindows({ ...valid, beforeEnd: '2026-06-01' })).toBeNull();
    expect(validCompareWindows({ ...valid, beforeStart: '2026/07/01' })).toBeNull();
  });

  it('投影容错：缺键 section → null；坏行丢弃不炸整组', () => {
    const projected = projectExtendedFactSections(extendedResponseBody);
    expect(projected.w3?.factRows).toHaveLength(2); // 坏行（缺 metric）被丢弃
    expect(projected.w3?.meta.n_judgments).toBe(3);
    expect(projected.w2?.factRows).toHaveLength(3);
    expect(projected.beforeAfter?.factRows).toHaveLength(1);
    const empty = projectExtendedFactSections({});
    expect(empty.w3).toBeNull();
    expect(empty.w2).toBeNull();
    expect(empty.beforeAfter).toBeNull();
    expect(projectExtendedFactSections('junk').w3).toBeNull();
  });

  it('扩展行 payload：数值行改值留痕；案例行固定 null 且带 extra', () => {
    const rate = extendedRow('disparagement_rate', 50, 'percent', {
      direction: 'smear_on_own',
    });
    const edited = buildExtendedFactPayload(rate, { removed: false, value: '55' });
    expect(edited.invalid).toBe(false);
    expect(edited.payload).toMatchObject({
      metric: 'disparagement_rate',
      value: 55,
      value_edited: true,
      original_value: 50,
      human_confirmed: true,
    });
    const caseRow = extendedRow('disparagement_case', null, 'case', {
      judgment_pub_id: 'jdg_1',
    });
    const casePayload = buildExtendedFactPayload(caseRow, { removed: false, value: '9' });
    expect(casePayload.invalid).toBe(false);
    expect(casePayload.payload.value).toBeNull(); // 案例行无数值，忽略输入
    expect(casePayload.payload.extra).toMatchObject({ judgment_pub_id: 'jdg_1' });
    expect(buildExtendedFactPayload(rate, { removed: false, value: 'abc' }).invalid).toBe(true);
  });

  it('面板渲染扩展组：案例引文+事实核查徽标+建议徽标+前后对比三列，随表单提交', async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    const fetchMock = stubExtendedFetch();
    getReportFactSuggestionsMock.mockResolvedValue({ kind: 'ready', data: suggestionsData() });
    renderWorkspace(onCreated);
    // 先填对比窗四个日期，再拉取 → 扩展请求带 before/after 参数
    await user.type(screen.getByLabelText('优化前开始日期'), '2026-07-01');
    await user.type(screen.getByLabelText('优化前结束日期'), '2026-07-07');
    await user.type(screen.getByLabelText('优化后开始日期'), '2026-08-01');
    await user.type(screen.getByLabelText('优化后结束日期'), '2026-08-07');
    await user.click(screen.getByRole('button', { name: '从分析链路预填事实' }));

    expect(await screen.findByText('内容风险核查（拉踩/抹黑）')).toBeTruthy();
    expect(screen.getByText('拉踩判定占比 · 第三方/竞品抹黑己方')).toBeTruthy();
    expect(screen.getByText(/盛邦安全的产品存在严重漏洞/)).toBeTruthy();
    expect(screen.getByText('事实核查：属实')).toBeTruthy();
    expect(screen.getByText(/核查结论：官网公告可证实/)).toBeTruthy();
    expect(screen.getByText('官网引用能效')).toBeTruthy();
    expect(screen.getByText('官网引用率')).toBeTruthy();
    expect(screen.getByText('官网内容采纳率')).toBeTruthy();
    expect(screen.getByText('严重程度：高')).toBeTruthy();
    expect(screen.getByText('缺少结构化数据')).toBeTruthy();
    expect(screen.getByText('优化前后对比')).toBeTruthy();
    expect(screen.getByText(/优化前 50% → 优化后 100% · 差值 \+50 百分点/)).toBeTruthy();

    expect(fetchMock).toHaveBeenCalledOnce();
    const url = fetchMock.mock.calls[0]![0];
    expect(url).toContain('/api/v2/projects/prj_test/report-fact-suggestions');
    expect(url).toContain('before_start=2026-07-01');
    expect(url).toContain('after_end=2026-08-07');

    // 全部扩展行默认勾选 → 随创建请求一并冻结（含案例行 extra 证据链）
    await user.click(screen.getByRole('button', { name: '创建首份报告' }));
    await waitFor(() => expect(createReportMock).toHaveBeenCalledOnce());
    const [body] = createReportMock.mock.calls[0]!;
    const metrics = body.fact_rows.map((row: { metric: string }) => row.metric);
    expect(metrics).toContain('disparagement_rate');
    expect(metrics).toContain('disparagement_case');
    expect(metrics).toContain('own_site_citation_share');
    expect(metrics).toContain('site_audit_suggestion');
    expect(metrics).toContain('before_after_metric');
    const caseRow = body.fact_rows.find(
      (row: { metric: string }) => row.metric === 'disparagement_case',
    );
    expect(caseRow.value).toBeNull();
    expect(caseRow.extra.fact_check).toMatchObject({ verdict: 'supported' });
    const diffRow = body.fact_rows.find(
      (row: { metric: string }) => row.metric === 'before_after_metric',
    );
    expect(diffRow.value).toBe(50);
    expect(diffRow.extra.before).toBe(50);
    expect(diffRow.extra.after).toBe(100);
    expect(onCreated).toHaveBeenCalledOnce();
  });

  it('扩展拉取失败只丢扩展组，主草稿照常', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, json: async () => ({}) })),
    );
    getReportFactSuggestionsMock.mockResolvedValue({ kind: 'ready', data: suggestionsData() });
    renderWorkspace();
    await user.click(screen.getByRole('button', { name: '从分析链路预填事实' }));
    expect(await screen.findByText('品牌提及率')).toBeTruthy();
    expect(screen.queryByText('内容风险核查（拉踩/抹黑）')).toBeNull();
  });
});
