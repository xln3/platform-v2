import { expectAccessible } from './accessibility';
import { expect, test } from './runtime-fixture';
import { captureSafeScreenshot } from './screenshot-safety';

const project = {
  pub_id: 'prj_service2_visual',
  tenant_pub_id: 'tnt_service2_visual',
  name: '全 U 核查可视验收项目',
  state: 'active',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-24T08:00:00Z',
};

const batch = {
  schema_version: 'formal-service2-source-corpus-v2',
  batch_pub_id: 's2b_visual_fixture',
  project_pub_id: project.pub_id,
  service_entitlement_pub_id: 'ent_service2_visual',
  service_entitlement_revision: 'a'.repeat(64),
  run_pub_ids: ['run_visual_a', 'run_visual_b'],
  analysis_model: 'gpt-5.6-luna',
  window_start: '2026-08-01T00:00:00Z',
  window_end: '2026-08-24T00:00:00Z',
  source_snapshot_boundary: '2026-08-24T00:00:01Z',
  corpus_policy_version: 'service2-all-u-occurrence-v1',
  judgment_policy_version: 'service2-entity-relation-v1',
  status: 'review',
  version: 4,
  workflow_id: 'service2-corpus/visual',
  frozen_at: null,
  manifest_hash: null,
  error_code: null,
  created_at: '2026-08-24T00:00:00Z',
  updated_at: '2026-08-24T00:10:00Z',
  coverage: {
    selected_queries: 6,
    successful_queries: 5,
    failed_queries: 1,
    successful_queries_with_u: 4,
    successful_queries_without_u: 1,
    query_failure_codes: { provider_timeout: 1 },
    query_outcomes_complete: true,
    query_coverage_complete: false,
    expected_occurrences: 5,
    materialized_items: 5,
    distinct_urls: 3,
    processing_states: { processed: 3, manual_evidence_required: 1, blocked: 1 },
    fetch_states: { succeeded: 4, blocked: 1 },
    entered_judgment: 3,
    findings: 3,
    reviewed_findings: 1,
    eligible_cases: 1,
    coverage_complete: true,
  },
};

function corpusItem(index: number) {
  const blocked = index === 5;
  return {
    item_pub_id: `s2i_visual_${index}`,
    occurrence_pub_id: `aso_visual_${index}`,
    run_pub_id: index < 4 ? 'run_visual_a' : 'run_visual_b',
    answer_pub_id: `ans_visual_${index}`,
    source_url_pub_id: `url_visual_${Math.min(index, 3)}`,
    snapshot_pub_id: `snp_visual_${Math.min(index, 3)}`,
    source_document_pub_id: blocked ? null : `src_visual_${Math.min(index, 3)}`,
    fetch_attempt_pub_id: `fat_visual_${Math.min(index, 3)}`,
    raw_url: `https://evidence.example.com/very-long-post-path/${index}`,
    canonical_url: `https://evidence.example.com/very-long-post-path/${index}`,
    site_host: 'evidence.example.com',
    occurrence_ordinal: index,
    u_rank: index,
    captured_at: `2026-08-${String(index).padStart(2, '0')}T00:00:00Z`,
    platform: 'doubao',
    model: 'fixed-model',
    region: 'CN-SH',
    collection_surface: 'consumer_web',
    question: `这个问题为什么会返回第 ${index} 条信源？`,
    retrieval_query: `目标品牌 能力比较 ${index}`,
    u_state: 'observed',
    fetch_state: blocked ? 'blocked' : 'succeeded',
    processing_state: blocked ? 'blocked' : index === 4 ? 'manual_evidence_required' : 'processed',
    entity_state: blocked ? 'pending' : 'validated',
    judgment_state: blocked ? 'pending' : 'completed',
    review_state: index === 1 ? 'accepted' : 'unreviewed',
    entered_judgment: index <= 3,
    finding_count: index <= 3 ? 1 : 0,
    retry_count: 0,
    failure_code: blocked ? 'source_blocked' : null,
    manual_evidence_state: blocked || index === 4 ? 'pending' : 'not_required',
    version: 1,
  };
}

function finding(index: number) {
  const quote = `目标品牌在关键能力上不如同业 ${index}`;
  return {
    finding_pub_id: `s2f_visual_${index}`,
    batch_pub_id: batch.batch_pub_id,
    corpus_item_pub_id: `s2i_visual_${index}`,
    occurrence_pub_id: `aso_visual_${index}`,
    snapshot_pub_id: `snp_visual_${index}`,
    canonical_url: `https://evidence.example.com/very-long-post-path/${index}`,
    ledger: 'statement',
    level: index === 3 ? 'L1' : 'L2b',
    relation_direction: index === 3 ? 'target_negative' : 'target_degraded',
    textual_speaker: `页面叙述者 ${index}`,
    target_entity: '目标品牌',
    beneficiary_entity: index === 3 ? null : '同业品牌',
    is_disparagement: index !== 3,
    fact_anchor_state: index === 3 ? 'present' : 'absent',
    evidence_quote: quote,
    quote_start: 12,
    quote_end: 12 + quote.length,
    context_text: `页面上下文：${quote}，该段落需要结合事实材料复核。`,
    context_start: 0,
    context_end: 80,
    snapshot_text_sha256: `${'a'.repeat(63)}${index}`,
    visual_anchor_pub_id: `anch_visual_${index}`,
    visual_evidence_pub_id: `evd_visual_${index}`,
    visual_bbox: [1, 1, 2, 2],
    visual_page_number: null,
    visual_validation_status: 'verified',
    flags: { secondary_position: index !== 3, direct_target_negative: index === 3 },
    comparison_dimensions: [],
    omitted_facts: [],
    method: 'human',
    policy_version: 'service2-entity-relation-v1',
    confidence: 0.9,
    validation_status: 'exact',
    validation_failures: [],
    publisher: { party: null, confidence: 'unknown', evidence: [] },
    commissioner: { party: null, confidence: 'unknown', evidence: [] },
    factcheck_claim: quote,
    factcheck_verdict: 'unverifiable',
    factcheck_evidence: [],
    factcheck_boundary: '只证明页面逐字存在，不证明内容为真。',
    current_review_state: index === 1 ? 'accepted' : 'unreviewed',
    version: 1,
    created_at: `2026-08-${String(index).padStart(2, '0')}T01:00:00Z`,
  };
}

async function installRoutes(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_service2_visual');
    localStorage.setItem('geo.session.actor', 'reviewer-service2-visual');
    localStorage.setItem('geo.session.role', 'reviewer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_service2_visual',
        user_pub_id: 'usr_service2_visual',
        role: 'reviewer',
        permissions: ['project:read', 'intelligence:read', 'intelligence:review'],
      }),
    }),
  );
  await page.route('**/api/v2/projects**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: [project], page: { next_cursor: null, has_more: false } }),
    }),
  );
  await page.route('**/api/v2/collection/runs**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'X-Has-More': 'false' },
      body: '[]',
    }),
  );
  await page.route('**/service2-source-corpus/**/analysis-models', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        default_model: 'gpt-5.6-luna',
        models: [
          {
            model: 'gpt-5.6-luna',
            label: 'GPT 5.6 Luna',
            provider: 'gpt',
            tier: 'economy',
            capability: '全量初筛与严格结构化抽取',
            web_search_mode: 'responses_web_search',
            input_usd_per_million_tokens: 0.2,
            output_usd_per_million_tokens: 1.2,
            context_window_tokens: 1050000,
            web_search_audit_status: 'verified_provider_citation',
            web_search_audited_at: '2026-08-25',
            auditable_source_mode: 'provider_citation',
            recommended: true,
            catalog_revision: 'service2-analysis-model-catalog-20260825.2',
            pricing_observed_at: '2026-08-25',
            pricing_source_url: 'https://api.inferera.com/api/v1/models?type=llm&sort_by=order',
            pricing_currency: 'USD',
            token_price_unit: 'per_million_tokens',
            web_search_usd_per_call: null,
            web_search_pricing_status: 'not_published_in_catalog_snapshot',
            pricing_notice: 'catalog_snapshot_provider_invoice_authoritative',
            web_search_audit_policy: 'provider_search_event_and_provider_citation_required',
          },
        ],
        credential_source: 'server_environment_only',
      }),
    }),
  );
  await page.route('**/service2-source-corpus/**/batches/current', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(batch) }),
  );
  await page.route('**/service2-source-corpus/**/items?*', (route) => {
    const cursor = new URL(route.request().url()).searchParams.get('cursor');
    const indexes = cursor ? [5] : [1, 2, 3, 4];
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        schema_version: 'internal-service2-corpus-items-v1',
        batch_pub_id: batch.batch_pub_id,
        data: indexes.map(corpusItem),
        filtered_count: 5,
        all_u_total: 5,
        next_cursor: cursor ? null : 'cursor-item-4',
        has_more: !cursor,
      }),
    });
  });
  await page.route('**/service2-source-corpus/**/findings?*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        schema_version: 'internal-service2-findings-v1',
        batch_pub_id: batch.batch_pub_id,
        data: [finding(1), finding(2), finding(3)],
        filtered_count: 3,
        all_findings_total: 3,
        next_cursor: null,
        has_more: false,
      }),
    }),
  );
  await page.route('**/api/v2/evidence/assets/*/content', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAFElEQVR4nGP8z8AARAwMjDAGAC0HAQAvsNYAAAAASUVORK5CYII=',
        'base64',
      ),
    }),
  );
}

test('service 2 keeps all-U coverage, local table overflow and evidence visible', async ({
  page,
}, testInfo) => {
  await installRoutes(page);
  await page.goto(`/platform/operations/service-outbound-risk?project=${project.pub_id}`);
  await page.getByRole('heading', { name: '1. 范围与覆盖' }).waitFor();

  await expect(page.getByText('全部 U occurrence').first()).toBeVisible();
  await expect(page.getByText(/当前筛选 5 \/ 全部 U 5/)).toBeVisible();
  await expect(page.getByText(/unknown · unknown/).first()).toBeVisible();
  await expect(page.getByRole('img', { name: /页面证据 evd_visual_1/ })).toBeVisible();

  const corpus = page.getByRole('region', { name: '全部 U 帖子表' });
  await expect(corpus.getByRole('row')).toHaveCount(5);
  await page.getByRole('button', { name: '下一页' }).first().click();
  await expect(corpus.getByRole('link', { name: /very-long-post-path\/5/ })).toBeVisible();

  const overflow = await page.evaluate(() => {
    const region = document.querySelector<HTMLElement>('[aria-label="全部 U 帖子表"]');
    return {
      root: document.documentElement.scrollWidth <= window.innerWidth + 1,
      tableIsLocal: Boolean(region && region.scrollWidth > region.clientWidth),
    };
  });
  expect(overflow.root).toBe(true);
  if ((page.viewportSize()?.width ?? 9999) <= 1024) expect(overflow.tableIsLocal).toBe(true);

  await expectAccessible(page);
  const screenshotPath = testInfo.outputPath('service2-all-u.png');
  await captureSafeScreenshot(page, {
    path: screenshotPath,
    fullPage: true,
    animations: 'disabled',
  });
  await testInfo.attach('service2-all-u', { path: screenshotPath, contentType: 'image/png' });
});
