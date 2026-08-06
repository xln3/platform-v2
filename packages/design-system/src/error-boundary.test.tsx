// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ProductErrorBoundary,
  safeClientDiagnosticEventName,
  safeReactRootErrorHandlers,
  StatePanel,
} from './index';

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
    expect(diagnostics[0]).toMatchObject({
      kind: 'react_error_boundary',
      errorName: 'Error',
      hasCause: false,
    });
    expect((diagnostics[0] as { componentFrames: number }).componentFrames).toBeGreaterThan(0);

    fail = false;
    fireEvent.click(screen.getByRole('button', { name: '重试页面' }));
    expect(screen.getByRole('region', { name: '恢复内容' }).textContent).toContain(
      '安全内容已恢复',
    );
  });

  it('replaces every React 19 root error default with a count-only ephemeral diagnostic', () => {
    const diagnostics: unknown[] = [];
    const listener = (event: Event) => {
      diagnostics.push((event as CustomEvent<unknown>).detail);
    };
    const originalUrl = window.location.href;
    const originalLocalStorage = JSON.stringify(localStorage);
    const originalSessionStorage = JSON.stringify(sessionStorage);
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const caughtError = new Error('Bearer react-root-canary OTP 824911', {
      cause: { profile_path: '/secret/browser/profile/react-root-canary' },
    });
    caughtError.name = 'Cookie=session-react-root-canary';
    window.addEventListener(safeClientDiagnosticEventName, listener);
    try {
      safeReactRootErrorHandlers.onCaughtError(caughtError, {
        componentStack: '\n at OTP824911\n at /secret/browser/profile/react-root-canary',
      });
      safeReactRootErrorHandlers.onUncaughtError(
        new TypeError('proxy_password=react-root-canary'),
        {
          componentStack: '\n at BearerReactRootCanary',
        },
      );
      safeReactRootErrorHandlers.onRecoverableError('13800138000', {
        componentStack: null,
      });
    } finally {
      window.removeEventListener(safeClientDiagnosticEventName, listener);
    }

    expect(diagnostics).toEqual([
      {
        kind: 'react_caught_error',
        errorName: 'Error',
        componentFrames: 2,
        hasCause: true,
      },
      {
        kind: 'react_uncaught_error',
        errorName: 'TypeError',
        componentFrames: 1,
        hasCause: false,
      },
      {
        kind: 'react_recoverable_error',
        errorName: 'Error',
        componentFrames: 0,
        hasCause: false,
      },
    ]);
    expect(diagnostics.every(Object.isFrozen)).toBe(true);
    expect(JSON.stringify(diagnostics)).not.toMatch(
      /Bearer|Cookie|session|token|OTP|824911|proxy_password|profile|13800138000|react-root-canary/i,
    );
    expect(consoleError).not.toHaveBeenCalled();
    expect(window.location.href).toBe(originalUrl);
    expect(JSON.stringify(localStorage)).toBe(originalLocalStorage);
    expect(JSON.stringify(sessionStorage)).toBe(originalSessionStorage);
  });

  it('keeps failed-region retry local and announces the transition', () => {
    const retry = vi.fn();
    render(<StatePanel state="failed" onRetry={retry} />);
    expect(screen.getByRole('alert').textContent).toContain('加载失败');
    fireEvent.click(screen.getByRole('button', { name: '重试此区域' }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
