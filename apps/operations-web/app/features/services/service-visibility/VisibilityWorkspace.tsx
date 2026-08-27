import { useEffect, useState } from 'react';
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
  type Project,
  type SessionContext,
} from '../api';

const AI_RANKING_RUNS_PAGE_SIZE = 2;
const BRAND_VISIBILITY_DEFAULT_PAGE_SIZE = 10;
const BRAND_VISIBILITY_PAGE_SIZE_OPTIONS = [10, 20, 50] as const;

const METRIC_LABELS: Record<string, string> = {
  mention_rate: '品牌提及率',
  average_rank: '平均排名',
  top3_rate: 'Top 3 占比',
  citation_coverage: '引用覆盖',
};

function formatMetric(metric: string, value: number | null): string {
  if (value === null) return '—';
  return metric === 'average_rank' ? value.toFixed(2) : `${(value * 100).toFixed(1)}%`;
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
      .brandVisibility(session, {
        projectPubId: project.pub_id,
        windowDays: 30,
      })
      .then((result) => {
        if (!cancelled) setBrands(result);
      });
    return () => {
      cancelled = true;
    };
  }, [session, project.pub_id, window_.start, window_.end]);

  // 规则包域以项目真源 project.brandrank_domain 为准，响应 domain 佐证；都未到位时显示中性占位。
  const rulepackDomain =
    project.brandrank_domain ?? (brands.kind === 'ready' ? brands.data.domain : undefined);
  const brandRows = brands.kind === 'ready' ? (brands.data.result?.overall?.merged ?? []) : [];
  const brandPage = usePageWindow(
    brandRows,
    `${project.pub_id}:${window_.start}:${window_.end}`,
    brandPageSize,
  );

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
          <h2>评测结果</h2>
          <span>
            {window_.start} ~ {window_.end} · 冻结窗口内真实采集答案计算
          </span>
        </div>
        <WindowPicker start={window_.start} end={window_.end} onChange={setWindow} />
        <h3>核心指标</h3>
        {overview.kind === 'loading' ? (
          <p className="empty">正在计算指标…</p>
        ) : overview.kind === 'ready' ? (
          overview.data.data.length === 0 ? (
            <p className="empty">该时间窗内尚无评测指标——采集 run 完成后自动生成。</p>
          ) : (
            <div className="metric-cards">
              {overview.data.data.map((metric) => (
                <article key={metric.metric}>
                  <span>{METRIC_LABELS[metric.metric] ?? metric.metric}</span>
                  <strong>{formatMetric(metric.metric, metric.value)}</strong>
                  <span>
                    {metric.numerator}/{metric.denominator}
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

        <h3>竞品对比</h3>
        {competitors.kind === 'ready' && competitors.data.data.length > 0 ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>竞品</th>
                  <th>提及率</th>
                  <th>提及次数</th>
                  <th>答案数</th>
                </tr>
              </thead>
              <tbody>
                {competitors.data.data.map((row) => (
                  <tr key={row.competitor}>
                    <td>{row.competitor}</td>
                    <td>{`${(row.mention_rate * 100).toFixed(1)}%`}</td>
                    <td>{row.mention_count}</td>
                    <td>{row.answer_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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

        <h3>
          {`品牌可见度榜单（近 30 天 · ${
            rulepackDomain ? `规则包：${rulepackDomain}` : '规则包信息加载中…'
          }）`}
        </h3>
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
                      ) {
                        return;
                      }
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
          <p className="empty">正在计算品牌榜单…</p>
        ) : (
          <p className="empty">品牌榜单暂不可用。</p>
        )}
      </section>
    </>
  );
}
