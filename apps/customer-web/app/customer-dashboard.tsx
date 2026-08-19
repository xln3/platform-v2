import {
  getAnalyticsAnswerRelations,
  getCustomerAnswerLibraryDetail,
  getCustomerAnswerLibraryMetaQuery,
  getCustomerAnswerLibraryPage,
  getCustomerAnswerLibraryQuestionRuns,
  getCustomerDashboard,
  getCustomerMetricCatalog,
  getEvidenceAssetContent,
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
  CustomerAnswerLoadError,
  CustomerAnswerExplorer,
  type CustomerAnswerDetail,
  type CustomerAnswerEvidenceImageLoader,
  type CustomerAnswerExplorerPage,
  type CustomerAnswerLibraryAnswer,
  type CustomerAnswerLibraryMetaDetail,
  type CustomerAnswerLibraryPage,
  type CustomerAnswerLibraryRootQuery,
  type CustomerAnswerLibraryRun,
  type CustomerAnswerLibraryRunQuery,
  type CustomerAnswerLibraryRuns,
  type CustomerAnswerLibrarySnapshot,
} from './customer-answer-explorer';
import { QuestionDataExplorer, SourceDataExplorer } from './customer-data-explorer';
import './customer-dashboard.css';

export type CustomerAnalyticsFocus =
  | 'overview'
  | 'visibility'
  | 'answers'
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
  'answers',
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

const visibilityMetricCodes = [
  'mention_count',
  'mention_rate',
  'recommendation_rate',
  'top1_rate',
  'top3_rate',
  'average_rank',
  'ranked_answer_rate',
  'citation_coverage',
] as const;

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
  answers: {
    eyebrow: 'Answer Intelligence',
    title: '真实 AI 回答与模型语境',
    description: '按 AI 平台、回答模式和地域分类查看真实问题、完整回答、品牌表现与引用证据。',
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
    title: '问题覆盖明细',
    description: '逐题查看品牌是否出现、出现位置、推荐情况与引用覆盖，不替客户推断增长优先级。',
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

// Keep the contract identity out of release bundles. The fixture data remains available to
// Vitest/dev and explicitly opted-in contract builds, while production has no synthetic project
// identity that could accidentally cross the browser boundary.
const customerFixtureProjectPubId =
  import.meta.env.DEV || import.meta.env.VITE_ALLOW_CONTRACT_FIXTURES === 'true'
    ? 'prj_01K0CONTRACTFIXTURE0000000'
    : 'prj_fixture_disabled';

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
  fixtureMetric('ranked_answer_rate', '有效排名覆盖率', 'ranking', 'percentage', 0.642),
  fixtureMetric('top1_rate', 'Top1 率', 'ranking', 'percentage', 0.238),
  fixtureMetric('top3_rate', 'Top3 率', 'ranking', 'percentage', 0.571),
  fixtureMetric('top5_rate', 'Top5 率', 'ranking', 'percentage', 0.704),
  fixtureMetric('share_of_voice', '竞争声量份额', 'competition', 'percentage', 0.381),
  fixtureMetric('head_to_head_win_rate', '同题对决胜率', 'competition', 'percentage', 0.552),
  fixtureMetric('citation_coverage', '引用覆盖率', 'source', 'percentage', 0.733),
  fixtureMetric('uncited_answer_rate', '无引用回答率', 'source', 'percentage', 0.267, 'lower'),
  fixtureMetric('unique_source_hosts', '独立信源网站', 'source', 'count', 126),
  fixtureMetric('unique_source_pages', '独立信源页面', 'source', 'count', 1842),
  fixtureMetric('citation_references', '引用总次数', 'source', 'count', 3426),
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
  fixtureMetric('source_unsupported_rate', '无依据信源率', 'content', 'percentage', 0.083, 'lower'),
  fixtureMetric('source_unverifiable_rate', '无法核实率', 'content', 'percentage', 0.042, 'lower'),
  fixtureMetric('cited_text_visibility_rate', '引用原文可见率', 'source', 'percentage', 0.91),
  fixtureMetric('citation_title_visibility_rate', '引用标题可见率', 'source', 'percentage', 0.95),
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
  project_pub_id: customerFixtureProjectPubId,
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
        '选择私有化知识库时，应把“能否回答”与“是否可信”分开评估。云岫智能在本地部署、权限治理与行业知识工程方面具备完整方案。[citation:0]\n\n## 建议重点核验四项能力\n\n1. **权限与数据边界**：确认租户隔离、细粒度授权、操作审计和模型调用边界。\n2. **知识更新效率**：验证增量同步、失效内容下线和版本回溯能力。[citation:2]\n3. **检索与引用质量**：用真实业务问题测试召回、答案相关性，以及引用能否落到原始页面。\n4. **实施与持续运营**：明确上线周期、数据治理责任和后续质量复盘机制。\n\n| 评估环节 | 客户应看到的证据 |\n| --- | --- |\n| 权限验证 | 不同角色的访问结果与审计记录 |\n| 回答验收 | 完整答案、引用原文与平台分享凭证 |\n| 持续运营 | 知识变更、答案变化与引用采纳时间轴 |\n\n采购阶段应使用企业自己的数据集完成对比测试，不应仅依据厂商演示结论。[citation:1]',
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

const customerAnswerFixtureDetails: Readonly<Record<string, CustomerAnswerDetail>> = {
  ans_fixture_01: {
    citations: [
      {
        id: 'cit_fixture_01',
        ordinal: 1,
        url: 'https://example.org/security/zero-trust',
        host: 'example.org',
        title: '企业知识系统的零信任访问实践',
        citedText: '敏感知识应同时实施最小权限、身份校验和访问审计。',
        ownSource: false,
        contentHash: 'a'.repeat(64),
        publishedAtRaw: '2026-06-18T09:00:00+08:00',
        publishedAt: '2026-06-18T09:00:00+08:00',
        publishedAtTimezone: '+08:00',
        publishedAtPrecision: 'second',
        publishedAtSource: '页面 JSON-LD datePublished',
        publishedAtConfidence: 'verified_structured',
      },
      {
        id: 'cit_fixture_02',
        ordinal: 2,
        url: 'https://research.example.com/enterprise-rag',
        host: 'research.example.com',
        title: '企业级检索增强生成质量评估',
        citedText: '上线前应使用真实业务问题评估召回率、回答相关性与引用可追溯性。',
        ownSource: false,
        contentHash: 'b'.repeat(64),
        publishedAtRaw: '2026-07-03',
        publishedAt: '2026-07-03T00:00:00+08:00',
        publishedAtTimezone: 'unknown',
        publishedAtPrecision: 'date',
        publishedAtSource: '页面可见 time 元素（仅日期）',
        publishedAtConfidence: 'visible_only',
      },
      {
        id: 'cit_fixture_03',
        ordinal: 3,
        url: 'https://docs.example.net/knowledge-governance',
        host: 'docs.example.net',
        title: '知识更新与权限治理指南',
        citedText: '知识更新频率和权限策略需要纳入持续运营，而不是一次性交付。',
        ownSource: false,
        contentHash: 'c'.repeat(64),
        publishedAt: null,
        publishedAtSource: null,
      },
      {
        id: 'cit_fixture_04',
        ordinal: 4,
        url: 'https://example.com/case-study',
        host: 'example.com',
        title: '制造企业知识助手实施案例',
        citedText: null,
        ownSource: true,
        contentHash: null,
        publishedAt: null,
        publishedAtSource: null,
      },
    ],
    evidence: [
      {
        id: 'evd_fixture_share_link_01',
        relation: 'official_share_link',
        kind: 'share_link',
        mimeType: 'application/json',
        byteSize: 256,
        sha256: 'd'.repeat(64),
        sourceUrl: 'https://chat.deepseek.com/share/fixture-answer-01',
        captureTime: '2026-08-17T07:42:00Z',
      },
    ],
    shareArtifact: {
      platform: 'deepseek',
      status: 'available',
      shareUrl: 'https://chat.deepseek.com/share/fixture-answer-01',
      finalUrl: 'https://chat.deepseek.com/share/fixture-answer-01',
      availabilityStatus: 'reachable',
      httpStatus: 200,
      checkedAt: '2026-08-17T07:42:00Z',
      lastAccessibleAt: '2026-08-17T07:42:00Z',
      embedStatus: 'allowed',
      embedReason: 'no_restrictive_frame_policy',
    },
    projectionComplete: true,
  },
};

const customerAnswerLibraryFixtureSnapshot: CustomerAnswerLibrarySnapshot = {
  snapshotId: `als_${'1'.repeat(24)}`,
  snapshotAt: '2026-08-17T08:00:00Z',
};

const fixtureLibraryHex = (value: number): string => value.toString(16).padStart(24, '0');
const fixtureLibraryLabels = [
  '制造企业私有化知识库选型',
  '企业 AI 知识助手数据安全',
  '知识库产品实施服务比较',
  '行业知识治理与权限设计',
  '大模型回答可追溯性',
  '企业 RAG 检索质量评估',
  '私有化部署成本与周期',
  '多模型接入与统一治理',
] as const;

const fixtureLibraryQuestionTexts = (metaIndex: number, label: string): string[] => {
  if (metaIndex === 0) {
    return [
      '制造企业如何选择可信的私有化知识库？',
      '私有化知识库选型需要关注哪些指标？',
      '哪些企业知识库方案适合制造业？',
      '如何验证企业知识助手的安全性与可追溯性？',
    ];
  }
  return [
    `${label}应该如何评估？`,
    `选择${label}时需要核验哪些能力？`,
    `${label}有哪些常见方案与风险？`,
    `企业如何对比${label}的实际效果？`,
  ];
};

const customerAnswerLibraryFixtureMetas: CustomerAnswerLibraryPage['data'] = Array.from(
  { length: 34 },
  (_, metaIndex) => {
    const ordinal = metaIndex + 1;
    const label =
      fixtureLibraryLabels[metaIndex] ?? `企业 GEO 监测主题 ${String(ordinal).padStart(2, '0')}`;
    const answerCount = 28 + (metaIndex % 7) * 4;
    const questionTexts = fixtureLibraryQuestionTexts(metaIndex, label);
    return {
      meta_query_id: `amq_${fixtureLibraryHex(ordinal)}`,
      ordinal,
      label,
      question_count: 4,
      answer_count: answerCount,
      cited_answer_count: Math.floor(answerCount * 0.78),
      citation_count: answerCount * 3 + (metaIndex % 5),
      mentioned_answer_count: Math.floor(answerCount * 0.69),
      latest_capture_time: '2026-08-17T07:42:00Z',
      models: [
        { label: 'DeepSeek', answer_count: Math.ceil(answerCount / 3) },
        { label: '豆包', answer_count: Math.floor(answerCount / 3) },
        {
          label: '通义千问',
          answer_count: answerCount - Math.ceil(answerCount / 3) - Math.floor(answerCount / 3),
        },
      ],
      regions: [
        { label: '华东', answer_count: Math.ceil(answerCount / 2) },
        { label: '华北', answer_count: Math.floor(answerCount / 4) },
        {
          label: '华南',
          answer_count: answerCount - Math.ceil(answerCount / 2) - Math.floor(answerCount / 4),
        },
      ],
      modes: [
        { label: '深度回答', answer_count: Math.ceil(answerCount * 0.65) },
        { label: '快速回答', answer_count: answerCount - Math.ceil(answerCount * 0.65) },
      ],
      questions: questionTexts.map((text, questionIndex) => ({
        question_id: `aq_${fixtureLibraryHex(metaIndex * 4 + questionIndex + 101)}`,
        ordinal: questionIndex + 1,
        variant_label:
          ['原问题', '变体 A', '变体 B', '变体 C'][questionIndex] ?? `变体 ${questionIndex}`,
        text,
        answer_count: Math.floor(answerCount / 4) + (questionIndex < answerCount % 4 ? 1 : 0),
      })),
    };
  },
);

const customerAnswerLibraryFixtureTotals = customerAnswerLibraryFixtureMetas.reduce(
  (totals, meta) => ({
    answer_count: totals.answer_count + meta.answer_count,
    cited_answer_count: totals.cited_answer_count + meta.cited_answer_count,
    citation_count: totals.citation_count + meta.citation_count,
    mentioned_answer_count: totals.mentioned_answer_count + meta.mentioned_answer_count,
  }),
  { answer_count: 0, cited_answer_count: 0, citation_count: 0, mentioned_answer_count: 0 },
);

const customerAnswerLibraryFixturePage: CustomerAnswerLibraryPage = {
  schema_version: 'customer-answer-library-v1',
  project_pub_id: customerDashboardFixture.project_pub_id,
  snapshot_id: customerAnswerLibraryFixtureSnapshot.snapshotId,
  snapshot_at: customerAnswerLibraryFixtureSnapshot.snapshotAt,
  totals: {
    meta_query_count: customerAnswerLibraryFixtureMetas.length,
    question_count: customerAnswerLibraryFixtureMetas.length * 4,
    ...customerAnswerLibraryFixtureTotals,
    unmapped_answer_count: 3,
  },
  models: [
    { label: 'DeepSeek', answer_count: 449 },
    { label: '豆包', answer_count: 438 },
    { label: '通义千问', answer_count: 425 },
  ],
  regions: [
    { label: '华东', answer_count: 656 },
    { label: '华北', answer_count: 328 },
    { label: '华南', answer_count: 328 },
  ],
  modes: [
    { label: '深度回答', answer_count: 853 },
    { label: '快速回答', answer_count: 459 },
  ],
  data: customerAnswerLibraryFixtureMetas.slice(0, 8),
  page: { total: 34, offset: 0, limit: 8, has_more: true },
};

const fixtureLibraryMetaDetail = (metaQueryId: string): CustomerAnswerLibraryMetaDetail | null => {
  const meta = customerAnswerLibraryFixtureMetas.find((item) => item.meta_query_id === metaQueryId);
  if (!meta) return null;
  return {
    schema_version: 'customer-answer-library-meta-v1',
    project_pub_id: customerDashboardFixture.project_pub_id,
    snapshot_id: customerAnswerLibraryFixtureSnapshot.snapshotId,
    snapshot_at: customerAnswerLibraryFixtureSnapshot.snapshotAt,
    meta_query_id: meta.meta_query_id,
    ordinal: meta.ordinal,
    label: meta.label,
    answer_count: meta.answer_count,
    cited_answer_count: meta.cited_answer_count,
    citation_count: meta.citation_count,
    mentioned_answer_count: meta.mentioned_answer_count,
    latest_capture_time: meta.latest_capture_time,
    questions: meta.questions.map((question) => ({
      ...question,
      cited_answer_count: Math.floor(question.answer_count * 0.78),
      citation_count: question.answer_count * 3,
      mentioned_answer_count: Math.floor(question.answer_count * 0.69),
      latest_capture_time: meta.latest_capture_time,
      models: meta.models,
      regions: meta.regions,
      modes: meta.modes,
    })),
  };
};

const fixtureLibraryQuestion = (questionId: string) => {
  for (const meta of customerAnswerLibraryFixtureMetas) {
    const detail = fixtureLibraryMetaDetail(meta.meta_query_id);
    const question = detail?.questions.find((item) => item.question_id === questionId);
    if (question && detail) return { meta: detail, question };
  }
  return null;
};

const fixtureLibraryRuns = (questionId: string): CustomerAnswerLibraryRun[] => {
  const selected = fixtureLibraryQuestion(questionId);
  if (!selected) return [];
  const models = ['DeepSeek', '豆包', '通义千问'] as const;
  const regions = ['华东', '华北', '华南'] as const;
  const modes = ['深度回答', '快速回答'] as const;
  return Array.from({ length: selected.question.answer_count }, (_, index) => ({
    answer_pub_id:
      questionId === customerAnswerLibraryFixtureMetas[0]?.questions[0]?.question_id && index === 0
        ? 'ans_fixture_01'
        : `ans_fixture_${questionId.slice(-6)}_${String(index + 1).padStart(2, '0')}`,
    repeat_index: Math.floor(index / (models.length * regions.length * modes.length)) + 1,
    model: models[index % models.length] ?? 'DeepSeek',
    region: regions[Math.floor(index / models.length) % regions.length] ?? '华东',
    mode: modes[Math.floor(index / (models.length * regions.length)) % modes.length] ?? '深度回答',
    capture_time: new Date(Date.parse('2026-08-17T07:42:00Z') - index * 3_600_000).toISOString(),
    analysis_state: index === selected.question.answer_count - 1 ? 'pending' : 'ready',
    mentioned: index === selected.question.answer_count - 1 ? null : index % 4 !== 0,
    rank: index % 4 === 0 ? null : (index % 5) + 1,
    sentiment:
      index === selected.question.answer_count - 1
        ? null
        : index % 3 === 0
          ? 'neutral'
          : 'positive',
    recommended: index === selected.question.answer_count - 1 ? null : index % 3 !== 0,
    citation_count: index % 5,
  }));
};

const fixtureLibraryAnswer = (answerPubId: string): CustomerAnswerLibraryAnswer | null => {
  for (const meta of customerAnswerLibraryFixtureMetas) {
    const metaDetail = fixtureLibraryMetaDetail(meta.meta_query_id);
    if (!metaDetail) continue;
    for (const question of metaDetail.questions) {
      const run = fixtureLibraryRuns(question.question_id).find(
        (candidate) => candidate.answer_pub_id === answerPubId,
      );
      if (!run) continue;
      const original = customerAnswerFixturePage.data.find(
        (candidate) => candidate.answer_pub_id === answerPubId,
      );
      return {
        schema_version: 'customer-answer-library-detail-v1',
        project_pub_id: customerDashboardFixture.project_pub_id,
        snapshot_id: customerAnswerLibraryFixtureSnapshot.snapshotId,
        snapshot_at: customerAnswerLibraryFixtureSnapshot.snapshotAt,
        meta_query_id: meta.meta_query_id,
        meta_query_ordinal: meta.ordinal,
        meta_query_label: meta.label,
        question_id: question.question_id,
        question_ordinal: question.ordinal,
        variant_label: question.variant_label,
        question_text: question.text,
        answer: run,
        response_text:
          original?.response_text ??
          `# ${question.variant_label}\n\n这是采集后仅在第四层按需读取的完整答案。\n\n针对“${question.text}”，建议从数据边界、权限治理、回答可追溯性与持续运营四个方面进行验证。`,
      };
    }
  }
  return null;
};

const fallbackMetricHelp: Readonly<Record<string, string>> = {
  mention_rate: '在所选统计区间和筛选条件内，提及目标品牌的回答数 ÷ 有效回答总数。',
  top3_rate: '目标品牌进入回答前三位的回答数 ÷ 有效回答总数。',
  average_rank: '仅对识别到品牌排名的回答计算平均位置；数值越小越靠前。',
  recommendation_rate: '明确推荐目标品牌的回答数 ÷ 可判定推荐倾向的有效回答数。',
  share_of_voice: '目标品牌在目标品牌与配置竞品全部有效提及中的占比。',
  citation_coverage: '至少含一条规范化引用的回答数 ÷ 有效回答总数。',
  own_source_answer_rate: '引用过品牌自有网站的回答数 ÷ 有效回答总数。',
  positive_rate: '正面回答数 ÷ 已完成情感判定的有效回答数。',
};

function HelpTip({ label, children }: { label: string; children: string }) {
  return (
    <span
      className="geo-help-tip"
      tabIndex={0}
      aria-label={`${label}说明：${children}`}
      onClick={(event) => event.stopPropagation()}
    >
      <span aria-hidden="true">?</span>
      <span className="geo-help-tip__content" role="tooltip">
        <strong>{label}</strong>
        {children}
      </span>
    </span>
  );
}

const metricHelpText = (
  metric: CustomerMetricProjection | undefined,
  catalog: ReadonlyMap<string, CustomerMetricSpecProjection>,
): string =>
  (metric ? catalog.get(metric.code)?.description : undefined) ??
  (metric ? fallbackMetricHelp[metric.code] : undefined) ??
  '该值来自版本化客户事实快照，并受页面所选统计区间、模型、地区和回答模式影响。';

function ScoreCard({
  metric,
  catalog,
}: {
  metric: CustomerMetricProjection | undefined;
  catalog: ReadonlyMap<string, CustomerMetricSpecProjection>;
}) {
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
        <span className="geo-metric-label-with-help">
          {metric?.label ?? '指标未配置'}
          <HelpTip label={metric?.label ?? '指标'}>{metricHelpText(metric, catalog)}</HelpTip>
        </span>
        <small>{ready ? '0–100 综合评分' : '等待数据'}</small>
      </div>
    </article>
  );
}

function SectionHeading({
  eyebrow,
  title,
  detail,
  help,
}: {
  eyebrow: string;
  title: string;
  detail: string;
  help?: string;
}) {
  return (
    <div className="geo-section-heading">
      <div>
        <span>{eyebrow}</span>
        <h2>
          {title}
          {help ? <HelpTip label={title}>{help}</HelpTip> : null}
        </h2>
      </div>
      <p>{detail}</p>
    </div>
  );
}

function MetricCards({
  dashboard,
  catalog,
  codes = coreMetricCodes,
}: {
  dashboard: CustomerDashboardProjection;
  catalog: ReadonlyMap<string, CustomerMetricSpecProjection>;
  codes?: readonly string[];
}) {
  return (
    <div className="geo-kpi-grid">
      {codes.map((code) => {
        const metric = metricValue(dashboard.metrics, code);
        return (
          <article className="geo-kpi-card" key={code}>
            <div className="geo-kpi-label">
              <span className="geo-metric-label-with-help">
                {metric?.label ?? code}
                <HelpTip label={metric?.label ?? code}>{metricHelpText(metric, catalog)}</HelpTip>
              </span>
              {metric?.state !== 'ready' ? <Badge tone="warning">待计算</Badge> : null}
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
  const points = rows.flatMap((row, index) => {
    const value = numericMetric(row.metrics, 'mention_rate');
    return value === null ? [] : [`${x(index)},${y(value)}`];
  });
  return (
    <div className="geo-trend-wrap">
      <div className="geo-chart-legend">
        <span>
          <i style={{ background: '#2563eb' }} /> {dashboard.brand_name} · 提及率
        </span>
      </div>
      <svg
        className="geo-trend-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${dashboard.brand_name}提及率趋势`}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <g key={tick}>
            <line x1={left} x2={width - 24} y1={y(tick)} y2={y(tick)} />
            <text x={left - 10} y={y(tick) + 4} textAnchor="end">
              {Math.round(tick * 100)}%
            </text>
          </g>
        ))}
        {points.length > 1 ? (
          <polyline points={points.join(' ')} fill="none" stroke="#2563eb" strokeWidth="3" />
        ) : null}
        {rows.map((row, index) => {
          const value = numericMetric(row.metrics, 'mention_rate');
          return value === null ? null : (
            <circle key={row.date} cx={x(index)} cy={y(value)} r="4" fill="#2563eb">
              <title>{`${row.date} · 提及率 ${(value * 100).toFixed(1)}%`}</title>
            </circle>
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

function VisibilityBenchmark({ dashboard }: { dashboard: CustomerDashboardProjection }) {
  const hasCompetitorMentionRate = dashboard.competitors.some(
    (competitor) => numericMetric(competitor.metrics, 'mention_rate') !== null,
  );
  const metricCode = hasCompetitorMentionRate ? 'mention_rate' : 'share_of_voice';
  const metricLabel = hasCompetitorMentionRate ? '提及率' : '心智份额';
  const brandValue = numericMetric(dashboard.metrics, metricCode);
  const competitors = dashboard.competitors.flatMap((competitor) => {
    const value = numericMetric(competitor.metrics, metricCode);
    return value === null ? [] : [{ name: competitor.name, value }];
  });
  if (brandValue === null || competitors.length === 0) return <StatePanel state="insufficient" />;
  const ranked = [
    { name: dashboard.brand_name, value: brandValue, brand: true },
    ...competitors.map((row) => ({ ...row, brand: false })),
  ].sort((left, right) => right.value - left.value);
  const brandRank = ranked.findIndex((row) => row.brand) + 1;
  const bestCompetitor = competitors.reduce((best, row) => (row.value > best.value ? row : best));
  const gap = brandValue - bestCompetitor.value;
  return (
    <div className="geo-visibility-benchmark">
      <header>
        <div>
          <span>COMPETITOR BENCHMARK</span>
          <h3>
            同期竞品基准
            <HelpTip label="同期竞品基准">
              {hasCompetitorMentionRate
                ? '目标品牌与配置竞品使用相同统计区间和筛选条件比较提及率。'
                : '竞品暂未提供逐日提及率序列，因此使用同一统计区间的心智份额做横向基准，不把聚合值伪装成趋势。'}
            </HelpTip>
          </h3>
        </div>
        <Badge tone="info">{metricLabel}</Badge>
      </header>
      <div className="geo-visibility-benchmark__verdict">
        <strong>
          第 {brandRank} / {ranked.length} 名
        </strong>
        <span>
          {gap >= 0 ? '领先' : '低于'}最高竞品 {Math.abs(gap * 100).toFixed(1)} 个百分点
        </span>
      </div>
      <div className="geo-visibility-benchmark__rows">
        {ranked.map((item) => (
          <div key={item.name} data-brand={item.brand ? 'true' : 'false'}>
            <span>
              <strong>{item.name}</strong>
              <small>{item.brand ? '目标品牌' : '配置竞品'}</small>
            </span>
            <i>
              <b style={{ width: `${Math.max(0, Math.min(100, item.value * 100))}%` }} />
            </i>
            <em>{(item.value * 100).toFixed(1)}%</em>
          </div>
        ))}
      </div>
    </div>
  );
}

function VisibilityAnalysis({ dashboard }: { dashboard: CustomerDashboardProjection }) {
  return (
    <section className="geo-dashboard-panel geo-visibility-analysis">
      <div className="geo-visibility-analysis__trend">
        <header>
          <span>BRAND TREND</span>
          <h3>品牌提及率趋势</h3>
        </header>
        <TrendChart dashboard={dashboard} />
      </div>
      <VisibilityBenchmark dashboard={dashboard} />
    </section>
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
        <Badge tone="info">同一统计区间</Badge>
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

const assetScaleMetrics = [
  {
    code: 'answer_count',
    label: '真实 AI 回答',
    unit: '条回答',
    detail: '进入当前事实窗口的真实回答',
    tone: 'answer',
  },
  {
    code: 'unique_source_hosts',
    label: '独立信源网站',
    unit: '个网站',
    detail: '按引用域名去重后的信源覆盖',
    tone: 'website',
  },
  {
    code: 'unique_source_pages',
    label: '独立信源页面',
    unit: '个页面',
    detail: '按规范化 URL 去重后的页面资产',
    tone: 'page',
  },
  {
    code: 'citation_references',
    label: '真实引用记录',
    unit: '次引用',
    detail: '回答正文中保留的引用关系',
    tone: 'citation',
  },
] as const;

function AssetScalePanel({ dashboard }: { dashboard: CustomerDashboardProjection }) {
  return (
    <section className="geo-asset-scale" aria-labelledby="geo-asset-scale-title">
      <header>
        <span>Business Data Scale</span>
        <h3 id="geo-asset-scale-title">所选统计区间沉淀的 AI 认知资产</h3>
        <p>所有数字均直接来自已保存的回答与引用事实，可继续下钻到回答、网站和页面。</p>
      </header>
      <div className="geo-asset-scale__grid">
        {assetScaleMetrics.map((item) => (
          <article data-tone={item.tone} key={item.code}>
            <span>{item.label}</span>
            <strong>
              <b>{formatMetric(metricValue(dashboard.metrics, item.code))}</b>
              <small>{item.unit}</small>
            </strong>
            <p>{item.detail}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

const contentReadinessMetrics = [
  ['source_audit_count', '已审计事实', '已经完成事实核验的信源记录'],
  ['source_accuracy_rate', '准确率', '审计结果中被判定准确的比例'],
  ['source_unsupported_rate', '无依据率', '缺少事实支持、需要补证的比例'],
  ['source_unverifiable_rate', '无法核实率', '当前材料不足以核实的比例'],
  ['cited_text_visibility_rate', '引用原文可见率', '引用中保留可核验原文的比例'],
  ['citation_title_visibility_rate', '引用标题可见率', '引用中保留页面标题的比例'],
] as const;

function ContentReadinessPanel({ dashboard }: { dashboard: CustomerDashboardProjection }) {
  const readiness = metricValue(dashboard.metrics, 'content_readiness_index');
  const auditMetric = (code: string) =>
    metricValue(dashboard.source_audit.metrics, code) ?? metricValue(dashboard.metrics, code);
  return (
    <section className="geo-content-readiness" aria-labelledby="geo-content-readiness-title">
      <div className="geo-content-readiness__score">
        <span>Content Readiness</span>
        <h3 id="geo-content-readiness-title">内容准备度与事实审计</h3>
        <p>把“有多少内容”进一步拆成“是否准确、是否可核验、引用材料是否完整”。</p>
        <strong>
          {formatMetric(readiness, true)}
          <small>/100</small>
        </strong>
      </div>
      <div className="geo-content-readiness__metrics">
        {contentReadinessMetrics.map(([code, label, detail]) => (
          <article key={code}>
            <span>{label}</span>
            <strong>{formatMetric(auditMetric(code))}</strong>
            <p>{detail}</p>
          </article>
        ))}
      </div>
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
  const websiteCount = metricValue(dashboard.metrics, 'unique_source_hosts');
  const pageCount = metricValue(dashboard.metrics, 'unique_source_pages');
  const referenceCount = metricValue(dashboard.metrics, 'citation_references');
  const citationCoverage = metricValue(dashboard.metrics, 'citation_coverage');
  return (
    <section className="geo-dashboard-panel">
      <header className="geo-source-scale" aria-labelledby="geo-source-scale-title">
        <div className="geo-source-scale__copy">
          <span>Source Intelligence Scale</span>
          <h3 id="geo-source-scale-title">AI 信源资产规模</h3>
          <p>从真实 AI 回答引用中汇总：网站按域名去重，页面按规范化 URL 去重。</p>
          <div className="geo-source-scale__facts" aria-label="信源引用概况">
            <span>
              <b>{formatMetric(referenceCount)}</b> 次真实引用
            </span>
            <span>
              <b>{formatMetric(citationCoverage)}</b> 回答含引用
            </span>
          </div>
        </div>
        <div className="geo-source-scale__numbers">
          <div data-tone="website">
            <span>独立信源网站</span>
            <strong>
              <b>{formatMetric(websiteCount)}</b>
              <small>个网站</small>
            </strong>
            <p>跨域信源覆盖规模</p>
          </div>
          <div data-tone="page">
            <span>独立信源页面</span>
            <strong>
              <b>{formatMetric(pageCount)}</b>
              <small>个页面</small>
            </strong>
            <p>去重后的引用页面</p>
          </div>
        </div>
      </header>
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
          <span>Question coverage</span>
          <h3>
            低提及问题清单
            <HelpTip label="低提及问题清单">
              仅按所选统计区间内的品牌提及率从低到高排列，表示品牌较少进入这些回答；不自动等于商业优先级或增长机会。
            </HelpTip>
          </h3>
        </div>
        <Badge tone="warning">按提及率升序</Badge>
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
        <span className="geo-filter-label">
          统计区间
          <HelpTip label="统计区间">
            页面所有指标只统计所选日期范围内、且符合模型、地区和回答模式筛选条件的有效回答；它不是实时瞬时值。
          </HelpTip>
        </span>
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

function DashboardScope({ dashboard }: { dashboard: CustomerDashboardProjection }) {
  const activeFilters = Object.entries(dashboard.window.filters).filter(
    ([, value]) => value !== null && value !== undefined && String(value).trim() !== '',
  );
  return (
    <div className="geo-dashboard-scope" aria-label="当前统计口径">
      <span>统计区间</span>
      <strong>
        {dashboard.window.start} 至 {dashboard.window.end}
      </strong>
      <HelpTip label="本页统计口径">
        所有经营指标、趋势与竞品基准均来自这个日期范围内的有效回答，并继续受右侧模型、地区和回答模式筛选影响。
      </HelpTip>
      <small>
        {activeFilters.length
          ? `已应用 ${activeFilters.map(([key, value]) => `${key}=${String(value)}`).join(' · ')}`
          : '全部模型 · 全部地区 · 全部回答模式'}
      </small>
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
    const sync = () => {
      if (experience?.source === 'live') setState('loading');
      setUrlState(readDashboardUrlState());
    };
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, [experience?.source]);
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

  const loadAnswerLibraryPage = useCallback(
    async (query: CustomerAnswerLibraryRootQuery): Promise<CustomerAnswerLibraryPage> => {
      if (experience?.source !== 'live' || !experience.projectPubId) {
        const needle = query.search.trim().toLocaleLowerCase('zh-CN');
        const matches = customerAnswerLibraryFixtureMetas.filter((meta) =>
          needle.length === 0
            ? true
            : `${meta.label}\n${meta.questions.map((question) => question.text).join('\n')}`
                .toLocaleLowerCase('zh-CN')
                .includes(needle),
        );
        const data = matches.slice(query.offset, query.offset + query.limit);
        return {
          ...customerAnswerLibraryFixturePage,
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
      if (!headers) throw new CustomerAnswerLoadError('forbidden');
      const result = await getCustomerAnswerLibraryPage(
        experience.projectPubId,
        activeDateWindow.start,
        activeDateWindow.end,
        {
          ...activeFilters,
          ...(query.search ? { search: query.search } : {}),
          ...(query.snapshotId && query.snapshotAt
            ? { snapshot_id: query.snapshotId, snapshot_at: query.snapshotAt }
            : {}),
          offset: query.offset,
          limit: query.limit,
        },
        headers,
      );
      if (result.kind !== 'ready') throw new CustomerAnswerLoadError(result.kind);
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

  const loadAnswerMetaQuery = useCallback(
    async (
      metaQueryId: string,
      snapshot: CustomerAnswerLibrarySnapshot,
    ): Promise<CustomerAnswerLibraryMetaDetail> => {
      if (experience?.source !== 'live' || !experience.projectPubId) {
        const fixture = fixtureLibraryMetaDetail(metaQueryId);
        if (!fixture) throw new CustomerAnswerLoadError('unavailable');
        return fixture;
      }
      const headers = getValidatedIdentityHeaders();
      if (!headers) throw new CustomerAnswerLoadError('forbidden');
      const result = await getCustomerAnswerLibraryMetaQuery(
        experience.projectPubId,
        metaQueryId,
        activeDateWindow.start,
        activeDateWindow.end,
        {
          snapshot_id: snapshot.snapshotId,
          snapshot_at: snapshot.snapshotAt,
          ...activeFilters,
        },
        headers,
      );
      if (result.kind !== 'ready') throw new CustomerAnswerLoadError(result.kind);
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

  const loadAnswerQuestionRuns = useCallback(
    async (
      questionId: string,
      query: CustomerAnswerLibraryRunQuery,
    ): Promise<CustomerAnswerLibraryRuns> => {
      if (experience?.source !== 'live' || !experience.projectPubId) {
        const selected = fixtureLibraryQuestion(questionId);
        if (!selected) throw new CustomerAnswerLoadError('unavailable');
        const matches = fixtureLibraryRuns(questionId).filter(
          (run) =>
            (query.model === 'all' || run.model === query.model) &&
            (query.region === 'all' || run.region === query.region) &&
            (query.mode === 'all' || run.mode === query.mode),
        );
        const data = matches.slice(query.offset, query.offset + query.limit);
        return {
          schema_version: 'customer-answer-library-runs-v1',
          project_pub_id: customerDashboardFixture.project_pub_id,
          snapshot_id: customerAnswerLibraryFixtureSnapshot.snapshotId,
          snapshot_at: customerAnswerLibraryFixtureSnapshot.snapshotAt,
          meta_query_id: selected.meta.meta_query_id,
          meta_query_ordinal: selected.meta.ordinal,
          meta_query_label: selected.meta.label,
          question: selected.question,
          models: selected.question.models,
          regions: selected.question.regions,
          modes: selected.question.modes,
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
      if (!headers) throw new CustomerAnswerLoadError('forbidden');
      const result = await getCustomerAnswerLibraryQuestionRuns(
        experience.projectPubId,
        questionId,
        activeDateWindow.start,
        activeDateWindow.end,
        {
          snapshot_id: query.snapshotId,
          snapshot_at: query.snapshotAt,
          ...(query.model !== 'all'
            ? { model: query.model }
            : activeFilters.model
              ? { model: activeFilters.model }
              : {}),
          ...(query.region !== 'all'
            ? { region: query.region }
            : activeFilters.region
              ? { region: activeFilters.region }
              : {}),
          ...(query.mode !== 'all'
            ? { mode: query.mode }
            : activeFilters.mode
              ? { mode: activeFilters.mode }
              : {}),
          offset: query.offset,
          limit: query.limit,
        },
        headers,
      );
      if (result.kind !== 'ready') throw new CustomerAnswerLoadError(result.kind);
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

  const loadAnswerContent = useCallback(
    async (
      answerPubId: string,
      snapshot: CustomerAnswerLibrarySnapshot,
    ): Promise<CustomerAnswerLibraryAnswer> => {
      if (experience?.source !== 'live' || !experience.projectPubId) {
        const fixture = fixtureLibraryAnswer(answerPubId);
        if (!fixture) throw new CustomerAnswerLoadError('unavailable');
        return fixture;
      }
      const headers = getValidatedIdentityHeaders();
      if (!headers) throw new CustomerAnswerLoadError('forbidden');
      const result = await getCustomerAnswerLibraryDetail(
        experience.projectPubId,
        answerPubId,
        activeDateWindow.start,
        activeDateWindow.end,
        { snapshot_id: snapshot.snapshotId, snapshot_at: snapshot.snapshotAt },
        headers,
      );
      if (result.kind !== 'ready') throw new CustomerAnswerLoadError(result.kind);
      return result.data;
    },
    [activeDateWindow.end, activeDateWindow.start, experience?.projectPubId, experience?.source],
  );

  const loadAnswerDetail = useCallback(
    async (
      answerPubId: string,
      snapshot: CustomerAnswerLibrarySnapshot,
    ): Promise<CustomerAnswerDetail> => {
      if (experience?.source !== 'live') {
        return (
          customerAnswerFixtureDetails[answerPubId] ?? {
            citations: [],
            evidence: [],
            projectionComplete: true,
          }
        );
      }
      const headers = getValidatedIdentityHeaders();
      if (!headers) throw new CustomerAnswerLoadError('forbidden');
      const result = await getAnalyticsAnswerRelations(
        answerPubId,
        headers,
        undefined,
        experience.projectPubId,
        snapshot.snapshotAt,
      );
      if (result.kind !== 'ready') throw new Error(`customer answer detail ${result.kind}`);
      const collections = Object.values(result.data.projection);
      return {
        citations: result.data.answer_citations.map((citation) => ({
          id: citation.pub_id,
          ordinal: citation.ordinal,
          url: citation.canonical_url,
          host: citation.host,
          title: citation.title,
          citedText: citation.cited_text,
          ownSource: citation.own_source,
          contentHash: citation.content_hash,
          publishedAtRaw: citation.published_at_raw ?? null,
          publishedAt: citation.published_at ?? null,
          publishedAtTimezone: citation.published_at_timezone ?? null,
          publishedAtPrecision:
            citation.published_at_precision === 'date' ||
            citation.published_at_precision === 'minute' ||
            citation.published_at_precision === 'second'
              ? citation.published_at_precision
              : null,
          publishedAtSource: citation.published_at_source ?? null,
          publishedAtConfidence: citation.published_at_confidence,
          support: {
            mappingStatus: citation.support.mapping_status,
            answerSentence: citation.support.answer_sentence ?? null,
            sourceQuote: citation.support.source_quote ?? null,
            sourceQuoteHash: citation.support.source_quote_hash ?? null,
            sourceMatchStatus: citation.support.source_match_status,
            relation: citation.support.relation,
            relevanceConfidence: citation.support.relevance_confidence ?? null,
            reviewStatus: citation.support.review_status,
          },
        })),
        evidence: result.data.evidence.map((evidence) => ({
          id: evidence.pub_id,
          relation: evidence.relation_type,
          kind: evidence.kind,
          mimeType: evidence.mime_type,
          byteSize: evidence.byte_size,
          sha256: evidence.sha256,
          sourceUrl: evidence.source_url,
          captureTime: evidence.capture_time,
        })),
        shareImage: result.data.share_image
          ? {
              id: result.data.share_image.pub_id,
              relation: 'official_share_image',
              kind: 'share_image',
              mimeType: result.data.share_image.mime_type,
              byteSize: result.data.share_image.byte_size,
              sha256: result.data.share_image.sha256,
              sourceUrl: null,
              captureTime: result.data.share_image.capture_time,
            }
          : null,
        shareArtifact: result.data.share_artifact
          ? {
              platform: result.data.share_artifact.platform,
              status: result.data.share_artifact.status,
              shareUrl: result.data.share_artifact.share_url ?? null,
              finalUrl: result.data.share_artifact.final_url ?? null,
              availabilityStatus: result.data.share_artifact.availability_status,
              httpStatus: result.data.share_artifact.http_status ?? null,
              checkedAt: result.data.share_artifact.checked_at ?? null,
              lastAccessibleAt: result.data.share_artifact.last_accessible_at ?? null,
              embedStatus: result.data.share_artifact.embed_status,
              embedReason: result.data.share_artifact.embed_reason ?? null,
            }
          : null,
        projectionComplete: collections.every(
          (collection) => !collection.invalid && collection.total === collection.shown,
        ),
      };
    },
    [experience?.projectPubId, experience?.source],
  );

  const loadAnswerEvidenceImage = useCallback<CustomerAnswerEvidenceImageLoader>(
    async (evidence) => {
      const headers = getValidatedIdentityHeaders();
      if (!headers) return { kind: 'forbidden' };
      const result = await getEvidenceAssetContent(
        evidence.id,
        {
          byteSize: evidence.byteSize,
          mimeType: evidence.mimeType,
          sha256: evidence.sha256,
        },
        headers,
      );
      return result.kind === 'ready'
        ? { kind: 'ready', blob: result.data.blob }
        : { kind: result.kind };
    },
    [],
  );

  const setFilter = (key: string, value: string) => {
    const nextValue = value === 'all' || (key === 'window' && value === '30d') ? null : value;
    // Unmount the old answer explorer in the same event update. Otherwise its loadPage callback
    // changes before the dashboard effect marks this workspace as loading, which sends one request
    // for the outgoing view and then an identical request after the refreshed dashboard mounts.
    if (experience?.source === 'live') setState('loading');
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

  const visibleFilterOptions =
    state === 'fixture'
      ? {
          model: dashboard.models.map((row) => row.key),
          region: dashboard.regions.map((row) => row.key),
          mode: dashboard.modes.map((row) => row.key),
        }
      : filterOptions;

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
      <DashboardFilters options={visibleFilterOptions} urlState={urlState} setFilter={setFilter} />
      <DashboardScope dashboard={dashboard} />

      {focus === 'overview' ? (
        <DashboardSection>
          <AssetScalePanel dashboard={dashboard} />
          <SectionHeading
            eyebrow="Executive Scores"
            title="六大经营指数"
            detail="从多个基础指标合成 0–100 分，便于管理层快速定位增长短板。"
            help="六项指数按已发布的指标版本由多项基础事实合成，0–100 只表示内部统一量尺；是否高于同行应结合下方竞品基准判断。"
          />
          <div className="geo-score-grid">
            {compositeCodes.map((code) => (
              <ScoreCard
                metric={metricValue(dashboard.metrics, code)}
                catalog={catalog}
                key={code}
              />
            ))}
          </div>
          <MetricCards dashboard={dashboard} catalog={catalog} />
        </DashboardSection>
      ) : null}

      {focus === 'visibility' ? (
        <DashboardSection>
          <SectionHeading
            eyebrow="Visibility Metrics"
            title="品牌表现核心指标"
            detail="聚焦提及、推荐、排名与引用结果；完整回答统一进入“真实 AI 回答”下钻。"
            help="每项指标右侧的问号说明计算含义；所有数值都受当前统计区间、模型、地区和回答模式筛选影响。"
          />
          <MetricCards dashboard={dashboard} catalog={catalog} codes={visibilityMetricCodes} />
        </DashboardSection>
      ) : null}

      {focus === 'answers' ? (
        <CustomerAnswerExplorer
          key={`${dashboard.project_pub_id}:${windowValue}:${urlState.model}:${urlState.region}:${urlState.mode}`}
          brandName={dashboard.brand_name}
          loadLibraryPage={loadAnswerLibraryPage}
          loadMetaQuery={loadAnswerMetaQuery}
          loadQuestionRuns={loadAnswerQuestionRuns}
          loadAnswer={loadAnswerContent}
          loadDetail={loadAnswerDetail}
          {...(experience?.source === 'live' ? { loadEvidenceImage: loadAnswerEvidenceImage } : {})}
          {...(experience?.source === 'live'
            ? {}
            : { fixturePage: customerAnswerLibraryFixturePage })}
        />
      ) : null}

      {focus === 'overview' || focus === 'visibility' ? (
        <DashboardSection>
          <SectionHeading
            eyebrow="Time Series"
            title="品牌可见度与竞品基准"
            detail="左侧看品牌自身随时间的变化，右侧立即给出同一统计区间的竞品位置，避免孤立解读一个百分比。"
            help="品牌折线只连接真实存在的观察日期；竞品若没有逐日序列，就明确使用同期聚合心智份额做横向基准，不伪造竞品趋势。"
          />
          <VisibilityAnalysis dashboard={dashboard} />
          {focus === 'visibility' ? (
            <>
              <div className="geo-dashboard-columns">
                <DimensionTable title="模型表现" rows={dashboard.models} />
                <DimensionTable title="地区表现" rows={dashboard.regions} />
              </div>
              <DimensionTable title="回答模式表现" rows={dashboard.modes} />
            </>
          ) : null}
        </DashboardSection>
      ) : null}

      {focus === 'overview' ? (
        <DashboardSection>
          <div className="geo-dashboard-columns">
            <CompetitorPanel dashboard={dashboard} />
            <ReputationPanel dashboard={dashboard} />
          </div>
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
          <ContentReadinessPanel dashboard={dashboard} />
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
    </div>
  );
}
