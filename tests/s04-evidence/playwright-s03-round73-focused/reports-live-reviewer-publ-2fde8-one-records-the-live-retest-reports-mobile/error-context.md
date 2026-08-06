# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: reports-live.spec.ts >> reviewer publishes and delivers while analyst alone records the live retest
- Location: tests/e2e/reports-live.spec.ts:4:5

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText('真实发布操作已完成')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText('真实发布操作已完成')

```

```yaml
- link "跳到主要内容":
  - /url: "#main-content"
- complementary:
  - navigation "Report Studio 主导航":
    - button "数据窗口"
    - button "KPI Trace"
    - button "章节编辑"
    - button "版本对比"
    - button "证据编排"
    - button "PDF 预览"
    - button "审核发布"
    - button "效果复盘"
- banner:
  - button "租户 · s_live · 真实报告联调项目 ⌄"
  - button "通知": ◌
  - text: 用
- main:
  - text: Report Studio
  - heading "报告工作室" [level=1]
  - paragraph: 冻结事实窗口，编辑可追溯章节，并通过审核门发布。
  - button "导出视图"
  - button "创建任务"
  - text: Release gates
  - heading "审核与发布门" [level=2]
  - text: approved 真实 reports API
  - list:
    - listitem: ✓事实窗口已冻结
    - listitem: ✓KPI 与章节证据齐全
    - listitem: ✓AI 草稿已人工确认
    - listitem: ✓未解决评论已逐条纳入本次审核
  - button "提交审核" [disabled]
  - button "批准发布" [disabled]
  - button "发布 v1.0"
  - status: 真实审核决定已记录
  - alert:
    - strong: 加载失败
    - paragraph: 局部请求失败，其他区域仍可使用。
  - complementary:
    - heading "审核评论" [level=2]
    - text: 新增评论
    - textbox "新增评论"
    - button "添加评论" [disabled]
```

# Test source

```ts
  292 |         }),
  293 |       });
  294 |       return;
  295 |     }
  296 |     if (path.endsWith('/effect-retests')) {
  297 |       await route.fulfill({
  298 |         status: 201,
  299 |         contentType: 'application/json',
  300 |         body: JSON.stringify({
  301 |           effect_retest_pub_id: validEffectRetestReceipt
  302 |             ? 'rts_live_safe'
  303 |             : 'Bearer retest-receipt-canary',
  304 |           cookie: validEffectRetestReceipt ? undefined : 'SESSION=retest-receipt-canary',
  305 |         }),
  306 |       });
  307 |       return;
  308 |     }
  309 |     if (path.endsWith('/reviews')) {
  310 |       await route.fulfill({
  311 |         status: 201,
  312 |         contentType: 'application/json',
  313 |         body: JSON.stringify({ review_pub_id: 'rvw_live_safe' }),
  314 |       });
  315 |       return;
  316 |     }
  317 |     if (path.endsWith('/comments')) {
  318 |       await route.fulfill({
  319 |         status: 201,
  320 |         contentType: 'application/json',
  321 |         body: JSON.stringify({
  322 |           comment_pub_id: 'cmt_live_write_safe',
  323 |           report_pub_id: 'rpt_live_safe',
  324 |         }),
  325 |       });
  326 |       return;
  327 |     }
  328 |     if (path.endsWith('/deliveries')) {
  329 |       await route.fulfill({
  330 |         status: 201,
  331 |         contentType: 'application/json',
  332 |         body: JSON.stringify({
  333 |           delivery_pub_id: 'dlv_live_safe',
  334 |           report_pub_id: 'rpt_live_safe',
  335 |         }),
  336 |       });
  337 |       return;
  338 |     }
  339 |     await route.fulfill({
  340 |       status: 201,
  341 |       contentType: 'application/json',
  342 |       body: JSON.stringify({ pub_id: 'receipt_safe' }),
  343 |     });
  344 |   });
  345 | 
  346 |   await page.goto(
  347 |     '/platform/reports/?report_page=2&report_cursor=rpt_Bearer%20report-cursor-request-canary',
  348 |   );
  349 |   await expect(page).not.toHaveURL(/report_(?:page|cursor)=/);
  350 |   await expect(page.getByRole('heading', { name: '真实季度报告' })).toBeVisible();
  351 |   await expect(page.getByText(/列表合同未提供冻结窗口/)).toBeVisible();
  352 |   await page.getByRole('button', { name: '下一页' }).click();
  353 |   await expect(page).toHaveURL(/report_page=2/);
  354 |   await expect(page).toHaveURL(/report_cursor=rpt_live_safe/);
  355 |   await expect(page.getByRole('heading', { name: '第二页真实季度报告' })).toBeVisible();
  356 |   await page.goBack();
  357 |   await expect(page).not.toHaveURL(/report_(?:page|cursor)=/);
  358 |   await expect(page.getByRole('heading', { name: '真实季度报告' })).toBeVisible();
  359 |   await page.getByRole('button', { name: '版本对比' }).click();
  360 |   await expect(page.getByRole('heading', { name: '版本 1 → 2' })).toBeVisible();
  361 |   await expect(page.getByLabel('真实报告版本正文差异')).toContainText('删除 4 字 · 新增 5 字');
  362 |   await page.getByRole('button', { name: '证据编排' }).click();
  363 |   await expect(page.getByLabel('冻结事实证据绑定').getByRole('table')).toContainText(
  364 |     'evd_report_source_safe',
  365 |   );
  366 |   await expect(page.getByLabel('冻结事实证据绑定').getByRole('table')).toContainText('2');
  367 |   await expect(page.getByText('Cookie=report-binding-object-canary')).toHaveCount(0);
  368 |   const artifactDownloadPromise = page.waitForEvent('download');
  369 |   await page.getByRole('button', { name: '校验后下载' }).click();
  370 |   const artifactDownload = await artifactDownloadPromise;
  371 |   expect(artifactDownload.suggestedFilename()).toBe('rpt_live_safe-rptv_live_safe.pdf');
  372 |   expect(await artifactDownload.failure()).toBeNull();
  373 |   await expect.poll(() => artifactRequests).toBe(1);
  374 |   await page.getByRole('button', { name: 'PDF 预览' }).click();
  375 |   await expect(page.getByRole('heading', { name: '已冻结 PDF 预览' })).toBeVisible();
  376 |   await expect.poll(() => artifactRequests).toBe(2);
  377 |   await page.getByRole('button', { name: '章节编辑' }).click();
  378 |   await expect(page.getByRole('button', { name: '保存不可变报告版本' })).toBeDisabled();
  379 |   await expect(page.getByText('报告修订仅由分析师维护。')).toBeVisible();
  380 |   await page.getByRole('button', { name: /审核发布/ }).click();
  381 |   await expect(page.getByText('真实 reports API')).toBeVisible();
  382 |   await page.getByRole('button', { name: '纳入本次审核', exact: true }).click();
  383 |   await page.getByLabel('新增评论').fill('请记录真实合同评论');
  384 |   await page.getByRole('button', { name: '添加评论' }).click();
  385 |   await expect(page.getByText('真实审核评论已记录')).toBeVisible();
  386 |   await page.getByRole('button', { name: '纳入本次审核', exact: true }).click();
  387 |   await page.getByRole('button', { name: '确认 AI 草稿已人工复核' }).click();
  388 |   await page.getByRole('button', { name: '提交审核' }).click();
  389 |   await page.getByRole('button', { name: '批准发布' }).click();
  390 |   await expect(page.getByText('真实审核决定已记录')).toBeVisible();
  391 |   await page.getByRole('button', { name: '发布 v1.0' }).click();
> 392 |   await expect(page.getByText('真实发布操作已完成')).toBeVisible();
      |                                             ^ Error: expect(locator).toBeVisible() failed
  393 |   await page.getByLabel('客户收件人 ID').fill('Bearer delivery-recipient-form-canary');
  394 |   await expect(page.getByText('只接受不含秘密的 usr_ 客户公开标识')).toBeVisible();
  395 |   await expect(page.getByRole('button', { name: '创建客户交付' })).toBeDisabled();
  396 |   expect(writes).toHaveLength(3);
  397 |   await page.getByLabel('客户收件人 ID').fill('usr_customer_delivery_safe');
  398 |   await page.getByRole('button', { name: '创建客户交付' }).click();
  399 |   await expect(page.getByText('真实 delivery 已创建，指定客户可确认接收')).toBeVisible();
  400 |   await expect(page).toHaveScreenshot('reports-live-published.png', {
  401 |     fullPage: true,
  402 |     animations: 'disabled',
  403 |   });
  404 |   expect(
  405 |     await page.evaluate(
  406 |       () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  407 |     ),
  408 |   ).toBe(true);
  409 |   await page.getByRole('button', { name: '效果复盘' }).click();
  410 |   await expect(page.getByRole('button', { name: '开始执行' })).toBeDisabled();
  411 |   await expect(page.getByText('优化行动与复测由分析师维护。')).toBeVisible();
  412 |   expect(writes).toHaveLength(4);
  413 |   expect(writes.map((write) => new URL(write.url).pathname)).toEqual([
  414 |     '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/comments',
  415 |     '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/reviews',
  416 |     '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/publish',
  417 |     '/api/v2/reports/rpt_live_safe/deliveries',
  418 |   ]);
  419 |   expect(writes[0]?.body).toEqual({ body: '请记录真实合同评论', parent_pub_id: null });
  420 |   expect(writes[1]?.body).toMatchObject({ decision: 'approved' });
  421 |   expect(writes[3]?.body).toEqual({ recipient_pub_id: 'usr_customer_delivery_safe' });
  422 |   const surfaces = await page.evaluate(() =>
  423 |     JSON.stringify({
  424 |       dom: document.documentElement.outerHTML,
  425 |       url: location.href,
  426 |       localStorage: { ...localStorage },
  427 |       sessionStorage: { ...sessionStorage },
  428 |     }),
  429 |   );
  430 |   expect(surfaces).not.toMatch(
  431 |     /report-detail-canary|report-cursor-request-canary|SESSION=|Bearer |824911|\/secret\/profile/i,
  432 |   );
  433 |   expect(JSON.stringify(writes)).not.toMatch(/Cookie|Bearer|otp|profile_path/i);
  434 |   identityRole = 'analyst';
  435 |   await page.evaluate(() => localStorage.setItem('geo.e2e.report-role', 'analyst'));
  436 |   await page.reload();
  437 |   await expect(page.getByRole('heading', { name: '优化建议与效果复盘' })).toBeVisible();
  438 |   await page.getByRole('button', { name: '章节编辑' }).click();
  439 |   await page.getByLabel('真实章节正文').fill('当前版结论已由分析师完成真实合同修订。');
  440 |   await page.getByLabel('组件证据 ID').fill('Bearer report-revision-secret-canary');
  441 |   await expect(page.getByText('证据绑定只接受不含秘密的 evd_ 公开标识')).toBeVisible();
  442 |   await expect(page.getByRole('button', { name: '保存不可变报告版本' })).toBeDisabled();
  443 |   await page.getByRole('button', { name: /风险建议/ }).click();
  444 |   await expect(page.getByRole('button', { name: '保存不可变报告版本' })).toBeDisabled();
  445 |   await page.getByRole('button', { name: /执行摘要/ }).click();
  446 |   expect(writes).toHaveLength(4);
  447 |   await page.getByLabel('组件证据 ID').fill('evd_report_source_safe');
  448 |   await page.getByRole('button', { name: '保存不可变报告版本' }).click();
  449 |   await expect(page.getByText('真实报告版本 3 已冻结')).toBeVisible();
  450 |   expect(revisionIdempotencyKeys).toHaveLength(1);
  451 |   expect(revisionIdempotencyKeys[0]).toMatch(/^report-revision-[0-9a-f-]{36}$/);
  452 |   await page.getByRole('button', { name: '效果复盘' }).click();
  453 |   await page.getByRole('button', { name: '开始执行' }).click();
  454 |   await expect(page.getByText('真实优化行动已登记')).toBeVisible();
  455 |   expect(writes).toHaveLength(7);
  456 |   await page.getByLabel('效果变化').fill('101');
  457 |   await expect(page.getByText('效果变化必须在 -100 到 100 之间')).toBeVisible();
  458 |   await expect(page.getByRole('button', { name: '记录复测效果' })).toBeDisabled();
  459 |   expect(writes).toHaveLength(7);
  460 |   await page.getByLabel('效果变化').fill('6.2');
  461 |   await page.getByRole('button', { name: '记录复测效果' }).click();
  462 |   await expect(page.getByText('真实效果复测已追加记录')).toBeVisible();
  463 |   expect(writes.map((write) => new URL(write.url).pathname)).toEqual([
  464 |     '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/comments',
  465 |     '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/reviews',
  466 |     '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/publish',
  467 |     '/api/v2/reports/rpt_live_safe/deliveries',
  468 |     '/api/v2/reports/rpt_live_safe/versions',
  469 |     '/api/v2/reports/rpt_live_safe/actions',
  470 |     '/api/v2/reports/rpt_live_safe/actions/act_live_safe',
  471 |     '/api/v2/reports/rpt_live_safe/actions/act_live_safe/effect-retests',
  472 |     '/api/v2/reports/rpt_live_safe/actions/act_live_safe',
  473 |   ]);
  474 |   expect(writes[4]?.body).toEqual({
  475 |     components: [
  476 |       {
  477 |         component_type: 'section',
  478 |         source: 'human',
  479 |         title: '执行摘要',
  480 |         body: '当前版结论已由分析师完成真实合同修订。',
  481 |         evidence_pub_ids: ['evd_report_source_safe'],
  482 |       },
  483 |       {
  484 |         component_type: 'section',
  485 |         source: 'ai',
  486 |         title: '风险建议',
  487 |         body: '建议由人工复核现有证据。',
  488 |         evidence_pub_ids: ['evd_report_risk_safe'],
  489 |       },
  490 |     ],
  491 |   });
  492 |   expect(writes[5]?.body).toMatchObject({
```