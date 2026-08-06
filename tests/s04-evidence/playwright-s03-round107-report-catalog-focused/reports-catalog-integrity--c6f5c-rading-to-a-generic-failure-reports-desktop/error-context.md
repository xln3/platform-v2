# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: reports-catalog-integrity.spec.ts >> a forbidden report detail remains non-inferential instead of degrading to a generic failure
- Location: tests/e2e/reports-catalog-integrity.spec.ts:867:5

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('heading', { name: '无权查看' })
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByRole('heading', { name: '无权查看' })

```

```yaml
- link "跳到主要内容":
  - /url: "#main-content"
- complementary:
  - text: G GEO Platform Report Studio
  - navigation "Report Studio 主导航":
    - button "数据窗口"
    - button "KPI Trace"
    - button "章节编辑"
    - button "版本对比"
    - button "证据编排"
    - button "PDF 预览"
    - button "审核发布"
    - button "效果复盘"
  - status: ok
- banner:
  - button "租户 · egrity · 报告目录完整性项目 ⌄"
  - button "通知": ◌
  - text: 用
- main:
  - text: Report Studio
  - heading "报告工作室" [level=1]
  - paragraph: 冻结事实窗口，编辑可追溯章节，并通过审核门发布。
  - button "导出视图"
  - button "创建任务"
  - status:
    - strong: 无权查看
    - paragraph: 当前角色没有此资源权限，也不会披露资源是否存在。
```

```
Error: Browser runtime must retain literal zero console errors, page errors and failed requests. Raw messages and URLs are intentionally excluded from failure output.

expect(received).toEqual(expected) // deep equality

- Expected  - 1
+ Received  + 1

  Object {
-   "console-error": 0,
+   "console-error": 1,
    "page-error": 0,
    "request-failed": 0,
  }
```

# Test source

```ts
  1  | import { expect, test as base } from '@playwright/test';
  2  | import { collectBrowserRuntimeIssues, summarizeBrowserRuntimeIssues } from './runtime-guard';
  3  | 
  4  | export { expect };
  5  | export type { Page, Route } from '@playwright/test';
  6  | 
  7  | export const test = base.extend<{ browserRuntimeGuard: void }>({
  8  |   browserRuntimeGuard: [
  9  |     async ({ page }, use, testInfo) => {
  10 |       const collector = collectBrowserRuntimeIssues(page);
  11 |       try {
  12 |         await use();
  13 |       } finally {
  14 |         if (!page.isClosed()) await page.waitForTimeout(150);
  15 |         collector.stop();
  16 |         const observed = summarizeBrowserRuntimeIssues(collector.issues);
  17 |         const expected = {
  18 |           'console-error': 0,
  19 |           'page-error': 0,
  20 |           'request-failed': 0,
  21 |         };
  22 |         if (collector.issues.length) {
  23 |           await testInfo.attach('browser-runtime-guard-summary', {
  24 |             body: Buffer.from(JSON.stringify({ observed, expected })),
  25 |             contentType: 'application/json',
  26 |           });
  27 |         }
  28 |         expect(
  29 |           observed,
  30 |           'Browser runtime must retain literal zero console errors, page errors and failed requests. Raw messages and URLs are intentionally excluded from failure output.',
> 31 |         ).toEqual(expected);
     |           ^ Error: Browser runtime must retain literal zero console errors, page errors and failed requests. Raw messages and URLs are intentionally excluded from failure output.
  32 |       }
  33 |     },
  34 |     { auto: true },
  35 |   ],
  36 | });
  37 | 
```