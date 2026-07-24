import { defineConfig } from '@playwright/test';

const webServerEnv = {
  ...process.env,
  CHOKIDAR_USEPOLLING: 'true',
  GEO_VITE_POLLING: '1',
  VITE_ALLOW_CONTRACT_FIXTURES: 'true',
};

export default defineConfig({
  testDir: './tests/e2e',
  outputDir: './tests/e2e-results',
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: 'tests/e2e-results/results.json' }]],
  use: {
    channel: 'chromium',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'customer-desktop',
      testMatch: /customer-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45101', viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'customer-tablet',
      testMatch: /customer-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45101', viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'customer-mobile',
      testMatch: /customer-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45101', viewport: { width: 390, height: 844 } },
    },
    {
      name: 'operations-desktop',
      testMatch: /operations-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45102', viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'operations-tablet',
      testMatch: /operations-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45102', viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'operations-mobile',
      testMatch: /operations-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45102', viewport: { width: 390, height: 844 } },
    },
    {
      name: 'reports-desktop',
      testMatch: /reports-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45103', viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'reports-tablet',
      testMatch: /reports-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45103', viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'reports-mobile',
      testMatch: /reports-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45103', viewport: { width: 390, height: 844 } },
    },
    {
      name: 'intelligence-desktop',
      testMatch: /intelligence-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45104', viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'intelligence-tablet',
      testMatch: /intelligence-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45104', viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'intelligence-mobile',
      testMatch: /intelligence-.*\.spec\.ts/,
      use: { baseURL: 'http://127.0.0.1:45104', viewport: { width: 390, height: 844 } },
    },
  ],
  webServer: [
    {
      command:
        'pnpm --filter @geo/customer-web build && pnpm --filter @geo/customer-web exec vite preview --base /platform/customer/ --host 127.0.0.1 --port 45101',
      url: 'http://127.0.0.1:45101/platform/customer/',
      env: webServerEnv,
      reuseExistingServer: true,
      timeout: 180_000,
    },
    {
      command:
        'pnpm --filter @geo/operations-web build && pnpm --filter @geo/operations-web exec vite preview --base /platform/operations/ --host 127.0.0.1 --port 45102',
      url: 'http://127.0.0.1:45102/platform/operations/',
      env: webServerEnv,
      reuseExistingServer: true,
      timeout: 180_000,
    },
    {
      command:
        'pnpm --filter @geo/report-studio build && pnpm --filter @geo/report-studio exec vite preview --base /platform/reports/ --host 127.0.0.1 --port 45103',
      url: 'http://127.0.0.1:45103/platform/reports/',
      env: webServerEnv,
      reuseExistingServer: true,
      timeout: 180_000,
    },
    {
      command:
        'pnpm --filter @geo/intelligence-web build && pnpm --filter @geo/intelligence-web exec vite preview --base /platform/intelligence/ --host 127.0.0.1 --port 45104',
      url: 'http://127.0.0.1:45104/platform/intelligence/',
      env: webServerEnv,
      reuseExistingServer: true,
      timeout: 180_000,
    },
  ],
});
