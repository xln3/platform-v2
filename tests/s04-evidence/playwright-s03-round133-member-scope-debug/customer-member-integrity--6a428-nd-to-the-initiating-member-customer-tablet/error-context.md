# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: customer-member-integrity.spec.ts >> tenant member writes stay serialized and bound to the initiating member
- Location: tests/e2e/customer-member-integrity.spec.ts:4:5

# Error details

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

# Page snapshot

```yaml
- generic [ref=e2]:
  - link "跳到主要内容" [ref=e3] [cursor=pointer]:
    - /url: "#main-content"
  - complementary [ref=e4]:
    - generic "GEO Platform V2" [ref=e5]:
      - generic [ref=e6]: G
    - navigation "Customer Web 主导航" [ref=e7]:
      - button "首页" [ref=e8] [cursor=pointer]: •
      - button "资料" [ref=e9] [cursor=pointer]: •
      - button "品牌产品" [ref=e10] [cursor=pointer]: •
      - button "问题目标" [ref=e11] [cursor=pointer]: •
      - button "监测表现" [ref=e12] [cursor=pointer]: •
      - button "回答证据" [ref=e13] [cursor=pointer]: •
      - button "报告" [ref=e14] [cursor=pointer]: •
      - button "成员" [ref=e15] [cursor=pointer]: •
      - button "平台账号" [ref=e16] [cursor=pointer]: •
  - generic [ref=e17]:
    - banner [ref=e18]:
      - button "租户 · egrity · 成员完整性项目 ⌄" [ref=e19] [cursor=pointer]
      - generic [ref=e20]:
        - button "通知" [ref=e21] [cursor=pointer]: ◌
        - generic "用户 · _admin" [ref=e22]: 用
    - main [ref=e23]:
      - generic [ref=e24]:
        - generic [ref=e25]:
          - text: Customer Web
          - heading "客户工作台" [level=1] [ref=e26]
          - paragraph [ref=e27]: 从项目资料到监测、证据、报告与平台账号授权的安全协作入口。
        - generic [ref=e28]:
          - button "导出视图" [ref=e29] [cursor=pointer]
          - button "创建任务" [ref=e30] [cursor=pointer]
      - generic [ref=e31]:
        - generic [ref=e32]:
          - heading "项目成员" [level=2] [ref=e33]
          - paragraph [ref=e34]: 客户管理员可以管理本租户成员；邮箱在列表和审计中保持掩码。
          - generic [ref=e35]:
            - article [ref=e36]:
              - generic [ref=e37]: 租
              - generic [ref=e38]:
                - strong [ref=e39]: 租户管理员
                - generic [ref=e40]: a***@example.test
              - generic [ref=e41]: 客户管理员
              - button "管理 租户管理员" [ref=e42] [cursor=pointer]: 管理
            - article [ref=e43]:
              - generic [ref=e44]: 成
              - generic [ref=e45]:
                - strong [ref=e46]: 成员甲
                - generic [ref=e47]: a***@example.test
              - generic [ref=e48]: 客户成员
              - button "管理 成员甲" [active] [ref=e49] [cursor=pointer]: 管理
            - article [ref=e50]:
              - generic [ref=e51]: 成
              - generic [ref=e52]:
                - strong [ref=e53]: 成员乙
                - generic [ref=e54]: b***@example.test
              - generic [ref=e55]: 客户成员
              - button "管理 成员乙" [ref=e56] [cursor=pointer]: 管理
        - generic [ref=e57]:
          - heading "邀请成员" [level=2] [ref=e58]
          - generic [ref=e59]:
            - generic [ref=e60]: 姓名
            - textbox "姓名" [ref=e61]
          - generic [ref=e62]:
            - generic [ref=e63]: 工作邮箱
            - textbox "工作邮箱" [ref=e64]
          - generic [ref=e65]:
            - generic [ref=e66]: 项目角色
            - combobox "项目角色" [ref=e67]:
              - option "客户成员" [selected]
              - option "客户管理员"
          - button "发送邀请" [ref=e68] [cursor=pointer]
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