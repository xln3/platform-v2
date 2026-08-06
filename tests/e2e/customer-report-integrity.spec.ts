import { expect, test, type Page } from './runtime-fixture';
import { expectAccessible } from './accessibility';
import { oversizedPagePdf, oversizedPagePdfIntegrity } from './pdf-fixtures';

const projectPubId = 'prj_customer_report_integrity';
const customerReportHtml =
  '<!doctype html><title>真实客户在线报告</title><main><h1>执行摘要</h1><p>已发布报告，包含<strong>已核验结论</strong>。</p><ul><li>证据一</li><li>证据二</li></ul><table><caption>关键指标</caption><thead><tr><th scope="col">指标</th><th scope="col">值</th></tr></thead><tbody><tr><th scope="row">提及率</th><td>68.4%</td></tr></tbody></table><p><a href="https://source.example/report">查看公开来源</a></p></main>';
const customerReportPdf = '%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF';
const customerReportHtmlSha256 = '40eb6105778ec6e9ab98b518801d34fb4aad2f0ee2a931e3694a040e392cd4bb';
const customerReportPdfSha256 = '5685e2d63d2a3b750e0850b8654c06f87fe9a1b138525deef264166e4152efbc';
const activeCustomerReportHtml =
  '<!doctype html><title>活动内容客户报告</title><main><h1>不应执行</h1><img src="https://tracker.invalid/report-pixel"><p style="background:url(https://tracker.invalid/report-css)">跟踪内容</p><script>window.__geoArtifactScriptExecuted=true</script></main>';
const activeCustomerReportHtmlSha256 =
  '2377344732d3647cc566c03aed794fd63baa1cac95ec5cf54974a71f31f7d7e7';

async function installCustomerReportExperience(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_report_integrity');
    localStorage.setItem('geo.session.actor', 'customer-report-integrity');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_customer_report_integrity',
        user_pub_id: 'usr_customer_report_integrity',
        role: 'customer',
        permissions: ['project:read', 'report:read'],
      }),
    }),
  );
  await page.route('**/api/v2/projects**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            pub_id: projectPubId,
            tenant_pub_id: 'tnt_customer_report_integrity',
            name: '客户报告完整性项目',
            state: 'active',
            created_at: '2026-07-25T00:00:00Z',
            updated_at: '2026-07-25T00:00:00Z',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );
  await page.route('**/api/v2/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v2' }),
    }),
  );
}

const reportSummary = (
  pubId: string,
  title: string,
  projectId = projectPubId,
  extension: Record<string, unknown> = {},
) => ({
  // Customer detail endpoints expose only published versions for this recipient.
  pub_id: pubId,
  project_pub_id: projectId,
  title,
  state: 'published',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T01:00:00Z',
  ...extension,
});

const reportDetail = (
  pubId: string,
  title: string,
  projectId = projectPubId,
  extension: Record<string, unknown> = {},
) => ({
  pub_id: pubId,
  project_pub_id: projectId,
  title,
  state: 'published',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T01:00:00Z',
  versions: [
    {
      pub_id: `rptv_${pubId.slice(4)}_01`,
      version_number: 1,
      window_start: '2026-07-01T00:00:00Z',
      window_end: '2026-07-21T23:59:59Z',
      filters: {},
      metric_version: 'metric-v1',
      scorer_version: 'scorer-v1',
      fact_snapshot_hash: 'a'.repeat(64),
      status: 'published',
      components: [],
      frozen_facts: [],
      artifacts: [
        {
          pub_id: `rpta_${pubId.slice(4)}_html`,
          report_version_pub_id: `rptv_${pubId.slice(4)}_01`,
          format: 'html',
          evidence_pub_id: `evd_${pubId.slice(4)}_html`,
          mime_type: 'text/html',
          byte_size: 457,
          sha256: customerReportHtmlSha256,
          created_at: '2026-07-25T01:00:00Z',
        },
        {
          pub_id: `rpta_${pubId.slice(4)}_pdf`,
          report_version_pub_id: `rptv_${pubId.slice(4)}_01`,
          format: 'pdf',
          evidence_pub_id: `evd_${pubId.slice(4)}_pdf`,
          mime_type: 'application/pdf',
          byte_size: 44,
          sha256: customerReportPdfSha256,
          created_at: '2026-07-25T01:00:00Z',
        },
      ],
      evidence_bindings: [],
      reviews: [],
      comments: [],
      events: [],
    },
  ],
  optimization_actions: [],
  ...extension,
});

test('customer discloses an oversized catalog and rejects a mismatched detail without leakage', async ({
  page,
}) => {
  await installCustomerReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            reportSummary('rpt_customer_integrity_a', '客户安全报告', projectPubId, {
              cookie: 'SESSION=customer-report-catalog-canary',
            }),
            reportSummary('rpt_customer_integrity_b', '客户安全报告二', projectPubId, {
              token: 'Bearer customer-report-over-limit-canary',
            }),
          ],
          page: { next_cursor: 'rpt_customer_integrity_b', has_more: true },
        }),
      });
    }
    if (path.endsWith('/deliveries')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        reportDetail('rpt_customer_integrity_mismatch', '不应采用的客户报告详情', projectPubId, {
          token: 'Bearer customer-report-detail-canary',
          profile_path: '/secret/profile/customer-report-detail-canary',
        }),
      ),
    });
  });

  await page.goto('/platform/customer/?section=reports');
  await expect(page.getByRole('heading', { name: '报告详情投影已拒绝' })).toBeVisible();
  await expect(page.getByText('报告目录：服务返回 2 条，浏览器安全视图展示 1 条')).toBeVisible();
  await expect(page.getByText(/预览、提问、确认和制品访问均已锁定/)).toBeVisible();
  const surfaces = await page.evaluate(() => ({
    dom: document.documentElement.outerHTML,
    url: location.href,
    localStorage: { ...localStorage },
    sessionStorage: { ...sessionStorage },
  }));
  expect(JSON.stringify(surfaces)).not.toMatch(
    /customer-report-catalog-canary|customer-report-over-limit-canary|customer-report-detail-canary|SESSION=|Bearer |\/secret\/profile/i,
  );
  await expectAccessible(page);
});

test('customer omits a legitimate cross-project-only catalog without probing its detail', async ({
  page,
}) => {
  let detailRequests = 0;
  await installCustomerReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.endsWith('/reports')) detailRequests += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          reportSummary(
            'rpt_customer_cross_project',
            '不应探测的跨项目报告',
            'prj_customer_report_other',
            { otp: '824911' },
          ),
        ],
        page: { next_cursor: null, has_more: false },
      }),
    });
  });

  await page.goto('/platform/customer/?section=reports');
  await expect(page.getByText('暂无数据', { exact: true })).toBeVisible();
  expect(detailRequests).toBe(0);
  expect(await page.locator('body').innerText()).not.toMatch(/不应探测的跨项目报告|824911/);
  await expectAccessible(page);
});

test('customer bounds a cross-project tenant scan and exposes safe continuation', async ({
  page,
}) => {
  let catalogRequests = 0;
  let detailRequests = 0;
  await installCustomerReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const url = new URL(route.request().url());
    if (!url.pathname.endsWith('/reports')) {
      detailRequests += 1;
      return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    }
    catalogRequests += 1;
    const prior = url.searchParams.get('cursor');
    const sequence = prior ? Number(prior.slice(-3)) + 1 : 1;
    const pubId = `rpt_customer_scan_${String(sequence).padStart(3, '0')}`;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [reportSummary(pubId, `其他项目租户报告 ${sequence}`, 'prj_customer_report_other')],
        page: { next_cursor: pubId, has_more: true },
      }),
    });
  });

  await page.goto('/platform/customer/?section=reports');
  await expect(page.getByRole('heading', { name: '当前扫描窗口没有项目报告' })).toBeVisible();
  await expect(
    page.getByText(/已安全扫描 10 条租户报告但尚未确认下一份当前项目报告/),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '下一页' })).toBeEnabled();
  expect(catalogRequests).toBe(10);
  expect(detailRequests).toBe(0);
  await expectAccessible(page);
});

test('customer selects the current-project report from a mixed tenant catalog', async ({
  page,
}) => {
  const detailRequests: string[] = [];
  await installCustomerReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            reportSummary(
              'rpt_customer_mixed_other',
              '不应展示的其他项目报告',
              'prj_customer_report_other',
            ),
            reportSummary('rpt_customer_mixed_current', '当前项目已发布报告'),
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/deliveries')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    }
    detailRequests.push(path);
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        reportDetail('rpt_customer_mixed_current', '当前项目已发布报告', projectPubId),
      ),
    });
  });

  await page.goto('/platform/customer/?section=reports');
  await expect(page.getByRole('heading', { name: '当前项目已发布报告' })).toBeVisible();
  await expect(page.getByText('真实 reports API')).toBeVisible();
  await expect(page.getByText('不应展示的其他项目报告')).toHaveCount(0);
  expect(detailRequests).toEqual(['/api/v2/reports/rpt_customer_mixed_current']);
  await expectAccessible(page);
});

test('customer locks receipt confirmation when delivery ownership is inconsistent', async ({
  page,
}) => {
  let confirmationWrites = 0;
  await installCustomerReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST') {
      confirmationWrites += 1;
      return route.fulfill({ status: 204, body: '' });
    }
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_customer_delivery_bound', '可交付客户报告')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/deliveries')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            pub_id: 'dlv_customer_delivery_bound',
            report_pub_id: 'rpt_customer_delivery_bound',
            recipient_pub_id: 'usr_customer_report_integrity',
            delivered_at: '2026-07-25T02:00:00Z',
            confirmed_at: null,
            confirmation_comment: null,
          },
          {
            pub_id: 'dlv_customer_delivery_other',
            report_pub_id: 'rpt_customer_delivery_bound',
            recipient_pub_id: 'usr_customer_report_other',
            delivered_at: '2026-07-25T02:00:00Z',
            confirmed_at: null,
            confirmation_comment: null,
            token: 'Bearer cross-recipient-delivery-canary',
          },
        ]),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(reportDetail('rpt_customer_delivery_bound', '可交付客户报告')),
    });
  });

  await page.goto('/platform/customer/?section=reports');
  await expect(page.getByRole('heading', { name: '可交付客户报告' })).toBeVisible();
  await expect(
    page.getByText('当前客户交付记录：服务返回 2 条，浏览器安全视图展示 1 条'),
  ).toBeVisible();
  await expect(page.getByText('交付投影不完整', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '确认收到此报告' })).toHaveCount(0);
  expect(confirmationWrites).toBe(0);
  expect(await page.locator('body').innerText()).not.toMatch(/cross-recipient-delivery-canary/);
  await expectAccessible(page);
});

test('customer locks preview, questions and delivery reads for a cross-version artifact', async ({
  page,
}) => {
  let deliveryReads = 0;
  await installCustomerReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_customer_artifact_bound', '制品异常客户报告')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/deliveries')) {
      deliveryReads += 1;
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    }
    const detail = reportDetail('rpt_customer_artifact_bound', '制品异常客户报告');
    detail.versions[0]!.artifacts[0]!.report_version_pub_id = 'rptv_customer_other';
    Object.assign(detail.versions[0]!.artifacts[0]!, {
      token: 'Bearer cross-version-artifact-canary',
      profile_path: '/secret/profile/cross-version-artifact-canary',
    });
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(detail),
    });
  });

  await page.goto('/platform/customer/?section=reports');
  await expect(page.getByRole('heading', { name: '制品异常客户报告' })).toBeVisible();
  await expect(page.getByText('制品投影不完整', { exact: true })).toBeVisible();
  await expect(
    page.getByText('当前版本制品：服务返回 2 条，浏览器安全视图展示 1 条'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '在线预览' })).toBeDisabled();
  await expect(page.getByRole('button', { name: /PDF 正在生成/ })).toBeDisabled();
  await page.getByRole('textbox', { name: '问题', exact: true }).fill('请解释当前报告结论');
  await expect(page.getByRole('button', { name: '提交问题' })).toBeDisabled();
  expect(deliveryReads).toBe(0);
  const surfaces = await page.evaluate(() => ({
    dom: document.documentElement.outerHTML,
    url: location.href,
    localStorage: { ...localStorage },
    sessionStorage: { ...sessionStorage },
  }));
  expect(JSON.stringify(surfaces)).not.toMatch(
    /cross-version-artifact-canary|Bearer |\/secret\/profile/i,
  );
  await expectAccessible(page);
});

test('customer treats a truncated version chain as incomplete and performs no downstream reads', async ({
  page,
}) => {
  let deliveryReads = 0;
  await installCustomerReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_customer_version_truncated', '版本链截断客户报告')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/deliveries')) {
      deliveryReads += 1;
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    }
    const detail = reportDetail('rpt_customer_version_truncated', '版本链截断客户报告');
    const latestTemplate = detail.versions[0]!;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...detail,
        versions: Array.from({ length: 101 }, (_, index) => {
          const versionNumber = index + 1;
          const versionPubId = `rptv_customer_version_truncated_${String(versionNumber).padStart(3, '0')}`;
          return {
            ...latestTemplate,
            pub_id: versionPubId,
            version_number: versionNumber,
            artifacts:
              versionNumber === 101
                ? latestTemplate.artifacts.map((artifact) => ({
                    ...artifact,
                    pub_id: artifact.pub_id.replace('_01_', '_101_'),
                    report_version_pub_id: versionPubId,
                  }))
                : [],
            ...(versionNumber === 1
              ? {
                  cookie: 'SESSION=customer-omitted-version-canary',
                  profile_path: '/secret/profile/customer-omitted-version-canary',
                }
              : {}),
          };
        }),
      }),
    });
  });

  await page.goto('/platform/customer/?section=reports');
  await expect(page.getByRole('heading', { name: '版本链截断客户报告' })).toBeVisible();
  await expect(
    page.getByText('报告版本：服务返回 101 条，浏览器安全视图展示 100 条'),
  ).toBeVisible();
  await expect(page.getByText('安全投影不完整', { exact: true })).toBeVisible();
  await expect(page.getByText(/部分报告版本未通过安全校验/)).toBeVisible();
  await expect(page.getByRole('button', { name: '在线预览' })).toBeDisabled();
  await expect(page.getByRole('button', { name: /PDF 正在生成/ })).toBeDisabled();
  await page.getByRole('textbox', { name: '问题', exact: true }).fill('请解释当前报告结论');
  await expect(page.getByRole('button', { name: '提交问题' })).toBeDisabled();
  expect(deliveryReads).toBe(0);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/customer-omitted-version-canary|SESSION=|\/secret\/profile/i);
  await expectAccessible(page);
});

test('customer renders neither HTML nor PDF when artifact bytes violate the projected hash', async ({
  page,
}) => {
  let artifactReads = 0;
  let downloads = 0;
  page.on('download', () => {
    downloads += 1;
  });
  await installCustomerReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (/\/artifacts\/(?:html|pdf)$/.test(path)) {
      artifactReads += 1;
      const html = path.endsWith('/html');
      return route.fulfill({
        status: 200,
        contentType: html ? 'text/html' : 'application/pdf',
        body: html
          ? customerReportHtml.replace('真实', '错误')
          : customerReportPdf.replace('1 0 obj', '2 0 obj'),
      });
    }
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_customer_hash_bound', '哈希绑定客户报告')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/deliveries')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(reportDetail('rpt_customer_hash_bound', '哈希绑定客户报告')),
    });
  });

  await page.goto('/platform/customer/?section=reports');
  await expect(page.getByRole('heading', { name: '哈希绑定客户报告' })).toBeVisible();

  await page.getByRole('button', { name: '在线预览' }).click();
  let dialog = page.getByRole('dialog', { name: '哈希绑定客户报告' });
  await expect(dialog.getByRole('button', { name: '重试此区域' })).toBeVisible();
  await expect(dialog.getByRole('article', { name: '客户报告在线预览' })).toHaveCount(0);
  await page.getByRole('button', { name: '关闭在线报告预览' }).click();

  await page.getByRole('button', { name: '打开 PDF' }).click();
  dialog = page.getByRole('dialog', { name: '哈希绑定客户报告' });
  await expect(dialog.getByRole('button', { name: '重试此区域' })).toBeVisible();
  await expect(dialog.getByText('PDF.js 已渲染客户报告第一页')).toHaveCount(0);
  await page.getByRole('button', { name: '关闭在线报告预览' }).click();

  await page.getByRole('button', { name: '下载 PDF' }).click();
  await expect(page.getByText('报告制品完整性校验失败')).toBeVisible();
  expect(downloads).toBe(0);
  expect(artifactReads).toBe(3);
  expect(await page.locator('body').innerText()).not.toMatch(/错误客户在线报告|2 0 obj/);
  await expectAccessible(page);
});

test('customer rejects an integrity-valid HTML artifact that contains active document content', async ({
  page,
}) => {
  let artifactReads = 0;
  let externalResourceReads = 0;
  await page.route('https://tracker.invalid/**', (route) => {
    externalResourceReads += 1;
    return route.abort();
  });
  await installCustomerReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/artifacts/html')) {
      artifactReads += 1;
      return route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: activeCustomerReportHtml,
      });
    }
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_customer_active_html', '活动内容拒绝报告')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/deliveries')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    }
    const detail = reportDetail('rpt_customer_active_html', '活动内容拒绝报告');
    detail.versions[0]!.artifacts = [
      {
        ...detail.versions[0]!.artifacts[0]!,
        byte_size: 270,
        sha256: activeCustomerReportHtmlSha256,
      },
    ];
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(detail),
    });
  });

  await page.goto('/platform/customer/?section=reports');
  await expect(page.getByRole('heading', { name: '活动内容拒绝报告' })).toBeVisible();
  await page.getByRole('button', { name: '在线预览' }).click();
  const dialog = page.getByRole('dialog', { name: '活动内容拒绝报告' });
  await expect(dialog.getByRole('button', { name: '重试此区域' })).toBeVisible();
  await expect(dialog.getByRole('article', { name: '客户报告在线预览' })).toHaveCount(0);
  await expect(dialog.getByText('不应执行')).toHaveCount(0);
  expect(
    await page.evaluate(() => Reflect.get(window, '__geoArtifactScriptExecuted')),
  ).toBeUndefined();
  expect(artifactReads).toBe(1);
  expect(externalResourceReads).toBe(0);
  const surfaces = await page.evaluate(() => ({
    dom: document.documentElement.outerHTML,
    url: location.href,
    localStorage: { ...localStorage },
    sessionStorage: { ...sessionStorage },
  }));
  expect(JSON.stringify(surfaces)).not.toMatch(
    /__geoArtifactScriptExecuted|不应执行|tracker\.invalid|跟踪内容/,
  );
  await expectAccessible(page);
});

test('customer rejects an integrity-valid oversized PDF page before canvas allocation', async ({
  page,
}) => {
  let artifactReads = 0;
  let externalResourceReads = 0;
  await page.route('https://tracker.invalid/**', (route) => {
    externalResourceReads += 1;
    return route.abort();
  });
  await installCustomerReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/artifacts/pdf')) {
      artifactReads += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/pdf',
        body: oversizedPagePdf,
      });
    }
    if (path.endsWith('/deliveries')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: '[]',
      });
    }
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_customer_pdf_limits', '超大页面客户报告')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    const detail = reportDetail('rpt_customer_pdf_limits', '超大页面客户报告');
    detail.versions[0]!.artifacts = [
      {
        pub_id: 'rpta_customer_pdf_limits',
        report_version_pub_id: 'rptv_customer_pdf_limits_01',
        format: 'pdf',
        evidence_pub_id: 'evd_customer_pdf_limits',
        mime_type: 'application/pdf',
        byte_size: oversizedPagePdfIntegrity.byteSize,
        sha256: oversizedPagePdfIntegrity.sha256,
        created_at: '2026-07-25T01:00:00Z',
      },
    ];
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(detail),
    });
  });

  await page.goto('/platform/customer/?section=reports');
  await expect(page.getByRole('heading', { name: '超大页面客户报告' })).toBeVisible();
  await page.getByRole('button', { name: '打开 PDF' }).click();
  const dialog = page.getByRole('dialog', { name: '超大页面客户报告' });
  await expect(dialog.getByRole('button', { name: '重试此区域' })).toBeVisible();
  await expect(dialog.getByText('PDF.js 已渲染客户报告第一页')).toHaveCount(0);
  await expect
    .poll(() =>
      dialog.locator('canvas').evaluate((canvas) => ({
        width: (canvas as HTMLCanvasElement).width,
        height: (canvas as HTMLCanvasElement).height,
      })),
    )
    .toEqual({ width: 0, height: 0 });
  expect(artifactReads).toBe(1);
  expect(externalResourceReads).toBe(0);
  await expectAccessible(page);
});

test('customer browser back discards a slower superseded report detail', async ({ page }) => {
  const pageOneId = 'rpt_customer_integrity_a';
  const pageTwoId = 'rpt_customer_integrity_z';
  await installCustomerReportExperience(page);
  await page.route('**/api/v2/reports**', async (route) => {
    const requestUrl = new URL(route.request().url());
    const path = requestUrl.pathname;
    if (path.endsWith('/artifacts/html')) {
      return route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: customerReportHtml,
      });
    }
    if (path.endsWith('/reports')) {
      const secondPage = requestUrl.searchParams.get('cursor') === pageOneId;
      const pubId = secondPage ? pageTwoId : pageOneId;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary(pubId, secondPage ? '第二页客户报告' : '第一页客户报告')],
          page: {
            next_cursor: secondPage ? null : pageOneId,
            has_more: !secondPage,
          },
        }),
      });
    }
    if (path.endsWith('/deliveries')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    }
    if (path.endsWith(`/${pageTwoId}`)) {
      await new Promise((resolve) => setTimeout(resolve, 650));
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          reportDetail(pageTwoId, '第二页延迟客户报告', projectPubId, {
            token: 'Bearer stale-customer-report-detail-canary',
          }),
        ),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(reportDetail(pageOneId, '第一页客户报告')),
    });
  });

  await page.goto('/platform/customer/?section=reports');
  await expect(page.getByRole('heading', { name: '第一页客户报告' })).toBeVisible();
  await page.getByRole('button', { name: '在线预览' }).click();
  await expect(page.getByRole('article', { name: '客户报告在线预览' })).toBeVisible();
  await page.evaluate(() => {
    history.pushState(
      null,
      '',
      '/platform/customer/?section=reports&report_page=2&report_cursor=rpt_customer_integrity_a',
    );
    dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(page).toHaveURL(/report_page=2/);
  await expect(page.getByText('数据正在安全获取，请稍候。')).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('article', { name: '客户报告在线预览' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '第一页客户报告' })).toHaveCount(0);
  await page.goBack();
  await expect(page).not.toHaveURL(/report_page=2/);
  await expect(page.getByRole('heading', { name: '第一页客户报告' })).toBeVisible();
  await page.waitForTimeout(750);
  await expect(page.getByText('第二页延迟客户报告')).toHaveCount(0);
  expect(await page.locator('body').innerText()).not.toMatch(/stale-customer-report-detail-canary/);
});
