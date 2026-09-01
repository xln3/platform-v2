import { useEffect, useMemo, useState } from 'react';
import { Pagination } from '@geo/design-system';
import {
  getAnalyticsBreakdown,
  getAnalyticsCompetitors,
  getAnalyticsOverview,
  type AnalyticsBreakdownProjection,
  type AnalyticsCompetitorProjection,
  type AnalyticsOverviewProjection,
} from '@geo/api-client';
import { usePageWindow } from '../../../pagination';
import { PlatformBadge } from '../../../platforms';
import { ReadonlyConfigSummary } from '../ReadonlyConfigSummary';
import { RunsPanel } from '../RunsPanel';
import { SamplingProgressPanel } from '../SamplingProgressPanel';
import { WindowPicker } from '../WindowPicker';
import {
  defaultWindow,
  servicesApi,
  type BrandVisibilityResult,
  type OfficialMetricCatalog,
  type OfficialMetricSnapshot,
  type OfficialMetricSnapshotSet,
  type Project,
  type ProjectEntityResource,
  type SessionContext,
} from '../api';
import {
  AI_RANKING_RUNS_PAGE_SIZE,
  BRAND_VISIBILITY_DEFAULT_PAGE_SIZE,
  BRAND_VISIBILITY_PAGE_NUMBER_WINDOW_SIZE,
  BRAND_VISIBILITY_PAGE_SIZE_OPTIONS,
} from '../pagination-policy';
import { SemanticBackfillLauncher } from './SemanticBackfillLauncher';

const METRIC_LABELS: Record<string, string> = {
  mention_rate: '品牌提及率',
  average_rank: '平均排名',
  top1_rate: 'Top 1 占比',
  top3_rate: 'Top 3 占比',
  top10_rate: 'Top 10 占比',
  citation_coverage: '引用覆盖',
  recommendation_rate: '品牌推荐率',
  ai_impression_effective_response_rate_v2: 'AI 印象有效回答率',
  ai_impression_neutral_spontaneous_association_rate_v2: '中性问题自然联想率',
  ai_impression_requested_dimension_coverage_v2: '要求维度覆盖率',
  ai_impression_unsolicited_recommendation_rate_v2: '非主动询问推荐率',
  ai_recommendation_entity_share_v2: '推荐实体份额',
  ai_recommendation_mean_rank_given_target_ranked_v2: '入榜时平均排名',
  ai_recommendation_organic_mention_rate_v2: '自然提及率',
  ai_recommendation_organic_recommendation_rate_v2: '自然推荐率',
  ai_recommendation_organic_top1_given_rankable_rate_v2: '可排名回答 Top1 率',
  ai_recommendation_organic_top1_visibility_rate_v2: 'Top1 可见率',
  ai_recommendation_organic_top3_given_rankable_rate_v2: '可排名回答 Top3 率',
  ai_recommendation_organic_top3_visibility_rate_v2: 'Top3 可见率',
  ai_recommendation_organic_top5_given_rankable_rate_v2: '可排名回答 Top5 率',
  ai_recommendation_organic_top5_visibility_rate_v2: 'Top5 可见率',
  ai_recommendation_rankable_response_rate_v2: '可排名回答率',
  brand_attribution_accuracy_v2: '品牌归因准确率',
  claim_accuracy_rate_v2: '事实主张准确率',
  competitor_anchored_target_alternative_rate_v2: '竞品锚定替代率',
  competitor_anchored_target_bring_in_rate_v2: '竞品锚定带入率',
  market_rank_claim_accuracy_v2: '市场排名主张准确率',
  multibrand_corecommendation_rate_v2: '多品牌共同推荐率',
  multibrand_pairwise_loss_rate_v2: '多品牌两两落败率',
  multibrand_pairwise_tie_rate_v2: '多品牌两两持平率',
  multibrand_pairwise_win_rate_v2: '多品牌两两胜出率',
  prompted_recommendation_conditional_rate_v2: '指定询问条件推荐率',
  prompted_recommendation_negative_rate_v2: '指定询问负向推荐率',
  prompted_recommendation_neutral_rate_v2: '指定询问中性推荐率',
  prompted_recommendation_positive_rate_v2: '指定询问正向推荐率',
  stale_information_rate_v2: '过时信息率',
  target_first_mention_order_rate_v2: '目标品牌首提率',
  target_stance_negative_rate_v2: '目标品牌负向态度率',
  target_stance_neutral_rate_v2: '目标品牌中性态度率',
  target_stance_positive_rate_v2: '目标品牌正向态度率',
  unsupported_claim_rate_v2: '无依据主张率',
};

const INDUSTRY_FIT_LABELS: Record<string, string> = {
  core_cybersecurity: '核心网安',
  adjacent_platform_security: '平台型安全',
  identity_security_specialist: '身份安全',
  scenario_specific_adjacent: '场景型相关',
  cybersecurity_integrator: '安全服务/集成',
  project_declared: '项目指定',
};

function formatLegacyMetric(metric: string, value: number | null): string {
  if (value === null) return '—';
  return metric === 'average_rank' ? value.toFixed(2) : `${(value * 100).toFixed(1)}%`;
}

function formatOfficialMetric(metric: OfficialMetricSnapshot): string {
  if (metric.value === null || metric.value === undefined) return '—';
  const isRank = metric.metric_name.includes('rank') && !metric.metric_name.includes('rate');
  return isRank ? metric.value.toFixed(2) : `${(metric.value * 100).toFixed(1)}%`;
}

function metricLabel(metricName: string): string {
  return METRIC_LABELS[metricName] ?? metricName;
}

function industryFitLabel(value: string | null | undefined): string {
  if (!value) return '已审核竞品';
  return INDUSTRY_FIT_LABELS[value] ?? value;
}

function entityName(entity: ProjectEntityResource | undefined, entityId: string): string {
  return entity?.data.name?.trim() || entityId;
}

type ResourceState<T> =
  | { kind: 'loading' }
  | { kind: 'ready'; data: T }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

export function VisibilityWorkspace({
  session,
  project,
}: {
  session: SessionContext;
  project: Project;
}) {
  const [window_, setWindow] = useState(defaultWindow);
  const [overview, setOverview] = useState<ResourceState<AnalyticsOverviewProjection>>({
    kind: 'loading',
  });
  const [breakdown, setBreakdown] = useState<ResourceState<AnalyticsBreakdownProjection>>({
    kind: 'loading',
  });
  const [competitors, setCompetitors] = useState<ResourceState<AnalyticsCompetitorProjection>>({
    kind: 'loading',
  });
  const [brands, setBrands] = useState<BrandVisibilityResult | { kind: 'loading' }>({
    kind: 'loading',
  });
  const [official, setOfficial] = useState<ResourceState<OfficialMetricSnapshotSet>>({
    kind: 'loading',
  });
  const [catalog, setCatalog] = useState<ResourceState<OfficialMetricCatalog>>({ kind: 'loading' });
  const [entities, setEntities] = useState<ProjectEntityResource[]>([]);
  const [brandPageSize, setBrandPageSize] = useState(BRAND_VISIBILITY_DEFAULT_PAGE_SIZE);

  useEffect(() => {
    let cancelled = false;
    setOverview({ kind: 'loading' });
    setBreakdown({ kind: 'loading' });
    setCompetitors({ kind: 'loading' });
    setBrands({ kind: 'loading' });
    void getAnalyticsOverview(project.pub_id, window_.start, window_.end, {}, session.headers).then(
      (result) => {
        if (!cancelled) setOverview(result.kind === 'ready' ? result : { kind: result.kind });
      },
    );
    void getAnalyticsBreakdown(
      project.pub_id,
      window_.start,
      window_.end,
      'model',
      {},
      session.headers,
    ).then((result) => {
      if (!cancelled) setBreakdown(result.kind === 'ready' ? result : { kind: result.kind });
    });
    void getAnalyticsCompetitors(
      project.pub_id,
      window_.start,
      window_.end,
      {},
      session.headers,
    ).then((result) => {
      if (!cancelled) setCompetitors(result.kind === 'ready' ? result : { kind: result.kind });
    });
    void servicesApi
      .brandVisibility(session, { projectPubId: project.pub_id, windowDays: 30 })
      .then((result) => {
        if (!cancelled) setBrands(result);
      });
    return () => {
      cancelled = true;
    };
  }, [session, project.pub_id, window_.start, window_.end]);

  useEffect(() => {
    let cancelled = false;
    setOfficial({ kind: 'loading' });
    setCatalog({ kind: 'loading' });
    setEntities([]);
    void servicesApi.officialMetricSnapshotSet(session, project.pub_id).then(
      (data) => {
        if (!cancelled) setOfficial({ kind: 'ready', data });
      },
      () => {
        if (!cancelled) setOfficial({ kind: 'unavailable' });
      },
    );
    void servicesApi.officialMetricCatalog(session).then(
      (data) => {
        if (!cancelled) setCatalog({ kind: 'ready', data });
      },
      () => {
        if (!cancelled) setCatalog({ kind: 'unavailable' });
      },
    );
    void servicesApi.projectEntities(session, project.pub_id).then(
      (data) => {
        if (!cancelled) setEntities(data);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [session, project.pub_id]);

  const rulepackDomain =
    project.brandrank_domain ?? (brands.kind === 'ready' ? brands.data.domain : undefined);
  const entityResolution =
    brands.kind === 'ready' ? brands.data.result?.entity_resolution : undefined;
  const pendingEntityNames = entityResolution?.counts?.unclassified_distinct_names ?? 0;
  const collapsedAliases = entityResolution?.counts?.alias_collapses_within_answers ?? 0;
  const brandRows = brands.kind === 'ready' ? (brands.data.result?.overall?.merged ?? []) : [];
  const brandPage = usePageWindow(
    brandRows,
    `${project.pub_id}:${window_.start}:${window_.end}`,
    brandPageSize,
  );
  const entityById = useMemo(
    () => new Map(entities.map((entity) => [entity.pub_id, entity])),
    [entities],
  );
  const v21Definitions =
    catalog.kind === 'ready' && Array.isArray(catalog.data.definitions)
      ? catalog.data.definitions.filter((definition) => definition.metric_version === '2.1.0')
      : [];
  const officialMetricNames = new Set(
    official.kind === 'ready' && Array.isArray(official.data.metrics)
      ? official.data.metrics.map((metric) => metric.metric_name)
      : [],
  );
  const officialKnownAnswers =
    official.kind === 'ready' && Array.isArray(official.data.metrics)
      ? Math.max(0, ...official.data.metrics.map((metric) => metric.known_answer_count))
      : 0;
  const observedAnswerCount =
    overview.kind === 'ready'
      ? Math.max(0, ...overview.data.data.map((metric) => metric.denominator))
      : 0;
  const targetBrandNames = new Set(
    entities
      .filter((entity) => entity.resource_kind === 'brands')
      .map((entity) => entity.data.name?.trim())
      .filter((name): name is string => Boolean(name)),
  );
  const targetBrandName = brands.kind === 'ready' ? brands.data.target_brand : undefined;
  const targetBrandRow =
    brandRows.find((row) => targetBrandNames.has(row.brand)) ??
    brandRows.find((row) => row.brand === targetBrandName) ??
    brandRows[0];
  const targetMentionRate =
    overview.kind === 'ready'
      ? overview.data.data.find((metric) => metric.metric === 'mention_rate')?.value
      : null;
  const competitorChartRows = [
    ...(targetBrandRow && targetMentionRate !== null && targetMentionRate !== undefined
      ? [{ name: targetBrandRow.brand, rate: targetMentionRate, target: true }]
      : []),
    ...(competitors.kind === 'ready'
      ? competitors.data.data.map((row) => ({
          name: row.competitor,
          rate: row.mention_rate,
          target: false,
        }))
      : []),
  ];
  const competitorChartMax = Math.max(0, ...competitorChartRows.map((row) => row.rate));
  const topBrandRows = brandRows.slice(0, 10);
  const topBrandScoreMax = Math.max(0, ...topBrandRows.map((row) => row.score));

  return (
    <>
      <ReadonlyConfigSummary session={session} projectPubId={project.pub_id} />
      <SamplingProgressPanel session={session} projectPubId={project.pub_id} />
      <RunsPanel
        session={session}
        projectPubId={project.pub_id}
        readOnly
        pageSize={AI_RANKING_RUNS_PAGE_SIZE}
      />
      <section className="execution-card">
        <div className="section-title">
          <h2>完整观测结果</h2>
          <span>当前汇报口径 · 冻结窗口内全部真实采集答案</span>
        </div>
        <WindowPicker start={window_.start} end={window_.end} onChange={setWindow} />

        <h3>核心指标</h3>
        {overview.kind === 'loading' ? (
          <p className="empty">正在计算指标…</p>
        ) : overview.kind === 'ready' ? (
          overview.data.data.length === 0 ? (
            <p className="empty">该时间窗内尚无评测指标。</p>
          ) : (
            <div className="metric-cards">
              {overview.data.data.map((metric) => (
                <article key={metric.metric}>
                  <span>{metricLabel(metric.metric)}</span>
                  <strong>{formatLegacyMetric(metric.metric, metric.value)}</strong>
                  <span>
                    {metric.numerator === null ? '—' : metric.numerator}/{metric.denominator} ·{' '}
                    {metric.state === 'ready' ? '可用' : '实验口径'}
                  </span>
                </article>
              ))}
            </div>
          )
        ) : (
          <p className="empty">
            {overview.kind === 'forbidden' ? '权限不足，无法读取评测指标。' : '指标暂不可用。'}
          </p>
        )}

        <h3>分平台表现</h3>
        {breakdown.kind === 'ready' && breakdown.data.data.length > 0 ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>平台</th>
                  <th>答案数</th>
                  <th>提及次数</th>
                  <th>提及率</th>
                  <th>平均排名</th>
                  <th>引用覆盖</th>
                </tr>
              </thead>
              <tbody>
                {breakdown.data.data.map((row, index) => (
                  <tr key={`${row.model ?? ''}-${index}`}>
                    <td>
                      <PlatformBadge platform={row.model ?? '—'} />
                    </td>
                    <td>{row.answer_count}</td>
                    <td>{row.mentioned_count}</td>
                    <td>
                      {row.mention_rate === null ? '—' : `${(row.mention_rate * 100).toFixed(1)}%`}
                    </td>
                    <td>{row.average_rank === null ? '—' : row.average_rank.toFixed(2)}</td>
                    <td>
                      {row.citation_coverage === null
                        ? '—'
                        : `${(row.citation_coverage * 100).toFixed(1)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">
            {breakdown.kind === 'loading'
              ? '正在加载分平台数据…'
              : breakdown.kind === 'forbidden'
                ? '权限不足，无法读取分平台数据。'
                : '该时间窗内尚无分平台数据。'}
          </p>
        )}

        <h3>项目竞品对比</h3>
        {competitors.kind === 'ready' && competitors.data.data.length > 0 ? (
          <div className="visibility-comparison">
            <div className="visibility-bars" role="img" aria-label="目标品牌与竞品提及率对比">
              {competitorChartRows.map((row) => (
                <div
                  className={row.target ? 'visibility-bar target' : 'visibility-bar'}
                  key={row.name}
                >
                  <span className="visibility-bar-label">
                    {row.name}
                    {row.target ? <em>目标品牌</em> : null}
                  </span>
                  <span className="visibility-bar-track">
                    <i
                      style={{
                        width: `${competitorChartMax > 0 ? (row.rate / competitorChartMax) * 100 : 0}%`,
                      }}
                    />
                  </span>
                  <strong>{`${(row.rate * 100).toFixed(1)}%`}</strong>
                </div>
              ))}
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>竞品</th>
                    <th>提及率</th>
                    <th>提及次数</th>
                    <th>答案数</th>
                    <th>平均排名</th>
                    <th>Top1 / Top3 / Top10</th>
                  </tr>
                </thead>
                <tbody>
                  {competitors.data.data.map((row) => (
                    <tr key={row.competitor}>
                      <td>{row.competitor}</td>
                      <td>{`${(row.mention_rate * 100).toFixed(1)}%`}</td>
                      <td>{row.mention_count}</td>
                      <td>{row.answer_count}</td>
                      <td>{row.average_rank === null ? '—' : row.average_rank.toFixed(2)}</td>
                      <td>
                        {row.top1_rate === null || row.top3_rate === null || row.top10_rate === null
                          ? '—'
                          : `${(row.top1_rate * 100).toFixed(1)}% / ${(row.top3_rate * 100).toFixed(1)}% / ${(row.top10_rate * 100).toFixed(1)}%`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <p className="empty">
            {competitors.kind === 'loading'
              ? '正在加载竞品数据…'
              : competitors.kind === 'forbidden'
                ? '权限不足，无法读取竞品数据。'
                : '该时间窗内尚无竞品数据。'}
          </p>
        )}

        {targetBrandRow ? (
          <>
            <h3>{`${targetBrandRow.brand}品牌表现`}</h3>
            <div className="metric-cards brand-performance-cards">
              <article>
                <span>品牌榜排名</span>
                <strong>{`#${targetBrandRow.rank}`}</strong>
                <span>{`共 ${brandRows.length} 个已审核品牌`}</span>
              </article>
              <article>
                <span>综合得分</span>
                <strong>{targetBrandRow.score}</strong>
                <span>当前正式榜单口径</span>
              </article>
              <article>
                <span>品牌出现次数</span>
                <strong>{targetBrandRow.occurrences}</strong>
                <span>来自真实采集答案</span>
              </article>
              <article>
                <span>入榜平均排名</span>
                <strong>{targetBrandRow.avg_rank}</strong>
                <span>数值越小越靠前</span>
              </article>
              <article>
                <span>品牌出现率</span>
                <strong>
                  {typeof targetBrandRow.appearance_rate === 'number'
                    ? `${targetBrandRow.appearance_rate.toFixed(1)}%`
                    : '—'}
                </strong>
                <span>近 30 天榜单口径</span>
              </article>
            </div>
          </>
        ) : null}

        {topBrandRows.length > 0 ? (
          <>
            <h3>品牌可见度 Top 10</h3>
            <div
              className="visibility-bars brand-score-bars"
              role="img"
              aria-label="品牌可见度 Top 10 横条图"
            >
              {topBrandRows.map((row) => {
                const isTarget = row.brand === targetBrandRow?.brand;
                return (
                  <div
                    className={isTarget ? 'visibility-bar target' : 'visibility-bar'}
                    key={row.brand}
                  >
                    <span className="visibility-bar-label">{`${row.rank}. ${row.brand}`}</span>
                    <span className="visibility-bar-track">
                      <i
                        style={{
                          width: `${topBrandScoreMax > 0 ? (row.score / topBrandScoreMax) * 100 : 0}%`,
                        }}
                        title={`${row.brand} · 出现 ${row.occurrences} 次 · 平均排名 ${row.avg_rank}`}
                      />
                    </span>
                    <strong>{row.score}</strong>
                  </div>
                );
              })}
            </div>
          </>
        ) : null}

        <h3>
          {`完整品牌可见度榜单（近 30 天 · ${
            rulepackDomain ? `规则包：${rulepackDomain}` : '规则包信息加载中…'
          }）`}
        </h3>
        {entityResolution?.mode === 'governed_hybrid_v2' ? (
          <p className="service-note">
            {`榜单按已审核品牌家族实体归并；本窗同一答案内消除 ${collapsedAliases} 次重复别名。`}
            {pendingEntityNames > 0
              ? `另有 ${pendingEntityNames} 个名称待语义复核，复核前不进入正式榜。`
              : '本窗没有待复核名称。'}
          </p>
        ) : null}
        {brands.kind === 'ready' ? (
          brandRows.length > 0 ? (
            <>
              <div className="brand-visibility-page-size">
                <label>
                  每页显示
                  <select
                    aria-label="品牌可见度榜单每页显示数量"
                    value={brandPageSize}
                    onChange={(event) => {
                      const nextPageSize = Number(event.currentTarget.value);
                      if (
                        !BRAND_VISIBILITY_PAGE_SIZE_OPTIONS.some(
                          (option) => option === nextPageSize,
                        )
                      )
                        return;
                      brandPage.setPage(1);
                      setBrandPageSize(nextPageSize);
                    }}
                  >
                    {BRAND_VISIBILITY_PAGE_SIZE_OPTIONS.map((pageSize) => (
                      <option key={pageSize} value={pageSize}>
                        {pageSize} 条
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="table-scroll">
                <table aria-label="品牌可见度榜单">
                  <thead>
                    <tr>
                      <th>排名</th>
                      <th>品牌</th>
                      <th>竞品属性</th>
                      <th>综合得分</th>
                      <th>出现次数</th>
                      <th>平均排名</th>
                      <th>出现率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {brandPage.visibleItems.map((row) => (
                      <tr key={row.brand}>
                        <td>{row.rank}</td>
                        <td>{row.brand}</td>
                        <td title={row.eligibility_note ?? undefined}>
                          {industryFitLabel(row.industry_fit)}
                        </td>
                        <td>{row.score}</td>
                        <td>{row.occurrences}</td>
                        <td>{row.avg_rank}</td>
                        <td>
                          {typeof row.appearance_rate === 'number'
                            ? `${row.appearance_rate.toFixed(1)}%`
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={brandPage.page}
                pageCount={brandPage.pageCount}
                totalItems={brandRows.length}
                windowSize={BRAND_VISIBILITY_PAGE_NUMBER_WINDOW_SIZE}
                onPageChange={brandPage.setPage}
                label="品牌可见度榜单分页"
              />
            </>
          ) : (
            <p className="empty">该时间窗内可用于榜单的真实答案不足。</p>
          )
        ) : brands.kind === 'brandrank_domain_unresolved' ? (
          <p className="service-note">
            项目未设置品牌规则包域，请先在项目设置中配置
            brandrank_domain。品牌榜单暂不可用，基础指标不受影响。
          </p>
        ) : brands.kind === 'unmapped_industry' ? (
          <p className="service-note">
            对应行业规则包尚未配置，品牌榜单暂不可用，基础指标不受影响。
          </p>
        ) : brands.kind === 'llm_disabled' ? (
          <p className="service-note">LLM 未配置，品牌榜单暂不可用，基础指标不受影响。</p>
        ) : brands.kind === 'loading' ? (
          <p className="empty">正在计算完整品牌榜单…</p>
        ) : (
          <p className="empty">完整品牌榜单暂不可用。</p>
        )}
      </section>

      <section className="execution-card">
        <div className="section-title">
          <h2>official V2 历史回算状态</h2>
          <div className="official-backfill-heading-actions">
            <span>迁移诊断区 · 不代替上方完整观测结果</span>
            <SemanticBackfillLauncher session={session} project={project} />
          </div>
        </div>
        {official.kind === 'ready' ? (
          <>
            <p className="service-note" role="status">
              {`当前 official 快照只完成 ${officialMetricNames.size}/${v21Definitions.length || 34} 项指标，并且仅纳入 ${officialKnownAnswers} 份答案；其余指标和历史答案一律标记为“待回算”，不会再按 0 展示。上方 ${observedAnswerCount || '全部'} 份答案的观测结果仍是当前汇报口径。`}
            </p>
            <div className="metric-cards official-migration-cards">
              <article>
                <span>V2.1 指标定义</span>
                <strong>{v21Definitions.length || 34}</strong>
                <span>完整指标目录</span>
              </article>
              <article>
                <span>已有 official 结果</span>
                <strong>{officialMetricNames.size}</strong>
                <span>{`${Math.max(0, (v21Definitions.length || 34) - officialMetricNames.size)} 项待回算`}</span>
              </article>
              <article>
                <span>当前快照纳入答案</span>
                <strong>{officialKnownAnswers}</strong>
                <span>仅为发布链路探针</span>
              </article>
              <article>
                <span>历史回算</span>
                <strong className="metric-pending">未完成</strong>
                <span>未计算不等于 0</span>
              </article>
            </div>
          </>
        ) : (
          <p className="empty">
            {official.kind === 'loading' ? '正在读取 official 快照…' : 'official 快照暂不可用。'}
          </p>
        )}

        {catalog.kind === 'ready' && official.kind === 'ready' ? (
          <details className="official-diagnostics">
            <summary>{`技术诊断明细：逐实体探针与 ${v21Definitions.length} 项指标定义`}</summary>
            <h3>当前逐实体发布探针</h3>
            <p className="service-note">
              下面数值只来自 1 份探针答案，不参与品牌排名，也不能外推到完整历史窗口。
            </p>
            <div className="table-scroll">
              <table aria-label="official V2 当前指标明细">
                <thead>
                  <tr>
                    <th>品牌</th>
                    <th>指标</th>
                    <th>探针结果</th>
                    <th>分子 / 分母</th>
                    <th>纳入答案</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {official.data.metrics.map((metric) => (
                    <tr key={metric.snapshot_pub_id}>
                      <td>
                        {entityName(entityById.get(metric.focal_entity_id), metric.focal_entity_id)}
                      </td>
                      <td>{metricLabel(metric.metric_name)}</td>
                      <td>{formatOfficialMetric(metric)}</td>
                      <td>
                        {metric.raw_numerator} / {metric.raw_denominator}
                      </td>
                      <td>{metric.known_answer_count}</td>
                      <td>{metric.state === 'ready' ? '探针已发布' : metric.state}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h3>{`V2.1 指标目录（${v21Definitions.length} 项）`}</h3>
            <div className="table-scroll">
              <table aria-label="V2.1 指标目录">
                <thead>
                  <tr>
                    <th>指标</th>
                    <th>定义状态</th>
                    <th>当前 official</th>
                    <th>依赖语义能力</th>
                  </tr>
                </thead>
                <tbody>
                  {v21Definitions.map((definition) => (
                    <tr key={definition.metric_name}>
                      <td>{metricLabel(definition.metric_name)}</td>
                      <td>{definition.status === 'published' ? '已发布' : '实验中'}</td>
                      <td>
                        {officialMetricNames.has(definition.metric_name) ? '已有探针' : '待回算'}
                      </td>
                      <td>{definition.required_semantic_capabilities.join('、') || '无'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        ) : null}
      </section>
    </>
  );
}
