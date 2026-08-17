import type { CustomerDashboardProjection, CustomerMetricProjection } from '@geo/api-client';
import { useMemo, useState, type ReactNode } from 'react';
import './customer-data-explorer.css';

type PageSize = 10 | 20 | 50;

type ExplorerFrameProps = {
  title: string;
  eyebrow: string;
  description: string;
  searchLabel: string;
  searchPlaceholder: string;
  query: string;
  onQueryChange: (value: string) => void;
  totalCount: number;
  filteredCount: number;
  page: number;
  pageSize: PageSize;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: PageSize) => void;
  emptyTitle: string;
  emptyDetail: string;
  children: ReactNode;
};

const pageSizes: readonly PageSize[] = [10, 20, 50];

const normalizedSearch = (value: string): string => value.trim().toLocaleLowerCase('zh-CN');

const metricByCode = (
  metrics: readonly CustomerMetricProjection[],
  code: string,
): CustomerMetricProjection | undefined => metrics.find((metric) => metric.code === code);

const formatMetric = (metrics: readonly CustomerMetricProjection[], code: string): string => {
  const metric = metricByCode(metrics, code);
  if (!metric || metric.state !== 'ready' || metric.value === null) return '—';
  if (metric.format === 'percentage') return `${(metric.value * 100).toFixed(1)}%`;
  if (metric.format === 'rank') {
    return `#${metric.value.toFixed(Number.isInteger(metric.value) ? 0 : 1)}`;
  }
  if (metric.format === 'count') return Math.round(metric.value).toLocaleString('zh-CN');
  return metric.value.toFixed(Number.isInteger(metric.value) ? 0 : 2);
};

const pageNumbers = (page: number, totalPages: number): Array<number | 'ellipsis'> => {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
  const candidates = new Set([1, totalPages, page - 1, page, page + 1]);
  const values = [...candidates]
    .filter((value) => value > 0 && value <= totalPages)
    .sort((left, right) => left - right);
  const result: Array<number | 'ellipsis'> = [];
  values.forEach((value, index) => {
    const previous = values[index - 1];
    if (previous !== undefined && value - previous > 1) result.push('ellipsis');
    result.push(value);
  });
  return result;
};

function ExplorerFrame({
  title,
  eyebrow,
  description,
  searchLabel,
  searchPlaceholder,
  query,
  onQueryChange,
  totalCount,
  filteredCount,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  emptyTitle,
  emptyDetail,
  children,
}: ExplorerFrameProps) {
  const headingId = `${eyebrow.toLocaleLowerCase('en-US').replaceAll(' ', '-')}-title`;
  const totalPages = Math.max(1, Math.ceil(filteredCount / pageSize));
  const safePage = Math.min(page, totalPages);
  const firstResult = filteredCount === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const lastResult = Math.min(safePage * pageSize, filteredCount);
  const hasSearch = query.trim().length > 0;

  return (
    <section className="geo-data-explorer" aria-labelledby={headingId}>
      <header className="geo-data-explorer__header">
        <div>
          <span>{eyebrow}</span>
          <h3 id={headingId}>{title}</h3>
          <p>{description}</p>
        </div>
        <strong>{totalCount.toLocaleString('zh-CN')} 条数据</strong>
      </header>

      <div className="geo-data-explorer__toolbar">
        <label className="geo-data-explorer__search">
          <span className="geo-visually-hidden">{searchLabel}</span>
          <input
            type="search"
            value={query}
            onChange={(event) => onQueryChange(event.currentTarget.value)}
            placeholder={searchPlaceholder}
            autoComplete="off"
          />
          {hasSearch ? (
            <button type="button" onClick={() => onQueryChange('')} aria-label="清除搜索">
              清除
            </button>
          ) : null}
        </label>
        <div className="geo-data-explorer__range" aria-live="polite">
          {hasSearch ? (
            <span>
              筛选出 <strong>{filteredCount.toLocaleString('zh-CN')}</strong> 条
            </span>
          ) : null}
          <span>
            当前 {firstResult.toLocaleString('zh-CN')}–{lastResult.toLocaleString('zh-CN')} 条，共{' '}
            {filteredCount.toLocaleString('zh-CN')} 条
          </span>
        </div>
      </div>

      {filteredCount > 0 ? (
        children
      ) : (
        <div className="geo-data-explorer__empty" role="status">
          <strong>{emptyTitle}</strong>
          <span>{emptyDetail}</span>
          {hasSearch ? (
            <button type="button" onClick={() => onQueryChange('')}>
              清除搜索条件
            </button>
          ) : null}
        </div>
      )}

      <footer className="geo-data-explorer__footer">
        <label>
          每页
          <select
            value={pageSize}
            onChange={(event) => onPageSizeChange(Number(event.currentTarget.value) as PageSize)}
          >
            {pageSizes.map((size) => (
              <option key={size} value={size}>
                {size} 条
              </option>
            ))}
          </select>
        </label>
        <nav className="geo-data-explorer__pagination" aria-label={`${title}分页`}>
          <button
            type="button"
            onClick={() => onPageChange(safePage - 1)}
            disabled={safePage === 1 || filteredCount === 0}
          >
            上一页
          </button>
          <div aria-label={`第 ${safePage} 页，共 ${totalPages} 页`}>
            {pageNumbers(safePage, totalPages).map((item, index) =>
              item === 'ellipsis' ? (
                <span key={`ellipsis-${index}`} aria-hidden="true">
                  …
                </span>
              ) : (
                <button
                  key={item}
                  type="button"
                  aria-current={item === safePage ? 'page' : undefined}
                  onClick={() => onPageChange(item)}
                  disabled={filteredCount === 0}
                >
                  {item}
                </button>
              ),
            )}
          </div>
          <button
            type="button"
            onClick={() => onPageChange(safePage + 1)}
            disabled={safePage === totalPages || filteredCount === 0}
          >
            下一页
          </button>
        </nav>
      </footer>
    </section>
  );
}

export function SourceDataExplorer({
  sources,
}: {
  sources: CustomerDashboardProjection['sources'];
}) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(20);
  const filteredSources = useMemo(() => {
    const search = normalizedSearch(query);
    return search
      ? sources.filter((source) => source.host.toLocaleLowerCase('zh-CN').includes(search))
      : sources;
  }, [query, sources]);
  const totalPages = Math.max(1, Math.ceil(filteredSources.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const rows = filteredSources.slice((safePage - 1) * pageSize, safePage * pageSize);

  const changeQuery = (value: string) => {
    setQuery(value);
    setPage(1);
  };
  const changePageSize = (value: PageSize) => {
    setPageSize(value);
    setPage(1);
  };

  return (
    <ExplorerFrame
      eyebrow="Source Explorer"
      title="全部信源"
      description="搜索并浏览当前筛选窗口内的真实引用网站；引用次数、覆盖回答和份额均来自事实快照。"
      searchLabel="搜索信源网站"
      searchPlaceholder="搜索网站域名，例如 gov.cn"
      query={query}
      onQueryChange={changeQuery}
      totalCount={sources.length}
      filteredCount={filteredSources.length}
      page={safePage}
      pageSize={pageSize}
      onPageChange={setPage}
      onPageSizeChange={changePageSize}
      emptyTitle={sources.length === 0 ? '当前暂无信源数据' : '没有匹配的信源'}
      emptyDetail={
        sources.length === 0
          ? '当前筛选窗口没有可展示的引用信源。'
          : '请更换网站关键词，或清除搜索条件后查看全部信源。'
      }
    >
      <div className="geo-data-explorer__table-wrap" tabIndex={0} aria-label="全部信源数据表">
        <table className="geo-data-explorer__table geo-data-explorer__table--sources">
          <thead>
            <tr>
              <th>信源网站</th>
              <th>引用次数</th>
              <th>覆盖回答</th>
              <th>引用份额</th>
              <th>信源类型</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((source) => (
              <tr key={source.host}>
                <th title={source.host}>{source.host}</th>
                <td>{source.references.toLocaleString('zh-CN')}</td>
                <td>{source.answers.toLocaleString('zh-CN')}</td>
                <td>{source.share === null ? '—' : `${(source.share * 100).toFixed(1)}%`}</td>
                <td>
                  <span
                    className="geo-data-explorer__tag"
                    data-tone={source.own_source ? 'owned' : 'third-party'}
                  >
                    {source.own_source ? '官网' : '第三方'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ExplorerFrame>
  );
}

export function QuestionDataExplorer({
  questions,
}: {
  questions: CustomerDashboardProjection['questions'];
}) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(20);
  const filteredQuestions = useMemo(() => {
    const search = normalizedSearch(query);
    if (!search) return questions;
    return questions.filter(
      (question) =>
        question.query_text.toLocaleLowerCase('zh-CN').includes(search) ||
        (question.query_group?.toLocaleLowerCase('zh-CN').includes(search) ?? false),
    );
  }, [query, questions]);
  const totalPages = Math.max(1, Math.ceil(filteredQuestions.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const rows = filteredQuestions.slice((safePage - 1) * pageSize, safePage * pageSize);

  const changeQuery = (value: string) => {
    setQuery(value);
    setPage(1);
  };
  const changePageSize = (value: PageSize) => {
    setPageSize(value);
    setPage(1);
  };

  return (
    <ExplorerFrame
      eyebrow="Question Explorer"
      title="全部问题"
      description="按用户问题或问题组即时检索，并对比提及、排名、Top3、引用和推荐表现。"
      searchLabel="搜索用户问题或问题组"
      searchPlaceholder="搜索问题文本或问题组"
      query={query}
      onQueryChange={changeQuery}
      totalCount={questions.length}
      filteredCount={filteredQuestions.length}
      page={safePage}
      pageSize={pageSize}
      onPageChange={setPage}
      onPageSizeChange={changePageSize}
      emptyTitle={questions.length === 0 ? '当前暂无问题数据' : '没有匹配的问题'}
      emptyDetail={
        questions.length === 0
          ? '当前筛选窗口没有可展示的问题表现。'
          : '请更换问题关键词或问题组，或清除搜索条件后查看全部问题。'
      }
    >
      <div className="geo-data-explorer__table-wrap" tabIndex={0} aria-label="全部问题数据表">
        <table className="geo-data-explorer__table geo-data-explorer__table--questions">
          <thead>
            <tr>
              <th>用户问题</th>
              <th>问题组</th>
              <th>提及率</th>
              <th>平均排名</th>
              <th>Top3</th>
              <th>引用覆盖</th>
              <th>推荐率</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((question) => (
              <tr key={question.query_pub_id}>
                <th title={question.query_text}>{question.query_text}</th>
                <td>{question.query_group ?? '未分组'}</td>
                <td>{formatMetric(question.metrics, 'mention_rate')}</td>
                <td>{formatMetric(question.metrics, 'average_rank')}</td>
                <td>{formatMetric(question.metrics, 'top3_rate')}</td>
                <td>{formatMetric(question.metrics, 'citation_coverage')}</td>
                <td>{formatMetric(question.metrics, 'recommendation_rate')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ExplorerFrame>
  );
}
