import { expect, test } from './runtime-fixture';
import { expectSafePageScreenshot } from './screenshot-safety';
import { installSyntheticHttpResponses, syntheticHttpResponseCount } from './synthetic-http';

test('reviewer and analyst live writes stay single under synchronous duplicate submissions', async ({
  page,
}) => {
  const writes: { method: string; url: string; body: unknown }[] = [];
  const revisionIdempotencyKeys: string[] = [];
  let identityRole: 'reviewer' | 'analyst' = 'reviewer';
  let artifactRequests = 0;
  let validEffectRetestReceipt = true;
  let commentAccepted = false;
  let commentVisible = false;
  let commentReconciliationReads = 0;
  let reviewVisible = false;
  let publishedVisible = false;
  let deliveryVisible = false;
  let deliveryReads = 0;
  let actionAccepted = false;
  let actionVisible = false;
  let actionReconciliationReads = 0;
  let effectRetestAccepted = false;
  let retestVisible = false;
  let revisionAccepted = false;
  let revisionVisible = false;
  let revisionReconciliationReads = 0;
  await page.addInitScript(() => {
    const role = localStorage.getItem('geo.e2e.report-role') === 'analyst' ? 'analyst' : 'reviewer';
    localStorage.setItem('geo.session.tenant', 'tnt_reports_live');
    localStorage.setItem('geo.session.actor', `${role}-reports-live`);
    localStorage.setItem('geo.session.role', role);
  });
  await installSyntheticHttpResponses(page, [
    {
      id: 'report-publish-no-content',
      path: '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/publish',
      method: 'POST',
      status: 204,
      passthrough: true,
    },
    {
      id: 'report-patch-no-content',
      path: '/api/v2/reports/',
      match: 'prefix',
      method: 'PATCH',
      status: 204,
      passthrough: true,
    },
  ]);
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_reports_live',
        user_pub_id: 'usr_reports_live',
        role: identityRole,
        permissions:
          identityRole === 'reviewer'
            ? ['project:read', 'report:review', 'report:publish', 'report:deliver']
            : ['project:read', 'report:write'],
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
            pub_id: 'prj_reports_live',
            tenant_pub_id: 'tnt_reports_live',
            name: '真实报告联调项目',
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
  await page.route('**/api/v2/reports**', async (route) => {
    const request = route.request();
    const requestUrl = new URL(request.url());
    const path = requestUrl.pathname;
    if (request.method() === 'GET' && path.endsWith('/reports')) {
      const secondPage = requestUrl.searchParams.get('cursor') === 'rpt_live_safe';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: secondPage ? 'rpt_live_z_page_02' : 'rpt_live_safe',
              project_pub_id: 'prj_reports_live',
              title: secondPage ? '第二页真实季度报告' : '真实季度报告',
              state: 'draft',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
              cookie: 'SESSION=report-list-canary',
            },
          ],
          page: {
            next_cursor: secondPage ? null : 'rpt_live_safe',
            has_more: !secondPage,
            token: 'Bearer report-page-canary',
          },
        }),
      });
      return;
    }
    if (request.method() === 'GET') {
      if (path.endsWith('/artifacts/pdf')) {
        artifactRequests += 1;
        await route.fulfill({
          status: 200,
          contentType: 'application/pdf',
          body: '%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF',
        });
        return;
      }
      if (path.endsWith('/deliveries')) {
        deliveryReads += 1;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(
            deliveryVisible
              ? [
                  {
                    pub_id: 'dlv_live_safe',
                    report_pub_id: 'rpt_live_safe',
                    recipient_pub_id: 'usr_customer_delivery_safe',
                    delivered_at: '2026-07-25T02:00:00Z',
                    confirmed_at: null,
                    cookie: 'SESSION=report-delivery-projection-canary',
                  },
                ]
              : [],
          ),
        });
        return;
      }
      const secondPage = path.includes('rpt_live_z_page_02');
      if (!secondPage && commentAccepted && !commentVisible) {
        commentReconciliationReads += 1;
        if (commentReconciliationReads >= 2) commentVisible = true;
      }
      if (!secondPage && actionAccepted && !actionVisible) {
        actionReconciliationReads += 1;
        if (actionReconciliationReads >= 2) actionVisible = true;
      }
      if (!secondPage && revisionAccepted && !revisionVisible) {
        revisionReconciliationReads += 1;
        if (revisionReconciliationReads >= 2) revisionVisible = true;
      }
      const reportState = publishedVisible ? 'published' : reviewVisible ? 'approved' : 'draft';
      const versionState = publishedVisible ? 'published' : reviewVisible ? 'approved' : 'frozen';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          pub_id: secondPage ? 'rpt_live_z_page_02' : 'rpt_live_safe',
          project_pub_id: 'prj_reports_live',
          title: secondPage ? '第二页真实季度报告' : '真实季度报告',
          state: secondPage ? 'draft' : reportState,
          created_at: '2026-07-25T00:00:00Z',
          updated_at: '2026-07-25T01:00:00Z',
          versions: [
            {
              pub_id: secondPage ? 'rptv_live_page_01' : 'rptv_live_previous',
              version_number: 1,
              window_start: '2026-06-01T00:00:00Z',
              window_end: '2026-06-30T23:59:59Z',
              filters: { region: 'global' },
              metric_version: 'metric-v2',
              scorer_version: 'scorer-v2',
              fact_snapshot_hash: 'a'.repeat(64),
              status: 'frozen',
              components: [
                {
                  pub_id: secondPage ? 'rptc_live_page_01_00' : 'rptc_live_previous_00',
                  report_version_pub_id: secondPage ? 'rptv_live_page_01' : 'rptv_live_previous',
                  component_type: 'section',
                  ordinal: 0,
                  source: 'ai',
                  payload: { title: '执行摘要', body: '上一版结论仍需补充证据。' },
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
              pub_id: secondPage ? 'rptv_live_page_02' : 'rptv_live_safe',
              version_number: 2,
              window_start: '2026-06-01T00:00:00Z',
              window_end: '2026-06-30T23:59:59Z',
              filters: { region: 'global' },
              metric_version: 'metric-v2',
              scorer_version: 'scorer-v2',
              fact_snapshot_hash: 'd'.repeat(64),
              status: secondPage ? 'frozen' : versionState,
              frozen_facts: [
                {
                  pub_id: secondPage ? 'rptf_live_page_02_00' : 'rptf_live_safe_00',
                  report_version_pub_id: secondPage ? 'rptv_live_page_02' : 'rptv_live_safe',
                  ordinal: 0,
                  payload: { metric: 'mention_rate', value: 0.684 },
                  payload_hash: 'b'.repeat(64),
                  created_at: '2026-07-25T00:20:00Z',
                },
              ],
              components: [
                {
                  pub_id: secondPage ? 'rptc_live_page_02_00' : 'rptc_live_safe_00',
                  report_version_pub_id: secondPage ? 'rptv_live_page_02' : 'rptv_live_safe',
                  component_type: 'section',
                  ordinal: 0,
                  source: 'human',
                  payload: { title: '执行摘要', body: '当前版结论已补充独立证据。' },
                  created_at: '2026-07-25T00:20:00Z',
                },
                {
                  pub_id: secondPage ? 'rptc_live_page_02_01' : 'rptc_live_safe_01',
                  report_version_pub_id: secondPage ? 'rptv_live_page_02' : 'rptv_live_safe',
                  component_type: 'section',
                  ordinal: 1,
                  source: 'ai',
                  payload: {
                    title: '风险建议',
                    body: '建议由人工复核现有证据。',
                    evidence_pub_ids: ['evd_report_risk_safe'],
                  },
                  created_at: '2026-07-25T00:21:00Z',
                },
              ],
              artifacts: [
                {
                  pub_id: secondPage ? 'rpta_live_page_02_pdf' : 'rpta_live_safe_pdf',
                  report_version_pub_id: secondPage ? 'rptv_live_page_02' : 'rptv_live_safe',
                  format: 'pdf',
                  evidence_pub_id: secondPage ? 'evd_report_page_02_pdf' : 'evd_report_safe_pdf',
                  mime_type: 'application/pdf',
                  byte_size: 44,
                  sha256: '5685e2d63d2a3b750e0850b8654c06f87fe9a1b138525deef264166e4152efbc',
                  created_at: '2026-07-25T00:25:00Z',
                },
              ],
              evidence_bindings: [
                {
                  pub_id: secondPage ? 'rptev_live_page_source' : 'rptev_live_safe',
                  report_version_pub_id: secondPage ? 'rptv_live_page_02' : 'rptv_live_safe',
                  evidence_pub_id: 'evd_report_source_safe',
                  purpose: 'frozen_fact_or_component',
                  kind: 'answer_screenshot',
                  access_class: 'customer_private',
                  mime_type: 'image/png',
                  byte_size: 2048,
                  sha256: 'a'.repeat(64),
                  anchor_count: 2,
                  capture_time: '2026-07-25T00:20:00Z',
                  created_at: '2026-07-25T00:30:00Z',
                  object_key: 'Cookie=report-binding-object-canary',
                },
                {
                  pub_id: secondPage ? 'rptev_live_page_risk' : 'rptev_live_risk_safe',
                  report_version_pub_id: secondPage ? 'rptv_live_page_02' : 'rptv_live_safe',
                  evidence_pub_id: 'evd_report_risk_safe',
                  purpose: 'frozen_fact_or_component',
                  kind: 'answer_screenshot',
                  access_class: 'customer_private',
                  mime_type: 'image/png',
                  byte_size: 1024,
                  sha256: 'c'.repeat(64),
                  anchor_count: 1,
                  capture_time: '2026-07-25T00:21:00Z',
                  created_at: '2026-07-25T00:31:00Z',
                },
              ],
              comments: secondPage
                ? []
                : [
                    {
                      pub_id: 'cmt_live_safe',
                      report_version_pub_id: 'rptv_live_safe',
                      parent_pub_id: null,
                      author_pub_id: 'usr_reviewer_safe',
                      body: '请确认 Top 3 的分母是否排除了 degraded 样本。',
                      resolved_at: null,
                      created_at: '2026-07-25T00:30:00Z',
                    },
                    ...(commentVisible
                      ? [
                          {
                            pub_id: 'cmt_live_write_safe',
                            report_version_pub_id: 'rptv_live_safe',
                            parent_pub_id: null,
                            author_pub_id: 'usr_reviewer_safe',
                            body: '请记录真实合同评论',
                            resolved_at: null,
                            created_at: '2026-07-25T00:31:00Z',
                          },
                        ]
                      : []),
                  ],
              reviews:
                !secondPage && reviewVisible
                  ? [
                      {
                        pub_id: 'rvw_live_safe',
                        report_version_pub_id: 'rptv_live_safe',
                        reviewer_pub_id: 'usr_reviewer_safe',
                        decision: 'approved',
                        rationale: '事实、证据、AI 草稿与评论门均已人工核验。',
                        created_at: '2026-07-25T01:00:00Z',
                      },
                    ]
                  : [],
              events:
                !secondPage && publishedVisible
                  ? [
                      {
                        pub_id: 'evt_report_published_safe',
                        report_version_pub_id: 'rptv_live_safe',
                        event_type: 'published',
                        actor_pub_id: 'usr_reviewer_safe',
                        data: { version_number: 2 },
                        created_at: '2026-07-25T01:30:00Z',
                      },
                    ]
                  : [],
              cookie: 'SESSION=report-detail-canary',
              token: 'Bearer report-detail-canary',
            },
            ...(!secondPage && revisionVisible
              ? [
                  {
                    pub_id: 'rptv_live_revision_safe',
                    version_number: 3,
                    window_start: '2026-06-01T00:00:00Z',
                    window_end: '2026-06-30T23:59:59Z',
                    filters: { region: 'global' },
                    metric_version: 'metric-v2',
                    scorer_version: 'scorer-v2',
                    fact_snapshot_hash: 'b'.repeat(64),
                    status: 'frozen',
                    frozen_facts: [
                      {
                        pub_id: 'rptf_live_revision_safe_00',
                        report_version_pub_id: 'rptv_live_revision_safe',
                        ordinal: 0,
                        payload: { metric: 'mention_rate', value: 0.684 },
                        payload_hash: 'e'.repeat(64),
                        created_at: '2026-07-25T02:10:00Z',
                      },
                    ],
                    components: [
                      {
                        pub_id: 'rptc_live_revision_safe_00',
                        report_version_pub_id: 'rptv_live_revision_safe',
                        component_type: 'section',
                        ordinal: 0,
                        source: 'human',
                        payload: {
                          title: '执行摘要',
                          body: '当前版结论已由分析师完成真实合同修订。',
                          evidence_pub_ids: ['evd_report_source_safe'],
                        },
                        created_at: '2026-07-25T02:10:00Z',
                      },
                      {
                        pub_id: 'rptc_live_revision_safe_01',
                        report_version_pub_id: 'rptv_live_revision_safe',
                        component_type: 'section',
                        ordinal: 1,
                        source: 'ai',
                        payload: {
                          title: '风险建议',
                          body: '建议由人工复核现有证据。',
                          evidence_pub_ids: ['evd_report_risk_safe'],
                        },
                        created_at: '2026-07-25T02:11:00Z',
                      },
                    ],
                    artifacts: [],
                    evidence_bindings: [
                      {
                        pub_id: 'rptev_live_revision_source',
                        report_version_pub_id: 'rptv_live_revision_safe',
                        evidence_pub_id: 'evd_report_source_safe',
                        purpose: 'frozen_fact_or_component',
                        kind: 'answer_screenshot',
                        access_class: 'customer_private',
                        mime_type: 'image/png',
                        byte_size: 2048,
                        sha256: 'f'.repeat(64),
                        anchor_count: 2,
                        capture_time: '2026-07-25T02:00:00Z',
                        created_at: '2026-07-25T02:12:00Z',
                      },
                      {
                        pub_id: 'rptev_live_revision_risk',
                        report_version_pub_id: 'rptv_live_revision_safe',
                        evidence_pub_id: 'evd_report_risk_safe',
                        purpose: 'frozen_fact_or_component',
                        kind: 'answer_screenshot',
                        access_class: 'customer_private',
                        mime_type: 'image/png',
                        byte_size: 1024,
                        sha256: '1'.repeat(64),
                        anchor_count: 1,
                        capture_time: '2026-07-25T02:01:00Z',
                        created_at: '2026-07-25T02:13:00Z',
                      },
                    ],
                    comments: [],
                    reviews: [],
                    events: [],
                  },
                ]
              : []),
          ],
          optimization_actions:
            !secondPage && actionVisible
              ? [
                  {
                    pub_id: 'act_live_safe',
                    description: '补齐私有化部署权威材料',
                    owner_pub_id: null,
                    state: retestVisible ? 'done' : 'in_progress',
                    baseline: { source: 'report_review', version: 3 },
                    outcome: retestVisible ? { delta: 6.2 } : null,
                    created_at: '2026-07-25T03:00:00Z',
                    updated_at: retestVisible ? '2026-07-25T03:30:00Z' : '2026-07-25T03:00:00Z',
                    effect_retests: retestVisible
                      ? [
                          {
                            pub_id: 'rts_live_safe',
                            action_pub_id: 'act_live_safe',
                            measured_at: '2026-07-25T03:20:00Z',
                            recorded_by_pub_id: 'usr_analyst_reports_safe',
                            result: {
                              metric: 'mention_rate',
                              baseline_version: 3,
                              delta: 6.2,
                            },
                            created_at: '2026-07-25T03:20:00Z',
                          },
                        ]
                      : [],
                  },
                ]
              : [],
          profile_path: '/secret/profile/report-detail-canary',
          otp: 824911,
        }),
      });
      return;
    }
    if (request.method() === 'OPTIONS') {
      await route.fulfill({
        status: 204,
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET,POST,PATCH,OPTIONS',
          'Access-Control-Allow-Headers':
            'Content-Type,X-Tenant-Id,X-Actor-Id,X-Actor-Role,Idempotency-Key',
        },
      });
      return;
    }
    writes.push({
      method: request.method(),
      url: request.url(),
      body: request.postData() ? request.postDataJSON() : null,
    });
    if (path.endsWith('/publish') || request.method() === 'PATCH') {
      if (path.endsWith('/publish')) publishedVisible = true;
      if (request.method() === 'PATCH' && path.includes('/actions/')) {
        const body = request.postDataJSON() as { state?: string };
        if (body.state === 'in_progress') {
          actionAccepted = true;
          retestVisible = false;
        }
        if (body.state === 'done' && effectRetestAccepted) {
          actionVisible = true;
          retestVisible = true;
        }
      }
      // The exact 204 contract is covered by generated-client and backend contract tests.
      // A body-bearing harness success avoids Chromium classifying intercepted 204 fetches as
      // ERR_ABORTED, so this browser gate can require literal zero failed requests.
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: '{}',
        headers: { 'Access-Control-Allow-Origin': '*' },
      });
      return;
    }
    if (path.endsWith('/actions')) {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ action_pub_id: 'act_live_safe' }),
      });
      return;
    }
    if (path.endsWith('/versions')) {
      revisionAccepted = true;
      revisionIdempotencyKeys.push(request.headers()['idempotency-key'] ?? '');
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          report_pub_id: 'rpt_live_safe',
          report_version_pub_id: 'rptv_live_revision_safe',
          version_number: 3,
          fact_snapshot_hash: 'b'.repeat(64),
          artifacts: {},
        }),
      });
      return;
    }
    if (path.endsWith('/effect-retests')) {
      if (validEffectRetestReceipt) effectRetestAccepted = true;
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          effect_retest_pub_id: validEffectRetestReceipt
            ? 'rts_live_safe'
            : 'Bearer retest-receipt-canary',
          cookie: validEffectRetestReceipt ? undefined : 'SESSION=retest-receipt-canary',
        }),
      });
      return;
    }
    if (path.endsWith('/reviews')) {
      reviewVisible = true;
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ review_pub_id: 'rvw_live_safe' }),
      });
      return;
    }
    if (path.endsWith('/comments')) {
      commentAccepted = true;
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          comment_pub_id: 'cmt_live_write_safe',
          report_pub_id: 'rpt_live_safe',
        }),
      });
      return;
    }
    if (path.endsWith('/deliveries')) {
      deliveryVisible = true;
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          delivery_pub_id: 'dlv_live_safe',
          report_pub_id: 'rpt_live_safe',
        }),
      });
      return;
    }
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ pub_id: 'receipt_safe' }),
    });
  });

  await page.goto(
    '/platform/reports/?report_page=2&report_cursor=rpt_Bearer%20report-cursor-request-canary',
  );
  await expect(page).not.toHaveURL(/report_(?:page|cursor)=/);
  await expect(page.getByRole('heading', { name: '真实季度报告' })).toBeVisible();
  await expect(page.getByText(/列表合同未提供冻结窗口/)).toBeVisible();
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/report_page=2/);
  await expect(page).toHaveURL(/report_cursor=rpt_live_safe/);
  await expect(page.getByRole('heading', { name: '第二页真实季度报告' })).toBeVisible();
  await page.goBack();
  await expect(page).not.toHaveURL(/report_(?:page|cursor)=/);
  await expect(page.getByRole('heading', { name: '真实季度报告' })).toBeVisible();
  await page.getByRole('button', { name: '版本对比' }).click();
  await expect(page.getByRole('heading', { name: '版本 1 → 2' })).toBeVisible();
  await expect(page.getByLabel('真实报告版本正文差异')).toContainText('删除 4 字 · 新增 5 字');
  await page.getByRole('button', { name: '证据编排' }).click();
  await expect(page.getByLabel('冻结事实证据绑定').getByRole('table')).toContainText(
    'evd_report_source_safe',
  );
  await expect(page.getByLabel('冻结事实证据绑定').getByRole('table')).toContainText('2');
  await expect(page.getByText('Cookie=report-binding-object-canary')).toHaveCount(0);
  const artifactDownloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '校验后下载' }).click();
  const artifactDownload = await artifactDownloadPromise;
  expect(artifactDownload.suggestedFilename()).toBe('rpt_live_safe-rptv_live_safe.pdf');
  expect(await artifactDownload.failure()).toBeNull();
  await expect.poll(() => artifactRequests).toBe(1);
  await page.getByRole('button', { name: 'PDF 预览' }).click();
  await expect(page.getByRole('heading', { name: '已冻结 PDF 预览' })).toBeVisible();
  await expect.poll(() => artifactRequests).toBe(2);
  await page.getByRole('button', { name: '章节编辑' }).click();
  await expect(page.getByRole('button', { name: '保存不可变报告版本' })).toBeDisabled();
  await expect(page.getByText('报告修订仅由分析师维护。')).toBeVisible();
  await page.getByRole('button', { name: /审核发布/ }).click();
  await expect(page.getByText('真实 reports API')).toBeVisible();
  await page.getByRole('button', { name: '纳入本次审核', exact: true }).click();
  await page.getByLabel('新增评论').fill('请记录真实合同评论');
  await page.getByRole('button', { name: '添加评论' }).evaluate((button) => {
    button.click();
    button.click();
  });
  await expect(page.getByRole('alert').filter({ hasText: '加载失败' })).toBeVisible();
  await expect(page.getByText('真实审核评论已记录')).toHaveCount(0);
  expect(
    writes.filter((write) =>
      new URL(write.url).pathname.endsWith('/versions/rptv_live_safe/comments'),
    ),
  ).toHaveLength(1);
  expect(commentReconciliationReads).toBe(1);
  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('真实审核评论已记录')).toBeVisible();
  expect(commentReconciliationReads).toBe(2);
  await expect(page.getByText('请记录真实合同评论')).toHaveCount(1);
  await page.getByRole('button', { name: '纳入本次审核', exact: true }).click();
  await page.getByRole('button', { name: '确认 AI 草稿已人工复核' }).click();
  await page.getByRole('button', { name: '提交审核' }).click();
  await page.getByRole('button', { name: '批准发布' }).evaluate((button) => {
    button.click();
    button.click();
  });
  await expect(page.getByText('真实审核决定已记录')).toBeVisible();
  await page.getByRole('button', { name: '发布 v1.0' }).evaluate((button) => {
    button.click();
    button.click();
  });
  await expect(page.getByText('真实发布操作已完成')).toBeVisible();
  await page.getByLabel('客户收件人 ID').fill('Bearer delivery-recipient-form-canary');
  await expect(page.getByText('只接受不含秘密的 usr_ 客户公开标识')).toBeVisible();
  await expect(page.getByRole('button', { name: '创建客户交付' })).toBeDisabled();
  expect(writes).toHaveLength(3);
  await page.getByLabel('客户收件人 ID').fill('usr_customer_delivery_safe');
  await page.getByRole('button', { name: '创建客户交付' }).evaluate((button) => {
    button.scrollIntoView({ block: 'nearest' });
    button.click();
    button.click();
  });
  await expect(page.getByText('真实 delivery 已创建，指定客户可确认接收')).toBeVisible();
  expect(deliveryReads).toBe(1);
  await page.getByLabel('客户收件人 ID').blur();
  await expectSafePageScreenshot(page, 'reports-live-published.png', {
    fullPage: true,
    animations: 'disabled',
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  await page.getByRole('button', { name: '效果复盘' }).click();
  await expect(page.getByRole('button', { name: '开始执行' })).toBeDisabled();
  await expect(page.getByText('优化行动与复测由分析师维护。')).toBeVisible();
  expect(writes).toHaveLength(4);
  expect(writes.map((write) => new URL(write.url).pathname)).toEqual([
    '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/comments',
    '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/reviews',
    '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/publish',
    '/api/v2/reports/rpt_live_safe/deliveries',
  ]);
  expect(writes[0]?.body).toEqual({ body: '请记录真实合同评论', parent_pub_id: null });
  expect(writes[1]?.body).toMatchObject({ decision: 'approved' });
  expect(writes[3]?.body).toEqual({ recipient_pub_id: 'usr_customer_delivery_safe' });
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /report-detail-canary|report-cursor-request-canary|SESSION=|Bearer |824911|\/secret\/profile/i,
  );
  expect(JSON.stringify(writes)).not.toMatch(/Cookie|Bearer|otp|profile_path/i);
  commentAccepted = false;
  commentVisible = false;
  commentReconciliationReads = 0;
  reviewVisible = false;
  publishedVisible = false;
  deliveryVisible = false;
  identityRole = 'analyst';
  await page.evaluate(() => localStorage.setItem('geo.e2e.report-role', 'analyst'));
  await page.reload();
  await expect(page.getByRole('heading', { name: '优化建议与效果复盘' })).toBeVisible();
  await page.getByRole('button', { name: '章节编辑' }).click();
  await page.getByLabel('真实章节正文').fill('当前版结论已由分析师完成真实合同修订。');
  await page.getByLabel('组件证据 ID').fill('Bearer report-revision-secret-canary');
  await expect(page.getByText('证据绑定只接受不含秘密的 evd_ 公开标识')).toBeVisible();
  await expect(page.getByRole('button', { name: '保存不可变报告版本' })).toBeDisabled();
  await page.getByRole('button', { name: /风险建议/ }).click();
  await expect(page.getByRole('button', { name: '保存不可变报告版本' })).toBeDisabled();
  await page.getByRole('button', { name: /执行摘要/ }).click();
  expect(writes).toHaveLength(4);
  await page.getByLabel('组件证据 ID').fill('evd_report_source_safe');
  await page.getByRole('button', { name: '保存不可变报告版本' }).evaluate((button) => {
    button.click();
    button.click();
  });
  await expect(page.getByRole('alert').filter({ hasText: '加载失败' })).toBeVisible();
  await expect(page.getByText('真实报告版本 3 已冻结')).toHaveCount(0);
  expect(revisionReconciliationReads).toBe(1);
  expect(writes).toHaveLength(5);
  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('真实报告版本 3 已冻结')).toBeVisible();
  expect(revisionReconciliationReads).toBe(2);
  expect(revisionIdempotencyKeys).toHaveLength(1);
  expect(revisionIdempotencyKeys[0]).toMatch(/^report-revision-[0-9a-f-]{36}$/);
  await page.getByRole('button', { name: '效果复盘' }).click();
  await page.getByRole('button', { name: '开始执行' }).evaluate((button) => {
    button.click();
    button.click();
  });
  await expect(page.getByRole('alert').filter({ hasText: '加载失败' })).toBeVisible();
  await expect(page.getByText('真实优化行动已登记')).toHaveCount(0);
  expect(actionReconciliationReads).toBe(1);
  expect(writes).toHaveLength(7);
  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('真实优化行动已登记')).toBeVisible();
  expect(actionReconciliationReads).toBe(2);
  expect(writes).toHaveLength(7);
  await page.getByLabel('效果变化').fill('101');
  await expect(page.getByText('效果变化必须在 -100 到 100 之间')).toBeVisible();
  await expect(page.getByRole('button', { name: '记录复测效果' })).toBeDisabled();
  expect(writes).toHaveLength(7);
  await page.getByLabel('效果变化').fill('6.2');
  await page.getByRole('button', { name: '记录复测效果' }).evaluate((button) => {
    button.click();
    button.click();
  });
  await expect(page.getByText('真实效果复测已追加记录')).toBeVisible();
  expect(writes.map((write) => new URL(write.url).pathname)).toEqual([
    '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/comments',
    '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/reviews',
    '/api/v2/reports/rpt_live_safe/versions/rptv_live_safe/publish',
    '/api/v2/reports/rpt_live_safe/deliveries',
    '/api/v2/reports/rpt_live_safe/versions',
    '/api/v2/reports/rpt_live_safe/actions',
    '/api/v2/reports/rpt_live_safe/actions/act_live_safe',
    '/api/v2/reports/rpt_live_safe/actions/act_live_safe/effect-retests',
    '/api/v2/reports/rpt_live_safe/actions/act_live_safe',
  ]);
  expect(writes[4]?.body).toEqual({
    components: [
      {
        component_type: 'section',
        source: 'human',
        title: '执行摘要',
        body: '当前版结论已由分析师完成真实合同修订。',
        evidence_pub_ids: ['evd_report_source_safe'],
      },
      {
        component_type: 'section',
        source: 'ai',
        title: '风险建议',
        body: '建议由人工复核现有证据。',
        evidence_pub_ids: ['evd_report_risk_safe'],
      },
    ],
  });
  expect(writes[5]?.body).toMatchObject({
    description: '补齐私有化部署权威材料',
    owner_pub_id: null,
  });
  expect(writes[6]?.body).toEqual({ state: 'in_progress', outcome: null });
  expect(writes[7]?.body).toMatchObject({
    result: { metric: 'mention_rate', baseline_version: 3, delta: 6.2 },
  });
  expect(writes[8]?.body).toEqual({ state: 'done', outcome: { delta: 6.2 } });

  validEffectRetestReceipt = false;
  await page.getByRole('button', { name: '开始执行' }).click();
  await page.getByLabel('效果变化').fill('7.1');
  await page.getByRole('button', { name: '记录复测效果' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '加载失败' })).toBeVisible();
  expect(writes.map((write) => new URL(write.url).pathname).slice(-2)).toEqual([
    '/api/v2/reports/rpt_live_safe/actions/act_live_safe',
    '/api/v2/reports/rpt_live_safe/actions/act_live_safe/effect-retests',
  ]);
  expect(writes).toHaveLength(11);
  const exposedSurfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(exposedSurfaces).not.toMatch(/retest-receipt-canary|Bearer |Cookie=/i);
  expect(JSON.stringify(writes)).not.toMatch(/Cookie|Bearer|otp|profile_path/i);
  expect(await syntheticHttpResponseCount(page, 'report-publish-no-content')).toBe(1);
  expect(await syntheticHttpResponseCount(page, 'report-patch-no-content')).toBe(3);
});

test('report 404 uses the same forbidden surface and does not probe detail', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_reports_forbidden');
    localStorage.setItem('geo.session.actor', 'analyst-reports-forbidden');
    localStorage.setItem('geo.session.role', 'analyst');
  });
  await installSyntheticHttpResponses(page, [
    {
      id: 'report-catalog-forbidden',
      path: '/api/v2/reports',
      status: 404,
      body: {
        error: {
          code: 'not_found',
          message: 'Bearer forbidden-report-canary',
          request_id: 'req_safe',
        },
      },
    },
    {
      id: 'report-detail-forbidden',
      path: '/api/v2/reports/',
      match: 'prefix',
      status: 404,
    },
  ]);
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_reports_forbidden',
        user_pub_id: 'usr_reports_forbidden',
        role: 'analyst',
        permissions: ['project:read'],
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
            pub_id: 'prj_reports_forbidden',
            tenant_pub_id: 'tnt_reports_forbidden',
            name: '报告权限隔离项目',
            state: 'active',
            created_at: '2026-07-25T00:00:00Z',
            updated_at: '2026-07-25T00:00:00Z',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );
  await page.goto('/platform/reports/');
  await expect(page.getByText('无权查看')).toBeVisible();
  await expect(page.getByText('Bearer forbidden-report-canary')).toHaveCount(0);
  expect(await syntheticHttpResponseCount(page, 'report-catalog-forbidden')).toBe(1);
  expect(await syntheticHttpResponseCount(page, 'report-detail-forbidden')).toBe(0);
});
