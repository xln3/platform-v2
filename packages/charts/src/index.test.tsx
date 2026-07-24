// @vitest-environment jsdom

import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { GeoBarChart } from './index';

const setOption = vi.fn();
const resize = vi.fn();
const dispose = vi.fn();

vi.mock('echarts/core', () => ({
  init: () => ({ setOption, resize, dispose }),
  use: vi.fn(),
}));
vi.mock('echarts/charts', () => ({ BarChart: {} }));
vi.mock('echarts/components', () => ({ GridComponent: {}, TooltipComponent: {} }));
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('GeoBarChart', () => {
  it('always exposes exact values and data-quality states through an accessible table', async () => {
    const { unmount } = render(
      <GeoBarChart
        title="模型覆盖率"
        valueSuffix="%"
        data={[
          { label: 'DeepSeek', value: 72, state: 'ready' },
          { label: '豆包', value: 0, state: 'real-zero' },
          { label: 'Kimi', value: null, state: 'insufficient' },
          { label: '元宝', value: null, state: 'failed' },
        ]}
      />,
    );

    const table = screen.getByRole('table', { name: '模型覆盖率（图表的可访问数据表）' });
    expect(within(table).getByRole('row', { name: 'DeepSeek 72% ready' })).toBeTruthy();
    expect(within(table).getByRole('row', { name: '豆包 0% real-zero' })).toBeTruthy();
    expect(within(table).getByRole('row', { name: 'Kimi — insufficient' })).toBeTruthy();
    expect(within(table).getByRole('row', { name: '元宝 — failed' })).toBeTruthy();
    expect((await screen.findByRole('status')).textContent).toBe('模型覆盖率图表已渲染');

    window.dispatchEvent(new Event('resize'));
    expect(resize).toHaveBeenCalledOnce();
    unmount();
    expect(dispose).toHaveBeenCalledOnce();
  });
});
