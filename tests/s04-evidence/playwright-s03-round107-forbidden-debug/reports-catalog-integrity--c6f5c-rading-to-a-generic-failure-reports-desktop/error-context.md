# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: reports-catalog-integrity.spec.ts >> a forbidden report detail remains non-inferential instead of degrading to a generic failure
- Location: tests/e2e/reports-catalog-integrity.spec.ts:877:5

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: 1
Received: 0

Call Log:
- Timeout 5000ms exceeded while waiting on the predicate
```

```
Error: Browser runtime must retain literal zero console errors, page errors and failed requests. Raw messages and URLs are intentionally excluded from failure output.

expect(received).toEqual(expected) // deep equality

- Expected  - 2
+ Received  + 2

  Object {
-   "console-error": 0,
+   "console-error": 1,
    "page-error": 0,
-   "request-failed": 0,
+   "request-failed": 1,
  }
```

# Page snapshot

```yaml
- generic [ref=e2]:
  - link "跳到主要内容" [ref=e3] [cursor=pointer]:
    - /url: "#main-content"
  - complementary [ref=e4]:
    - generic "GEO Platform V2" [ref=e5]:
      - generic [ref=e6]: G
      - generic [ref=e7]:
        - text: GEO
        - text: Platform
    - generic [ref=e8]: Report Studio
    - navigation "Report Studio 主导航" [ref=e9]:
      - button "数据窗口" [ref=e10] [cursor=pointer]:
        - generic [ref=e11]: 数据窗口
      - button "KPI Trace" [ref=e12] [cursor=pointer]:
        - generic [ref=e13]: KPI Trace
      - button "章节编辑" [ref=e14] [cursor=pointer]:
        - generic [ref=e15]: 章节编辑
      - button "版本对比" [ref=e16] [cursor=pointer]:
        - generic [ref=e17]: 版本对比
      - button "证据编排" [ref=e18] [cursor=pointer]:
        - generic [ref=e19]: 证据编排
      - button "PDF 预览" [ref=e20] [cursor=pointer]:
        - generic [ref=e21]: PDF 预览
      - button "审核发布" [ref=e22] [cursor=pointer]:
        - generic [ref=e23]: 审核发布
      - button "效果复盘" [ref=e24] [cursor=pointer]:
        - generic [ref=e25]: 效果复盘
    - status [ref=e26]: ok
  - generic [ref=e28]:
    - banner [ref=e29]:
      - button "租户 · egrity · 报告目录完整性项目 ⌄" [ref=e30] [cursor=pointer]
      - generic [ref=e31]:
        - button "通知" [ref=e32] [cursor=pointer]: ◌
        - generic "用户 · egrity" [ref=e33]: 用
    - main [ref=e34]:
      - generic [ref=e35]:
        - generic [ref=e36]:
          - text: Report Studio
          - heading "报告工作室" [level=1] [ref=e37]
          - paragraph [ref=e38]: 冻结事实窗口，编辑可追溯章节，并通过审核门发布。
        - generic [ref=e39]:
          - button "导出视图" [ref=e40] [cursor=pointer]
          - button "创建任务" [ref=e41] [cursor=pointer]
      - alert [ref=e42]:
        - generic [ref=e44]:
          - strong [ref=e45]: 加载失败
          - paragraph [ref=e46]: 局部请求失败，其他区域仍可使用。
        - button "重试此区域" [ref=e47] [cursor=pointer]
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