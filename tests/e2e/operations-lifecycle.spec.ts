import { expect, test } from './runtime-fixture';
import { prepareApp } from './accessibility';

test('operations shell routes lifecycle entries to the cursor-paged execution plane', async ({
  page,
}) => {
  await prepareApp(page, '/platform/operations/');

  await expect(page.getByRole('link', { name: /会话健康/ })).toHaveAttribute(
    'href',
    /\/platform\/operations\/execution#platform-accounts/u,
  );
  await expect(page.getByRole('link', { name: /人工接管/ })).toHaveAttribute(
    'href',
    /\/platform\/operations\/execution#interventions/u,
  );
  await expect(page.getByRole('link', { name: /事件审计/ })).toHaveAttribute(
    'href',
    /\/platform\/operations\/execution#events/u,
  );

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
});
