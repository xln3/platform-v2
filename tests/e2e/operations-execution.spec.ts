import { expect, test } from '@playwright/test';
import path from 'node:path';

const session = {
  tenant: 'tnt_6FGT8JGH9ASAQ7B1P87R6VHKNE',
  actor: 's01-e2e-664fd9bb30',
  role: 'admin',
};

test('operations execution uses real lifecycle APIs without secret leakage', async ({
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
  await page.addInitScript((context) => {
    localStorage.setItem('geo.ops.tenant', context.tenant);
    localStorage.setItem('geo.ops.actor', context.actor);
    localStorage.setItem('geo.ops.role', context.role);
  }, session);
  await page.goto('/platform/operations/execution');
  await expect(page.getByRole('heading', { name: '执行与账号控制面' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'fixture-***42' })).toBeVisible();
  await expect(page.getByText('adapter_ready').first()).toBeVisible();
  await expect(page.getByText('尚未 live 验证').first()).toBeVisible();
  await expect(page.getByRole('heading', { name: '运行与任务矩阵' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '人工接管队列' })).toBeVisible();
  const healthResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      response.url().includes('/health-checks') &&
      !response.url().includes('live_canary=true'),
  );
  await page.getByRole('button', { name: 'L0–L3 健康检查' }).click();
  const health = await healthResponse;
  expect(health.ok()).toBeTruthy();
  expect((await health.json()).levels.L0).toBe('passed');
  await expect(page.getByText('health_check.completed').first()).toBeVisible();

  const surfaces = await page.evaluate(() => ({
    url: location.href,
    body: document.body.textContent ?? '',
    localStorage: JSON.stringify(localStorage),
    sessionStorage: JSON.stringify(sessionStorage),
  }));
  const rendered = JSON.stringify(surfaces);
  for (const canary of [
    'sid=secret',
    'Bearer secret',
    '/tmp/browser-profile',
    'proxy-password',
    'human_verified_token',
  ]) {
    expect(rendered).not.toContain(canary);
  }
  expect(consoleErrors, consoleErrors.join('\n')).toEqual([]);
  expect(failedRequests, failedRequests.join('\n')).toEqual([]);
  await page.screenshot({
    path: path.resolve(
      process.cwd(),
      `tests/visual-evidence/s01/operations-execution-${testInfo.project.name}.png`,
    ),
    fullPage: true,
  });
});
