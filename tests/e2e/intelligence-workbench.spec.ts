import { expect, test } from '@playwright/test';
import { readDownload, secretArtifactPattern } from './downloads';

test('intelligence workbench traces claims, graph, history, verdict and appeal', async ({
  page,
}, testInfo) => {
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('requestfailed', (request) =>
    failedRequests.push(`${request.method()} ${request.url()}`),
  );
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
  await page.goto('/platform/intelligence/');

  await page.getByRole('button', { name: 'Claim 矩阵' }).click();
  await expect(page.getByText(/独立一手来源不足 2 个/)).toBeVisible();
  await page.getByRole('button', { name: '多源证据' }).click();
  await page.getByLabel('筛选同源簇').selectOption('C-07');
  await expect(page.getByText('同源传播')).toHaveCount(2);
  await page.getByRole('button', { name: '检查证据锚点' }).first().click();
  await expect(page.getByRole('dialog')).toContainText('字符 112–168');
  await expect(page.getByRole('img', { name: /锚点 bbox 84,176,310,42/ })).toBeVisible();
  await page.getByRole('button', { name: '标记锚点已核验' }).click();
  await expect(page.getByRole('status')).toContainText('核验事件绑定页面 hash');
  await page.getByRole('button', { name: '关闭对话框' }).click();
  await expect(page.getByText('锚点已核验', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: '传播关系' }).click();
  await expect(page.getByRole('table', { name: '传播图节点与关系' })).toBeVisible();
  await expect(page.getByText('相似度 0.91')).toBeVisible();
  await page.getByRole('button', { name: '页面历史' }).click();
  await expect(page.getByText('bbox 84,176,310,42')).toBeVisible();

  await page.getByRole('button', { name: /裁决与申诉/ }).click();
  await page.getByRole('button', { name: '确认高风险表述' }).click();
  await page.getByLabel('申诉理由').fill('请使用 token: Bearer dlp-canary 复核');
  await expect(page.getByText(/请勿在申诉中粘贴验证码/)).toBeVisible();
  await expect(page.getByRole('button', { name: '提交申诉' })).toBeDisabled();
  await page.getByLabel('申诉理由').fill('新增登记材料需要重新复核');
  await page.getByRole('button', { name: '提交申诉' }).click();
  await expect(page.getByText(/原裁决保持可追溯/)).toBeVisible();
  await page.getByRole('button', { name: '记录二次复核' }).click();
  await expect(page.getByText('reviewed').first()).toBeVisible();

  await page.getByRole('button', { name: '证据包' }).click();
  const packageDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '生成并下载 manifest' }).click();
  const packageArtifact = await packageDownload;
  expect(packageArtifact.suggestedFilename()).toBe('CASE-2407-evidence-manifest.json');
  const packageContent = await readDownload(packageArtifact);
  expect(JSON.parse(packageContent)).toEqual({
    case_id: 'CASE-2407',
    verdict: 'reviewed',
    rule_version: 'intelligence-v2.3',
    evidence_count: 4,
    generated_at: '2026-07-24T16:00:00+08:00',
  });
  expect(packageContent).not.toMatch(secretArtifactPattern);
  await expect(page.getByRole('status')).toContainText('4 项完整性检查通过');

  expect(consoleErrors, consoleErrors.join('\n')).toEqual([]);
  expect(failedRequests, failedRequests.join('\n')).toEqual([]);
  const viewportName = testInfo.project.name.replace('intelligence-', '');
  await page.screenshot({
    path: `tests/e2e-results/intelligence-workbench-${viewportName}.png`,
    fullPage: true,
  });
});
