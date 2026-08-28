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
const e2eOutputDir = process.env.GEO_E2E_OUTPUT_DIR ?? 'test-results/playwright/results';
const e2eJsonReport = process.env.GEO_E2E_JSON_REPORT ?? 'test-results/playwright/e2e-results.json';
const e2eWorkers = Number(process.env.GEO_E2E_WORKERS ?? '2');
if (!Number.isSafeInteger(e2eWorkers) || e2eWorkers < 1) {
  throw new Error('GEO_E2E_WORKERS must be a positive integer.');
}

// Desktop exercises every business flow. Tablet/mobile repeat only the suites that
// intentionally verify responsive layout, accessibility, navigation, or screenshots.
const customerResponsive =
  /customer-(?:accessibility|account|shared-shell|state-matrix|visual)\.spec\.ts/;
const operationsResponsive =
  /operations-(?:accessibility|readonly-pagination|shared-shell|visual)\.spec\.ts/;
const reportsResponsive = /reports-(?:accessibility|shared-shell|studio|visual)\.spec\.ts/;
const intelligenceResponsive =
  /intelligence-(?:accessibility|shared-shell|visual|workbench)\.spec\.ts/;

export default defineConfig({
  testDir: './tests/e2e',
  outputDir: e2eOutputDir,
  fullyParallel: false,
  workers: e2eWorkers,
  forbidOnly: true,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: e2eJsonReport }]],
  use: {
    channel: 'chromium',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'off',
    screenshot: 'off',
    launchOptions: {
      // 视觉基线跨环境确定性：关闭 hinting/次像素定位/LCD 次像素渲染，钉死 sRGB。
      // 基线机(jammy)与 CI(noble)的 freetype/harfbuzz 版本不同，hinted 渲染在字形边缘
      // 产生系统性像素差（run 31432561202 字体包对齐后仍 16 例稀疏文本边缘 diff）；
      // 无 hinting + 整数定位 + 灰阶 AA 的渲染路径跨版本稳定，本地与 CI 收敛同一像素。
      args: [
        '--font-render-hinting=none',
        '--disable-font-subpixel-positioning',
        '--disable-lcd-text',
        '--force-color-profile=srgb',
      ],
    },
  },
  projects: [
    {
      name: 'customer-desktop',
      testMatch: /customer-.*\.spec\.ts/,
      use: { baseURL: appUrl(1), viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'customer-tablet',
      testMatch: customerResponsive,
      use: { baseURL: appUrl(1), viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'customer-mobile',
      testMatch: customerResponsive,
      use: { baseURL: appUrl(1), viewport: { width: 390, height: 844 } },
    },
    {
      name: 'operations-desktop',
      testMatch: /operations-.*\.spec\.ts/,
      use: { baseURL: appUrl(2), viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'operations-tablet',
      testMatch: operationsResponsive,
      use: { baseURL: appUrl(2), viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'operations-mobile',
      testMatch: operationsResponsive,
      use: { baseURL: appUrl(2), viewport: { width: 390, height: 844 } },
    },
    {
      name: 'reports-desktop',
      testMatch: /reports-.*\.spec\.ts/,
      use: { baseURL: appUrl(3), viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'reports-tablet',
      testMatch: reportsResponsive,
      use: { baseURL: appUrl(3), viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'reports-mobile',
      testMatch: reportsResponsive,
      use: { baseURL: appUrl(3), viewport: { width: 390, height: 844 } },
    },
    {
      name: 'intelligence-desktop',
      testMatch: /intelligence-.*\.spec\.ts/,
      use: { baseURL: appUrl(4), viewport: { width: 1600, height: 1100 } },
    },
    {
      name: 'intelligence-tablet',
      testMatch: intelligenceResponsive,
      use: { baseURL: appUrl(4), viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'intelligence-mobile',
      testMatch: intelligenceResponsive,
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
