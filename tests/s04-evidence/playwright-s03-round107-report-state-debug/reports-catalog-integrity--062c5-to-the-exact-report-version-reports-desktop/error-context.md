# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: reports-catalog-integrity.spec.ts >> browser history rekeys review and action state to the exact report version
- Location: tests/e2e/reports-catalog-integrity.spec.ts:905:5

# Error details

```
Error: Browser runtime must retain literal zero console errors, page errors and failed requests. Raw messages and URLs are intentionally excluded from failure output.

expect(received).toEqual(expected) // deep equality

- Expected  - 1
+ Received  + 1

  Object {
    "console-error": 0,
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
      - generic [ref=e42]:
        - article [ref=e43]:
          - generic [ref=e44]: 建议
          - generic [ref=e45]: "1"
          - generic [ref=e47]: 真实优化行动
        - article [ref=e48]:
          - generic [ref=e49]: 执行中
          - generic [ref=e50]: "0"
          - generic [ref=e52]: 合同状态
        - article [ref=e53]:
          - generic [ref=e54]: 复测记录
          - generic [ref=e55]: "0"
          - generic [ref=e57]: 不可变记录
        - article [ref=e58]:
          - generic [ref=e59]: 最近效果
          - generic [ref=e60]: —
          - generic [ref=e62]: 复测后可用
      - generic [ref=e63]:
        - heading "优化建议与效果复盘" [level=2] [ref=e64]
        - generic [ref=e65]: 真实 reports API
        - region "优化建议与复测表" [ref=e66]:
          - table [ref=e67]:
            - rowgroup [ref=e68]:
              - row "建议 负责人 状态 复测" [ref=e69]:
                - columnheader "建议" [ref=e70]
                - columnheader "负责人" [ref=e71]
                - columnheader "状态" [ref=e72]
                - columnheader "复测" [ref=e73]
            - rowgroup [ref=e74]:
              - row "第一份报告优化行动 安全投影未提供 proposed 待复测" [ref=e75]:
                - cell "第一份报告优化行动" [ref=e76]
                - cell "安全投影未提供" [ref=e77]
                - cell "proposed" [ref=e78]:
                  - generic [ref=e79]: proposed
                - cell "待复测" [ref=e80]
        - list "建议执行与复测进度" [ref=e81]:
          - listitem [ref=e82]:
            - generic [ref=e84]:
              - strong [ref=e85]: 建议已确认
              - generic [ref=e86]: 真实 action 记录
            - generic [ref=e87]: completed
          - listitem [ref=e88]:
            - generic [ref=e90]:
              - strong [ref=e91]: 内容优化
              - generic [ref=e92]: 按 action 状态推进
            - generic [ref=e93]: completed
          - listitem [ref=e94]:
            - generic [ref=e96]:
              - strong [ref=e97]: 30 天复测
              - generic [ref=e98]: 等待人工录入真实复测
            - generic [ref=e99]: scheduled
        - generic [ref=e100]:
          - button "开始执行" [ref=e101] [cursor=pointer]
          - button "记录复测效果" [disabled] [ref=e102]
        - generic [ref=e104]:
          - generic [ref=e105]: 效果变化（百分点，-100 至 100）
          - spinbutton "效果变化" [ref=e106]
        - status [ref=e107]: 真实优化行动已登记
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