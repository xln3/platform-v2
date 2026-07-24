// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProductErrorBoundary, StatePanel } from './index';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('shared error recovery', () => {
  it('redacts a thrown secret-bearing error and recovers after an explicit retry', async () => {
    let fail = true;
    const diagnostics: unknown[] = [];
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    function UnstablePanel() {
      if (fail) {
        throw new Error(
          'render failed for 13800138000 with OTP 394820 at /var/browser/profile/customer-a',
        );
      }
      return <section aria-label="恢复内容">安全内容已恢复</section>;
    }

    render(
      <ProductErrorBoundary onDiagnostic={(value) => diagnostics.push(value)}>
        <UnstablePanel />
      </ProductErrorBoundary>,
    );

    expect(screen.getByRole('alert').textContent).toContain('此页面暂时无法显示');
    await waitFor(() => expect(diagnostics).toHaveLength(1));
    const serialized = JSON.stringify(diagnostics);
    for (const secret of ['13800138000', '394820', '/profile/customer-a']) {
      expect(serialized).not.toContain(secret);
    }
    expect(serialized).toContain('[redacted]');

    fail = false;
    fireEvent.click(screen.getByRole('button', { name: '重试页面' }));
    expect(screen.getByRole('region', { name: '恢复内容' }).textContent).toContain(
      '安全内容已恢复',
    );
  });

  it('keeps failed-region retry local and announces the transition', () => {
    const retry = vi.fn();
    render(<StatePanel state="failed" onRetry={retry} />);
    expect(screen.getByRole('alert').textContent).toContain('加载失败');
    fireEvent.click(screen.getByRole('button', { name: '重试此区域' }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
