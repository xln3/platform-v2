import { chromium, request } from '@playwright/test';
import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { extname, isAbsolute, join, normalize, relative, resolve, sep } from 'node:path';
import {
  collectBrowserRuntimeEvidence,
  isBrowserRuntimeEvidenceClean,
  persistSafeBrowserScreenshot,
} from './browser_runtime_evidence.mjs';
import { loadLegacySessionCookie, loadNativeSessionCookie } from './production_identity.mjs';

const baseURL = process.env.S04_PRODUCTION_URL ?? 'https://127.0.0.1:8443';
const identity = process.env.S04_NATIVE_SESSION_TOKEN
  ? loadNativeSessionCookie(
      process.env.S04_NATIVE_SESSION_TOKEN,
      baseURL,
      process.env.S04_NATIVE_SESSION_ROLE ?? 'operator',
    )
  : loadLegacySessionCookie(process.env.S04_LEGACY_SESSION_DB, baseURL);
const candidateRootInput = process.env.S03_FRONTEND_CANDIDATE_ROOT;
const candidateRoot = candidateRootInput ? realpathSync(candidateRootInput) : null;
const candidateApplicationDirectories = new Map([
  ['customer', 'customer-web'],
  ['operations', 'operations-web'],
  ['reports', 'report-studio'],
  ['intelligence', 'intelligence-web'],
]);
const candidateContentTypes = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.ico', 'image/x-icon'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.mjs', 'text/javascript; charset=utf-8'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
  ['.wasm', 'application/wasm'],
  ['.woff', 'font/woff'],
  ['.woff2', 'font/woff2'],
]);
let candidateManifest = null;
if (candidateRoot !== null) {
  const allowedRoot = realpathSync('.frontend-releases');
  const relativeCandidate = relative(allowedRoot, candidateRoot);
  if (
    !candidateRootInput ||
    !isAbsolute(candidateRootInput) ||
    relativeCandidate.startsWith(`..${sep}`) ||
    relativeCandidate === '..' ||
    isAbsolute(relativeCandidate)
  ) {
    throw new Error('candidate_root_outside_frontend_releases');
  }
  const manifestPath = resolve(candidateRoot, '..', 'manifest.json');
  candidateManifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  if (
    candidateManifest?.status !== 'prepared' ||
    typeof candidateManifest?.release_id !== 'string' ||
    candidateManifest?.secrets_recorded !== false
  ) {
    throw new Error('candidate_manifest_not_prepared');
  }
  for (const applicationDirectory of candidateApplicationDirectories.values()) {
    const clientRoot = join(candidateRoot, applicationDirectory, 'client');
    if (!statSync(clientRoot).isDirectory() || !statSync(join(clientRoot, 'index.html')).isFile()) {
      throw new Error('candidate_application_missing');
    }
  }
}
const outputDirectory =
  candidateManifest === null
    ? 'tests/s04-evidence/production-screenshots'
    : `tests/s04-evidence/frontend-candidate-screenshots/${candidateManifest.release_id}`;
const outputFile =
  candidateManifest === null
    ? 'tests/s04-evidence/production-browser-acceptance.json'
    : 'tests/s04-evidence/frontend-candidate-browser-acceptance.json';
const applications = [
  { name: 'customer', path: 'customer' },
  {
    name: 'customer-profile',
    url: '/platform/customer/?section=profile',
    expectedHeading: '甲方资料',
  },
  {
    name: 'customer-assets',
    url: '/platform/customer/?section=assets',
    expectedHeading: '品牌、产品与竞品',
  },
  {
    name: 'customer-accounts',
    url: '/platform/customer/?section=accounts',
    expectedHeading: '平台账号与授权',
  },
  {
    name: 'customer-monitoring',
    url: '/platform/customer/?section=monitoring',
    expectedHeading: '模型表现',
  },
  {
    name: 'customer-members',
    url: '/platform/customer/?section=members',
    expectedHeading: '项目成员',
  },
  {
    name: 'operations-overview',
    url: '/platform/operations/?section=overview',
    expectedHeading: '运行时间线',
  },
  {
    name: 'operations-sessions',
    url: '/platform/operations/?section=sessions',
    expectedHeading: '授权、租约与会话健康',
  },
  {
    name: 'operations-interventions',
    url: '/platform/operations/?section=interventions',
    expectedHeading: '人工接管队列',
  },
  {
    name: 'operations-events',
    url: '/platform/operations/?section=events',
    expectedHeading: '账号生命周期事件',
  },
  {
    name: 'operations-execution',
    path: 'operations/execution',
    expectedHeading: '执行与账号控制面',
    sharedShell: false,
  },
  { name: 'reports', path: 'reports' },
  { name: 'intelligence', path: 'intelligence' },
  {
    name: 'intelligence-history',
    url: '/platform/intelligence/?section=history',
  },
  {
    name: 'intelligence-calibration',
    url: '/platform/intelligence/?section=calibration',
    expectedHeading: '模型校准与准入',
  },
];
const viewports = [
  { name: 'desktop', width: 1600, height: 1100 },
  { name: 'tablet', width: 1024, height: 768 },
  { name: 'mobile', width: 390, height: 844 },
];

await mkdir(outputDirectory, { recursive: true });

function candidateFileFor(requestURL) {
  if (candidateRoot === null) return null;
  const pathname = decodeURIComponent(new URL(requestURL).pathname);
  const match = /^\/platform\/(customer|operations|reports|intelligence)(?:\/(.*))?$/u.exec(
    pathname,
  );
  if (match === null) return null;
  const [, application, requested = ''] = match;
  const applicationDirectory = candidateApplicationDirectories.get(application);
  if (applicationDirectory === undefined) throw new Error('candidate_application_unknown');
  const clientRoot = realpathSync(join(candidateRoot, applicationDirectory, 'client'));
  const normalizedRequest = normalize(requested).replace(/^(\.\.(\/|\\|$))+/, '');
  let file = join(clientRoot, normalizedRequest);
  if (!existsSync(file) || !statSync(file).isFile()) file = join(clientRoot, 'index.html');
  const resolvedFile = realpathSync(file);
  const relativeFile = relative(clientRoot, resolvedFile);
  if (relativeFile.startsWith(`..${sep}`) || relativeFile === '..' || isAbsolute(relativeFile)) {
    throw new Error('candidate_file_outside_client_root');
  }
  return resolvedFile;
}

async function waitForStableProduction() {
  const client = await request.newContext({
    baseURL,
    ignoreHTTPSErrors: true,
  });
  let consecutiveHealthyResponses = 0;
  try {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const response = await client.get('/api/v2/health', { timeout: 2_000 });
        consecutiveHealthyResponses =
          response.status() === 200 ? consecutiveHealthyResponses + 1 : 0;
      } catch {
        consecutiveHealthyResponses = 0;
      }
      if (consecutiveHealthyResponses >= 3) return;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  } finally {
    await client.dispose();
  }
  throw new Error('production_health_not_stable');
}

await waitForStableProduction();
const browser = await chromium.launch({ headless: true, channel: 'chromium' });
const results = [];

async function verify({ name, path, url, expectedHeading, sharedShell = true }, viewport) {
  const context = await browser.newContext({
    viewport,
    ignoreHTTPSErrors: true,
  });
  await context.addCookies([identity.cookie]);
  if (candidateRoot !== null) {
    await context.route('**/platform/**', async (route) => {
      const file = candidateFileFor(route.request().url());
      if (file === null) {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        path: file,
        headers: {
          'Cache-Control': 'no-store',
          'Content-Type': candidateContentTypes.get(extname(file)) ?? 'application/octet-stream',
        },
      });
    });
  }
  const page = await context.newPage();
  const runtimeEvidence = collectBrowserRuntimeEvidence(page);
  try {
    await page.addInitScript(() => {
      localStorage.setItem('geo.session.tenant', 'tnt_browser_controlled_claim');
      localStorage.setItem('geo.session.actor', 'usr_browser_controlled_claim');
      localStorage.setItem('geo.session.role', 'admin');
    });

    const response = await page.goto(`${baseURL}${url ?? `/platform/${path}/`}`, {
      waitUntil: 'networkidle',
      timeout: 30_000,
    });
    await page.waitForTimeout(500);
    const expectedContentVisible = expectedHeading
      ? await page.getByRole('heading', { name: expectedHeading }).isVisible()
      : true;
    const role = await page.evaluate(async () => {
      const result = await fetch('/api/v2/identity/session');
      if (!result.ok) return null;
      const value = await result.json();
      return typeof value?.role === 'string' &&
        ['customer', 'operator', 'analyst', 'reviewer', 'admin'].includes(value.role)
        ? value.role
        : null;
    });
    const body = await page.locator('body').innerText();
    const authenticated = Boolean(role);
    const workspaceAvailable =
      !body.includes('无权查看') && !body.includes('暂时不可用') && !body.includes('无法显示');
    const screenshotText = [body];
    const antiGeoGovernanceBoundaryPassed =
      name !== 'intelligence-calibration' ||
      (body.includes('外部授权数据集') &&
        body.includes('真实 intelligence API') &&
        !body.includes('contract fixture'));
    const navigationBadgeCount = sharedShell ? await page.locator('.sidebar nav em').count() : 0;
    let liveNotificationBoundaryPassed = !sharedShell;
    if (sharedShell) {
      const notificationButton = page.getByRole('button', { name: '通知' });
      if ((await notificationButton.count()) === 1) {
        await notificationButton.click();
        const notificationDialog = page.getByRole('dialog', { name: '通知中心' });
        if (await notificationDialog.isVisible()) {
          const notificationText = await notificationDialog.innerText();
          screenshotText.push(notificationText);
          liveNotificationBoundaryPassed =
            notificationText.includes('当前安全投影未提供通知集合') &&
            !notificationText.includes('数据窗口已冻结') &&
            !notificationText.includes('有一项待人工确认');
          await page.getByRole('button', { name: '关闭通知中心' }).click();
        }
      }
    }
    const staleBrowserIdentityKeys = await page.evaluate(() =>
      ['geo.session.tenant', 'geo.session.actor', 'geo.session.role'].filter(
        (key) => localStorage.getItem(key) !== null,
      ),
    );
    const screenshot = `${outputDirectory}/${name}-${viewport.name}.png`;
    const safeScreenshot = await persistSafeBrowserScreenshot(page, screenshot, screenshotText);
    results.push({
      application: name,
      role,
      viewport: viewport.name,
      entry_status: response?.status() ?? 0,
      screenshot: safeScreenshot.screenshot,
      secret_material_absent: safeScreenshot.secretMaterialAbsent,
      authenticated,
      workspace_available: workspaceAvailable,
      expected_content_visible: expectedContentVisible,
      runtime_issue_counts: { ...runtimeEvidence.counts },
      stale_browser_identity_keys: staleBrowserIdentityKeys,
      unverified_navigation_badges: navigationBadgeCount,
      live_notification_boundary_passed: liveNotificationBoundaryPassed,
      anti_geo_governance_boundary_passed: antiGeoGovernanceBoundaryPassed,
    });
  } finally {
    runtimeEvidence.stop();
    await context.close();
  }
}

try {
  for (const application of applications) {
    for (const viewport of viewports) await verify(application, viewport);
  }
} finally {
  await browser.close();
}

const passed = results.filter(
  (result) =>
    result.entry_status === 200 &&
    result.authenticated &&
    result.workspace_available &&
    result.expected_content_visible &&
    result.secret_material_absent &&
    result.screenshot !== null &&
    isBrowserRuntimeEvidenceClean(result.runtime_issue_counts) &&
    result.stale_browser_identity_keys.length === 0 &&
    result.unverified_navigation_badges === 0 &&
    result.live_notification_boundary_passed &&
    result.anti_geo_governance_boundary_passed,
).length;
const evidence = {
  generated_at: new Date().toISOString(),
  production_url: baseURL,
  qualification:
    candidateManifest === null
      ? {
          kind: 'active_production_assets',
          production_assets_mutated: false,
        }
      : {
          kind: 'isolated_frontend_candidate',
          release_id: candidateManifest.release_id,
          source_sha256: candidateManifest.source?.sha256 ?? null,
          production_assets_mutated: false,
        },
  result: passed === results.length ? 'passed' : 'failed',
  identity: {
    source: 'legacy_http_only_session',
    legacy_role: identity.legacyRole,
    verified_v2_roles: [...new Set(results.map((result) => result.role).filter(Boolean))],
    browser_actor_headers_used: results.some(
      (result) => result.runtime_issue_counts.forbidden_actor_header !== 0,
    ),
    secret_emitted: false,
  },
  checks: results,
  summary: {
    total: results.length,
    passed,
  },
};
await writeFile(outputFile, `${JSON.stringify(evidence, null, 2)}\n`);
console.log(
  JSON.stringify({
    ...evidence.summary,
    qualification: evidence.qualification.kind,
    release_id: evidence.qualification.release_id ?? null,
  }),
);
if (evidence.summary.passed !== evidence.summary.total) process.exitCode = 1;
