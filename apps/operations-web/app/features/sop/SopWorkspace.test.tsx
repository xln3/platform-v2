// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getSopDashboard, loadSopStage, mutateSopStage, type SopDashboard } from '@geo/api-client';
import { SopWorkspace } from './SopWorkspace';

vi.mock('@geo/api-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@geo/api-client')>();
  return {
    ...original,
    getSopDashboard: vi.fn(),
    loadSopStage: vi.fn(),
    mutateSopStage: vi.fn(),
  };
});

const headers = {
  'X-Tenant-Id': 'tnt_sop_test',
  'X-Actor-Id': 'operator-test',
  'X-Actor-Role': 'operator',
};

const stepDefinitions = [
  ['project-definition', '阶段0', '项目定义'],
  ['query-set', '阶段1', '查询词全集'],
  ['baseline', '阶段2', '基线采集'],
  ['retrieval-review', '阶段3', '检索复盘'],
  ['evidence-ledger', '阶段4', '证据账本'],
  ['opportunities', '阶段5-6', '内容机会与信源'],
  ['writing', '阶段7', '文章写作'],
  ['pre-publish', '阶段8', '发布前验证'],
  ['publishing', '阶段9', '发布管理'],
  ['index-watch', '阶段10', '索引观察'],
  ['retest', '阶段11', '同题复测'],
  ['comparison', '阶段12-13', '对比归因'],
  ['experiments', '阶段14', '持续实验'],
  ['archive-log', '阶段15', '归档与工作日志'],
] as const;

const stepRows: SopDashboard['steps'] = stepDefinitions.map(([key, stage, name], index) => ({
  key,
  stage,
  name,
  status: index < 2 ? 'done' : 'empty',
  metrics: [{ label: 'records', value: index < 2 ? '1' : '0' }],
}));

const dashboard: SopDashboard = {
  project: {
    pubId: 'spr_test',
    name: 'Acme 信源闭环',
    brandStandardName: 'Acme',
    status: 'active',
    updatedAt: '2026-07-28T10:00:00Z',
  },
  steps: stepRows,
  articles: [
    {
      articlePubId: 'sar_test',
      title: '可信知识服务判断标准',
      status: 'in_review',
      versionCount: 1,
      publicationReady: false,
      hasPublication: false,
      maturityLevel: 'L0',
    },
  ],
};

beforeEach(() => {
  vi.mocked(getSopDashboard).mockResolvedValue({ kind: 'ready', data: dashboard });
  vi.mocked(loadSopStage).mockResolvedValue({
    kind: 'ready',
    data: {
      items: [
        {
          pubId: 'sqi_test',
          label: '用户如何选择可信知识服务？',
          status: 'frozen',
          detail: 'P0',
          createdAt: '2026-07-28T10:00:00Z',
        },
      ],
      metrics: [],
    },
  });
  vi.mocked(mutateSopStage).mockResolvedValue({
    kind: 'ready',
    data: { pubId: 'sop_result', relatedPubId: 'sop_related', message: '操作成功' },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('SopWorkspace', () => {
  it('renders all 14 monitoring steps and the article maturity monitor', async () => {
    render(<SopWorkspace projectPubId="spr_test" headers={headers} canWrite />);
    expect(await screen.findByText('Acme 信源闭环')).toBeTruthy();
    expect(screen.getAllByRole('button', { current: false }).length).toBeGreaterThan(10);
    expect(screen.getByText('L0')).toBeTruthy();
    expect(screen.getByText('2/14')).toBeTruthy();
    expect(await screen.findByText('用户如何选择可信知识服务？')).toBeTruthy();
  });

  it('submits the query-set freeze, writing version and comparison consoles', async () => {
    render(<SopWorkspace projectPubId="spr_test" headers={headers} canWrite />);
    await screen.findByText('Acme 信源闭环');

    fireEvent.click(screen.getByRole('button', { name: /查询词全集/ }));
    fireEvent.click(screen.getByRole('tab', { name: '操作台' }));
    fireEvent.change(screen.getByLabelText('用户查询句'), {
      target: { value: '企业如何选择可信知识服务？' },
    });
    fireEvent.click(screen.getByRole('button', { name: '创建并冻结查询集' }));
    await waitFor(() =>
      expect(mutateSopStage).toHaveBeenCalledWith(
        headers,
        expect.objectContaining({
          kind: 'query-set',
          projectPubId: 'spr_test',
          queryText: '企业如何选择可信知识服务？',
          priority: 'P0',
        }),
        expect.stringMatching(/^sop-/),
      ),
    );

    fireEvent.click(screen.getByRole('button', { name: /文章写作/ }));
    fireEvent.click(screen.getByRole('tab', { name: '操作台' }));
    fireEvent.change(screen.getByLabelText('文章标题'), {
      target: { value: '可信知识服务判断标准' },
    });
    fireEvent.change(screen.getByLabelText('文章正文'), {
      target: { value: '正文与可核验证据。' },
    });
    fireEvent.click(screen.getByRole('button', { name: '创建文章与版本' }));
    await waitFor(() =>
      expect(mutateSopStage).toHaveBeenCalledWith(
        headers,
        expect.objectContaining({
          kind: 'writing',
          title: '可信知识服务判断标准',
          body: '正文与可核验证据。',
        }),
        expect.stringMatching(/^sop-/),
      ),
    );

    fireEvent.click(screen.getByRole('button', { name: /对比归因/ }));
    fireEvent.click(screen.getByRole('tab', { name: '操作台' }));
    fireEvent.change(screen.getByLabelText('发布记录公开 ID'), {
      target: { value: 'spb_test' },
    });
    fireEvent.change(screen.getByLabelText('查询词公开 ID'), {
      target: { value: 'sqi_test' },
    });
    fireEvent.change(screen.getByLabelText('归因结论'), {
      target: { value: '文章被引用且品牌归属正确' },
    });
    fireEvent.click(screen.getByLabelText('品牌归属正确'));
    fireEvent.click(screen.getByRole('button', { name: '保存对比归因' }));
    await waitFor(() =>
      expect(mutateSopStage).toHaveBeenCalledWith(
        headers,
        expect.objectContaining({
          kind: 'comparison',
          publicationPubId: 'spb_test',
          queryItemPubId: 'sqi_test',
          attributionCorrect: true,
          conclusion: '文章被引用且品牌归属正确',
        }),
        expect.stringMatching(/^sop-/),
      ),
    );
  });

  it('keeps the operation console read-only for reviewer sessions', async () => {
    render(<SopWorkspace projectPubId="spr_test" headers={headers} canWrite={false} />);
    await screen.findByText('Acme 信源闭环');
    fireEvent.click(screen.getByRole('tab', { name: '操作台' }));
    expect(await screen.findByText('无权查看')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '保存项目定义' })).toBeNull();
  });
});
