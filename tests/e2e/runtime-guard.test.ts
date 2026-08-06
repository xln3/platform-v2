import { EventEmitter } from 'node:events';
import { describe, expect, it } from 'vitest';
import { collectBrowserRuntimeIssues, summarizeBrowserRuntimeIssues } from './runtime-guard';

class RuntimePageProbe extends EventEmitter {
  off(event: string, listener: (...args: never[]) => void): this {
    return super.off(event, listener);
  }

  on(event: string, listener: (...args: never[]) => void): this {
    return super.on(event, listener);
  }
}

describe('browser runtime guard', () => {
  it('collects every failure channel while retaining only safe kinds and counts', () => {
    const page = new RuntimePageProbe();
    const collector = collectBrowserRuntimeIssues(page as never);
    page.emit('console', {
      type: () => 'error',
      text: () => 'Bearer runtime-guard-secret-canary',
    });
    page.emit('console', { type: () => 'warning', text: () => 'safe warning' });
    page.emit('pageerror', new Error('OTP 824911'));
    page.emit('requestfailed', {
      url: () => 'https://example.invalid/?access_token=runtime-guard-secret-canary',
    });

    expect(collector.issues).toEqual([
      { kind: 'console-error' },
      { kind: 'page-error' },
      { kind: 'request-failed' },
    ]);
    const summary = summarizeBrowserRuntimeIssues(collector.issues);
    expect(summary).toEqual({
      'console-error': 1,
      'page-error': 1,
      'request-failed': 1,
    });
    expect(JSON.stringify({ issues: collector.issues, summary })).not.toMatch(
      /Bearer|824911|access_token|runtime-guard-secret-canary/,
    );

    collector.stop();
    page.emit('pageerror', new Error('detached listener'));
    expect(collector.issues).toHaveLength(3);
  });
});
