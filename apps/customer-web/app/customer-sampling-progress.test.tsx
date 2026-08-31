// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { ExperienceProvider } from '@geo/design-system';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CustomerSamplingProgressEntry } from './customer-sampling-progress';

const apiSpies = vi.hoisted(() => ({
  samplingProgress: vi.fn(),
}));

vi.mock('@geo/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@geo/api-client')>()),
  getCustomerSamplingProgress: apiSpies.samplingProgress,
}));

vi.mock('@geo/auth', () => ({
  getValidatedIdentityHeaders: () => ({
    'X-Tenant-Id': 'tnt_customer',
    'X-Actor-Id': 'usr_customer',
    'X-Actor-Role': 'customer',
  }),
}));

describe('CustomerSamplingProgressEntry', () => {
  beforeEach(() => {
    apiSpies.samplingProgress.mockReset();
    apiSpies.samplingProgress.mockResolvedValue({
      kind: 'ready',
      data: {
        projectPubId: 'prj_customer',
        configRevisionStart: 7,
        configRevisionEnd: 9,
        columns: [
          { key: 'leg-1', model: 'doubao', region: '北京', mode: 'normal', modes: ['normal'] },
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
            groupName: '品牌认知',
            expression: '原词',
            queryText: '客户问题一',
            cells: [
              {
                columnKey: 'leg-1',
                completedSamples: 2,
                latestCaptureTime: '2026-08-28T02:00:00Z',
                modeBreakdown: [
                  {
                    mode: 'normal',
                    completedSamples: 2,
                    latestCaptureTime: '2026-08-28T02:00:00Z',
                  },
                ],
              },
            ],
          },
          {
            appendix: '附录二',
            group: 'G02',
            groupName: '服务选择',
            expression: '优化句',
            queryText: '客户问题二',
            cells: [],
          },
        ],
        observedCells: 1,
        totalCells: 4,
        answerCount: 2,
        latestCaptureTime: '2026-08-28T02:00:00Z',
        liveRuns: 0,
      },
    });
  });

  afterEach(cleanup);

  it('loads the customer-safe full sampling matrix only after the customer opens it', async () => {
    render(
      <ExperienceProvider
        value={{
          tenantPubId: 'tnt_customer',
          tenantLabel: '客户租户',
          projectPubId: 'prj_customer',
          projectLabel: '客户项目',
          userPubId: 'usr_customer',
          userLabel: '客户用户',
          roles: ['customer'],
          source: 'live',
        }}
      >
        <CustomerSamplingProgressEntry />
      </ExperienceProvider>,
    );

    expect(apiSpies.samplingProgress).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '查看采样进度' }));

    const dialog = await screen.findByRole('dialog', { name: '采样进度' });
    expect(apiSpies.samplingProgress).toHaveBeenCalledWith(
      expect.objectContaining({ 'X-Actor-Role': 'customer' }),
      'prj_customer',
    );
    expect(within(dialog).getByText('配置 v7–v9')).toBeTruthy();
    expect(within(dialog).getByText('已观测 1/4 格')).toBeTruthy();
    const table = within(dialog).getByRole('table', { name: '客户采样进度全景表' });
    expect(within(table).getAllByRole('row')).toHaveLength(3);
    expect(within(table).getByText('客户问题二')).toBeTruthy();
    expect(within(dialog).getByLabelText('客户采样进度横竖滚动区域')).toBeTruthy();
  });
});
