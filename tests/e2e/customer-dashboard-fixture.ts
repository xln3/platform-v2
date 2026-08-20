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
  await page.route('**/api/v2/customer-dashboard/projects/*', (route) => {
    const projectPubId = new URL(route.request().url()).pathname.split('/').at(-1) ?? '';
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildCustomerDashboardFixture(projectPubId)),
    });
  });
}
