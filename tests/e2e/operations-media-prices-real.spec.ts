import { expectAccessible } from './accessibility';
import { expect, test } from './runtime-fixture';

// @live-api 车道：本用例 POST 真实 /api/v2/identity/bootstrap（需可写 API+库，静态 harness 的
// /api 代理目标 45200 在 CI/本地均无监听）。CI browser-e2e 以 --grep-invert @live-api 显式排除；
// 本地有 API 时可直接跑。待 dedicated live-API e2e 车道立项后归位。
test('media prices consumes the real 20k-row API artifact through the generated boundary @live-api', async ({
  page,
  request,
}) => {
  const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
  const subject = `media-prices-real-${suffix}`;
  const bootstrap = await request.post('/api/v2/identity/bootstrap', {
    headers: { 'X-Bootstrap-Secret': 'development-bootstrap' },
    data: {
      tenant_name: `Media Prices Real ${suffix}`,
      subject,
      display_name: 'Media Prices Real Operator',
    },
  });
  expect(bootstrap.ok()).toBeTruthy();
  const identity = (await bootstrap.json()) as {
    tenant_pub_id: string;
    user_pub_id: string;
  };
  await page.addInitScript(
    (session) => {
      localStorage.setItem('geo.session.tenant', session.tenant);
      localStorage.setItem('geo.session.actor', session.actor);
      localStorage.setItem('geo.session.role', 'admin');
      const nativeFetch = globalThis.fetch.bind(globalThis);
      Reflect.set(globalThis, '__geoRealMediaDatasetReads', 0);
      globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const requestUrl =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (
          new URL(requestUrl, globalThis.location.origin).pathname ===
          '/api/v2/datasets/media-prices'
        ) {
          Reflect.set(
            globalThis,
            '__geoRealMediaDatasetReads',
            Number(Reflect.get(globalThis, '__geoRealMediaDatasetReads')) + 1,
          );
        }
        return nativeFetch(input, init);
      };
    },
    { tenant: identity.tenant_pub_id, actor: subject },
  );

  const artifactResponse = await request.get('/api/v2/datasets/media-prices', {
    headers: {
      'X-Tenant-Id': identity.tenant_pub_id,
      'X-Actor-Id': subject,
      'X-Actor-Role': 'admin',
    },
  });
  expect(artifactResponse.status()).toBe(200);
  expect(artifactResponse.headers()['cache-control']).toBe('private, no-store');
  expect(artifactResponse.headers()['x-content-type-options']).toBe('nosniff');
  expect(artifactResponse.headers()['x-dataset-sha256']).toMatch(/^[0-9a-f]{64}$/);
  await artifactResponse.dispose();

  const startedAt = Date.now();
  await page.goto('/platform/operations/media-prices');

  const uniqueMediaMetric = page
    .locator('.metric-row article')
    .filter({ hasText: '去重总数' })
    .locator('strong');
  await expect(uniqueMediaMetric).toBeVisible();
  const uniqueMedia = Number(await uniqueMediaMetric.textContent());
  expect(uniqueMedia).toBeGreaterThan(20_000);
  await expect(page.locator('.media-prices-summary')).toContainText(`筛选 ${uniqueMedia} 条`);
  await expect(page.locator('.media-prices-table tbody tr')).toHaveCount(100);
  await expect(page.locator('.metric-refresh small')).not.toHaveText('正在读取刷新状态…');
  expect(Date.now() - startedAt).toBeLessThan(20_000);
  expect(
    await page.evaluate(() => Number(Reflect.get(globalThis, '__geoRealMediaDatasetReads'))),
  ).toBe(1);

  await page.getByLabel('仅 GEO').click();
  await expect(page).toHaveURL(/media_geo_only=1/);
  await page.goBack();
  await expect(page).not.toHaveURL(/media_geo_only=1/);
  expect(
    await page.evaluate(() => Number(Reflect.get(globalThis, '__geoRealMediaDatasetReads'))),
  ).toBe(1);

  const surfaces = await page.evaluate(() => ({
    url: window.location.href,
    body: document.body.textContent ?? '',
    localStorage: JSON.stringify(window.localStorage),
    sessionStorage: JSON.stringify(window.sessionStorage),
    history: JSON.stringify(window.history.state),
  }));
  expect(JSON.stringify(surfaces)).not.toMatch(
    /(?:Bearer\s+|Cookie\s*=|access_token|profile_path|OTP\s+\d{6}|1[3-9]\d{9})/i,
  );
  await expectAccessible(page);
});
