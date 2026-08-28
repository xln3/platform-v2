import type { Page } from '@playwright/test';

export type CustomerDashboardMetricFixture = {
  code: string;
  label: string;
  group: string;
  format: 'percentage' | 'score' | 'rank' | 'count' | 'decimal';
  direction: 'higher' | 'lower' | 'neutral';
  value: number;
  state: 'ready';
  version: 'customer-metrics-v1';
};

export const customerDashboardMetric = (
  code: string,
  label: string,
  group: string,
  format: CustomerDashboardMetricFixture['format'],
  value: number,
  direction: CustomerDashboardMetricFixture['direction'] = 'higher',
): CustomerDashboardMetricFixture => ({
  code,
  label,
  group,
  format,
  direction,
  value,
  state: 'ready',
  version: 'customer-metrics-v1',
});

const dimensionMetrics = (mentionRate = 0.5): CustomerDashboardMetricFixture[] => [
  customerDashboardMetric('mention_rate', '品牌提及率', 'visibility', 'percentage', mentionRate),
  customerDashboardMetric('top3_rate', 'Top3 率', 'ranking', 'percentage', 0.5),
  customerDashboardMetric('average_rank', '平均排名', 'ranking', 'rank', 2, 'lower'),
  customerDashboardMetric('recommendation_rate', '品牌推荐率', 'visibility', 'percentage', 0.625),
  customerDashboardMetric('citation_coverage', '引用覆盖率', 'source', 'percentage', 0.5),
];

export function buildCustomerDashboardFixture(
  projectPubId: string,
  options: { brandName?: string; mentionRate?: number; model?: string } = {},
) {
  const brandName = options.brandName ?? '云岫智能';
  const mentionRate = options.mentionRate ?? 0.5;
  const model = options.model ?? 'DeepSeek';
  const dimensions = dimensionMetrics(mentionRate);
  const metrics = [
    customerDashboardMetric('geo_visibility_index', 'GEO 可见度指数', 'composite', 'score', 75),
    customerDashboardMetric('competitive_power_index', '竞争力指数', 'composite', 'score', 68),
    customerDashboardMetric('source_authority_index', '信源权威指数', 'composite', 'score', 71),
    customerDashboardMetric('content_readiness_index', '内容准备度指数', 'composite', 'score', 64),
    customerDashboardMetric('reputation_index', 'AI 口碑指数', 'composite', 'score', 79),
    customerDashboardMetric(
      'cognition_consistency_index',
      'AI 认知一致性指数',
      'composite',
      'score',
      73,
    ),
    customerDashboardMetric('answer_count', '已分析回答', 'visibility', 'count', 240, 'neutral'),
    customerDashboardMetric('mention_count', '品牌提及回答', 'visibility', 'count', 120),
    customerDashboardMetric('query_count', '覆盖问题数', 'visibility', 'count', 48, 'neutral'),
    customerDashboardMetric('model_count', '覆盖模型数', 'visibility', 'count', 1, 'neutral'),
    customerDashboardMetric('region_count', '覆盖地区数', 'visibility', 'count', 1, 'neutral'),
    customerDashboardMetric(
      'observation_day_count',
      '有效观察日',
      'visibility',
      'count',
      5,
      'neutral',
    ),
    ...dimensions,
    customerDashboardMetric('top1_rate', 'Top1 率', 'ranking', 'percentage', 0.25),
    customerDashboardMetric('ranked_answer_rate', '有效排名覆盖率', 'ranking', 'percentage', 0.75),
    customerDashboardMetric('share_of_voice', '竞争声量份额', 'competition', 'percentage', 0.4),
    customerDashboardMetric(
      'own_source_answer_rate',
      '官网引用回答率',
      'source',
      'percentage',
      0.25,
    ),
    customerDashboardMetric('unique_source_hosts', '独立信源网站', 'source', 'count', 126),
    customerDashboardMetric('unique_source_pages', '独立信源页面', 'source', 'count', 1842),
    customerDashboardMetric('citation_references', '引用总次数', 'source', 'count', 3426),
    customerDashboardMetric('source_audit_count', '已完成信源审计', 'content', 'count', 96),
    customerDashboardMetric('source_accuracy_rate', '信源准确率', 'content', 'percentage', 0.875),
    customerDashboardMetric('positive_rate', '正面回答率', 'reputation', 'percentage', 0.575),
    customerDashboardMetric('risk_judgment_count', '风险判断数', 'risk', 'count', 42, 'neutral'),
    customerDashboardMetric(
      'disparagement_rate',
      '品牌贬损率',
      'risk',
      'percentage',
      0.048,
      'lower',
    ),
    customerDashboardMetric('support_rate', '品牌支持率', 'risk', 'percentage', 0.619),
  ];
  return {
    schema_version: 'customer-dashboard-v1',
    metric_version: 'customer-metrics-v1',
    project_pub_id: projectPubId,
    brand_name: brandName,
    state: 'ready',
    generated_at: '2026-08-17T08:00:00Z',
    as_of: '2026-08-17T07:45:00Z',
    window: { start: '2026-07-19', end: '2026-08-17', filters: {} },
    metrics,
    models: [{ key: model, label: model, metrics: dimensions }],
    competitors: [
      {
        name: '北辰智库',
        metrics: [
          customerDashboardMetric(
            'share_of_voice',
            '竞争声量份额',
            'competition',
            'percentage',
            0.3,
          ),
        ],
      },
    ],
    questions: [
      {
        query_pub_id: 'qry_customer_dashboard_fixture',
        query_text: '制造企业如何选择可信的私有化知识库？',
        query_group: '采购选型',
        metrics: dimensions,
      },
    ],
    sources: [
      { host: 'example.com', references: 48, share: 0.5, own_source: false, answers: 39 },
      { host: 'brand.example.cn', references: 36, share: 0.375, own_source: true, answers: 31 },
    ],
    regions: [{ key: '华东', label: '华东', metrics: dimensions }],
    modes: [{ key: '深度回答', label: '深度回答', metrics: dimensions }],
    trends: ['2026-08-13', '2026-08-14', '2026-08-15', '2026-08-16', '2026-08-17'].map(
      (date, index) => ({
        date,
        metrics: dimensionMetrics(Math.min(1, mentionRate - 0.08 + index * 0.02)),
      }),
    ),
    risk: {
      metrics: metrics.filter((metric) => metric.group === 'risk'),
      by_model: [{ key: model, label: model, metrics: dimensions }],
    },
    source_audit: {
      metrics: metrics.filter((metric) => metric.group === 'content'),
      verdicts: { accurate: 84, unsupported: 7, unverifiable: 5 },
    },
    snapshot_hash: 'f'.repeat(64),
  };
}

export type CustomerDashboardV2BusinessView = 'ai_impression' | 'ai_recommendation';
export type CustomerDashboardV2ExposureRole =
  | 'brand_neutral'
  | 'focal_named_only'
  | 'other_brand_named'
  | 'focal_named_with_others';

const fixtureHash = (character: string) => character.repeat(64);

export function buildCustomerDashboardV2Fixture(
  projectPubId: string,
  options: {
    businessView: CustomerDashboardV2BusinessView;
    exposureRole: CustomerDashboardV2ExposureRole;
    metricNames: string[];
    start: string;
    end: string;
    models?: string[];
    regions?: string[];
    modes?: string[];
    value?: number;
  },
) {
  const metrics = options.metricNames.map((metricName) => {
    const meanRank = metricName.endsWith('mean_rank_given_target_ranked_v2');
    const value = meanRank ? 3 : (options.value ?? 0.5);
    return {
      snapshot_pub_id: `msn_${metricName}`,
      snapshot_hash: fixtureHash('e'),
      focal_entity_id: 'ent_customer_brand',
      metric_name: metricName,
      metric_version: '2.0.0',
      metric_definition_hash: fixtureHash('d'),
      state: 'ready' as const,
      state_reason_codes: [],
      value,
      observed_value: value,
      answer_weighted_value: value,
      raw_numerator: meanRank ? 3 : 2,
      raw_denominator: meanRank ? 1 : 4,
      weighted_numerator: meanRank ? 3 : 0.5,
      weighted_denominator: 1,
      coverage: {
        collection: 1,
        query_context: 1,
        semantic: 1,
        evidence: 1,
        semantic_by_capability: { recommendation_relation: 1 },
      },
      decision_method_mix: { model: 1 },
      adjudication_sensitivity: { lower: value, upper: value },
      missing_bounds: { lower: value, upper: value },
      unique_query_count: 4,
      candidate_answer_count: 4,
      known_answer_count: 4,
      unknown_answer_count: 0,
      not_applicable_answer_count: 0,
      excluded_answer_count: 0,
      design_cell_count: 4,
      contribution_set_hash: fixtureHash('1'),
      query_contribution_set_hash: fixtureHash('2'),
      design_contribution_set_hash: fixtureHash('3'),
      label: metricName,
      business_view: options.businessView,
      exposure_role: options.exposureRole,
      aggregation_method: 'query_macro' as const,
      definition: {
        business_question: `如何验证 ${metricName}？`,
        denominator_description: '满足当前业务视角与品牌暴露 cohort 的语义已知回答。',
        outcome_source: 'hybrid' as const,
        query_predicate: {
          analysis_lens: options.businessView,
          exposure_role: options.exposureRole,
        },
        outcome_expression: { accepted_event: 'recommendation_relation' },
        required_semantic_capabilities: ['recommendation_relation'],
        decision_task_refs: [{ task: 'recommendation_relation_v2', version: '2.0.0' }],
        semantic_rubric_ref: 'rubric://recommendation-relation/v2',
      },
    };
  });
  return {
    schema_version: 'customer-dashboard-v2' as const,
    project_pub_id: projectPubId,
    brand_name: '云岫智能',
    business_view: options.businessView,
    exposure_role: options.exposureRole,
    publication_channel: 'official' as const,
    requested_metric_names: options.metricNames,
    focal_entity_id: 'ent_customer_brand',
    snapshot_set_pub_id: 'mss_customer_dashboard_fixture',
    snapshot_set_hash: fixtureHash('a'),
    state: 'ready' as const,
    as_of: '2026-08-17T08:00:00Z',
    window: { start: options.start, end: options.end },
    filters: {
      model: options.models ?? [],
      region: options.regions ?? [],
      mode: options.modes ?? [],
    },
    aggregation_method: 'query_macro' as const,
    design_basis: 'planned_cells' as const,
    scope_hash: fixtureHash('b'),
    dependency_bundle_hash: fixtureHash('c'),
    metrics,
  };
}

export function buildCustomerDashboardV2FixtureFromUrl(
  url: URL,
  overrides: { metricNames?: string[]; value?: number } = {},
) {
  const pathSegments = url.pathname.split('/');
  const dashboardV2Index = pathSegments.indexOf('dashboard-v2');
  const projectPubId = pathSegments[dashboardV2Index - 1] ?? '';
  const businessView =
    url.searchParams.get('business_view') === 'ai_impression'
      ? 'ai_impression'
      : 'ai_recommendation';
  const rawExposureRole = url.searchParams.get('exposure_role');
  const exposureRole: CustomerDashboardV2ExposureRole =
    rawExposureRole === 'focal_named_only' ||
    rawExposureRole === 'other_brand_named' ||
    rawExposureRole === 'focal_named_with_others'
      ? rawExposureRole
      : 'brand_neutral';
  return buildCustomerDashboardV2Fixture(projectPubId, {
    businessView,
    exposureRole,
    metricNames: overrides.metricNames ?? url.searchParams.getAll('metric_name'),
    start: url.searchParams.get('start') ?? '2026-07-19',
    end: url.searchParams.get('end') ?? '2026-08-17',
    models: url.searchParams.getAll('model'),
    regions: url.searchParams.getAll('region'),
    modes: url.searchParams.getAll('mode'),
    ...(overrides.value === undefined ? {} : { value: overrides.value }),
  });
}

export function buildCustomerMetricTraceV2Fixture(
  dashboard: ReturnType<typeof buildCustomerDashboardV2Fixture>,
  snapshotPubId: string,
) {
  const metric = dashboard.metrics.find((item) => item.snapshot_pub_id === snapshotPubId);
  if (!metric) throw new Error(`unknown customer metric snapshot: ${snapshotPubId}`);
  const answerDetailHref =
    `/api/v2/customer-dashboard/projects/${dashboard.project_pub_id}` +
    `/answer-library/answers/ans_customer_trace_01?` +
    new URLSearchParams({
      metric_snapshot_set_pub_id: dashboard.snapshot_set_pub_id,
      metric_snapshot_set_hash: dashboard.snapshot_set_hash,
      snapshot_at: dashboard.as_of,
      start: dashboard.window.start,
      end: dashboard.window.end,
    }).toString();
  return {
    schema_version: 'customer-metric-trace-v2' as const,
    project_pub_id: dashboard.project_pub_id,
    snapshot_set_pub_id: dashboard.snapshot_set_pub_id,
    snapshot_set_hash: dashboard.snapshot_set_hash,
    as_of: dashboard.as_of,
    metric,
    contributions: {
      schema_version: 'metric-contributions-v2' as const,
      snapshot_pub_id: metric.snapshot_pub_id,
      totals: {
        snapshot_candidate_count: metric.candidate_answer_count,
        filtered_count: metric.candidate_answer_count,
        raw_numerator: metric.raw_numerator,
        raw_denominator: metric.raw_denominator,
        weighted_numerator: metric.weighted_numerator,
        weighted_denominator: metric.weighted_denominator,
        contribution_set_hash: metric.contribution_set_hash,
      },
      data: [
        {
          answer_pub_id: 'ans_customer_trace_01',
          query_pub_id: 'qry_customer_trace_01',
          query_key: 'query-customer-trace-01',
          query_text: '请推荐可信的企业知识库服务商。',
          analysis_lenses: [dashboard.business_view],
          requested_operations: ['recommend'],
          exposure_role: dashboard.exposure_role,
          model: 'DeepSeek',
          region: '华东',
          mode: '深度回答',
          capture_time: '2026-08-16T08:00:00Z',
          eligibility_status: 'included_hit' as const,
          reason_codes: ['accepted_recommendation_relation'],
          outcome_value: true,
          numerator_contribution: 1,
          denominator_contribution: 1,
          query_weight: 0.25,
          design_cell_weight: 1,
          repeat_weight: 1,
          final_weight: 0.25,
          weighted_numerator: 0.25,
          weighted_denominator: 0.25,
          semantic_manifest_pub_id: 'smf_customer_trace_01',
          supporting_events: [
            {
              event_pub_id: 'sev_customer_trace_01',
              event_type: 'recommendation_relation',
              subject_entity_id: 'ent_customer_brand',
              object_entity_id: null,
              event_value: { stance: 'positive' },
              answer_text_start: 0,
              answer_text_end: 12,
              answer_excerpt: '云岫智能值得优先考虑。',
            },
          ],
          supporting_decisions: [
            {
              decision_pub_id: 'sdr_customer_trace_01',
              task: 'recommendation_relation_v2',
              version: '2.0.0',
              method: 'model' as const,
              status: 'accepted' as const,
              calibrated_confidence: 0.96,
              rubric_hash: fixtureHash('4'),
              evidence_refs: [{ event_pub_id: 'sev_customer_trace_01' }],
              rationale_summary: '回答明确把焦点品牌作为正向推荐候选。',
            },
          ],
          answer_excerpt: '云岫智能值得优先考虑。',
          answer_detail_href: answerDetailHref,
          contribution_hash: fixtureHash('5'),
        },
      ],
      next_cursor: null,
      has_more: false,
    },
  };
}

export async function installDefaultCustomerDashboardRoutes(page: Page): Promise<void> {
  await page.route('**/api/v2/customer-dashboard/metrics/catalog**', (route) => {
    const fixture = buildCustomerDashboardFixture('prj_customer_dashboard_catalog');
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        schema_version: 'customer-metric-catalog-v1',
        metrics: fixture.metrics.map(({ value: _value, state: _state, ...metric }) => ({
          ...metric,
          description: `${metric.label}的客户看板测试合同口径。`,
        })),
      }),
    });
  });
  await page.route('**/api/v2/customer-dashboard/projects/**', (route) => {
    const url = new URL(route.request().url());
    const pathSegments = url.pathname.split('/');
    const dashboardV2Index = pathSegments.indexOf('dashboard-v2');
    if (dashboardV2Index >= 0) {
      const snapshotsIndex = pathSegments.indexOf('snapshots');
      const snapshotPubId = snapshotsIndex >= 0 ? (pathSegments[snapshotsIndex + 1] ?? '') : '';
      const dashboard = buildCustomerDashboardV2FixtureFromUrl(url, {
        metricNames:
          snapshotsIndex >= 0
            ? [snapshotPubId.replace(/^msn_/u, '')]
            : url.searchParams.getAll('metric_name'),
      });
      const response =
        snapshotsIndex >= 0
          ? buildCustomerMetricTraceV2Fixture(dashboard, snapshotPubId)
          : dashboard;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(response),
      });
    }
    const projectsIndex = pathSegments.indexOf('projects');
    if (projectsIndex >= 0 && projectsIndex === pathSegments.length - 2) {
      const projectPubId = pathSegments.at(-1) ?? '';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(buildCustomerDashboardFixture(projectPubId)),
      });
    }
    return route.fallback();
  });
}
