// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Dialog, Pagination, StatePanel, Toast } from './index';

afterEach(cleanup);

describe('shared experience primitives', () => {
  it('keeps every non-ready data state semantically distinct', () => {
    const states = [
      ['loading', '正在加载', 'status'],
      ['empty', '暂无数据', 'status'],
      ['real-zero', '结果为 0', 'status'],
      ['insufficient', '样本不足', 'status'],
      ['failed', '加载失败', 'alert'],
      ['delayed', '数据延迟', 'status'],
      ['forbidden', '无权查看', 'status'],
    ] as const;

    const { rerender } = render(<StatePanel state="loading" />);
    for (const [state, title, role] of states) {
      rerender(<StatePanel state={state} />);
      expect(screen.getByRole(role).textContent).toContain(title);
    }
  });

  it('clamps pagination and never emits an out-of-range page', () => {
    const onPageChange = vi.fn();
    const { rerender } = render(
      <Pagination page={-8} pageCount={3} onPageChange={onPageChange} label="回答分页" />,
    );
    expect(screen.getByText('第 1 / 3 页')).toBeTruthy();
    expect((screen.getByRole('button', { name: '上一页' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(onPageChange).toHaveBeenLastCalledWith(2);

    rerender(<Pagination page={99} pageCount={0} onPageChange={onPageChange} label="回答分页" />);
    expect(screen.getByText('第 1 / 1 页')).toBeTruthy();
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('closes dialogs with Escape and distinguishes assertive failure toasts', () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <Dialog title="授权确认" closeLabel="关闭授权确认" onClose={onClose}>
        <button>确认授权</button>
      </Dialog>,
    );
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();

    rerender(<Toast tone="negative">保存失败</Toast>);
    expect(screen.getByRole('alert').textContent).toBe('保存失败');
    rerender(<Toast>保存成功</Toast>);
    expect(screen.getByRole('status').textContent).toBe('保存成功');
  });
});
