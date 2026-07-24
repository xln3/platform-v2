import { expect, test } from '@playwright/test';
import { prepareVisualPage } from './visual-regression';

const workspaces = [
  { section: 'home', snapshot: 'customer-home.png', ready: '监测运行中' },
  { section: 'profile', snapshot: 'customer-profile.png', ready: '甲方资料' },
  { section: 'assets', snapshot: 'customer-brand-assets.png', ready: '品牌、产品与竞品' },
  {
    section: 'questions',
    snapshot: 'customer-questions-goals.png',
    ready: '问题、目标与配置申请',
  },
  { section: 'monitoring', snapshot: 'customer-monitoring-dimensions.png', ready: '模型表现' },
  {
    section: 'evidence',
    snapshot: 'customer-answers-evidence.png',
    ready: '企业知识库如何选择？',
  },
  {
    section: 'reports',
    snapshot: 'customer-reports.png',
    ready: '2026 Q3 GEO 监测与优化建议',
  },
  { section: 'members', snapshot: 'customer-members.png', ready: '项目成员' },
  { section: 'accounts', snapshot: 'customer-account-safety.png', ready: '平台账号与授权' },
] as const;

for (const workspace of workspaces) {
  test(`customer ${workspace.section} visual baseline has no page overflow`, async ({ page }) => {
    const expectCleanRuntime = await prepareVisualPage(
      page,
      `/platform/customer/?section=${workspace.section}`,
    );
    await page.getByRole('heading', { name: workspace.ready, exact: true }).first().waitFor();
    if (workspace.section === 'monitoring') {
      await page.getByText('近五个冻结日品牌提及率趋势图表已渲染').waitFor();
    }
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
      .toBe(true);
    await expect(page).toHaveScreenshot(workspace.snapshot, {
      fullPage: true,
      animations: 'disabled',
      maxDiffPixelRatio: 0.005,
    });
    expectCleanRuntime();
  });
}

test('customer secure pairing QR and native challenge have responsive visual baselines', async ({
  page,
}) => {
  const expectCleanRuntime = await prepareVisualPage(page, '/platform/customer/?section=accounts');
  await page.getByRole('button', { name: '创建一次性配对' }).click();
  await page.getByRole('button', { name: '确认并生成配对码' }).click();
  await page.getByRole('img', { name: /一次性安全配对二维码/ }).waitFor();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    .toBe(true);
  await expect(page).toHaveScreenshot('customer-account-pairing-qr.png', {
    fullPage: true,
    animations: 'disabled',
    maxDiffPixelRatio: 0.005,
  });

  await page.getByRole('button', { name: '终端已连接' }).click();
  await page.getByRole('heading', { name: '请在豆包原生页面完成验证' }).waitFor();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    .toBe(true);
  await expect(page).toHaveScreenshot('customer-account-native-challenge.png', {
    fullPage: true,
    animations: 'disabled',
    maxDiffPixelRatio: 0.005,
  });
  expectCleanRuntime();
});
