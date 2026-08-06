# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: reports-catalog-integrity.spec.ts >> a cross-project catalog row is rejected before any report detail probe
- Location: tests/e2e/reports-catalog-integrity.spec.ts:781:5

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText(/报告目录包含跨项目、重复标识/)
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText(/报告目录包含跨项目、重复标识/)

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
    - strong: 暂无数据
    - paragraph: 当前筛选下没有记录，可以调整筛选条件。
```

# Test source

```ts
  702 |       projectPubId,
  703 |       { state: 'approved' },
  704 |     );
  705 |     return route.fulfill({
  706 |       status: 200,
  707 |       contentType: 'application/json',
  708 |       body: JSON.stringify({
  709 |         ...detail,
  710 |         versions: [
  711 |           detail.versions[0],
  712 |           {
  713 |             ...detail.versions[1],
  714 |             status: 'approved',
  715 |             components: detail.versions[1]!.components.map((component) => ({
  716 |               ...component,
  717 |               payload: {
  718 |                 ...component.payload,
  719 |                 evidence_pub_ids: [
  720 |                   'evd_report_section_linked',
  721 |                   'evd_report_section_dangling_canary',
  722 |                 ],
  723 |               },
  724 |               token: 'Bearer report-section-link-canary',
  725 |             })),
  726 |             frozen_facts: [
  727 |               {
  728 |                 pub_id: 'rptf_report_section_linked',
  729 |                 report_version_pub_id: 'rptv_dangling_evidence_02',
  730 |                 ordinal: 0,
  731 |                 payload: { metric: 'evidence_closure', value: 1 },
  732 |                 payload_hash: 'c'.repeat(64),
  733 |                 created_at: '2026-07-25T00:21:00Z',
  734 |               },
  735 |             ],
  736 |             evidence_bindings: [
  737 |               {
  738 |                 pub_id: 'rptev_report_section_linked',
  739 |                 report_version_pub_id: 'rptv_dangling_evidence_02',
  740 |                 evidence_pub_id: 'evd_report_section_linked',
  741 |                 purpose: 'frozen_fact_or_component',
  742 |                 kind: 'answer_screenshot',
  743 |                 access_class: 'customer_private',
  744 |                 mime_type: 'image/png',
  745 |                 byte_size: 128,
  746 |                 sha256: 'b'.repeat(64),
  747 |                 anchor_count: 1,
  748 |                 capture_time: '2026-07-25T00:20:00Z',
  749 |                 created_at: '2026-07-25T00:22:00Z',
  750 |               },
  751 |             ],
  752 |           },
  753 |         ],
  754 |       }),
  755 |     });
  756 |   });
  757 | 
  758 |   await page.goto('/platform/reports/');
  759 |   await expect(page.getByRole('heading', { name: '章节证据闭包报告' })).toBeVisible();
  760 |   await page.getByRole('button', { name: /审核发布/ }).click();
  761 |   await expect(page.getByText(/章节证据标识含未通过安全校验的数据/)).toBeVisible();
  762 |   await expect(page.getByText('evd_report_section_dangling_canary')).toHaveCount(0);
  763 |   await expect(page.getByRole('button', { name: '发布 v1.0' })).toBeDisabled();
  764 |   await page.getByLabel('新增评论').fill('不应提交未闭包证据报告');
  765 |   await expect(page.getByRole('button', { name: '添加评论' })).toBeDisabled();
  766 |   expect(writes).toEqual([]);
  767 |   const surfaces = await page.evaluate(() =>
  768 |     JSON.stringify({
  769 |       dom: document.documentElement.outerHTML,
  770 |       url: location.href,
  771 |       localStorage: { ...localStorage },
  772 |       sessionStorage: { ...sessionStorage },
  773 |     }),
  774 |   );
  775 |   expect(surfaces).not.toMatch(
  776 |     /evd_report_section_dangling_canary|report-section-link-canary|Bearer /i,
  777 |   );
  778 |   await expectAccessible(page);
  779 | });
  780 | 
  781 | test('a cross-project catalog row is rejected before any report detail probe', async ({ page }) => {
  782 |   let detailRequests = 0;
  783 |   await installReportExperience(page);
  784 |   await page.route('**/api/v2/reports**', (route) => {
  785 |     const path = new URL(route.request().url()).pathname;
  786 |     if (!path.endsWith('/reports')) detailRequests += 1;
  787 |     return route.fulfill({
  788 |       status: 200,
  789 |       contentType: 'application/json',
  790 |       body: JSON.stringify({
  791 |         data: [
  792 |           reportSummary('rpt_cross_project', '不应公开的跨项目报告', 'prj_reports_catalog_other', {
  793 |             token: 'Bearer report-cross-project-canary',
  794 |           }),
  795 |         ],
  796 |         page: { next_cursor: null, has_more: false },
  797 |       }),
  798 |     });
  799 |   });
  800 | 
  801 |   await page.goto('/platform/reports/');
> 802 |   await expect(page.getByText(/报告目录包含跨项目、重复标识/)).toBeVisible();
      |                                                  ^ Error: expect(locator).toBeVisible() failed
  803 |   await expect(page.getByText('不应公开的跨项目报告')).toHaveCount(0);
  804 |   expect(detailRequests).toBe(0);
  805 |   const surfaces = await page.evaluate(() =>
  806 |     JSON.stringify({
  807 |       dom: document.documentElement.outerHTML,
  808 |       url: location.href,
  809 |       localStorage: { ...localStorage },
  810 |       sessionStorage: { ...sessionStorage },
  811 |     }),
  812 |   );
  813 |   expect(surfaces).not.toMatch(/report-cross-project-canary|Bearer /i);
  814 |   await expectAccessible(page);
  815 | });
  816 | 
  817 | test('browser back discards a slower superseded report detail response', async ({ page }) => {
  818 |   let secondPageDetailRequests = 0;
  819 |   await installReportExperience(page);
  820 |   await page.route('**/api/v2/reports**', async (route) => {
  821 |     const url = new URL(route.request().url());
  822 |     const path = url.pathname;
  823 |     const secondPage = url.searchParams.get('cursor') === 'rpt_catalog_page_01';
  824 |     if (path.endsWith('/reports')) {
  825 |       await route.fulfill({
  826 |         status: 200,
  827 |         contentType: 'application/json',
  828 |         body: JSON.stringify({
  829 |           data: [
  830 |             reportSummary(
  831 |               secondPage ? 'rpt_catalog_page_02' : 'rpt_catalog_page_01',
  832 |               secondPage ? '第二页报告' : '第一页报告',
  833 |             ),
  834 |           ],
  835 |           page: {
  836 |             next_cursor: secondPage ? null : 'rpt_catalog_page_01',
  837 |             has_more: !secondPage,
  838 |           },
  839 |         }),
  840 |       });
  841 |       return;
  842 |     }
  843 |     if (path.endsWith('/rpt_catalog_page_02')) {
  844 |       secondPageDetailRequests += 1;
  845 |       await new Promise((resolve) => setTimeout(resolve, 700));
  846 |       await route.fulfill({
  847 |         status: 200,
  848 |         contentType: 'application/json',
  849 |         body: JSON.stringify(
  850 |           reportDetail(
  851 |             'rpt_catalog_page_02',
  852 |             '第二页报告',
  853 |             '不应覆盖的第二页报告正文。',
  854 |             projectPubId,
  855 |             { token: 'Bearer stale-report-detail-canary' },
  856 |           ),
  857 |         ),
  858 |       });
  859 |       return;
  860 |     }
  861 |     await route.fulfill({
  862 |       status: 200,
  863 |       contentType: 'application/json',
  864 |       body: JSON.stringify(
  865 |         reportDetail('rpt_catalog_page_01', '第一页报告', '当前第一页报告正文。'),
  866 |       ),
  867 |     });
  868 |   });
  869 | 
  870 |   await page.goto('/platform/reports/');
  871 |   await expect(page.getByRole('heading', { name: '第一页报告' })).toBeVisible();
  872 |   await page.getByRole('button', { name: '下一页' }).click();
  873 |   await expect.poll(() => secondPageDetailRequests).toBe(1);
  874 |   await page.goBack();
  875 |   await expect(page).not.toHaveURL(/report_(?:page|cursor)=/);
  876 |   await expect(page.getByRole('heading', { name: '第一页报告' })).toBeVisible();
  877 |   await page.waitForTimeout(850);
  878 |   await page.getByRole('button', { name: '章节编辑' }).click();
  879 |   await expect(page.getByLabel('真实章节正文')).toHaveValue('当前第一页报告正文。');
  880 |   await expect(page.getByLabel('真实章节正文')).not.toHaveValue('不应覆盖的第二页报告正文。');
  881 |   const surfaces = await page.evaluate(() => ({
  882 |     dom: document.documentElement.outerHTML,
  883 |     url: location.href,
  884 |     localStorage: { ...localStorage },
  885 |     sessionStorage: { ...sessionStorage },
  886 |   }));
  887 |   expect(JSON.stringify(surfaces)).not.toMatch(/stale-report-detail-canary|Bearer /i);
  888 |   await expectAccessible(page);
  889 | });
  890 | 
```