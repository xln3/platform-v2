// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@xyflow/react', () => ({
  ReactFlow: () => <div data-testid="react-flow" />,
  Background: () => null,
  Controls: () => null,
}));

import Shell from './shell';

describe('Intelligence Web', () => {
  beforeEach(() => {
    history.replaceState(null, '', '/platform/intelligence/');
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              status: 'mock-ready',
              service: 'geo-platform-v2',
              version: 'contract-v1',
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('explains atomic claims and source independence', async () => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: 'Claim 矩阵' }));
    expect(screen.getByText(/独立一手来源不足 2 个/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '多源证据' }));
    await user.selectOptions(screen.getByLabelText('筛选同源簇'), 'C-07');
    expect(screen.getAllByText('同源传播')).toHaveLength(2);
  });

  it('provides a table equivalent for the propagation graph', async () => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: '传播关系' }));
    expect(screen.getByTestId('react-flow')).toBeTruthy();
    expect(screen.getByRole('table', { name: '传播图节点与关系' })).toBeTruthy();
    expect(screen.getByText('相似度 0.91')).toBeTruthy();
  });

  it('records a versioned verdict, appeal and evidence package', async () => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: /裁决与申诉/ }));
    await user.click(screen.getByRole('button', { name: '确认高风险表述' }));
    await user.type(screen.getByLabelText('申诉理由'), '新增登记材料需要重新复核');
    await user.click(screen.getByRole('button', { name: '提交申诉' }));
    expect(screen.getByText(/原裁决保持可追溯/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '记录二次复核' }));
    expect(screen.getAllByText('reviewed').length).toBeGreaterThan(0);
  });
});
