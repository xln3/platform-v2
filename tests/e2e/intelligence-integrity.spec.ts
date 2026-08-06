import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';

test('unsafe detail rows fail closed without leaking or enabling partial writes', async ({
  page,
}) => {
  const writes: string[] = [];
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_integrity');
    localStorage.setItem('geo.session.actor', 'admin-intelligence-integrity');
    localStorage.setItem('geo.session.role', 'admin');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_integrity',
        user_pub_id: 'usr_intelligence_integrity',
        role: 'admin',
        permissions: ['intelligence:read', 'intelligence:analyze', 'intelligence:review'],
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
            pub_id: 'prj_intelligence_integrity',
            tenant_pub_id: 'tnt_intelligence_integrity',
            name: '安全投影完整性项目',
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
  await page.route('**/api/v2/evidence/packages', (route) => {
    writes.push(`${route.request().method()} ${route.request().url()}`);
    return route.fulfill({ status: 201, contentType: 'application/json', body: '{}' });
  });
  await page.route('**/api/v2/intelligence/investigations**', (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      writes.push(`${request.method()} ${request.url()}`);
      return route.fulfill({ status: 201, contentType: 'application/json', body: '{}' });
    }
    if (path.endsWith('/investigations')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: 'inv_intelligence_integrity',
              title: '安全投影完整性案件',
              state: 'review',
              access_class: 'customer_private',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
              claim_count: 1,
              source_cluster_count: 1,
              probability: '0.84',
              latest_verdict: 'likely',
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/page-history')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            content_pub_id: 'cnt_intelligence_history',
            version_pub_id: 'cntv_intelligence_history_01',
            canonical_url: 'https://integrity.example/history',
            title: '安全历史页面',
            version_number: 1,
            body_hash: 'a'.repeat(64),
            evidence_pub_id: 'evd_intelligence_history_01',
            captured_at: '2026-07-25T01:00:00Z',
            published_at: null,
            snapshot_pub_id: 'snap_intelligence_history_01',
            snapshot_number: 1,
            normalized_text_hash: 'a'.repeat(64),
            perceptual_hash: null,
          },
          {
            content_pub_id: 'cnt_intelligence_history',
            version_pub_id: 'cntv_intelligence_history_02',
            canonical_url: 'https://integrity.example/history',
            title: '安全历史页面（修订）',
            version_number: 2,
            body_hash: 'b'.repeat(64),
            evidence_pub_id: 'evd_intelligence_history_02',
            captured_at: '2026-07-25T02:00:00Z',
            published_at: null,
            snapshot_pub_id: 'snap_intelligence_history_02',
            snapshot_number: 2,
            normalized_text_hash: 'b'.repeat(64),
            perceptual_hash: null,
          },
        ]),
      });
    }
    if (path.endsWith('/visual-diffs')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            pub_id: 'diff_intelligence_history_safe',
            content_pub_id: 'cnt_intelligence_history',
            before_version_pub_id: 'cntv_intelligence_history_01',
            after_version_pub_id: 'cntv_intelligence_history_02',
            before_evidence_pub_id: 'evd_intelligence_history_01',
            after_evidence_pub_id: 'evd_intelligence_history_02',
            text_diff: {
              before_hash: 'a'.repeat(64),
              after_hash: 'b'.repeat(64),
            },
            similarity: '0.75',
            visual_diff_available: false,
            created_at: '2026-07-25T02:01:00Z',
          },
          {
            pub_id: 'diff_intelligence_history_mismatch',
            content_pub_id: 'cnt_intelligence_history',
            before_version_pub_id: 'cntv_intelligence_history_01',
            after_version_pub_id: 'cntv_intelligence_history_02',
            before_evidence_pub_id: 'evd_intelligence_history_01',
            after_evidence_pub_id: 'evd_intelligence_history_other',
            text_diff: {
              before_hash: 'a'.repeat(64),
              after_hash: 'b'.repeat(64),
            },
            similarity: '0.5',
            visual_diff_available: true,
            created_at: '2026-07-25T02:02:00Z',
            cookie: 'SESSION=history-chain-canary',
          },
        ]),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pub_id: 'inv_intelligence_integrity',
        scores: [
          {
            pub_id: 'score_intelligence_integrity',
            probability: '0.84',
            evidence_sufficiency: '0.91',
            uncertainty: '0.16',
            rule_version: 'integrity-v1',
            explanation: { basis: '真实安全解释' },
            created_at: '2026-07-25T00:00:00Z',
          },
          {
            pub_id: 'score_intelligence_integrity',
            probability: '0.99',
            evidence_sufficiency: '0.99',
            uncertainty: '0.01',
            rule_version: 'integrity-duplicate-score',
            explanation: { basis: 'Bearer integrity-duplicate-score-canary' },
            created_at: '2026-07-25T00:01:00Z',
          },
          {
            pub_id: 'score_intelligence_reverse',
            probability: '0.12',
            evidence_sufficiency: '0.22',
            uncertainty: '0.88',
            rule_version: 'integrity-reverse-score',
            explanation: { basis: 'SESSION=integrity-reverse-score-canary' },
            created_at: '2026-07-24T23:59:00Z',
          },
        ],
        claims: [
          {
            pub_id: 'clm_intelligence_integrity',
            normalized_text: '真实安全 Claim',
            verifiability: 'verifiable',
          },
          {
            pub_id: 'clm_intelligence_integrity',
            normalized_text: '重复 Claim 不得展示',
            verifiability: 'verifiable',
            cookie: 'SESSION=integrity-duplicate-claim-canary',
          },
        ],
        evidence_matrix: [
          {
            pub_id: 'ce_intelligence_integrity',
            claim_pub_id: 'clm_intelligence_integrity',
            evidence_pub_id: 'evd_intelligence_integrity',
            relation: 'supports',
            source_cluster: 'cluster-integrity',
            independence_weight: '0.9',
            rationale: '真实安全理由',
          },
          {
            pub_id: 'ce_intelligence_hostile',
            claim_pub_id: 'clm_intelligence_integrity',
            evidence_pub_id: 'evd_intelligence_hostile',
            relation: 'supports',
            source_cluster: 'cluster-integrity',
            independence_weight: '0.8',
            rationale: 'Bearer integrity-evidence-canary',
          },
          {
            pub_id: 'ce_intelligence_cross_claim',
            claim_pub_id: 'clm_intelligence_other',
            evidence_pub_id: 'evd_intelligence_cross_claim',
            relation: 'supports',
            source_cluster: 'cluster-integrity',
            independence_weight: '0.8',
            rationale: '跨案件 Claim 关系不得展示',
            cookie: 'SESSION=integrity-cross-claim-canary',
          },
          {
            pub_id: 'ce_intelligence_duplicate_pair',
            claim_pub_id: 'clm_intelligence_integrity',
            evidence_pub_id: 'evd_intelligence_integrity',
            relation: 'supports',
            source_cluster: 'cluster-integrity',
            independence_weight: '0.7',
            rationale: '重复 Claim 证据关系不得展示',
          },
        ],
        source_independence: [
          {
            pub_id: 'srca_intelligence_integrity',
            source_pub_id: 'evd_intelligence_integrity',
            cluster_id: 'cluster-integrity',
            independence_weight: '0.9',
            circular_citation_risk: '0.1',
          },
          {
            pub_id: 'srca_intelligence_duplicate_source',
            source_pub_id: 'evd_intelligence_integrity',
            cluster_id: 'cluster-other',
            independence_weight: '0.7',
            circular_citation_risk: '0.2',
            token: 'Bearer integrity-duplicate-source-canary',
          },
        ],
        graph: [
          {
            from_pub_id: 'evd_intelligence_integrity',
            to_pub_id: 'clm_intelligence_integrity',
            relation: 'supports',
            weight: '0.9',
            evidence_pub_id: 'evd_intelligence_integrity',
          },
          {
            from_pub_id: 'evd_intelligence_integrity',
            to_pub_id: 'clm_intelligence_integrity',
            relation: 'supports',
            weight: '0.7',
            evidence_pub_id: 'evd_intelligence_other',
            token: 'Bearer integrity-duplicate-graph-canary',
          },
          {
            from_pub_id: 'cntv_intelligence_integrity',
            to_pub_id: 'ent_intelligence_integrity',
            relation: 'organized_by',
            weight: '0.8',
            evidence_pub_id: null,
            cookie: 'SESSION=integrity-invalid-graph-canary',
          },
        ],
        appeals: [
          {
            pub_id: 'apl_intelligence_integrity',
            state: 'open',
            submitted_by_pub_id: 'usr_intelligence_submitter',
            reason: '申请复核当前裁决。',
            resolution: null,
            resolved_by_pub_id: null,
            resolution_rationale: null,
            created_at: '2026-07-25T03:00:00Z',
            updated_at: '2026-07-25T03:00:00Z',
            resolved_at: null,
          },
          {
            pub_id: 'apl_intelligence_integrity',
            state: 'reviewing',
            submitted_by_pub_id: 'usr_intelligence_submitter',
            reason: '申请复核当前裁决。',
            resolution: null,
            resolved_by_pub_id: null,
            resolution_rationale: null,
            created_at: '2026-07-25T03:01:00Z',
            updated_at: '2026-07-25T03:01:00Z',
            resolved_at: null,
            token: 'Bearer integrity-duplicate-appeal-canary',
          },
          {
            pub_id: 'apl_intelligence_reverse',
            state: 'open',
            submitted_by_pub_id: 'usr_intelligence_submitter',
            reason: '申请复核当前裁决。',
            resolution: null,
            resolved_by_pub_id: null,
            resolution_rationale: null,
            created_at: '2026-07-25T02:59:00Z',
            updated_at: '2026-07-25T02:59:00Z',
            resolved_at: null,
            cookie: 'SESSION=integrity-reverse-appeal-canary',
          },
        ],
        verdicts: [
          {
            pub_id: 'vrd_intelligence_integrity',
            verdict: 'likely',
            reviewer_pub_id: 'usr_intelligence_reviewer',
            rationale: '安全人工裁决理由。',
            supersedes_pub_id: null,
            created_at: '2026-07-25T02:00:00Z',
          },
          {
            pub_id: 'vrd_intelligence_integrity',
            verdict: 'unlikely',
            reviewer_pub_id: 'usr_intelligence_reviewer',
            rationale: '安全人工裁决理由。',
            supersedes_pub_id: null,
            created_at: '2026-07-25T02:01:00Z',
            token: 'Bearer integrity-duplicate-verdict-canary',
          },
          {
            pub_id: 'vrd_intelligence_reverse',
            verdict: 'insufficient',
            reviewer_pub_id: 'usr_intelligence_reviewer',
            rationale: '安全人工裁决理由。',
            supersedes_pub_id: null,
            created_at: '2026-07-25T01:59:00Z',
            cookie: 'SESSION=integrity-reverse-verdict-canary',
          },
        ],
        cookie: 'SESSION=integrity-root-canary',
        profile_path: '/secret/profile/integrity-root-canary',
        otp: 824911,
      }),
    });
  });

  await page.goto('/platform/intelligence/');
  await expect(page.getByText('安全投影完整性案件', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '页面历史' }).click();
  await expect(
    page.getByRole('heading', { name: '安全历史页面（修订）', exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/相似度 75.0%/)).toBeVisible();
  await expect(page.getByText(/视觉 Diff含未通过安全校验的数据/)).toBeVisible();
  await expect(page.getByText(/50.0%/)).toHaveCount(0);
  await expectAccessible(page);
  await page.getByRole('button', { name: /裁决与申诉/ }).click();
  await expect(page.getByText('安全投影不完整', { exact: true })).toBeVisible();
  await expect(
    page.getByText(
      /评分记录、原子 Claim、证据关系、来源独立性、传播关系、申诉记录、裁决记录含未通过安全校验的数据/,
    ),
  ).toBeVisible();
  await expect(page.getByText('0.84', { exact: true })).toBeVisible();
  await expect(page.getByText('0.61', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '证据不足，不成立' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '确认高风险表述' })).toBeDisabled();
  await page.getByLabel('申诉理由').fill('新增独立证据申请重新复核');
  await expect(page.getByRole('button', { name: '提交申诉' })).toBeDisabled();
  await expectAccessible(page);

  await page.getByRole('button', { name: '传播关系' }).click();
  await expect(page.getByText(/传播关系含未通过安全校验的数据；相关写操作已锁定/)).toBeVisible();
  await expect(page.getByRole('table', { name: '传播图节点与关系' }).getByRole('row')).toHaveCount(
    2,
  );
  await expect(page.getByText('organized_by', { exact: true })).toHaveCount(0);
  await expectAccessible(page);

  await page.getByRole('button', { name: '证据包' }).click();
  await expect(page.getByText('安全投影不完整', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '生成证据对象包' })).toBeDisabled();
  await expectAccessible(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /integrity-(?:duplicate-claim|evidence|cross-claim|duplicate-source|duplicate-graph|invalid-graph|duplicate-score|reverse-score|duplicate-appeal|reverse-appeal|duplicate-verdict|reverse-verdict|root)-canary|history-chain-canary|SESSION=|Bearer |824911|\/secret\/profile/i,
  );
  expect(writes).toEqual([]);
});

test('a broken verdict supersession chain is disclosed and write locked', async ({ page }) => {
  const writes: string[] = [];
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_verdict_chain');
    localStorage.setItem('geo.session.actor', 'admin-verdict-chain');
    localStorage.setItem('geo.session.role', 'admin');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_verdict_chain',
        user_pub_id: 'usr_intelligence_verdict_chain',
        role: 'admin',
        permissions: ['intelligence:read', 'intelligence:analyze', 'intelligence:review'],
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
            pub_id: 'prj_intelligence_verdict_chain',
            tenant_pub_id: 'tnt_intelligence_verdict_chain',
            name: '裁决版本链完整性项目',
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
  await page.route('**/api/v2/intelligence/investigations**', (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      writes.push(`${request.method()} ${path}`);
      return route.fulfill({ status: 201, contentType: 'application/json', body: '{}' });
    }
    if (path.endsWith('/investigations')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: 'inv_intelligence_verdict_chain',
              title: '裁决版本链案件',
              state: 'corrected',
              access_class: 'customer_private',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T02:00:00Z',
              claim_count: 0,
              source_cluster_count: 0,
              probability: '0.73',
              latest_verdict: 'unlikely',
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/page-history')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            content_pub_id: 'cnt_other_root_history',
            version_pub_id: 'cntv_other_root_history_01',
            canonical_url: 'https://other-root.example/history',
            title: '跨案件历史不得展示',
            version_number: 1,
            body_hash: 'a'.repeat(64),
            evidence_pub_id: 'evd_other_root_history_01',
            captured_at: '2026-07-25T01:00:00Z',
            published_at: null,
            snapshot_pub_id: 'snap_other_root_history_01',
            snapshot_number: 1,
            normalized_text_hash: 'a'.repeat(64),
            perceptual_hash: null,
          },
          {
            content_pub_id: 'cnt_other_root_history',
            version_pub_id: 'cntv_other_root_history_02',
            canonical_url: 'https://other-root.example/history',
            title: '跨案件历史修订不得展示',
            version_number: 2,
            body_hash: 'b'.repeat(64),
            evidence_pub_id: 'evd_other_root_history_02',
            captured_at: '2026-07-25T02:00:00Z',
            published_at: null,
            snapshot_pub_id: 'snap_other_root_history_02',
            snapshot_number: 2,
            normalized_text_hash: 'b'.repeat(64),
            perceptual_hash: null,
            token: 'Bearer other-root-history-canary',
          },
        ]),
      });
    }
    if (path.endsWith('/visual-diffs')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            pub_id: 'diff_other_root_history',
            content_pub_id: 'cnt_other_root_history',
            before_version_pub_id: 'cntv_other_root_history_01',
            after_version_pub_id: 'cntv_other_root_history_02',
            before_evidence_pub_id: 'evd_other_root_history_01',
            after_evidence_pub_id: 'evd_other_root_history_02',
            text_diff: {
              before_hash: 'a'.repeat(64),
              after_hash: 'b'.repeat(64),
            },
            similarity: '0.93',
            visual_diff_available: true,
            created_at: '2026-07-25T02:01:00Z',
          },
        ]),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pub_id: 'inv_intelligence_verdict_chain',
        scores: [
          {
            pub_id: 'score_intelligence_verdict_chain',
            probability: '0.73',
            evidence_sufficiency: '0.82',
            uncertainty: '0.19',
            rule_version: 'verdict-chain-v1',
            explanation: ['安全解释'],
            created_at: '2026-07-25T00:00:00Z',
          },
        ],
        claims: [],
        evidence_matrix: [],
        source_independence: [],
        graph: [],
        appeals: [],
        verdicts: [
          {
            pub_id: 'vrd_intelligence_verdict_chain_01',
            verdict: 'likely',
            reviewer_pub_id: 'usr_verdict_reviewer_01',
            rationale: '初次人工裁决理由。',
            supersedes_pub_id: null,
            created_at: '2026-07-25T01:00:00Z',
          },
          {
            pub_id: 'vrd_intelligence_verdict_chain_02',
            verdict: 'unlikely',
            reviewer_pub_id: 'usr_verdict_reviewer_02',
            rationale: '复核后的人工裁决理由。',
            supersedes_pub_id: 'vrd_intelligence_verdict_chain_missing',
            created_at: '2026-07-25T02:00:00Z',
            token: 'Bearer broken-verdict-chain-e2e-canary',
          },
        ],
      }),
    });
  });

  await page.goto('/platform/intelligence/');
  await expect(page.getByText('裁决版本链案件', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /裁决与申诉/ }).click();
  await expect(page.getByText('pending', { exact: true })).toBeVisible();
  await expect(page.getByText(/裁决记录含未通过安全校验的数据；相关写操作已锁定/)).toBeVisible();
  await expect(page.getByRole('button', { name: '证据不足，不成立' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '确认高风险表述' })).toBeDisabled();
  await page.getByLabel('申诉理由').fill('新增独立来源申请重新复核');
  await expect(page.getByRole('button', { name: '提交申诉' })).toBeDisabled();
  expect(writes).toEqual([]);
  await expectAccessible(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/broken-verdict-chain-e2e-canary|Bearer /i);
});

test('appeals inconsistent with verdict history or resolution transaction are disclosed and write locked', async ({
  page,
}) => {
  const writes: string[] = [];
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_appeal_history');
    localStorage.setItem('geo.session.actor', 'admin-appeal-history');
    localStorage.setItem('geo.session.role', 'admin');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_appeal_history',
        user_pub_id: 'usr_intelligence_appeal_history',
        role: 'admin',
        permissions: ['intelligence:read', 'intelligence:analyze', 'intelligence:review'],
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
            pub_id: 'prj_intelligence_appeal_history',
            tenant_pub_id: 'tnt_intelligence_appeal_history',
            name: '申诉与裁决历史完整性项目',
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
  await page.route('**/api/v2/intelligence/investigations**', (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      writes.push(`${request.method()} ${path}`);
      return route.fulfill({ status: 201, contentType: 'application/json', body: '{}' });
    }
    if (path.endsWith('/investigations')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: 'inv_intelligence_appeal_history',
              title: '申诉与裁决历史案件',
              state: 'corrected',
              access_class: 'customer_private',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T02:30:00Z',
              claim_count: 0,
              source_cluster_count: 0,
              probability: '0.73',
              latest_verdict: 'likely',
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/page-history') || path.endsWith('/visual-diffs')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pub_id: 'inv_intelligence_appeal_history',
        scores: [
          {
            pub_id: 'score_intelligence_appeal_history',
            probability: '0.73',
            evidence_sufficiency: '0.82',
            uncertainty: '0.19',
            rule_version: 'appeal-verdict-v1',
            explanation: ['安全解释'],
            created_at: '2026-07-25T00:00:00Z',
          },
        ],
        claims: [],
        evidence_matrix: [],
        source_independence: [],
        graph: [],
        appeals: [
          {
            pub_id: 'apl_intelligence_before_verdict',
            state: 'open',
            submitted_by_pub_id: 'usr_intelligence_submitter',
            reason: '新增独立来源申请重新复核。',
            resolution: null,
            resolved_by_pub_id: null,
            resolution_rationale: null,
            created_at: '2026-07-25T00:30:00Z',
            updated_at: '2026-07-25T00:30:00Z',
            resolved_at: null,
            token: 'Bearer appeal-before-verdict-e2e-canary',
          },
          {
            pub_id: 'apl_intelligence_missing_correction',
            state: 'corrected',
            submitted_by_pub_id: 'usr_intelligence_submitter',
            reason: '新增独立来源申请重新复核。',
            resolution: 'corrected',
            resolved_by_pub_id: 'usr_intelligence_reviewer',
            resolution_rationale: '独立复核确认需要更正原裁决。',
            created_at: '2026-07-25T01:30:00Z',
            updated_at: '2026-07-25T02:30:00Z',
            resolved_at: '2026-07-25T02:30:00Z',
            cookie: 'SESSION=appeal-missing-correction-e2e-canary',
          },
          {
            pub_id: 'apl_intelligence_resolution_mismatch',
            state: 'upheld',
            submitted_by_pub_id: 'usr_intelligence_submitter',
            reason: '新增独立来源申请重新复核。',
            resolution: 'rejected',
            resolved_by_pub_id: 'usr_intelligence_reviewer',
            resolution_rationale: '不应保留的错配解决结果。',
            created_at: '2026-07-25T03:00:00Z',
            updated_at: '2026-07-25T04:00:00Z',
            resolved_at: '2026-07-25T04:00:00Z',
            token: 'Bearer appeal-resolution-mismatch-e2e-canary',
          },
        ],
        verdicts: [
          {
            pub_id: 'vrd_intelligence_appeal_history_01',
            verdict: 'likely',
            reviewer_pub_id: 'usr_appeal_prior_reviewer',
            rationale: '原人工裁决理由。',
            supersedes_pub_id: null,
            created_at: '2026-07-25T01:00:00Z',
          },
        ],
      }),
    });
  });

  await page.goto('/platform/intelligence/');
  await expect(page.getByText('申诉与裁决历史案件', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /裁决与申诉/ }).click();
  await expect(page.getByText('pending', { exact: true })).toBeVisible();
  await expect(page.getByText(/申诉记录含未通过安全校验的数据；相关写操作已锁定/)).toBeVisible();
  await expect(page.getByRole('button', { name: '证据不足，不成立' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '确认高风险表述' })).toBeDisabled();
  await page.getByLabel('申诉理由').fill('新增独立来源申请重新复核');
  await expect(page.getByRole('button', { name: '提交申诉' })).toBeDisabled();
  expect(writes).toEqual([]);
  await expectAccessible(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /appeal-before-verdict-e2e-canary|appeal-missing-correction-e2e-canary|appeal-resolution-mismatch-e2e-canary|SESSION=|Bearer /i,
  );
});

test('a non-independent appeal resolver is disclosed and write locked', async ({ page }) => {
  const writes: string[] = [];
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_appeal_independence');
    localStorage.setItem('geo.session.actor', 'admin-appeal-independence');
    localStorage.setItem('geo.session.role', 'admin');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_appeal_independence',
        user_pub_id: 'usr_intelligence_appeal_independence',
        role: 'admin',
        permissions: ['intelligence:read', 'intelligence:analyze', 'intelligence:review'],
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
            pub_id: 'prj_intelligence_appeal_independence',
            tenant_pub_id: 'tnt_intelligence_appeal_independence',
            name: '独立申诉复核项目',
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
  await page.route('**/api/v2/intelligence/investigations**', (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      writes.push(`${request.method()} ${path}`);
      return route.fulfill({ status: 201, contentType: 'application/json', body: '{}' });
    }
    if (path.endsWith('/investigations')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: 'inv_intelligence_appeal_independence',
              title: '独立申诉复核案件',
              state: 'corrected',
              access_class: 'customer_private',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T02:30:00Z',
              claim_count: 0,
              source_cluster_count: 0,
              probability: '0.73',
              latest_verdict: 'unlikely',
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/page-history') || path.endsWith('/visual-diffs')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pub_id: 'inv_intelligence_appeal_independence',
        scores: [
          {
            pub_id: 'score_intelligence_appeal_independence',
            probability: '0.73',
            evidence_sufficiency: '0.82',
            uncertainty: '0.19',
            rule_version: 'appeal-independence-v1',
            explanation: ['安全解释'],
            created_at: '2026-07-25T00:00:00Z',
          },
        ],
        claims: [],
        evidence_matrix: [],
        source_independence: [],
        graph: [],
        appeals: [
          {
            pub_id: 'apl_intelligence_appeal_independence',
            state: 'corrected',
            submitted_by_pub_id: 'usr_intelligence_appeal_submitter',
            reason: '新增独立来源申请重新复核。',
            resolution: 'corrected',
            resolved_by_pub_id: 'usr_intelligence_prior_reviewer',
            resolution_rationale: '独立复核确认需要更正原裁决。',
            created_at: '2026-07-25T01:30:00Z',
            updated_at: '2026-07-25T02:30:00Z',
            resolved_at: '2026-07-25T02:30:00Z',
            token: 'Bearer non-independent-appeal-e2e-canary',
          },
        ],
        verdicts: [
          {
            pub_id: 'vrd_intelligence_appeal_independence_01',
            verdict: 'likely',
            reviewer_pub_id: 'usr_intelligence_prior_reviewer',
            rationale: '原人工裁决理由。',
            supersedes_pub_id: null,
            created_at: '2026-07-25T01:00:00Z',
          },
          {
            pub_id: 'vrd_intelligence_appeal_independence_02',
            verdict: 'unlikely',
            reviewer_pub_id: 'usr_intelligence_prior_reviewer',
            rationale: '独立复核确认需要更正原裁决。',
            supersedes_pub_id: 'vrd_intelligence_appeal_independence_01',
            created_at: '2026-07-25T02:00:00Z',
          },
        ],
      }),
    });
  });

  await page.goto('/platform/intelligence/');
  await expect(page.getByText('独立申诉复核案件', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /裁决与申诉/ }).click();
  await expect(page.getByText('pending', { exact: true })).toBeVisible();
  await expect(page.getByText(/申诉记录含未通过安全校验的数据；相关写操作已锁定/)).toBeVisible();
  await expect(page.getByRole('button', { name: '证据不足，不成立' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '确认高风险表述' })).toBeDisabled();
  await page.getByLabel('申诉理由').fill('新增独立来源申请重新复核');
  await expect(page.getByRole('button', { name: '提交申诉' })).toBeDisabled();
  expect(writes).toEqual([]);
  await expectAccessible(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/non-independent-appeal-e2e-canary|Bearer /i);
});

test('invalid case summary domain values reveal neither the row nor a detail probe', async ({
  page,
}) => {
  let detailRequests = 0;
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_invalid_summary');
    localStorage.setItem('geo.session.actor', 'reviewer-invalid-summary');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_invalid_summary',
        user_pub_id: 'usr_intelligence_invalid_summary',
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
            pub_id: 'prj_intelligence_invalid_summary',
            tenant_pub_id: 'tnt_intelligence_invalid_summary',
            name: '案件列表安全投影项目',
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
  await page.route('**/api/v2/intelligence/investigations**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.endsWith('/investigations')) {
      detailRequests += 1;
      return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            pub_id: 'inv_invalid_summary',
            title: '不应推断存在的无效案件',
            state: 'open',
            access_class: 'restricted',
            created_at: '1',
            updated_at: '2026-07-25T01:00:00Z',
            claim_count: Number.MAX_VALUE,
            source_cluster_count: 1,
            probability: '1.5',
            latest_verdict: 'confirmed',
            token: 'Bearer invalid-summary-canary',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    });
  });

  await page.goto('/platform/intelligence/');
  await expect(page.getByText('安全投影不完整', { exact: true })).toBeVisible();
  await expect(page.getByText('加载失败', { exact: true })).toBeVisible();
  await expect(page.getByText('暂无数据')).toHaveCount(0);
  await expect(page.getByText('不应推断存在的无效案件')).toHaveCount(0);
  expect(detailRequests).toBe(0);
  await expectAccessible(page);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/invalid-summary-canary|Bearer /i);
});

test('a detail response bound to another investigation fails closed', async ({ page }) => {
  const writes: string[] = [];
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_intelligence_root_binding');
    localStorage.setItem('geo.session.actor', 'reviewer-root-binding');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_intelligence_root_binding',
        user_pub_id: 'usr_intelligence_root_binding',
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
            pub_id: 'prj_intelligence_root_binding',
            tenant_pub_id: 'tnt_intelligence_root_binding',
            name: '案件根资源绑定项目',
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
  await page.route('**/api/v2/intelligence/investigations**', (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      writes.push(`${request.method()} ${path}`);
      return route.fulfill({ status: 201, contentType: 'application/json', body: '{}' });
    }
    if (path.endsWith('/investigations')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              pub_id: 'inv_requested_root_binding',
              title: '请求路径中的案件',
              state: 'review',
              access_class: 'customer_private',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
              claim_count: 1,
              source_cluster_count: 1,
              probability: '0.84',
              latest_verdict: 'likely',
            },
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/page-history') || path.endsWith('/visual-diffs')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pub_id: 'inv_other_root_binding',
        scores: [
          {
            pub_id: 'score_other_root_binding',
            probability: '0.99',
            evidence_sufficiency: '0.99',
            uncertainty: '0.01',
            rule_version: 'cross-investigation-canary',
            explanation: { basis: 'Bearer root-binding-canary' },
            created_at: '2026-07-25T00:00:00Z',
          },
        ],
        claims: [
          {
            pub_id: 'clm_other_root_binding',
            normalized_text: '不应展示的跨案件 Claim',
            verifiability: 'verifiable',
          },
        ],
        evidence_matrix: [],
        source_independence: [],
        graph: [],
        appeals: [],
        verdicts: [],
        cookie: 'SESSION=root-binding-canary',
      }),
    });
  });

  await page.goto('/platform/intelligence/');
  await expect(page.getByText('加载失败', { exact: true })).toBeVisible();
  await expect(page.getByText('请求路径中的案件', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Claim 矩阵' }).click();
  await expect(page.getByText('加载失败', { exact: true })).toBeVisible();
  await expect(page.getByText('不应展示的跨案件 Claim')).toHaveCount(0);
  await expectAccessible(page);
  await page.getByRole('button', { name: '页面历史' }).click();
  await expect(page.getByText('加载失败', { exact: true })).toBeVisible();
  await expect(page.getByText(/跨案件历史/)).toHaveCount(0);
  await expect(page.getByText('93.0%')).toHaveCount(0);
  await expectAccessible(page);
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
    /cross-investigation-canary|root-binding-canary|other-root-history-canary|SESSION=|Bearer /i,
  );
});
