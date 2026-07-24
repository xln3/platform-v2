// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

import Shell from './shell';

describe('Report Studio', () => {
  beforeEach(() => {
    history.replaceState(null, '', '/platform/reports/');
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

  it('freezes facts, traces a KPI and distinguishes AI from human edits', async () => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: '冻结事实并创建 v0.8' }));
    expect(screen.getAllByText('事实已冻结').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: 'KPI Trace' }));
    await user.click(screen.getByRole('button', { name: /Top 3 占比/ }));
    expect(screen.getByText('ans_03 · rank 1')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '章节编辑' }));
    await user.click(screen.getByRole('button', { name: /模型差异分析/ }));
    expect(screen.getByText('AI 生成 · 未确认')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '接受草稿并标记人工确认' }));
    expect(screen.getByText('人工内容')).toBeTruthy();
  });

  it('binds accessible Konva evidence and pages through the report preview', async () => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: '证据编排' }));
    expect(screen.getByRole('img', { name: /品牌提及锚点/ })).toBeTruthy();
    expect(screen.getByTestId('konva-stage')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '绑定到“执行摘要”' }));
    expect(screen.getByText('绑定成功')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'PDF 预览' }));
    expect(screen.getByLabelText('报告预览第 1 页')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getByLabelText('报告预览第 2 页')).toBeTruthy();
  });

  it('enforces review gates before publication and records outcomes', async () => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: '冻结事实并创建 v0.8' }));
    await user.click(screen.getByRole('button', { name: /审核发布/ }));
    await user.click(screen.getByRole('button', { name: '提交审核' }));
    expect((screen.getByRole('button', { name: '批准发布' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    await user.click(screen.getByRole('button', { name: '标记已解决' }));
    await user.click(screen.getByRole('button', { name: '批准发布' }));
    await user.click(screen.getByRole('button', { name: '发布 v1.0' }));
    expect(screen.getByText('在线版与交付记录已生成，客户可见。')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '效果复盘' }));
    await user.click(screen.getByRole('button', { name: '开始执行' }));
    await user.click(screen.getByRole('button', { name: '记录复测效果' }));
    expect(screen.getByText('+6.2pp')).toBeTruthy();
  });
});
