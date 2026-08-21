import { expect, test } from './runtime-fixture';
import { expectSafePageScreenshot } from './screenshot-safety';
import { prepareVisualPage } from './visual-regression';

const workspaces = [
  { section: 'cases', snapshot: 'intelligence-cases.png', ready: '调查案件' },
  { section: 'claims', snapshot: 'intelligence-claims.png', ready: 'Claim × Evidence 矩阵' },
  { section: 'sources', snapshot: 'intelligence-sources.png', ready: '多源证据与同源簇' },
  {
    section: 'source-insight',
    snapshot: 'intelligence-source-insight.png',
    ready: '信源洞察',
  },
  { section: 'graph', snapshot: 'intelligence-propagation.png', ready: '内容传播关系' },
  { section: 'history', snapshot: 'intelligence-history-diff.png', ready: '页面历史与视觉 Diff' },
  { section: 'calibration', snapshot: 'intelligence-model-admission.png', ready: '模型校准与准入' },
  { section: 'verdict', snapshot: 'intelligence-verdict-appeal.png', ready: '人工裁决' },
  { section: 'package', snapshot: 'intelligence-evidence-package.png', ready: '证据包' },
] as const;

for (const workspace of workspaces) {
  test(`intelligence ${workspace.section} visual baseline has no page overflow`, async ({
    page,
  }) => {
    await prepareVisualPage(page, `/platform/intelligence/?section=${workspace.section}`);
    await page.getByRole('heading', { name: workspace.ready, exact: true }).waitFor();
    if (workspace.section === 'graph') {
      await page.locator('.react-flow__node').first().waitFor();
    }
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
      .toBe(true);
    await expectSafePageScreenshot(page, workspace.snapshot, {
      fullPage: true,
      animations: 'disabled',
      maxDiffPixelRatio: 0.005,
    });
  });
}

test('intelligence source insight W review visual baseline has no page overflow', async ({
  page,
}) => {
  await prepareVisualPage(page, '/platform/intelligence/?section=source-insight');
  await page.getByRole('heading', { name: '信源洞察', exact: true }).waitFor();
  await page.getByRole('button', { name: '查看 URL' }).first().click();
  await page.getByRole('button', { name: '查看详情' }).first().click();
  await page.getByRole('heading', { name: 'W 内容片段、版本与人工复核' }).waitFor();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    .toBe(true);
  await expectSafePageScreenshot(page, 'intelligence-source-insight-w-review.png', {
    fullPage: true,
    animations: 'disabled',
    maxDiffPixelRatio: 0.005,
  });
});
