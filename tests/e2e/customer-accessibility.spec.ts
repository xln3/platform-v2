import { expect, test } from './runtime-fixture';
import {
  expectAccessible,
  expectSharedInteractionAccessibility,
  prepareApp,
} from './accessibility';

test('customer shell is keyboard reachable and WCAG AA clean', async ({ page }) => {
  await prepareApp(page, '/platform/customer/');
  await expectAccessible(page);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: '跳到主要内容' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();
  await expectSharedInteractionAccessibility(page);
  // fixture 缺省为「已收起」回访态；展开 AI 操作面板也过一次可访问性闸（覆盖抽屉 summary 等控件）。
  await page.getByRole('button', { name: '展开 AI 面板' }).click();
  await expectAccessible(page);
  await page.getByRole('button', { name: '收起 AI 面板' }).click();
  for (const workspace of ['资料', '品牌产品', '问题目标', '信源与内容', '报告', '成员']) {
    await page.getByRole('button', { name: workspace, exact: true }).click();
    if (workspace === '报告') {
      await page.getByRole('button', { name: '在线预览' }).click();
      await expectAccessible(page);
      await page.keyboard.press('Escape');
    }
    if (workspace === '成员') {
      await page.getByRole('button', { name: '管理 林澄' }).click();
      await expectAccessible(page);
      await page.keyboard.press('Escape');
    }
    await expectAccessible(page);
  }
  await page.getByRole('button', { name: /平台账号/ }).click();
  await expectAccessible(page);
  await page.getByRole('button', { name: '查看撤销流程' }).click();
  await expectAccessible(page);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '创建一次性配对' }).click();
  await page.getByRole('button', { name: '确认并进入配对演示' }).click();
  await expect(page.getByRole('img', { name: /一次性安全配对二维码占位/ })).toBeVisible();
  await expectAccessible(page);
  await page.getByRole('button', { name: '终端已连接' }).click();
  await expect(page.getByRole('heading', { name: '请在豆包原生页面完成验证' })).toBeVisible();
  await expectAccessible(page);
  await page.getByRole('button', { name: '品牌可见度' }).click();
  await page.getByRole('heading', { name: '云岫智能 · 品牌可见度与模型表现' }).waitFor();
  await expectAccessible(page);
  await page.getByRole('button', { name: '回答明细' }).click();
  const evidenceTrigger = page.getByRole('button', { name: '查看回答截图' }).first();
  await evidenceTrigger.focus();
  await page.keyboard.press('Enter');
  const closeDialog = page.getByRole('button', { name: '关闭证据弹窗' });
  await expect(closeDialog).toBeFocused();
  await expectAccessible(page);
  await page.keyboard.press('Tab');
  await expect(closeDialog).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(evidenceTrigger).toBeFocused();
});
