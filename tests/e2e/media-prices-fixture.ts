import type { Page, Route } from './runtime-fixture';

export async function installOperationsMediaIdentity(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_media_prices_live');
    localStorage.setItem('geo.session.actor', 'operator-media-prices-live');
    localStorage.setItem('geo.session.role', 'operator');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_media_prices_live',
        user_pub_id: 'usr_media_prices_live',
        role: 'operator',
        permissions: ['account:read'],
      }),
    }),
  );
  await page.route('**/api/v2/projects**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            pub_id: 'prj_media_prices_live',
            tenant_pub_id: 'tnt_media_prices_live',
            name: '媒体成本运营项目',
            state: 'active',
            created_at: '2026-07-27T08:00:00Z',
            updated_at: '2026-07-27T08:00:00Z',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );
  await page.route('**/api/v2/datasets/media-prices/refresh-status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: {
        'cache-control': 'private, no-store',
        'x-content-type-options': 'nosniff',
      },
      body: JSON.stringify({
        state: 'never',
        started_at: null,
        updated_at: null,
        message: '',
        sources: {},
      }),
    }),
  );
  await page.route('**/api/v2/posting/batches**', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '[]',
    });
  });
}

export function buildMediaPricesContractPayload(rowCount = 3) {
  const seedRows = [
    {
      name: '人民网',
      prices: { prfabu: 100, toumeiw: 80 },
      best: 80,
      best_plat: 'toumeiw',
      spread: 1.3,
      n_src: 2,
      geo: ['b'],
      geo_n: 1,
      portal: '门户网站',
      channel: '新闻',
      include: '收录',
      pc_w: 9,
      pub_rate: 98,
      remark: '全国综合媒体',
    },
    {
      name: '新华网',
      prices: { prfabu: 50 },
      best: 50,
      best_plat: 'prfabu',
      spread: null,
      n_src: 1,
      geo: [],
      geo_n: 0,
      portal: '门户网站',
      channel: '新闻',
      include: '收录',
      pc_w: 10,
      pub_rate: 99,
    },
    {
      name: '价差媒体',
      prices: { prfabu: 300, mtpfw: 90 },
      best: 90,
      best_plat: 'mtpfw',
      spread: 3.3,
      n_src: 2,
      geo: ['a', 'f'],
      geo_n: 1,
      portal: '客户端',
      channel: '商业',
      include: '包收录',
      pc_w: 7,
      pub_rate: 92,
      remark: '多源交叉样本',
    },
  ] as const;
  const rows = Array.from({ length: rowCount }, (_, index) => ({
    ...seedRows[index % seedRows.length],
    name:
      index < seedRows.length
        ? seedRows[index]!.name
        : `${seedRows[index % seedRows.length]!.name} ${String(index + 1).padStart(3, '0')}`,
  }));
  return {
    generated_at: '2026-07-27 16:30',
    sources: {
      prfabu: 'prfabu媒体管家',
      toumeiw: '投媒网',
      mtpfw: '媒体批发网',
      meititejia: '媒体特价网',
      meijiehezi: '媒介盒子',
      pinda: '品达发稿',
    },
    partial: {
      prfabu: false,
      toumeiw: false,
      mtpfw: false,
      meititejia: false,
      meijiehezi: false,
      pinda: false,
    },
    stats: {
      counts: {
        prfabu: rowCount,
        toumeiw: Math.ceil(rowCount / 3),
        mtpfw: Math.floor(rowCount / 3),
        meititejia: 0,
        meijiehezi: 0,
        pinda: 0,
      },
      geo_counts: {
        prfabu: Math.ceil(rowCount / 3),
        toumeiw: Math.ceil(rowCount / 3),
        mtpfw: Math.floor(rowCount / 3),
        meititejia: 0,
        meijiehezi: 0,
        pinda: 0,
      },
      unique_media: rowCount,
      matched_2plus: Math.ceil((rowCount * 2) / 3),
      matched_3: 0,
      geo_union: Math.ceil((rowCount * 2) / 3),
      geo_multi_src: 0,
    },
    rows,
  };
}

export async function fulfillMediaPricesJson(
  route: Route,
  body: unknown,
  status = 200,
  sha256Override?: string | null,
): Promise<void> {
  const encodedBody = JSON.stringify(body);
  const digest =
    sha256Override === undefined
      ? await globalThis.crypto.subtle.digest(
          'SHA-256',
          new TextEncoder().encode(encodedBody).buffer as ArrayBuffer,
        )
      : null;
  const sha256 =
    sha256Override === undefined
      ? [...new Uint8Array(digest!)].map((value) => value.toString(16).padStart(2, '0')).join('')
      : sha256Override;
  await route.fulfill({
    status,
    contentType: 'application/json',
    headers: {
      'cache-control': 'private, no-store',
      ...(sha256 ? { 'x-dataset-sha256': sha256 } : {}),
    },
    body: encodedBody,
  });
}

export async function routeReadyMediaPrices(page: Page, rowCount = 3): Promise<void> {
  await page.route('**/api/v2/datasets/media-prices', (route) =>
    fulfillMediaPricesJson(route, buildMediaPricesContractPayload(rowCount)),
  );
}
