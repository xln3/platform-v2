import { expect, test } from './runtime-fixture';
import { captureSafeScreenshot } from './screenshot-safety';

test('report studio freezes, edits, binds evidence, reviews, publishes and records outcomes', async ({
  page,
}, testInfo) => {
  await page.route('**/api/v2/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'mock-ready',
        service: 'geo-platform-v2',
        version: 'contract-v1',
      }),
    }),
  );
  await page.goto('/platform/reports/');

  await page.getByRole('button', { name: '冻结事实并创建 v0.8' }).click();
  await expect(page.getByText('事实已冻结').first()).toBeVisible();
  await page.getByRole('button', { name: 'KPI Trace' }).click();
  await page.getByRole('button', { name: /Top 3 占比/ }).click();
  await expect(page.getByText('ans_03 · rank 1')).toBeVisible();
  await page.getByRole('button', { name: '打开贡献证据' }).click();
  await expect(page.getByRole('region', { name: 'Top 3 占比贡献证据' })).toContainText(
    '证据版本已冻结',
  );

  await page.getByRole('button', { name: '章节编辑' }).click();
  await page.getByRole('button', { name: /模型差异分析/ }).click();
  await page.getByLabel('章节正文').fill('请粘贴验证码 824911 完成人工确认');
  await expect(page.getByText(/请移除验证码、Cookie、token/)).toBeVisible();
  await expect(page.getByRole('button', { name: '保存章节版本' })).toBeDisabled();
  await page.getByLabel('章节正文').fill('DeepSeek 的引用覆盖更高，模型差异需要结合证据复核。');
  await expect(page.getByText('人工内容')).toBeVisible();
  await page.getByRole('button', { name: '保存章节版本' }).click();
  await expect(
    page.getByRole('status').filter({ hasText: 'v1 已保存，正文快照不可变' }),
  ).toBeVisible();
  await page.getByLabel('章节正文').fill('DeepSeek 的引用覆盖更高；模型差异需要结合独立证据复核。');
  await page.getByRole('button', { name: '保存章节版本' }).click();
  await expect(
    page.getByRole('status').filter({ hasText: 'v2 已保存，正文快照不可变' }),
  ).toBeVisible();
  const versionHistory = page.getByRole('list', { name: '模型差异分析章节版本历史' });
  await expect(versionHistory.getByText(/v1/)).toBeVisible();
  await expect(versionHistory.getByText(/v2/)).toBeVisible();

  await page.getByRole('button', { name: '版本对比' }).click();
  await page.getByLabel('对比章节').selectOption('model');
  await expect(page.getByRole('article', { name: '模型差异分析 v1 与 v2 正文差异' })).toContainText(
    '独立证据',
  );
  const versionDiff = page.getByRole('article', {
    name: '模型差异分析 v1 与 v2 正文差异',
  });
  await expect(versionDiff.locator('del')).toHaveText('，');
  await expect(versionDiff.locator('ins')).toHaveText(['；', '独立']);
  await expect(
    page.getByRole('status').filter({ hasText: '已对比 v1 → v2；删除 1 字，新增 3 字' }),
  ).toBeVisible();
  await page.getByLabel('基准版本').selectOption('2');
  await expect(
    page.getByRole('status').filter({ hasText: '所选版本相同，正文无差异' }),
  ).toBeVisible();

  await page.getByRole('button', { name: '证据编排' }).click();
  await expect(page.getByRole('img', { name: /品牌提及锚点/ })).toBeVisible();
  await page.getByRole('button', { name: '调整锚点' }).click();
  await page.getByRole('button', { name: '左移' }).click();
  await expect(page.getByRole('group', { name: '锚点位置微调' }).getByRole('status')).toContainText(
    'bbox 237,118,238,52',
  );
  await expect(page.getByRole('img', { name: /坐标 237,118/ })).toBeVisible();
  await page.getByRole('button', { name: '完成锚点调整' }).click();
  await page.getByRole('button', { name: '绑定到“执行摘要”' }).click();
  await expect(page.getByText('绑定成功')).toBeVisible();

  await page.getByRole('button', { name: 'PDF 预览' }).click();
  await expect(page.getByLabel('报告预览第 1 页')).toBeVisible();
  await expect(page.getByText('PDF.js 已渲染第 1 页')).toBeAttached();
  await page.getByRole('button', { name: '100%' }).click();
  await expect(page.getByRole('button', { name: '100%' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.pdf-canvas-wrap')).toHaveAttribute('data-zoom', '100');
  await page.getByRole('button', { name: '适合页面' }).click();
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page.getByLabel('报告预览第 2 页')).toBeVisible();
  await expect(page.getByText('PDF.js 已渲染第 2 页')).toBeAttached();

  await page.getByRole('button', { name: /审核发布/ }).click();
  await page.getByRole('button', { name: '提交审核' }).click();
  await expect(page.getByRole('button', { name: '批准发布' })).toBeDisabled();
  await page.getByLabel('新增评论').fill('Cookie=SESSION-dlp-canary');
  await expect(page.getByText(/请勿在评论中粘贴验证码/)).toBeVisible();
  await expect(page.getByRole('button', { name: '添加评论' })).toBeDisabled();
  await page.getByLabel('新增评论').fill('');
  await page.getByRole('button', { name: '纳入本次审核' }).click();
  await page.getByRole('button', { name: '批准发布' }).click();
  await page.getByRole('button', { name: '发布 v1.0' }).click();
  await expect(page.getByText('在线版已生成；客户可见性以独立 delivery 记录为准。')).toBeVisible();

  await page.getByRole('button', { name: '效果复盘' }).click();
  await page.getByRole('button', { name: '开始执行' }).click();
  await page.getByRole('button', { name: '记录复测效果' }).click();
  await expect(page.getByText('+6.2pp')).toBeVisible();

  const viewportName = testInfo.project.name.replace('reports-', '');
  await captureSafeScreenshot(page, {
    path: `tests/e2e-results/report-studio-${viewportName}.png`,
    fullPage: true,
  });
});
