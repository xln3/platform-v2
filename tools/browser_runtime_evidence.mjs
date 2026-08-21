import { rm } from 'node:fs/promises';

const runtimeIssueKeys = [
  'console_error',
  'page_error',
  'request_failed',
  'error_response',
  'forbidden_actor_header',
];
const browserEvidenceTextLimit = 2_000_000;
const browserEvidenceStorageEntryLimit = 10_000;
const zeroWidthCharacters = /[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/gu;
const secretMaterialPatterns = [
  /\bBearer\s+[^\s,;]{6,}/iu,
  /\b(?:Cookie|Set-Cookie|SESSION|access_token|refresh_token|pairing_token|proxy_password|profile_path|biometric_material)\s*[:=]\s*\S+/iu,
  /\bOTP\s*[:=]?\s*\d{4,}/iu,
  /(?:^|[^\w])\d{3}[\s.-]\d{3}(?:[^\w]|$)/iu,
  /\botpauth:\/\/\S+/iu,
  /(?:[A-Za-z]:\\|\\\\)[^\r\n]{0,1024}(?:profiles?|user[ _-]?data)(?:\\[^\r\n]{0,1024})?/iu,
  /\/[^\s]*profile(?:s?\/[^\s]*)?/iu,
  /(?<!\d)1[3-9]\d{9}(?!\d)/u,
  /1[3-9](?:[\t ().-]?\d){9}/u,
];

function normalizeBrowserEvidenceCandidate(value) {
  let normalized = value.normalize('NFKC').replace(zeroWidthCharacters, '');
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const decoded = decodeURIComponent(normalized);
      if (decoded === normalized) break;
      normalized = decoded.normalize('NFKC').replace(zeroWidthCharacters, '');
    } catch {
      break;
    }
  }
  return normalized;
}

export function containsBrowserSecretMaterial(value) {
  if (typeof value !== 'string' || value.length > browserEvidenceTextLimit) return true;
  const normalized = normalizeBrowserEvidenceCandidate(value);
  return secretMaterialPatterns.some((pattern) => pattern.test(normalized));
}

export async function isBrowserSurfaceSecretMaterialAbsent(page) {
  try {
    return await page.evaluate(
      async ({ textLimit, storageEntryLimit }) => {
        const invisibleCharacters = /[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/gu;
        const secretKeyPattern =
          /cookie|authorization|token|otp|password|phone|profile|biometric|storage.?state|qr/iu;
        const secretValuePatterns = [
          /\bBearer\s+[^\s,;]{6,}/iu,
          /\b(?:Cookie|Set-Cookie|SESSION|access_token|refresh_token|pairing_token|proxy_password|profile_path|biometric_material)\s*[:=]\s*\S+/iu,
          /\bOTP\s*[:=]?\s*\d{4,}/iu,
          /(?:^|[^\w])\d{3}[\s.-]\d{3}(?:[^\w]|$)/iu,
          /\botpauth:\/\/\S+/iu,
          /(?:[A-Za-z]:\\|\\\\)[^\r\n]{0,1024}(?:profiles?|user[ _-]?data)(?:\\[^\r\n]{0,1024})?/iu,
          /\/[^\s]*profile(?:s?\/[^\s]*)?/iu,
          /(?<!\d)1[3-9]\d{9}(?!\d)/u,
          /1[3-9](?:[\t ().-]?\d){9}/u,
        ];
        const normalize = (value) => {
          let normalized = value.normalize('NFKC').replace(invisibleCharacters, '');
          for (let attempt = 0; attempt < 3; attempt += 1) {
            try {
              const decoded = decodeURIComponent(normalized);
              if (decoded === normalized) break;
              normalized = decoded.normalize('NFKC').replace(invisibleCharacters, '');
            } catch {
              break;
            }
          }
          return normalized;
        };
        const containsSecretKey = (value) => secretKeyPattern.test(normalize(value));
        const containsSecretValue = (value) => {
          const normalized = normalize(value);
          return secretValuePatterns.some((pattern) => pattern.test(normalized));
        };
        let inspectedSize = 0;
        const inspectValue = (value) => {
          inspectedSize += value.length;
          return inspectedSize <= textLimit && !containsSecretValue(value);
        };
        const inspectKeyValue = (key, value) => {
          inspectedSize += key.length + value.length;
          return (
            inspectedSize <= textLimit && !containsSecretKey(key) && !containsSecretValue(value)
          );
        };
        const inspectStructuredValue = (value, depth = 0) => {
          if (depth > 6) return false;
          if (typeof value === 'string') return inspectValue(value);
          if (typeof value === 'number') {
            return Number.isFinite(value) && inspectValue(String(value));
          }
          if (typeof value === 'boolean' || value === null || value === undefined) return true;
          if (Array.isArray(value)) {
            if (value.length > 1_000) return false;
            return value.every((item) => inspectStructuredValue(item, depth + 1));
          }
          if (typeof value !== 'object') return false;
          const entries = Object.entries(value);
          if (entries.length > 1_000) return false;
          return entries.every(
            ([key, item]) => inspectKeyValue(key, '') && inspectStructuredValue(item, depth + 1),
          );
        };
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
          'srcset',
          'title',
          'xlink:href',
        ]);
        if (!inspectValue(document.body?.innerText ?? '')) return false;
        const textWalker = document.createTreeWalker(
          document.documentElement,
          NodeFilter.SHOW_TEXT,
        );
        while (textWalker.nextNode()) {
          if (!inspectValue(textWalker.currentNode.nodeValue ?? '')) return false;
        }
        for (const element of document.querySelectorAll('*')) {
          for (const attribute of element.attributes) {
            if (
              semanticAttributeNames.has(attribute.name) ||
              attribute.name.startsWith('aria-') ||
              attribute.name.startsWith('data-')
            ) {
              if (containsSecretKey(attribute.name) || !inspectValue(attribute.value)) {
                return false;
              }
            }
          }
          for (const pseudo of [null, '::before', '::after']) {
            const computedStyle = getComputedStyle(element, pseudo);
            if (
              [
                'background-image',
                'mask-image',
                '-webkit-mask-image',
                'border-image-source',
                'list-style-image',
                'cursor',
                'content',
              ].some((property) => /url\(/iu.test(computedStyle.getPropertyValue(property)))
            ) {
              return false;
            }
            if (pseudo === null) continue;
            const content = computedStyle.content;
            if (content && content !== 'none' && content !== 'normal' && !inspectValue(content)) {
              return false;
            }
          }
        }
        for (const control of document.querySelectorAll('input,textarea,select')) {
          if (!inspectValue(control.value)) return false;
        }
        let url;
        try {
          url = new URL(location.href);
        } catch {
          return false;
        }
        const decodedHash = decodeURIComponent(url.hash);
        if (
          url.username ||
          url.password ||
          containsSecretKey(decodedHash) ||
          !inspectValue(decodedHash)
        ) {
          return false;
        }
        for (const segment of url.pathname.split('/').filter(Boolean)) {
          const decodedSegment = decodeURIComponent(segment);
          if (containsSecretKey(decodedSegment) || !inspectValue(decodedSegment)) return false;
        }
        for (const [key, value] of url.searchParams) {
          if (!inspectKeyValue(key, value)) return false;
        }
        for (const storage of [localStorage, sessionStorage]) {
          if (storage.length > storageEntryLimit) return false;
          for (let index = 0; index < storage.length; index += 1) {
            const key = storage.key(index);
            if (key === null || !inspectKeyValue(key, storage.getItem(key) ?? '')) return false;
          }
        }
        if (globalThis.name.length !== 0) return false;
        if (document.cookie.length !== 0) return false;
        if (typeof globalThis.cookieStore?.getAll === 'function') {
          const cookies = await globalThis.cookieStore.getAll();
          if (!Array.isArray(cookies) || cookies.length !== 0) return false;
        }
        if (!inspectStructuredValue(history.state)) return false;
        if (typeof indexedDB !== 'undefined') {
          if (typeof indexedDB.databases !== 'function') return false;
          const databases = await indexedDB.databases();
          if (!Array.isArray(databases) || databases.length !== 0) return false;
        }
        if (typeof globalThis.caches !== 'undefined') {
          const cacheNames = await globalThis.caches.keys();
          if (!Array.isArray(cacheNames) || cacheNames.length !== 0) return false;
        }
        if (typeof navigator.serviceWorker !== 'undefined') {
          const registrations = await navigator.serviceWorker.getRegistrations();
          if (!Array.isArray(registrations) || registrations.length !== 0) return false;
        }
        if (typeof navigator.storage?.getDirectory === 'function') {
          const root = await navigator.storage.getDirectory();
          for await (const _entry of root.values()) return false;
        }
        if (typeof navigator.storageBuckets?.keys === 'function') {
          const bucketNames = await navigator.storageBuckets.keys();
          if (!Array.isArray(bucketNames) || bucketNames.length !== 0) return false;
        }
        if (typeof globalThis.webkitRequestFileSystem === 'function') {
          const legacyFileSystemRootIsEmpty = (type) =>
            new Promise((resolve) => {
              try {
                globalThis.webkitRequestFileSystem(
                  type,
                  1_024,
                  (fileSystem) => {
                    const reader = fileSystem.root.createReader();
                    let count = 0;
                    const readNextBatch = () => {
                      reader.readEntries(
                        (entries) => {
                          count += entries.length;
                          if (entries.length === 0) resolve(count === 0);
                          else if (count > storageEntryLimit) resolve(false);
                          else readNextBatch();
                        },
                        () => resolve(false),
                      );
                    };
                    readNextBatch();
                  },
                  () => resolve(false),
                );
              } catch {
                resolve(false);
              }
            });
          if (!(await legacyFileSystemRootIsEmpty(0)) || !(await legacyFileSystemRootIsEmpty(1))) {
            return false;
          }
        }
        return true;
      },
      {
        textLimit: browserEvidenceTextLimit,
        storageEntryLimit: browserEvidenceStorageEntryLimit,
      },
    );
  } catch {
    return false;
  }
}

export async function isBrowserMachineReadableVisualSurfaceSafe(page) {
  try {
    return await page.evaluate(() => {
      const hasComputedResource = (element) => {
        for (const pseudo of [null, '::before', '::after']) {
          const style = getComputedStyle(element, pseudo);
          for (let index = 0; index < style.length; index += 1) {
            if (style.getPropertyValue(style.item(index)).includes('url(')) return true;
          }
        }
        return false;
      };
      const resourceAttributes = ['src', 'srcset', 'href', 'xlink:href', 'data', 'poster'];
      const machineReadableVisuals = Array.from(
        document.querySelectorAll('[role="img"],img,canvas,svg,object,embed,picture,video'),
      ).filter((element) => {
        const label = [
          element.getAttribute('aria-label'),
          element.getAttribute('alt'),
          element.getAttribute('title'),
        ]
          .filter(Boolean)
          .join(' ');
        return /(?:二维码|qr(?:\s+code)?|pairing\s+code)/iu.test(label);
      });
      return machineReadableVisuals.every((element) => {
        const candidates = [element, ...element.querySelectorAll('*')];
        return (
          element instanceof HTMLDivElement &&
          element.dataset.visualEvidence === 'payload-free' &&
          element.querySelector('img,canvas,svg,picture,video,object,embed') === null &&
          !candidates.some((candidate) =>
            resourceAttributes.some((attribute) => candidate.hasAttribute(attribute)),
          ) &&
          !candidates.some((candidate) => candidate.hasAttribute('style')) &&
          !candidates.some(hasComputedResource)
        );
      });
    });
  } catch {
    return false;
  }
}

export async function persistSafeBrowserScreenshot(page, path, textValues) {
  const textSecretMaterialAbsent =
    Array.isArray(textValues) &&
    textValues.length > 0 &&
    textValues.every((value) => !containsBrowserSecretMaterial(value));
  const browserSurfaceSecretMaterialAbsent =
    textSecretMaterialAbsent && (await isBrowserSurfaceSecretMaterialAbsent(page));
  const machineReadableVisualsSafe =
    browserSurfaceSecretMaterialAbsent && (await isBrowserMachineReadableVisualSurfaceSafe(page));
  const secretMaterialAbsent =
    textSecretMaterialAbsent && browserSurfaceSecretMaterialAbsent && machineReadableVisualsSafe;
  if (secretMaterialAbsent) {
    let captureError;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        await page.screenshot({ path, fullPage: true });
        captureError = undefined;
        break;
      } catch (error) {
        captureError = error;
        await rm(path, { force: true });
        if (attempt === 0) await new Promise((resolve) => setTimeout(resolve, 100));
      }
    }
    if (captureError) throw captureError;
  } else {
    await rm(path, { force: true });
  }
  return {
    screenshot: secretMaterialAbsent ? path : null,
    secretMaterialAbsent,
  };
}

export function collectBrowserRuntimeEvidence(page) {
  const counts = Object.seal({
    console_error: 0,
    page_error: 0,
    request_failed: 0,
    error_response: 0,
    forbidden_actor_header: 0,
  });
  const onConsole = (message) => {
    if (message.type() === 'error') counts.console_error += 1;
  };
  const onPageError = () => {
    counts.page_error += 1;
  };
  const onRequestFailed = () => {
    counts.request_failed += 1;
  };
  const onResponse = (response) => {
    if (response.status() >= 400) counts.error_response += 1;
  };
  const onRequest = (request) => {
    const headers = request.headers();
    if (headers['x-tenant-id'] || headers['x-actor-id'] || headers['x-actor-role']) {
      counts.forbidden_actor_header += 1;
    }
  };

  page.on('console', onConsole);
  page.on('pageerror', onPageError);
  page.on('requestfailed', onRequestFailed);
  page.on('response', onResponse);
  page.on('request', onRequest);

  return {
    counts,
    stop() {
      page.off('console', onConsole);
      page.off('pageerror', onPageError);
      page.off('requestfailed', onRequestFailed);
      page.off('response', onResponse);
      page.off('request', onRequest);
    },
  };
}

export function isBrowserRuntimeEvidenceClean(counts) {
  return runtimeIssueKeys.every((key) => Number.isSafeInteger(counts[key]) && counts[key] === 0);
}
