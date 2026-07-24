import { expect, test } from '@playwright/test';
import { expectAccessible, prepareApp } from './accessibility';

test('customer shell is keyboard reachable and WCAG AA clean', async ({ page }) => {
  const expectCleanRuntime = await prepareApp(page, '/platform/customer/');
  await expectAccessible(page);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: '跳到主要内容' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();
  for (const workspace of ['资料', '品牌产品', '问题目标', '报告', '成员']) {
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
  await page.getByRole('button', { name: '确认并生成配对码' }).click();
  await expect(page.getByRole('img', { name: /一次性安全配对二维码/ })).toBeVisible();
  await expectAccessible(page);
  await page.getByRole('button', { name: '终端已连接' }).click();
  await expect(page.getByRole('heading', { name: '请在豆包原生页面完成验证' })).toBeVisible();
  await expectAccessible(page);
  await page.getByRole('button', { name: '监测表现' }).click();
  await page.getByText('近五个冻结日品牌提及率趋势图表已渲染').waitFor();
  await expectAccessible(page);
  await page.getByRole('button', { name: '回答证据' }).click();
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
  expectCleanRuntime();
});
