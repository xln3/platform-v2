# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: customer-account.spec.ts >> validated customer submits a project change request through the generated live contract
- Location: tests/e2e/customer-account.spec.ts:465:5

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: getByRole('status')
Expected substring: "申请已进入待运营审核队列"
Error: strict mode violation: getByRole('status') resolved to 2 elements:
    1) <div role="status" aria-live="polite" class="sidebar-foot">…</div> aka getByText('ok')
    2) <section role="status" class="state-panel state-empty">…</section> aka locator('section')

Call log:
  - Expect "toContainText" with timeout 5000ms
  - waiting for getByRole('status')

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
    - generic [ref=e8]: Customer Web
    - navigation "Customer Web 主导航" [ref=e9]:
      - button "首页" [ref=e10] [cursor=pointer]:
        - generic [ref=e11]: 首页
      - button "资料" [ref=e12] [cursor=pointer]:
        - generic [ref=e13]: 资料
      - button "品牌产品" [ref=e14] [cursor=pointer]:
        - generic [ref=e15]: 品牌产品
      - button "问题目标" [ref=e16] [cursor=pointer]:
        - generic [ref=e17]: 问题目标
      - button "监测表现" [ref=e18] [cursor=pointer]:
        - generic [ref=e19]: 监测表现
      - button "回答证据" [ref=e20] [cursor=pointer]:
        - generic [ref=e21]: 回答证据
      - button "报告" [ref=e22] [cursor=pointer]:
        - generic [ref=e23]: 报告
      - button "成员" [ref=e24] [cursor=pointer]:
        - generic [ref=e25]: 成员
      - button "平台账号" [ref=e26] [cursor=pointer]:
        - generic [ref=e27]: 平台账号
    - status [ref=e28]: ok
  - generic [ref=e30]:
    - banner [ref=e31]:
      - button "租户 · r_live · 客户真实项目 ⌄" [ref=e32] [cursor=pointer]
      - generic [ref=e33]:
        - button "通知" [ref=e34] [cursor=pointer]: ◌
        - generic "用户 · r_live" [ref=e35]: 用
    - main [ref=e36]:
      - generic [ref=e37]:
        - generic [ref=e38]:
          - text: Customer Web
          - heading "客户工作台" [level=1] [ref=e39]
          - paragraph [ref=e40]: 从项目资料到监测、证据、报告与平台账号授权的安全协作入口。
        - generic [ref=e41]:
          - button "导出视图" [ref=e42] [cursor=pointer]
          - button "创建任务" [ref=e43] [cursor=pointer]
      - generic [ref=e44]:
        - generic [ref=e45]:
          - text: Change request
          - heading "问题、目标与配置申请" [level=2] [ref=e46]
          - paragraph [ref=e47]: 客户提交的是待审核申请，不直接修改调度真源。运营审批、生效版本和审计事件分别记录。
          - generic [ref=e48]:
            - generic [ref=e49]: 关注问题
            - textbox "关注问题" [ref=e50]:
              - /placeholder: 例如：制造企业如何选择可私有化部署的知识库？
          - generic [ref=e51]:
            - generic [ref=e52]:
              - generic [ref=e53]: 优先级
              - combobox "优先级" [ref=e54]:
                - option "高"
                - option "中" [selected]
                - option "低"
            - generic [ref=e55]:
              - generic [ref=e56]: 目标指标
              - combobox "目标指标" [ref=e57]:
                - option "品牌提及率" [selected]
                - option "Top 3 占比"
                - option "引用覆盖"
            - generic [ref=e58]:
              - generic [ref=e59]: 目标值（%）
              - spinbutton "目标值（%）" [ref=e60]: "70"
            - generic [ref=e61]:
              - generic [ref=e62]: 申请动作
              - combobox "申请动作" [ref=e63]:
                - option "新增问题" [selected]
                - option "申请暂停"
                - option "申请恢复"
                - option "申请补采"
          - generic [ref=e64]:
            - generic [ref=e65]: 业务原因
            - textbox "业务原因" [ref=e66]
          - generic [ref=e67]:
            - generic [ref=e68]: 提交将通过生成的 OpenAPI client 写入幂等申请与审计记录。
            - button "提交审核" [ref=e69] [cursor=pointer]
          - status [ref=e70]: 申请已进入待运营审核队列
        - complementary [ref=e71]:
          - heading "当前问题与目标" [level=2] [ref=e72]
          - article [ref=e74]:
            - generic [ref=e75]: 待运营审核
            - strong [ref=e76]: 制造企业如何选择可信的私有化知识库？
            - generic [ref=e77]: 目标 70% · add_query
```

# Test source

```ts
  468 |   let capturedRequest: { headers: Record<string, string>; body: Record<string, unknown> } | null =
  469 |     null;
  470 |   await page.addInitScript(() => {
  471 |     localStorage.setItem('geo.session.tenant', 'tnt_customer_live');
  472 |     localStorage.setItem('geo.session.actor', 'customer-live-subject');
  473 |     localStorage.setItem('geo.session.role', 'customer');
  474 |   });
  475 |   await page.route('**/api/v2/identity/session', (route) =>
  476 |     route.fulfill({
  477 |       status: 200,
  478 |       contentType: 'application/json',
  479 |       body: JSON.stringify({
  480 |         tenant_pub_id: 'tnt_customer_live',
  481 |         user_pub_id: 'usr_customer_live',
  482 |         role: 'customer',
  483 |         permissions: ['project:read', 'project:write'],
  484 |       }),
  485 |     }),
  486 |   );
  487 |   await page.route('**/api/v2/projects?**', (route) =>
  488 |     route.fulfill({
  489 |       status: 200,
  490 |       contentType: 'application/json',
  491 |       body: JSON.stringify({
  492 |         data: [
  493 |           {
  494 |             pub_id: 'prj_customer_live',
  495 |             tenant_pub_id: 'tnt_customer_live',
  496 |             name: '客户真实项目',
  497 |             state: 'active',
  498 |             created_at: '2026-07-24T00:00:00Z',
  499 |             updated_at: '2026-07-24T00:00:00Z',
  500 |           },
  501 |         ],
  502 |         page: { next_cursor: null, has_more: false },
  503 |       }),
  504 |     }),
  505 |   );
  506 |   await page.route('**/api/v2/projects/prj_customer_live/resources/*', (route) =>
  507 |     route.fulfill({
  508 |       status: 200,
  509 |       contentType: 'application/json',
  510 |       body: '[]',
  511 |     }),
  512 |   );
  513 |   await page.route(
  514 |     '**/api/v2/projects/prj_customer_live/resources/change-requests',
  515 |     async (route) => {
  516 |       const request = route.request();
  517 |       const requestBody = request.postDataJSON() as Record<string, unknown>;
  518 |       capturedRequest = {
  519 |         headers: request.headers(),
  520 |         body: requestBody,
  521 |       };
  522 |       await route.fulfill({
  523 |         status: 201,
  524 |         contentType: 'application/json',
  525 |         body: JSON.stringify({
  526 |           pub_id: 'ent_change_safe',
  527 |           project_pub_id: 'prj_customer_live',
  528 |           resource_kind: 'change-requests',
  529 |           version: 1,
  530 |           data: {
  531 |             kind: requestBody.kind,
  532 |             payload: requestBody.payload,
  533 |             state: requestBody.state,
  534 |             reviewed_by: null,
  535 |             token: 'Bearer change-request-response-canary',
  536 |           },
  537 |           profile_path: '/secret/profile/change-request-response-canary',
  538 |         }),
  539 |       });
  540 |     },
  541 |   );
  542 |   await page.route('**/api/v2/health', (route) =>
  543 |     route.fulfill({
  544 |       status: 200,
  545 |       contentType: 'application/json',
  546 |       body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v1' }),
  547 |     }),
  548 |   );
  549 |   await page.route('**/api/v2/analytics/overview**', (route) =>
  550 |     route.fulfill({
  551 |       status: 200,
  552 |       contentType: 'application/json',
  553 |       body: '[]',
  554 |     }),
  555 |   );
  556 | 
  557 |   await page.goto('/platform/customer/');
  558 |   await page.getByRole('button', { name: '问题目标' }).click();
  559 |   await expect(page.getByText(/生成的 OpenAPI client/)).toBeVisible();
  560 |   await page.getByLabel('关注问题').fill('请使用验证码 824911 查询企业知识库');
  561 |   await page.getByLabel('业务原因').fill('需要覆盖客户采购决策阶段的真实比较问题。');
  562 |   await page.getByRole('button', { name: '提交审核' }).click();
  563 |   await expect(page.getByText(/请勿在普通表单粘贴验证码、Cookie、token、密码/)).toBeVisible();
  564 |   expect(capturedRequest).toBeNull();
  565 | 
  566 |   await page.getByLabel('关注问题').fill('制造企业如何选择可信的私有化知识库？');
  567 |   await page.getByRole('button', { name: '提交审核' }).click();
> 568 |   await expect(page.getByRole('status')).toContainText('申请已进入待运营审核队列');
      |                                          ^ Error: expect(locator).toContainText(expected) failed
  569 | 
  570 |   expect(capturedRequest).not.toBeNull();
  571 |   if (!capturedRequest) throw new Error('live change request was not captured');
  572 |   expect(capturedRequest.headers['x-tenant-id']).toBe('tnt_customer_live');
  573 |   expect(capturedRequest.headers['x-actor-id']).toBe('customer-live-subject');
  574 |   expect(capturedRequest.headers['x-actor-role']).toBe('customer');
  575 |   expect(capturedRequest.headers['idempotency-key']).toMatch(/^customer-change-/);
  576 |   expect(capturedRequest.headers['x-service-token']).toBeUndefined();
  577 |   expect(capturedRequest.body).toMatchObject({
  578 |     kind: 'add_query',
  579 |     state: 'pending',
  580 |     payload: {
  581 |       question: '制造企业如何选择可信的私有化知识库？',
  582 |       goal_metric: 'mention_rate',
  583 |       target_percent: 70,
  584 |     },
  585 |   });
  586 |   expect(JSON.stringify(capturedRequest)).not.toMatch(
  587 |     /cookie|bearer|otp|proxy_password|profile_path|biometric/i,
  588 |   );
  589 |   const browserSurfaces = await page.evaluate(() =>
  590 |     JSON.stringify({
  591 |       dom: document.documentElement.outerHTML,
  592 |       url: location.href,
  593 |       localStorage: { ...localStorage },
  594 |       sessionStorage: { ...sessionStorage },
  595 |     }),
  596 |   );
  597 |   expect(browserSurfaces).not.toMatch(
  598 |     /change-request-response-canary|Bearer |profile_path|\/secret\/profile/i,
  599 |   );
  600 | });
  601 | 
  602 | test('monitoring filters are URL-bound and restore through browser history', async ({ page }) => {
  603 |   await page.route('**/api/v2/health', (route) =>
  604 |     route.fulfill({
  605 |       status: 200,
  606 |       contentType: 'application/json',
  607 |       body: JSON.stringify({
  608 |         status: 'mock-ready',
  609 |         service: 'geo-platform-v2',
  610 |         version: 'contract-v1',
  611 |       }),
  612 |     }),
  613 |   );
  614 |   await page.goto('/platform/customer/');
  615 |   await page.getByRole('button', { name: '监测表现' }).click();
  616 |   await expect(page).toHaveURL(/section=monitoring/);
  617 |   await expect(page.getByText('各模型品牌提及率图表已渲染')).toBeAttached();
  618 |   await expect(page.getByRole('table', { name: /各模型品牌提及率/ })).toBeVisible();
  619 |   await page.getByLabel('模型', { exact: true }).selectOption('deepseek');
  620 |   await expect(page).toHaveURL(/model=deepseek/);
  621 |   await page.getByLabel('回答模式', { exact: true }).selectOption('deep');
  622 |   await expect(page).toHaveURL(/mode=deep/);
  623 |   await page.getByLabel('时间窗口').selectOption('7d');
  624 |   await expect(page).toHaveURL(/window=7d/);
  625 |   await page.getByLabel('监测地域').selectOption('east');
  626 |   await expect(page).toHaveURL(/region=east/);
  627 |   await expect(page.getByRole('heading', { name: '竞品表现' })).toBeVisible();
  628 |   await expect(page.getByRole('table', { name: '近五个冻结日品牌提及率趋势' })).toBeVisible();
  629 |   await expect(page.getByRole('table', { name: '品牌与确认竞品提及率' })).toBeVisible();
  630 |   await expect(page.getByLabel('地域与回答模式表现')).toBeVisible();
  631 | 
  632 |   await page.goBack();
  633 |   await expect(page.getByLabel('监测地域')).toHaveValue('all');
  634 |   await expect(page.getByLabel('时间窗口')).toHaveValue('7d');
  635 |   await page.goBack();
  636 |   await expect(page.getByLabel('时间窗口')).toHaveValue('30d');
  637 |   await expect(page.getByLabel('回答模式', { exact: true })).toHaveValue('deep');
  638 |   await page.goBack();
  639 |   await expect(page.getByLabel('回答模式', { exact: true })).toHaveValue('all');
  640 |   await expect(page.getByLabel('模型', { exact: true })).toHaveValue('deepseek');
  641 |   await page.goBack();
  642 |   await expect(page.getByLabel('模型', { exact: true })).toHaveValue('all');
  643 | });
  644 | 
  645 | test('customer profile, brand assets and configuration requests validate and submit', async ({
  646 |   page,
  647 | }, testInfo) => {
  648 |   const viewportName = testInfo.project.name.replace('customer-', '');
  649 |   await page.route('**/api/v2/health', (route) =>
  650 |     route.fulfill({
  651 |       status: 200,
  652 |       contentType: 'application/json',
  653 |       body: JSON.stringify({
  654 |         status: 'mock-ready',
  655 |         service: 'geo-platform-v2',
  656 |         version: 'contract-v1',
  657 |       }),
  658 |     }),
  659 |   );
  660 |   await page.goto('/platform/customer/');
  661 | 
  662 |   await page.getByRole('button', { name: '资料' }).click();
  663 |   await page.getByRole('button', { name: '保存并生成版本' }).click();
  664 |   await expect(page.getByText('提交前必须确认资料真实性')).toBeVisible();
  665 |   await page.getByRole('checkbox', { name: /我确认上述客户声明真实/ }).check();
  666 |   await page.getByRole('button', { name: '保存并生成版本' }).click();
  667 |   await expect(page.getByText(/客户声明 v3/)).toBeVisible();
  668 | 
```