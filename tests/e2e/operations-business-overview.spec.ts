import { expectAccessible, prepareApp } from './accessibility';
import { expect, test } from './runtime-fixture';

test('business overview keeps 4 + 1 projects reachable, restores URL filters, and stays read-only', async ({
  page,
}) => {
  let lifecycleReads = 0;
  let contractExports = 0;
  let apiWrites = 0;
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname === '/api/v2/operations/lifecycle') lifecycleReads += 1;
    if (url.pathname.endsWith('/contract.docx')) contractExports += 1;
    if (
      url.pathname.startsWith('/api/v2/') &&
      !['GET', 'HEAD', 'OPTIONS'].includes(request.method())
    ) {
      apiWrites += 1;
    }
  });

  await prepareApp(page, '/platform/operations/?section=overview');
  await expect(page.getByRole('heading', { name: '项目商务总览', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: '项目组合', exact: true })).toBeVisible();
  await expect(page.getByText('1–4 / 5', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '华东品牌增长', exact: true })).toBeVisible();
  await expect(page.getByText('历史项目归档', { exact: true })).toHaveCount(0);
  await expect(page.getByText('未购买', { exact: true })).toHaveCount(0);
  await expect(
    page.getByText(/系统目前未保存可查询的报价历史、已签合同、开票应收与回款台账/),
  ).toBeVisible();
  expect(lifecycleReads).toBe(0);
  expect(contractExports).toBe(0);
  expect(apiWrites).toBe(0);

  const viewport = page.viewportSize();
  if (viewport && viewport.width <= 620) {
    await expect(page.locator('.business-project-cards')).toBeVisible();
    await expect(page.locator('.business-project-card')).toHaveCount(4);
    await expect(page.locator('.business-project-table')).toBeHidden();
  } else {
    await expect(page.locator('.business-project-table')).toBeVisible();
    await expect(page.locator('.business-project-table tbody tr')).toHaveCount(4);
  }

  await expect(page.getByRole('link', { name: '星河科技', exact: true })).toHaveAttribute(
    'href',
    '/platform/operations/onboarding?project=prj_fixture_business_05',
  );
  await expect(page.getByRole('link', { name: '华东品牌增长', exact: true })).toHaveAttribute(
    'href',
    '/platform/operations/sop/projects/prj_fixture_business_05',
  );
  const attentionHrefs = await page
    .getByRole('link', { name: '前往处理', exact: true })
    .evaluateAll((links) => links.map((link) => link.getAttribute('href')));
  expect(attentionHrefs).toContain(
    '/platform/operations/formal-reports?project=prj_fixture_business_03',
  );
  expect(attentionHrefs).toContain(
    '/platform/operations/execution?project=prj_fixture_business_02',
  );
  await expect(
    page.getByRole('link', { name: '正式报告生成', exact: true }).last(),
  ).toHaveAttribute('href', '/platform/operations/formal-reports');
  await expect(page.getByRole('link', { name: '运行会话', exact: true })).toHaveAttribute(
    'href',
    '/platform/operations/execution#platform-accounts',
  );

  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page.getByText('5–5 / 5', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '历史项目归档', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '上一页', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '上一页', exact: true }).click();
  await expect(page.getByText('1–4 / 5', { exact: true })).toBeVisible();

  await page.getByLabel('项目状态').selectOption('draft');
  await expect(page).toHaveURL(/project_state=draft/);
  await expect(page.getByRole('link', { name: '新品首版评测', exact: true })).toBeVisible();
  await page.getByLabel('客户或项目').fill('远山制造');
  await page.getByRole('button', { name: '搜索', exact: true }).click();
  await expect(page).toHaveURL(/q=%E8%BF%9C%E5%B1%B1%E5%88%B6%E9%80%A0/);
  await page.reload();
  await expect(page.getByLabel('项目状态')).toHaveValue('draft');
  await expect(page.getByLabel('客户或项目')).toHaveValue('远山制造');
  await expect(page.getByText('1–1 / 1', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: '清除筛选', exact: true }).click();
  await expect(page).not.toHaveURL(/project_state=|[?&]q=/);
  await expect(page.getByText('1–4 / 5', { exact: true })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    .toBe(true);
  await expectAccessible(page);
});
