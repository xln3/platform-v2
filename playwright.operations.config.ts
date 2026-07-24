import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'operations-execution.spec.ts',
  outputDir: './tests/e2e-results/operations',
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: 'tests/e2e-results/operations-results.json' }]],
  use: {
    baseURL: 'http://127.0.0.1:45112',
    channel: 'chromium',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1600, height: 1100 } } },
    { name: 'tablet', use: { viewport: { width: 1024, height: 768 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command:
      'VITEST=1 pnpm --filter @geo/operations-web exec vite preview --outDir build/client --host 127.0.0.1 --port 45112',
    url: 'http://127.0.0.1:45112/platform/operations/execution',
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
