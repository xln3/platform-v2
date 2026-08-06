# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: customer-account.spec.ts >> customer reviews evidence, exports, questions reports and manages members
- Location: tests/e2e/customer-account.spec.ts:693:5

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: getByRole('status')
Expected substring: "已移出项目"
Error: strict mode violation: getByRole('status') resolved to 2 elements:
    1) <div role="status" aria-live="polite" class="sidebar-foot">…</div> aka getByText('unavailable')
    2) <div role="status" class="toast toast-positive">周岚 已移出项目，历史审计仍保留</div> aka getByText('周岚 已移出项目，历史审计仍保留')

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
        - emphasis [ref=e28]: "2"
    - status [ref=e29]: unavailable
  - generic [ref=e31]:
    - banner [ref=e32]:
      - button "云岫智能 · 品牌增长项目 ⌄" [ref=e33] [cursor=pointer]
      - generic [ref=e34]:
        - button "通知" [ref=e35] [cursor=pointer]: ◌
        - generic "林澄" [ref=e36]: 林
    - main [ref=e37]:
      - generic [ref=e38]:
        - generic [ref=e39]:
          - text: Customer Web
          - heading "客户工作台" [level=1] [ref=e40]
          - paragraph [ref=e41]: 从项目资料到监测、证据、报告与平台账号授权的安全协作入口。
        - generic [ref=e42]:
          - button "导出视图" [ref=e43] [cursor=pointer]
          - button "创建任务" [ref=e44] [cursor=pointer]
      - generic [ref=e45]:
        - generic [ref=e46]:
          - heading "项目成员" [level=2] [ref=e47]
          - paragraph [ref=e48]: 客户管理员可以管理本租户成员；邮箱在列表和审计中保持掩码。
          - article [ref=e50]:
            - generic [ref=e51]: 林
            - generic [ref=e52]:
              - strong [ref=e53]: 林澄
              - generic [ref=e54]: l***@yunxiu.example
            - generic [ref=e55]: 客户管理员
            - button "管理 林澄" [ref=e56] [cursor=pointer]: 管理
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
        - status [ref=e69]: 周岚 已移出项目，历史审计仍保留
```

# Test source

```ts
  683 |   await page.getByLabel('业务原因').fill('需要覆盖客户采购决策阶段的真实比较问题。');
  684 |   await page.getByRole('button', { name: '提交审核' }).click();
  685 |   await expect(page.getByText('待运营审核', { exact: true })).toBeVisible();
  686 | 
  687 |   await captureSafeScreenshot(page, {
  688 |     path: `tests/e2e-results/customer-forms-${viewportName}.png`,
  689 |     fullPage: true,
  690 |   });
  691 | });
  692 | 
  693 | test('customer reviews evidence, exports, questions reports and manages members', async ({
  694 |   page,
  695 | }, testInfo) => {
  696 |   const viewportName = testInfo.project.name.replace('customer-', '');
  697 |   await page.route('**/api/v2/health', (route) =>
  698 |     route.fulfill({
  699 |       status: 200,
  700 |       contentType: 'application/json',
  701 |       body: JSON.stringify({
  702 |         status: 'mock-ready',
  703 |         service: 'geo-platform-v2',
  704 |         version: 'contract-v1',
  705 |       }),
  706 |     }),
  707 |   );
  708 |   await page.goto('/platform/customer/');
  709 |   await page.getByRole('button', { name: '前往报告' }).click();
  710 |   await expect(page).toHaveURL(/section=reports/);
  711 |   await expect(page.getByRole('heading', { name: '2026 Q3 GEO 监测与优化建议' })).toBeVisible();
  712 | 
  713 |   await page.getByRole('button', { name: '回答证据' }).click();
  714 |   await page.getByRole('button', { name: '下一页' }).click();
  715 |   await expect(page).toHaveURL(/answer_page=2/);
  716 |   await page.getByLabel('回答地域').selectOption('上海');
  717 |   await expect(page.getByText('企业知识库如何选择？')).toBeVisible();
  718 |   await expect(page.getByText('第 1 / 1 页')).toBeVisible();
  719 |   await expect(page).not.toHaveURL(/answer_page=/);
  720 |   await page.getByLabel('回答地域').selectOption('all');
  721 |   await page.getByLabel('回答模式筛选').selectOption('deep');
  722 |   await expect(page).toHaveURL(/answer_mode=deep/);
  723 |   await expect(page.getByText('企业知识库如何选择？')).toBeVisible();
  724 |   await page.getByRole('button', { name: '查看回答截图' }).first().click();
  725 |   await expect(page.getByRole('dialog', { name: '证据与历史差异' })).toBeVisible();
  726 |   await expect(page.getByRole('img', { name: /锚点高亮品牌提及/ })).toBeVisible();
  727 |   await page.getByRole('button', { name: '关闭证据弹窗' }).click();
  728 |   await page.getByRole('button', { name: '打开证据中心' }).first().click();
  729 |   await expect(page.getByRole('dialog', { name: '证据与历史差异' })).toBeVisible();
  730 |   await page.getByRole('button', { name: '关闭证据弹窗' }).click();
  731 |   await page.getByLabel('搜索问题').fill('OTP: 824911 · Cookie=session-dlp-canary');
  732 |   await expect(page).not.toHaveURL(/824911|session-dlp-canary/);
  733 |   const evidenceDownload = page.waitForEvent('download');
  734 |   await page.getByRole('button', { name: '生成证据包' }).click();
  735 |   const evidenceArtifact = await evidenceDownload;
  736 |   expect(evidenceArtifact.suggestedFilename()).toBe('evidence-package-manifest.json');
  737 |   const evidenceContent = await readDownload(evidenceArtifact);
  738 |   const evidenceManifest = JSON.parse(evidenceContent) as {
  739 |     version: string;
  740 |     answers: Array<{ id: string; question: string; model: string; capturedAt: string }>;
  741 |   };
  742 |   expect(evidenceManifest.version).toBe('1.0');
  743 |   expect(evidenceManifest.answers.length).toBeGreaterThan(0);
  744 |   expect(evidenceContent).not.toMatch(secretArtifactPattern);
  745 | 
  746 |   await page.getByRole('button', { name: '报告' }).click();
  747 |   await page.getByRole('button', { name: '在线预览' }).click();
  748 |   await expect(page.getByRole('dialog', { name: '2026 Q3 GEO 监测与优化建议' })).toContainText(
  749 |     '发布 hash 已核验',
  750 |   );
  751 |   await page.getByRole('button', { name: '关闭在线报告预览' }).click();
  752 |   const csvDownload = page.waitForEvent('download');
  753 |   await page.getByRole('button', { name: '导出筛选数据' }).click();
  754 |   const csvArtifact = await csvDownload;
  755 |   expect(csvArtifact.suggestedFilename()).toBe('geo-report-data.csv');
  756 |   const csvContent = await readDownload(csvArtifact);
  757 |   expect(csvContent).toContain('metric,value,numerator,denominator');
  758 |   expect(csvContent).toContain('mention_rate,0.684,26,38');
  759 |   expect(csvContent).not.toMatch(secretArtifactPattern);
  760 |   await page.getByRole('textbox', { name: '问题' }).fill('Cookie=SESSION-customer-question-canary');
  761 |   await expect(page.getByText(/请勿在普通表单粘贴验证码/)).toBeVisible();
  762 |   await expect(page.getByRole('button', { name: '提交问题' })).toBeDisabled();
  763 |   await page.getByRole('textbox', { name: '问题' }).fill('Top 3 目标值如何复算？');
  764 |   await page.getByRole('button', { name: '提交问题' }).click();
  765 |   await page.getByRole('button', { name: '确认收到 v1.2' }).click();
  766 |   await expect(page.getByText('已确认接收 v1.2')).toBeVisible();
  767 | 
  768 |   await page.getByRole('button', { name: '成员' }).click();
  769 |   await page.getByLabel('姓名').fill('周岚');
  770 |   await page.getByLabel('工作邮箱').fill('zhoulan@example.test');
  771 |   await page.getByRole('button', { name: '发送邀请' }).click();
  772 |   await expect(page.getByText('z***@example.test')).toBeVisible();
  773 |   await expect(page.locator('body')).not.toContainText('zhoulan@example.test');
  774 |   await page.getByRole('button', { name: '管理 林澄' }).click();
  775 |   await expect(page.getByRole('button', { name: '改为客户成员' })).toBeDisabled();
  776 |   await expect(page.getByRole('button', { name: '移出项目' })).toBeDisabled();
  777 |   await page.getByRole('button', { name: '关闭成员管理' }).click();
  778 |   await page.getByRole('button', { name: '管理 周岚' }).click();
  779 |   await page.getByRole('button', { name: '提升为客户管理员' }).click();
  780 |   await expect(page.getByRole('dialog')).toContainText('客户管理员');
  781 |   await page.getByRole('button', { name: '移出项目' }).click();
  782 |   await expect(page.getByRole('button', { name: '管理 周岚' })).toHaveCount(0);
> 783 |   await expect(page.getByRole('status')).toContainText('已移出项目');
      |                                          ^ Error: expect(locator).toContainText(expected) failed
  784 | 
  785 |   await captureSafeScreenshot(page, {
  786 |     path: `tests/e2e-results/customer-delivery-${viewportName}.png`,
  787 |     fullPage: true,
  788 |   });
  789 | });
  790 | 
```