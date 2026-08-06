import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';
import { expectSafePageScreenshot } from './screenshot-safety';
import { installSyntheticHttpResponses, syntheticHttpResponseCount } from './synthetic-http';

test('validated reviewer live writes stay single under synchronous duplicate activation', async ({
  page,
}) => {
  const writes: { url: string; body: unknown }[] = [];
  const packageBodies: unknown[] = [];
  let verdictAccepted = false;
  let postWriteDetailReads = 0;
  let releaseVerdictProjection = () => {};
  const delayedVerdictProjection = new Promise<void>((resolve) => {
    releaseVerdictProjection = resolve;
  });
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_live');
    localStorage.setItem('geo.session.actor', 'reviewer-intelligence-live');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_live',
        user_pub_id: 'usr_intelligence_live',
        role: 'reviewer',
        permissions: ['intelligence:read', 'intelligence:review'],
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
            pub_id: 'prj_intelligence_live',
            tenant_pub_id: 'tnt_intelligence_live',
            name: '真实调查联调项目',
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
  await page.route('**/api/v2/intelligence/investigations**', async (route) => {
    const request = route.request();
    const requestUrl = new URL(request.url());
    const path = requestUrl.pathname;
    if (request.method() === 'GET' && path.endsWith('/investigations')) {
      const secondPage = requestUrl.searchParams.get('cursor') === 'inv_cursor_safe_02';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: secondPage ? 'inv_live_page_02' : 'inv_live_safe',
              title: secondPage ? '第二页真实调查案件' : '真实调查案件',
              state: 'review',
              access_class: 'customer_private',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
              claim_count: 2,
              source_cluster_count: 1,
              probability: '0.73',
              latest_verdict: null,
              cookie: 'SESSION=intelligence-list-canary',
            },
          ],
          page: {
            next_cursor: secondPage ? null : 'inv_cursor_safe_02',
            has_more: !secondPage,
            token: 'Bearer intelligence-page-canary',
          },
        }),
      });
      return;
    }
    if (request.method() === 'GET' && path.endsWith('/page-history')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            content_pub_id: 'cnt_live_safe',
            version_pub_id: 'cntv_live_safe_01',
            canonical_url: 'https://source.example/history',
            title: '真实历史页面',
            version_number: 1,
            body_hash: 'a'.repeat(64),
            evidence_pub_id: 'evd_live_safe_01',
            captured_at: '2026-07-02T00:00:00Z',
            published_at: null,
            snapshot_pub_id: 'snap_live_safe_01',
            snapshot_number: 1,
            normalized_text_hash: 'a'.repeat(64),
            perceptual_hash: null,
            cookie: 'SESSION=intelligence-history-canary',
          },
          {
            content_pub_id: 'cnt_live_safe',
            version_pub_id: 'cntv_live_safe_02',
            canonical_url: 'https://source.example/history',
            title: '真实历史页面（修订）',
            version_number: 2,
            body_hash: 'b'.repeat(64),
            evidence_pub_id: 'evd_live_safe_02',
            captured_at: '2026-07-21T00:00:00Z',
            published_at: null,
            snapshot_pub_id: 'snap_live_safe_02',
            snapshot_number: 2,
            normalized_text_hash: 'b'.repeat(64),
            perceptual_hash: null,
            profile_path: '/secret/profile/intelligence-history-canary',
          },
          {
            content_pub_id: 'cnt_live_trailing',
            version_pub_id: 'cntv_live_trailing_01',
            canonical_url: 'https://other-source.example/history',
            title: '另一真实历史页面',
            version_number: 1,
            body_hash: 'c'.repeat(64),
            evidence_pub_id: 'evd_live_trailing_01',
            captured_at: '2026-07-10T00:00:00Z',
            published_at: null,
            snapshot_pub_id: 'snap_live_trailing_01',
            snapshot_number: 1,
            normalized_text_hash: 'c'.repeat(64),
            perceptual_hash: null,
          },
        ]),
      });
      return;
    }
    if (request.method() === 'GET' && path.endsWith('/visual-diffs')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            pub_id: 'diff_live_safe',
            content_pub_id: 'cnt_live_safe',
            before_version_pub_id: 'cntv_live_safe_01',
            after_version_pub_id: 'cntv_live_safe_02',
            before_evidence_pub_id: 'evd_live_safe_01',
            after_evidence_pub_id: 'evd_live_safe_02',
            text_diff: {
              before_hash: 'a'.repeat(64),
              after_hash: 'b'.repeat(64),
              unified: 'Bearer history-diff-canary',
            },
            similarity: '0.75',
            visual_diff_available: false,
            created_at: '2026-07-21T00:00:00Z',
          },
        ]),
      });
      return;
    }
    if (request.method() === 'GET') {
      const secondPage = path.includes('inv_live_page_02');
      if (verdictAccepted && !secondPage) {
        postWriteDetailReads += 1;
        await delayedVerdictProjection;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          pub_id: secondPage ? 'inv_live_page_02' : 'inv_live_safe',
          scores: [
            {
              pub_id: secondPage ? 'score_live_page_02' : 'score_live_safe',
              probability: 0.73,
              evidence_sufficiency: 0.82,
              uncertainty: 0.19,
              rule_version: 'geo-rule-v2',
              explanation: {
                independence: '两个独立来源簇共同支持该 Claim',
                uncertainty: '仍存在 19% 不确定性，必须由人工裁决',
              },
              created_at: '2026-07-21T00:00:00Z',
              cookie: 'SESSION=intelligence-detail-canary',
            },
          ],
          claims: [
            {
              pub_id: 'clm_live_safe_a',
              normalized_text: '真实原子 Claim A',
              verifiability: 'verifiable',
            },
            {
              pub_id: 'clm_live_safe_b',
              normalized_text: '真实原子 Claim B',
              verifiability: 'verifiable',
            },
          ],
          evidence_matrix: [
            {
              pub_id: 'ce_live_safe_a',
              claim_pub_id: 'clm_live_safe_a',
              evidence_pub_id: 'evd_live_safe',
              relation: 'supports',
              source_cluster: 'relation-cluster-support',
              independence_weight: 0.9,
              rationale: '真实独立来源',
            },
            {
              pub_id: 'ce_live_safe_b',
              claim_pub_id: 'clm_live_safe_b',
              evidence_pub_id: 'evd_live_safe',
              relation: 'contradicts',
              source_cluster: 'relation-cluster-contradiction',
              independence_weight: 0.7,
              rationale: '同一证据反驳第二个 Claim',
            },
          ],
          source_independence: [
            {
              pub_id: 'srca_live_safe',
              source_pub_id: 'evd_live_safe',
              cluster_id: 'cluster-live-assessed',
              independence_weight: 0.9,
              circular_citation_risk: 0.1,
            },
          ],
          graph: [
            {
              from_pub_id: 'evd_live_safe',
              to_pub_id: 'clm_live_safe_a',
              relation: 'supports',
              weight: 0.9,
              evidence_pub_id: 'evd_live_safe',
            },
            {
              from_pub_id: 'evd_live_safe',
              to_pub_id: 'clm_live_safe_a',
              relation: 'mentions',
              weight: 0.6,
              evidence_pub_id: null,
            },
          ],
          appeals: [],
          verdicts:
            verdictAccepted && !secondPage && postWriteDetailReads >= 2
              ? [
                  {
                    pub_id: 'vrd_live_safe',
                    verdict: 'likely',
                    reviewer_pub_id: 'usr_intelligence_live',
                    rationale: '人工复核确认当前证据支持高风险表述。',
                    supersedes_pub_id: null,
                    created_at: '2026-07-25T02:00:00Z',
                  },
                ]
              : [],
          token: 'Bearer intelligence-detail-canary',
          profile_path: '/secret/profile/intelligence-detail-canary',
          otp: 824911,
        }),
      });
      return;
    }
    writes.push({ url: request.url(), body: request.postDataJSON() });
    if (path.endsWith('/verdicts')) verdictAccepted = true;
    const responseBody = path.endsWith('/appeals')
      ? { appeal_pub_id: 'apl_live_safe' }
      : path.endsWith('/resolve')
        ? { replacement_verdict_pub_id: null }
        : { verdict_pub_id: 'vrd_live_safe' };
    await route.fulfill({
      status: path.endsWith('/resolve') ? 200 : 201,
      contentType: 'application/json',
      body: JSON.stringify(responseBody),
    });
  });
  await page.route('**/api/v2/evidence/packages', async (route) => {
    const packageBody = route.request().postDataJSON() as { package_pub_id: string };
    packageBodies.push(packageBody);
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        package_pub_id: packageBody.package_pub_id,
        manifest_sha256: 'c'.repeat(64),
        state: 'ready',
        token: 'Bearer intelligence-package-canary',
      }),
    });
  });

  await page.goto(
    '/platform/intelligence/?case_page=2&case_cursor=inv_Bearer%20case-cursor-request-canary',
  );
  await expect(page).not.toHaveURL(/case_(?:page|cursor)=/);
  await expect(page.getByText('真实调查案件', { exact: true })).toBeVisible();
  await expect(page.getByText('Claim 数')).toBeVisible();
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/case_page=2/);
  await expect(page).toHaveURL(/case_cursor=inv_cursor_safe_02/);
  await expect(page.getByText('第二页真实调查案件', { exact: true })).toBeVisible();
  await page.goBack();
  await expect(page).not.toHaveURL(/case_(?:page|cursor)=/);
  await expect(page.getByText('真实调查案件', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Claim 矩阵' }).click();
  await expect(page.getByText('真实原子 Claim A')).toBeVisible();
  await expect(page.getByText('真实原子 Claim B')).toBeVisible();
  await page.getByRole('button', { name: '多源证据' }).click();
  await expect(page.getByText('真实独立来源')).toBeVisible();
  await expect(page.getByText('同一证据反驳第二个 Claim')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'evd_live_safe' })).toHaveCount(2);
  await page.getByLabel('筛选同源簇').selectOption('cluster-live-assessed');
  await expect(page.getByRole('heading', { name: 'evd_live_safe' })).toHaveCount(2);
  await expect(page.locator('option[value^="relation-cluster-"]')).toHaveCount(0);
  await page.getByRole('button', { name: '传播关系' }).click();
  await expect(page.getByRole('cell', { name: 'supports' })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'mentions' })).toBeVisible();
  await expect(page.getByRole('table', { name: '传播图节点与关系' }).getByRole('row')).toHaveCount(
    3,
  );
  await expectSafePageScreenshot(page, 'intelligence-live-graph.png', {
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '页面历史' }).click();
  await expect(page.getByRole('heading', { name: '真实历史页面（修订）' })).toBeVisible();
  await expect(page.getByText('75.0%')).toBeVisible();
  await expect(page.getByLabel('选择历史页面').locator('option')).toHaveCount(2);
  await page.getByLabel('选择历史页面').selectOption('cnt_live_trailing');
  await expect(page.getByRole('heading', { name: '另一真实历史页面' })).toBeVisible();
  await expect(page.getByText('75.0%')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '上一版本' })).toBeDisabled();
  await page.getByLabel('选择历史页面').selectOption('cnt_live_safe');
  await expect(page.getByRole('heading', { name: '真实历史页面（修订）' })).toBeVisible();
  await expect(page.getByText('75.0%')).toBeVisible();
  await expect(page.getByText(/Bearer history-diff-canary/)).toHaveCount(0);
  await expectAccessible(page);
  await expectSafePageScreenshot(page, 'intelligence-live-history.png', {
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: /裁决与申诉/ }).click();
  await expect(page.getByText('真实 intelligence API')).toBeVisible();
  await expect(page.getByText('0.73', { exact: true })).toBeVisible();
  await expect(page.getByText('两个独立来源簇共同支持该 Claim')).toBeVisible();
  const confirmVerdictButton = page.getByRole('button', { name: '确认高风险表述' });
  await confirmVerdictButton.evaluate((button) => {
    button.addEventListener('click', () => button.click(), { once: true });
  });
  await confirmVerdictButton.click();
  await expect(page.getByText('写入已接受，正在重新读取同一案件的权威治理投影。')).toBeVisible();
  await expect(confirmVerdictButton).toBeDisabled();
  await expect(page.getByText('真实人工裁决已记录')).toHaveCount(0);
  await expect.poll(() => postWriteDetailReads).toBe(1);
  releaseVerdictProjection();
  await expect(page.getByRole('button', { name: '重试此区域' })).toBeVisible();
  await expect(page.getByText('真实人工裁决已记录')).toHaveCount(0);
  expect(writes).toHaveLength(1);
  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('真实人工裁决已记录')).toBeVisible();
  await expect.poll(() => postWriteDetailReads).toBe(2);
  expect(writes).toHaveLength(1);
  await page.getByLabel('申诉理由').fill('补充新的独立来源并申请重新复核');
  await expect(page.getByRole('button', { name: '提交申诉' })).toBeDisabled();
  await expect(page.getByText('申诉由分析师提交，审核人不能代为发起。')).toBeVisible();
  await expectSafePageScreenshot(page, 'intelligence-live-verdict.png', {
    fullPage: true,
    animations: 'disabled',
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  await page.getByRole('button', { name: '证据包' }).click();
  await expect(page.getByText('案件 manifest 合同待补齐')).toBeVisible();
  await expect(
    page.getByText(/当前 OpenAPI 未把案件、裁决、申诉、规则解释或历史版本绑定/),
  ).toBeVisible();
  await expect(page.getByText('未由当前 package 合同绑定。')).toHaveCount(2);
  const packageButton = page.getByRole('button', { name: '生成证据对象包' });
  await packageButton.evaluate((button) => {
    button.addEventListener('click', () => button.click(), { once: true });
  });
  await packageButton.click();
  await expect(page.getByText('证据对象包已生成')).toBeVisible();
  await expect(page.getByText(/manifest SHA-256 cccccccccccc…/)).toBeVisible();
  const packageReceipt = page.getByText(/未声明为完整案件包/);
  await expect(packageReceipt).toBeVisible();
  await packageReceipt.scrollIntoViewIfNeeded();
  expect(
    await packageReceipt.evaluate((receipt) => {
      if (innerWidth > 620) return false;
      const navigation = document.querySelector('.sidebar');
      if (!(navigation instanceof HTMLElement)) return true;
      return receipt.getBoundingClientRect().bottom > navigation.getBoundingClientRect().top;
    }),
  ).toBe(false);
  await expect(page.getByText(/案件证据已通过真实 evidence package 合同冻结/)).toHaveCount(0);
  await expectSafePageScreenshot(page, 'intelligence-live-package.png', {
    fullPage: true,
    animations: 'disabled',
  });

  expect(writes).toHaveLength(1);
  expect(new URL(writes[0]!.url).pathname).toBe(
    '/api/v2/intelligence/investigations/inv_live_safe/verdicts',
  );
  expect(writes[0]?.body).toMatchObject({ verdict: 'likely' });
  expect(packageBodies).toHaveLength(1);
  expect(packageBodies[0]).toMatchObject({
    evidence_pub_ids: ['evd_live_safe'],
    public: false,
    expires_at: null,
  });
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /intelligence-detail-canary|intelligence-history-canary|intelligence-package-canary|case-cursor-request-canary|SESSION=|Bearer |824911|\/secret\/profile/i,
  );
  expect(JSON.stringify(writes)).not.toMatch(/Cookie|Bearer|otp|profile_path/i);
});

test('appeal and independent review reconcile by read-only retry without duplicate writes', async ({
  page,
}) => {
  let currentRole: 'analyst' | 'reviewer' = 'analyst';
  let currentUserPubId = 'usr_intelligence_analyst';
  let appealAccepted = false;
  let appealVisible = false;
  let resolutionAccepted = false;
  let resolutionVisible = false;
  let appealReconciliationReads = 0;
  let resolutionReconciliationReads = 0;
  let appealWrites = 0;
  let resolutionWrites = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_governance');
    localStorage.setItem('geo.session.actor', 'analyst-intelligence-governance');
    localStorage.setItem('geo.session.role', 'analyst');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_governance',
        user_pub_id: currentUserPubId,
        role: currentRole,
        permissions:
          currentRole === 'analyst'
            ? ['intelligence:read', 'intelligence:write']
            : ['intelligence:read', 'intelligence:review'],
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
            pub_id: 'prj_intelligence_governance',
            tenant_pub_id: 'tnt_intelligence_governance',
            name: '申诉复核联调项目',
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
  await page.route('**/api/v2/intelligence/investigations**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'GET' && path.endsWith('/investigations')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: 'inv_governance_safe',
              title: '独立申诉复核案件',
              state: 'review',
              access_class: 'customer_private',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
              claim_count: 1,
              source_cluster_count: 1,
              probability: '0.73',
              latest_verdict: 'likely',
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
      return;
    }
    if (
      request.method() === 'GET' &&
      (path.endsWith('/page-history') || path.endsWith('/visual-diffs'))
    ) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: '[]',
      });
      return;
    }
    if (request.method() === 'GET') {
      if (appealAccepted && !appealVisible) {
        appealReconciliationReads += 1;
        if (appealReconciliationReads >= 2) appealVisible = true;
      } else if (resolutionAccepted && !resolutionVisible) {
        resolutionReconciliationReads += 1;
        if (resolutionReconciliationReads >= 2) resolutionVisible = true;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          pub_id: 'inv_governance_safe',
          scores: [
            {
              pub_id: 'score_governance_safe',
              probability: 0.73,
              evidence_sufficiency: 0.82,
              uncertainty: 0.19,
              rule_version: 'geo-rule-v2',
              explanation: ['两个独立来源簇共同支持该 Claim'],
              created_at: '2026-07-25T00:30:00Z',
            },
          ],
          claims: [
            {
              pub_id: 'clm_governance_safe',
              normalized_text: '独立申诉复核原子 Claim',
              verifiability: 'verifiable',
            },
          ],
          evidence_matrix: [
            {
              pub_id: 'ce_governance_safe',
              claim_pub_id: 'clm_governance_safe',
              evidence_pub_id: 'evd_governance_safe',
              relation: 'supports',
              source_cluster: 'cluster-governance-safe',
              independence_weight: 0.9,
              rationale: '独立来源支持',
            },
          ],
          source_independence: [
            {
              pub_id: 'srca_governance_safe',
              source_pub_id: 'evd_governance_safe',
              cluster_id: 'cluster-governance-safe',
              independence_weight: 0.9,
              circular_citation_risk: 0.1,
            },
          ],
          graph: [],
          appeals: appealVisible
            ? [
                {
                  pub_id: 'apl_governance_safe',
                  submitted_by_pub_id: 'usr_intelligence_analyst',
                  reason: '新增独立登记材料，需要重新复核原裁决。',
                  state: resolutionVisible ? 'upheld' : 'open',
                  resolution: resolutionVisible ? 'upheld' : null,
                  resolved_by_pub_id: resolutionVisible ? 'usr_intelligence_reviewer' : null,
                  resolution_rationale: resolutionVisible
                    ? '二次复核未发现足以改写原裁决的新独立证据。'
                    : null,
                  created_at: '2026-07-25T02:00:00Z',
                  updated_at: resolutionVisible ? '2026-07-25T03:00:00Z' : '2026-07-25T02:00:00Z',
                  resolved_at: resolutionVisible ? '2026-07-25T03:00:00Z' : null,
                },
              ]
            : [],
          verdicts: [
            {
              pub_id: 'vrd_governance_safe',
              verdict: 'likely',
              reviewer_pub_id: 'usr_original_reviewer',
              rationale: '初次人工复核确认当前证据支持高风险表述。',
              supersedes_pub_id: null,
              created_at: '2026-07-25T01:00:00Z',
            },
          ],
          token: 'Bearer governance-detail-canary',
          profile_path: '/secret/profile/governance-detail-canary',
        }),
      });
      return;
    }
    if (path.endsWith('/appeals')) {
      appealWrites += 1;
      appealAccepted = true;
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          appeal_pub_id: 'apl_governance_safe',
          cookie: 'SESSION=governance-appeal-receipt-canary',
        }),
      });
      return;
    }
    resolutionWrites += 1;
    resolutionAccepted = true;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        replacement_verdict_pub_id: null,
        token: 'Bearer governance-resolution-receipt-canary',
      }),
    });
  });

  await page.goto('/platform/intelligence/');
  await page.getByRole('button', { name: /裁决与申诉/ }).click();
  await page.getByLabel('申诉理由').fill('新增独立登记材料，需要重新复核原裁决。');
  await page.getByRole('button', { name: '提交申诉' }).click();
  await expect(page.getByRole('button', { name: '重试此区域' })).toBeVisible();
  expect(appealWrites).toBe(1);
  expect(appealReconciliationReads).toBe(1);
  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('真实申诉已登记')).toBeVisible();
  await expect(page.getByText('原裁决保持可追溯，等待另一名复核员。')).toBeVisible();
  expect(appealWrites).toBe(1);
  expect(appealReconciliationReads).toBe(2);
  await expect(page.getByRole('button', { name: '记录二次复核' })).toBeDisabled();

  currentRole = 'reviewer';
  currentUserPubId = 'usr_intelligence_reviewer';
  await page.evaluate(() => {
    localStorage.setItem('geo.session.actor', 'reviewer-intelligence-governance');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await page.reload();
  await page.getByRole('button', { name: /裁决与申诉/ }).click();
  await expect(page.getByText('原裁决保持可追溯，等待另一名复核员。')).toBeVisible();
  await page.getByRole('button', { name: '记录二次复核' }).click();
  await expect(page.getByRole('button', { name: '重试此区域' })).toBeVisible();
  expect(resolutionWrites).toBe(1);
  expect(resolutionReconciliationReads).toBe(1);
  await page.getByRole('button', { name: '重试此区域' }).click();
  await expect(page.getByText('真实二次复核已记录')).toBeVisible();
  await expect(page.getByText('reviewed', { exact: true })).toBeVisible();
  expect(resolutionWrites).toBe(1);
  expect(resolutionReconciliationReads).toBe(2);
  await expectAccessible(page);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /governance-detail-canary|governance-appeal-receipt-canary|governance-resolution-receipt-canary|SESSION=|Bearer |\/secret\/profile/i,
  );
});

test('investigation 403 fails closed without probing a case detail', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_forbidden');
    localStorage.setItem('geo.session.actor', 'reviewer-intelligence-forbidden');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await installSyntheticHttpResponses(page, [
    {
      id: 'intelligence-catalog-forbidden',
      path: '/api/v2/intelligence/investigations',
      status: 403,
      body: {
        error: {
          code: 'forbidden',
          message: 'SESSION=forbidden-intelligence-canary',
          request_id: 'req_safe',
        },
      },
    },
    {
      id: 'intelligence-detail-forbidden',
      path: '/api/v2/intelligence/investigations/',
      match: 'prefix',
      status: 403,
    },
  ]);
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_forbidden',
        user_pub_id: 'usr_intelligence_forbidden',
        role: 'reviewer',
        permissions: ['intelligence:read', 'intelligence:review'],
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
        data: [],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );
  await page.goto('/platform/intelligence/');
  await expect(page.getByText('无权查看')).toBeVisible();
  await expect(page.getByText('SESSION=forbidden-intelligence-canary')).toHaveCount(0);
  expect(await syntheticHttpResponseCount(page, 'intelligence-catalog-forbidden')).toBe(1);
  expect(await syntheticHttpResponseCount(page, 'intelligence-detail-forbidden')).toBe(0);
});
