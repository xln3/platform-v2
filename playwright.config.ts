import { defineConfig } from '@playwright/test';

const webServerEnv = {
  ...process.env,
  GEO_VITE_NO_WATCH: '1',
  GEO_E2E_BUILD: '1',
  VITE_ALLOW_CONTRACT_FIXTURES: 'true',
  VITE_GEO_API_BASE: '',
};
const e2ePortBase = Number(process.env.GEO_E2E_PORT_BASE ?? '45100');
if (!Number.isSafeInteger(e2ePortBase) || e2ePortBase < 1024 || e2ePortBase > 65530) {
  throw new Error('GEO_E2E_PORT_BASE must reserve five valid consecutive ports.');
}
const reuseExistingE2eServer = process.env.GEO_E2E_REUSE_SERVER === '1';
const appUrl = (offset: number) => `http://127.0.0.1:${e2ePortBase + offset}`;
const e2eOutputDir = process.env.GEO_E2E_OUTPUT_DIR ?? 'tests/e2e-results';
const e2eJsonReport = process.env.GEO_E2E_JSON_REPORT ?? 'tests/s04-evidence/e2e-results.json';

export default defineConfig({
  testDir: './tests/e2e',
  outputDir: e2eOutputDir,
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: e2eJsonReport }]],
  use: {
    channel: 'chromium',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'off',
    screenshot: 'off',
  },
  projects: [
    {
      name: 'customer-desktop',
      testMatch: /customer-.*\.spec\.ts/,
      use: { baseURL: appUrl(1), viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'customer-tablet',
      testMatch: /customer-.*\.spec\.ts/,
      use: { baseURL: appUrl(1), viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'customer-mobile',
      testMatch: /customer-.*\.spec\.ts/,
      use: { baseURL: appUrl(1), viewport: { width: 390, height: 844 } },
    },
    {
      name: 'operations-desktop',
      testMatch: /operations-.*\.spec\.ts/,
      use: { baseURL: appUrl(2), viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'operations-tablet',
      testMatch: /operations-.*\.spec\.ts/,
      use: { baseURL: appUrl(2), viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'operations-mobile',
      testMatch: /operations-.*\.spec\.ts/,
      use: { baseURL: appUrl(2), viewport: { width: 390, height: 844 } },
    },
    {
      name: 'reports-desktop',
      testMatch: /reports-.*\.spec\.ts/,
      use: { baseURL: appUrl(3), viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'reports-tablet',
      testMatch: /reports-.*\.spec\.ts/,
      use: { baseURL: appUrl(3), viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'reports-mobile',
      testMatch: /reports-.*\.spec\.ts/,
      use: { baseURL: appUrl(3), viewport: { width: 390, height: 844 } },
    },
    {
      name: 'intelligence-desktop',
      testMatch: /intelligence-.*\.spec\.ts/,
      use: { baseURL: appUrl(4), viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'intelligence-tablet',
      testMatch: /intelligence-.*\.spec\.ts/,
      use: { baseURL: appUrl(4), viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'intelligence-mobile',
      testMatch: /intelligence-.*\.spec\.ts/,
      use: { baseURL: appUrl(4), viewport: { width: 390, height: 844 } },
    },
    {
      name: 'intake-form-desktop',
      testMatch: /intake-form-.*\.spec\.ts/,
      use: { baseURL: appUrl(5), viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'intake-form-tablet',
      testMatch: /intake-form-.*\.spec\.ts/,
      use: { baseURL: appUrl(5), viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'intake-form-mobile',
      testMatch: /intake-form-.*\.spec\.ts/,
      use: { baseURL: appUrl(5), viewport: { width: 390, height: 844 } },
    },
  ],
  webServer: {
    command: 'exec bash scripts/start_e2e_webservers.sh',
    url: `${appUrl(1)}/platform/customer/`,
    env: webServerEnv,
    reuseExistingServer: reuseExistingE2eServer,
    timeout: 240_000,
  },
});
