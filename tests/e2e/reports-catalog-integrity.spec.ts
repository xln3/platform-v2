import { expect, test, type Page } from './runtime-fixture';
import { expectAccessible } from './accessibility';
import { oversizedPagePdf, oversizedPagePdfIntegrity } from './pdf-fixtures';
import { installSyntheticHttpResponses, syntheticHttpResponseCount } from './synthetic-http';

const projectPubId = 'prj_reports_catalog_integrity';

async function installReportExperience(page: Page, role: 'reviewer' | 'analyst' = 'reviewer') {
  await page.addInitScript((selectedRole) => {
    localStorage.setItem('geo.session.tenant', 'tnt_reports_catalog_integrity');
    localStorage.setItem('geo.session.actor', `${selectedRole}-reports-catalog-integrity`);
    localStorage.setItem('geo.session.role', selectedRole);
  }, role);
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_reports_catalog_integrity',
        user_pub_id: `usr_${role}_reports_catalog_integrity`,
        role,
        permissions:
          role === 'analyst'
            ? ['project:read', 'report:read', 'report:write']
            : ['project:read', 'report:review'],
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
            tenant_pub_id: 'tnt_reports_catalog_integrity',
            name: '报告目录完整性项目',
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
  pub_id: pubId,
  project_pub_id: projectId,
  title,
  state: 'review',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T01:00:00Z',
  ...extension,
});

const reportDetail = (
  pubId: string,
  title: string,
  currentBody: string,
  projectId = projectPubId,
  extension: Record<string, unknown> = {},
) => {
  const versionBase = pubId.replace('rpt_', 'rptv_');
  const componentBase = pubId.replace('rpt_', 'rptc_');
  return {
    pub_id: pubId,
    project_pub_id: projectId,
    title,
    state: 'review',
    created_at: '2026-07-25T00:00:00Z',
    updated_at: '2026-07-25T01:00:00Z',
    versions: [
      {
        pub_id: `${versionBase}_01`,
        version_number: 1,
        window_start: '2026-07-01T00:00:00Z',
        window_end: '2026-07-21T23:59:59Z',
        filters: {},
        metric_version: 'metric-v1',
        scorer_version: 'scorer-v1',
        fact_snapshot_hash: 'a'.repeat(64),
        status: 'review',
        components: [
          {
            pub_id: `${componentBase}_01_00`,
            report_version_pub_id: `${versionBase}_01`,
            component_type: 'section',
            ordinal: 0,
            source: 'human',
            payload: { title: '执行摘要', body: '上一版报告正文。' },
            created_at: '2026-07-25T00:10:00Z',
          },
        ],
        frozen_facts: [],
        artifacts: [],
        evidence_bindings: [],
        reviews: [],
        comments: [],
        events: [],
      },
      {
        pub_id: `${versionBase}_02`,
        version_number: 2,
        window_start: '2026-07-01T00:00:00Z',
        window_end: '2026-07-21T23:59:59Z',
        filters: {},
        metric_version: 'metric-v1',
        scorer_version: 'scorer-v1',
        fact_snapshot_hash: 'b'.repeat(64),
        status: 'review',
        components: [
          {
            pub_id: `${componentBase}_02_00`,
            report_version_pub_id: `${versionBase}_02`,
            component_type: 'section',
            ordinal: 0,
            source: 'human',
            payload: { title: '执行摘要', body: currentBody },
            created_at: '2026-07-25T00:20:00Z',
          },
        ],
        frozen_facts: [],
        artifacts: [],
        evidence_bindings: [],
        reviews: [],
        comments: [],
        events: [],
      },
    ],
    optimization_actions: [],
    ...extension,
  };
};

test('an unsafe oversized catalog fails before any mismatched detail can be adopted', async ({
  page,
}) => {
  await installReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            reportSummary('rpt_catalog_safe', '目录安全报告', projectPubId, {
              cookie: 'SESSION=report-catalog-root-canary',
            }),
            reportSummary('rpt_catalog_over_limit', 'Bearer report-catalog-limit-canary'),
          ],
          page: { next_cursor: 'rpt_catalog_safe', has_more: true },
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        reportDetail(
          'rpt_catalog_mismatched',
          '不应采用的详情',
          '不应采用的跨报告正文。',
          projectPubId,
          {
            token: 'Bearer report-detail-mismatch-canary',
            profile_path: '/secret/profile/report-detail-mismatch-canary',
          },
        ),
      ),
    });
  });

  await page.goto('/platform/reports/');
  await expect(
    page.getByText('当前检索窗口内的项目报告：服务返回 2 条，浏览器安全视图展示 1 条'),
  ).toBeVisible();
  await expect(page.getByText('详情投影已拒绝', { exact: true })).toHaveCount(0);
  await expect(page.getByText('加载失败')).toBeVisible();
  const surfaces = await page.evaluate(() => ({
    dom: document.documentElement.outerHTML,
    url: location.href,
    localStorage: { ...localStorage },
    sessionStorage: { ...sessionStorage },
  }));
  expect(JSON.stringify(surfaces)).not.toMatch(
    /report-catalog-root-canary|report-catalog-limit-canary|report-detail-mismatch-canary|SESSION=|Bearer |\/secret\/profile/i,
  );
  await expectAccessible(page);
});

test('an embedded full phone in report detail fails closed before cache or rendering', async ({
  page,
}) => {
  await installReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_embedded_phone', '安全目录标题')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        reportDetail(
          'rpt_embedded_phone',
          'report13800138000detail-phone-canary',
          '安全正文不应掩盖根详情的 DLP 失败。',
        ),
      ),
    });
  });

  await page.goto('/platform/reports/');
  await expect(page.getByText('详情投影已拒绝', { exact: true })).toBeVisible();
  await expect(page.getByText('加载失败')).toBeVisible();
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/13800138000|detail-phone-canary/i);
  await expectAccessible(page);
});

test('a bare six-digit OTP in report detail fails closed before query cache', async ({ page }) => {
  await installReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_bare_otp', '安全目录标题')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        reportDetail(
          'rpt_bare_otp',
          '请在原生页面输入 824911 完成验证',
          '安全正文不应掩盖根详情的 OTP DLP 失败。',
        ),
      ),
    });
  });

  await page.goto('/platform/reports/');
  await expect(page.getByText('详情投影已拒绝', { exact: true })).toBeVisible();
  await expect(page.getByText('加载失败')).toBeVisible();
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/824911|bare-otp-canary/i);
  await expectAccessible(page);
});

test('numeric OTP and phone fields are rejected before structured report state', async ({
  page,
}) => {
  await installReportExperience(page, 'analyst');
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_numeric_secret', '数值 DLP 报告')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    const detail = reportDetail(
      'rpt_numeric_secret',
      '数值 DLP 报告',
      '安全正文不应掩盖结构化数值秘密。',
    );
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...detail,
        versions: [
          detail.versions[0],
          {
            ...detail.versions[1],
            components: detail.versions[1]!.components.map((component) => ({
              ...component,
              payload: {
                ...component.payload,
                challenge: 824911,
                contact: 13800138000,
              },
            })),
          },
        ],
      }),
    });
  });

  await page.goto('/platform/reports/');
  await page.getByRole('button', { name: '章节编辑' }).click();
  await expect(page.getByText('安全投影不完整')).toBeVisible();
  await expect(page.getByText(/版本章节含未通过安全校验的数据/)).toBeVisible();
  await expect(
    page.getByText('当前章节投影不完整，禁止用浏览器中的部分章节覆盖不可变报告版本。'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '保存不可变报告版本' })).toHaveCount(0);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/824911|13800138000/);
  await expectAccessible(page);
});

test('a hash-mismatched PDF is neither downloaded nor rendered', async ({ page }) => {
  let artifactReads = 0;
  let downloads = 0;
  page.on('download', () => {
    downloads += 1;
  });
  await installReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/artifacts/pdf')) {
      artifactReads += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/pdf',
        body: '%PDF-1.4\n2 0 obj<<>>endobj\ntrailer<<>>\n%%EOF',
      });
    }
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_pdf_mismatch', '制品完整性报告')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    const detail = reportDetail(
      'rpt_pdf_mismatch',
      '制品完整性报告',
      '只有与冻结元数据一致的制品才能离开浏览器边界。',
    );
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...detail,
        versions: [
          detail.versions[0],
          {
            ...detail.versions[1],
            artifacts: [
              {
                pub_id: 'rpta_pdf_mismatch',
                report_version_pub_id: 'rptv_pdf_mismatch_02',
                format: 'pdf',
                evidence_pub_id: 'evd_pdf_mismatch',
                mime_type: 'application/pdf',
                byte_size: 44,
                sha256: '5685e2d63d2a3b750e0850b8654c06f87fe9a1b138525deef264166e4152efbc',
                created_at: '2026-07-25T00:25:00Z',
              },
            ],
          },
        ],
      }),
    });
  });

  await page.goto('/platform/reports/');
  await page.getByRole('button', { name: '证据编排' }).click();
  await page.getByRole('button', { name: '校验后下载' }).click();
  await expect(page.getByText('制品完整性校验失败')).toBeVisible();
  expect(downloads).toBe(0);
  await expect.poll(() => artifactReads).toBe(1);

  await page.getByRole('button', { name: 'PDF 预览' }).click();
  await expect(page.getByText('PDF 页面渲染失败')).toBeAttached();
  await expect(page.getByText('PDF.js 已渲染第 1 页')).toHaveCount(0);
  await expect.poll(() => artifactReads).toBe(2);
  await expectAccessible(page);
});

test('an integrity-valid oversized PDF page is rejected before canvas allocation', async ({
  page,
}) => {
  let artifactReads = 0;
  let externalResourceReads = 0;
  await page.route('https://tracker.invalid/**', (route) => {
    externalResourceReads += 1;
    return route.abort();
  });
  await installReportExperience(page);
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
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_pdf_limits', '超大页面冻结报告')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    const detail = reportDetail(
      'rpt_pdf_limits',
      '超大页面冻结报告',
      '即使制品哈希正确，浏览器也不会分配无界画布。',
    );
    detail.versions[1]!.artifacts = [
      {
        pub_id: 'rpta_pdf_limits',
        report_version_pub_id: 'rptv_pdf_limits_02',
        format: 'pdf',
        evidence_pub_id: 'evd_pdf_limits',
        mime_type: 'application/pdf',
        byte_size: oversizedPagePdfIntegrity.byteSize,
        sha256: oversizedPagePdfIntegrity.sha256,
        created_at: '2026-07-25T00:25:00Z',
      },
    ];
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(detail),
    });
  });

  await page.goto('/platform/reports/');
  await expect(page.getByRole('heading', { name: '超大页面冻结报告' })).toBeVisible();
  await page.getByRole('button', { name: 'PDF 预览' }).click();
  await expect(page.getByText('PDF 页面渲染失败')).toBeAttached();
  await expect(page.getByText('PDF.js 已渲染第 1 页')).toHaveCount(0);
  await expect
    .poll(() =>
      page.locator('.pdf-canvas-wrap canvas').evaluate((canvas) => ({
        width: (canvas as HTMLCanvasElement).width,
        height: (canvas as HTMLCanvasElement).height,
      })),
    )
    .toEqual({ width: 0, height: 0 });
  expect(artifactReads).toBe(1);
  expect(externalResourceReads).toBe(0);
  await expectAccessible(page);
});

test('a cross-version artifact locks preview and all release writes', async ({ page }) => {
  const writes: string[] = [];
  let artifactReads = 0;
  await installReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      writes.push(`${request.method()} ${path}`);
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ pub_id: 'unexpected_write' }),
      });
    }
    if (path.endsWith('/artifacts/pdf')) {
      artifactReads += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/pdf',
        body: '%PDF-1.4\n%%EOF',
      });
    }
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            reportSummary('rpt_cross_artifact', '跨版本制品报告', projectPubId, {
              state: 'approved',
            }),
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    const detail = reportDetail(
      'rpt_cross_artifact',
      '跨版本制品报告',
      '当前版本正文。',
      projectPubId,
      { state: 'approved' },
    );
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...detail,
        versions: [
          detail.versions[0],
          {
            ...detail.versions[1],
            status: 'approved',
            frozen_facts: [
              {
                pub_id: 'rptf_cross_version_fact',
                report_version_pub_id: 'rptv_other_version',
                ordinal: 0,
                payload: {
                  metric: 'cross_version',
                  value: 1,
                  token: 'Bearer report-cross-fact-canary',
                },
                payload_hash: 'c'.repeat(64),
                created_at: '2026-07-25T00:29:00Z',
              },
            ],
            artifacts: [
              {
                pub_id: 'rpta_cross_artifact_pdf',
                report_version_pub_id: 'rptv_other_version',
                format: 'pdf',
                evidence_pub_id: 'evd_cross_artifact_pdf',
                mime_type: 'application/pdf',
                byte_size: 48,
                sha256: 'a'.repeat(64),
                created_at: '2026-07-25T00:30:00Z',
                token: 'Bearer report-cross-artifact-canary',
              },
            ],
            evidence_bindings: [
              {
                pub_id: 'rptev_cross_version_binding',
                report_version_pub_id: 'rptv_other_version',
                evidence_pub_id: 'evd_cross_version_binding',
                purpose: 'frozen_fact_or_component',
                kind: 'answer_screenshot',
                access_class: 'customer_private',
                mime_type: 'image/png',
                byte_size: 128,
                sha256: 'b'.repeat(64),
                anchor_count: 1,
                capture_time: '2026-07-25T00:20:00Z',
                created_at: '2026-07-25T00:30:00Z',
              },
            ],
            comments: [
              {
                pub_id: 'cmt_cross_version_comment',
                report_version_pub_id: 'rptv_other_version',
                parent_pub_id: null,
                author_pub_id: 'usr_reports_catalog_integrity',
                body: '不应显示的跨版本评论',
                resolved_at: null,
                created_at: '2026-07-25T00:31:00Z',
              },
            ],
            reviews: [
              {
                pub_id: 'rvw_cross_version_review',
                report_version_pub_id: 'rptv_other_version',
                reviewer_pub_id: 'usr_reports_catalog_integrity',
                decision: 'approved',
                rationale: 'Bearer report-cross-review-canary',
                created_at: '2026-07-25T00:32:00Z',
              },
            ],
            events: [
              {
                pub_id: 'evt_cross_version_event',
                report_version_pub_id: 'rptv_other_version',
                event_type: 'published',
                actor_pub_id: 'usr_reports_catalog_integrity',
                data: { token: 'Bearer report-cross-event-canary' },
                created_at: '2026-07-25T00:33:00Z',
              },
            ],
          },
        ],
      }),
    });
  });

  await page.goto('/platform/reports/');
  await expect(page.getByRole('heading', { name: '跨版本制品报告' })).toBeVisible();
  await page.getByRole('button', { name: 'PDF 预览' }).click();
  await expect(page.getByText('安全投影不完整')).toBeVisible();
  await expect(page.getByText('样本不足', { exact: true })).toBeVisible();
  expect(artifactReads).toBe(0);

  await page.getByRole('button', { name: /审核发布/ }).click();
  await expect(
    page.getByText(
      /冻结事实、版本产物、证据绑定、审核评论、审核决定、报告事件含未通过安全校验的数据/,
    ),
  ).toBeVisible();
  await expect(page.getByText('不应显示的跨版本评论')).toHaveCount(0);
  await expect(page.getByText('evd_cross_version_binding')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '发布 v1.0' })).toBeDisabled();
  await page.getByLabel('新增评论').fill('不应提交的评论');
  await expect(page.getByRole('button', { name: '添加评论' })).toBeDisabled();
  expect(writes).toEqual([]);

  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/report-cross-(?:artifact|fact|review|event)-canary|Bearer /i);
  await expectAccessible(page);
});

test('a dangling section evidence id is removed and locks report release writes', async ({
  page,
}) => {
  const writes: string[] = [];
  await installReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      writes.push(`${request.method()} ${path}`);
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ pub_id: 'unexpected_write' }),
      });
    }
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            reportSummary('rpt_dangling_evidence', '章节证据闭包报告', projectPubId, {
              state: 'approved',
            }),
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    const detail = reportDetail(
      'rpt_dangling_evidence',
      '章节证据闭包报告',
      '当前版本正文。',
      projectPubId,
      { state: 'approved' },
    );
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...detail,
        versions: [
          detail.versions[0],
          {
            ...detail.versions[1],
            status: 'approved',
            components: detail.versions[1]!.components.map((component) => ({
              ...component,
              payload: {
                ...component.payload,
                evidence_pub_ids: [
                  'evd_report_section_linked',
                  'evd_report_section_dangling_canary',
                ],
              },
              token: 'Bearer report-section-link-canary',
            })),
            frozen_facts: [
              {
                pub_id: 'rptf_report_section_linked',
                report_version_pub_id: 'rptv_dangling_evidence_02',
                ordinal: 0,
                payload: { metric: 'evidence_closure', value: 1 },
                payload_hash: 'c'.repeat(64),
                created_at: '2026-07-25T00:21:00Z',
              },
            ],
            evidence_bindings: [
              {
                pub_id: 'rptev_report_section_linked',
                report_version_pub_id: 'rptv_dangling_evidence_02',
                evidence_pub_id: 'evd_report_section_linked',
                purpose: 'frozen_fact_or_component',
                kind: 'answer_screenshot',
                access_class: 'customer_private',
                mime_type: 'image/png',
                byte_size: 128,
                sha256: 'b'.repeat(64),
                anchor_count: 1,
                capture_time: '2026-07-25T00:20:00Z',
                created_at: '2026-07-25T00:22:00Z',
              },
            ],
          },
        ],
      }),
    });
  });

  await page.goto('/platform/reports/');
  await expect(page.getByRole('heading', { name: '章节证据闭包报告' })).toBeVisible();
  await page.getByRole('button', { name: /审核发布/ }).click();
  await expect(page.getByText(/章节证据标识含未通过安全校验的数据/)).toBeVisible();
  await expect(page.getByText('evd_report_section_dangling_canary')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '发布 v1.0' })).toBeDisabled();
  await page.getByLabel('新增评论').fill('不应提交未闭包证据报告');
  await expect(page.getByRole('button', { name: '添加评论' })).toBeDisabled();
  expect(writes).toEqual([]);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /evd_report_section_dangling_canary|report-section-link-canary|Bearer /i,
  );
  await expectAccessible(page);
});

test('a cross-project-only catalog row is omitted before any report detail probe', async ({
  page,
}) => {
  let detailRequests = 0;
  await installReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.endsWith('/reports')) detailRequests += 1;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          reportSummary('rpt_cross_project', '不应公开的跨项目报告', 'prj_reports_catalog_other', {
            token: 'Bearer report-cross-project-canary',
          }),
        ],
        page: { next_cursor: null, has_more: false },
      }),
    });
  });

  await page.goto('/platform/reports/');
  await expect(page.getByText('暂无数据')).toBeVisible();
  await expect(page.getByText(/报告目录包含跨项目、重复标识/)).toHaveCount(0);
  await expect(page.getByText('不应公开的跨项目报告')).toHaveCount(0);
  expect(detailRequests).toBe(0);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/report-cross-project-canary|Bearer /i);
  await expectAccessible(page);
});

test('tenant-scoped catalog scanning selects the current-project report without probing another project', async ({
  page,
}) => {
  const detailRequests: string[] = [];
  await installReportExperience(page);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            reportSummary(
              'rpt_catalog_other_first',
              '其他项目报告不可进入当前项目状态',
              'prj_reports_catalog_other',
            ),
            reportSummary('rpt_catalog_project_safe', '当前项目安全报告'),
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    detailRequests.push(path);
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        reportDetail(
          'rpt_catalog_project_safe',
          '当前项目安全报告',
          '只采用与当前项目严格绑定的报告正文。',
        ),
      ),
    });
  });

  await page.goto('/platform/reports/');
  await expect(page.getByRole('heading', { name: '当前项目安全报告' })).toBeVisible();
  await expect(page.getByText('其他项目报告不可进入当前项目状态')).toHaveCount(0);
  await page.getByRole('button', { name: '章节编辑' }).click();
  await expect(page.getByLabel('真实章节正文')).toHaveValue('只采用与当前项目严格绑定的报告正文。');
  expect(detailRequests).toEqual(['/api/v2/reports/rpt_catalog_project_safe']);
  await expectAccessible(page);
});

test('a forbidden report detail remains non-inferential instead of degrading to a generic failure', async ({
  page,
}) => {
  await installReportExperience(page);
  await installSyntheticHttpResponses(page, [
    {
      id: 'report-detail-forbidden',
      path: '/api/v2/reports/',
      match: 'prefix',
      status: 403,
      body: { detail: { code: 'report_forbidden' } },
    },
  ]);
  await page.route('**/api/v2/reports**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/reports')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [reportSummary('rpt_catalog_forbidden_detail', '目录中可见的报告摘要')],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    return route.abort();
  });

  await page.goto('/platform/reports/?section=review');
  await expect.poll(() => syntheticHttpResponseCount(page, 'report-detail-forbidden')).toBe(1);
  await expect(page.getByText('无权查看', { exact: true })).toBeVisible();
  await expect(page.getByText('当前角色没有此资源权限，也不会披露资源是否存在。')).toBeVisible();
  await expect(page.getByText('加载失败')).toHaveCount(0);
  await expect(page.getByText('目录中可见的报告摘要')).toHaveCount(0);
  await expectAccessible(page);
});

test('browser history rekeys review and action state to the exact report version', async ({
  page,
}) => {
  const patches: string[] = [];
  await installReportExperience(page, 'analyst');
  await installSyntheticHttpResponses(page, [
    {
      id: 'report-action-patch',
      path: '/api/v2/reports/',
      match: 'prefix',
      method: 'PATCH',
      status: 204,
      passthrough: true,
    },
  ]);
  const detailFor = (pubId: 'rpt_state_01' | 'rpt_state_02') => {
    const label = pubId === 'rpt_state_01' ? '第一份报告' : '第二份报告';
    const detail = reportDetail(pubId, label, `${label}正文。`);
    const version = detail.versions[1]!;
    return {
      ...detail,
      versions: [
        detail.versions[0],
        {
          ...version,
          comments: [
            {
              pub_id: pubId === 'rpt_state_01' ? 'cmt_state_01' : 'cmt_state_02',
              report_version_pub_id: version.pub_id,
              parent_pub_id: null,
              author_pub_id: 'usr_reports_state_reviewer',
              body: `${label}待处理评论`,
              resolved_at: null,
              created_at: '2026-07-25T00:30:00Z',
            },
          ],
        },
      ],
      optimization_actions: [
        {
          pub_id: pubId === 'rpt_state_01' ? 'act_state_01' : 'act_state_02',
          description: `${label}优化行动`,
          owner_pub_id: null,
          state: 'proposed',
          baseline: { version: 2 },
          outcome: null,
          created_at: '2026-07-25T00:40:00Z',
          updated_at: '2026-07-25T00:40:00Z',
          effect_retests: [],
        },
      ],
    };
  };

  await page.route('**/api/v2/reports**', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (request.method() === 'PATCH') {
      patches.push(path);
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: '{}',
      });
    }
    if (path.endsWith('/reports')) {
      const second = url.searchParams.get('cursor') === 'rpt_state_01';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            reportSummary(
              second ? 'rpt_state_02' : 'rpt_state_01',
              second ? '第二份报告' : '第一份报告',
            ),
          ],
          page: {
            next_cursor: second ? null : 'rpt_state_01',
            has_more: !second,
          },
        }),
      });
    }
    const second = path.endsWith('/rpt_state_02');
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(detailFor(second ? 'rpt_state_02' : 'rpt_state_01')),
    });
  });

  await page.goto('/platform/reports/?section=review');
  await expect(page.getByText('第一份报告待处理评论')).toBeVisible();
  await page.evaluate(() => {
    history.pushState(
      null,
      '',
      '/platform/reports/?section=review&report_page=2&report_cursor=rpt_state_01',
    );
    dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(page.getByText('第二份报告待处理评论')).toBeVisible();
  await expect(page.getByText('第一份报告待处理评论')).toHaveCount(0);

  await page.getByRole('button', { name: '效果复盘' }).click();
  await expect(page.getByText('第二份报告优化行动')).toBeVisible();
  await page.evaluate(() => {
    history.pushState(null, '', '/platform/reports/?section=outcomes');
    dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(page.getByText('第一份报告优化行动')).toBeVisible();
  await expect(page.getByText('第二份报告优化行动')).toHaveCount(0);
  await page.getByRole('button', { name: '开始执行' }).click();
  await expect.poll(() => patches).toEqual(['/api/v2/reports/rpt_state_01/actions/act_state_01']);
  expect(await syntheticHttpResponseCount(page, 'report-action-patch')).toBe(1);
  await expectAccessible(page);
});

test('browser back discards a slower superseded report detail response', async ({ page }) => {
  let secondPageDetailRequests = 0;
  await installReportExperience(page);
  await page.route('**/api/v2/reports**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const secondPage = url.searchParams.get('cursor') === 'rpt_catalog_page_01';
    if (path.endsWith('/reports')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            reportSummary(
              secondPage ? 'rpt_catalog_page_02' : 'rpt_catalog_page_01',
              secondPage ? '第二页报告' : '第一页报告',
            ),
          ],
          page: {
            next_cursor: secondPage ? null : 'rpt_catalog_page_01',
            has_more: !secondPage,
          },
        }),
      });
      return;
    }
    if (path.endsWith('/rpt_catalog_page_02')) {
      secondPageDetailRequests += 1;
      await new Promise((resolve) => setTimeout(resolve, 700));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          reportDetail(
            'rpt_catalog_page_02',
            '第二页报告',
            '不应覆盖的第二页报告正文。',
            projectPubId,
            { token: 'Bearer stale-report-detail-canary' },
          ),
        ),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        reportDetail('rpt_catalog_page_01', '第一页报告', '当前第一页报告正文。'),
      ),
    });
  });

  await page.goto('/platform/reports/');
  await expect(page.getByRole('heading', { name: '第一页报告' })).toBeVisible();
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/report_page=2/);
  await expect(page.getByText('数据正在安全获取，请稍候。')).toBeVisible();
  await expect(page.getByRole('heading', { name: '第一页报告' })).toHaveCount(0);
  await expect.poll(() => secondPageDetailRequests).toBe(1);
  await page.goBack();
  await expect(page).not.toHaveURL(/report_(?:page|cursor)=/);
  await expect(page.getByRole('heading', { name: '第一页报告' })).toBeVisible();
  await page.waitForTimeout(850);
  await page.getByRole('button', { name: '章节编辑' }).click();
  await expect(page.getByLabel('真实章节正文')).toHaveValue('当前第一页报告正文。');
  await expect(page.getByLabel('真实章节正文')).not.toHaveValue('不应覆盖的第二页报告正文。');
  const surfaces = await page.evaluate(() => ({
    dom: document.documentElement.outerHTML,
    url: location.href,
    localStorage: { ...localStorage },
    sessionStorage: { ...sessionStorage },
  }));
  expect(JSON.stringify(surfaces)).not.toMatch(/stale-report-detail-canary|Bearer /i);
  await expectAccessible(page);
});
