# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: reports-live.spec.ts >> report 404 uses the same forbidden surface and does not probe detail
- Location: tests/e2e/reports-live.spec.ts:552:5

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText('无权查看')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText('无权查看')

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
  - button "租户 · bidden · 暂无可用项目 ⌄"
  - button "通知": ◌
  - text: 用
- main:
  - text: Report Studio
  - heading "报告工作室" [level=1]
  - paragraph: 冻结事实窗口，编辑可追溯章节，并通过审核门发布。
  - button "导出视图"
  - button "创建任务"
  - alert: 安全投影不完整 报告目录包含跨项目、重复标识、乱序时间、游标不一致或未通过 DLP 校验的记录；未请求其详情。
  - alert:
    - strong: 加载失败
    - paragraph: 局部请求失败，其他区域仍可使用。
    - button "重试此区域"
```

# Test source

```ts
  501 |     components: [
  502 |       {
  503 |         component_type: 'section',
  504 |         source: 'human',
  505 |         title: '执行摘要',
  506 |         body: '当前版结论已由分析师完成真实合同修订。',
  507 |         evidence_pub_ids: ['evd_report_source_safe'],
  508 |       },
  509 |       {
  510 |         component_type: 'section',
  511 |         source: 'ai',
  512 |         title: '风险建议',
  513 |         body: '建议由人工复核现有证据。',
  514 |         evidence_pub_ids: ['evd_report_risk_safe'],
  515 |       },
  516 |     ],
  517 |   });
  518 |   expect(writes[5]?.body).toMatchObject({
  519 |     description: '补齐私有化部署权威材料',
  520 |     owner_pub_id: null,
  521 |   });
  522 |   expect(writes[6]?.body).toEqual({ state: 'in_progress', outcome: null });
  523 |   expect(writes[7]?.body).toMatchObject({
  524 |     result: { metric: 'mention_rate', baseline_version: 2, delta: 6.2 },
  525 |   });
  526 |   expect(writes[8]?.body).toEqual({ state: 'done', outcome: { delta: 6.2 } });
  527 | 
  528 |   validEffectRetestReceipt = false;
  529 |   await page.getByRole('button', { name: '开始执行' }).click();
  530 |   await page.getByLabel('效果变化').fill('7.1');
  531 |   await page.getByRole('button', { name: '记录复测效果' }).click();
  532 |   await expect(page.getByRole('alert').filter({ hasText: '加载失败' })).toBeVisible();
  533 |   expect(writes.map((write) => new URL(write.url).pathname).slice(-2)).toEqual([
  534 |     '/api/v2/reports/rpt_live_safe/actions/act_live_safe',
  535 |     '/api/v2/reports/rpt_live_safe/actions/act_live_safe/effect-retests',
  536 |   ]);
  537 |   expect(writes).toHaveLength(11);
  538 |   const exposedSurfaces = await page.evaluate(() =>
  539 |     JSON.stringify({
  540 |       dom: document.documentElement.outerHTML,
  541 |       url: location.href,
  542 |       localStorage: { ...localStorage },
  543 |       sessionStorage: { ...sessionStorage },
  544 |     }),
  545 |   );
  546 |   expect(exposedSurfaces).not.toMatch(/retest-receipt-canary|Bearer |Cookie=/i);
  547 |   expect(JSON.stringify(writes)).not.toMatch(/Cookie|Bearer|otp|profile_path/i);
  548 |   expect(await syntheticHttpResponseCount(page, 'report-publish-no-content')).toBe(1);
  549 |   expect(await syntheticHttpResponseCount(page, 'report-patch-no-content')).toBe(3);
  550 | });
  551 | 
  552 | test('report 404 uses the same forbidden surface and does not probe detail', async ({ page }) => {
  553 |   await page.addInitScript(() => {
  554 |     localStorage.setItem('geo.session.tenant', 'tnt_reports_forbidden');
  555 |     localStorage.setItem('geo.session.actor', 'analyst-reports-forbidden');
  556 |     localStorage.setItem('geo.session.role', 'analyst');
  557 |   });
  558 |   await installSyntheticHttpResponses(page, [
  559 |     {
  560 |       id: 'report-catalog-forbidden',
  561 |       path: '/api/v2/reports',
  562 |       status: 404,
  563 |       body: {
  564 |         error: {
  565 |           code: 'not_found',
  566 |           message: 'Bearer forbidden-report-canary',
  567 |           request_id: 'req_safe',
  568 |         },
  569 |       },
  570 |     },
  571 |     {
  572 |       id: 'report-detail-forbidden',
  573 |       path: '/api/v2/reports/',
  574 |       match: 'prefix',
  575 |       status: 404,
  576 |     },
  577 |   ]);
  578 |   await page.route('**/api/v2/identity/session', (route) =>
  579 |     route.fulfill({
  580 |       status: 200,
  581 |       contentType: 'application/json',
  582 |       body: JSON.stringify({
  583 |         tenant_pub_id: 'tnt_reports_forbidden',
  584 |         user_pub_id: 'usr_reports_forbidden',
  585 |         role: 'analyst',
  586 |         permissions: ['project:read'],
  587 |       }),
  588 |     }),
  589 |   );
  590 |   await page.route('**/api/v2/projects**', (route) =>
  591 |     route.fulfill({
  592 |       status: 200,
  593 |       contentType: 'application/json',
  594 |       body: JSON.stringify({
  595 |         data: [],
  596 |         page: { next_cursor: null, has_more: false },
  597 |       }),
  598 |     }),
  599 |   );
  600 |   await page.goto('/platform/reports/');
> 601 |   await expect(page.getByText('无权查看')).toBeVisible();
      |                                        ^ Error: expect(locator).toBeVisible() failed
  602 |   await expect(page.getByText('Bearer forbidden-report-canary')).toHaveCount(0);
  603 |   expect(await syntheticHttpResponseCount(page, 'report-catalog-forbidden')).toBe(1);
  604 |   expect(await syntheticHttpResponseCount(page, 'report-detail-forbidden')).toBe(0);
  605 | });
  606 | 
```