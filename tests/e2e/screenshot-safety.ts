import {
  containsClientSecret,
  containsClientSecretKey,
} from '../../packages/design-system/src/index';
import {
  expect,
  type Locator,
  type Page,
  type PageAssertionsToHaveScreenshotOptions,
  type PageScreenshotOptions,
} from '@playwright/test';
import { rm } from 'node:fs/promises';
import { isAbsolute, relative, resolve, sep } from 'node:path';

const screenshotSurfaceLimit = 2_000_000;
const visualEvidenceRoots = [
  resolve(process.cwd(), 'tests/e2e-results'),
  resolve(process.cwd(), 'tests/visual-evidence'),
];
const snapshotNamePattern = /^[A-Za-z0-9][A-Za-z0-9._-]*\.png$/u;
const renderedSecretInvisiblePattern = /[\u200b-\u200d\u2060\ufeff]/gu;
const renderedSecretPattern =
  /(?:\bbearer\s+[A-Za-z0-9%._~+/=-]{6,}|\b(?:cookie|set-cookie|session|access_token|refresh_token|pairing_token|token|otp|password|proxy_password|profile_path)\s*["']?\s*[:=]\s*["']?[^\s"'<]{4,}|dlp-canary|(?:^|[^\w])\d{6}(?:[^\w]|$)|1[3-9](?:[\s().-]?\d){9}|(?:[A-Za-z]:\\|\\\\)[^\r\n]{0,1024}(?:profiles?|user[ _-]?data)(?:\\[^\r\n]{0,1024})?|\/[^\s]*profile(?:s?\/[^\s]*)?)/iu;

type ScreenshotSurface = {
  textLines: string[];
  textNodes: string[];
  attributeNames: string[];
  attributes: Array<{
    value: string;
    isSameOriginBlobImageSource: boolean;
  }>;
  controls: string[];
  machineReadableVisuals: Array<{ payloadFree: boolean }>;
  computedGeneratedContentSafe: boolean;
  computedResourceStylesSafe: boolean;
  url: string;
  windowName: string;
  scriptReadableCookieLength: number | null;
  cookieStoreEntryCount: number | null;
  historyState: string | null;
  localStorage: Array<{ key: string; value: string }>;
  sessionStorage: Array<{ key: string; value: string }>;
  indexedDbDatabaseCount: number | null;
  cacheStorageCount: number | null;
  serviceWorkerRegistrationCount: number | null;
  opfsRootEntryCount: number | null;
  storageBucketCount: number | null;
  legacyFileSystemTemporaryRootEntryCount: number | null;
  legacyFileSystemPersistentRootEntryCount: number | null;
};

export function isSafeScreenshotSurface(value: ScreenshotSurface): boolean {
  return screenshotSurfaceIssues(value).length === 0;
}

function screenshotSurfaceIssues(value: ScreenshotSurface): string[] {
  const parts = [
    ...value.textLines,
    ...value.textNodes,
    value.url,
    value.windowName,
    ...value.attributeNames,
    ...value.attributes.map(({ value: attributeValue }) => attributeValue),
    ...value.controls,
    ...(value.historyState === null ? [] : [value.historyState]),
    ...value.localStorage.flatMap(({ key, value: item }) => [key, item]),
    ...value.sessionStorage.flatMap(({ key, value: item }) => [key, item]),
  ];
  const size = parts.reduce((total, part) => total + part.length, 0);
  const issues: string[] = [];
  if (size > screenshotSurfaceLimit) issues.push('oversized');
  const secretTextNode = value.textNodes.some(containsRenderedSecret);
  if (secretTextNode) issues.push('text-node');
  if (!secretTextNode && value.textLines.some(containsRenderedSecret)) {
    issues.push('text-line');
  }
  if (value.attributeNames.some(containsClientSecretKey)) issues.push('attribute-names');
  if (
    value.attributes.some(
      ({ value: attributeValue, isSameOriginBlobImageSource }) =>
        !isSameOriginBlobImageSource && containsClientSecret(attributeValue),
    )
  ) {
    issues.push('attributes');
  }
  if (value.controls.some(containsClientSecret)) issues.push('controls');
  if (value.scriptReadableCookieLength !== 0) issues.push('script-readable-cookie');
  if (value.cookieStoreEntryCount !== 0) issues.push('cookie-store');
  if (value.windowName.length !== 0) issues.push('window-name');
  if (value.historyState === null || containsRenderedSecret(value.historyState)) {
    issues.push('history-state');
  }
  if (value.machineReadableVisuals.some(({ payloadFree }) => !payloadFree)) {
    issues.push('machine-readable-visual');
  }
  if (!value.computedGeneratedContentSafe) issues.push('computed-generated-content');
  if (!value.computedResourceStylesSafe) issues.push('computed-resource-style');
  if (value.indexedDbDatabaseCount !== 0) issues.push('indexed-db');
  if (value.cacheStorageCount !== 0) issues.push('cache-storage');
  if (value.serviceWorkerRegistrationCount !== 0) issues.push('service-worker');
  if (value.opfsRootEntryCount !== 0) issues.push('opfs');
  if (value.storageBucketCount !== 0) issues.push('storage-buckets');
  if (value.legacyFileSystemTemporaryRootEntryCount !== 0) {
    issues.push('legacy-filesystem-temporary');
  }
  if (value.legacyFileSystemPersistentRootEntryCount !== 0) {
    issues.push('legacy-filesystem-persistent');
  }
  if (containsUrlSecret(value.url)) issues.push('url');
  for (const [label, storage] of [
    ['local-storage', value.localStorage],
    ['session-storage', value.sessionStorage],
  ] as const) {
    if (storage.some(({ key }) => containsClientSecretKey(key))) {
      issues.push(`${label}-key`);
    }
    if (storage.some(({ value: item }) => containsClientSecret(item))) {
      issues.push(`${label}-value`);
    }
  }
  return issues;
}

function containsRenderedSecret(value: string): boolean {
  let normalized = value.normalize('NFKC').replace(renderedSecretInvisiblePattern, '');
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const decoded = decodeURIComponent(normalized);
      if (decoded === normalized) break;
      normalized = decoded.normalize('NFKC').replace(renderedSecretInvisiblePattern, '');
    } catch {
      break;
    }
  }
  return renderedSecretPattern.test(normalized);
}

function containsUrlSecret(value: string): boolean {
  try {
    const url = new URL(value);
    if (
      url.username.length > 0 ||
      url.password.length > 0 ||
      containsClientSecretKey(decodeURIComponent(url.hash)) ||
      containsClientSecret(decodeURIComponent(url.hash))
    ) {
      return true;
    }
    if (
      url.pathname
        .split('/')
        .filter(Boolean)
        .some(
          (segment) =>
            containsClientSecretKey(decodeURIComponent(segment)) ||
            containsClientSecret(decodeURIComponent(segment)),
        )
    ) {
      return true;
    }
    return [...url.searchParams].some(
      ([key, item]) => containsClientSecretKey(key) || containsClientSecret(item),
    );
  } catch {
    return true;
  }
}

async function inspectScreenshotSurface(page: Page): Promise<string[]> {
  const value = await page.evaluate(async () => {
    const semanticAttributeNames = new Set([
      'action',
      'alt',
      'data',
      'formaction',
      'href',
      'id',
      'name',
      'poster',
      'src',
      'title',
    ]);
    const attributeNames: string[] = [];
    const attributes: Array<{
      value: string;
      isSameOriginBlobImageSource: boolean;
    }> = [];
    const textNodes: string[] = [];
    let computedGeneratedContentSafe = true;
    let computedGeneratedContentSize = 0;
    let computedResourceStylesSafe = true;
    const computedResourceProperties = [
      'background-image',
      'mask-image',
      '-webkit-mask-image',
      'border-image-source',
      'list-style-image',
      'cursor',
      'content',
    ];
    const generatedContentInvisiblePattern = /[\u200b-\u200d\u2060\ufeff]/gu;
    const generatedContentSecretPattern =
      /(?:\bbearer\s+[A-Za-z0-9%._~+/=-]{6,}|\b(?:cookie|set-cookie|session|access_token|refresh_token|pairing_token|token|otp|password|proxy_password|profile_path)\s*["']?\s*[:=]\s*["']?[^\s"'<]{4,}|dlp-canary|(?:^|[^\w])\d{6}(?:[^\w]|$)|1[3-9](?:[\s().-]?\d){9}|(?:[A-Za-z]:\\|\\\\)[^\r\n]{0,1024}(?:profiles?|user[ _-]?data)(?:\\[^\r\n]{0,1024})?|\/[^\s]*profile(?:s?\/[^\s]*)?)/iu;
    const generatedContentContainsSecret = (value: string) => {
      let normalized = value.normalize('NFKC').replace(generatedContentInvisiblePattern, '');
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          const decoded = decodeURIComponent(normalized);
          if (decoded === normalized) break;
          normalized = decoded.normalize('NFKC').replace(generatedContentInvisiblePattern, '');
        } catch {
          break;
        }
      }
      return generatedContentSecretPattern.test(normalized);
    };
    const textWalker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
    while (textWalker.nextNode()) textNodes.push(textWalker.currentNode.nodeValue ?? '');
    for (const element of document.querySelectorAll<HTMLElement | SVGElement>('*')) {
      for (const attribute of element.attributes) {
        if (
          semanticAttributeNames.has(attribute.name) ||
          attribute.name.startsWith('aria-') ||
          attribute.name.startsWith('data-')
        ) {
          attributeNames.push(attribute.name);
          let isSameOriginBlobImageSource = false;
          if (element instanceof HTMLImageElement && attribute.name === 'src') {
            try {
              const resourceUrl = new URL(attribute.value);
              isSameOriginBlobImageSource =
                resourceUrl.protocol === 'blob:' && resourceUrl.origin === location.origin;
            } catch {
              // Invalid resource attributes remain subject to the normal secret scan.
            }
          }
          attributes.push({
            value: attribute.value,
            isSameOriginBlobImageSource,
          });
        }
      }
      if (element instanceof HTMLElement) {
        for (const property of element.style) {
          const styleValue = element.style.getPropertyValue(property);
          if (styleValue.includes('url(')) {
            attributes.push({
              value: styleValue,
              isSameOriginBlobImageSource: false,
            });
          }
        }
      }
      for (const pseudo of [null, '::before', '::after']) {
        const computedStyle = getComputedStyle(element, pseudo);
        if (
          computedResourceProperties.some((property) =>
            /url\(/iu.test(computedStyle.getPropertyValue(property)),
          )
        ) {
          computedResourceStylesSafe = false;
        }
        if (pseudo === null) continue;
        const content = computedStyle.content;
        if (!content || content === 'none' || content === 'normal') continue;
        computedGeneratedContentSize += content.length;
        if (
          computedGeneratedContentSize > 2_000_000 ||
          /url\(/iu.test(content) ||
          generatedContentContainsSecret(content)
        ) {
          computedGeneratedContentSafe = false;
        }
      }
    }
    const machineReadableVisuals = Array.from(
      document.querySelectorAll<HTMLElement | SVGElement>(
        '[role="img"],img,canvas,svg,object,embed,picture,video',
      ),
    )
      .filter((element) => {
        const label = [
          element.getAttribute('aria-label'),
          element.getAttribute('alt'),
          element.getAttribute('title'),
        ]
          .filter(Boolean)
          .join(' ');
        return /(?:二维码|qr(?:\s+code)?|pairing\s+code)/iu.test(label);
      })
      .map((element) => {
        const resourceAttributes = ['src', 'srcset', 'href', 'xlink:href', 'data', 'poster'];
        const candidates = [element, ...element.querySelectorAll('*')];
        const hasResourceAttribute = candidates.some((candidate) =>
          resourceAttributes.some((attribute) => candidate.hasAttribute(attribute)),
        );
        const hasComputedResource = candidates.some((candidate) => {
          for (const pseudo of [null, '::before', '::after']) {
            const style = getComputedStyle(candidate, pseudo);
            for (let index = 0; index < style.length; index += 1) {
              if (style.getPropertyValue(style.item(index)).includes('url(')) return true;
            }
          }
          return false;
        });
        return {
          payloadFree:
            element instanceof HTMLDivElement &&
            element.dataset.visualEvidence === 'payload-free' &&
            element.querySelector('img,canvas,svg,picture,video,object,embed') === null &&
            !hasResourceAttribute &&
            !candidates.some((candidate) => candidate.hasAttribute('style')) &&
            !hasComputedResource,
        };
      });
    type LegacyFileSystem = {
      root: {
        createReader: () => {
          readEntries: (success: (entries: unknown[]) => void, failure: () => void) => void;
        };
      };
    };
    const legacyRequestFileSystem = (
      globalThis as typeof globalThis & {
        webkitRequestFileSystem?: (
          type: number,
          size: number,
          success: (fileSystem: LegacyFileSystem) => void,
          failure: () => void,
        ) => void;
      }
    ).webkitRequestFileSystem;
    const countLegacyFileSystemRootEntries = (type: number): Promise<number | null> => {
      if (typeof legacyRequestFileSystem !== 'function') return Promise.resolve(0);
      return new Promise((resolve) => {
        try {
          legacyRequestFileSystem(
            type,
            1_024,
            (fileSystem) => {
              const reader = fileSystem.root.createReader();
              let count = 0;
              const readNextBatch = () => {
                reader.readEntries(
                  (entries) => {
                    count += entries.length;
                    if (entries.length === 0 || count > 10_000) resolve(count);
                    else readNextBatch();
                  },
                  () => resolve(null),
                );
              };
              readNextBatch();
            },
            () => resolve(null),
          );
        } catch {
          resolve(null);
        }
      });
    };
    const [legacyFileSystemTemporaryRootEntryCount, legacyFileSystemPersistentRootEntryCount] =
      await Promise.all([countLegacyFileSystemRootEntries(0), countLegacyFileSystemRootEntries(1)]);
    return {
      textLines: (document.body.innerText ?? '').split(/\r?\n/u),
      textNodes,
      attributeNames,
      attributes,
      controls: Array.from(
        document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
          'input,textarea,select',
        ),
        (control) => control.value,
      ),
      machineReadableVisuals,
      computedGeneratedContentSafe,
      computedResourceStylesSafe,
      url: location.href,
      windowName: globalThis.name,
      scriptReadableCookieLength: document.cookie.length,
      cookieStoreEntryCount: await (() => {
        const cookieStore = (
          globalThis as typeof globalThis & {
            cookieStore?: { getAll: () => Promise<unknown[]> };
          }
        ).cookieStore;
        return typeof cookieStore?.getAll !== 'function'
          ? Promise.resolve(0)
          : cookieStore.getAll().then(
              (cookies) => cookies.length,
              () => null,
            );
      })(),
      historyState: (() => {
        try {
          return JSON.stringify(history.state) ?? null;
        } catch {
          return null;
        }
      })(),
      localStorage: Object.entries(localStorage).map(([key, item]) => ({
        key,
        value: item,
      })),
      sessionStorage: Object.entries(sessionStorage).map(([key, item]) => ({
        key,
        value: item,
      })),
      indexedDbDatabaseCount:
        typeof indexedDB === 'undefined'
          ? 0
          : typeof indexedDB.databases !== 'function'
            ? null
            : await indexedDB.databases().then(
                (databases) => databases.length,
                () => null,
              ),
      cacheStorageCount:
        typeof globalThis.caches === 'undefined'
          ? 0
          : await globalThis.caches.keys().then(
              (cacheNames) => cacheNames.length,
              () => null,
            ),
      serviceWorkerRegistrationCount:
        typeof navigator.serviceWorker === 'undefined'
          ? 0
          : await navigator.serviceWorker.getRegistrations().then(
              (registrations) => registrations.length,
              () => null,
            ),
      opfsRootEntryCount:
        typeof navigator.storage?.getDirectory !== 'function'
          ? 0
          : await navigator.storage.getDirectory().then(
              async (root) => {
                let count = 0;
                for await (const _entry of root.values()) {
                  count += 1;
                  if (count > 10_000) break;
                }
                return count;
              },
              () => null,
            ),
      storageBucketCount: await (() => {
        const storageBuckets = (
          navigator as Navigator & {
            storageBuckets?: { keys: () => Promise<string[]> };
          }
        ).storageBuckets;
        return typeof storageBuckets?.keys !== 'function'
          ? Promise.resolve(0)
          : storageBuckets.keys().then(
              (bucketNames) => bucketNames.length,
              () => null,
            );
      })(),
      legacyFileSystemTemporaryRootEntryCount,
      legacyFileSystemPersistentRootEntryCount,
    };
  });
  return screenshotSurfaceIssues(value);
}

function assertSafeSnapshotName(name: string): void {
  if (!snapshotNamePattern.test(name)) {
    throw new Error('Visual snapshot name must be one fixed PNG basename.');
  }
}

function resolveSafeEvidencePath(path: string): string {
  const target = isAbsolute(path) ? resolve(path) : resolve(process.cwd(), path);
  const contained = visualEvidenceRoots.some((root) => {
    const child = relative(root, target);
    return (
      child.length > 0 && child !== '..' && !child.startsWith(`..${sep}`) && !isAbsolute(child)
    );
  });
  if (!contained || !target.endsWith('.png')) {
    throw new Error('Visual evidence path must be a PNG inside an approved test evidence root.');
  }
  return target;
}

async function requireSafeScreenshotSurface(page: Page): Promise<void> {
  const issues = await inspectScreenshotSurface(page);
  expect(
    issues,
    `Visual evidence rejected by DLP (${issues.join(',')}); raw rendered values are intentionally omitted.`,
  ).toEqual([]);
}

export async function captureSafeScreenshot(
  page: Page,
  options: PageScreenshotOptions & { path: string },
): Promise<void> {
  const target = resolveSafeEvidencePath(options.path);
  const issues = await inspectScreenshotSurface(page);
  if (issues.length > 0) await rm(target, { force: true });
  expect(
    issues,
    `Visual evidence rejected by DLP (${issues.join(',')}); raw rendered values are intentionally omitted.`,
  ).toEqual([]);
  await page.screenshot({ ...options, path: target });
}

export async function expectSafePageScreenshot(
  page: Page,
  name: string,
  options?: PageAssertionsToHaveScreenshotOptions,
): Promise<void> {
  assertSafeSnapshotName(name);
  await requireSafeScreenshotSurface(page);
  await expect(page).toHaveScreenshot(name, options);
}

export async function expectSafeLocatorScreenshot(
  page: Page,
  locator: Locator,
  name: string,
  options?: {
    animations?: 'disabled' | 'allow';
    caret?: 'hide' | 'initial';
    maxDiffPixelRatio?: number;
    maxDiffPixels?: number;
    omitBackground?: boolean;
    timeout?: number;
  },
): Promise<void> {
  assertSafeSnapshotName(name);
  await requireSafeScreenshotSurface(page);
  await expect(locator).toHaveScreenshot(name, options);
}
