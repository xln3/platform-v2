import { expectAccessible } from './accessibility';
import { buildMediaPricesContractPayload, fulfillMediaPricesJson } from './media-prices-fixture';
import { expect, test } from './runtime-fixture';

test('anonymous visitors can read media prices without receiving operator controls', async ({
  page,
}) => {
  let refreshStatusReads = 0;
  let postingReads = 0;
  await page.route('**/api/v2/datasets/media-prices', (route) =>
    fulfillMediaPricesJson(route, buildMediaPricesContractPayload(3)),
  );
  await page.route('**/api/v2/datasets/media-prices/refresh-status', (route) => {
    refreshStatusReads += 1;
    return route.abort();
  });
  await page.route('**/api/v2/posting/**', (route) => {
    postingReads += 1;
    return route.abort();
  });

  await page.goto('/platform/operations/media-prices');

  await expect(page.getByRole('heading', { name: '媒体比价台', exact: true })).toBeVisible();
  await expect(page.getByText('人民网', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '内部人员登录' })).toHaveAttribute(
    'href',
    '/platform/operations/login',
  );
  await expect(page.getByRole('button', { name: '刷新数据' })).toHaveCount(0);
  await expect(page.getByRole('columnheader', { name: '选择' })).toHaveCount(0);
  expect(refreshStatusReads).toBe(0);
  expect(postingReads).toBe(0);
  await expectAccessible(page);
});

test('the operations login page reports rejected email credentials in place', async ({ page }) => {
  await page.addInitScript(() => {
    const nativeFetch = globalThis.fetch.bind(globalThis);
    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const requestUrl =
        typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (
        new URL(requestUrl, globalThis.location.origin).pathname === '/api/v2/identity/login' &&
        (init?.method ?? 'GET').toUpperCase() === 'POST'
      ) {
        const response = new Response(JSON.stringify({ error: 'invalid_credentials' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
        });
        Object.defineProperties(response, {
          status: { value: 401 },
          ok: { value: false },
        });
        return response;
      }
      return nativeFetch(input, init);
    };
  });

  await page.goto('/platform/operations/login');
  await page.getByLabel('邮箱').fill('operator@example.com');
  await page.getByLabel('密码').fill('wrong-test-password');
  await page.getByRole('button', { name: '登录并进入运营工作台' }).click();

  await expect(page.getByRole('alert')).toHaveText('邮箱或密码错误。');
  await expect(page).toHaveURL(/\/platform\/operations\/login$/);
  await expectAccessible(page);
});
