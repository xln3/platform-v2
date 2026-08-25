// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { formatSamplingTime, SamplingProgressPanel } from './SamplingProgressPanel';

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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('SamplingProgressPanel', () => {
  it('shows at most four question rows and keeps full-cohort summaries while paging', async () => {
    const requestedPages: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const requestedPage = url.searchParams.get('page') ?? '1';
        requestedPages.push(requestedPage);
        const allRows = Array.from({ length: 5 }, (_, index) => ({
          appendix: null,
          group: `G0${index + 1}`,
          group_name: `问题组 ${index + 1}`,
          expression: '原词',
          query_text: `问题 ${index + 1}`,
          cells: [],
        }));
        const page = Number(requestedPage);
        return new Response(
          JSON.stringify({
            project_pub_id: 'prj_test',
            config_revision_start: 2,
            config_revision_end: 2,
            columns: [
              { key: 'leg-1', model: 'doubao', region: '北京', mode: 'normal', modes: ['normal'] },
            ],
            rows: allRows.slice((page - 1) * 4, page * 4),
            page: { page, page_size: 4, total_count: 5, total_pages: 2 },
            observed_cells: 0,
            total_cells: 5,
            answer_count: 0,
            latest_capture_time: null,
            live_runs: 0,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }),
    );

    render(<SamplingProgressPanel session={session} projectPubId="prj_test" />);
    const table = await screen.findByRole('table', { name: '问题采样进度总览' });
    expect(within(table).getAllByRole('row')).toHaveLength(5);
    expect(screen.getByText('5 问')).toBeTruthy();
    expect(screen.getByText('已观测 0/5 格')).toBeTruthy();
    expect(screen.queryByText('问题 5')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('问题 5')).toBeTruthy();
    expect(
      within(screen.getByRole('table', { name: '问题采样进度总览' })).getAllByRole('row'),
    ).toHaveLength(2);
    expect(screen.getByText('5 问')).toBeTruthy();
    expect(requestedPages).toEqual(['1', '2']);
  });

  it('renders the overview matrix with repeat counts and latest Shanghai time', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              project_pub_id: 'prj_test',
              config_revision_start: 33,
              config_revision_end: 46,
              columns: [
                {
                  key: 'leg-1',
                  model: 'doubao',
                  region: '北京',
                  mode: 'deep_think',
                  modes: ['deep_think', 'normal'],
                },
                {
                  key: 'leg-2',
                  model: 'deepseek',
                  region: '上海',
                  mode: 'deep_think',
                  modes: ['deep_think'],
                },
              ],
              rows: [
                {
                  appendix: '附录二',
                  group: 'G01',
                  group_name: '高校双非资产排查可以找什么公司做',
                  expression: '原词/优化句',
                  query_text: '高校双非资产排查可以找什么公司做',
                  cells: [
                    {
                      column_key: 'leg-1',
                      completed_samples: 4,
                      latest_capture_time: '2026-08-13T00:53:00Z',
                      answer_pub_ids: ['ans_4', 'ans_3', 'ans_2', 'ans_1'],
                      mode_breakdown: [
                        {
                          mode: 'deep_think',
                          completed_samples: 3,
                          latest_capture_time: '2026-08-13T00:53:00Z',
                          answer_pub_ids: ['ans_4', 'ans_3', 'ans_2'],
                        },
                        {
                          mode: 'normal',
                          completed_samples: 1,
                          latest_capture_time: '2026-08-12T23:53:00Z',
                          answer_pub_ids: ['ans_1'],
                        },
                      ],
                    },
                  ],
                },
              ],
              page: { page: 1, page_size: 4, total_count: 1, total_pages: 1 },
              observed_cells: 1,
              total_cells: 2,
              answer_count: 4,
              latest_capture_time: '2026-08-13T00:53:00Z',
              live_runs: 0,
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          ),
      ),
    );

    render(<SamplingProgressPanel session={session} projectPubId="prj_test" />);

    const table = await screen.findByRole('table', { name: '问题采样进度总览' });
    expect(screen.getByText('配置 v33–v46')).toBeTruthy();
    expect(screen.getByText('已观测 1/2 格')).toBeTruthy();
    expect(screen.getByText('共 4 条有效回答')).toBeTruthy();
    expect(screen.getByText(/每格仅汇总合格且非降级的有效回答/)).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: '豆包×北京' })).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'DeepSeek×上海' })).toBeTruthy();
    expect(within(table).getByText('深度思考 3遍 · 快速模式 1遍')).toBeTruthy();
    expect(within(table).getByText('4遍')).toBeTruthy();
    expect(within(table).getByText('08-13 08:53')).toBeTruthy();
    expect(within(table).getByLabelText('尚无观测')).toBeTruthy();
  });

  it('formats invalid timestamps as an honest empty value', () => {
    expect(formatSamplingTime('not-a-date')).toBe('—');
  });

  it('labels genuinely distinct formal modes while leaving fallback modes inside one leg', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              project_pub_id: 'prj_test',
              config_revision_start: 1,
              config_revision_end: 1,
              columns: [
                {
                  key: 'leg-1',
                  model: 'doubao',
                  region: '北京',
                  mode: 'normal',
                  modes: ['normal'],
                },
                {
                  key: 'leg-2',
                  model: 'doubao',
                  region: '北京',
                  mode: 'deep_think',
                  modes: ['deep_think'],
                },
              ],
              rows: [
                {
                  appendix: null,
                  group: 'G01',
                  group_name: '目标问题',
                  expression: '原词/优化句',
                  query_text: '目标问题',
                  cells: [],
                },
              ],
              page: { page: 1, page_size: 4, total_count: 1, total_pages: 1 },
              observed_cells: 0,
              total_cells: 2,
              answer_count: 0,
              latest_capture_time: null,
              live_runs: 0,
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          ),
      ),
    );

    const { unmount } = render(<SamplingProgressPanel session={session} projectPubId="prj_test" />);
    const table = await screen.findByRole('table', { name: '问题采样进度总览' });
    const headers = within(table).getAllByRole('columnheader');

    expect(within(headers[4]!).getByText('快速模式')).toBeTruthy();
    expect(within(headers[5]!).getByText('深度思考')).toBeTruthy();
    unmount();
  });

  it('opens the exact answers behind a repeat count and reuses the full answer detail', async () => {
    const requestedAnswerIds: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
          'https://operations.example.test',
        );
        if (url.pathname.endsWith('/analytics/sampling-progress')) {
          return new Response(
            JSON.stringify({
              project_pub_id: 'prj_test',
              config_revision_start: 46,
              config_revision_end: 46,
              columns: [
                {
                  key: 'leg-1',
                  model: 'doubao',
                  region: '北京',
                  mode: 'deep_think',
                  modes: ['deep_think', 'normal'],
                },
              ],
              rows: [
                {
                  appendix: null,
                  group: 'G01',
                  group_name: '目标问题组',
                  expression: '原词/优化句',
                  query_text: '目标品牌怎么样？',
                  cells: [
                    {
                      column_key: 'leg-1',
                      completed_samples: 4,
                      latest_capture_time: '2026-08-17T03:00:00Z',
                      // Deliberately grouped by mode rather than globally by time.
                      answer_pub_ids: [
                        'ans_z_deep_new',
                        'ans_deep_old',
                        'ans_a_normal_mid',
                        'ans_b_normal_mid',
                      ],
                      mode_breakdown: [
                        {
                          mode: 'deep_think',
                          completed_samples: 2,
                          latest_capture_time: '2026-08-17T03:00:00Z',
                          answer_pub_ids: ['ans_z_deep_new', 'ans_deep_old'],
                        },
                        {
                          mode: 'normal',
                          completed_samples: 2,
                          latest_capture_time: '2026-08-17T02:00:00Z',
                          answer_pub_ids: ['ans_a_normal_mid', 'ans_b_normal_mid'],
                        },
                      ],
                    },
                  ],
                },
              ],
              page: { page: 1, page_size: 4, total_count: 1, total_pages: 1 },
              observed_cells: 1,
              total_cells: 1,
              answer_count: 4,
              latest_capture_time: '2026-08-17T03:00:00Z',
              live_runs: 0,
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (url.pathname.endsWith('/analytics/answers')) {
          const answerPubId = url.searchParams.get('answer_pub_id');
          expect(url.searchParams.get('project_pub_id')).toBe('prj_test');
          expect(url.searchParams.get('limit')).toBe('1');
          expect(answerPubId).toMatch(/^ans_(z_deep_new|deep_old|a_normal_mid|b_normal_mid)$/);
          requestedAnswerIds.push(answerPubId!);
          const answers = {
            ans_z_deep_new: {
              mode: 'deep_think',
              captureTime: '2026-08-17T03:00:00Z',
              responseText: '专家最新回答',
            },
            ans_deep_old: {
              mode: 'deep_think',
              captureTime: '2026-08-17T01:00:00Z',
              responseText: '专家较早回答',
            },
            ans_a_normal_mid: {
              mode: 'normal',
              captureTime: '2026-08-17T02:00:00Z',
              responseText: '同秒快速回答 A',
            },
            ans_b_normal_mid: {
              mode: 'normal',
              captureTime: '2026-08-17T02:00:00Z',
              responseText: '同秒快速回答 B',
            },
          } as const;
          const answer = answers[answerPubId! as keyof typeof answers];
          return new Response(
            JSON.stringify({
              data: [
                {
                  pub_id: answerPubId,
                  project_pub_id: 'prj_test',
                  run_pub_id: `run_${answerPubId}`,
                  config_version_pub_id: 'cfg_46',
                  query_pub_id: 'qry_target',
                  query_text: '目标品牌怎么样？',
                  response_text: answer.responseText,
                  model: 'doubao',
                  region: '北京',
                  mode: answer.mode,
                  eligible: true,
                  degraded: false,
                  capture_time: answer.captureTime,
                  mentioned: true,
                  rank: 1,
                  sentiment: 'neutral',
                  recommendation_state: null,
                  citation_count: 1,
                },
              ],
              page: { next_cursor: null, has_more: false },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (url.pathname.endsWith('/relations') || url.pathname.endsWith('/trace')) {
          return new Response(JSON.stringify({ detail: { code: 'task_not_found' } }), {
            status: 404,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        throw new Error(`unexpected request: ${url.pathname}`);
      }),
    );

    render(<SamplingProgressPanel session={session} projectPubId="prj_test" />);
    fireEvent.click(
      await screen.findByRole('button', {
        name: '目标品牌怎么样？，豆包×北京，4遍，深度思考 2遍 · 快速模式 2遍，查看具体回答',
      }),
    );

    expect(await screen.findByRole('dialog', { name: '采样具体回答' })).toBeTruthy();
    expect(screen.getByText('豆包×北京 · 有效合计 4遍 · 深度思考 2遍 · 快速模式 2遍')).toBeTruthy();
    const answersTable = await screen.findByRole('table', { name: '该采样位具体回答' });
    expect(requestedAnswerIds).toEqual([
      'ans_z_deep_new',
      'ans_deep_old',
      'ans_a_normal_mid',
      'ans_b_normal_mid',
    ]);
    expect(within(answersTable).getAllByText('目标品牌怎么样？')).toHaveLength(4);
    const answerRows = within(answersTable).getAllByRole('row').slice(1);
    expect(
      answerRows.map((row) => row.querySelector('td[data-label="模式"]')?.textContent),
    ).toEqual(['deep_think', 'normal', 'normal', 'deep_think']);

    // Same timestamp: pub_id DESC makes B stable before A.
    fireEvent.click(answerRows[1]!);
    expect(await screen.findByRole('dialog', { name: '问答详情' })).toBeTruthy();
    expect(screen.getByText('同秒快速回答 B')).toBeTruthy();
  });
});
