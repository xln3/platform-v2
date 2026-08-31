// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BrandVisibilityRow, OfficialMetricSnapshotSet, Project } from '../api';
import { VisibilityWorkspace } from './VisibilityWorkspace';

const session = {
  tenantId: 'tnt_test',
  actorId: 'usr_test',
  role: 'operator' as const,
  headers: {
    'X-Tenant-Id': 'tnt_test',
    'X-Actor-Id': 'subject_test',
    'X-Actor-Role': 'operator',
  },
};

const project: Project = {
  pub_id: 'prj_test',
  name: '测试项目',
  state: 'active',
  updated_at: '2026-08-10T00:00:00Z',
  brandrank_domain: 'cybersecurity',
};

describe('VisibilityWorkspace', () => {
  const requestedUrls: string[] = [];
  let snapshot = officialSnapshot(['brd_own', 'cmp_a']);
  let brandRows: BrandVisibilityRow[] = [];
  let entities = [
    { pub_id: 'brd_own', resource_kind: 'brands', data: { name: '盛邦安全' } },
    { pub_id: 'cmp_a', resource_kind: 'competitors', data: { name: '奇安信' } },
  ];

  beforeEach(() => {
    requestedUrls.length = 0;
    snapshot = officialSnapshot(['brd_own', 'cmp_a']);
    brandRows = [
      {
        rank: 1,
        brand: '盛邦安全',
        score: 295.534,
        avg_rank: 2.52,
        occurrences: 744,
        appearance_rate: 56.62,
        industry_fit: 'core_cybersecurity',
      },
      {
        rank: 2,
        brand: '奇安信',
        score: 216.587,
        avg_rank: 3.1,
        occurrences: 672,
        appearance_rate: 51.14,
      },
    ];
    entities = [
      { pub_id: 'brd_own', resource_kind: 'brands', data: { name: '盛邦安全' } },
      { pub_id: 'cmp_a', resource_kind: 'competitors', data: { name: '奇安信' } },
    ];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = input instanceof Request ? input.url : String(input);
        requestedUrls.push(url);
        if (url.includes('/semantic-backfill/options')) {
          return json({
            schema_version: 'semantic-backfill-options-v2',
            project_pub_id: 'prj_test',
            as_of: '2026-08-28T00:00:00Z',
            candidate_count: 1330,
            next_cursor: 'next-page',
            max_batch_size: 100,
            default_model: 'glm-5.3-flash',
            models: [
              {
                model: 'glm-5.3-flash',
                label: 'GLM 5.3 Flash',
                provider: 'Z.AI',
                tier: 'economy',
                input_usd_per_million_tokens: 0.11268,
                output_usd_per_million_tokens: 0.39438,
                context_window_tokens: 1000000,
                recommended: true,
                catalog_revision: 'catalog-test',
                pricing_observed_at: '2026-08-28',
                pricing_source_url: 'https://example.test/models',
                pricing_currency: 'USD',
                token_price_unit: 'per_million_tokens',
                pricing_notice: 'catalog_snapshot_provider_invoice_authoritative',
              },
              {
                model: 'gpt-5.6-sol',
                label: 'GPT 5.6 Sol',
                provider: 'OpenAI-compatible',
                tier: 'premium',
                input_usd_per_million_tokens: 4,
                output_usd_per_million_tokens: 20,
                context_window_tokens: 1050000,
                recommended: false,
                catalog_revision: 'catalog-test',
                pricing_observed_at: '2026-08-28',
                pricing_source_url: 'https://example.test/models',
                pricing_currency: 'USD',
                token_price_unit: 'per_million_tokens',
                pricing_notice: 'catalog_snapshot_provider_invoice_authoritative',
              },
            ],
            candidates: [
              semanticCandidate('ans_one', '如何选择网络安全厂商？'),
              semanticCandidate('ans_two', '盛邦安全有哪些优势？'),
            ],
          });
        }
        if (url.includes('/semantic-backfill/plan')) {
          return json({
            schema_version: 'semantic-backfill-plan-v2',
            project_pub_id: 'prj_test',
            model: 'glm-5.3-flash',
            as_of: '2026-08-28T00:00:00Z',
            window: { start: '2026-08-10', end: '2026-08-10' },
            focal_entity_ids: ['brd_own', 'cmp_a'],
            selected_answer_count: 2,
            executable_answer_count: 2,
            preparation_unknown_count: 0,
            estimated_atomic_decisions: 68,
            estimated_input_tokens: 200000,
            estimated_output_tokens: 30600,
            estimated_cost_usd: 0.0346,
            estimated_cost_high_usd: 0.0612,
            budget_limit_usd: 100,
            selection_hash: 'a'.repeat(64),
            confirmation_token: 'b'.repeat(64),
            start_allowed: true,
            blocker_codes: [],
            estimate_notice: 'bounded_estimate_provider_invoice_authoritative',
          });
        }
        if (url.includes('/semantic-backfill/start')) {
          return json({
            schema_version: 'semantic-backfill-start-v2',
            project_pub_id: 'prj_test',
            workflow_id: `metrics-v2-backfill/semantic/${'a'.repeat(64)}`,
            job_pub_id: 'sdb_started',
            selection_hash: 'a'.repeat(64),
            status: 'started',
            selected_answer_count: 2,
            model: 'glm-5.3-flash',
          });
        }
        if (url.includes('/semantic-backfill/status/')) {
          return json({
            schema_version: 'semantic-backfill-status-v2',
            project_pub_id: 'prj_test',
            selection_hash: 'a'.repeat(64),
            workflow_id: `metrics-v2-backfill/semantic/${'a'.repeat(64)}`,
            status: 'succeeded',
            processed_answer_count: 2,
            metric_evaluation_count: 68,
            snapshot_set_pub_id: 'mss_backfill',
            failure_code: null,
          });
        }
        if (url.includes('/snapshot-sets/mss_backfill')) return json(snapshot);
        if (url.includes('/snapshot-sets/current')) return json(snapshot);
        if (url.includes('/metrics/catalog')) return json(metricCatalog());
        if (url.includes('/analytics/competitors')) {
          return json([
            {
              competitor: '奇安信',
              mention_rate: 0.145,
              mention_count: 193,
              answer_count: 1330,
              average_rank: 6.27,
              top1_rate: 0.01,
              top3_rate: 0.03,
              top10_rate: 0.1,
            },
          ]);
        }
        if (url.includes('/brand-visibility')) {
          return json({
            project_pub_id: 'prj_test',
            window_days: 30,
            domain: 'cybersecurity',
            result: {
              overall: { merged: brandRows },
              entity_resolution: {
                mode: 'governed_hybrid_v2',
                counts: { alias_collapses_within_answers: 3, unclassified_distinct_names: 2 },
              },
            },
          });
        }
        if (url.includes('/resources/brands')) {
          return json(entities.filter((row) => row.resource_kind === 'brands'));
        }
        if (url.includes('/resources/competitors')) {
          return json(entities.filter((row) => row.resource_kind === 'competitors'));
        }
        if (url.includes('/collection/runs/summary')) {
          return json({
            project_pub_id: 'prj_test',
            run_count: 3,
            active_run_count: 0,
            total_tasks: 30,
            completed_tasks: 30,
            failed_tasks: 0,
          });
        }
        if (url.includes('/collection/runs?')) {
          return new Response(JSON.stringify([run(1), run(2)]), {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
              'X-Page': '1',
              'X-Page-Size': '2',
              'X-Total-Count': '3',
              'X-Page-Count': '2',
              'X-Has-More': 'true',
            },
          });
        }
        if (url.includes('/analytics/sampling-progress')) {
          return json({
            project_pub_id: 'prj_test',
            config_revision_start: null,
            config_revision_end: null,
            columns: [],
            rows: [],
            page: { page: 1, page_size: 4, total_count: 0, total_pages: 0 },
            observed_cells: 0,
            total_cells: 0,
            answer_count: 0,
            latest_capture_time: null,
            live_runs: 0,
          });
        }
        return json([]);
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('renders the full observed ranking and labels the small official snapshot as migration progress', async () => {
    render(<VisibilityWorkspace session={session} project={project} />);

    await screen.findByText(/当前 official 快照只完成 1\/34 项指标/);
    expect(screen.getAllByText('自然提及率').length).toBeGreaterThan(0);
    expect(screen.getAllByText('盛邦安全').length).toBeGreaterThan(0);
    expect(screen.getAllByText('奇安信').length).toBeGreaterThan(0);
    expect(screen.getByRole('table', { name: '品牌可见度榜单' })).toBeTruthy();
    expect(screen.getByText(/仅纳入 1 份答案/)).toBeTruthy();
    expect(screen.getByText('历史回算')).toBeTruthy();
    expect(screen.getByText('未完成')).toBeTruthy();
    expect(screen.getByText('未计算不等于 0')).toBeTruthy();
    expect(screen.getByText('品牌榜排名')).toBeTruthy();
    expect(screen.getByText('#1')).toBeTruthy();
    expect(screen.getByRole('img', { name: '品牌可见度 Top 10 横条图' })).toBeTruthy();
    expect(screen.getByText(/榜单按已审核品牌家族实体归并/)).toBeTruthy();
    expect(screen.getAllByText('295.534')).toHaveLength(3);
    expect(screen.queryByText('品牌榜单暂不可用')).toBeNull();

    fireEvent.click(screen.getByText(/技术诊断明细/));
    expect(screen.getByRole('table', { name: 'official V2 当前指标明细' })).toBeTruthy();

    const officialRequest = requestedUrls.find((url) => url.includes('/snapshot-sets/current'));
    expect(officialRequest).toBeTruthy();
    expect(new URL(officialRequest!).searchParams.get('publication_channel')).toBe('official');
    expect(requestedUrls.some((url) => /\/brand-visibility(?:\?|$)/u.test(url))).toBe(true);
  });

  it('plans and starts a bounded model-selected semantic backfill from the operations page', async () => {
    render(<VisibilityWorkspace session={session} project={project} />);
    await screen.findByText(/当前 official 快照只完成 1\/34 项指标/);

    fireEvent.click(screen.getByRole('button', { name: '启动 official V2 回算' }));
    const dialog = await screen.findByRole('dialog', { name: 'official V2 历史回算控制台' });
    const model = (await within(dialog).findByRole('combobox', {
      name: '判定模型',
    })) as HTMLSelectElement;
    expect(model.value).toBe('glm-5.3-flash');
    expect(within(dialog).getByText(/候选答案 1330 份/)).toBeTruthy();
    expect(within(dialog).getByLabelText('可纳入回算的问答滚动列表')).toBeTruthy();
    expect(await within(dialog).findByText('68')).toBeTruthy();
    expect(within(dialog).getByText('$0.0346 / $0.0612')).toBeTruthy();

    fireEvent.click(within(dialog).getByRole('button', { name: '检查完成，进入二次确认' }));
    expect(within(dialog).getByText(/确认启动 2 份答案/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: '确认启动回算' }));
    expect(await within(dialog).findByText(/回算完成：2 份答案，生成 68 条指标评价/)).toBeTruthy();
    expect(within(dialog).getByRole('table', { name: '本次 V2 回算快照指标' })).toBeTruthy();
    expect(requestedUrls.some((url) => url.includes('/semantic-backfill/start'))).toBe(true);
  });

  it('shows ten official entities by default and lets the operator change the page size', async () => {
    const ids = ['brd_own', ...Array.from({ length: 20 }, (_, index) => `cmp_${index + 1}`)];
    snapshot = officialSnapshot(ids);
    brandRows = ids.map((_, index) => ({
      rank: index + 1,
      brand: index === 0 ? '盛邦安全' : `品牌${index}`,
      score: 100 - index,
      avg_rank: index + 1,
      occurrences: 100 - index,
      appearance_rate: 50 - index,
    }));
    entities = ids.map((pub_id, index) => ({
      pub_id,
      resource_kind: index === 0 ? 'brands' : 'competitors',
      data: { name: index === 0 ? '盛邦安全' : `品牌${index}` },
    }));

    render(<VisibilityWorkspace session={session} project={project} />);
    const pageSizeSelect = (await screen.findByRole('combobox', {
      name: '品牌可见度榜单每页显示数量',
    })) as HTMLSelectElement;
    expect(pageSizeSelect.value).toBe('10');

    const table = screen.getByRole('table', { name: '品牌可见度榜单' });
    expect(within(table).getAllByRole('row')).toHaveLength(11);
    const pagination = screen.getByRole('navigation', { name: '品牌可见度榜单分页' });
    expect(within(pagination).getByText('第 1 / 3 页')).toBeTruthy();
    fireEvent.click(within(pagination).getByRole('button', { name: '下一页' }));
    expect(await within(pagination).findByText('第 2 / 3 页')).toBeTruthy();

    fireEvent.change(pageSizeSelect, { target: { value: '20' } });
    await waitFor(() => expect(within(pagination).getByText('第 1 / 2 页')).toBeTruthy());
    expect(within(table).getAllByRole('row')).toHaveLength(21);
  });

  it('keeps the full observed ranking visible when the official migration snapshot is unavailable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = input instanceof Request ? input.url : String(input);
        requestedUrls.push(url);
        if (url.includes('/snapshot-sets/current')) return json({}, 404);
        if (url.includes('/brand-visibility')) {
          return json({
            project_pub_id: 'prj_test',
            window_days: 30,
            domain: 'cybersecurity',
            result: { overall: { merged: brandRows } },
          });
        }
        return json([]);
      }),
    );

    render(<VisibilityWorkspace session={session} project={project} />);
    await screen.findByText('official 快照暂不可用。');
    expect(await screen.findByRole('table', { name: '品牌可见度榜单' })).toBeTruthy();
    expect(requestedUrls.some((url) => /\/brand-visibility(?:\?|$)/u.test(url))).toBe(true);
  });
});

function semanticCandidate(answerPubId: string, queryText: string) {
  return {
    answer_pub_id: answerPubId,
    query_text: queryText,
    model: 'doubao',
    region: '北京',
    mode: 'deep',
    channel: 'web',
    capture_time: '2026-08-10T00:00:00Z',
    preparation_state: 'ready',
    reason_codes: [],
  };
}

function officialSnapshot(entityIds: string[]): OfficialMetricSnapshotSet {
  return {
    schema_version: 'metric-snapshot-set-v2',
    snapshot_set_pub_id: 'mss_test',
    snapshot_set_hash: 'a'.repeat(64),
    project_pub_id: 'prj_test',
    state: 'ready',
    as_of: '2026-08-28T08:00:00Z',
    window: { start: '2026-08-10', end: '2026-08-10' },
    filters: { model: ['deepseek'], region: ['上海'], mode: ['deep_think'] },
    focal_entity_ids: entityIds,
    metrics: entityIds.map((focal_entity_id, index) => ({
      snapshot_pub_id: `mts_${index}`,
      focal_entity_id,
      metric_name: 'ai_recommendation_organic_mention_rate_v2',
      metric_version: '2.1.0',
      state: 'ready',
      value: index / 100,
      raw_numerator: index,
      raw_denominator: 100,
      coverage: { known: 1, unknown: 0, total: 1, ratio: 1 },
      unique_query_count: 1,
      known_answer_count: 1,
      unknown_answer_count: 0,
    })),
  };
}

function metricCatalog() {
  return {
    schema_version: 'metric-catalog-v2',
    definitions: Array.from({ length: 34 }, (_, index) => ({
      metric_name:
        index === 0 ? 'ai_recommendation_organic_mention_rate_v2' : `experimental_metric_${index}`,
      metric_version: '2.1.0',
      status: index === 0 ? 'published' : 'experimental',
      required_semantic_capabilities: ['substantive_entity_mention'],
    })),
  };
}

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function run(index: number) {
  return {
    pub_id: `run_${index}`,
    project_pub_id: 'prj_test',
    config_version_pub_id: 'cfv_test',
    workflow_id: `geo-collection/${index}`,
    temporal_run_id: null,
    state: 'completed',
    total_tasks: 10,
    completed_tasks: 10,
    failed_tasks: 0,
    paused: false,
    error_code: null,
    source: 'manual',
    schedule_pub_id: null,
    retry_of_run_pub_id: null,
    initiated_by_pub_id: 'usr_test',
    created_at: `2026-08-${String(24 - index).padStart(2, '0')}T00:00:00Z`,
    updated_at: `2026-08-${String(24 - index).padStart(2, '0')}T01:00:00Z`,
  };
}
