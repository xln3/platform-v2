# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: customer-role-isolation.spec.ts >> an explicit session failure stays fail-closed and recovers only after user retry
- Location: tests/e2e/customer-role-isolation.spec.ts:268:5

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
      - button "租户 · y_safe · 重试恢复项目 ⌄" [ref=e19] [cursor=pointer]
      - generic [ref=e20]:
        - button "通知" [ref=e21] [cursor=pointer]: ◌
        - generic "用户 · y_safe" [ref=e22]: 用
    - main [ref=e23]:
      - generic [ref=e24]:
        - generic [ref=e25]:
          - text: Customer Web
          - heading "客户工作台" [level=1] [ref=e26]
          - paragraph [ref=e27]: 从项目资料到监测、证据、报告与平台账号授权的安全协作入口。
        - generic [ref=e28]:
          - button "导出视图" [ref=e29] [cursor=pointer]
          - button "创建任务" [ref=e30] [cursor=pointer]
      - generic [ref=e32]:
        - text: Analytics overview
        - heading "项目监测概览" [level=2] [ref=e33]
        - paragraph [ref=e34]: 展示当前项目最近 30 天的真实分析合同结果；当前合同未提供项目阶段或采集计划，不展示进度比例。
      - status [ref=e35]:
        - generic [ref=e37]:
          - strong [ref=e38]: 无权查看
          - paragraph [ref=e39]: 当前角色没有此资源权限，也不会披露资源是否存在。
      - generic [ref=e40]:
        - generic [ref=e41]:
          - heading "下一步" [level=2] [ref=e42]
          - paragraph [ref=e43]: 当前安全投影未提供建议动作，不根据指标推断客户待办。
          - status [ref=e44]:
            - generic [ref=e46]:
              - strong [ref=e47]: 样本不足
              - paragraph [ref=e48]: 已有数据尚未达到可解释门槛，暂不生成结论。
        - generic [ref=e49]:
          - heading "数据新鲜度" [level=2] [ref=e50]
          - paragraph [ref=e51]: 真实状态与最后可用版本分开显示。
          - status [ref=e52]:
            - generic [ref=e54]:
              - strong [ref=e55]: 样本不足
              - paragraph [ref=e56]: 已有数据尚未达到可解释门槛，暂不生成结论。
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
  14 |         collector.stop();
  15 |         const observed = summarizeBrowserRuntimeIssues(collector.issues);
  16 |         const expected = {
  17 |           'console-error': 0,
  18 |           'page-error': 0,
  19 |           'request-failed': 0,
  20 |         };
  21 |         if (collector.issues.length) {
  22 |           await testInfo.attach('browser-runtime-guard-summary', {
  23 |             body: Buffer.from(JSON.stringify({ observed, expected })),
  24 |             contentType: 'application/json',
  25 |           });
  26 |         }
  27 |         expect(
  28 |           observed,
  29 |           'Browser runtime must retain literal zero console errors, page errors and failed requests. Raw messages and URLs are intentionally excluded from failure output.',
> 30 |         ).toEqual(expected);
     |           ^ Error: Browser runtime must retain literal zero console errors, page errors and failed requests. Raw messages and URLs are intentionally excluded from failure output.
  31 |       }
  32 |     },
  33 |     { auto: true },
  34 |   ],
  35 | });
  36 | 
```