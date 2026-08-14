// @vitest-environment jsdom

import { cleanup, render, screen, within } from '@testing-library/react';
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
                { key: 'leg-1', model: 'doubao', region: '北京', mode: 'deep_think' },
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
    expect(within(table).getByText('4遍')).toBeTruthy();
    expect(within(table).getByText('08-13 08:53')).toBeTruthy();
    expect(within(table).getByLabelText('尚无观测')).toBeTruthy();
  });

  it('formats invalid timestamps as an honest empty value', () => {
    expect(formatSamplingTime('not-a-date')).toBe('—');
  });
});
