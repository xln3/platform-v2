import { expect, test as base } from '@playwright/test';
import { installDefaultCustomerDashboardRoutes } from './customer-dashboard-fixture';
import { collectBrowserRuntimeIssues, summarizeBrowserRuntimeIssues } from './runtime-guard';

export { expect };
export type { Page, Route } from '@playwright/test';

type BrowserPersistenceCounts = {
  'window-name-length': number | null;
  'script-readable-cookie-length': number | null;
  'cookie-store-entries': number | null;
  'indexed-db-databases': number | null;
  'cache-storage-entries': number | null;
  'service-worker-registrations': number | null;
  'opfs-root-entries': number | null;
  'storage-buckets': number | null;
  'legacy-filesystem-temporary-root-entries': number | null;
  'legacy-filesystem-persistent-root-entries': number | null;
};

const zeroBrowserPersistenceCounts: BrowserPersistenceCounts = {
  'window-name-length': 0,
  'script-readable-cookie-length': 0,
  'cookie-store-entries': 0,
  'indexed-db-databases': 0,
  'cache-storage-entries': 0,
  'service-worker-registrations': 0,
  'opfs-root-entries': 0,
  'storage-buckets': 0,
  'legacy-filesystem-temporary-root-entries': 0,
  'legacy-filesystem-persistent-root-entries': 0,
};

async function projectBrowserPersistenceCounts(page: import('@playwright/test').Page) {
  if (page.isClosed()) return zeroBrowserPersistenceCounts;
  return page
    .evaluate(async (): Promise<BrowserPersistenceCounts> => {
      if (location.origin === 'null') {
        return {
          'window-name-length': 0,
          'script-readable-cookie-length': 0,
          'cookie-store-entries': 0,
          'indexed-db-databases': 0,
          'cache-storage-entries': 0,
          'service-worker-registrations': 0,
          'opfs-root-entries': 0,
          'storage-buckets': 0,
          'legacy-filesystem-temporary-root-entries': 0,
          'legacy-filesystem-persistent-root-entries': 0,
        };
      }
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
      const cookieStore = (
        globalThis as typeof globalThis & {
          cookieStore?: { getAll: () => Promise<unknown[]> };
        }
      ).cookieStore;
      const cookieStoreEntryCount =
        typeof cookieStore?.getAll !== 'function'
          ? 0
          : await cookieStore.getAll().then(
              (cookies) => cookies.length,
              () => null,
            );
      const indexedDbDatabaseCount =
        typeof indexedDB === 'undefined'
          ? 0
          : typeof indexedDB.databases !== 'function'
            ? null
            : await indexedDB.databases().then(
                (databases) => databases.length,
                () => null,
              );
      const cacheStorageCount =
        typeof globalThis.caches === 'undefined'
          ? 0
          : await globalThis.caches.keys().then(
              (cacheNames) => cacheNames.length,
              () => null,
            );
      const serviceWorkerRegistrationCount =
        typeof navigator.serviceWorker === 'undefined'
          ? 0
          : await navigator.serviceWorker.getRegistrations().then(
              (registrations) => registrations.length,
              () => null,
            );
      const opfsRootEntryCount =
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
            );
      const storageBuckets = (
        navigator as Navigator & {
          storageBuckets?: { keys: () => Promise<string[]> };
        }
      ).storageBuckets;
      const storageBucketCount =
        typeof storageBuckets?.keys !== 'function'
          ? 0
          : await storageBuckets.keys().then(
              (bucketNames) => bucketNames.length,
              () => null,
            );
      const [legacyFileSystemTemporaryRootEntryCount, legacyFileSystemPersistentRootEntryCount] =
        await Promise.all([
          countLegacyFileSystemRootEntries(0),
          countLegacyFileSystemRootEntries(1),
        ]);
      return {
        'window-name-length': globalThis.name.length,
        'script-readable-cookie-length': document.cookie.length,
        'cookie-store-entries': cookieStoreEntryCount,
        'indexed-db-databases': indexedDbDatabaseCount,
        'cache-storage-entries': cacheStorageCount,
        'service-worker-registrations': serviceWorkerRegistrationCount,
        'opfs-root-entries': opfsRootEntryCount,
        'storage-buckets': storageBucketCount,
        'legacy-filesystem-temporary-root-entries': legacyFileSystemTemporaryRootEntryCount,
        'legacy-filesystem-persistent-root-entries': legacyFileSystemPersistentRootEntryCount,
      };
    })
    .catch(
      (): BrowserPersistenceCounts => ({
        'window-name-length': null,
        'script-readable-cookie-length': null,
        'cookie-store-entries': null,
        'indexed-db-databases': null,
        'cache-storage-entries': null,
        'service-worker-registrations': null,
        'opfs-root-entries': null,
        'storage-buckets': null,
        'legacy-filesystem-temporary-root-entries': null,
        'legacy-filesystem-persistent-root-entries': null,
      }),
    );
}

export const test = base.extend<{ browserRuntimeGuard: void }>({
  browserRuntimeGuard: [
    async ({ page }, use, testInfo) => {
      const collector = collectBrowserRuntimeIssues(page);
      try {
        // AI 操作面板只在客户信息表挂载，并会记忆用户的展开选择。E2E 统一使用已收起
        // 的回访态；需要审计展开交互的用例（如可访问性 spec）自行进入信息表后展开。
        await page.addInitScript(() => {
          try {
            if (localStorage.getItem('geo.ai.dock.expanded') === null) {
              localStorage.setItem('geo.ai.dock.expanded', '0');
            }
          } catch {
            // localStorage 不可用时保持应用缺省行为
          }
        });
        await page.route('**/api/v2/health', (route) =>
          route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              status: 'ok',
              service: 'geo-platform-v2',
              version: 'contract-v2',
            }),
          }),
        );
        // AiOpsDock 在 live 会话下会拉取模型清单；与上方 health 一样属于全局端点，
        // 在此统一兜底，避免未 mock 的用例打到不存在的上游（45200）而产生 request-failed。
        await page.route('**/api/v2/projects/*/intake/research-models', (route) =>
          route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              models: ['fixture-research-model'],
              groups: [{ provider: 'fixture', models: ['fixture-research-model'] }],
            }),
          }),
        );
        await page.route('**/api/v2/reports/ai-draft-models', (route) =>
          route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ models: ['fixture-draft-model'] }),
          }),
        );
        if (testInfo.project.name.startsWith('customer-')) {
          await installDefaultCustomerDashboardRoutes(page);
        }
        if (testInfo.project.name.startsWith('operations-')) {
          await page.route('**/api/v2/operations/business-overview**', (route) =>
            route.fulfill({
              status: 200,
              contentType: 'application/json',
              body: JSON.stringify({
                schema_version: 1,
                as_of: '2026-08-24T10:30:00Z',
                summary: {
                  scope: 'filtered',
                  tenant_project_count: 0,
                  project_count: 0,
                  project_state_counts: { draft: 0, active: 0, paused: 0, archived: 0 },
                  setup_ready_project_count: 0,
                  project_with_entitlement_record_count: 0,
                  active_entitlement_count: 0,
                  attention_project_count: 0,
                },
                commercial_capabilities: {
                  quotation_history: 'unsupported',
                  signed_contract_ledger: 'unsupported',
                  invoice_receivable_payment_ledger: 'unsupported',
                },
                items: [],
                page: { limit: 4, next_cursor: null, has_more: false, filtered_total: 0 },
              }),
            }),
          );
          await page.route('**/api/v2/operations/lifecycle**', (route) =>
            route.fulfill({
              status: 200,
              contentType: 'application/json',
              body: JSON.stringify({
                metrics: {
                  running_runs: 0,
                  project_count: 0,
                  pending_interventions: 0,
                  healthy_sessions: 0,
                  total_sessions: 0,
                  delayed_runs: 0,
                  p95_delay_seconds: null,
                },
                activity: [],
                accounts: [],
                interventions: [],
                events: [],
                projection: {
                  activity: { total: 0, shown: 0, truncated: false },
                  accounts: { total: 0, shown: 0, truncated: false },
                  interventions: { total: 0, shown: 0, truncated: false },
                  events: { total: 0, shown: 0, truncated: false },
                },
              }),
            }),
          );
        }
        await use();
      } finally {
        if (!page.isClosed()) await page.waitForTimeout(150);
        const browserPersistenceCounts = await projectBrowserPersistenceCounts(page);
        collector.stop();
        const observed = {
          ...summarizeBrowserRuntimeIssues(collector.issues),
          ...browserPersistenceCounts,
        };
        const expected = {
          'console-error': 0,
          'page-error': 0,
          'request-failed': 0,
          ...zeroBrowserPersistenceCounts,
        };
        if (
          collector.issues.length ||
          Object.values(browserPersistenceCounts).some((count) => count !== 0)
        ) {
          await testInfo.attach('browser-runtime-guard-summary', {
            body: Buffer.from(JSON.stringify({ observed, expected })),
            contentType: 'application/json',
          });
        }
        expect(
          observed,
          'Browser runtime must retain literal zero console errors, page errors, failed requests, Window.name length, script-readable Cookie length, Cookie Store entries, IndexedDB databases, Cache Storage entries, Service Worker registrations, OPFS root entries, named Storage Buckets and Legacy FileSystem temporary/persistent root entries. Only bounded counts are retained; raw messages, URLs, Window.name contents, Cookie names/values, database names, cache names, worker URLs, file names and bucket names are intentionally excluded from failure output.',
        ).toEqual(expected);
      }
    },
    { auto: true },
  ],
});
