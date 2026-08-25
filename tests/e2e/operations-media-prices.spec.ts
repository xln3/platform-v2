import { expectAccessible } from './accessibility';
import {
  buildMediaPricesContractPayload,
  fulfillMediaPricesJson,
  installOperationsMediaIdentity,
  routeReadyMediaPrices,
} from './media-prices-fixture';
import { expect, test } from './runtime-fixture';
import { installSyntheticHttpResponses } from './synthetic-http';

test.beforeEach(async ({ page }) => {
  await installOperationsMediaIdentity(page);
});

test('media prices uses the shared shell, URL history, pagination and safe CSV export', async ({
  page,
}) => {
  let reads = 0;
  await page.route('**/api/v2/datasets/media-prices', async (route) => {
    reads += 1;
    await fulfillMediaPricesJson(route, buildMediaPricesContractPayload(103));
  });
  await page.goto('/platform/operations/media-prices?media_geo=a');

  await expect(page.getByRole('heading', { name: '媒体比价台', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '媒体比价台' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(page.getByRole('link', { name: '会话健康' })).toHaveAttribute(
    'href',
    '/platform/operations/execution?project=prj_media_prices_live#platform-accounts',
  );
  await expect(page.getByText('价差媒体', { exact: true })).toBeVisible();
  await expect(page.getByText('人民网', { exact: true })).toHaveCount(0);
  const readsAfterReady = reads;

  await page.getByRole('button', { name: 'DeepSeek' }).click();
  await expect(page).not.toHaveURL(/media_geo=/);
  await expect(page.getByText('人民网', { exact: true })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/media_geo=a/);
  await expect(page.getByText('人民网', { exact: true })).toHaveCount(0);
  expect(reads).toBe(readsAfterReady);

  await page.getByRole('button', { name: 'DeepSeek' }).click();
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/media_page=2/);
  await expect(page.getByRole('navigation', { name: '媒体比价分页' })).toContainText('第 2 / 2 页');

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出筛选 CSV' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('media-prices.csv');
  await expect(page.locator('.media-prices-notice')).toContainText('已导出 103 条筛选结果');

  await expectAccessible(page);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    .toBe(true);
});

test('media prices distinguishes missing, forbidden, failed retry and authoritative real zero', async ({
  page,
}) => {
  await installSyntheticHttpResponses(page, [
    {
      id: 'media-prices-missing',
      path: '/api/v2/datasets/media-prices',
      status: 404,
      body: { error: { code: 'dataset_not_found' } },
      remaining: 1,
    },
    {
      id: 'media-prices-forbidden',
      path: '/api/v2/datasets/media-prices',
      status: 403,
      body: { error: { code: 'permission_denied' } },
      remaining: 1,
    },
    {
      id: 'media-prices-failed',
      path: '/api/v2/datasets/media-prices',
      status: 500,
      body: { error: { code: 'temporary_failure' } },
      remaining: 1,
    },
  ]);
  await page.route('**/api/v2/datasets/media-prices', (route) =>
    fulfillMediaPricesJson(route, buildMediaPricesContractPayload(0)),
  );

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('数据集尚未生成，可直接在本页启动首次生成。')).toBeVisible();

  await page.reload();
  await expect(page.getByText('无权查看')).toBeVisible();

  await page.reload();
  await expect(page.getByText('加载失败')).toBeVisible();
  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('结果为 0')).toBeVisible();
  await expectAccessible(page);
});

test('media prices rejects secret-bearing API projections without rendering or persisting canaries', async ({
  page,
}) => {
  const payload = buildMediaPricesContractPayload(1);
  await page.route('**/api/v2/datasets/media-prices', (route) =>
    fulfillMediaPricesJson(route, {
      ...payload,
      access_token: 'dataset-envelope-canary',
      rows: [
        {
          ...payload.rows[0],
          remark: 'OTP 824911',
          Cookie: 'dataset-cookie-canary',
          profile_path: '/tmp/dataset-profile-canary',
        },
      ],
    }),
  );
  await page.goto(
    '/platform/operations/media-prices?media_q=Bearer%20url-canary&access_token=url-secret-canary',
  );

  await expect(page.getByText('人民网', { exact: true })).toBeVisible();
  const surfaces = await page.evaluate(() => ({
    url: window.location.href,
    body: document.body.textContent ?? '',
    localStorage: JSON.stringify(window.localStorage),
    sessionStorage: JSON.stringify(window.sessionStorage),
    history: JSON.stringify(window.history.state),
  }));
  expect(JSON.stringify(surfaces)).not.toMatch(
    /Bearer|url-canary|access_token|url-secret-canary|dataset-envelope-canary|824911|Cookie|dataset-cookie-canary|profile_path|dataset-profile-canary/i,
  );
  await expectAccessible(page);
});

test('media refresh status and write receipts fail closed on secret-shaped display fields', async ({
  page,
}) => {
  await routeReadyMediaPrices(page);
  await page.route('**/api/v2/datasets/media-prices/refresh-status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'done',
        started_at: '2026-07-27 16:00:00',
        updated_at: '2026-07-27 16:01:00',
        message: 'Bearer refresh-status-secret-canary',
        sources: {
          prfabu: { status: 'ok', rows: 3, note: 'OTP 824911' },
        },
      }),
    }),
  );
  await page.route('**/api/v2/datasets/media-prices/refresh', (route) =>
    route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'running',
        started_at: null,
        message: '/secret/browser/profile/refresh-write-canary',
        sources: {},
      }),
    }),
  );

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('人民网', { exact: true })).toBeVisible();
  await expect(page.getByText('刷新状态读取失败，当前状态未知。')).toBeVisible();
  await expect(page.getByText('尚未刷新')).toHaveCount(0);
  await page.getByRole('button', { name: '刷新数据' }).click();
  await expect(page.getByText('刷新请求失败，请稍后重试。')).toBeVisible();

  const surfaces = await page.evaluate(() => ({
    url: window.location.href,
    body: document.body.textContent ?? '',
    localStorage: JSON.stringify(window.localStorage),
    sessionStorage: JSON.stringify(window.sessionStorage),
    history: JSON.stringify(window.history.state),
  }));
  expect(JSON.stringify(surfaces)).not.toMatch(
    /refresh-status-secret-canary|824911|refresh-write-canary|Bearer|\/secret\/browser\/profile/i,
  );
  await expectAccessible(page);
});

test('media refresh status permission loss stays distinct and stops polling immediately', async ({
  page,
}) => {
  let refreshWrites = 0;
  await page.addInitScript(() => {
    const nativeFetch = globalThis.fetch.bind(globalThis);
    Reflect.set(globalThis, '__geoMediaRefreshStatusForbidden', false);
    Reflect.set(globalThis, '__geoMediaRefreshStatusForbiddenReads', 0);
    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const requestUrl =
        typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const pathname = new URL(requestUrl, globalThis.location.origin).pathname;
      if (
        Reflect.get(globalThis, '__geoMediaRefreshStatusForbidden') === true &&
        pathname === '/api/v2/datasets/media-prices/refresh-status'
      ) {
        Reflect.set(
          globalThis,
          '__geoMediaRefreshStatusForbiddenReads',
          Number(Reflect.get(globalThis, '__geoMediaRefreshStatusForbiddenReads')) + 1,
        );
        const response = new Response(JSON.stringify({ error: { code: 'permission_denied' } }), {
          status: 200,
          headers: { 'Cache-Control': 'no-store', 'Content-Type': 'application/json' },
        });
        Object.defineProperties(response, {
          status: { value: 403 },
          ok: { value: false },
          statusText: { value: '' },
        });
        return response;
      }
      return nativeFetch(input, init);
    };
  });
  await routeReadyMediaPrices(page);
  await page.route('**/api/v2/datasets/media-prices/refresh', (route) => {
    refreshWrites += 1;
    return route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'running',
        started_at: null,
        updated_at: null,
        message: '刷新已启动',
        sources: {},
      }),
    });
  });

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('尚未刷新')).toBeVisible();
  await page.evaluate(() => {
    Reflect.set(globalThis, '__geoMediaRefreshStatusForbidden', true);
  });
  await page.getByRole('button', { name: '刷新数据' }).click();
  await expect(page.getByText('权限不足：无法查看或启动数据刷新。')).toBeVisible();
  await expect(page.getByText('无权查看刷新状态')).toBeVisible();
  await expect(page.getByRole('button', { name: '刷新数据' })).toBeDisabled();

  expect(refreshWrites).toBe(1);
  await expect
    .poll(() =>
      page.evaluate(() => Number(Reflect.get(globalThis, '__geoMediaRefreshStatusForbiddenReads'))),
    )
    .toBe(1);
  await expectAccessible(page);
});

test('a delayed initial refresh status cannot overwrite a newer completed refresh', async ({
  page,
}) => {
  let statusReads = 0;
  let releaseInitialStatus!: () => void;
  const initialStatusGate = new Promise<void>((resolve) => {
    releaseInitialStatus = resolve;
  });
  await routeReadyMediaPrices(page);
  await page.route('**/api/v2/datasets/media-prices/refresh-status', async (route) => {
    statusReads += 1;
    if (statusReads === 1) {
      await initialStatusGate;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { 'x-test-stale-initial': '1' },
        body: JSON.stringify({
          state: 'never',
          started_at: null,
          updated_at: null,
          message: '',
          sources: {},
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        statusReads === 2
          ? {
              state: 'running',
              started_at: '2026-07-27 20:30:00',
              updated_at: '2026-07-27 20:30:00',
              message: '本轮刷新进行中',
              sources: {
                prfabu: { status: 'pending', rows: 0, note: '' },
                toumeiw: { status: 'pending', rows: 0, note: '' },
                mtpfw: { status: 'pending', rows: 0, note: '' },
                meititejia: { status: 'pending', rows: 0, note: '' },
                meijiehezi: { status: 'pending', rows: 0, note: '' },
                pinda: { status: 'pending', rows: 0, note: '' },
              },
            }
          : {
              state: 'done',
              started_at: '2026-07-27 20:30:00',
              updated_at: '2026-07-27 20:30:02',
              message: '新刷新已完成',
              sources: {
                prfabu: { status: 'ok', rows: 3, note: '' },
                toumeiw: { status: 'ok', rows: 2, note: '' },
                mtpfw: { status: 'ok', rows: 1, note: '' },
                meititejia: { status: 'ok', rows: 0, note: '' },
                meijiehezi: { status: 'ok', rows: 0, note: '' },
                pinda: { status: 'ok', rows: 0, note: '' },
              },
            },
      ),
    });
  });
  await page.route('**/api/v2/datasets/media-prices/refresh', (route) =>
    route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'running',
        started_at: null,
        updated_at: null,
        message: '刷新已启动',
        sources: {},
      }),
    }),
  );

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('正在读取刷新状态…')).toBeVisible();
  await page.getByRole('button', { name: '刷新数据' }).click();
  await expect(page.getByText('本轮刷新进行中', { exact: true })).toBeVisible();
  // 生产轮询节拍 REFRESH_POLL_INTERVAL_MS=10s：完成态最早在下一次 tick 才出现，
  // 默认 5s 断言窗口必然超时（该用例自入版起从未真正跑过，此前未发现）。
  await expect(page.getByText('刷新完成：新刷新已完成')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('上次刷新：新刷新已完成')).toBeVisible({ timeout: 15_000 });

  const staleResponse = page.waitForResponse(
    (response) => response.headers()['x-test-stale-initial'] === '1',
  );
  releaseInitialStatus();
  await staleResponse;
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      }),
  );

  await expect(page.getByText('尚未刷新')).toHaveCount(0);
  await expect(page.getByText('上次刷新：新刷新已完成')).toBeVisible();
  expect(statusReads).toBe(3);
  await expectAccessible(page);
});

test('a contradictory completed refresh remains unavailable and never claims success', async ({
  page,
}) => {
  let datasetReads = 0;
  let statusReads = 0;
  let refreshWrites = 0;
  await page.route('**/api/v2/datasets/media-prices', async (route) => {
    datasetReads += 1;
    await fulfillMediaPricesJson(route, buildMediaPricesContractPayload(3));
  });
  await page.route('**/api/v2/datasets/media-prices/refresh-status', (route) => {
    statusReads += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        statusReads === 1
          ? {
              state: 'never',
              started_at: null,
              updated_at: null,
              message: '',
              sources: {},
            }
          : {
              state: 'done',
              started_at: '2026-07-27 20:55:00',
              updated_at: '2026-07-27 20:55:02',
              message: '矛盾终态不得采信',
              sources: {
                prfabu: { status: 'ok', rows: 3, note: '' },
                toumeiw: { status: 'ok', rows: 2, note: '' },
                mtpfw: { status: 'pending', rows: 0, note: '' },
                meititejia: { status: 'ok', rows: 0, note: '' },
                meijiehezi: { status: 'ok', rows: 0, note: '' },
                pinda: { status: 'ok', rows: 0, note: '' },
              },
            },
      ),
    });
  });
  await page.route('**/api/v2/datasets/media-prices/refresh', (route) => {
    refreshWrites += 1;
    return route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'running',
        started_at: null,
        updated_at: null,
        message: '刷新已启动',
        sources: {},
      }),
    });
  });

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('尚未刷新')).toBeVisible();
  await page.getByRole('button', { name: '刷新数据' }).click();
  await expect(page.getByText('刷新状态暂不可用，正在重试…')).toBeVisible();
  await expect(page.getByText(/刷新完成：/)).toHaveCount(0);
  await expect(page.getByText('上次刷新：矛盾终态不得采信')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '刷新中…' })).toBeDisabled();
  expect(refreshWrites).toBe(1);
  expect(statusReads).toBe(2);
  expect(datasetReads).toBe(1);
  await expectAccessible(page);
});

test('an unchanged pre-refresh terminal record cannot complete a newly accepted refresh', async ({
  page,
}) => {
  let datasetReads = 0;
  let statusReads = 0;
  const oldDone = {
    state: 'done',
    started_at: '2026-07-27 20:58:00',
    updated_at: '2026-07-27 20:58:02',
    message: '上一轮刷新完成',
    sources: {
      prfabu: { status: 'ok', rows: 3, note: '' },
      toumeiw: { status: 'ok', rows: 2, note: '' },
      mtpfw: { status: 'ok', rows: 1, note: '' },
      meititejia: { status: 'ok', rows: 0, note: '' },
      meijiehezi: { status: 'ok', rows: 0, note: '' },
      pinda: { status: 'ok', rows: 0, note: '' },
    },
  };
  await page.route('**/api/v2/datasets/media-prices', async (route) => {
    datasetReads += 1;
    await fulfillMediaPricesJson(
      route,
      buildMediaPricesContractPayload(datasetReads === 1 ? 3 : 4),
    );
  });
  await page.route('**/api/v2/datasets/media-prices/refresh-status', (route) => {
    statusReads += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        statusReads < 3
          ? oldDone
          : {
              ...oldDone,
              started_at: '2026-07-27 21:00:00',
              updated_at: '2026-07-27 21:00:02',
              message: '新一轮刷新完成',
              sources: {
                ...oldDone.sources,
                prfabu: { status: 'ok', rows: 4, note: '' },
              },
            },
      ),
    });
  });
  await page.route('**/api/v2/datasets/media-prices/refresh', (route) =>
    route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'running',
        started_at: null,
        updated_at: null,
        message: '刷新已启动',
        sources: {},
      }),
    }),
  );

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('上次刷新：上一轮刷新完成')).toBeVisible();
  await page.getByRole('button', { name: '刷新数据' }).click();
  await expect(page.getByText('刷新已接受，正在等待新的终态记录…')).toBeVisible();
  await expect(page.getByText('刷新完成：上一轮刷新完成')).toHaveCount(0);
  expect(datasetReads).toBe(1);

  // 生产轮询节拍 REFRESH_POLL_INTERVAL_MS=10s：完成态最早在下一次 tick 才出现。
  await expect(page.getByText('刷新完成：新一轮刷新完成')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('上次刷新：新一轮刷新完成')).toBeVisible({ timeout: 15_000 });
  expect(statusReads).toBe(3);
  expect(datasetReads).toBe(2);
  await expectAccessible(page);
});

test('a contradictory authoritative failure remains unavailable and never claims failure', async ({
  page,
}) => {
  let statusReads = 0;
  let refreshWrites = 0;
  await page.route('**/api/v2/datasets/media-prices', (route) =>
    fulfillMediaPricesJson(route, buildMediaPricesContractPayload(3)),
  );
  await page.route('**/api/v2/datasets/media-prices/refresh-status', (route) => {
    statusReads += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        statusReads === 1
          ? {
              state: 'never',
              started_at: null,
              updated_at: null,
              message: '',
              sources: {},
            }
          : {
              state: 'failed',
              started_at: '2026-07-27 21:31:00',
              updated_at: '2026-07-27 21:31:02',
              message: '不得采信的不完整失败',
              sources: {
                prfabu: { status: 'failed', rows: 0, note: 'source_unavailable' },
              },
            },
      ),
    });
  });
  await page.route('**/api/v2/datasets/media-prices/refresh', (route) => {
    refreshWrites += 1;
    return route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'running',
        started_at: null,
        updated_at: null,
        message: '刷新已启动',
        sources: {},
      }),
    });
  });

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('尚未刷新')).toBeVisible();
  await page.getByRole('button', { name: '刷新数据' }).click();
  await expect(page.getByText('刷新状态暂不可用，正在重试…')).toBeVisible();
  await expect(page.getByText(/刷新失败：/)).toHaveCount(0);
  await expect(page.getByText('上次刷新失败：不得采信的不完整失败')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '刷新中…' })).toBeDisabled();
  expect(refreshWrites).toBe(1);
  expect(statusReads).toBe(2);
  await expectAccessible(page);
});

test('an authoritative status-shaped 202 body is not accepted as a start receipt', async ({
  page,
}) => {
  let statusReads = 0;
  let refreshWrites = 0;
  await page.route('**/api/v2/datasets/media-prices', (route) =>
    fulfillMediaPricesJson(route, buildMediaPricesContractPayload(3)),
  );
  await page.route('**/api/v2/datasets/media-prices/refresh-status', (route) => {
    statusReads += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'never',
        started_at: null,
        updated_at: null,
        message: '',
        sources: {},
      }),
    });
  });
  await page.route('**/api/v2/datasets/media-prices/refresh', (route) => {
    refreshWrites += 1;
    return route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'running',
        started_at: '2026-07-27 21:32:00',
        updated_at: '2026-07-27 21:32:01',
        message: '这是状态记录而不是接受回执',
        sources: {
          prfabu: { status: 'pending', rows: 0, note: '' },
          toumeiw: { status: 'pending', rows: 0, note: '' },
          mtpfw: { status: 'pending', rows: 0, note: '' },
          meititejia: { status: 'pending', rows: 0, note: '' },
          meijiehezi: { status: 'pending', rows: 0, note: '' },
          pinda: { status: 'pending', rows: 0, note: '' },
        },
      }),
    });
  });

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('尚未刷新')).toBeVisible();
  await page.getByRole('button', { name: '刷新数据' }).click();
  await expect(page.getByText('刷新请求失败，请稍后重试。')).toBeVisible();
  await expect(page.getByText('刷新已启动…')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '刷新数据' })).toBeEnabled();
  expect(refreshWrites).toBe(1);
  expect(statusReads).toBe(1);
  await expectAccessible(page);
});

test('media refresh starts, polls and reloads an authoritative completed refresh exactly once', async ({
  page,
}) => {
  let datasetReads = 0;
  let statusReads = 0;
  let refreshWrites = 0;
  await page.route('**/api/v2/datasets/media-prices', async (route) => {
    datasetReads += 1;
    await fulfillMediaPricesJson(
      route,
      buildMediaPricesContractPayload(datasetReads === 1 ? 3 : 4),
    );
  });
  await page.route('**/api/v2/datasets/media-prices/refresh-status', (route) => {
    statusReads += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        statusReads === 1
          ? {
              state: 'never',
              started_at: null,
              updated_at: null,
              message: '',
              sources: {},
            }
          : {
              state: 'done',
              started_at: '2026-07-27 18:40:00',
              updated_at: '2026-07-27 18:40:02',
              message: 'prfabu 4 · 投媒网 2 · 媒体批发网 1',
              sources: {
                prfabu: { status: 'ok', rows: 4, note: '' },
                toumeiw: { status: 'ok', rows: 2, note: '' },
                mtpfw: { status: 'ok', rows: 1, note: '' },
                meititejia: { status: 'ok', rows: 0, note: '' },
                meijiehezi: { status: 'ok', rows: 0, note: '' },
                pinda: { status: 'ok', rows: 0, note: '' },
              },
            },
      ),
    });
  });
  await page.route('**/api/v2/datasets/media-prices/refresh', (route) => {
    refreshWrites += 1;
    return route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'running',
        started_at: null,
        updated_at: null,
        message: '刷新已启动',
        sources: {},
      }),
    });
  });

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('3', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '刷新数据' }).click();
  await expect(page.getByText('刷新完成：prfabu 4 · 投媒网 2 · 媒体批发网 1')).toBeVisible();
  await expect(page.getByText('4', { exact: true })).toBeVisible();

  expect(refreshWrites).toBe(1);
  expect(statusReads).toBe(2);
  expect(datasetReads).toBe(2);
  await expectAccessible(page);
});

test('completed refresh fails closed when the new dataset read loses permission', async ({
  page,
}) => {
  let statusReads = 0;
  let refreshWrites = 0;
  await page.addInitScript(() => {
    const nativeFetch = globalThis.fetch.bind(globalThis);
    Reflect.set(globalThis, '__geoMediaDatasetForbidden', false);
    Reflect.set(globalThis, '__geoMediaDatasetForbiddenReads', 0);
    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const requestUrl =
        typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const pathname = new URL(requestUrl, globalThis.location.origin).pathname;
      if (
        Reflect.get(globalThis, '__geoMediaDatasetForbidden') === true &&
        pathname === '/api/v2/datasets/media-prices'
      ) {
        Reflect.set(
          globalThis,
          '__geoMediaDatasetForbiddenReads',
          Number(Reflect.get(globalThis, '__geoMediaDatasetForbiddenReads')) + 1,
        );
        const response = new Response(JSON.stringify({ error: { code: 'permission_denied' } }), {
          status: 200,
          headers: { 'Cache-Control': 'no-store', 'Content-Type': 'application/json' },
        });
        Object.defineProperties(response, {
          status: { value: 403 },
          ok: { value: false },
          statusText: { value: '' },
        });
        return response;
      }
      return nativeFetch(input, init);
    };
  });
  await routeReadyMediaPrices(page);
  await page.route('**/api/v2/datasets/media-prices/refresh-status', (route) => {
    statusReads += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        statusReads === 1
          ? {
              state: 'never',
              started_at: null,
              updated_at: null,
              message: '',
              sources: {},
            }
          : {
              state: 'done',
              started_at: '2026-07-27 19:30:00',
              updated_at: '2026-07-27 19:30:02',
              message: '新快照已生成',
              sources: {
                prfabu: { status: 'ok', rows: 3, note: '' },
                toumeiw: { status: 'ok', rows: 2, note: '' },
                mtpfw: { status: 'ok', rows: 1, note: '' },
                meititejia: { status: 'ok', rows: 0, note: '' },
                meijiehezi: { status: 'ok', rows: 0, note: '' },
                pinda: { status: 'ok', rows: 0, note: '' },
              },
            },
      ),
    });
  });
  await page.route('**/api/v2/datasets/media-prices/refresh', (route) => {
    refreshWrites += 1;
    return route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'running',
        started_at: null,
        updated_at: null,
        message: '刷新已启动',
        sources: {},
      }),
    });
  });

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('人民网', { exact: true })).toBeVisible();
  await page.evaluate(() => {
    Reflect.set(globalThis, '__geoMediaDatasetForbidden', true);
  });
  await page.getByRole('button', { name: '刷新数据' }).click();

  await expect(page.getByText('无权查看')).toBeVisible();
  await expect(page.getByText('人民网', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/刷新完成：/)).toHaveCount(0);
  expect(refreshWrites).toBe(1);
  expect(statusReads).toBe(2);
  await expect
    .poll(() =>
      page.evaluate(() => Number(Reflect.get(globalThis, '__geoMediaDatasetForbiddenReads'))),
    )
    .toBe(1);
  await expectAccessible(page);
});

test('completed refresh warns when a source is partial or uses stale data', async ({ page }) => {
  let statusReads = 0;
  await routeReadyMediaPrices(page);
  await page.route('**/api/v2/datasets/media-prices/refresh-status', (route) => {
    statusReads += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        statusReads === 1
          ? {
              state: 'never',
              started_at: null,
              updated_at: null,
              message: '',
              sources: {},
            }
          : {
              state: 'done',
              started_at: '2026-07-27 20:05:00',
              updated_at: '2026-07-27 20:05:02',
              message: '刷新任务结束',
              sources: {
                prfabu: { status: 'ok', rows: 3, note: '' },
                toumeiw: { status: 'partial', rows: 2, note: 'rate_limited' },
                mtpfw: { status: 'stale', rows: 1, note: 'source_unavailable' },
                meititejia: { status: 'ok', rows: 0, note: '' },
                meijiehezi: { status: 'ok', rows: 0, note: '' },
                pinda: { status: 'ok', rows: 0, note: '' },
              },
            },
      ),
    });
  });
  await page.route('**/api/v2/datasets/media-prices/refresh', (route) =>
    route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        state: 'running',
        started_at: null,
        updated_at: null,
        message: '刷新已启动',
        sources: {},
      }),
    }),
  );

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('尚未刷新')).toBeVisible();
  await page.getByRole('button', { name: '刷新数据' }).click();

  const warning = page.locator('.media-prices-notice.warn');
  await expect(warning).toContainText('toumeiw 仅完成部分采集');
  await expect(warning).toContainText('mtpfw 数据陈旧，沿用旧数据');
  await expect(page.getByText('人民网', { exact: true })).toBeVisible();
  expect(statusReads).toBe(2);
  await expectAccessible(page);
});

test('media prices accepts a bounded ready projection after a local failure', async ({ page }) => {
  await installSyntheticHttpResponses(page, [
    {
      id: 'media-prices-retry',
      path: '/api/v2/datasets/media-prices',
      status: 503,
      body: { error: { code: 'temporary_failure' } },
      remaining: 1,
    },
  ]);
  await routeReadyMediaPrices(page);
  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('加载失败')).toBeVisible();
  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('人民网', { exact: true })).toBeVisible();
});

test('media prices fails closed when artifact digest or statistics contradict projected rows', async ({
  page,
}) => {
  const payload = buildMediaPricesContractPayload(3);
  let reads = 0;
  await page.route('**/api/v2/datasets/media-prices', async (route) => {
    reads += 1;
    if (reads === 1) {
      await fulfillMediaPricesJson(route, payload, 200, 'b'.repeat(64));
      return;
    }
    if (reads === 2) {
      await fulfillMediaPricesJson(route, {
        ...payload,
        stats: { ...payload.stats, matched_3: 1 },
      });
      return;
    }
    await fulfillMediaPricesJson(route, payload);
  });

  await page.goto('/platform/operations/media-prices');
  await expect(page.getByText('加载失败')).toBeVisible();
  await expect(page.getByText('人民网', { exact: true })).toHaveCount(0);

  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect.poll(() => reads).toBe(2);
  await expect(page.getByText('加载失败')).toBeVisible();
  await expect(page.getByText('人民网', { exact: true })).toHaveCount(0);

  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('人民网', { exact: true })).toBeVisible();
  expect(reads).toBe(3);
  await expectAccessible(page);
});
