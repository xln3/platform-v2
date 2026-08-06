# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: reports-catalog-integrity.spec.ts >> oversized catalog is disclosed and a mismatched detail is rejected without leakage
- Location: tests/e2e/reports-catalog-integrity.spec.ts:150:5

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText('当前项目报告目录：服务返回 2 条，浏览器安全视图展示 1 条')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText('当前项目报告目录：服务返回 2 条，浏览器安全视图展示 1 条')

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
    - text: 受控展示上限
    - list:
      - listitem: 当前检索窗口内的项目报告：服务返回 2 条，浏览器安全视图展示 1 条
    - text: 完整集合需通过服务端分页或受控导出查看；当前视图不会静默声称数据完整。
  - alert: 安全投影不完整 报告目录包含跨项目、重复标识、乱序时间、游标不一致或未通过 DLP 校验的记录；未请求其详情。
  - alert:
    - strong: 加载失败
    - paragraph: 局部请求失败，其他区域仍可使用。
    - button "重试此区域"
```

# Test source

```ts
  92  |         window_end: '2026-07-21T23:59:59Z',
  93  |         filters: {},
  94  |         metric_version: 'metric-v1',
  95  |         scorer_version: 'scorer-v1',
  96  |         fact_snapshot_hash: 'a'.repeat(64),
  97  |         status: 'review',
  98  |         components: [
  99  |           {
  100 |             pub_id: `${componentBase}_01_00`,
  101 |             report_version_pub_id: `${versionBase}_01`,
  102 |             component_type: 'section',
  103 |             ordinal: 0,
  104 |             source: 'human',
  105 |             payload: { title: '执行摘要', body: '上一版报告正文。' },
  106 |             created_at: '2026-07-25T00:10:00Z',
  107 |           },
  108 |         ],
  109 |         frozen_facts: [],
  110 |         artifacts: [],
  111 |         evidence_bindings: [],
  112 |         reviews: [],
  113 |         comments: [],
  114 |         events: [],
  115 |       },
  116 |       {
  117 |         pub_id: `${versionBase}_02`,
  118 |         version_number: 2,
  119 |         window_start: '2026-07-01T00:00:00Z',
  120 |         window_end: '2026-07-21T23:59:59Z',
  121 |         filters: {},
  122 |         metric_version: 'metric-v1',
  123 |         scorer_version: 'scorer-v1',
  124 |         fact_snapshot_hash: 'b'.repeat(64),
  125 |         status: 'review',
  126 |         components: [
  127 |           {
  128 |             pub_id: `${componentBase}_02_00`,
  129 |             report_version_pub_id: `${versionBase}_02`,
  130 |             component_type: 'section',
  131 |             ordinal: 0,
  132 |             source: 'human',
  133 |             payload: { title: '执行摘要', body: currentBody },
  134 |             created_at: '2026-07-25T00:20:00Z',
  135 |           },
  136 |         ],
  137 |         frozen_facts: [],
  138 |         artifacts: [],
  139 |         evidence_bindings: [],
  140 |         reviews: [],
  141 |         comments: [],
  142 |         events: [],
  143 |       },
  144 |     ],
  145 |     optimization_actions: [],
  146 |     ...extension,
  147 |   };
  148 | };
  149 | 
  150 | test('oversized catalog is disclosed and a mismatched detail is rejected without leakage', async ({
  151 |   page,
  152 | }) => {
  153 |   await installReportExperience(page);
  154 |   await page.route('**/api/v2/reports**', (route) => {
  155 |     const path = new URL(route.request().url()).pathname;
  156 |     if (path.endsWith('/reports')) {
  157 |       return route.fulfill({
  158 |         status: 200,
  159 |         contentType: 'application/json',
  160 |         body: JSON.stringify({
  161 |           data: [
  162 |             reportSummary('rpt_catalog_safe', '目录安全报告', projectPubId, {
  163 |               cookie: 'SESSION=report-catalog-root-canary',
  164 |             }),
  165 |             reportSummary('rpt_catalog_over_limit', 'Bearer report-catalog-limit-canary'),
  166 |           ],
  167 |           page: { next_cursor: 'rpt_catalog_safe', has_more: true },
  168 |         }),
  169 |       });
  170 |     }
  171 |     return route.fulfill({
  172 |       status: 200,
  173 |       contentType: 'application/json',
  174 |       body: JSON.stringify(
  175 |         reportDetail(
  176 |           'rpt_catalog_mismatched',
  177 |           '不应采用的详情',
  178 |           '不应采用的跨报告正文。',
  179 |           projectPubId,
  180 |           {
  181 |             token: 'Bearer report-detail-mismatch-canary',
  182 |             profile_path: '/secret/profile/report-detail-mismatch-canary',
  183 |           },
  184 |         ),
  185 |       ),
  186 |     });
  187 |   });
  188 | 
  189 |   await page.goto('/platform/reports/');
  190 |   await expect(
  191 |     page.getByText('当前项目报告目录：服务返回 2 条，浏览器安全视图展示 1 条'),
> 192 |   ).toBeVisible();
      |     ^ Error: expect(locator).toBeVisible() failed
  193 |   await expect(page.getByText('详情投影已拒绝', { exact: true })).toBeVisible();
  194 |   await expect(page.getByText('加载失败')).toBeVisible();
  195 |   const surfaces = await page.evaluate(() => ({
  196 |     dom: document.documentElement.outerHTML,
  197 |     url: location.href,
  198 |     localStorage: { ...localStorage },
  199 |     sessionStorage: { ...sessionStorage },
  200 |   }));
  201 |   expect(JSON.stringify(surfaces)).not.toMatch(
  202 |     /report-catalog-root-canary|report-catalog-limit-canary|report-detail-mismatch-canary|SESSION=|Bearer |\/secret\/profile/i,
  203 |   );
  204 |   await expectAccessible(page);
  205 | });
  206 | 
  207 | test('an embedded full phone in report detail fails closed before cache or rendering', async ({
  208 |   page,
  209 | }) => {
  210 |   await installReportExperience(page);
  211 |   await page.route('**/api/v2/reports**', (route) => {
  212 |     const path = new URL(route.request().url()).pathname;
  213 |     if (path.endsWith('/reports')) {
  214 |       return route.fulfill({
  215 |         status: 200,
  216 |         contentType: 'application/json',
  217 |         body: JSON.stringify({
  218 |           data: [reportSummary('rpt_embedded_phone', '安全目录标题')],
  219 |           page: { next_cursor: null, has_more: false },
  220 |         }),
  221 |       });
  222 |     }
  223 |     return route.fulfill({
  224 |       status: 200,
  225 |       contentType: 'application/json',
  226 |       body: JSON.stringify(
  227 |         reportDetail(
  228 |           'rpt_embedded_phone',
  229 |           'report13800138000detail-phone-canary',
  230 |           '安全正文不应掩盖根详情的 DLP 失败。',
  231 |         ),
  232 |       ),
  233 |     });
  234 |   });
  235 | 
  236 |   await page.goto('/platform/reports/');
  237 |   await expect(page.getByText('详情投影已拒绝', { exact: true })).toBeVisible();
  238 |   await expect(page.getByText('加载失败')).toBeVisible();
  239 |   const surfaces = await page.evaluate(() =>
  240 |     JSON.stringify({
  241 |       dom: document.documentElement.outerHTML,
  242 |       url: location.href,
  243 |       localStorage: { ...localStorage },
  244 |       sessionStorage: { ...sessionStorage },
  245 |     }),
  246 |   );
  247 |   expect(surfaces).not.toMatch(/13800138000|detail-phone-canary/i);
  248 |   await expectAccessible(page);
  249 | });
  250 | 
  251 | test('a bare six-digit OTP in report detail fails closed before query cache', async ({ page }) => {
  252 |   await installReportExperience(page);
  253 |   await page.route('**/api/v2/reports**', (route) => {
  254 |     const path = new URL(route.request().url()).pathname;
  255 |     if (path.endsWith('/reports')) {
  256 |       return route.fulfill({
  257 |         status: 200,
  258 |         contentType: 'application/json',
  259 |         body: JSON.stringify({
  260 |           data: [reportSummary('rpt_bare_otp', '安全目录标题')],
  261 |           page: { next_cursor: null, has_more: false },
  262 |         }),
  263 |       });
  264 |     }
  265 |     return route.fulfill({
  266 |       status: 200,
  267 |       contentType: 'application/json',
  268 |       body: JSON.stringify(
  269 |         reportDetail(
  270 |           'rpt_bare_otp',
  271 |           '请在原生页面输入 824911 完成验证',
  272 |           '安全正文不应掩盖根详情的 OTP DLP 失败。',
  273 |         ),
  274 |       ),
  275 |     });
  276 |   });
  277 | 
  278 |   await page.goto('/platform/reports/');
  279 |   await expect(page.getByText('详情投影已拒绝', { exact: true })).toBeVisible();
  280 |   await expect(page.getByText('加载失败')).toBeVisible();
  281 |   const surfaces = await page.evaluate(() =>
  282 |     JSON.stringify({
  283 |       dom: document.documentElement.outerHTML,
  284 |       url: location.href,
  285 |       localStorage: { ...localStorage },
  286 |       sessionStorage: { ...sessionStorage },
  287 |     }),
  288 |   );
  289 |   expect(surfaces).not.toMatch(/824911|bare-otp-canary/i);
  290 |   await expectAccessible(page);
  291 | });
  292 | 
```