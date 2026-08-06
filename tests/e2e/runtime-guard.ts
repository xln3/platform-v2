import type { ConsoleMessage, Page, Request } from '@playwright/test';

export type BrowserRuntimeIssueKind = 'console-error' | 'page-error' | 'request-failed';
export type BrowserRuntimeIssue = { kind: BrowserRuntimeIssueKind };

type RuntimeGuardPage = Pick<Page, 'off' | 'on'>;

export function collectBrowserRuntimeIssues(page: RuntimeGuardPage): {
  issues: BrowserRuntimeIssue[];
  stop: () => void;
} {
  const issues: BrowserRuntimeIssue[] = [];
  const onConsole = (message: ConsoleMessage) => {
    if (message.type() === 'error') issues.push({ kind: 'console-error' });
  };
  const onPageError = () => issues.push({ kind: 'page-error' });
  const onRequestFailed = (_request: Request) => issues.push({ kind: 'request-failed' });
  page.on('console', onConsole);
  page.on('pageerror', onPageError);
  page.on('requestfailed', onRequestFailed);
  return {
    issues,
    stop: () => {
      page.off('console', onConsole);
      page.off('pageerror', onPageError);
      page.off('requestfailed', onRequestFailed);
    },
  };
}

export function summarizeBrowserRuntimeIssues(
  issues: readonly BrowserRuntimeIssue[],
): Record<BrowserRuntimeIssueKind, number> {
  const summary: Record<BrowserRuntimeIssueKind, number> = {
    'console-error': 0,
    'page-error': 0,
    'request-failed': 0,
  };
  for (const issue of issues) summary[issue.kind] += 1;
  return summary;
}
