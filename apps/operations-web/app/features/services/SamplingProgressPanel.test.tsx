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
                { key: 'leg-1', model: 'doubao', region: '北京', mode: 'normal' },
                { key: 'leg-2', model: 'deepseek', region: '上海', mode: 'deep_think' },
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
                    },
                  ],
                },
              ],
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
    expect(within(table).getByText('豆包×北京')).toBeTruthy();
    expect(within(table).getByText('DeepSeek×上海')).toBeTruthy();
    expect(within(table).getByText('快速模式')).toBeTruthy();
    expect(within(table).getByText('深度思考')).toBeTruthy();
    expect(within(table).getByText('4遍')).toBeTruthy();
    expect(within(table).getByText('08-13 08:53')).toBeTruthy();
    expect(within(table).getByLabelText('尚无观测')).toBeTruthy();
  });

  it('formats invalid timestamps as an honest empty value', () => {
    expect(formatSamplingTime('not-a-date')).toBe('—');
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
              columns: [{ key: 'leg-1', model: 'doubao', region: '北京', mode: 'normal' }],
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
                      completed_samples: 2,
                      latest_capture_time: '2026-08-17T02:00:00Z',
                      answer_pub_ids: ['ans_newest', 'ans_oldest'],
                    },
                  ],
                },
              ],
              observed_cells: 1,
              total_cells: 1,
              answer_count: 2,
              latest_capture_time: '2026-08-17T02:00:00Z',
              live_runs: 0,
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (url.pathname.endsWith('/analytics/answers')) {
          const answerPubId = url.searchParams.get('answer_pub_id');
          expect(url.searchParams.get('project_pub_id')).toBe('prj_test');
          expect(url.searchParams.get('limit')).toBe('1');
          expect(answerPubId).toMatch(/^ans_(newest|oldest)$/);
          requestedAnswerIds.push(answerPubId!);
          const newest = answerPubId === 'ans_newest';
          return new Response(
            JSON.stringify({
              data: [
                {
                  pub_id: answerPubId,
                  project_pub_id: 'prj_test',
                  run_pub_id: newest ? 'run_newest' : 'run_oldest',
                  config_version_pub_id: 'cfg_46',
                  query_pub_id: 'qry_target',
                  query_text: '目标品牌怎么样？',
                  response_text: newest ? '最新一遍完整回答' : '较早一遍完整回答',
                  model: 'doubao',
                  region: '北京',
                  mode: 'normal',
                  eligible: true,
                  degraded: false,
                  capture_time: newest ? '2026-08-17T02:00:00Z' : '2026-08-17T01:00:00Z',
                  mentioned: true,
                  rank: newest ? 1 : 2,
                  sentiment: 'neutral',
                  recommendation_state: null,
                  citation_count: newest ? 2 : 1,
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
        name: '目标品牌怎么样？，豆包×北京，2遍，查看具体回答',
      }),
    );

    expect(await screen.findByRole('dialog', { name: '采样具体回答' })).toBeTruthy();
    const answersTable = await screen.findByRole('table', { name: '该采样位具体回答' });
    expect(requestedAnswerIds).toEqual(['ans_newest', 'ans_oldest']);
    expect(within(answersTable).getAllByText('目标品牌怎么样？')).toHaveLength(2);
    expect(within(answersTable).getAllByText('豆包')).toHaveLength(2);

    fireEvent.click(within(answersTable).getAllByRole('row')[1]!);
    expect(await screen.findByRole('dialog', { name: '问答详情' })).toBeTruthy();
    expect(screen.getByText('最新一遍完整回答')).toBeTruthy();
  });
});
