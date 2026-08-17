import {
  getCustomerAnswerPage,
  getCustomerDashboard,
  getCustomerMetricCatalog,
  type CustomerDashboardProjection,
  type CustomerMetricProjection,
  type CustomerMetricSpecProjection,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import {
  Badge,
  StatePanel,
  updateClientUrlParameters,
  useOptionalExperienceContext,
} from '@geo/design-system';
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import {
  CustomerAnswerExplorer,
  type CustomerAnswerExplorerPage,
  type CustomerAnswerExplorerQuery,
} from './customer-answer-explorer';
import { QuestionDataExplorer, SourceDataExplorer } from './customer-data-explorer';
import './customer-dashboard.css';

export type CustomerAnalyticsFocus =
  | 'overview'
  | 'visibility'
  | 'competition'
  | 'sources'
  | 'reputation'
  | 'opportunities';

type LoadState = 'loading' | 'ready' | 'empty' | 'failed' | 'forbidden' | 'fixture';

type DashboardUrlState = {
  window: string;
  model: string;
  region: string;
  mode: string;
};

type DashboardFilterOptions = Record<'model' | 'region' | 'mode', string[]>;

const emptyDashboardFilterOptions = (): DashboardFilterOptions => ({
  model: [],
  region: [],
  mode: [],
});

const safeDashboardFilterValue = (
  parameters: URLSearchParams,
  key: 'model' | 'region' | 'mode',
  maxLength: number,
): string => {
  const value = parameters.get(key);
  return value && value.length <= maxLength ? value : 'all';
};

const localIsoDate = (value: Date): string =>
  [
    value.getFullYear().toString().padStart(4, '0'),
    (value.getMonth() + 1).toString().padStart(2, '0'),
    value.getDate().toString().padStart(2, '0'),
  ].join('-');

const dashboardDateWindow = (windowValue: string): { start: string; end: string } => {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - Number.parseInt(windowValue, 10) + 1);
  return { start: localIsoDate(start), end: localIsoDate(end) };
};

const customerDashboardAllowedSections = [
  'home',
  'monitoring',
  'competition',
  'sources',
  'reputation',
  'opportunities',
  'evidence',
  'reports',
  'profile',
  'intake',
  'assets',
  'questions',
  'members',
  'accounts',
] as const;

const readDashboardUrlState = (): DashboardUrlState => {
  if (typeof window === 'undefined') {
    return { window: '30d', model: 'all', region: 'all', mode: 'all' };
  }
  const parameters = new URL(window.location.href).searchParams;
  const windowValue = parameters.get('window');
  return {
    window: ['7d', '30d', '90d', '365d'].includes(windowValue ?? '') ? windowValue! : '30d',
    model: safeDashboardFilterValue(parameters, 'model', 120),
    region: safeDashboardFilterValue(parameters, 'region', 120),
    mode: safeDashboardFilterValue(parameters, 'mode', 80),
  };
};

const metricValue = (
  metrics: readonly CustomerMetricProjection[],
  code: string,
): CustomerMetricProjection | undefined => metrics.find((metric) => metric.code === code);

const numericMetric = (metrics: readonly CustomerMetricProjection[], code: string): number | null =>
  metricValue(metrics, code)?.value ?? null;

const formatMetric = (metric: CustomerMetricProjection | undefined, compact = false): string => {
  if (!metric || metric.value === null) return '—';
  const value = metric.value;
  if (metric.format === 'percentage') return `${(value * 100).toFixed(compact ? 0 : 1)}%`;
  if (metric.format === 'score') return value.toFixed(compact ? 0 : 1);
  if (metric.format === 'rank') return `#${value.toFixed(value % 1 === 0 ? 0 : 1)}`;
  if (metric.format === 'count') return Math.round(value).toLocaleString('zh-CN');
  return value.toFixed(value % 1 === 0 ? 0 : 2);
};

const compositeCodes = [
  'geo_visibility_index',
  'competitive_power_index',
  'source_authority_index',
  'content_readiness_index',
  'reputation_index',
  'cognition_consistency_index',
] as const;

const coreMetricCodes = [
  'mention_rate',
  'top3_rate',
  'average_rank',
  'recommendation_rate',
  'share_of_voice',
  'citation_coverage',
  'own_source_answer_rate',
  'positive_rate',
] as const;

const metricGroups: Record<string, string> = {
  composite: '综合指数',
  visibility: '品牌可见度',
  ranking: '排名表现',
  competition: '竞争表现',
  source: '信源结构',
  content: '内容准备度',
  reputation: 'AI 口碑',
  risk: '品牌风险',
};

const focusCopy: Record<
  CustomerAnalyticsFocus,
  { eyebrow: string; title: string; description: string }
> = {
  overview: {
    eyebrow: 'GEO Intelligence Overview',
    title: 'AI 认知资产总览',
    description: '把可见度、竞争位置、信源权威、内容准备度与口碑风险放在同一个经营视图。',
  },
  visibility: {
    eyebrow: 'Visibility Analytics',
    title: '品牌可见度与模型表现',
    description: '拆解不同模型、地区、回答模式和日期下的提及、排名、推荐与引用表现。',
  },
  competition: {
    eyebrow: 'Competitive Intelligence',
    title: '竞品对标与心智份额',
    description: '比较目标品牌与配置竞品的声量、同题排名、优先出现和共现关系。',
  },
  sources: {
    eyebrow: 'Source Intelligence',
    title: '信源权威与内容准备度',
    description: '定位 AI 依赖的核心网站、官网引用效率、信源集中风险与事实审计结果。',
  },
  reputation: {
    eyebrow: 'Reputation & Risk',
    title: 'AI 口碑与品牌风险',
    description: '观察正中负态度、净情感和明确贬损风险在不同平台间的分布。',
  },
  opportunities: {
    eyebrow: 'Query Opportunities',
    title: '问题机会与增长缺口',
    description: '按问题识别品牌未提及、排名落后、缺少引用和缺少推荐的增长机会。',
  },
};

const fixtureMetric = (
  code: string,
  label: string,
  group: string,
  format: CustomerMetricProjection['format'],
  value: number,
  direction: CustomerMetricProjection['direction'] = 'higher',
): CustomerMetricProjection => ({
  code,
  label,
  group,
  format,
  direction,
  value,
  state: 'ready',
  version: 'customer-metrics-v1',
});

const fixtureDimensionMetrics = (
  mentionRate: number,
  top3Rate: number,
  averageRank: number,
  recommendationRate: number,
  citationCoverage: number,
): CustomerMetricProjection[] => [
  fixtureMetric('mention_rate', '品牌提及率', 'visibility', 'percentage', mentionRate),
  fixtureMetric('top3_rate', 'Top3 率', 'ranking', 'percentage', top3Rate),
  fixtureMetric('average_rank', '平均排名', 'ranking', 'rank', averageRank, 'lower'),
  fixtureMetric(
    'recommendation_rate',
    '品牌推荐率',
    'visibility',
    'percentage',
    recommendationRate,
  ),
  fixtureMetric('citation_coverage', '引用覆盖率', 'source', 'percentage', citationCoverage),
];

const customerDashboardFixtureMetrics: CustomerMetricProjection[] = [
  fixtureMetric('geo_visibility_index', 'GEO 可见度指数', 'composite', 'score', 68.4),
  fixtureMetric('competitive_power_index', '竞争力指数', 'composite', 'score', 61.8),
  fixtureMetric('source_authority_index', '信源权威指数', 'composite', 'score', 72.6),
  fixtureMetric('content_readiness_index', '内容准备度指数', 'composite', 'score', 57.3),
  fixtureMetric('reputation_index', 'AI 口碑指数', 'composite', 'score', 76.2),
  fixtureMetric('cognition_consistency_index', 'AI 认知一致性指数', 'composite', 'score', 70.9),
  fixtureMetric('answer_count', '已分析回答', 'visibility', 'count', 240, 'neutral'),
  fixtureMetric('mention_count', '品牌提及回答', 'visibility', 'count', 154),
  fixtureMetric('query_count', '覆盖问题数', 'visibility', 'count', 48),
  fixtureMetric('model_count', '覆盖模型数', 'visibility', 'count', 3),
  fixtureMetric('region_count', '覆盖地区数', 'visibility', 'count', 4, 'neutral'),
  fixtureMetric('observation_day_count', '有效观察日', 'visibility', 'count', 5),
  fixtureMetric('mention_rate', '品牌提及率', 'visibility', 'percentage', 0.642),
  fixtureMetric('no_mention_rate', '品牌未提及率', 'visibility', 'percentage', 0.358, 'lower'),
  fixtureMetric('recommendation_rate', '品牌推荐率', 'visibility', 'percentage', 0.483),
  fixtureMetric(
    'recommendation_classification_rate',
    '推荐分类覆盖率',
    'visibility',
    'percentage',
    0.8,
  ),
  fixtureMetric('average_rank', '平均排名', 'ranking', 'rank', 2.8, 'lower'),
  fixtureMetric('rank_stddev', '排名波动', 'ranking', 'decimal', 1.12, 'lower'),
  fixtureMetric('top1_rate', 'Top1 率', 'ranking', 'percentage', 0.238),
  fixtureMetric('top3_rate', 'Top3 率', 'ranking', 'percentage', 0.571),
  fixtureMetric('top5_rate', 'Top5 率', 'ranking', 'percentage', 0.704),
  fixtureMetric('share_of_voice', '竞争声量份额', 'competition', 'percentage', 0.381),
  fixtureMetric('head_to_head_win_rate', '同题对决胜率', 'competition', 'percentage', 0.552),
  fixtureMetric('citation_coverage', '引用覆盖率', 'source', 'percentage', 0.733),
  fixtureMetric('uncited_answer_rate', '无引用回答率', 'source', 'percentage', 0.267, 'lower'),
  fixtureMetric('unique_source_hosts', '独立信源网站', 'source', 'count', 126),
  fixtureMetric('source_diversity_index', '信源多样性指数', 'source', 'score', 82.4),
  fixtureMetric('top_source_share', '头部信源份额', 'source', 'percentage', 0.126, 'lower'),
  fixtureMetric('own_source_answer_rate', '官网引用回答率', 'source', 'percentage', 0.317),
  fixtureMetric(
    'third_party_source_answer_rate',
    '第三方信源回答率',
    'source',
    'percentage',
    0.667,
  ),
  fixtureMetric('source_accuracy_rate', '信源准确率', 'content', 'percentage', 0.875),
  fixtureMetric('source_audit_count', '已完成信源审计', 'content', 'count', 96),
  fixtureMetric('positive_rate', '正面回答率', 'reputation', 'percentage', 0.575),
  fixtureMetric('neutral_rate', '中性回答率', 'reputation', 'percentage', 0.35, 'neutral'),
  fixtureMetric('negative_rate', '负面回答率', 'reputation', 'percentage', 0.075, 'lower'),
  fixtureMetric('net_sentiment', '净情感指数', 'reputation', 'score', 75),
  fixtureMetric('risk_judgment_count', '风险判断数', 'risk', 'count', 42, 'neutral'),
  fixtureMetric('disparagement_rate', '品牌贬损率', 'risk', 'percentage', 0.048, 'lower'),
  fixtureMetric('support_rate', '品牌支持率', 'risk', 'percentage', 0.619),
];

const customerDashboardFixture: CustomerDashboardProjection = {
  schema_version: 'customer-dashboard-v1',
  metric_version: 'customer-metrics-v1',
  project_pub_id: 'prj_01K0CONTRACTFIXTURE0000000',
  brand_name: '云岫智能',
  state: 'ready',
  generated_at: '2026-08-17T08:00:00Z',
  as_of: '2026-08-17T07:45:00Z',
  snapshot_hash: '8b53639f7f49c6a54432f0d26a6112a8909ef434313328a78d9a03fa1b08e588',
  window: { start: '2026-08-13', end: '2026-08-17', filters: {} },
  metrics: customerDashboardFixtureMetrics,
  models: [
    { key: '豆包', label: '豆包', metrics: fixtureDimensionMetrics(0.71, 0.62, 2.3, 0.54, 0.78) },
    {
      key: 'DeepSeek',
      label: 'DeepSeek',
      metrics: fixtureDimensionMetrics(0.64, 0.57, 2.8, 0.48, 0.73),
    },
    {
      key: '通义千问',
      label: '通义千问',
      metrics: fixtureDimensionMetrics(0.57, 0.48, 3.4, 0.41, 0.69),
    },
  ],
  competitors: [
    {
      name: '北辰智库',
      metrics: [
        fixtureMetric('share_of_voice', '竞争声量份额', 'competition', 'percentage', 0.274),
      ],
    },
    {
      name: '澄明云',
      metrics: [
        fixtureMetric('share_of_voice', '竞争声量份额', 'competition', 'percentage', 0.213),
      ],
    },
    {
      name: '知策科技',
      metrics: [
        fixtureMetric('share_of_voice', '竞争声量份额', 'competition', 'percentage', 0.132),
      ],
    },
  ],
  questions: [
    {
      query_pub_id: 'qry_fixture_1',
      query_text: '制造企业如何选择可信的私有化知识库？',
      query_group: '采购选型',
      metrics: fixtureDimensionMetrics(0.25, 0.17, 4.2, 0.2, 0.33),
    },
    {
      query_pub_id: 'qry_fixture_2',
      query_text: '哪些知识助手适合强调数据安全的团队？',
      query_group: '安全能力',
      metrics: fixtureDimensionMetrics(0.42, 0.33, 3.5, 0.29, 0.5),
    },
    {
      query_pub_id: 'qry_fixture_3',
      query_text: '企业知识库产品的实施服务怎么比较？',
      query_group: '服务比较',
      metrics: fixtureDimensionMetrics(0.58, 0.5, 3, 0.44, 0.67),
    },
  ],
  sources: [
    { host: 'example.com', references: 48, share: 0.126, own_source: false, answers: 39 },
    { host: 'cloud.example.cn', references: 36, share: 0.094, own_source: true, answers: 31 },
    { host: 'industry.example.cn', references: 29, share: 0.076, own_source: false, answers: 26 },
    { host: 'news.example.cn', references: 21, share: 0.055, own_source: false, answers: 19 },
  ],
  regions: [
    { key: '华东', label: '华东', metrics: fixtureDimensionMetrics(0.69, 0.61, 2.4, 0.52, 0.76) },
    { key: '华北', label: '华北', metrics: fixtureDimensionMetrics(0.61, 0.53, 3, 0.46, 0.71) },
  ],
  modes: [
    {
      key: '深度回答',
      label: '深度回答',
      metrics: fixtureDimensionMetrics(0.72, 0.64, 2.4, 0.55, 0.82),
    },
    {
      key: '快速回答',
      label: '快速回答',
      metrics: fixtureDimensionMetrics(0.56, 0.48, 3.3, 0.39, 0.65),
    },
  ],
  trends: [
    ['2026-08-13', 0.54, 0.46, 0.65],
    ['2026-08-14', 0.59, 0.5, 0.69],
    ['2026-08-15', 0.61, 0.54, 0.71],
    ['2026-08-16', 0.66, 0.58, 0.74],
    ['2026-08-17', 0.7, 0.63, 0.79],
  ].map(([day, mention, top3, citation]) => ({
    date: String(day),
    metrics: [
      fixtureMetric('mention_rate', '品牌提及率', 'visibility', 'percentage', Number(mention)),
      fixtureMetric('top3_rate', 'Top3 率', 'ranking', 'percentage', Number(top3)),
      fixtureMetric('citation_coverage', '引用覆盖率', 'source', 'percentage', Number(citation)),
    ],
  })),
  risk: {
    metrics: customerDashboardFixtureMetrics.filter((metric) => metric.group === 'risk'),
    by_model: [
      {
        key: '豆包',
        label: '豆包',
        metrics: [
          fixtureMetric('risk_judgment_count', '风险判断数', 'risk', 'count', 24, 'neutral'),
          fixtureMetric('disparagement_rate', '品牌贬损率', 'risk', 'percentage', 0.03, 'lower'),
          fixtureMetric('support_rate', '品牌支持率', 'risk', 'percentage', 0.67),
        ],
      },
      {
        key: 'DeepSeek',
        label: 'DeepSeek',
        metrics: [
          fixtureMetric('risk_judgment_count', '风险判断数', 'risk', 'count', 18, 'neutral'),
          fixtureMetric('disparagement_rate', '品牌贬损率', 'risk', 'percentage', 0.06, 'lower'),
          fixtureMetric('support_rate', '品牌支持率', 'risk', 'percentage', 0.58),
        ],
      },
    ],
  },
  source_audit: {
    metrics: customerDashboardFixtureMetrics.filter((metric) => metric.group === 'content'),
    verdicts: { accurate: 84, unsupported: 7, unverifiable: 5 },
  },
};

const customerAnswerFixturePage: CustomerAnswerExplorerPage = {
  schema_version: 'customer-answer-page-v1',
  project_pub_id: customerDashboardFixture.project_pub_id,
  data: [
    {
      answer_pub_id: 'ans_fixture_01',
      query_pub_id: 'qry_fixture_1',
      query_text: '制造企业如何选择可信的私有化知识库？',
      response_text:
        '选择私有化知识库时，应重点比较权限隔离、知识更新效率、检索准确率和实施服务。云岫智能在本地部署、权限治理与行业知识工程方面具备完整方案。\n\n采购阶段还应通过真实业务问题验证回答质量，并确认引用来源是否可追溯。',
      model: 'DeepSeek',
      region: '华东',
      mode: '深度回答',
      capture_time: '2026-08-17T07:42:00Z',
      mentioned: true,
      rank: 1,
      sentiment: 'positive',
      recommended: true,
      citation_count: 4,
    },
    {
      answer_pub_id: 'ans_fixture_02',
      query_pub_id: 'qry_fixture_2',
      query_text: '哪些知识助手适合强调数据安全的团队？',
      response_text:
        '强调数据安全的团队通常会考察私有化部署、细粒度权限、审计留痕和模型接入方式。云岫智能、北辰智库等产品都提供面向企业的知识助手能力，建议结合现有基础设施进行验证。',
      model: '豆包',
      region: '华北',
      mode: '快速回答',
      capture_time: '2026-08-17T07:18:00Z',
      mentioned: true,
      rank: 2,
      sentiment: 'neutral',
      recommended: false,
      citation_count: 2,
    },
    {
      answer_pub_id: 'ans_fixture_03',
      query_pub_id: 'qry_fixture_3',
      query_text: '企业知识库产品的实施服务怎么比较？',
      response_text:
        '可以从需求梳理、数据治理、上线周期、培训和持续运营五个方面比较实施服务。部分厂商的产品能力较完整，但公开资料对交付团队和行业案例披露不足。',
      model: '通义千问',
      region: '华东',
      mode: '深度回答',
      capture_time: '2026-08-16T16:26:00Z',
      mentioned: false,
      rank: null,
      sentiment: 'unknown',
      recommended: null,
      citation_count: 0,
    },
    {
      answer_pub_id: 'ans_fixture_04',
      query_pub_id: 'qry_fixture_1',
      query_text: '私有化知识库选型需要关注哪些指标？',
      response_text:
        '建议关注召回准确率、回答可追溯性、权限粒度、知识更新时效、并发能力与总体拥有成本。云岫智能在知识治理方面评价较好，但仍应使用企业自己的数据集完成对比测试。',
      model: 'DeepSeek',
      region: '华南',
      mode: '深度回答',
      capture_time: '2026-08-16T14:05:00Z',
      mentioned: true,
      rank: 3,
      sentiment: 'positive',
      recommended: true,
      citation_count: 3,
    },
  ],
  page: { total: 4, offset: 0, limit: 20, has_more: false },
};

function ScoreCard({ metric }: { metric: CustomerMetricProjection | undefined }) {
  const score = metric?.value ?? 0;
  const ready = metric?.state === 'ready' && metric.value !== null;
  return (
    <article className="geo-score-card">
      <div
        className="geo-score-ring"
        style={{ '--geo-score': `${Math.max(0, Math.min(100, score)) * 3.6}deg` } as CSSProperties}
        aria-label={ready ? `${metric?.label} ${score.toFixed(1)} 分` : `${metric?.label}尚未就绪`}
      >
        <strong>{ready ? score.toFixed(0) : '—'}</strong>
        <small>/100</small>
      </div>
      <div>
        <span>{metric?.label ?? '指标未配置'}</span>
        <small>
          {ready
            ? score >= 75
              ? '表现领先'
              : score >= 55
                ? '仍有提升空间'
                : '优先优化'
            : '等待数据'}
        </small>
      </div>
    </article>
  );
}

function SectionHeading({
  eyebrow,
  title,
  detail,
}: {
  eyebrow: string;
  title: string;
  detail: string;
}) {
  return (
    <div className="geo-section-heading">
      <div>
        <span>{eyebrow}</span>
        <h2>{title}</h2>
      </div>
      <p>{detail}</p>
    </div>
  );
}

function MetricCards({ dashboard }: { dashboard: CustomerDashboardProjection }) {
  return (
    <div className="geo-kpi-grid">
      {coreMetricCodes.map((code) => {
        const metric = metricValue(dashboard.metrics, code);
        return (
          <article className="geo-kpi-card" key={code}>
            <div className="geo-kpi-label">
              <span>{metric?.label ?? code}</span>
              <Badge tone={metric?.state === 'ready' ? 'positive' : 'warning'}>
                {metric?.state === 'ready' ? '当前窗口' : '待计算'}
              </Badge>
            </div>
            <strong>{formatMetric(metric)}</strong>
            <div className="geo-kpi-track" aria-hidden="true">
              <i
                style={{
                  width: `${Math.max(
                    0,
                    Math.min(
                      100,
                      metric?.format === 'percentage'
                        ? (metric.value ?? 0) * 100
                        : metric?.format === 'rank'
                          ? Math.max(0, 100 - ((metric.value ?? 10) - 1) * 10)
                          : (metric?.value ?? 0),
                    ),
                  )}%`,
                }}
              />
            </div>
          </article>
        );
      })}
    </div>
  );
}

const trendSeries = [
  { code: 'mention_rate', label: '提及率', color: '#176b51' },
  { code: 'top3_rate', label: 'Top3 率', color: '#6f8b2f' },
  { code: 'citation_coverage', label: '引用覆盖率', color: '#b36b22' },
] as const;

function TrendChart({ dashboard }: { dashboard: CustomerDashboardProjection }) {
  const rows = dashboard.trends;
  if (!rows.length) return <StatePanel state="insufficient" />;
  const width = 760;
  const height = 280;
  const left = 48;
  const top = 24;
  const plotWidth = width - left - 24;
  const plotHeight = height - top - 44;
  const x = (index: number) =>
    left + (rows.length === 1 ? plotWidth / 2 : (index / (rows.length - 1)) * plotWidth);
  const y = (value: number) => top + plotHeight - Math.max(0, Math.min(1, value)) * plotHeight;
  return (
    <div className="geo-trend-wrap">
      <div className="geo-chart-legend">
        {trendSeries.map((series) => (
          <span key={series.code}>
            <i style={{ background: series.color }} /> {series.label}
          </span>
        ))}
      </div>
      <svg
        className="geo-trend-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="品牌提及率、Top3 率和引用覆盖率趋势"
      >
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <g key={tick}>
            <line x1={left} x2={width - 24} y1={y(tick)} y2={y(tick)} />
            <text x={left - 10} y={y(tick) + 4} textAnchor="end">
              {Math.round(tick * 100)}%
            </text>
          </g>
        ))}
        {trendSeries.map((series) => {
          const points = rows.flatMap((row, index) => {
            const value = numericMetric(row.metrics, series.code);
            return value === null ? [] : [`${x(index)},${y(value)}`];
          });
          return (
            <g key={series.code}>
              {points.length > 1 ? (
                <polyline
                  points={points.join(' ')}
                  fill="none"
                  stroke={series.color}
                  strokeWidth="3"
                />
              ) : null}
              {rows.map((row, index) => {
                const value = numericMetric(row.metrics, series.code);
                return value === null ? null : (
                  <circle key={row.date} cx={x(index)} cy={y(value)} r="4" fill={series.color}>
                    <title>{`${row.date} · ${series.label} ${(value * 100).toFixed(1)}%`}</title>
                  </circle>
                );
              })}
            </g>
          );
        })}
        {rows.map((row, index) => (
          <text key={row.date} x={x(index)} y={height - 12} textAnchor="middle">
            {row.date.slice(5)}
          </text>
        ))}
      </svg>
    </div>
  );
}

function DimensionTable({
  title,
  rows,
}: {
  title: string;
  rows: CustomerDashboardProjection['models'];
}) {
  return (
    <section className="geo-dashboard-panel">
      <div className="geo-panel-title">
        <h3>{title}</h3>
        <span>{rows.length} 个维度</span>
      </div>
      {rows.length ? (
        <div className="geo-table-scroll" tabIndex={0} aria-label={`${title}数据表`}>
          <table className="geo-dashboard-table">
            <thead>
              <tr>
                <th>维度</th>
                <th>提及率</th>
                <th>Top3</th>
                <th>平均排名</th>
                <th>推荐率</th>
                <th>引用覆盖</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <th>{row.label}</th>
                  {[
                    'mention_rate',
                    'top3_rate',
                    'average_rank',
                    'recommendation_rate',
                    'citation_coverage',
                  ].map((code) => (
                    <td key={code}>{formatMetric(metricValue(row.metrics, code))}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <StatePanel state="insufficient" />
      )}
    </section>
  );
}

function RiskDimensionTable({ rows }: { rows: CustomerDashboardProjection['risk']['by_model'] }) {
  return (
    <section className="geo-dashboard-panel">
      <div className="geo-panel-title">
        <h3>风险平台分布</h3>
        <span>{rows.length} 个平台</span>
      </div>
      {rows.length ? (
        <div className="geo-table-scroll" tabIndex={0} aria-label="风险平台分布数据表">
          <table className="geo-dashboard-table">
            <thead>
              <tr>
                <th>AI 平台</th>
                <th>判断数</th>
                <th>品牌贬损率</th>
                <th>品牌支持率</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <th>{row.label}</th>
                  {['risk_judgment_count', 'disparagement_rate', 'support_rate'].map((code) => (
                    <td key={code}>{formatMetric(metricValue(row.metrics, code))}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <StatePanel state="insufficient" />
      )}
    </section>
  );
}

function CompetitorPanel({
  dashboard,
  full = false,
}: {
  dashboard: CustomerDashboardProjection;
  full?: boolean;
}) {
  const brandShare = numericMetric(dashboard.metrics, 'share_of_voice') ?? 0;
  const rows = full ? dashboard.competitors : dashboard.competitors.slice(0, 6);
  return (
    <section className="geo-dashboard-panel">
      <div className="geo-panel-title">
        <div>
          <span>Share of Voice</span>
          <h3>竞品心智份额</h3>
        </div>
        <Badge tone="info">同一观察窗口</Badge>
      </div>
      <div className="geo-competitor-list">
        <div className="geo-competitor-row geo-brand-row">
          <div>
            <strong>{dashboard.brand_name}</strong>
            <small>目标品牌</small>
          </div>
          <div className="geo-bar-track">
            <i style={{ width: `${brandShare * 100}%` }} />
          </div>
          <b>{(brandShare * 100).toFixed(1)}%</b>
        </div>
        {rows.map((row) => {
          const share = numericMetric(row.metrics, 'share_of_voice') ?? 0;
          return (
            <div className="geo-competitor-row" key={row.name}>
              <div>
                <strong>{row.name}</strong>
                <small>配置竞品</small>
              </div>
              <div className="geo-bar-track">
                <i style={{ width: `${share * 100}%` }} />
              </div>
              <b>{(share * 100).toFixed(1)}%</b>
            </div>
          );
        })}
      </div>
      {full ? (
        <div className="geo-table-scroll" tabIndex={0} aria-label="竞品完整指标对比表">
          <table className="geo-dashboard-table">
            <thead>
              <tr>
                <th>品牌</th>
                <th>提及率</th>
                <th>心智份额</th>
                <th>平均排名</th>
                <th>Top3</th>
                <th>我方同题胜率</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>{dashboard.brand_name}</th>
                {[
                  'mention_rate',
                  'share_of_voice',
                  'average_rank',
                  'top3_rate',
                  'head_to_head_win_rate',
                ].map((code) => (
                  <td key={code}>{formatMetric(metricValue(dashboard.metrics, code))}</td>
                ))}
              </tr>
              {rows.map((row) => (
                <tr key={`matrix-${row.name}`}>
                  <th>{row.name}</th>
                  {['mention_rate', 'share_of_voice', 'average_rank', 'top3_rate'].map((code) => (
                    <td key={code}>{formatMetric(metricValue(row.metrics, code))}</td>
                  ))}
                  <td>{formatMetric(metricValue(row.metrics, 'head_to_head_win_rate'))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function SourcePanel({
  dashboard,
  full = false,
}: {
  dashboard: CustomerDashboardProjection;
  full?: boolean;
}) {
  const rows = (full ? dashboard.sources : dashboard.sources.slice(0, 10)).slice(
    0,
    full ? 100 : 10,
  );
  return (
    <section className="geo-dashboard-panel">
      <div className="geo-panel-title">
        <div>
          <span>Source Landscape</span>
          <h3>AI 核心信源</h3>
        </div>
        <strong>
          {formatMetric(metricValue(dashboard.metrics, 'unique_source_hosts'))} 个网站
        </strong>
      </div>
      <div className="geo-source-summary">
        {[
          'citation_coverage',
          'own_source_answer_rate',
          'source_diversity_index',
          'source_accuracy_rate',
        ].map((code) => {
          const metric = metricValue(dashboard.metrics, code);
          return (
            <div key={code}>
              <span>{metric?.label ?? code}</span>
              <strong>{formatMetric(metric)}</strong>
            </div>
          );
        })}
      </div>
      {rows.length ? (
        <div className="geo-table-scroll" tabIndex={0} aria-label="核心信源数据表">
          <table className="geo-dashboard-table">
            <thead>
              <tr>
                <th>信源网站</th>
                <th>引用回答</th>
                <th>引用次数</th>
                <th>份额</th>
                <th>类型</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.host}>
                  <th>{row.host}</th>
                  <td>{row.answers}</td>
                  <td>{row.references}</td>
                  <td>{row.share === null ? '—' : `${(row.share * 100).toFixed(1)}%`}</td>
                  <td>
                    <Badge tone={row.own_source ? 'positive' : 'neutral'}>
                      {row.own_source ? '官网' : '第三方'}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <StatePanel state="insufficient" />
      )}
    </section>
  );
}

function QuestionPanel({
  dashboard,
  full = false,
}: {
  dashboard: CustomerDashboardProjection;
  full?: boolean;
}) {
  const rows = useMemo(
    () =>
      [...dashboard.questions]
        .sort((left, right) => {
          const leftRate = numericMetric(left.metrics, 'mention_rate');
          const rightRate = numericMetric(right.metrics, 'mention_rate');
          return (leftRate ?? -1) - (rightRate ?? -1);
        })
        .slice(0, full ? 500 : 8),
    [dashboard.questions, full],
  );
  return (
    <section className="geo-dashboard-panel geo-question-panel">
      <div className="geo-panel-title">
        <div>
          <span>Opportunity Map</span>
          <h3>优先增长问题</h3>
        </div>
        <Badge tone="warning">低提及优先</Badge>
      </div>
      {rows.length ? (
        <div className="geo-table-scroll" tabIndex={0} aria-label="问题机会数据表">
          <table className="geo-dashboard-table">
            <thead>
              <tr>
                <th>用户问题</th>
                <th>问题组</th>
                <th>提及率</th>
                <th>平均排名</th>
                <th>推荐率</th>
                <th>引用覆盖</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.query_pub_id}>
                  <th>{row.query_text}</th>
                  <td>{row.query_group ?? '未分组'}</td>
                  {['mention_rate', 'average_rank', 'recommendation_rate', 'citation_coverage'].map(
                    (code) => (
                      <td key={code}>{formatMetric(metricValue(row.metrics, code))}</td>
                    ),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <StatePanel state="insufficient" />
      )}
    </section>
  );
}

function ReputationPanel({ dashboard }: { dashboard: CustomerDashboardProjection }) {
  const risks = dashboard.risk.metrics;
  return (
    <section className="geo-dashboard-panel">
      <div className="geo-panel-title">
        <div>
          <span>AI Reputation</span>
          <h3>口碑与风险雷达</h3>
        </div>
        <strong>净情感 {formatMetric(metricValue(dashboard.metrics, 'net_sentiment'))}</strong>
      </div>
      <div className="geo-sentiment-grid">
        {(
          [
            ['positive_rate', '正面'],
            ['neutral_rate', '中性'],
            ['negative_rate', '负面'],
          ] as const
        ).map(([code, label]) => (
          <div key={code} data-tone={code.replace('_rate', '')}>
            <span>{label}</span>
            <strong>{formatMetric(metricValue(dashboard.metrics, code))}</strong>
          </div>
        ))}
      </div>
      <div className="geo-risk-grid">
        <div>
          <span>品牌贬损率</span>
          <strong>{formatMetric(metricValue(risks, 'disparagement_rate'))}</strong>
        </div>
        <div>
          <span>品牌支持率</span>
          <strong>{formatMetric(metricValue(risks, 'support_rate'))}</strong>
        </div>
        <div>
          <span>风险覆盖平台</span>
          <strong>{dashboard.risk.by_model.length}</strong>
        </div>
      </div>
    </section>
  );
}

function MetricDirectory({
  dashboard,
  catalog,
  groups,
}: {
  dashboard: CustomerDashboardProjection;
  catalog: ReadonlyMap<string, CustomerMetricSpecProjection>;
  groups?: readonly string[];
}) {
  const grouped = useMemo(() => {
    const result = new Map<string, CustomerMetricProjection[]>();
    for (const metric of dashboard.metrics) {
      if (groups && !groups.includes(metric.group)) continue;
      const list = result.get(metric.group) ?? [];
      list.push(metric);
      result.set(metric.group, list);
    }
    return result;
  }, [catalog, dashboard.metrics, groups]);
  return (
    <section className="geo-dashboard-panel geo-metric-directory">
      <SectionHeading
        eyebrow="Metric Dictionary"
        title="全部指标"
        detail="每个指标均来自版本化客户事实快照；未就绪与真实 0 分开显示。"
      />
      {[...grouped.entries()].map(([group, metrics]) => (
        <div className="geo-metric-group" key={group}>
          <h3>{metricGroups[group] ?? group}</h3>
          <div>
            {metrics.map((metric) => (
              <article key={metric.code} title={catalog.get(metric.code)?.description}>
                <span>{metric.label}</span>
                <strong>{formatMetric(metric)}</strong>
                <small>
                  {metric.state === 'ready'
                    ? (catalog.get(metric.code)?.description ?? '当前窗口已计算')
                    : '当前事实不足，暂不生成数值'}
                </small>
              </article>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

function DashboardFilters({
  options,
  urlState,
  setFilter,
}: {
  options: DashboardFilterOptions;
  urlState: DashboardUrlState;
  setFilter: (key: string, value: string) => void;
}) {
  return (
    <div className="geo-dashboard-filters" aria-label="分析筛选">
      <label>
        观察窗口
        <select
          value={urlState.window}
          onChange={(event) => setFilter('window', event.target.value)}
        >
          <option value="7d">近 7 天</option>
          <option value="30d">近 30 天</option>
          <option value="90d">近 90 天</option>
          <option value="365d">近 365 天</option>
        </select>
      </label>
      {(['model', 'region', 'mode'] as const).map((key) => (
        <label key={key}>
          {key === 'model' ? 'AI 模型' : key === 'region' ? '地区' : '回答模式'}
          <select value={urlState[key]} onChange={(event) => setFilter(key, event.target.value)}>
            <option value="all">全部</option>
            {(urlState[key] !== 'all' && !options[key].includes(urlState[key])
              ? [urlState[key], ...options[key]]
              : options[key]
            ).map((value) => (
              <option value={value} key={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
      ))}
    </div>
  );
}

function DashboardSection({ children }: { children: ReactNode }) {
  return <div className="geo-dashboard-section">{children}</div>;
}

export function CustomerAnalyticsWorkspace({
  focus = 'overview',
}: {
  focus?: CustomerAnalyticsFocus;
}) {
  const experience = useOptionalExperienceContext();
  const [urlState, setUrlState] = useState<DashboardUrlState>(readDashboardUrlState);
  const [liveDashboard, setDashboard] = useState<CustomerDashboardProjection | null>(null);
  const [catalog, setCatalog] = useState<ReadonlyMap<string, CustomerMetricSpecProjection>>(
    new Map(),
  );
  const [filterOptions, setFilterOptions] = useState<DashboardFilterOptions>(
    emptyDashboardFilterOptions,
  );
  const [state, setState] = useState<LoadState>(
    experience?.source === 'live' ? 'loading' : 'fixture',
  );
  const [retryKey, setRetryKey] = useState(0);
  const windowValue = urlState.window;
  const activeFilters = {
    ...(urlState.model !== 'all' ? { model: urlState.model } : {}),
    ...(urlState.region !== 'all' ? { region: urlState.region } : {}),
    ...(urlState.mode !== 'all' ? { mode: urlState.mode } : {}),
  };
  const activeDateWindow = useMemo(() => dashboardDateWindow(windowValue), [windowValue]);
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const sync = () => setUrlState(readDashboardUrlState());
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);
  useEffect(() => {
    setFilterOptions(emptyDashboardFilterOptions());
  }, [experience?.projectPubId]);
  useEffect(() => {
    if (experience?.source !== 'live' || !experience.projectPubId) {
      setState('fixture');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setState('forbidden');
      return;
    }
    let cancelled = false;
    setState('loading');
    void Promise.all([
      getCustomerDashboard(
        experience.projectPubId,
        activeDateWindow.start,
        activeDateWindow.end,
        activeFilters,
        headers,
      ),
      getCustomerMetricCatalog(headers),
    ]).then(([dashboardResult, catalogResult]) => {
      if (cancelled) return;
      if (dashboardResult.kind !== 'ready') {
        setDashboard(null);
        setState(dashboardResult.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      setDashboard(dashboardResult.data);
      setFilterOptions((current) => ({
        model: [
          ...new Set([...current.model, ...dashboardResult.data.models.map((row) => row.key)]),
        ],
        region: [
          ...new Set([...current.region, ...dashboardResult.data.regions.map((row) => row.key)]),
        ],
        mode: [...new Set([...current.mode, ...dashboardResult.data.modes.map((row) => row.key)])],
      }));
      setCatalog(
        new Map(
          catalogResult.kind === 'ready'
            ? catalogResult.data.metrics.map((item) => [item.code, item])
            : [],
        ),
      );
      setState(dashboardResult.data.state === 'building' ? 'empty' : 'ready');
    });
    return () => {
      cancelled = true;
    };
  }, [
    experience?.projectPubId,
    experience?.source,
    retryKey,
    windowValue,
    activeFilters.model,
    activeFilters.region,
    activeFilters.mode,
    activeDateWindow.start,
    activeDateWindow.end,
  ]);

  const loadAnswerPage = useCallback(
    async (query: CustomerAnswerExplorerQuery): Promise<CustomerAnswerExplorerPage> => {
      if (experience?.source !== 'live' || !experience.projectPubId) {
        const needle = query.search.trim().toLocaleLowerCase('zh-CN');
        const matches = customerAnswerFixturePage.data.filter((row) => {
          if (query.mentioned !== 'all' && row.mentioned !== (query.mentioned === 'true')) {
            return false;
          }
          if (query.sentiment !== 'all' && row.sentiment !== query.sentiment) return false;
          return (
            needle.length === 0 ||
            `${row.query_text ?? ''}\n${row.response_text}`
              .toLocaleLowerCase('zh-CN')
              .includes(needle)
          );
        });
        const data = matches.slice(query.offset, query.offset + query.limit);
        return {
          schema_version: 'customer-answer-page-v1',
          project_pub_id: customerAnswerFixturePage.project_pub_id,
          data,
          page: {
            total: matches.length,
            offset: query.offset,
            limit: query.limit,
            has_more: query.offset + data.length < matches.length,
          },
        };
      }
      const headers = getValidatedIdentityHeaders();
      if (!headers) throw new Error('customer answer identity unavailable');
      const result = await getCustomerAnswerPage(
        experience.projectPubId,
        activeDateWindow.start,
        activeDateWindow.end,
        {
          ...activeFilters,
          ...(query.search ? { search: query.search } : {}),
          ...(query.mentioned === 'all' ? {} : { mentioned: query.mentioned === 'true' }),
          ...(query.sentiment === 'all' ? {} : { sentiment: query.sentiment }),
          offset: query.offset,
          limit: query.limit,
        },
        headers,
      );
      if (result.kind !== 'ready') throw new Error(`customer answer page ${result.kind}`);
      return result.data;
    },
    [
      experience?.projectPubId,
      experience?.source,
      activeDateWindow.start,
      activeDateWindow.end,
      activeFilters.model,
      activeFilters.region,
      activeFilters.mode,
    ],
  );

  const setFilter = (key: string, value: string) => {
    const nextValue = value === 'all' || (key === 'window' && value === '30d') ? null : value;
    updateClientUrlParameters({ [key]: nextValue }, customerDashboardAllowedSections);
    setUrlState((current) => ({ ...current, [key]: value }));
  };
  const copy = focusCopy[focus];
  const dashboard = state === 'fixture' ? customerDashboardFixture : liveDashboard;
  if (state === 'loading') return <StatePanel state="loading" />;
  if (state === 'forbidden') return <StatePanel state="forbidden" />;
  if (state === 'failed')
    return <StatePanel state="failed" onRetry={() => setRetryKey((value) => value + 1)} />;
  if (state === 'empty') return <StatePanel state="insufficient" />;
  if (!dashboard) return <StatePanel state="insufficient" />;

  const metricGroupsForFocus: Record<CustomerAnalyticsFocus, readonly string[]> = {
    overview: [
      'composite',
      'visibility',
      'ranking',
      'competition',
      'source',
      'content',
      'reputation',
      'risk',
    ],
    visibility: ['composite', 'visibility', 'ranking'],
    competition: ['competition', 'visibility', 'ranking'],
    sources: ['source', 'content'],
    reputation: ['reputation', 'risk'],
    opportunities: ['visibility', 'ranking', 'competition', 'source'],
  };
  return (
    <div className="geo-customer-dashboard">
      <section className="geo-dashboard-hero">
        <div>
          <span>{copy.eyebrow}</span>
          <h2>
            {dashboard.brand_name} · {copy.title}
          </h2>
          <p>{copy.description}</p>
        </div>
        <div className="geo-snapshot-meta">
          <span>事实快照</span>
          <strong>
            {dashboard.as_of
              ? new Date(dashboard.as_of).toLocaleString('zh-CN', {
                  month: 'short',
                  day: 'numeric',
                  hour: '2-digit',
                  minute: '2-digit',
                })
              : '构建中'}
          </strong>
          <small>{dashboard.snapshot_hash.slice(0, 12)}</small>
        </div>
      </section>
      <DashboardFilters options={filterOptions} urlState={urlState} setFilter={setFilter} />

      {focus === 'overview' || focus === 'visibility' ? (
        <DashboardSection>
          <SectionHeading
            eyebrow="Executive Scores"
            title="六大经营指数"
            detail="从多个基础指标合成 0–100 分，便于管理层快速定位增长短板。"
          />
          <div className="geo-score-grid">
            {compositeCodes.map((code) => (
              <ScoreCard metric={metricValue(dashboard.metrics, code)} key={code} />
            ))}
          </div>
          <MetricCards dashboard={dashboard} />
        </DashboardSection>
      ) : null}

      {focus === 'overview' || focus === 'visibility' ? (
        <CustomerAnswerExplorer
          key={`${dashboard.project_pub_id}:${windowValue}:${urlState.model}:${urlState.region}:${urlState.mode}`}
          brandName={dashboard.brand_name}
          loadPage={loadAnswerPage}
          {...(experience?.source === 'live' ? {} : { fixturePage: customerAnswerFixturePage })}
        />
      ) : null}

      {focus === 'overview' || focus === 'visibility' ? (
        <DashboardSection>
          <SectionHeading
            eyebrow="Time Series"
            title="可见度趋势"
            detail="只连接真实存在的观察日期；没有数据的日期不会伪造成 0。"
          />
          <section className="geo-dashboard-panel">
            <TrendChart dashboard={dashboard} />
          </section>
          <div className="geo-dashboard-columns">
            <DimensionTable title="模型表现" rows={dashboard.models} />
            <DimensionTable title="地区表现" rows={dashboard.regions} />
          </div>
          {focus === 'visibility' ? (
            <DimensionTable title="回答模式表现" rows={dashboard.modes} />
          ) : null}
        </DashboardSection>
      ) : null}

      {focus === 'overview' ? (
        <DashboardSection>
          <div className="geo-dashboard-columns">
            <CompetitorPanel dashboard={dashboard} />
            <ReputationPanel dashboard={dashboard} />
          </div>
          <SourcePanel dashboard={dashboard} />
          <QuestionPanel dashboard={dashboard} />
        </DashboardSection>
      ) : null}
      {focus === 'competition' ? (
        <DashboardSection>
          <CompetitorPanel dashboard={dashboard} full />
          <QuestionPanel dashboard={dashboard} />
        </DashboardSection>
      ) : null}
      {focus === 'sources' ? (
        <DashboardSection>
          <SourcePanel dashboard={dashboard} />
          <SourceDataExplorer sources={dashboard.sources} />
        </DashboardSection>
      ) : null}
      {focus === 'reputation' ? (
        <DashboardSection>
          <ReputationPanel dashboard={dashboard} />
          <RiskDimensionTable rows={dashboard.risk.by_model} />
        </DashboardSection>
      ) : null}
      {focus === 'opportunities' ? (
        <DashboardSection>
          <QuestionPanel dashboard={dashboard} />
          <QuestionDataExplorer questions={dashboard.questions} />
        </DashboardSection>
      ) : null}
      <MetricDirectory
        dashboard={dashboard}
        catalog={catalog}
        groups={metricGroupsForFocus[focus]}
      />
    </div>
  );
}
