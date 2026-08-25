import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { captureSafeScreenshot, isSafeScreenshotSurface } from './screenshot-safety';

const safeSurface = {
  textLines: ['安全投影；不会显示 Cookie、token、OTP、profile 路径或生物材料。'],
  textNodes: ['安全投影；不会显示 Cookie、token、OTP、profile 路径或生物材料。'],
  attributeNames: ['id', 'aria-label'],
  attributes: [
    { value: 'main-content', isSameOriginBlobImageSource: false },
    { value: '平台账号与授权', isSameOriginBlobImageSource: false },
  ],
  controls: ['read', 'query'],
  machineReadableVisuals: [{ payloadFree: true }],
  computedGeneratedContentSafe: true,
  computedResourceStylesSafe: true,
  url: 'http://127.0.0.1/platform/customer/?section=accounts',
  windowName: '',
  scriptReadableCookieLength: 0,
  cookieStoreEntryCount: 0,
  historyState: '{"idx":0}',
  localStorage: [{ key: 'geo.session.role', value: 'customer' }],
  sessionStorage: [],
  indexedDbDatabaseCount: 0,
  cacheStorageCount: 0,
  serviceWorkerRegistrationCount: 0,
  opfsRootEntryCount: 0,
  storageBucketCount: 0,
  legacyFileSystemTemporaryRootEntryCount: 0,
  legacyFileSystemPersistentRootEntryCount: 0,
};

describe('local visual evidence DLP', () => {
  it('allows the fixed CI Playwright output root', async () => {
    const target = join(process.cwd(), 'test-results', 'ci-approved-output.png');
    let capturedPath = '';
    const page = {
      async evaluate() {
        return safeSurface;
      },
      async screenshot(options: { path?: string }) {
        capturedPath = options.path ?? '';
      },
    };

    await captureSafeScreenshot(page as never, { path: target, fullPage: true });

    expect(capturedPath).toBe(target);
  });

  it('rejects secrets from DOM, controls, URL and browser storage without false positives', () => {
    expect(isSafeScreenshotSurface(safeSurface)).toBe(true);
    const phoneShapedBlobUrl = 'blob:http://127.0.0.1/a2172290-4754-45ba-b7ce-505cab745263';
    expect(
      isSafeScreenshotSurface({
        ...safeSurface,
        attributes: [
          ...safeSurface.attributes,
          {
            value: phoneShapedBlobUrl,
            isSameOriginBlobImageSource: true,
          },
        ],
      }),
    ).toBe(true);
    expect(
      isSafeScreenshotSurface({
        ...safeSurface,
        attributes: [
          ...safeSurface.attributes,
          {
            value: phoneShapedBlobUrl,
            isSameOriginBlobImageSource: false,
          },
        ],
      }),
    ).toBe(false);
    expect(
      isSafeScreenshotSurface({
        ...safeSurface,
        url: 'http://127.0.0.1/platform/customer/?section=profile',
      }),
    ).toBe(true);
    for (const override of [
      { textLines: ['Bearer screenshot-secret-canary'] },
      { textNodes: ['Bearer screenshot-secret-canary'] },
      {
        textLines: ['138' + '0013' + '8000'],
        textNodes: ['138', '0013', '8000'],
      },
      { controls: ['OTP 824911'] },
      { machineReadableVisuals: [{ payloadFree: false }] },
      { computedGeneratedContentSafe: false },
      { computedResourceStylesSafe: false },
      { attributeNames: ['data-access_token'] },
      {
        attributes: [
          {
            value: 'access_token=screenshot-secret-canary',
            isSameOriginBlobImageSource: false,
          },
        ],
      },
      { url: 'http://example.test/?access_token=screenshot-secret-canary' },
      { url: 'http://example.test/platform/customer/access%255Ftoken' },
      { url: 'http://example.test/platform/customer/#profile%255Fpath' },
      { url: 'http://ordinary-user:ordinary-pass@example.test/platform/customer/' },
      { windowName: 'Bearer window-name-screenshot-secret-canary' },
      { scriptReadableCookieLength: 1 },
      { scriptReadableCookieLength: null },
      { cookieStoreEntryCount: 1 },
      { cookieStoreEntryCount: null },
      { historyState: '{"profile_path":"/secret/profile/screenshot-secret-canary"}' },
      { historyState: null },
      {
        localStorage: [
          { key: 'geo.session.role', value: 'customer' },
          { key: 'Cookie', value: 'session=screenshot-secret-canary' },
        ],
      },
      {
        sessionStorage: [
          {
            key: 'profile_path',
            value: '/secret/profile/screenshot-secret-canary',
          },
        ],
      },
      { indexedDbDatabaseCount: 1 },
      { indexedDbDatabaseCount: null },
      { cacheStorageCount: 1 },
      { cacheStorageCount: null },
      { serviceWorkerRegistrationCount: 1 },
      { serviceWorkerRegistrationCount: null },
      { opfsRootEntryCount: 1 },
      { opfsRootEntryCount: null },
      { storageBucketCount: 1 },
      { storageBucketCount: null },
      { legacyFileSystemTemporaryRootEntryCount: 1 },
      { legacyFileSystemTemporaryRootEntryCount: null },
      { legacyFileSystemPersistentRootEntryCount: 1 },
      { legacyFileSystemPersistentRootEntryCount: null },
    ]) {
      expect(isSafeScreenshotSurface({ ...safeSurface, ...override })).toBe(false);
    }
    expect(
      isSafeScreenshotSurface({
        ...safeSurface,
        textLines: ['x'.repeat(2_000_001)],
      }),
    ).toBe(false);
  });

  it('removes a stale approved-path PNG and omits raw secrets from the rejection', async () => {
    const directory = await mkdtemp(join(process.cwd(), 'tests/e2e-results/safe-shot-'));
    const target = join(directory, 'hostile.png');
    let screenshotCalls = 0;
    const page = {
      async evaluate() {
        return {
          ...safeSurface,
          controls: ['Ｃｏｏｋｉｅ\u200b＝session=screenshot-secret-canary'],
        };
      },
      async screenshot() {
        screenshotCalls += 1;
      },
    };
    try {
      await writeFile(target, 'stale-sensitive-bitmap');
      let rejection = '';
      try {
        await captureSafeScreenshot(page as never, { path: target, fullPage: true });
      } catch (error) {
        rejection = error instanceof Error ? error.message : String(error);
      }
      expect(rejection).toContain('Visual evidence rejected by DLP');
      expect(rejection).not.toMatch(/Cookie|session|screenshot-secret-canary/);
      await expect(readFile(target)).rejects.toMatchObject({ code: 'ENOENT' });
      expect(screenshotCalls).toBe(0);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
