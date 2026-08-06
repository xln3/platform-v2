import { expect, test } from './runtime-fixture';
import { expectSafePageScreenshot } from './screenshot-safety';
import { prepareVisualPage } from './visual-regression';

const workspaces = [
  { section: 'window', snapshot: 'report-window.png', ready: '数据窗口与事实冻结' },
  { section: 'trace', snapshot: 'report-kpi-trace.png', ready: 'KPI Trace' },
  { section: 'editor', snapshot: 'report-section-editor.png', ready: '报告章节' },
  { section: 'diff', snapshot: 'report-version-diff.png', ready: '章节版本对比' },
  { section: 'evidence', snapshot: 'report-evidence-editor.png', ready: '图表与证据编辑' },
  { section: 'preview', snapshot: 'report-pdf-preview.png', ready: 'PDF.js 已渲染第 1 页' },
  { section: 'review', snapshot: 'report-review-publish.png', ready: '审核与发布门' },
  { section: 'outcomes', snapshot: 'report-outcomes.png', ready: '优化建议与效果复盘' },
] as const;

for (const workspace of workspaces) {
  test(`report ${workspace.section} visual baseline has no page overflow`, async ({ page }) => {
    await prepareVisualPage(page, `/platform/reports/?section=${workspace.section}`);
    if (workspace.section === 'preview') {
      await page.getByText(workspace.ready, { exact: false }).first().waitFor();
    } else {
      await page.getByRole('heading', { name: workspace.ready, exact: true }).waitFor();
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
