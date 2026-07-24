import { expect, test } from '@playwright/test';
import { prepareApp } from './accessibility';

test('operations shell exposes the safe account lifecycle without secrets', async ({ page }) => {
  const expectCleanRuntime = await prepareApp(page, '/platform/operations/');

  await page.getByRole('button', { name: /会话健康/ }).click();
  await expect(page.getByRole('heading', { name: '授权、租约与会话健康' })).toBeVisible();
  await expect(page.getByText('适配器就绪 · 未经 live 验证')).toBeVisible();
  await expect(page.getByText('已撤销', { exact: true }).first()).toBeVisible();

  await page.getByRole('button', { name: /待人工/ }).click();
  await expect(page.getByRole('heading', { name: '人工接管队列' })).toBeVisible();
  await expect(page.getByText('等待客户扫码')).toBeVisible();
  await expect(page.getByText('Push MFA')).toBeVisible();

  await page.getByRole('button', { name: '事件审计' }).click();
  await expect(page.getByRole('heading', { name: '账号生命周期事件' })).toBeVisible();
  await expect(page.getByText('授权即将到期')).toBeVisible();
  await expect(page.getByText('账号进入隔离')).toBeVisible();
  await expect(page.getByRole('article', { name: /撤销回执/ })).toBeVisible();

  const browserSurfaces = await page.evaluate(() => ({
    body: document.body.textContent ?? '',
    url: location.href,
    localStorage: JSON.stringify(localStorage),
    sessionStorage: JSON.stringify(sessionStorage),
  }));
  const serialized = JSON.stringify(browserSurfaces);
  for (const forbidden of [
    'Cookie:',
    'Bearer ',
    'proxy-password',
    '/tmp/browser-profile',
    'human_verified_token',
    '13800138000',
  ]) {
    expect(serialized).not.toContain(forbidden);
  }

  expectCleanRuntime();
});
