import { chromium } from '@playwright/test';
import { EventEmitter } from 'node:events';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  collectBrowserRuntimeEvidence,
  containsBrowserSecretMaterial,
  isBrowserRuntimeEvidenceClean,
  isBrowserSurfaceSecretMaterialAbsent,
  persistSafeBrowserScreenshot,
} from './browser_runtime_evidence.mjs';

class RuntimePageProbe extends EventEmitter {
  off(event, listener) {
    return super.off(event, listener);
  }

  on(event, listener) {
    return super.on(event, listener);
  }
}

describe('production browser runtime evidence', () => {
  it('retains only bounded issue counts when every raw channel contains secrets', () => {
    const page = new RuntimePageProbe();
    const collector = collectBrowserRuntimeEvidence(page);
    page.emit('console', {
      type: () => 'error',
      text: () => 'Bearer production-browser-secret-canary',
    });
    page.emit('pageerror', new Error('OTP 824911'));
    page.emit('requestfailed', {
      url: () => 'https://example.invalid/?access_token=production-browser-secret-canary',
      failure: () => ({ errorText: 'Cookie=production-browser-secret-canary' }),
    });
    page.emit('response', {
      status: () => 503,
      url: () => 'https://example.invalid/secret/profile/production-browser-secret-canary',
    });
    page.emit('request', {
      headers: () => ({
        cookie: 'session=production-browser-secret-canary',
        'x-tenant-id': 'tnt_controlled_claim',
      }),
      url: () => 'https://example.invalid/?token=production-browser-secret-canary',
    });

    expect(collector.counts).toEqual({
      console_error: 1,
      page_error: 1,
      request_failed: 1,
      error_response: 1,
      forbidden_actor_header: 1,
    });
    expect(isBrowserRuntimeEvidenceClean(collector.counts)).toBe(false);
    expect(JSON.stringify(collector.counts)).not.toMatch(
      /Bearer|824911|access_token|Cookie|profile|session|secret-canary/,
    );

    collector.stop();
    page.emit('pageerror', new Error('detached production-browser-secret-canary'));
    expect(collector.counts.page_error).toBe(1);
  });

  it('accepts a clean browser without retaining request or response metadata', () => {
    const page = new RuntimePageProbe();
    const collector = collectBrowserRuntimeEvidence(page);
    page.emit('console', { type: () => 'warning' });
    page.emit('response', { status: () => 204 });
    page.emit('request', { headers: () => ({ accept: 'application/json' }) });

    expect(isBrowserRuntimeEvidenceClean(collector.counts)).toBe(true);
    expect(Object.keys(collector.counts)).toHaveLength(5);
  });

  it('retains count-only page-error observability after raw global defaults are suppressed', async () => {
    const browser = await chromium.launch({ headless: true, channel: 'chromium' });
    const page = await browser.newPage();
    await page.addInitScript(() => {
      const relayedSafeErrors = new WeakSet();
      const relaySafeRuntimeError = (message, rejection) => {
        const error = new Error(message);
        relayedSafeErrors.add(error);
        setTimeout(() => {
          if (rejection) void Promise.reject(error);
          else throw error;
        }, 0);
      };
      addEventListener('error', (event) => {
        if (event.error instanceof Error && relayedSafeErrors.has(event.error)) {
          relayedSafeErrors.delete(event.error);
          return;
        }
        event.stopImmediatePropagation();
        event.preventDefault();
        relaySafeRuntimeError('GEO_SAFE_WINDOW_ERROR', false);
      });
      addEventListener('unhandledrejection', (event) => {
        if (event.reason instanceof Error && relayedSafeErrors.has(event.reason)) {
          relayedSafeErrors.delete(event.reason);
          return;
        }
        event.stopImmediatePropagation();
        event.preventDefault();
        relaySafeRuntimeError('GEO_SAFE_UNHANDLED_REJECTION', true);
      });
    });
    await page.route('http://127.0.0.1/**', (route) =>
      route.fulfill({
        contentType: 'text/html',
        body: '<!doctype html><html lang="zh-CN"><body><main>安全投影</main></body></html>',
      }),
    );
    const collector = collectBrowserRuntimeEvidence(page);
    try {
      await page.goto('http://127.0.0.1/global-error-relay/');
      await page.evaluate(() => {
        setTimeout(() => {
          throw new Error('Bearer raw-window-error-canary OTP 824911');
        }, 0);
        void Promise.reject(
          new Error('Cookie=session-raw-rejection-canary profile_path=/secret/profile/canary'),
        );
      });
      await expect
        .poll(() => collector.counts.page_error, {
          timeout: 2_000,
        })
        .toBe(2);
      expect(collector.counts).toEqual({
        console_error: 0,
        page_error: 2,
        request_failed: 0,
        error_response: 0,
        forbidden_actor_header: 0,
      });
      expect(JSON.stringify(collector.counts)).not.toMatch(
        /Bearer|Cookie|session|OTP|824911|profile|raw-(?:window-error|rejection)-canary/i,
      );
    } finally {
      collector.stop();
      await page.close();
      await browser.close();
    }
  });

  it('rejects normalized screenshot secrets without treating safety guidance as a secret', () => {
    expect(
      containsBrowserSecretMaterial(
        '请勿在聊天或普通表单粘贴验证码、Cookie、token、密码或生物材料。',
      ),
    ).toBe(false);
    for (const value of [
      'Authorization: Bearer screenshot-secret-canary',
      'Cookie=session=screenshot-secret-canary',
      'OTP 824911',
      'profile_path=/secret/profile/screenshot-secret-canary',
      '客户手机号 13800138000',
      'Ｃｏｏｋｉｅ\u200b＝session=screenshot-secret-canary',
      'otpauth://totp/screenshot-secret-canary',
      'OTP%2520824-911',
      '客户手机号 138 0013 8000',
      String.raw`profile_dir=C:\Users\customer\AppData\User Data\Profile 1`,
    ]) {
      expect(containsBrowserSecretMaterial(value)).toBe(true);
    }
    expect(containsBrowserSecretMaterial('x'.repeat(2_000_001))).toBe(true);
  });

  it('rejects hidden DOM, URL, Window.name, storage, script-readable Cookie, history, IndexedDB, Cache Storage, Service Worker, OPFS, named Storage Bucket and Legacy FileSystem surfaces without returning values', async () => {
    const browser = await chromium.launch({ headless: true, channel: 'chromium' });
    const page = await browser.newPage();
    const server = createServer((request, response) => {
      if (request.url === '/geo-runtime-evidence-sw.js') {
        response
          .writeHead(200, {
            'Cache-Control': 'no-store',
            'Content-Type': 'application/javascript; charset=utf-8',
            'Service-Worker-Allowed': '/',
          })
          .end(
            "const canary = 'Bearer service-worker-persistence-canary'; void canary; self.addEventListener('install', () => self.skipWaiting());",
          );
        return;
      }
      response
        .writeHead(200, {
          'Cache-Control': 'no-store',
          'Content-Type': 'text/html; charset=utf-8',
        })
        .end('<!doctype html><html lang="zh-CN"><body><main>安全投影</main></body></html>');
    });
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    const address = server.address();
    if (!address || typeof address === 'string')
      throw new Error('Browser evidence server unavailable');
    try {
      await page.goto(`http://127.0.0.1:${address.port}/browser-evidence/`);
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(true);

      await page.setContent(
        '<!doctype html><html><body><input type="hidden" value="OTP 824911"></body></html>',
      );
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);

      await page.setContent(
        '<!doctype html><html><body><main aria-label="Bearer hidden-attribute-canary">安全投影</main></body></html>',
      );
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);

      await page.setContent(
        '<!doctype html><html><head><style>.safe::before{content:"Bearer css-generated-content-canary"}</style></head><body><main class="safe">安全投影</main></body></html>',
      );
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);

      await page.setContent(
        '<!doctype html><html><head><style>.safe{background-image:url("data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27%3E%3Ctext%3EBearer css-resource-canary%3C/text%3E%3C/svg%3E")}</style></head><body><main class="safe">安全投影</main></body></html>',
      );
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);

      await page.setContent('<!doctype html><html><body><main>安全投影</main></body></html>');
      await page.evaluate(() => localStorage.setItem('access_token', 'opaque-storage-canary'));
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);
      await page.evaluate(() => localStorage.clear());

      await page.evaluate(() => {
        globalThis.name = 'Bearer window-name-persistence-canary';
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);
      await page.evaluate(() => {
        globalThis.name = '';
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(true);

      await page.evaluate(() =>
        history.replaceState(
          { profile_path: '/secret/browser/profile/history-canary' },
          '',
          '/safe',
        ),
      );
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);
      await page.evaluate(() => history.replaceState(null, '', '/safe'));

      await page.evaluate(() => {
        document.cookie =
          'geo_runtime_cookie_probe=opaque-value-without-sensitive-shape; SameSite=Lax';
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);
      await page.evaluate(() => {
        document.cookie = 'geo_runtime_cookie_probe=; Max-Age=0; SameSite=Lax';
        history.replaceState(null, '', '/?access_token=url-canary');
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);

      await page.evaluate(() => {
        history.replaceState(null, '', '/platform/customer/access%255Ftoken#profile%255Fpath');
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);

      await page.evaluate(
        () =>
          new Promise((resolve, reject) => {
            history.replaceState(null, '', '/safe');
            const request = indexedDB.open('geo-browser-persistence-canary', 1);
            request.onupgradeneeded = () => request.result.createObjectStore('records');
            request.onerror = () => reject(new Error('IndexedDB setup failed'));
            request.onsuccess = () => {
              const database = request.result;
              const transaction = database.transaction('records', 'readwrite');
              transaction.objectStore('records').put('Bearer persistence-canary', 'record');
              transaction.onerror = () => reject(new Error('IndexedDB write failed'));
              transaction.oncomplete = () => {
                database.close();
                resolve(undefined);
              };
            };
          }),
      );
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);
      await page.evaluate(
        () =>
          new Promise((resolve, reject) => {
            const request = indexedDB.deleteDatabase('geo-browser-persistence-canary');
            request.onerror = () => reject(new Error('IndexedDB cleanup failed'));
            request.onsuccess = () => resolve(undefined);
          }),
      );
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(true);

      await page.evaluate(async () => {
        const cache = await globalThis.caches.open('geo-browser-cache-canary');
        await cache.put('/safe-resource', new Response('Bearer cache-persistence-canary'));
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);
      await page.evaluate(async () => {
        await globalThis.caches.delete('geo-browser-cache-canary');
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(true);

      await page.evaluate(async () => {
        await navigator.serviceWorker.register('/geo-runtime-evidence-sw.js');
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);
      await page.evaluate(async () => {
        for (const registration of await navigator.serviceWorker.getRegistrations()) {
          await registration.unregister();
        }
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(true);

      await page.evaluate(async () => {
        const root = await navigator.storage.getDirectory();
        const file = await root.getFileHandle('geo-browser-opfs-canary', { create: true });
        const writable = await file.createWritable();
        await writable.write('Bearer opfs-persistence-canary');
        await writable.close();
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);
      await page.evaluate(async () => {
        const root = await navigator.storage.getDirectory();
        await root.removeEntry('geo-browser-opfs-canary');
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(true);

      await page.evaluate(async () => {
        const bucket = await navigator.storageBuckets.open('geo-browser-storage-bucket-canary');
        const root = await bucket.getDirectory();
        const file = await root.getFileHandle('opaque-record', { create: true });
        const writable = await file.createWritable();
        await writable.write('Bearer storage-bucket-persistence-canary');
        await writable.close();
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);
      await page.evaluate(async () => {
        await navigator.storageBuckets.delete('geo-browser-storage-bucket-canary');
      });
      expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(true);

      for (const legacyFileSystem of [
        {
          type: 0,
          fileName: 'geo-browser-legacy-temporary-canary',
          value: 'Bearer legacy-temporary-persistence-canary',
        },
        {
          type: 1,
          fileName: 'geo-browser-legacy-persistent-canary',
          value: 'Bearer legacy-persistent-persistence-canary',
        },
      ]) {
        await page.evaluate(
          ({ type, fileName, value }) =>
            new Promise((resolve, reject) => {
              globalThis.webkitRequestFileSystem(
                type,
                1_024,
                (fileSystem) => {
                  fileSystem.root.getFile(
                    fileName,
                    { create: true },
                    (fileEntry) => {
                      fileEntry.createWriter(
                        (writer) => {
                          writer.onerror = () =>
                            reject(new Error('Legacy FileSystem write failed'));
                          writer.onwriteend = () => resolve(undefined);
                          writer.write(new Blob([value], { type: 'application/octet-stream' }));
                        },
                        () => reject(new Error('Legacy FileSystem writer unavailable')),
                      );
                    },
                    () => reject(new Error('Legacy FileSystem file unavailable')),
                  );
                },
                () => reject(new Error('Legacy FileSystem root unavailable')),
              );
            }),
          legacyFileSystem,
        );
        expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(false);
        await page.evaluate(
          ({ type, fileName }) =>
            new Promise((resolve, reject) => {
              globalThis.webkitRequestFileSystem(
                type,
                1_024,
                (fileSystem) => {
                  fileSystem.root.getFile(
                    fileName,
                    { create: false },
                    (fileEntry) =>
                      fileEntry.remove(
                        () => resolve(undefined),
                        () => reject(new Error('Legacy FileSystem removal failed')),
                      ),
                    () => reject(new Error('Legacy FileSystem cleanup file unavailable')),
                  );
                },
                () => reject(new Error('Legacy FileSystem cleanup root unavailable')),
              );
            }),
          legacyFileSystem,
        );
        expect(await isBrowserSurfaceSecretMaterialAbsent(page)).toBe(true);
      }
    } finally {
      await page.close();
      await browser.close();
      await new Promise((resolve) => server.close(resolve));
    }
  });

  it('removes stale output, retries capture once and never screenshots rendered secrets', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'geo-browser-evidence-'));
    const unsafeTarget = join(directory, 'unsafe.png');
    const safeTarget = join(directory, 'safe.png');
    let screenshotCalls = 0;
    const page = {
      async evaluate() {
        return true;
      },
      async screenshot({ path }) {
        screenshotCalls += 1;
        if (screenshotCalls === 1) throw new Error('transient screenshot protocol failure');
        await writeFile(path, 'safe-bitmap-fixture');
      },
    };
    try {
      await writeFile(unsafeTarget, 'stale-sensitive-bitmap');
      const rejected = await persistSafeBrowserScreenshot(page, unsafeTarget, [
        'Cookie=session=screenshot-secret-canary',
      ]);
      expect(rejected).toEqual({ screenshot: null, secretMaterialAbsent: false });
      await expect(readFile(unsafeTarget)).rejects.toMatchObject({ code: 'ENOENT' });
      expect(screenshotCalls).toBe(0);

      const accepted = await persistSafeBrowserScreenshot(page, safeTarget, [
        '安全投影已完成；请勿在普通表单粘贴 Cookie 或 token。',
      ]);
      expect(accepted).toEqual({
        screenshot: safeTarget,
        secretMaterialAbsent: true,
      });
      expect(await readFile(safeTarget, 'utf8')).toBe('safe-bitmap-fixture');
      expect(screenshotCalls).toBe(2);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it('rejects resource-backed machine visuals before a production screenshot', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'geo-browser-machine-visual-'));
    const target = join(directory, 'machine-visual.png');
    const browser = await chromium.launch({ headless: true, channel: 'chromium' });
    const page = await browser.newPage();
    await page.route('http://browser-machine-visual.test/**', (route) =>
      route.fulfill({
        contentType: 'text/html',
        body: '<!doctype html><html lang="zh-CN"><body></body></html>',
      }),
    );
    try {
      await page.goto('http://browser-machine-visual.test/');
      await page.setContent(`
        <!doctype html>
        <html lang="zh-CN">
          <head>
            <style>
              .unsafe-pairing span::before {
                content: "";
                display: block;
                width: 32px;
                height: 32px;
                background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='32' height='32'%3E%3Cpath d='M0 0h32v32H0z'/%3E%3C/svg%3E");
              }
            </style>
          </head>
          <body>
            <div
              class="unsafe-pairing"
              role="img"
              aria-label="pairing QR code"
              data-visual-evidence="payload-free"
            ><span aria-hidden="true"></span></div>
          </body>
        </html>
      `);
      await writeFile(target, 'stale-sensitive-bitmap');
      const rejected = await persistSafeBrowserScreenshot(page, target, ['受控终端安全配对。']);
      expect(rejected).toEqual({ screenshot: null, secretMaterialAbsent: false });
      await expect(readFile(target)).rejects.toMatchObject({ code: 'ENOENT' });

      await page.setContent(`
        <!doctype html>
        <html lang="zh-CN">
          <head>
            <style>
              .safe-pairing {
                width: 32px;
                height: 32px;
                background-image: linear-gradient(90deg, #000 50%, #fff 50%);
              }
            </style>
          </head>
          <body>
            <div
              class="safe-pairing"
              role="img"
              aria-label="一次性安全配对二维码占位；不可扫描"
              data-visual-evidence="payload-free"
            ><span aria-hidden="true"></span></div>
          </body>
        </html>
      `);
      const accepted = await persistSafeBrowserScreenshot(page, target, ['契约演示；不可扫描。']);
      expect(accepted).toEqual({ screenshot: target, secretMaterialAbsent: true });
      expect((await readFile(target)).length).toBeGreaterThan(0);
    } finally {
      await page.close();
      await browser.close();
      await rm(directory, { recursive: true, force: true });
    }
  });
});
