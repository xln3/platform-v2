# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: intelligence-live.spec.ts >> investigation 403 fails closed without probing a case detail
- Location: tests/e2e/intelligence-live.spec.ts:681:5

# Error details

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
    - navigation "Intelligence Web 主导航" [ref=e5]:
      - button "案件" [ref=e6] [cursor=pointer]:
        - generic [ref=e7]: 案件
      - button "Claim 矩阵" [ref=e8] [cursor=pointer]:
        - generic [ref=e9]: Claim 矩阵
      - button "多源证据" [ref=e10] [cursor=pointer]:
        - generic [ref=e11]: 多源证据
      - button "传播关系" [ref=e12] [cursor=pointer]:
        - generic [ref=e13]: 传播关系
      - button "页面历史" [ref=e14] [cursor=pointer]:
        - generic [ref=e15]: 页面历史
      - button "模型准入" [ref=e16] [cursor=pointer]:
        - generic [ref=e17]: 模型准入
      - button "裁决与申诉" [ref=e18] [cursor=pointer]:
        - generic [ref=e19]: 裁决与申诉
      - button "证据包" [ref=e20] [cursor=pointer]:
        - generic [ref=e21]: 证据包
  - generic [ref=e22]:
    - banner [ref=e23]:
      - button "租户 · bidden · 暂无可用项目 ⌄" [ref=e24] [cursor=pointer]
      - generic [ref=e25]:
        - button "通知" [ref=e26] [cursor=pointer]: ◌
        - generic "用户 · bidden" [ref=e27]: 用
    - main [ref=e28]:
      - generic [ref=e29]:
        - generic [ref=e30]:
          - text: Intelligence Web
          - heading "证据调查台" [level=1] [ref=e31]
          - paragraph [ref=e32]: 从原子 Claim、多源证据与传播关系形成可解释的人工裁决。
        - generic [ref=e33]:
          - button "导出视图" [ref=e34] [cursor=pointer]
          - button "创建任务" [ref=e35] [cursor=pointer]
      - status [ref=e36]:
        - generic [ref=e38]:
          - strong [ref=e39]: 无权查看
          - paragraph [ref=e40]: 当前角色没有此资源权限，也不会披露资源是否存在。
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
  12 |         if (testInfo.project.name.startsWith('operations-')) {
  13 |           await page.route('**/api/v2/operations/lifecycle**', (route) =>
  14 |             route.fulfill({
  15 |               status: 200,
  16 |               contentType: 'application/json',
  17 |               body: JSON.stringify({
  18 |                 metrics: {
  19 |                   running_runs: 0,
  20 |                   project_count: 0,
  21 |                   pending_interventions: 0,
  22 |                   healthy_sessions: 0,
  23 |                   total_sessions: 0,
  24 |                   delayed_runs: 0,
  25 |                   p95_delay_seconds: null,
  26 |                 },
  27 |                 activity: [],
  28 |                 accounts: [],
  29 |                 interventions: [],
  30 |                 events: [],
  31 |                 projection: {
  32 |                   activity: { total: 0, shown: 0, truncated: false },
  33 |                   accounts: { total: 0, shown: 0, truncated: false },
  34 |                   interventions: { total: 0, shown: 0, truncated: false },
  35 |                   events: { total: 0, shown: 0, truncated: false },
  36 |                 },
  37 |               }),
  38 |             }),
  39 |           );
  40 |         }
  41 |         await use();
  42 |       } finally {
  43 |         if (!page.isClosed()) await page.waitForTimeout(150);
  44 |         collector.stop();
  45 |         const observed = summarizeBrowserRuntimeIssues(collector.issues);
  46 |         const expected = {
  47 |           'console-error': 0,
  48 |           'page-error': 0,
  49 |           'request-failed': 0,
  50 |         };
  51 |         if (collector.issues.length) {
  52 |           await testInfo.attach('browser-runtime-guard-summary', {
  53 |             body: Buffer.from(JSON.stringify({ observed, expected })),
  54 |             contentType: 'application/json',
  55 |           });
  56 |         }
  57 |         expect(
  58 |           observed,
  59 |           'Browser runtime must retain literal zero console errors, page errors and failed requests. Raw messages and URLs are intentionally excluded from failure output.',
> 60 |         ).toEqual(expected);
     |           ^ Error: Browser runtime must retain literal zero console errors, page errors and failed requests. Raw messages and URLs are intentionally excluded from failure output.
  61 |       }
  62 |     },
  63 |     { auto: true },
  64 |   ],
  65 | });
  66 | 
```