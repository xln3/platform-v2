// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Project } from '../api';
import { ComparisonPanel } from './ComparisonPanel';

const session = {
  tenantId: 'tnt_test',
  actorId: 'usr_test',
  role: 'operator' as const,
  headers: {
    'X-Tenant-Id': 'tnt_test',
    'X-Actor-Id': 'subject_test',
    'X-Actor-Role': 'operator',
  },
};

const project: Project = {
  pub_id: 'prj_test',
  name: '测试项目',
  state: 'active',
  updated_at: '2026-08-10T00:00:00Z',
  brandrank_domain: 'cybersecurity',
};

const RUNS = [
  {
    pub_id: 'run_baseline_1',
    project_pub_id: 'prj_test',
    state: 'completed',
    total_tasks: 4,
    completed_tasks: 4,
    failed_tasks: 0,
    paused: false,
    created_at: '2026-08-09T01:00:00Z',
    updated_at: '2026-08-09T02:00:00Z',
  },
  {
    pub_id: 'run_optimized_1',
    project_pub_id: 'prj_test',
    state: 'completed',
    total_tasks: 4,
    completed_tasks: 4,
    failed_tasks: 0,
    paused: false,
    created_at: '2026-08-10T01:00:00Z',
    updated_at: '2026-08-10T02:00:00Z',
  },
];

function comparisonEntity(overrides: Record<string, unknown> = {}) {
  return {
    pub_id: 'cmp_new',
    project_pub_id: 'prj_test',
    name: 'FAQ 优化前后',
    baseline_run_pub_ids: ['run_baseline_1'],
    optimized_run_pub_ids: ['run_optimized_1'],
    note: null,
    created_by: 'usr_test',
    created_at: '2026-08-10T03:00:00Z',
    ...overrides,
  };
}

function comparisonDetail(overrides: Record<string, unknown> = {}) {
  return {
    ...comparisonEntity(),
    result: {
      status: 'ok',
      insufficient_reasons: [],
      domain: 'cybersecurity',
      target_brand: '盛邦安全',
      coverage: {
        before_answers: 10,
        before_with_extract: 10,
        after_answers: 12,
        after_with_extract: 11,
        before_truncated: false,
        after_truncated: false,
      },
      aggregate: { metrics: [] },
      questions: [],
      unpaired: { baseline_only: [], optimized_only: [] },
      ...overrides,
    },
  };
}

type RecordedCall = { url: string; method: string; body: unknown };

type StubOptions = {
  listItems?: unknown[];
  created?: unknown;
  createStatus?: number;
  createError?: unknown;
  detail?: unknown;
  detailStatus?: number;
  detailError?: unknown;
};

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('ComparisonPanel', () => {
  const calls: RecordedCall[] = [];

  function stubFetch(options: StubOptions = {}) {
    calls.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        // servicesPost 以 fetch(url, init) 形式调用：method/body 在 init 上；
        // openapi-fetch 则直接传 Request。两种形态都要还原。
        const url = input instanceof Request ? input.url : String(input);
        const method = input instanceof Request ? input.method : (init?.method ?? 'GET');
        let body: unknown = null;
        if (method === 'POST') {
          if (input instanceof Request) body = await input.clone().json();
          else if (typeof init?.body === 'string') body = JSON.parse(init.body);
        }
        calls.push({ url, method, body });
        if (url.includes('/api/v2/collection/runs')) return json(RUNS);
        if (/\/api\/v2\/analytics\/comparisons\/[^/]+/.test(url)) {
          if (options.detailStatus && options.detailStatus >= 400)
            return json(options.detailError, options.detailStatus);
          return json(options.detail ?? comparisonDetail());
        }
        if (url.includes('/api/v2/analytics/comparisons')) {
          if (method === 'POST') {
            if (options.createStatus && options.createStatus >= 400)
              return json(options.createError, options.createStatus);
            return json(options.created ?? comparisonEntity(), 201);
          }
          return json({ items: options.listItems ?? [] });
        }
        return json({});
      }),
    );
  }

  beforeEach(() => {
    stubFetch();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  function renderPanel() {
    render(<ComparisonPanel session={session} project={project} />);
  }

  function armFieldset(name: string) {
    return within(screen.getByRole('group', { name }));
  }

  async function pickArms() {
    const baselineArm = armFieldset('基线 run（优化前，多选）');
    await baselineArm.findByText(/run_baseline_1 · completed/);
    fireEvent.click(baselineArm.getByLabelText(/run_baseline_1/));
    fireEvent.click(armFieldset('优化后 run（多选）').getByLabelText(/run_optimized_1/));
  }

  it('creates a comparison with the contracted body, refreshes the list and expands the new detail', async () => {
    stubFetch({ listItems: [comparisonEntity()] });
    renderPanel();
    await pickArms();
    fireEvent.change(screen.getByLabelText('对比名称'), { target: { value: 'FAQ 优化前后' } });
    fireEvent.change(screen.getByLabelText(/备注/), { target: { value: '首轮优化' } });
    fireEvent.click(screen.getByRole('button', { name: '创建对比' }));

    // 创建成功后自动展开新实体详情。
    await screen.findByText(/覆盖：优化前答案 10 条（含抽取 10）/);

    const creates = calls.filter(
      (call) => call.url.includes('/api/v2/analytics/comparisons') && call.method === 'POST',
    );
    expect(creates).toHaveLength(1);
    expect(creates[0]?.body).toEqual({
      project_pub_id: 'prj_test',
      name: 'FAQ 优化前后',
      baseline_run_pub_ids: ['run_baseline_1'],
      optimized_run_pub_ids: ['run_optimized_1'],
      note: '首轮优化',
    });

    // 列表刷新：初次加载 + 创建后刷新各一次。
    const lists = calls.filter(
      (call) =>
        call.method === 'GET' &&
        call.url.includes('/api/v2/analytics/comparisons') &&
        !/\/api\/v2\/analytics\/comparisons\/[^/]+/.test(call.url),
    );
    expect(lists.length).toBeGreaterThanOrEqual(2);
    expect(calls.some((call) => call.url.includes('/api/v2/analytics/comparisons/cmp_new'))).toBe(
      true,
    );
  });

  it('renders aggregate deltas and per-question rows with honest null placeholders', async () => {
    const longQuery = '题'.repeat(70);
    stubFetch({
      listItems: [comparisonEntity()],
      detail: comparisonDetail({
        aggregate: {
          metrics: [
            {
              metric: 'before_after_metric',
              value: 12.5,
              unit: 'percent',
              extra: {
                metric_name: 'mention_rate',
                before: 40.0,
                after: 52.5,
                denominators: { before_n: 10, after_n: 12 },
              },
            },
            {
              metric: 'before_after_metric',
              value: -0.75,
              unit: 'rank',
              extra: {
                metric_name: 'avg_rank',
                before: 3.5,
                after: 2.75,
                denominators: { before_n: 10, after_n: 12 },
              },
            },
            {
              metric: 'before_after_metric',
              value: null,
              unit: 'percent',
              extra: {
                metric_name: 'top1',
                before: null,
                after: null,
                denominators: { before_n: 10, after_n: 12 },
                before_of_mentions: true,
                after_of_mentions: true,
              },
            },
          ],
        },
        questions: [
          {
            query_text: '国内网络空间资产搜索引擎哪家强',
            status: 'ok',
            insufficient_reasons: [],
            before: { mention_rate: { value: 50 }, avg_rank: { value: 2.5 } },
            after: { mention_rate: { value: 70 }, avg_rank: { value: 1.75 } },
            delta: { mention_rate: 20.0, avg_rank: -0.75, top1: null, top3: 5.0, top5: null },
          },
          {
            query_text: longQuery,
            status: 'insufficient',
            insufficient_reasons: ['before_no_answers'],
            before: null,
            after: null,
            delta: { mention_rate: null, avg_rank: null, top1: null, top3: null, top5: null },
          },
        ],
        unpaired: { baseline_only: ['仅基线题目甲'], optimized_only: [] },
      }),
    });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: '展开' }));

    // 聚合：差值带正负号；null 一律「—」，绝不渲染成 0。
    await screen.findByText('品牌提及率');
    expect(screen.getByText('40.0%')).toBeTruthy();
    expect(screen.getByText('52.5%')).toBeTruthy();
    expect(screen.getByText('+12.5%')).toBeTruthy();
    expect(screen.getByText('-0.75')).toBeTruthy();
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(3);

    // 逐题：ok 行数值 + 差值；insufficient 行置灰显示「数据不足」。
    expect(screen.getByText('国内网络空间资产搜索引擎哪家强')).toBeTruthy();
    expect(screen.getByText('50.0%')).toBeTruthy();
    expect(screen.getByText('+20.0%')).toBeTruthy();
    expect(screen.getByText('2.50')).toBeTruthy();
    expect(screen.getByText(`${'题'.repeat(60)}…`)).toBeTruthy();
    expect(screen.getByText('数据不足')).toBeTruthy();

    // 未配对折叠区。
    expect(screen.getByText(/未配对题目（仅基线 1 \/ 仅优化后 0）/)).toBeTruthy();
    expect(screen.getByText('仅基线题目甲')).toBeTruthy();
  });

  it('shows the insufficient badge and reasons for a thin comparison', async () => {
    stubFetch({
      listItems: [comparisonEntity()],
      detail: comparisonDetail({
        status: 'insufficient',
        insufficient_reasons: ['before_no_answers', 'target_brand_unset'],
      }),
    });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: '展开' }));
    await screen.findByText('数据不足');
    expect(screen.getByText('基线臂无答案')).toBeTruthy();
    expect(screen.getByText('目标品牌未设置')).toBeTruthy();
  });

  it('surfaces the unknown run ids when creation is rejected', async () => {
    stubFetch({
      createStatus: 400,
      createError: {
        error: {
          code: 'unknown_run_pub_id',
          message: 'unknown run pub id',
          request_id: 'req_test',
          details: { unknown_run_pub_ids: ['run_ghost_9'] },
        },
      },
    });
    renderPanel();
    await pickArms();
    fireEvent.change(screen.getByLabelText('对比名称'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: '创建对比' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('run_ghost_9'));
  });

  it('rejects overlapping arms locally without posting', async () => {
    renderPanel();
    await armFieldset('基线 run（优化前，多选）').findByText(/run_baseline_1 · completed/);
    fireEvent.click(armFieldset('基线 run（优化前，多选）').getByLabelText(/run_baseline_1/));
    fireEvent.click(armFieldset('优化后 run（多选）').getByLabelText(/run_baseline_1/));
    fireEvent.change(screen.getByLabelText('对比名称'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: '创建对比' }));
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain(
        '基线与优化后 run 不得重叠：run_baseline_1',
      ),
    );
    expect(
      calls.filter(
        (call) => call.url.includes('/api/v2/analytics/comparisons') && call.method === 'POST',
      ),
    ).toHaveLength(0);
  });
});
