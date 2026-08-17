import { Badge } from '@geo/design-system';
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import './customer-answer-explorer.css';

export type CustomerAnswerSentiment = 'positive' | 'neutral' | 'negative' | 'unknown';

export type CustomerAnswerExplorerRow = {
  answer_pub_id: string;
  query_pub_id: string | null;
  query_text: string | null;
  response_text: string;
  model: string;
  region: string;
  mode: string;
  capture_time: string;
  mentioned: boolean;
  rank: number | null;
  sentiment: CustomerAnswerSentiment | null;
  recommended: boolean | null;
  citation_count: number;
};

export type CustomerAnswerExplorerPage = {
  schema_version: 'customer-answer-page-v1';
  project_pub_id: string;
  data: readonly CustomerAnswerExplorerRow[];
  page: {
    total: number;
    offset: number;
    limit: number;
    has_more: boolean;
  };
};

export type CustomerAnswerMentionFilter = 'all' | 'true' | 'false';
export type CustomerAnswerSentimentFilter = 'all' | CustomerAnswerSentiment;
export type CustomerAnswerPageSize = 10 | 20 | 50;

export type CustomerAnswerExplorerQuery = {
  search: string;
  mentioned: CustomerAnswerMentionFilter;
  sentiment: CustomerAnswerSentimentFilter;
  offset: number;
  limit: CustomerAnswerPageSize;
};

export type CustomerAnswerExplorerProps = {
  brandName: string;
  loadPage: (query: CustomerAnswerExplorerQuery) => Promise<CustomerAnswerExplorerPage>;
  fixturePage?: CustomerAnswerExplorerPage;
};

type LoadState = 'loading' | 'ready' | 'failed';

const pageSizes: readonly CustomerAnswerPageSize[] = [10, 20, 50];

const sentimentPresentation: Record<
  CustomerAnswerSentiment,
  { label: string; tone: 'positive' | 'neutral' | 'danger' | 'warning' }
> = {
  positive: { label: '正面', tone: 'positive' },
  neutral: { label: '中性', tone: 'neutral' },
  negative: { label: '负面', tone: 'danger' },
  unknown: { label: '情感未知', tone: 'warning' },
};

const firstParagraph = (response: string): string => {
  const paragraph = response
    .split(/(?:\r?\n){2,}|\r?\n/u)
    .map((item) => item.trim())
    .find(Boolean);
  return paragraph ?? '该回答没有可显示的正文。';
};

const formatCaptureTime = (value: string): string => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
};

function SummaryTile({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone: 'blue' | 'violet' | 'orange' | 'cyan';
}) {
  return (
    <div className="geo-answer-explorer__summary-tile" data-tone={tone}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function AnswerFact({ children }: { children: ReactNode }) {
  return <span className="geo-answer-explorer__fact">{children}</span>;
}

function AnswerCard({ brandName, row }: { brandName: string; row: CustomerAnswerExplorerRow }) {
  const sentiment = sentimentPresentation[row.sentiment ?? 'unknown'];
  const queryLabel = row.query_text?.trim() || '未关联原始问题';
  const responseLead = firstParagraph(row.response_text);

  return (
    <article className="geo-answer-card">
      <header className="geo-answer-card__header">
        <div className="geo-answer-card__question">
          <span>用户问题</span>
          <h3>{queryLabel}</h3>
        </div>
        <div className="geo-answer-card__context" aria-label="回答上下文">
          <strong>{row.model}</strong>
          <span>{row.region}</span>
          <span>{row.mode}</span>
          <time dateTime={row.capture_time}>{formatCaptureTime(row.capture_time)}</time>
        </div>
      </header>

      <div className="geo-answer-card__facts" aria-label="回答指标">
        <Badge tone={row.mentioned ? 'positive' : 'neutral'}>
          {row.mentioned ? `已提及${brandName}` : `未提及${brandName}`}
        </Badge>
        <Badge tone={row.rank === null ? 'neutral' : row.rank <= 3 ? 'info' : 'warning'}>
          {row.rank === null ? '排名 —' : `排名 #${row.rank}`}
        </Badge>
        <Badge tone={sentiment.tone}>{sentiment.label}</Badge>
        <Badge
          tone={
            row.recommended === true
              ? 'positive'
              : row.recommended === false
                ? 'warning'
                : 'neutral'
          }
        >
          {row.recommended === true
            ? '明确推荐'
            : row.recommended === false
              ? '未形成推荐'
              : '推荐待判定'}
        </Badge>
        <Badge tone={row.citation_count > 0 ? 'info' : 'neutral'}>
          {row.citation_count.toLocaleString('zh-CN')} 条引用
        </Badge>
      </div>

      <div className="geo-answer-card__response">
        <span>AI 回答首段</span>
        <p>{responseLead}</p>
      </div>

      <details className="geo-answer-card__details">
        <summary>展开完整回答</summary>
        <div>{row.response_text || '该回答没有可显示的正文。'}</div>
      </details>

      <footer>
        <AnswerFact>回答记录 {row.answer_pub_id}</AnswerFact>
        {row.query_pub_id ? <AnswerFact>问题记录 {row.query_pub_id}</AnswerFact> : null}
      </footer>
    </article>
  );
}

function LoadingPanel() {
  return (
    <div className="geo-answer-explorer__loading" role="status" aria-live="polite">
      <div>
        <strong>正在读取真实回答</strong>
        <span>正在按当前搜索和筛选条件整理结果。</span>
      </div>
      <div className="geo-answer-explorer__skeleton" aria-hidden="true">
        {Array.from({ length: 4 }, (_, index) => (
          <span key={index} />
        ))}
      </div>
    </div>
  );
}

export function CustomerAnswerExplorer({
  brandName,
  loadPage,
  fixturePage,
}: CustomerAnswerExplorerProps) {
  const [searchDraft, setSearchDraft] = useState('');
  const [search, setSearch] = useState('');
  const [mentioned, setMentioned] = useState<CustomerAnswerMentionFilter>('all');
  const [sentiment, setSentiment] = useState<CustomerAnswerSentimentFilter>('all');
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState<CustomerAnswerPageSize>(20);
  const [state, setState] = useState<LoadState>(fixturePage ? 'ready' : 'loading');
  const [result, setResult] = useState<CustomerAnswerExplorerPage | null>(fixturePage ?? null);
  const [retryKey, setRetryKey] = useState(0);
  const requestSequence = useRef(0);

  const query = useMemo<CustomerAnswerExplorerQuery>(
    () => ({ search, mentioned, sentiment, offset, limit }),
    [limit, mentioned, offset, search, sentiment],
  );

  useEffect(() => {
    const requestId = ++requestSequence.current;
    let cancelled = false;
    setState('loading');
    void loadPage(query).then(
      (page) => {
        if (cancelled || requestId !== requestSequence.current) return;
        setResult(page);
        setState('ready');
      },
      () => {
        if (cancelled || requestId !== requestSequence.current) return;
        if (fixturePage) {
          setResult(fixturePage);
          setState('ready');
          return;
        }
        setState('failed');
      },
    );
    return () => {
      cancelled = true;
    };
  }, [fixturePage, loadPage, query, retryKey]);

  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setOffset(0);
    setSearch(searchDraft.trim());
  };

  const clearSearch = () => {
    setSearchDraft('');
    setSearch('');
    setOffset(0);
  };

  const changeMentioned = (value: CustomerAnswerMentionFilter) => {
    setMentioned(value);
    setOffset(0);
  };

  const changeSentiment = (value: CustomerAnswerSentimentFilter) => {
    setSentiment(value);
    setOffset(0);
  };

  const changeLimit = (value: CustomerAnswerPageSize) => {
    setLimit(value);
    setOffset(0);
  };

  const page = result?.page;
  const rows = result?.data ?? [];
  const total = page?.total ?? 0;
  const firstItem = rows.length > 0 && page ? page.offset + 1 : 0;
  const lastItem = rows.length > 0 && page ? page.offset + rows.length : 0;
  const currentPage = page ? Math.floor(page.offset / page.limit) + 1 : 1;
  const totalPages = page ? Math.max(1, Math.ceil(page.total / page.limit)) : 1;
  const currentMentionCount = rows.filter((row) => row.mentioned).length;
  const currentCitationCount = rows.filter((row) => row.citation_count > 0).length;
  const hasFilters = search.length > 0 || mentioned !== 'all' || sentiment !== 'all';

  return (
    <section className="geo-answer-explorer" aria-labelledby="geo-answer-explorer-title">
      <header className="geo-answer-explorer__hero">
        <div>
          <span>Answer Intelligence</span>
          <h2 id="geo-answer-explorer-title">{brandName} · 真实 AI 回答</h2>
          <p>直接查看用户问题、模型原文、品牌提及、排名、推荐语境与引用证据。</p>
        </div>
        <div className="geo-answer-explorer__hero-total">
          <span>匹配回答</span>
          <strong>{total.toLocaleString('zh-CN')}</strong>
          <small>当前筛选结果</small>
        </div>
      </header>

      <form className="geo-answer-explorer__toolbar" onSubmit={submitSearch} role="search">
        <label className="geo-answer-explorer__search">
          <span>搜索问题或回答原文</span>
          <div>
            <input
              type="search"
              value={searchDraft}
              maxLength={200}
              autoComplete="off"
              placeholder={`搜索与${brandName}相关的问题、回答关键词`}
              onChange={(event) => setSearchDraft(event.currentTarget.value)}
            />
            {searchDraft || search ? (
              <button type="button" className="geo-answer-explorer__clear" onClick={clearSearch}>
                清除
              </button>
            ) : null}
            <button type="submit" className="geo-answer-explorer__submit">
              搜索回答
            </button>
          </div>
        </label>

        <label>
          <span>品牌提及</span>
          <select
            value={mentioned}
            onChange={(event) =>
              changeMentioned(event.currentTarget.value as CustomerAnswerMentionFilter)
            }
          >
            <option value="all">全部回答</option>
            <option value="true">已提及品牌</option>
            <option value="false">未提及品牌</option>
          </select>
        </label>

        <label>
          <span>回答情感</span>
          <select
            value={sentiment}
            onChange={(event) =>
              changeSentiment(event.currentTarget.value as CustomerAnswerSentimentFilter)
            }
          >
            <option value="all">全部情感</option>
            <option value="positive">正面</option>
            <option value="neutral">中性</option>
            <option value="negative">负面</option>
            <option value="unknown">未知</option>
          </select>
        </label>
      </form>

      <div className="geo-answer-explorer__summary" aria-label="回答结果摘要">
        <SummaryTile
          tone="blue"
          label="匹配回答总数"
          value={total.toLocaleString('zh-CN')}
          detail={hasFilters ? '已应用搜索或筛选' : '当前观察窗口'}
        />
        <SummaryTile
          tone="violet"
          label="当前页"
          value={`${currentPage} / ${totalPages}`}
          detail={`${firstItem.toLocaleString('zh-CN')}–${lastItem.toLocaleString('zh-CN')} 条`}
        />
        <SummaryTile
          tone="orange"
          label="当前页提及"
          value={currentMentionCount.toLocaleString('zh-CN')}
          detail={`本页 ${rows.length.toLocaleString('zh-CN')} 条回答`}
        />
        <SummaryTile
          tone="cyan"
          label="当前页有引用"
          value={currentCitationCount.toLocaleString('zh-CN')}
          detail="至少包含一条引用"
        />
      </div>

      {state === 'loading' ? <LoadingPanel /> : null}

      {state === 'failed' ? (
        <div className="geo-answer-explorer__state geo-answer-explorer__state--failed" role="alert">
          <span aria-hidden="true">!</span>
          <div>
            <strong>回答加载失败</strong>
            <p>当前回答列表暂时无法读取，可以保留筛选条件后重新请求。</p>
          </div>
          <button type="button" onClick={() => setRetryKey((value) => value + 1)}>
            重新加载
          </button>
        </div>
      ) : null}

      {state === 'ready' && rows.length === 0 ? (
        <div className="geo-answer-explorer__state" role="status">
          <span aria-hidden="true">0</span>
          <div>
            <strong>{hasFilters ? '没有匹配的回答' : '当前窗口暂无回答'}</strong>
            <p>
              {hasFilters
                ? '更换关键词或清除筛选条件，查看其他真实回答。'
                : '当前观察窗口尚无可展示的 AI 回答。'}
            </p>
          </div>
          {hasFilters ? (
            <button
              type="button"
              onClick={() => {
                setSearchDraft('');
                setSearch('');
                setMentioned('all');
                setSentiment('all');
                setOffset(0);
              }}
            >
              清除全部筛选
            </button>
          ) : null}
        </div>
      ) : null}

      {state === 'ready' && rows.length > 0 ? (
        <div className="geo-answer-explorer__cards">
          {rows.map((row) => (
            <AnswerCard key={row.answer_pub_id} brandName={brandName} row={row} />
          ))}
        </div>
      ) : null}

      <footer className="geo-answer-explorer__pagination">
        <div aria-live="polite">
          <strong>
            {firstItem.toLocaleString('zh-CN')}–{lastItem.toLocaleString('zh-CN')}
          </strong>
          <span> / 共 {total.toLocaleString('zh-CN')} 条</span>
        </div>
        <label>
          每页
          <select
            value={limit}
            onChange={(event) =>
              changeLimit(Number(event.currentTarget.value) as CustomerAnswerPageSize)
            }
          >
            {pageSizes.map((size) => (
              <option value={size} key={size}>
                {size} 条
              </option>
            ))}
          </select>
        </label>
        <nav aria-label="回答分页">
          <button
            type="button"
            disabled={state !== 'ready' || !page || page.offset === 0}
            onClick={() => setOffset(Math.max(0, (page?.offset ?? 0) - (page?.limit ?? limit)))}
          >
            上一页
          </button>
          <span>
            第 {currentPage.toLocaleString('zh-CN')} / {totalPages.toLocaleString('zh-CN')} 页
          </span>
          <button
            type="button"
            disabled={state !== 'ready' || !page?.has_more}
            onClick={() => setOffset((page?.offset ?? 0) + (page?.limit ?? limit))}
          >
            下一页
          </button>
        </nav>
      </footer>
    </section>
  );
}
