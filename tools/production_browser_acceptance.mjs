import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const tenant = process.env.S04_TENANT_ID;
if (!tenant) throw new Error('S04_TENANT_ID is required');

const baseURL = process.env.S04_PRODUCTION_URL ?? 'https://127.0.0.1:8443';
const outputDirectory = 'tests/s04-evidence/production-screenshots';
const applications = [
  { name: 'customer', role: 'customer', actor: 's04-acceptance-customer' },
  { name: 'operations', role: 'operator', actor: 's04-acceptance-operator' },
  { name: 'reports', role: 'analyst', actor: 's04-acceptance-analyst' },
  { name: 'intelligence', role: 'reviewer', actor: 's04-acceptance-reviewer' },
];
const viewports = [
  { name: 'desktop', width: 1600, height: 1100 },
  { name: 'tablet', width: 1024, height: 768 },
  { name: 'mobile', width: 390, height: 844 },
];

await mkdir(outputDirectory, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: 'chromium' });
const results = [];

async function verify({ name, role, actor }, viewport) {
  const context = await browser.newContext({
    viewport,
    ignoreHTTPSErrors: true,
  });
  await context.addInitScript(
    ({ tenantId, actorId, actorRole }) => {
      localStorage.setItem('geo.session.tenant', tenantId);
      localStorage.setItem('geo.session.actor', actorId);
      localStorage.setItem('geo.session.role', actorRole);
    },
    { tenantId: tenant, actorId: actor, actorRole: role },
  );
  const page = await context.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  const errorResponses = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => pageErrors.push(error.name));
  page.on('requestfailed', (request) => {
    failedRequests.push({
      method: request.method(),
      pathname: new URL(request.url()).pathname,
      error: request.failure()?.errorText ?? 'unknown',
    });
  });
  page.on('response', (response) => {
    if (response.status() >= 400) {
      errorResponses.push({
        status: response.status(),
        method: response.request().method(),
        pathname: new URL(response.url()).pathname,
      });
    }
  });

  const response = await page.goto(`${baseURL}/platform/${name}/`, {
    waitUntil: 'networkidle',
    timeout: 30_000,
  });
  await page.waitForTimeout(500);
  const body = await page.locator('body').innerText();
  const screenshot = `${outputDirectory}/${name}-${viewport.name}.png`;
  await page.screenshot({ path: screenshot, fullPage: true });
  const result = {
    application: name,
    role,
    viewport: viewport.name,
    entry_status: response?.status() ?? 0,
    title: await page.title(),
    screenshot,
    authenticated:
      !body.includes('无权查看') &&
      !body.includes('暂时不可用') &&
      !body.includes('无法显示'),
    console_errors: consoleErrors,
    page_errors: pageErrors,
    failed_requests: failedRequests,
    error_responses: errorResponses,
  };
  await context.close();
  results.push(result);
}

try {
  for (const application of applications) {
    for (const viewport of viewports) await verify(application, viewport);
  }
  await verify(
    { name: 'operations', role: 'admin', actor: process.env.S04_ADMIN_SUBJECT },
    { name: 'admin-desktop', width: 1600, height: 1100 },
  );
} finally {
  await browser.close();
}

const evidence = {
  generated_at: new Date().toISOString(),
  production_url: baseURL,
  tenant_pub_id: tenant,
  checks: results,
  summary: {
    total: results.length,
    passed: results.filter(
      (result) =>
        result.entry_status === 200 &&
        result.authenticated &&
        result.console_errors.length === 0 &&
        result.page_errors.length === 0 &&
        result.failed_requests.length === 0 &&
        result.error_responses.length === 0,
    ).length,
  },
};
await writeFile(
  'tests/s04-evidence/production-browser-acceptance.json',
  `${JSON.stringify(evidence, null, 2)}\n`,
);
console.log(JSON.stringify(evidence.summary));
if (evidence.summary.passed !== evidence.summary.total) process.exitCode = 1;
