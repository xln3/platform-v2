import { expect, test } from './runtime-fixture';
import type { Locator } from '@playwright/test';
import { expectAccessible } from './accessibility';
import { expectSafeLocatorScreenshot, expectSafePageScreenshot } from './screenshot-safety';
import { installSyntheticHttpResponses, syntheticHttpResponseCount } from './synthetic-http';

const customerReportHtml =
  '<!doctype html><title>真实客户在线报告</title><main><h1>执行摘要</h1><p>已发布报告，包含<strong>已核验结论</strong>。</p><ul><li>证据一</li><li>证据二</li></ul><table><caption>关键指标</caption><thead><tr><th scope="col">指标</th><th scope="col">值</th></tr></thead><tbody><tr><th scope="row">提及率</th><td>68.4%</td></tr></tbody></table><p><a href="https://source.example/report">查看公开来源</a></p></main>';
const customerReportPdf = '%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF';
const customerReportHtmlSha256 = '40eb6105778ec6e9ab98b518801d34fb4aad2f0ee2a931e3694a040e392cd4bb';
const customerReportPdfSha256 = '5685e2d63d2a3b750e0850b8654c06f87fe9a1b138525deef264166e4152efbc';

const synchronouslyActivateTwice = async (button: Locator) => {
  await button.evaluate((element) => {
    element.addEventListener('click', () => (element as HTMLButtonElement).click(), { once: true });
  });
  await button.click();
};

test('validated customer reads mounted data and serializes every write without secret leakage', async ({
  page,
}) => {
  const exportBodies: unknown[] = [];
  const packageBodies: unknown[] = [];
  const reportQuestionBodies: unknown[] = [];
  const deliveryConfirmBodies: unknown[] = [];
  let reportArtifactRequests = 0;
  let deliveryConfirmed = false;
  let reportQuestionAccepted = false;
  let reportQuestionAuthorityReads = 0;
  let releaseDelayedReportQuestion: (() => void) | null = null;
  const delayedReportQuestionResponse = new Promise<void>((resolve) => {
    releaseDelayedReportQuestion = resolve;
  });
  const profileBodies: unknown[] = [];
  const assetConfirmationBodies: unknown[] = [];
  const profileCursors: Array<string | null> = [];
  const assetConfirmationCursors: Array<string | null> = [];
  const evidenceCursors: Array<string | null> = [];
  let profileCreated = false;
  let assetConfirmationCreated = false;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_product_live');
    localStorage.setItem('geo.session.actor', 'customer-product-live');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_customer_product_live',
        user_pub_id: 'usr_customer_product_live',
        role: 'customer',
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
            pub_id: 'prj_customer_product_live',
            tenant_pub_id: 'tnt_customer_product_live',
            name: '客户产品联调项目',
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
  await page.route('**/api/v2/projects/*/resources/*', (route) => {
    const kind = new URL(route.request().url()).pathname.split('/').at(-1);
    const resource =
      kind === 'competitors'
        ? {
            pub_id: 'cmp_customer_live',
            resource_kind: 'competitors',
            data: {
              name: '真实客户竞品',
              token: 'Bearer catalog-competitor-canary',
            },
          }
        : kind === 'query-items'
          ? {
              pub_id: 'qry_customer_catalog_live',
              resource_kind: 'query-items',
              data: {
                parent_pub_id: 'qgrp_customer_catalog_live',
                text: '真实目录中的客户关注问题',
                priority: 10,
                cookie: 'SESSION=catalog-query-canary',
              },
            }
          : kind === 'goals'
            ? {
                pub_id: 'gol_customer_catalog_live',
                resource_kind: 'goals',
                data: {
                  metric: 'mention_rate',
                  state: 'active',
                  payload: { target: 0.8, otp: 429155 },
                  profile_path: '/secret/profile/catalog-goal-canary',
                },
              }
            : {
                pub_id: 'brd_customer_live',
                resource_kind: 'brands',
                data: {
                  name: '真实客户品牌',
                  website: 'https://brand.example.test',
                  cookie: 'SESSION=catalog-brand-canary',
                  profile_path: '/secret/profile/catalog-brand-canary',
                },
              };
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          ...resource,
          project_pub_id: 'prj_customer_product_live',
          version: 1,
          otp: 731904,
        },
      ]),
    });
  });
  await page.route('**/api/v2/projects/*/client-profile/versions**', async (route) => {
    const searchParams = new URL(route.request().url()).searchParams;
    const cursor = searchParams.get('cursor');
    const limit = searchParams.get('limit');
    profileCursors.push(cursor);
    if (route.request().method() === 'POST') {
      profileBodies.push(route.request().postDataJSON());
      profileCreated = true;
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          pub_id: 'cpv_customer_live_03',
          project_pub_id: 'prj_customer_product_live',
          revision: 3,
          company_name: '真实客户企业',
          contact_role: '品牌负责人',
          audience: '需要可验证企业知识服务的采购团队',
          public_statement: '真实客户企业提供可公开核验的知识服务。',
          created_at: '2026-07-25T01:00:00Z',
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: cursor
          ? [
              {
                pub_id: 'cpv_customer_history_01',
                project_pub_id: 'prj_customer_product_live',
                revision: 1,
                company_name: '历史客户企业',
                contact_role: '品牌负责人',
                audience: '历史企业采购团队',
                public_statement: '历史版本仅用于审计，不覆盖当前表单。',
                created_at: '2026-07-24T00:00:00Z',
              },
            ]
          : [
              {
                pub_id: profileCreated ? 'cpv_customer_live_03' : 'cpv_customer_live_01',
                project_pub_id: 'prj_customer_product_live',
                revision: profileCreated ? 3 : 2,
                company_name: '真实客户企业',
                contact_role: '品牌负责人',
                audience: '需要可验证企业知识服务的采购团队',
                public_statement: '真实客户企业提供可公开核验的知识服务。',
                created_at: profileCreated ? '2026-07-25T01:00:00Z' : '2026-07-25T00:00:00Z',
              },
              ...(profileCreated && limit !== '1'
                ? [
                    {
                      pub_id: 'cpv_customer_live_01',
                      project_pub_id: 'prj_customer_product_live',
                      revision: 2,
                      company_name: '真实客户企业',
                      contact_role: '品牌负责人',
                      audience: '需要可验证企业知识服务的采购团队',
                      public_statement: '真实客户企业提供可公开核验的知识服务。',
                      created_at: '2026-07-25T00:00:00Z',
                    },
                  ]
                : []),
            ],
        next_cursor: cursor ? null : profileCreated && limit === '1' ? '3' : '2',
      }),
    });
  });
  await page.route('**/api/v2/projects/*/asset-confirmations**', async (route) => {
    const searchParams = new URL(route.request().url()).searchParams;
    const cursor = searchParams.get('cursor');
    const limit = searchParams.get('limit');
    assetConfirmationCursors.push(cursor);
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      assetConfirmationBodies.push(body);
      assetConfirmationCreated = true;
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          pub_id: 'acv_customer_live_03',
          project_pub_id: 'prj_customer_product_live',
          revision: 3,
          ...body,
          website: body.website,
          created_at: '2026-07-25T01:00:00Z',
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: cursor
          ? [
              {
                pub_id: 'acv_customer_history_01',
                project_pub_id: 'prj_customer_product_live',
                revision: 1,
                brand_name: '历史确认品牌',
                website: 'https://history.example.test',
                product_name: '历史确认产品',
                competitor_name: '历史确认竞品',
                prohibited_claim: '历史禁止表述',
                created_at: '2026-07-24T00:00:00Z',
              },
            ]
          : [
              {
                pub_id: assetConfirmationCreated ? 'acv_customer_live_03' : 'acv_customer_live_02',
                project_pub_id: 'prj_customer_product_live',
                revision: assetConfirmationCreated ? 3 : 2,
                brand_name: assetConfirmationCreated ? '确认品牌' : '当前确认品牌',
                website: assetConfirmationCreated
                  ? 'https://confirmed.example'
                  : 'https://current.example.test',
                product_name: assetConfirmationCreated ? '确认产品' : '当前确认产品',
                competitor_name: assetConfirmationCreated ? '确认竞品' : '当前确认竞品',
                prohibited_claim: assetConfirmationCreated ? '未经证实的行业第一' : '当前禁止表述',
                created_at: assetConfirmationCreated
                  ? '2026-07-25T01:00:00Z'
                  : '2026-07-25T00:00:00Z',
              },
              ...(assetConfirmationCreated && limit !== '1'
                ? [
                    {
                      pub_id: 'acv_customer_live_02',
                      project_pub_id: 'prj_customer_product_live',
                      revision: 2,
                      brand_name: '当前确认品牌',
                      website: 'https://current.example.test',
                      product_name: '当前确认产品',
                      competitor_name: '当前确认竞品',
                      prohibited_claim: '当前禁止表述',
                      created_at: '2026-07-25T00:00:00Z',
                    },
                  ]
                : []),
            ],
        next_cursor: cursor ? null : assetConfirmationCreated && limit === '1' ? '3' : '2',
      }),
    });
  });
  await page.route('**/api/v2/analytics/overview**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          metric: 'mention_rate',
          value: 0.75,
          numerator: 3,
          denominator: 4,
          state: 'ready',
          metric_version: 'metric-v1',
          scorer_version: 'scorer-v1',
          filter_hash: 'safe',
          trace_tokens: [],
          token: 'Bearer analytics-canary',
        },
      ]),
    }),
  );
  await page.route('**/api/v2/analytics/delta**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        mention_rate: {
          current: 0.75,
          previous: 0.5,
          delta: 0.25,
          cookie: 'SESSION=analytics-delta-canary',
        },
        token: 'Bearer analytics-delta-root-canary',
      }),
    }),
  );
  await page.route('**/api/v2/analytics/competitors**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          competitor: '真实分析竞品',
          mention_rate: 0.625,
          mention_count: 5,
          answer_count: 8,
          average_rank: 2.4,
          metric_version: 'competitor-aggregation-v1',
          profile_path: '/secret/profile/analytics-competitor-canary',
          otp: 318294,
        },
      ]),
    }),
  );
  await page.route('**/api/v2/analytics/breakdown**', (route) => {
    const groupBy = new URL(route.request().url()).searchParams.get('group_by');
    const dimensions =
      groupBy === 'day'
        ? { day: '2026-07-25' }
        : groupBy === 'model'
          ? { model: 'doubao' }
          : groupBy === 'region_mode'
            ? { region: 'east', mode: 'deep' }
            : {
                question_pub_id: 'qry_live_safe',
                question_text: '真实合同问题',
              };
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          group_by: groupBy,
          ...dimensions,
          answer_count: 4,
          mentioned_count: 3,
          mention_rate: 0.75,
          average_rank: 2,
          citation_coverage: 0.5,
          token: 'Bearer analytics-breakdown-canary',
        },
      ]),
    });
  });
  await page.route('**/api/v2/analytics/answers**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            pub_id: 'ans_live_safe',
            project_pub_id: 'prj_customer_product_live',
            run_pub_id: 'run_customer_live_safe',
            config_version_pub_id: 'cfv_customer_live_safe',
            query_pub_id: 'qry_live_safe',
            query_text: '真实合同问题',
            response_text: '真实合同回答，包含可追溯引用。',
            model: 'doubao',
            region: '上海',
            mode: 'deep',
            eligible: true,
            degraded: false,
            capture_time: '2026-07-25T01:00:00Z',
            mentioned: true,
            rank: 1,
            sentiment: 'positive',
            recommendation_state: null,
            citation_count: 2,
            cookie: 'SESSION=answers-canary',
            profile_path: '/secret/profile/answers-canary',
            previous_run_pub_id: 'Cookie=answer-provenance-canary',
          },
        ],
        page: { next_cursor: null, has_more: false },
        token: 'Bearer answers-canary',
      }),
    }),
  );
  await page.route('**/api/v2/analytics/answers/*/relations', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        answer_pub_id: 'ans_live_safe',
        citations: [
          {
            pub_id: 'cit_live_safe',
            ordinal: 1,
            canonical_url: 'https://source.example/review',
            host: 'source.example',
            title: '真实独立来源',
            cited_text: '真实可追溯提及段落。',
            own_source: false,
            content_hash: 'c'.repeat(64),
          },
          {
            // 密钥形 cited_text 的引用按当前 fail-closed 投影口径整条丢弃（同
            // customer-evidence-integrity 的 201→199 语义）；金丝雀因此永不到达浏览器界面。
            pub_id: 'cit_live_secret',
            ordinal: 2,
            canonical_url: 'https://dropped.example/review',
            host: 'dropped.example',
            title: '被安全投影丢弃的引用',
            cited_text: 'Bearer relation-cited-text-canary',
            own_source: false,
            content_hash: 'c'.repeat(64),
          },
        ],
        evidence: [
          {
            pub_id: 'evd_live_safe',
            relation_type: 'visualizes',
            kind: 'answer_screenshot',
            access_class: 'customer_private',
            sha256: 'a'.repeat(64),
            mime_type: 'image/png',
            byte_size: 1024,
            source_url: 'https://capture.example/answer',
            capture_time: '2026-07-25T01:00:00Z',
            anchors: [
              {
                pub_id: 'anch_live_safe',
                text_start: 0,
                text_end: 4,
                bbox: { x: 1, y: 2, width: 3, height: 4 },
                page_number: null,
                quote_hash: 'd'.repeat(64),
              },
            ],
            object_key: 'Cookie=relation-object-key-canary',
          },
        ],
        history: [
          {
            pub_id: 'diff_live_safe',
            before_evidence_pub_id: 'evd_live_before',
            after_evidence_pub_id: 'evd_live_safe',
            similarity: 0.875,
            visual_diff_available: true,
            created_at: '2026-07-25T01:00:00Z',
            text_diff: 'Bearer relation-diff-canary',
          },
        ],
      }),
    }),
  );
  await page.route('**/api/v2/exports/metrics', async (route) => {
    exportBodies.push(route.request().postDataJSON());
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        export_pub_id: 'exp_live_safe',
        evidence_pub_id: 'evd_export_safe',
        format: 'xlsx',
        row_count: 1,
        filter_hash: 'e'.repeat(64),
        fact_snapshot_hash: 'f'.repeat(64),
        metric_version: 'metrics-v2',
        scorer_version: 'scorer-v1',
        token: 'Bearer export-canary',
      }),
    });
  });
  await page.route('**/api/v2/evidence/assets**', (route) => {
    const cursor = new URL(route.request().url()).searchParams.get('cursor');
    evidenceCursors.push(cursor);
    const secondPage = cursor === 'evd_customer_cursor_safe_02';
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            pub_id: secondPage ? 'evd_live_page_02' : 'evd_live_safe',
            kind: 'answer_screenshot',
            mime_type: 'image/png',
            capture_time: '2026-07-25T01:00:00Z',
            sha256: 'a'.repeat(64),
            cookie: 'SESSION=evidence-canary',
          },
        ],
        page: {
          next_cursor: secondPage ? null : 'evd_customer_cursor_safe_02',
          has_more: !secondPage,
        },
        token: 'Bearer evidence-canary',
      }),
    });
  });
  // 证据画廊逐资产拉取 content（VerifiedBlobImage）；上方 `assets**` 通配会 shadow 该
  // 路径并返回 JSON，加载器因 MIME 不符中止请求（request-failed）。补一个合法 PNG 响应；
  // 尺寸/哈希与夹具元数据不符时加载器 fail-closed 为占位态，不产生运行时告警。
  await page.route('**/api/v2/evidence/assets/*/content', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
        'base64',
      ),
    }),
  );
  await page.route('**/api/v2/evidence/packages', async (route) => {
    const packageBody = route.request().postDataJSON() as { package_pub_id: string };
    packageBodies.push(packageBody);
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        package_pub_id: packageBody.package_pub_id,
        manifest_sha256: 'b'.repeat(64),
        state: 'ready',
        cookie: 'SESSION=package-canary',
      }),
    });
  });
  await page.route('**/api/v2/reports**', async (route) => {
    const request = route.request();
    const requestUrl = new URL(request.url());
    const path = requestUrl.pathname;
    const deliveryMatch = path.match(/\/reports\/(rpt_[^/]+)\/deliveries$/);
    if (request.method() === 'GET' && deliveryMatch) {
      const reportPubId = deliveryMatch[1] ?? '';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            pub_id: `dlv_${reportPubId.slice(4)}_safe`,
            report_pub_id: reportPubId,
            recipient_pub_id: 'usr_customer_product_live',
            delivered_at: '2026-07-25T01:15:00Z',
            confirmed_at: deliveryConfirmed ? '2026-07-25T01:20:00Z' : null,
            confirmation_comment: deliveryConfirmed ? 'Bearer delivery-comment-canary' : null,
            cookie: 'SESSION=delivery-extension-canary',
            otp: 318294,
            recipient_shadow: 'Bearer delivery-recipient-canary',
          },
        ]),
      });
      return;
    }
    if (request.method() === 'POST' && /\/deliveries\/dlv_[^/]+\/confirm$/.test(path)) {
      deliveryConfirmBodies.push(request.postDataJSON());
      deliveryConfirmed = true;
      const confirmedDeliveryPubId = path.match(/\/deliveries\/(dlv_[^/]+)\/confirm$/)?.[1] ?? '';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          delivery_pub_id: confirmedDeliveryPubId,
          state: 'confirmed',
          token: 'Bearer delivery-confirm-response-canary',
        }),
      });
      return;
    }
    if (request.method() === 'GET' && /\/artifacts\/(?:html|pdf)$/.test(path)) {
      reportArtifactRequests += 1;
      const html = path.endsWith('/html');
      await route.fulfill({
        status: 200,
        contentType: html ? 'text/html' : 'application/pdf',
        body: html ? customerReportHtml : customerReportPdf,
      });
      return;
    }
    if (request.method() === 'POST') {
      reportQuestionBodies.push(request.postDataJSON());
      reportQuestionAccepted = true;
      if (reportQuestionBodies.length === 2) await delayedReportQuestionResponse;
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          comment_pub_id:
            reportQuestionBodies.length === 2 ? 'cmt_customer_delayed' : 'cmt_customer_safe',
          report_pub_id: path.split('/')[4],
        }),
      });
      return;
    }
    const reportQuestionVisible =
      reportQuestionAccepted &&
      /^\/api\/v2\/reports\/rpt_[^/]+$/.test(path) &&
      (reportQuestionAuthorityReads += 1) > 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        path.endsWith('/reports')
          ? {
              data: [
                {
                  pub_id:
                    requestUrl.searchParams.get('cursor') === 'rpt_customer_safe'
                      ? 'rpt_customer_z_page_02'
                      : 'rpt_customer_safe',
                  project_pub_id: 'prj_customer_product_live',
                  title:
                    requestUrl.searchParams.get('cursor') === 'rpt_customer_safe'
                      ? '第二页真实客户报告'
                      : '真实客户报告',
                  state: 'published',
                  created_at: '2026-07-25T00:00:00Z',
                  updated_at: '2026-07-25T01:00:00Z',
                  otp: 824911,
                },
              ],
              page: {
                next_cursor:
                  requestUrl.searchParams.get('cursor') === 'rpt_customer_safe'
                    ? null
                    : 'rpt_customer_safe',
                has_more: requestUrl.searchParams.get('cursor') !== 'rpt_customer_safe',
              },
            }
          : {
              pub_id: path.includes('rpt_customer_z_page_02')
                ? 'rpt_customer_z_page_02'
                : 'rpt_customer_safe',
              project_pub_id: 'prj_customer_product_live',
              title: path.includes('rpt_customer_z_page_02')
                ? '第二页真实客户报告'
                : '真实客户报告',
              state: 'published',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
              versions: [
                {
                  pub_id: path.includes('rpt_customer_z_page_02')
                    ? 'rptv_customer_page_02'
                    : 'rptv_customer_safe',
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
                      pub_id: path.includes('rpt_customer_z_page_02')
                        ? 'rpta_customer_page_02_html'
                        : 'rpta_customer_safe_html',
                      report_version_pub_id: path.includes('rpt_customer_z_page_02')
                        ? 'rptv_customer_page_02'
                        : 'rptv_customer_safe',
                      format: 'html',
                      evidence_pub_id: path.includes('rpt_customer_z_page_02')
                        ? 'evd_customer_page_02_html'
                        : 'evd_customer_safe_html',
                      mime_type: 'text/html',
                      byte_size: 457,
                      sha256: customerReportHtmlSha256,
                      created_at: '2026-07-25T01:00:00Z',
                    },
                    {
                      pub_id: path.includes('rpt_customer_z_page_02')
                        ? 'rpta_customer_page_02_pdf'
                        : 'rpta_customer_safe_pdf',
                      report_version_pub_id: path.includes('rpt_customer_z_page_02')
                        ? 'rptv_customer_page_02'
                        : 'rptv_customer_safe',
                      format: 'pdf',
                      evidence_pub_id: path.includes('rpt_customer_z_page_02')
                        ? 'evd_customer_page_02_pdf'
                        : 'evd_customer_safe_pdf',
                      mime_type: 'application/pdf',
                      byte_size: 44,
                      sha256: customerReportPdfSha256,
                      created_at: '2026-07-25T01:00:00Z',
                    },
                  ],
                  evidence_bindings: [],
                  reviews: [],
                  comments:
                    reportQuestionVisible && !path.includes('rpt_customer_z_page_02')
                      ? [
                          {
                            pub_id: 'cmt_customer_safe',
                            report_version_pub_id: 'rptv_customer_safe',
                            parent_pub_id: null,
                            author_pub_id: 'usr_customer_product_live',
                            body: '请解释真实报告中的冻结口径',
                            resolved_at: null,
                            created_at: '2026-07-25T01:25:00Z',
                          },
                        ]
                      : [],
                  events: [],
                  token: 'Bearer customer-report-detail-canary',
                },
              ],
              optimization_actions: [],
            },
      ),
    });
  });

  const dashboardMetric = (
    code: string,
    label: string,
    group: string,
    format: 'percentage' | 'score' | 'rank' | 'count' | 'decimal',
    value: number,
    direction: 'higher' | 'lower' | 'neutral' = 'higher',
  ) => ({
    code,
    label,
    group,
    format,
    direction,
    value,
    state: 'ready',
    version: 'customer-metrics-v1',
  });
  const dimensionMetrics = [
    dashboardMetric('mention_rate', '品牌提及率', 'visibility', 'percentage', 0.75),
    dashboardMetric('top3_rate', 'Top3 率', 'ranking', 'percentage', 0.5),
    dashboardMetric('average_rank', '平均排名', 'ranking', 'rank', 2, 'lower'),
    dashboardMetric('recommendation_rate', '品牌推荐率', 'visibility', 'percentage', 0.625),
    dashboardMetric('citation_coverage', '引用覆盖率', 'source', 'percentage', 0.5),
  ];
  const dashboardMetrics = [
    dashboardMetric('geo_visibility_index', 'GEO 可见度指数', 'composite', 'score', 75),
    dashboardMetric('competitive_power_index', '竞争力指数', 'composite', 'score', 68),
    dashboardMetric('source_authority_index', '信源权威指数', 'composite', 'score', 71),
    dashboardMetric('content_readiness_index', '内容准备度指数', 'composite', 'score', 64),
    dashboardMetric('reputation_index', 'AI 口碑指数', 'composite', 'score', 79),
    dashboardMetric('cognition_consistency_index', 'AI 认知一致性指数', 'composite', 'score', 73),
    ...dimensionMetrics,
    dashboardMetric('share_of_voice', '竞争声量份额', 'competition', 'percentage', 0.4),
    dashboardMetric('own_source_answer_rate', '官网引用回答率', 'source', 'percentage', 0.25),
    dashboardMetric('positive_rate', '正面回答率', 'reputation', 'percentage', 0.75),
  ];
  await page.route('**/api/v2/customer-dashboard/metrics/catalog**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        schema_version: 'customer-metric-catalog-v1',
        metrics: dashboardMetrics.map(({ value: _value, state: _state, ...metric }) => ({
          ...metric,
          description: `${metric.label}的真实客户合同口径。`,
        })),
      }),
    }),
  );
  await page.route('**/api/v2/customer-dashboard/projects/**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/answers')) {
      const offset = Number(url.searchParams.get('offset') ?? '0');
      const limit = Number(url.searchParams.get('limit') ?? '20');
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          schema_version: 'customer-answer-page-v1',
          project_pub_id: 'prj_customer_product_live',
          data:
            offset === 0
              ? [
                  {
                    answer_pub_id: 'ans_customer_product_live_01',
                    query_pub_id: 'qry_customer_product_live_01',
                    query_text: '真实客户合同问题',
                    response_text: '真实客户回答原文，完整展示品牌提及、推荐语境与引用信息。',
                    model: 'doubao',
                    region: 'east',
                    mode: 'deep',
                    capture_time: '2026-07-25T00:00:00Z',
                    mentioned: true,
                    rank: 1,
                    sentiment: 'positive',
                    recommended: true,
                    citation_count: 2,
                  },
                ]
              : [],
          page: { total: 1, offset, limit, has_more: false },
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        schema_version: 'customer-dashboard-v1',
        metric_version: 'customer-metrics-v1',
        project_pub_id: 'prj_customer_product_live',
        brand_name: '真实客户品牌',
        state: 'ready',
        generated_at: '2026-07-25T01:00:00Z',
        as_of: '2026-07-25T00:00:00Z',
        window: { start: '2026-07-01', end: '2026-07-25', filters: {} },
        metrics: dashboardMetrics,
        models: [{ key: 'doubao', label: 'doubao', metrics: dimensionMetrics }],
        competitors: [
          {
            name: '真实分析竞品',
            metrics: [
              dashboardMetric('share_of_voice', '竞争声量份额', 'competition', 'percentage', 0.3),
            ],
          },
        ],
        questions: [
          {
            query_pub_id: 'qry_customer_product_live_01',
            query_text: '真实客户合同问题',
            query_group: '选型',
            metrics: dimensionMetrics,
          },
        ],
        sources: [
          {
            host: 'source.example',
            references: 2,
            share: 1,
            own_source: false,
            answers: 1,
          },
        ],
        regions: [{ key: 'east', label: 'east', metrics: dimensionMetrics }],
        modes: [{ key: 'deep', label: 'deep', metrics: dimensionMetrics }],
        trends: ['2026-07-21', '2026-07-22', '2026-07-23', '2026-07-24', '2026-07-25'].map(
          (date) => ({ date, metrics: dimensionMetrics }),
        ),
        risk: { metrics: [], by_model: [] },
        source_audit: { metrics: [], verdicts: {} },
        snapshot_hash: 'a'.repeat(64),
      }),
    });
  });

  await page.goto('/platform/customer/');
  await expect(page.getByRole('heading', { name: '真实客户品牌 · AI 认知资产总览' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '监测运行中' })).toHaveCount(0);
  await expect(page.getByRole('progressbar', { name: '项目进度' })).toHaveCount(0);
  await expect(page.getByText('资料确认', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Project stage', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '六大经营指数' })).toBeVisible();
  await expect(
    page.locator('.geo-kpi-card').filter({ hasText: '品牌提及率' }).getByText('75.0%'),
  ).toBeVisible();
  await expect(page.getByRole('heading', { name: '真实客户品牌 · 真实 AI 回答' })).toBeVisible();
  await expect(
    page
      .locator('.geo-answer-card__response p')
      .filter({ hasText: '真实客户回答原文，完整展示品牌提及、推荐语境与引用信息。' }),
  ).toBeVisible();
  await expectSafePageScreenshot(page, 'customer-live-home.png', {
    fullPage: true,
    animations: 'disabled',
  });
  await page.goto(
    '/platform/customer/?section=profile&declaration_page=2&declaration_cursor=rev_Bearer%20profile-cursor-canary',
  );
  await expect(page).not.toHaveURL(/declaration_(?:page|cursor)=/);
  await expect(page.getByText('客户声明 v2')).toBeVisible();
  const profileHistory = page.getByRole('heading', { name: '字段历史' }).locator('..');
  await profileHistory.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/declaration_page=2/);
  await expect(page).toHaveURL(/declaration_cursor=rev_2/);
  await expect(profileHistory.getByText('历史客户企业')).toHaveCount(0);
  await expect(profileHistory.getByText('客户声明 v1')).toBeVisible();
  await expect(page.getByLabel('企业全称')).toHaveValue('真实客户企业');
  await page.goBack();
  await expect(page).not.toHaveURL(/declaration_(?:page|cursor)=/);
  await page.getByRole('checkbox', { name: /我确认上述客户声明真实/ }).check();
  await page.getByRole('button', { name: '保存并生成版本' }).click();
  await expect(page.getByText('客户声明 v3 · 已保存')).toBeVisible();
  await page.goto(
    '/platform/customer/?section=assets&asset_history_page=2&asset_history_cursor=rev_Bearer%20asset-cursor-canary',
  );
  await expect(page).not.toHaveURL(/asset_history_(?:page|cursor)=/);
  await expect(page.getByText('真实客户品牌', { exact: true })).toBeVisible();
  await expect(page.getByText('真实客户竞品', { exact: true })).toBeVisible();
  const assetHistory = page.getByRole('heading', { name: '客户资产确认历史' }).locator('..');
  await expect(assetHistory.getByText(/v2 · 当前确认品牌/)).toBeVisible();
  await assetHistory.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/asset_history_page=2/);
  await expect(page).toHaveURL(/asset_history_cursor=rev_2/);
  await expect(assetHistory.getByText(/v1 · 历史确认品牌/)).toBeVisible();
  await expect(page.getByLabel('品牌名称')).toHaveValue('');
  await page.goBack();
  await expect(page).not.toHaveURL(/asset_history_(?:page|cursor)=/);
  await page.getByLabel('品牌名称').fill('确认品牌');
  await page.getByLabel('官方 HTTPS 网站').fill('https://confirmed.example');
  await page.getByLabel('产品或服务').fill('确认产品');
  await page.getByLabel('客户指定竞品').fill('确认竞品');
  await page.getByLabel('禁止使用的表述').fill('未经证实的行业第一');
  await page.getByRole('checkbox', { name: /我确认品牌、产品、竞品与禁止表述真实/ }).check();
  await page.getByRole('button', { name: '登记资产' }).click();
  await expect(page.getByText('最新客户确认 v3')).toBeVisible();
  await expectSafePageScreenshot(page, 'customer-live-assets.png', {
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '问题目标' }).click();
  await expect(page.getByText('真实目录中的客户关注问题')).toBeVisible();
  await expect(page.getByText('目标 80.0% · active')).toBeVisible();
  await expectSafePageScreenshot(page, 'customer-live-questions.png', {
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '品牌可见度' }).click();
  await expect(
    page.getByRole('heading', { name: '真实客户品牌 · 品牌可见度与模型表现' }),
  ).toBeVisible();
  await expect(
    page.locator('.geo-kpi-card').filter({ hasText: '品牌提及率' }).getByText('75.0%'),
  ).toBeVisible();
  await expect(
    page.getByRole('img', { name: '品牌提及率、Top3 率和引用覆盖率趋势' }),
  ).toBeVisible();
  await expect(page.getByLabel('模型表现数据表')).toContainText('doubao');
  await expect(page.getByLabel('地区表现数据表')).toContainText('east');
  await expect(page.getByLabel('回答模式表现数据表')).toContainText('deep');
  await expect(page.getByText('真实客户合同问题', { exact: true })).toBeVisible();
  await expectSafePageScreenshot(page, 'customer-live-monitoring.png', {
    fullPage: true,
    animations: 'disabled',
  });
  await page.goto(
    '/platform/customer/?section=evidence&evidence_page=2&evidence_cursor=evd_Bearer%20evidence-cursor-canary',
  );
  await expect(page).not.toHaveURL(/evidence_(?:page|cursor)=/);
  await expect(page.getByRole('heading', { name: '真实合同问题' })).toBeVisible();
  await expect(page.getByText('真实合同回答，包含可追溯引用。')).toBeVisible();
  await expect(
    page.getByText('运行 run_customer_live_safe · 冻结配置 cfv_customer_live_safe'),
  ).toBeVisible();
  await page.getByRole('button', { name: '打开证据中心' }).click();
  const evidenceDialog = page.getByRole('dialog', { name: '证据与历史差异' });
  await expect(
    evidenceDialog.getByRole('cell', { name: 'evd_live_safe', exact: true }),
  ).toBeVisible();
  await expect(evidenceDialog.getByText('真实独立来源')).toBeVisible();
  await expect(evidenceDialog.getByText('source.example')).toBeVisible();
  await expect(evidenceDialog.getByText('1 个锚点', { exact: false })).toBeVisible();
  await expect(evidenceDialog.getByText('87.5%')).toBeVisible();
  await expect(page.getByText('Bearer relation-cited-text-canary')).toHaveCount(0);
  await expect(page.getByText('Cookie=relation-object-key-canary')).toHaveCount(0);
  await expect(page.getByText('Bearer relation-diff-canary')).toHaveCount(0);
  await expectSafePageScreenshot(page, 'customer-live-evidence.png', {
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '关闭证据弹窗' }).click();
  await synchronouslyActivateTwice(page.getByRole('button', { name: '生成证据包' }));
  await expect(page.getByText('真实证据包已生成并冻结清单')).toBeVisible();
  await page.goto(
    '/platform/customer/?section=reports&report_page=2&report_cursor=rpt_Bearer%20customer-report-cursor-canary',
  );
  await expect(page).not.toHaveURL(/report_(?:page|cursor)=/);
  await expect(page.getByRole('heading', { name: '真实客户报告' })).toBeVisible();
  await expect(page.getByText('真实 reports API')).toBeVisible();
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/report_page=2/);
  await expect(page).toHaveURL(/report_cursor=rpt_customer_safe/);
  await expect(page.getByRole('heading', { name: '第二页真实客户报告' })).toBeVisible();
  await page.goBack();
  await expect(page).not.toHaveURL(/report_(?:page|cursor)=/);
  await expect(page.getByRole('heading', { name: '真实客户报告' })).toBeVisible();
  await page.getByRole('button', { name: '在线预览' }).click();
  const htmlPreview = page.getByRole('article', { name: '客户报告在线预览' });
  await expect(htmlPreview).toBeVisible();
  await expect(page.getByText('HTML 完整性与活动内容已校验')).toBeVisible();
  await expect(htmlPreview.getByRole('heading', { name: '执行摘要', level: 4 })).toBeVisible();
  await expect(htmlPreview.getByRole('listitem')).toHaveCount(2);
  await expect(htmlPreview.getByRole('table', { name: '关键指标' })).toBeVisible();
  const sourceLink = htmlPreview.getByRole('link', { name: '查看公开来源' });
  await expect(sourceLink).toHaveAttribute('href', 'https://source.example/report');
  await expect(sourceLink).toHaveAttribute('rel', 'noopener noreferrer');
  await expectSafeLocatorScreenshot(page, htmlPreview, 'customer-live-html-report-preview.png', {
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '关闭在线报告预览' }).click();
  await page.getByRole('button', { name: '打开 PDF' }).click();
  await expect(page.getByRole('dialog', { name: '真实客户报告' })).toBeVisible();
  await page.getByRole('button', { name: '关闭在线报告预览' }).click();
  const reportDownloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 PDF' }).click();
  const reportDownload = await reportDownloadPromise;
  expect(reportDownload.suggestedFilename()).toBe('rpt_customer_safe-rptv_customer_safe.pdf');
  expect(await reportDownload.failure()).toBeNull();
  await expect(page.getByText('报告制品完整性校验通过并已下载')).toBeAttached();
  await expect.poll(() => reportArtifactRequests).toBe(3);
  await expect(page.getByText('v1 · published · 当前版本')).toBeVisible();
  await synchronouslyActivateTwice(page.getByRole('button', { name: '确认收到此报告' }));
  await expect(page.getByText('确认事件已由真实 delivery API 写入审计事实')).toBeVisible();
  await page.getByRole('textbox', { name: '问题', exact: true }).fill('请解释真实报告中的冻结口径');
  await synchronouslyActivateTwice(page.getByRole('button', { name: '提交问题' }));
  const reportQuestionPanel = page.getByRole('heading', { name: '向报告提问' }).locator('..');
  await expect(reportQuestionPanel.getByText('问题已写入真实报告版本评论')).toHaveCount(0);
  await expect(reportQuestionPanel.getByRole('button', { name: '重试此区域' })).toBeVisible();
  expect(reportQuestionAuthorityReads).toBe(1);
  expect(reportQuestionBodies).toHaveLength(1);
  await reportQuestionPanel.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('问题已写入真实报告版本评论')).toBeVisible();
  expect(reportQuestionAuthorityReads).toBe(2);
  expect(reportQuestionBodies).toHaveLength(1);
  await expectSafePageScreenshot(page, 'customer-live-reports.png', {
    fullPage: true,
    animations: 'disabled',
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  await page.getByRole('textbox', { name: '问题', exact: true }).fill('请解释导航后的回执隔离边界');
  await synchronouslyActivateTwice(page.getByRole('button', { name: '提交问题' }));
  await expect.poll(() => reportQuestionBodies).toHaveLength(2);
  await page.getByRole('button', { name: '前往监测导出' }).click();
  await expect(page).toHaveURL(/section=monitoring/);
  await expect(page.getByRole('heading', { name: '模型表现', exact: true })).toBeVisible();
  releaseDelayedReportQuestion?.();
  await page.waitForTimeout(250);
  expect(reportQuestionAuthorityReads).toBe(2);
  await expect(page.getByText('问题已写入真实报告版本评论')).toHaveCount(0);

  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /analytics-(?:canary|breakdown-canary)|analytics-delta-(?:canary|root-canary)|analytics-competitor-canary|answers-canary|answer-provenance-canary|evidence-canary|export-canary|package-canary|catalog-(?:brand|competitor|query|goal)-canary|customer-report-(?:detail|cursor)-canary|delivery-(?:comment|extension|recipient|confirm-response)-canary|profile-field-canary|proxy-password|SESSION=|Bearer |318294|429155|731904|824911|\/secret\/profile/i,
  );
  expect(exportBodies).toHaveLength(0);
  expect(packageBodies).toHaveLength(1);
  expect(packageBodies[0]).toMatchObject({
    evidence_pub_ids: ['evd_live_safe'],
    public: false,
    expires_at: null,
  });
  expect(reportQuestionBodies).toEqual([
    { body: '请解释真实报告中的冻结口径', parent_pub_id: null },
    { body: '请解释导航后的回执隔离边界', parent_pub_id: null },
  ]);
  expect(deliveryConfirmBodies).toEqual([{ confirmation_comment: '客户确认已收到此报告版本' }]);
  expect(profileBodies).toEqual([
    {
      company_name: '真实客户企业',
      contact_role: '品牌负责人',
      audience: '需要可验证企业知识服务的采购团队',
      public_statement: '真实客户企业提供可公开核验的知识服务。',
      truth_confirmed: true,
    },
  ]);
  expect(assetConfirmationBodies).toEqual([
    {
      brand_name: '确认品牌',
      website: 'https://confirmed.example',
      product_name: '确认产品',
      competitor_name: '确认竞品',
      prohibited_claim: '未经证实的行业第一',
      truth_confirmed: true,
    },
  ]);
  expect(profileCursors.filter(Boolean)).toEqual(['2']);
  expect(assetConfirmationCursors.filter(Boolean)).toEqual(['2']);
  expect(evidenceCursors).toEqual([null]);
});

test('customer product 404 fails closed without revealing whether analytics exist', async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_forbidden');
    localStorage.setItem('geo.session.actor', 'customer-forbidden');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await installSyntheticHttpResponses(page, [
    {
      id: 'customer-overview-forbidden',
      path: '/api/v2/analytics/overview',
      status: 404,
      body: {
        error: {
          code: 'not_found',
          message: 'Cookie=forbidden-customer-canary',
          request_id: 'req_safe',
        },
      },
    },
    {
      id: 'customer-delta-forbidden',
      path: '/api/v2/analytics/delta',
      status: 404,
    },
    {
      id: 'customer-competitors-forbidden',
      path: '/api/v2/analytics/competitors',
      status: 404,
    },
    {
      id: 'customer-breakdown-forbidden',
      path: '/api/v2/analytics/breakdown',
      status: 404,
    },
    {
      id: 'customer-answers-forbidden',
      path: '/api/v2/analytics/answers',
      status: 404,
    },
    {
      id: 'customer-dashboard-forbidden',
      path: '/api/v2/customer-dashboard/projects/prj_customer_hidden',
      status: 404,
      body: {
        error: {
          code: 'not_found',
          message: 'customer-dashboard-forbidden-canary',
          request_id: 'req_dashboard_safe',
        },
      },
    },
    {
      id: 'customer-metric-catalog-forbidden',
      path: '/api/v2/customer-dashboard/metrics/catalog',
      status: 404,
    },
  ]);
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_customer_forbidden',
        user_pub_id: 'usr_customer_forbidden',
        role: 'customer',
        permissions: ['project:read'],
      }),
    }),
  );
  await page.route('**/api/v2/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        service: 'geo-platform-v2',
        version: 'contract-v2',
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
            pub_id: 'prj_customer_hidden',
            tenant_pub_id: 'tnt_customer_forbidden',
            name: '不可推断项目',
            state: 'active',
            created_at: '2026-07-25T00:00:00Z',
            updated_at: '2026-07-25T00:00:00Z',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );
  await page.goto('/platform/customer/');
  await page.getByRole('button', { name: '品牌可见度' }).click();
  await expect(page.getByText('无权查看')).toBeVisible();
  await expect(page.getByText('Cookie=forbidden-customer-canary')).toHaveCount(0);
  await expect(page.getByText('customer-dashboard-forbidden-canary')).toHaveCount(0);
  expect(await syntheticHttpResponseCount(page, 'customer-dashboard-forbidden')).toBeGreaterThan(0);
  expect(await syntheticHttpResponseCount(page, 'customer-answers-forbidden')).toBe(0);
});

test('validated tenant admin manages masked customer members through generated identity paths', async ({
  page,
}) => {
  const writes: Array<{ url: string; body: unknown; headers: Record<string, string> }> = [];
  let memberAccepted = false;
  let memberVisible = false;
  let memberRevoked = false;
  let inviteReconciliationReads = 0;
  let oidcActive = false;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_member_live');
    localStorage.setItem('geo.session.actor', 'tenant-admin-live');
    localStorage.setItem('geo.session.role', 'admin');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_member_live',
        user_pub_id: 'usr_admin_live',
        role: 'admin',
        permissions: ['*'],
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
            pub_id: 'prj_member_live',
            tenant_pub_id: 'tnt_member_live',
            name: '成员联调项目',
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
  await page.route('**/api/v2/identity/oidc-bindings', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          user_pub_id: 'usr_member_not_in_safe_projection',
          active: true,
          created_at: '2026-07-25T00:00:00Z',
          revoked_at: null,
          token: 'Bearer oidc-binding-extension-canary',
        },
        ...(oidcActive
          ? [
              {
                user_pub_id: 'usr_new_live',
                active: true,
                created_at: '2026-07-25T00:05:00Z',
                revoked_at: null,
              },
            ]
          : []),
      ]),
    }),
  );
  await page.route('**/api/v2/identity/members**', async (route) => {
    const request = route.request();
    if (request.method() === 'GET') {
      if (memberAccepted && !memberVisible && !memberRevoked) {
        inviteReconciliationReads += 1;
        if (inviteReconciliationReads >= 2) memberVisible = true;
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            pub_id: 'mbr_admin_live',
            user_pub_id: 'usr_admin_live',
            subject: 'admin@example.test',
            display_name: '租户管理员',
            role: 'admin',
            state: 'active',
            service_account: false,
            cookie: 'SESSION=member-list-canary',
            profile_path: '/secret/profile/member-list-canary',
          },
          {
            pub_id: 'mbr_worker_hidden',
            user_pub_id: 'usr_worker_hidden',
            subject: 'service:worker',
            display_name: '不应展示的服务账号',
            role: 'worker',
            state: 'active',
            service_account: true,
            token: 'Bearer member-worker-canary',
          },
          ...(memberVisible || memberRevoked
            ? [
                {
                  pub_id: 'mbr_new_live',
                  user_pub_id: 'usr_new_live',
                  subject: 'new.member@example.test',
                  display_name: '新成员',
                  role: 'customer',
                  state: memberRevoked ? 'revoked' : 'active',
                  service_account: false,
                },
              ]
            : []),
        ]),
      });
    }
    writes.push({
      url: request.url(),
      body: request.postData() ? request.postDataJSON() : null,
      headers: request.headers(),
    });
    if (request.url().endsWith('/oidc-binding')) {
      oidcActive = request.method() === 'PUT';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user_pub_id: 'usr_new_live',
          active: request.method() === 'PUT',
          created_at: '2026-07-25T00:00:00Z',
          revoked_at: request.method() === 'DELETE' ? '2026-07-25T00:05:00Z' : null,
        }),
      });
    }
    if (request.url().endsWith('/revoke')) {
      memberRevoked = true;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          pub_id: 'mbr_new_live',
          user_pub_id: 'usr_new_live',
          subject: 'new.member@example.test',
          display_name: '新成员',
          role: 'customer',
          state: 'revoked',
          service_account: false,
          otp: '123456',
        }),
      });
    }
    memberAccepted = true;
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        pub_id: 'mbr_new_live',
        user_pub_id: 'usr_new_live',
        subject: 'new.member@example.test',
        display_name: '新成员',
        role: 'customer',
        state: 'active',
        service_account: false,
        authorization: 'Bearer member-create-canary',
      }),
    });
  });

  await page.goto('/platform/customer/?section=members');
  await expect(page.getByText('租户管理员', { exact: true })).toBeVisible();
  await expect(page.getByText('a***@example.test')).toBeVisible();
  await expect(page.getByText('不应展示的服务账号')).toHaveCount(0);
  await page.getByLabel('姓名').fill('新成员');
  await page.getByLabel('工作邮箱').fill('new.member@example.test');
  await synchronouslyActivateTwice(page.getByRole('button', { name: '发送邀请' }));
  const reconciliationFailure = page.getByRole('alert').filter({ hasText: '加载失败' });
  await expect(reconciliationFailure).toBeVisible();
  await expect(page.getByText('新成员 已加入租户，联系标识只保留掩码')).toHaveCount(0);
  expect(inviteReconciliationReads).toBe(1);
  expect(writes).toHaveLength(1);
  await reconciliationFailure.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('新成员 已加入租户，联系标识只保留掩码')).toBeVisible();
  expect(inviteReconciliationReads).toBe(2);
  await expect(page.getByText('n***@example.test')).toBeVisible();
  await page.getByRole('button', { name: '管理 新成员' }).click();
  await expect(page.getByRole('button', { name: '提升为客户管理员' })).toBeDisabled();
  await page.getByLabel('IdP opaque subject').fill('opaque-idp-member-safe');
  await synchronouslyActivateTwice(page.getByRole('button', { name: '建立 OIDC 绑定' }));
  await expect(page.getByText('新成员 的 OIDC 标识已哈希绑定；原始 subject 未保留')).toBeVisible();
  await expect(page.getByText('opaque-idp-member-safe')).toHaveCount(0);
  await synchronouslyActivateTwice(page.getByRole('button', { name: '撤销 OIDC 绑定' }));
  await expect(page.getByText('新成员 的 OIDC 绑定已撤销并记录审计')).toBeVisible();
  await synchronouslyActivateTwice(page.getByRole('button', { name: '移出项目' }));
  await expect(page.getByText('新成员 已移出项目，历史审计仍保留')).toBeVisible();
  await expect(page.getByText('n***@example.test')).toHaveCount(0);

  expect(writes).toHaveLength(4);
  expect(writes[0]?.body).toEqual({
    subject: 'new.member@example.test',
    display_name: '新成员',
    role: 'customer',
  });
  expect(writes[1]).toMatchObject({
    body: { subject: 'opaque-idp-member-safe' },
  });
  expect(writes[1]?.url).toMatch(/\/identity\/members\/usr_new_live\/oidc-binding$/);
  expect(writes[2]?.url).toMatch(/\/identity\/members\/usr_new_live\/oidc-binding$/);
  expect(writes[3]?.url).toMatch(/\/identity\/members\/mbr_new_live\/revoke$/);
  for (const write of writes) {
    expect(write.headers).toMatchObject({
      'x-tenant-id': 'tnt_member_live',
      'x-actor-id': 'tenant-admin-live',
      'x-actor-role': 'admin',
    });
  }
  const surface = `${await page.locator('body').innerText()} ${await page.evaluate(() =>
    JSON.stringify({ localStorage, sessionStorage, href: location.href }),
  )}`;
  for (const canary of [
    'member-list-canary',
    'member-worker-canary',
    'member-create-canary',
    'oidc-binding-extension-canary',
    '/secret/profile',
    '123456',
  ]) {
    expect(surface).not.toContain(canary);
  }
});

test('a same-subject but input-mismatched member receipt fails locally without leakage', async ({
  page,
}) => {
  let memberWrites = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_member_receipt');
    localStorage.setItem('geo.session.actor', 'tenant-admin-receipt');
    localStorage.setItem('geo.session.role', 'admin');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_member_receipt',
        user_pub_id: 'usr_member_receipt_admin',
        role: 'admin',
        permissions: ['*'],
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
            pub_id: 'prj_member_receipt',
            tenant_pub_id: 'tnt_member_receipt',
            name: '成员回执校验项目',
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
      body: JSON.stringify({ status: 'ok' }),
    }),
  );
  await page.route('**/api/v2/identity/oidc-bindings', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '[]',
    }),
  );
  await page.route('**/api/v2/identity/members**', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            pub_id: 'mbr_member_receipt_admin',
            user_pub_id: 'usr_member_receipt_admin',
            subject: 'admin@example.test',
            display_name: '租户管理员',
            role: 'admin',
            state: 'active',
            service_account: false,
          },
        ]),
      });
    }
    memberWrites += 1;
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        pub_id: 'mbr_member_receipt_wrong',
        user_pub_id: 'usr_member_receipt_wrong',
        subject: 'new.member@example.test',
        display_name: '错误成员',
        role: 'analyst',
        state: 'active',
        service_account: true,
        token: 'Bearer member-input-mismatch-canary',
        profile_path: '/secret/profile/member-input-mismatch-canary',
      }),
    });
  });

  await page.goto('/platform/customer/?section=members');
  await expect(page.getByText('租户管理员', { exact: true })).toBeVisible();
  await page.getByLabel('姓名').fill('新成员');
  await page.getByLabel('工作邮箱').fill('new.member@example.test');
  await page.getByRole('button', { name: '发送邀请' }).click();

  await expect(page.getByRole('alert')).toContainText('加载失败');
  await expect(page.getByText(/已加入租户|错误成员/)).toHaveCount(0);
  expect(memberWrites).toBe(1);
  await expectAccessible(page);
  const surface = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      localStorage,
      sessionStorage,
      href: location.href,
    }),
  );
  expect(surface).not.toMatch(/member-input-mismatch-canary|\/secret\/profile|Bearer|错误成员/);
});

test('oversized or unsafe identity governance lists stay explicit and governance-write locked', async ({
  page,
}) => {
  let governanceWrites = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_member_projection');
    localStorage.setItem('geo.session.actor', 'tenant-admin-projection');
    localStorage.setItem('geo.session.role', 'admin');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_member_projection',
        user_pub_id: 'usr_member_projection_admin',
        role: 'admin',
        permissions: ['*'],
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
            pub_id: 'prj_member_projection',
            tenant_pub_id: 'tnt_member_projection',
            name: '成员安全投影项目',
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
  await page.route('**/api/v2/identity/oidc-bindings', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        Array.from({ length: 102 }, (_, index) => ({
          user_pub_id:
            index === 1
              ? 'usr_member_boundary_0'
              : index === 2
                ? 'Cookie=oidc-visible-row-canary'
                : `usr_member_boundary_${index}`,
          active: true,
          created_at: '2026-07-25T00:00:00Z',
          revoked_at: null,
          profile_path: '/secret/profile/oidc-list-extension-canary',
        })),
      ),
    }),
  );
  await page.route('**/api/v2/identity/members**', (route) => {
    if (route.request().method() !== 'GET') {
      governanceWrites += 1;
      return route.fulfill({ status: 500, body: '{}' });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        Array.from({ length: 102 }, (_, index) => ({
          pub_id: index === 1 ? 'mbr_boundary_0' : `mbr_boundary_${index}`,
          user_pub_id: `usr_member_boundary_${index}`,
          subject: `member${index}@example.test`,
          display_name: index === 2 ? 'Bearer member-visible-row-canary' : `安全成员 ${index}`,
          role: index === 0 ? 'admin' : 'customer',
          state: 'active',
          service_account: false,
          cookie: 'SESSION=member-list-extension-canary',
        })),
      ),
    });
  });

  await page.goto('/platform/customer/?section=members');
  await expect(page.getByRole('alert')).toContainText('成员安全投影不完整');
  await expect(
    page
      .getByRole('status')
      .filter({ hasText: '成员合同安全投影：服务返回 102 条，浏览器安全视图展示 98 条' }),
  ).toBeVisible();
  await expect(
    page
      .getByRole('status')
      .filter({ hasText: 'OIDC 绑定安全投影：服务返回 102 条，浏览器安全视图展示 98 条' }),
  ).toBeVisible();
  await expect(page.locator('.member-list article')).toHaveCount(98);
  await expect(page.getByRole('button', { name: '发送邀请' })).toBeDisabled();

  await page.getByRole('button', { name: '管理 安全成员 0' }).click();
  await expect(page.getByRole('button', { name: '改为客户成员' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '移出项目' })).toBeDisabled();
  await expect(page.getByLabel('IdP opaque subject')).toBeDisabled();
  await expect(page.getByRole('button', { name: '撤销 OIDC 绑定' })).toBeDisabled();

  await expectAccessible(page);
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    )
    .toBe(true);
  const surface = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      localStorage,
      sessionStorage,
      href: location.href,
    }),
  );
  for (const canary of [
    'member-visible-row-canary',
    'oidc-visible-row-canary',
    'member-list-extension-canary',
    'oidc-list-extension-canary',
    '/secret/profile',
  ]) {
    expect(surface).not.toContain(canary);
  }
  expect(governanceWrites).toBe(0);
});

test('customer role cannot infer tenant member existence', async ({ page }) => {
  let memberWrites = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_member_forbidden');
    localStorage.setItem('geo.session.actor', 'customer-member-forbidden');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_member_forbidden',
        user_pub_id: 'usr_member_forbidden',
        role: 'customer',
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
            pub_id: 'prj_member_forbidden',
            tenant_pub_id: 'tnt_member_forbidden',
            name: '成员不可推断项目',
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
  await page.route('**/api/v2/identity/oidc-bindings', (route) =>
    route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ detail: { code: 'admin_required' } }),
    }),
  );
  await page.route('**/api/v2/identity/members**', (route) => {
    if (route.request().method() !== 'GET') memberWrites += 1;
    return route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: { code: 'admin_required', token: 'Bearer member-forbidden-canary' },
      }),
    });
  });

  await page.goto('/platform/customer/?section=members');
  await expect(page.getByText('无权查看')).toBeVisible();
  await expect(page.getByLabel('工作邮箱')).toHaveCount(0);
  await expect(page.getByText('Bearer member-forbidden-canary')).toHaveCount(0);
  expect(memberWrites).toBe(0);
});

test('live answer cursor pagination preserves URL history and rejects secret-shaped cursors', async ({
  page,
}) => {
  const requestedCursors: Array<string | null> = [];
  let inconsistentPageMeta = false;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_answer_cursor_live');
    localStorage.setItem('geo.session.actor', 'customer-answer-cursor-live');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_answer_cursor_live',
        user_pub_id: 'usr_answer_cursor_live',
        role: 'customer',
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
            pub_id: 'prj_answer_cursor_live',
            tenant_pub_id: 'tnt_answer_cursor_live',
            name: '回答分页联调项目',
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
  await page.route('**/api/v2/evidence/assets**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
    }),
  );
  await page.route('**/api/v2/analytics/answers**', (route) => {
    const cursor = new URL(route.request().url()).searchParams.get('cursor');
    requestedCursors.push(cursor);
    const secondPage = cursor === 'ans_cursor_safe_01';
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            pub_id: secondPage ? 'ans_cursor_page_02' : 'ans_cursor_page_01',
            project_pub_id: 'prj_answer_cursor_live',
            run_pub_id: null,
            config_version_pub_id: null,
            query_pub_id: secondPage ? 'qry_cursor_page_02' : 'qry_cursor_page_01',
            query_text: secondPage ? '第二页真实问题' : '第一页真实问题',
            response_text: secondPage ? '第二页真实回答' : '第一页真实回答',
            model: 'doubao',
            region: '上海',
            mode: 'deep',
            eligible: true,
            degraded: false,
            capture_time: '2026-07-25T01:00:00Z',
            mentioned: true,
            rank: 1,
            sentiment: 'positive',
            recommendation_state: null,
            citation_count: 1,
          },
        ],
        page: {
          next_cursor: secondPage ? null : 'ans_cursor_safe_01',
          has_more: inconsistentPageMeta ? false : !secondPage,
          cookie: 'SESSION=cursor-page-canary',
        },
        token: 'Bearer cursor-response-canary',
      }),
    });
  });

  await page.goto(
    '/platform/customer/?section=evidence&answer_page=2&answer_cursor=ans_Bearer%20cursor-request-canary',
  );
  await expect(page).toHaveURL(/section=evidence/);
  await expect(page).not.toHaveURL(/answer_(?:page|cursor)=/);
  await expect(page.getByRole('heading', { name: '第一页真实问题' })).toBeVisible();
  expect(requestedCursors).toEqual([null]);

  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/answer_page=2/);
  await expect(page).toHaveURL(/answer_cursor=ans_cursor_safe_01/);
  await expect(page.getByRole('heading', { name: '第二页真实问题' })).toBeVisible();
  expect(requestedCursors).toEqual([null, 'ans_cursor_safe_01']);

  await page.goBack();
  await expect(page).not.toHaveURL(/answer_(?:page|cursor)=/);
  await expect(page.getByRole('heading', { name: '第一页真实问题' })).toBeVisible();
  expect(requestedCursors).toEqual([null, 'ans_cursor_safe_01', null]);

  inconsistentPageMeta = true;
  await page.reload();
  await expect(page.getByRole('heading', { name: '第一页真实问题' })).toBeVisible();
  await expect(page.getByRole('button', { name: '下一页' })).toBeDisabled();
  expect(requestedCursors).toEqual([null, 'ans_cursor_safe_01', null, null]);

  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /cursor-request-canary|cursor-response-canary|cursor-page-canary|SESSION=|Bearer /i,
  );
});
