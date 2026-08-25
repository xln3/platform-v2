// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { OutboundRiskWorkspace, service2BatchWindow } from './OutboundRiskWorkspace';

const session = {
  tenantId: 'tnt_service2',
  actorId: 'usr_reviewer',
  role: 'reviewer' as const,
  headers: {
    'X-Tenant-Id': 'tnt_service2',
    'X-Actor-Id': 'usr_reviewer',
    'X-Actor-Role': 'reviewer',
  },
};

const project = {
  pub_id: 'prj_service2',
  name: '全 U 项目',
  state: 'active',
  updated_at: '2026-08-24T00:00:00Z',
};

const batch = {
  schema_version: 'formal-service2-source-corpus-v2',
  batch_pub_id: 's2b_fixture',
  project_pub_id: project.pub_id,
  service_entitlement_pub_id: 'ent_service2',
  service_entitlement_revision: 'a'.repeat(64),
  run_pub_ids: ['run_a', 'run_b'],
  analysis_model: 'fast-model',
  window_start: '2026-08-01T00:00:00Z',
  window_end: '2026-08-24T00:00:00Z',
  source_snapshot_boundary: '2026-08-24T00:00:01Z',
  corpus_policy_version: 'service2-all-u-occurrence-v1',
  judgment_policy_version: 'service2-entity-relation-v1',
  status: 'review',
  version: 4,
  workflow_id: 'service2/s2b_fixture',
  frozen_at: null,
  manifest_hash: null,
  error_code: null,
  created_at: '2026-08-24T00:00:00Z',
  updated_at: '2026-08-24T00:10:00Z',
  coverage: {
    selected_queries: 10,
    successful_queries: 9,
    failed_queries: 1,
    successful_queries_with_u: 8,
    successful_queries_without_u: 1,
    query_failure_codes: { provider_timeout: 1 },
    query_outcomes_complete: true,
    query_coverage_complete: false,
    expected_occurrences: 9,
    materialized_items: 9,
    distinct_urls: 7,
    processing_states: { processed: 8, blocked: 1 },
    fetch_states: { succeeded: 8, blocked: 1 },
    entered_judgment: 8,
    findings: 9,
    reviewed_findings: 0,
    eligible_cases: 0,
    coverage_complete: true,
  },
};

function item(index: number) {
  return {
    item_pub_id: `s2i_${index}`,
    occurrence_pub_id: `occ_${index}`,
    run_pub_id: index < 6 ? 'run_a' : 'run_b',
    answer_pub_id: `ans_${index}`,
    source_url_pub_id: `url_${index}`,
    snapshot_pub_id: `snp_${index}`,
    source_document_pub_id: `srd_${index}`,
    fetch_attempt_pub_id: `fat_${index}`,
    raw_url: `https://source.example.com/post-${index}`,
    canonical_url: `https://source.example.com/post-${index}`,
    site_host: 'source.example.com',
    occurrence_ordinal: index,
    u_rank: index,
    captured_at: `2026-08-${String(index).padStart(2, '0')}T00:00:00Z`,
    platform: 'doubao',
    model: 'fixed-model',
    region: 'CN-SH',
    collection_surface: 'consumer_web',
    question: `问题 ${index}`,
    retrieval_query: `检索 ${index}`,
    u_state: 'observed',
    fetch_state: index === 9 ? 'blocked' : 'succeeded',
    processing_state: index === 9 ? 'blocked' : 'processed',
    entity_state: index === 9 ? 'pending' : 'validated',
    judgment_state: index === 9 ? 'pending' : 'completed',
    review_state: 'unreviewed',
    entered_judgment: index !== 9,
    finding_count: index === 9 ? 0 : 1,
    retry_count: 0,
    failure_code: index === 9 ? 'source_blocked' : null,
    manual_evidence_state: index === 9 ? 'pending' : 'not_required',
    version: 1,
  };
}

function finding(index: number) {
  const quote = `品牌 ${index} 被置于次级位置`;
  return {
    finding_pub_id: `s2f_${index}`,
    batch_pub_id: batch.batch_pub_id,
    corpus_item_pub_id: `s2i_${index}`,
    occurrence_pub_id: `occ_${index}`,
    snapshot_pub_id: `snp_${index}`,
    canonical_url: `https://source.example.com/post-${index}`,
    ledger: 'statement',
    level: 'L2b',
    relation_direction: 'target_degraded',
    textual_speaker: `作者 ${index}`,
    target_entity: `品牌 ${index}`,
    beneficiary_entity: `同业 ${index}`,
    is_disparagement: true,
    fact_anchor_state: 'absent',
    evidence_quote: quote,
    quote_start: 0,
    quote_end: quote.length,
    context_text: `${quote}，上下文可复核。`,
    context_start: 0,
    context_end: quote.length + 8,
    snapshot_text_sha256: String(index).padStart(64, '0'),
    visual_anchor_pub_id: `eva_${index}`,
    visual_evidence_pub_id: `evd_${index}`,
    visual_bbox: [10, 20, 300, 80],
    visual_page_number: null,
    visual_validation_status: 'verified',
    flags: { secondary_position: true },
    comparison_dimensions: [],
    omitted_facts: [],
    method: 'llm',
    policy_version: 'service2-entity-relation-v1',
    confidence: 0.9,
    validation_status: 'exact',
    validation_failures: [],
    publisher: { party: null, confidence: 'unknown', evidence: [] },
    commissioner: { party: null, confidence: 'unknown', evidence: [] },
    factcheck_claim: quote,
    factcheck_verdict: 'unverifiable',
    factcheck_evidence: [],
    factcheck_boundary: '仅核验页面逐字存在',
    current_review_state: 'unreviewed',
    version: 1,
    created_at: `2026-08-${String(index).padStart(2, '0')}T01:00:00Z`,
  };
}

function pageIndexes(cursor: string | null): number[] {
  if (cursor === 'cursor-4') return [5, 6, 7, 8];
  if (cursor === 'cursor-8') return [9];
  return [1, 2, 3, 4];
}

function cursorPage(cursor: string | null) {
  if (cursor === 'cursor-4') return { next_cursor: 'cursor-8', has_more: true };
  if (cursor === 'cursor-8') return { next_cursor: null, has_more: false };
  return { next_cursor: 'cursor-4', has_more: true };
}

function nextButton(index: number): HTMLElement {
  const button = screen.getAllByRole('button', { name: '下一页' })[index];
  if (!button) throw new Error(`missing pagination button ${index}`);
  return button;
}

describe('OutboundRiskWorkspace all-U service 2 projection', () => {
  const requestedUrls: string[] = [];

  beforeEach(() => {
    requestedUrls.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const request = input instanceof Request ? input : new Request(input);
        requestedUrls.push(request.url);
        const url = new URL(request.url);
        if (url.pathname.endsWith('/analysis-models')) {
          return Response.json({
            default_model: 'fast-model',
            models: [
              {
                model: 'fast-model',
                label: 'Fast Model',
                provider: 'fixture',
                tier: 'economy',
                capability: '全量初筛',
                web_search_mode: 'fixture_search',
                input_usd_per_million_tokens: 0.2,
                output_usd_per_million_tokens: 1.2,
                context_window_tokens: 1000000,
                recommended: true,
                pricing_observed_at: '2026-08-25',
                pricing_source_url: 'https://example.com/models',
                pricing_notice: 'catalog_snapshot_provider_invoice_authoritative',
              },
            ],
            credential_source: 'server_environment_only',
          });
        }
        if (url.pathname.endsWith('/batches/current')) {
          return Response.json(batch);
        }
        if (url.pathname === '/api/v2/collection/runs') {
          return Response.json([], { headers: { 'X-Has-More': 'false' } });
        }
        if (url.pathname.endsWith('/items')) {
          const cursor = url.searchParams.get('cursor');
          return Response.json({
            batch_pub_id: batch.batch_pub_id,
            data: pageIndexes(cursor).map(item),
            filtered_count: 9,
            all_u_total: 9,
            ...cursorPage(cursor),
          });
        }
        if (url.pathname.endsWith('/findings')) {
          const cursor = url.searchParams.get('cursor');
          return Response.json({
            batch_pub_id: batch.batch_pub_id,
            data: pageIndexes(cursor).map(finding),
            filtered_count: 9,
            all_findings_total: 9,
            ...cursorPage(cursor),
          });
        }
        throw new Error(`unexpected request: ${request.method} ${request.url}`);
      }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('uses one instant for a current-day window end and its frozen U boundary', () => {
    const now = new Date('2026-08-24T11:30:45.123Z');
    const result = service2BatchWindow({ start: '2026-08-01', end: '2026-08-24' }, now);

    expect(result).toEqual({
      windowStart: '2026-07-31T16:00:00.000Z',
      windowEnd: now.toISOString(),
      sourceSnapshotBoundary: now.toISOString(),
    });
    expect(Date.parse(result.sourceSnapshotBoundary)).toBeGreaterThanOrEqual(
      Date.parse(result.windowEnd),
    );
  });

  it('renders five governed sections and reaches the fifth and ninth rows by server cursor', async () => {
    render(<OutboundRiskWorkspace session={session} project={project} />);

    for (const heading of [
      '1. 范围与覆盖',
      '2. 全部帖子处理队列',
      '3. 实体—关系发现',
      '4. 待审核 finding',
      '5. 案例与交付',
    ]) {
      expect(await screen.findByRole('heading', { name: heading })).toBeTruthy();
    }
    expect(screen.getByText(/当前筛选 9 \/ 全部 U 9/)).toBeTruthy();
    expect(screen.getByText(/归属只是一列证据，不是入池门槛/)).toBeTruthy();

    const corpus = screen.getByRole('region', { name: '全部 U 帖子表' });
    expect(within(corpus).getAllByRole('row')).toHaveLength(5);
    fireEvent.click(nextButton(0));
    expect(await within(corpus).findByRole('link', { name: /post-5/ })).toBeTruthy();
    fireEvent.click(nextButton(0));
    expect(await within(corpus).findByRole('link', { name: /post-9/ })).toBeTruthy();
    expect(screen.getByText(/当前筛选 9 \/ 全部 U 9/)).toBeTruthy();

    const relations = screen.getByRole('region', { name: '实体关系 finding 表' });
    fireEvent.click(nextButton(1));
    expect(await within(relations).findByRole('button', { name: /作者 5 → 品牌 5/ })).toBeTruthy();
    fireEvent.click(nextButton(1));
    expect(await within(relations).findByRole('button', { name: /作者 9 → 品牌 9/ })).toBeTruthy();

    await waitFor(() => {
      const itemRequests = requestedUrls.filter(
        (value) => value.endsWith('/items') || value.includes('/items?'),
      );
      const findingRequests = requestedUrls.filter(
        (value) => value.endsWith('/findings') || value.includes('/findings?'),
      );
      expect(itemRequests.some((value) => value.includes('page_size=4'))).toBe(true);
      expect(itemRequests.some((value) => value.includes('cursor=cursor-8'))).toBe(true);
      expect(findingRequests.some((value) => value.includes('cursor=cursor-8'))).toBe(true);
    });
  });

  it('shows unknown attribution as a non-attribution boundary', async () => {
    render(<OutboundRiskWorkspace session={session} project={project} />);
    expect(await screen.findByText('独立归属')).toBeTruthy();
    expect(screen.getAllByText(/unknown · unknown/)).toHaveLength(2);
    expect(screen.getByText(/不得输出“竞品委托”“受雇”“水军”/)).toBeTruthy();
    expect(screen.getByText('absent')).toBeTruthy();
    expect(await screen.findByText(/可视证据不可读；该 finding 不能进入客户案例/)).toBeTruthy();
  });

  it('admits terminal partial runs per query and freezes the shared-selector model', async () => {
    localStorage.setItem('geo.ai.model.intake-research', 'deep-model');
    let createBody: Record<string, unknown> | null = null;
    const models = {
      default_model: 'fast-model',
      models: [
        {
          model: 'fast-model',
          label: 'Fast Model',
          provider: 'fixture-a',
          tier: 'economy',
          capability: '全量初筛',
          web_search_mode: 'fixture_search',
          input_usd_per_million_tokens: 0.2,
          output_usd_per_million_tokens: 1.2,
          context_window_tokens: 1000000,
          recommended: true,
          pricing_observed_at: '2026-08-25',
          pricing_source_url: 'https://example.com/models',
          pricing_notice: 'catalog_snapshot_provider_invoice_authoritative',
        },
        {
          model: 'deep-model',
          label: 'Deep Model',
          provider: 'fixture-b',
          tier: 'premium',
          capability: '复杂语义和事实核查',
          web_search_mode: 'fixture_search',
          input_usd_per_million_tokens: 5,
          output_usd_per_million_tokens: 25,
          context_window_tokens: 1000000,
          recommended: false,
          pricing_observed_at: '2026-08-25',
          pricing_source_url: 'https://example.com/models',
          pricing_notice: 'catalog_snapshot_provider_invoice_authoritative',
        },
      ],
      credential_source: 'server_environment_only',
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const request = input instanceof Request ? input : new Request(input, init);
        const url = new URL(request.url);
        if (url.pathname.endsWith('/analysis-models')) return Response.json(models);
        if (url.pathname.endsWith('/batches/current')) {
          return Response.json({ error: { code: 'service2_batch_not_found' } }, { status: 404 });
        }
        if (url.pathname === '/api/v2/collection/runs') {
          return Response.json(
            [
              {
                pub_id: 'run_partial',
                project_pub_id: project.pub_id,
                config_version_pub_id: 'cfg_1',
                workflow_id: 'collection/run_partial',
                state: 'completed_with_failures',
                total_tasks: 4,
                completed_tasks: 1,
                failed_tasks: 3,
                paused: false,
                error_code: 'partial_failure',
                source: 'manual',
                schedule_pub_id: null,
                retry_of_run_pub_id: null,
                initiated_by_pub_id: 'usr_operator',
                created_at: '2026-08-24T00:00:00Z',
                updated_at: '2026-08-24T01:00:00Z',
              },
              {
                pub_id: 'run_active',
                project_pub_id: project.pub_id,
                config_version_pub_id: 'cfg_1',
                workflow_id: 'collection/run_active',
                state: 'running',
                total_tasks: 4,
                completed_tasks: 1,
                failed_tasks: 0,
                paused: false,
                error_code: null,
                source: 'manual',
                schedule_pub_id: null,
                retry_of_run_pub_id: null,
                initiated_by_pub_id: 'usr_operator',
                created_at: '2026-08-24T00:00:00Z',
                updated_at: '2026-08-24T01:00:00Z',
              },
            ],
            { headers: { 'X-Has-More': 'false' } },
          );
        }
        if (url.pathname.endsWith('/batches') && request.method === 'POST') {
          createBody = (await request.json()) as Record<string, unknown>;
          return Response.json(
            { ...batch, analysis_model: 'deep-model', status: 'draft' },
            { status: 201 },
          );
        }
        if (url.pathname.endsWith('/items')) {
          return Response.json({
            batch_pub_id: batch.batch_pub_id,
            data: [],
            filtered_count: 0,
            all_u_total: 9,
            next_cursor: null,
            has_more: false,
          });
        }
        if (url.pathname.endsWith('/findings')) {
          return Response.json({
            batch_pub_id: batch.batch_pub_id,
            data: [],
            filtered_count: 0,
            all_findings_total: 0,
            next_cursor: null,
            has_more: false,
          });
        }
        throw new Error(`unexpected request: ${request.method} ${request.url}`);
      }),
    );

    render(
      <OutboundRiskWorkspace
        session={{
          ...session,
          role: 'operator',
          headers: { ...session.headers, 'X-Actor-Role': 'operator' },
        }}
        project={project}
      />,
    );

    const partial = await screen.findByRole('checkbox', { name: /run_partial/ });
    const active = screen.getByRole('checkbox', { name: /run_active/ });
    expect((partial as HTMLInputElement).disabled).toBe(false);
    await waitFor(() => expect((partial as HTMLInputElement).checked).toBe(true));
    expect((active as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/completed_with_failures.*成功 1.*失败 3.*总计 4/)).toBeTruthy();

    const service2Model = screen.getByRole('combobox', {
      name: '主动拉踩内容联网分析模型选择',
    }) as HTMLSelectElement;
    expect(service2Model.value).toBe('fast-model');
    expect(localStorage.getItem('geo.ai.model.intake-research')).toBe('deep-model');
    fireEvent.change(service2Model, { target: { value: 'deep-model' } });
    fireEvent.click(screen.getByRole('button', { name: '物化全部 U occurrence' }));

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({
      run_pub_ids: ['run_partial'],
      analysis_model: 'deep-model',
    });
  });
});
