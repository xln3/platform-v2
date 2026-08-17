import { expect, test } from './runtime-fixture';
import { captureSafeScreenshot, expectSafePageScreenshot } from './screenshot-safety';
import { prepareVisualPage } from './visual-regression';
import { rm } from 'node:fs/promises';

const workspaces = [
  { section: 'home', snapshot: 'customer-home.png', ready: '云岫智能 · AI 认知资产总览' },
  { section: 'profile', snapshot: 'customer-profile.png', ready: '甲方资料' },
  { section: 'intake', snapshot: 'customer-intake-form.png', ready: '客户信息收集表' },
  { section: 'assets', snapshot: 'customer-brand-assets.png', ready: '品牌、产品与竞品' },
  {
    section: 'questions',
    snapshot: 'customer-questions-goals.png',
    ready: '问题、目标与配置申请',
  },
  {
    section: 'monitoring',
    snapshot: 'customer-monitoring-dimensions.png',
    ready: '云岫智能 · 品牌可见度与模型表现',
  },
  {
    section: 'answers',
    snapshot: 'customer-real-ai-answers.png',
    ready: '云岫智能 · 真实 AI 回答与模型语境',
  },
  {
    section: 'competition',
    snapshot: 'customer-competition.png',
    ready: '云岫智能 · 竞品对标与心智份额',
  },
  {
    section: 'sources',
    snapshot: 'customer-sources.png',
    ready: '云岫智能 · 信源权威与内容准备度',
  },
  {
    section: 'reputation',
    snapshot: 'customer-reputation.png',
    ready: '云岫智能 · AI 口碑与品牌风险',
  },
  {
    section: 'opportunities',
    snapshot: 'customer-opportunities.png',
    ready: '云岫智能 · 问题机会与增长缺口',
  },
  {
    section: 'evidence',
    snapshot: 'customer-answers-evidence.png',
    ready: '企业知识库如何选择？',
  },
  {
    section: 'reports',
    snapshot: 'customer-reports.png',
    ready: '2026 Q3 GEO 监测与优化建议',
  },
  { section: 'members', snapshot: 'customer-members.png', ready: '项目成员' },
  { section: 'accounts', snapshot: 'customer-account-safety.png', ready: '平台账号与授权' },
] as const;

for (const workspace of workspaces) {
  test(`customer ${workspace.section} visual baseline has no page overflow`, async ({ page }) => {
    await prepareVisualPage(page, `/platform/customer/?section=${workspace.section}`);
    await page.getByRole('heading', { name: workspace.ready, exact: true }).first().waitFor();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
      .toBe(true);
    await expectSafePageScreenshot(page, workspace.snapshot, {
      fullPage: true,
      animations: 'disabled',
      maxDiffPixelRatio: 0.005,
    });
  });
}

test('customer secure pairing QR and native challenge have responsive visual baselines', async ({
  page,
}) => {
  await prepareVisualPage(page, '/platform/customer/?section=accounts');
  await page.getByRole('button', { name: '创建一次性配对' }).click();
  await page.getByRole('button', { name: '确认并进入配对演示' }).click();
  await page.getByRole('img', { name: /一次性安全配对二维码占位/ }).waitFor();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    .toBe(true);
  await expectSafePageScreenshot(page, 'customer-account-pairing-qr.png', {
    fullPage: true,
    animations: 'disabled',
    maxDiffPixelRatio: 0.005,
  });

  await page.getByRole('button', { name: '终端已连接' }).click();
  await page.getByRole('heading', { name: '请在豆包原生页面完成验证' }).waitFor();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    .toBe(true);
  await expectSafePageScreenshot(page, 'customer-account-native-challenge.png', {
    fullPage: true,
    animations: 'disabled',
    maxDiffPixelRatio: 0.005,
  });
});

test('unmarked machine-readable QR pixels are rejected before visual evidence', async ({
  page,
}, testInfo) => {
  await page.route('**/qr-safety-fixture', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: `
        <!doctype html>
        <html lang="zh-CN">
          <body>
            <main>
              <h1>受控终端交接</h1>
              <img
                alt="pairing QR code"
                src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='32' height='32'%3E%3Cpath d='M0 0h32v32H0z'/%3E%3C/svg%3E"
              />
            </main>
          </body>
        </html>
      `,
    }),
  );
  await page.goto('/qr-safety-fixture');

  const temporaryScreenshot = `tests/e2e-results/machine-readable-rejection-${testInfo.project.name}.png`;
  try {
    await expect(
      captureSafeScreenshot(page, {
        path: temporaryScreenshot,
        fullPage: true,
        animations: 'disabled',
      }),
    ).rejects.toThrow(/machine-readable-visual/u);

    await page.setContent(`
      <!doctype html>
      <html lang="zh-CN">
        <head>
          <style>
            .unsafe-pairing span::before {
              content: "";
              display: block;
              width: 32px;
              height: 32px;
              background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='32' height='32'%3E%3Cpath d='M0 0h32v32H0z'/%3E%3C/svg%3E");
            }
          </style>
        </head>
        <body>
          <div
            class="unsafe-pairing"
            role="img"
            aria-label="pairing QR code"
            data-visual-evidence="payload-free"
          ><span aria-hidden="true"></span></div>
        </body>
      </html>
    `);
    await expect(
      captureSafeScreenshot(page, {
        path: temporaryScreenshot,
        fullPage: true,
        animations: 'disabled',
      }),
    ).rejects.toThrow(/machine-readable-visual/u);
  } finally {
    await rm(temporaryScreenshot, { force: true });
  }
});

test('hidden browser surface secrets are rejected before visual evidence', async ({
  page,
}, testInfo) => {
  await page.route('**/browser-surface-safety-fixture', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: `
        <!doctype html>
        <html lang="zh-CN">
          <body>
            <main><h1>安全投影</h1><input type="hidden" value="OTP 824911" /></main>
          </body>
        </html>
      `,
    }),
  );
  await page.goto('/browser-surface-safety-fixture');

  const temporaryScreenshot = `tests/e2e-results/browser-surface-rejection-${testInfo.project.name}.png`;
  const rejectCapture = (issue: RegExp) =>
    expect(
      captureSafeScreenshot(page, {
        path: temporaryScreenshot,
        fullPage: true,
        animations: 'disabled',
      }),
    ).rejects.toThrow(issue);
  try {
    await rejectCapture(/controls/u);

    await page.setContent('<!doctype html><html><body><main>安全投影</main></body></html>');
    await page.evaluate(() =>
      history.replaceState({ profile_path: '/secret/browser/profile/history-canary' }, '', '/safe'),
    );
    await rejectCapture(/history-state/u);

    await page.evaluate(() => {
      history.replaceState(null, '', '/safe');
      document.cookie = 'pairing_token=browser-cookie-canary; SameSite=Lax';
    });
    await rejectCapture(/cookie/u);

    await page.evaluate(() => {
      document.cookie = 'pairing_token=; Max-Age=0; SameSite=Lax';
      history.replaceState(null, '', '/?access_token=url-canary');
    });
    await rejectCapture(/url/u);

    await page.evaluate(() => {
      history.replaceState(null, '', '/safe');
      document.querySelector('main')?.setAttribute('data-access_token', 'opaque-canary');
    });
    await rejectCapture(/attribute-names/u);
  } finally {
    await page.evaluate(() => {
      document.cookie = 'pairing_token=; Max-Age=0; SameSite=Lax';
      history.replaceState(null, '', '/safe');
    });
    await rm(temporaryScreenshot, { force: true });
  }
});
