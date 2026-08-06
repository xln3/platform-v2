import { chromium } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import {
  collectBrowserRuntimeEvidence,
  isBrowserRuntimeEvidenceClean,
} from './browser_runtime_evidence.mjs';
import { loadLegacySessionCookie } from './production_identity.mjs';

const baseURL = process.env.S04_PRODUCTION_URL ?? 'https://127.0.0.1:8443';
const identity = loadLegacySessionCookie(process.env.S04_LEGACY_SESSION_DB, baseURL);
const matrix = {
  customer: {
    sections: [
      'home',
      'profile',
      'assets',
      'questions',
      'monitoring',
      'evidence',
      'reports',
      'members',
      'accounts',
    ],
  },
  operations: {
    sections: ['overview', 'sessions', 'interventions', 'events'],
  },
  reports: {
    sections: ['window', 'trace', 'editor', 'diff', 'evidence', 'preview', 'review', 'outcomes'],
  },
  intelligence: {
    sections: [
      'cases',
      'claims',
      'sources',
      'graph',
      'history',
      'calibration',
      'verdict',
      'package',
    ],
  },
};
const forbiddenText = [
  /contract fixture/iu,
  /contract-fixture/iu,
  /mock-ready/iu,
  /fixture tenant/iu,
  /fixture project/iu,
  /fixture user/iu,
];

const browser = await chromium.launch({ headless: true, channel: 'chromium' });
const checks = [];
try {
  for (const [application, config] of Object.entries(matrix)) {
    for (const section of config.sections) {
      const context = await browser.newContext({ ignoreHTTPSErrors: true });
      await context.addCookies([identity.cookie]);
      const page = await context.newPage();
      const runtimeEvidence = collectBrowserRuntimeEvidence(page);
      try {
        const response = await page.goto(`${baseURL}/platform/${application}/?section=${section}`, {
          waitUntil: 'networkidle',
          timeout: 30_000,
        });
        const body = await page.locator('body').innerText();
        checks.push({
          application,
          section,
          http_status: response?.status() ?? 0,
          forbidden_fixture_markers: forbiddenText
            .filter((pattern) => pattern.test(body))
            .map((pattern) => pattern.source),
          runtime_issue_counts: { ...runtimeEvidence.counts },
        });
      } finally {
        runtimeEvidence.stop();
        await context.close();
      }
    }
  }
} finally {
  await browser.close();
}

const passed = checks.filter(
  (check) =>
    check.http_status === 200 &&
    check.forbidden_fixture_markers.length === 0 &&
    isBrowserRuntimeEvidenceClean(check.runtime_issue_counts),
).length;
const evidence = {
  generated_at: new Date().toISOString(),
  production_url: baseURL,
  result: passed === checks.length ? 'passed' : 'failed',
  summary: { total: checks.length, passed },
  identity: {
    source: 'legacy_http_only_session',
    legacy_role: identity.legacyRole,
    browser_actor_headers_used: false,
    secret_emitted: false,
  },
  checks,
};
await writeFile(
  'tests/s04-evidence/production-mock-scan.json',
  `${JSON.stringify(evidence, null, 2)}\n`,
);
console.log(JSON.stringify(evidence.summary));
if (passed !== checks.length) process.exitCode = 1;
