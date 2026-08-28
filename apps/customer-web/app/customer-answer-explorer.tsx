import {
  Badge,
  Dialog,
  VerifiedBlobImage,
  type VerifiedBlobDownloadResult,
} from '@geo/design-system';
import type {
  CustomerAnswerLibraryDetailProjection,
  CustomerAnswerLibraryDimensionProjection,
  CustomerAnswerLibraryMetaDetailProjection,
  CustomerAnswerLibraryMetaProjection,
  CustomerAnswerLibraryPageProjection,
  CustomerAnswerLibraryQuestionProjection,
  CustomerAnswerLibraryRunProjection,
  CustomerAnswerLibraryRunsProjection,
} from '@geo/api-client';
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './customer-answer-explorer.css';
import './customer-answer-library.css';

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

export type CustomerAnswerCitationDetail = {
  id: string;
  ordinal: number;
  url: string;
  host: string;
  title: string | null;
  citedText: string | null;
  ownSource: boolean;
  contentHash: string | null;
  publishedAtRaw?: string | null;
  publishedAt: string | null;
  publishedAtTimezone?: string | null;
  publishedAtPrecision?: 'date' | 'minute' | 'second' | null;
  publishedAtSource: string | null;
  publishedAtConfidence?:
    | 'verified_structured'
    | 'structured_only'
    | 'visible_only'
    | 'inferred_low'
    | 'unknown';
  support?: {
    mappingStatus: 'mapped' | 'unmapped' | 'ambiguous';
    answerSentence: string | null;
    sourceQuote: string | null;
    sourceQuoteHash: string | null;
    sourceMatchStatus: 'exact' | 'normalized' | 'not_found' | 'not_checked';
    relation: 'supports' | 'contradicts' | 'background' | 'unverified';
    relevanceConfidence: number | null;
    reviewStatus: 'unreviewed' | 'approved' | 'rejected' | 'needs_review';
  };
};

export type CustomerAnswerEvidenceDetail = {
  id: string;
  relation: string;
  kind: string;
  mimeType: string;
  byteSize: number;
  sha256: string;
  sourceUrl: string | null;
  captureTime: string;
};

export type CustomerAnswerDetail = {
  citations: readonly CustomerAnswerCitationDetail[];
  evidence: readonly CustomerAnswerEvidenceDetail[];
  shareImage?: CustomerAnswerEvidenceDetail | null;
  shareArtifact?: {
    platform: string;
    status: 'available' | 'missing' | 'unsupported' | 'invalid';
    shareUrl: string | null;
    finalUrl: string | null;
    availabilityStatus: 'reachable' | 'redirected' | 'blocked' | 'unreachable' | 'unchecked';
    httpStatus: number | null;
    checkedAt: string | null;
    lastAccessibleAt: string | null;
    embedStatus: 'allowed' | 'blocked' | 'unknown';
    embedReason: string | null;
  } | null;
  projectionComplete: boolean;
};

export type CustomerAnswerEvidenceImageLoader = (
  evidence: CustomerAnswerEvidenceDetail,
) => Promise<VerifiedBlobDownloadResult>;

export type CustomerAnswerMentionFilter = 'all' | 'true' | 'false';
export type CustomerAnswerSentimentFilter = 'all' | CustomerAnswerSentiment;
export type CustomerAnswerPageSize = 10 | 20 | 50;
export type CustomerAnswerGroupBy = 'platform' | 'mode' | 'region';

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
  loadDetail?: (answerPubId: string) => Promise<CustomerAnswerDetail>;
  loadEvidenceImage?: CustomerAnswerEvidenceImageLoader;
  fixturePage?: CustomerAnswerExplorerPage;
};

export type CustomerAnswerLibraryPage = CustomerAnswerLibraryPageProjection;
export type CustomerAnswerLibraryMeta = CustomerAnswerLibraryMetaProjection;
export type CustomerAnswerLibraryMetaDetail = CustomerAnswerLibraryMetaDetailProjection;
export type CustomerAnswerLibraryQuestion = CustomerAnswerLibraryQuestionProjection;
export type CustomerAnswerLibraryRun = CustomerAnswerLibraryRunProjection;
export type CustomerAnswerLibraryRuns = CustomerAnswerLibraryRunsProjection;
export type CustomerAnswerLibraryAnswer = CustomerAnswerLibraryDetailProjection;

export type CustomerAnswerLibraryRootQuery = {
  search: string;
  offset: number;
  limit: 8 | 12 | 20;
  snapshotId?: string;
  snapshotAt?: string;
};

export type CustomerAnswerLibrarySnapshot = {
  snapshotId: string;
  snapshotAt: string;
  metricSnapshotSetPubId?: string | null;
  metricSnapshotSetHash?: string | null;
};

export type CustomerAnswerLibraryRunQuery = CustomerAnswerLibrarySnapshot & {
  model: string;
  region: string;
  mode: string;
  offset: number;
  limit: 10 | 20 | 50;
};

export type CustomerAnswerLibraryProps = {
  brandName: string;
  loadLibraryPage: (query: CustomerAnswerLibraryRootQuery) => Promise<CustomerAnswerLibraryPage>;
  loadMetaQuery: (
    metaQueryId: string,
    snapshot: CustomerAnswerLibrarySnapshot,
  ) => Promise<CustomerAnswerLibraryMetaDetail>;
  loadQuestionRuns: (
    questionId: string,
    query: CustomerAnswerLibraryRunQuery,
  ) => Promise<CustomerAnswerLibraryRuns>;
  loadAnswer: (
    answerPubId: string,
    snapshot: CustomerAnswerLibrarySnapshot,
  ) => Promise<CustomerAnswerLibraryAnswer>;
  loadDetail?: (
    answerPubId: string,
    snapshot: CustomerAnswerLibrarySnapshot,
  ) => Promise<CustomerAnswerDetail>;
  loadEvidenceImage?: CustomerAnswerEvidenceImageLoader;
  fixturePage?: CustomerAnswerLibraryPage;
};

export type CustomerAnswerLoadErrorKind = 'forbidden' | 'unavailable';

export class CustomerAnswerLoadError extends Error {
  readonly kind: CustomerAnswerLoadErrorKind;

  constructor(kind: CustomerAnswerLoadErrorKind) {
    super(`customer answer page ${kind}`);
    this.name = 'CustomerAnswerLoadError';
    this.kind = kind;
  }
}

type LoadState = 'loading' | 'ready' | 'failed' | 'forbidden';
type DetailState = 'idle' | 'loading' | 'ready' | 'failed';
const pageSizes: readonly CustomerAnswerPageSize[] = [10, 20, 50];

type PaginationItem = number | `gap-${number}`;

export const customerAnswerPaginationItems = (
  currentPage: number,
  totalPages: number,
): PaginationItem[] => {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }
  const pages = [
    ...new Set([1, 2, currentPage - 1, currentPage, currentPage + 1, totalPages - 1, totalPages]),
  ]
    .filter((page) => page >= 1 && page <= totalPages)
    .sort((left, right) => left - right);
  const items: PaginationItem[] = [];
  for (const page of pages) {
    const previous = items.at(-1);
    if (typeof previous === 'number' && page - previous > 1) items.push(`gap-${previous}`);
    items.push(page);
  }
  return items;
};

const sentimentPresentation: Record<
  CustomerAnswerSentiment,
  { label: string; tone: 'positive' | 'neutral' | 'danger' | 'warning' }
> = {
  positive: { label: '正面', tone: 'positive' },
  neutral: { label: '中性', tone: 'neutral' },
  negative: { label: '负面', tone: 'danger' },
  unknown: { label: '情感未知', tone: 'warning' },
};

const groupByPresentation: Record<
  CustomerAnswerGroupBy,
  { label: string; groupLabel: string; description: string }
> = {
  platform: {
    label: '按 AI 平台',
    groupLabel: 'AI 平台',
    description: '先区分豆包、DeepSeek、通义千问等回答平台',
  },
  mode: {
    label: '按回答模式',
    groupLabel: '回答模式',
    description: '区分快速回答、深度回答等采集模式',
  },
  region: {
    label: '按地域',
    groupLabel: '地域',
    description: '按华北、华东等实际采集地域归类',
  },
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

const formatPublishedTime = (
  value: string | null,
  precision: CustomerAnswerCitationDetail['publishedAtPrecision'],
): string | null => {
  if (!value || !precision) return null;
  if (precision === 'date') {
    const match = /^(\d{4})-(\d{2})-(\d{2})/u.exec(value);
    return match ? `${match[1]}年${match[2]}月${match[3]}日` : null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: precision === 'second' ? '2-digit' : undefined,
    hour12: false,
  }).format(date);
};

const publicationPrecisionLabel = (
  precision: CustomerAnswerCitationDetail['publishedAtPrecision'],
): string => {
  if (!precision) return '精度未知';
  return { date: '仅日期', minute: '精确到分钟', second: '精确到秒' }[precision];
};

const safeHttpUrl = (value: string | null | undefined): string | null => {
  if (!value || value.length > 2_000) return null;
  try {
    const parsed = new URL(value);
    return ['http:', 'https:'].includes(parsed.protocol) && !parsed.username && !parsed.password
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
};

const safeOfficialShareUrl = (value: string | null, model: string): string | null => {
  const safe = safeHttpUrl(value);
  if (!safe) return null;
  const parsed = new URL(safe);
  if (parsed.protocol !== 'https:') return null;
  const normalizedModel = model.trim().toLocaleLowerCase('zh-CN');
  if (normalizedModel === 'doubao' || normalizedModel === '豆包') {
    return ['doubao.com', 'www.doubao.com'].includes(parsed.hostname) &&
      parsed.pathname.startsWith('/thread/')
      ? parsed.toString()
      : null;
  }
  if (normalizedModel === 'deepseek') {
    return parsed.hostname === 'chat.deepseek.com' && parsed.pathname.startsWith('/share/')
      ? parsed.toString()
      : null;
  }
  if (['yiyan', '文心一言', '文心'].includes(normalizedModel)) {
    return ['mr.baidu.com', 'wenxin.baidu.com'].includes(parsed.hostname)
      ? parsed.toString()
      : null;
  }
  return null;
};

const platformMonogram = (model: string): string => {
  const normalized = model.trim();
  if (!normalized) return 'AI';
  if (/^[\x00-\x7F]+$/u.test(normalized)) return normalized.slice(0, 2).toLocaleUpperCase();
  return normalized.slice(0, 1);
};

function DetailMetric({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="geo-answer-dossier__metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}

function DetailEmpty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="geo-answer-dossier__empty" role="status">
      <span aria-hidden="true">◇</span>
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  );
}

const readableFallbackMarkdown = (
  source: string,
  citations: readonly CustomerAnswerCitationDetail[],
): string => {
  const markers = [...source.matchAll(/\[citation:(\d+)\]/giu)];
  const zeroBased = markers.some((marker) => marker[1] === '0');
  const ordinals = new Set(citations.map((citation) => citation.ordinal));
  return source.replace(/\[citation:(\d+)\]/giu, (_marker, captured: string) => {
    const rawOrdinal = Number.parseInt(captured, 10);
    const ordinal = zeroBased ? rawOrdinal + 1 : rawOrdinal;
    return ordinals.has(ordinal)
      ? `[${ordinal}](#citation-${ordinal})`
      : `〔引用 ${ordinal} 未映射〕`;
  });
};

const safeFallbackLink = (href: string | undefined): string | null => {
  if (!href) return null;
  if (/^#citation-\d+$/u.test(href)) return href;
  try {
    const parsed = new URL(href);
    return ['http:', 'https:'].includes(parsed.protocol) ? parsed.toString() : null;
  } catch {
    return null;
  }
};

function FallbackAnswer({
  row,
  citations,
  notice = {
    title: '退阶说明',
    detail: '未保存官方分享链接；以下为采集时保留的答案，不等同于官方实时页。',
  },
}: {
  row: CustomerAnswerExplorerRow;
  citations: readonly CustomerAnswerCitationDetail[];
  notice?: { title: string; detail: string };
}) {
  const markdown = readableFallbackMarkdown(row.response_text, citations);
  return (
    <div className="geo-answer-dossier__fallback" role="region" aria-label="历史采集答案退阶阅读版">
      <div className="geo-answer-dossier__fallback-notice" role="note">
        <strong>{notice.title}</strong>
        <span>{notice.detail}</span>
      </div>
      <article className="geo-answer-dossier__markdown geo-answer-dossier__fallback-markdown">
        <ReactMarkdown
          skipHtml
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => {
              const safeHref = safeFallbackLink(href);
              if (!safeHref) return <span>{children}</span>;
              const citationAnchor = safeHref.startsWith('#citation-');
              return (
                <a
                  href={safeHref}
                  target={citationAnchor ? undefined : '_blank'}
                  rel={citationAnchor ? undefined : 'noreferrer noopener'}
                >
                  {children}
                </a>
              );
            },
            img: () => null,
            table: ({ children }) => (
              <div
                className="geo-answer-dossier__table-scroll"
                role="region"
                aria-label="回答正文数据表"
                tabIndex={0}
              >
                <table>{children}</table>
              </div>
            ),
          }}
        >
          {markdown}
        </ReactMarkdown>
      </article>
      <footer>
        <span>
          {row.model} · 采集于 {formatCaptureTime(row.capture_time)}
        </span>
        <strong>已保留采集证据</strong>
      </footer>
    </div>
  );
}

type AnswerDisplayMode = 'text' | 'official' | 'image';

const answerDisplayModeLabel: Readonly<Record<AnswerDisplayMode, string>> = {
  text: '文本回答',
  official: '官方实时页',
  image: '分享图片',
};

const officialShareImageEvidence = (
  detail: CustomerAnswerDetail | null,
): CustomerAnswerEvidenceDetail | null =>
  detail?.shareImage ??
  detail?.evidence.find(
    (evidence) =>
      evidence.relation === 'official_share_image' &&
      evidence.kind === 'share_image' &&
      evidence.mimeType === 'image/png' &&
      evidence.byteSize > 0 &&
      evidence.byteSize <= 30 * 1024 * 1024 &&
      /^[a-f0-9]{64}$/iu.test(evidence.sha256),
  ) ??
  null;

function AnswerDisplay({
  row,
  detailState,
  detail,
  officialShareUrl,
  officialShareEmbeddable,
  loadEvidenceImage,
}: {
  row: CustomerAnswerExplorerRow;
  detailState: DetailState;
  detail: CustomerAnswerDetail | null;
  officialShareUrl: string | null;
  officialShareEmbeddable: boolean;
  loadEvidenceImage?: CustomerAnswerEvidenceImageLoader;
}) {
  const hasStoredAnswer = row.response_text.trim().length > 0;
  const shareImage = officialShareImageEvidence(detail);
  const availableModes: AnswerDisplayMode[] = [
    ...(hasStoredAnswer ? (['text'] as const) : []),
    ...(officialShareUrl && officialShareEmbeddable ? (['official'] as const) : []),
    ...(shareImage && loadEvidenceImage ? (['image'] as const) : []),
  ];
  const modeSignature = availableModes.join(':');
  const [selectedMode, setSelectedMode] = useState<AnswerDisplayMode | null>(
    availableModes[0] ?? null,
  );
  const [officialScale, setOfficialScale] = useState(1);
  const officialScrollRef = useRef<HTMLDivElement>(null);
  const activeMode =
    selectedMode && availableModes.includes(selectedMode)
      ? selectedMode
      : (availableModes[0] ?? null);
  const panelId = `geo-answer-display-${row.answer_pub_id}`;
  const estimatedOfficialPageHeight = Math.max(
    1_410,
    Math.min(28_000, 1_200 + Math.ceil(row.response_text.length * 1.65)),
  );

  useEffect(() => {
    setSelectedMode(availableModes[0] ?? null);
    // A newly selected answer always starts with its first genuinely available modality.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.answer_pub_id, modeSignature]);

  useEffect(() => {
    if (activeMode !== 'official') return;
    const viewport = officialScrollRef.current;
    if (!viewport) return;
    const fitOfficialPage = () => {
      setOfficialScale(Math.min(1, Math.max(0.36, (viewport.clientWidth - 2) / 900)));
    };
    fitOfficialPage();
    const resizeObserver =
      typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(fitOfficialPage);
    resizeObserver?.observe(viewport);
    window.addEventListener('resize', fitOfficialPage);
    return () => {
      resizeObserver?.disconnect();
      window.removeEventListener('resize', fitOfficialPage);
    };
  }, [activeMode]);

  return (
    <section className="geo-answer-display" aria-label="答案展示">
      <header>
        <div>
          <span>Answer view</span>
          <strong>{activeMode ? answerDisplayModeLabel[activeMode] : '回答内容'}</strong>
        </div>
        {availableModes.length > 1 ? (
          <nav className="geo-answer-display__tabs" aria-label="答案展示方式" role="tablist">
            {availableModes.map((mode) => (
              <button
                type="button"
                role="tab"
                aria-selected={activeMode === mode}
                aria-controls={panelId}
                key={mode}
                onClick={() => setSelectedMode(mode)}
              >
                {answerDisplayModeLabel[mode]}
              </button>
            ))}
          </nav>
        ) : null}
      </header>
      <div
        className="geo-answer-display__stage"
        id={panelId}
        role="tabpanel"
        data-mode={activeMode ?? undefined}
      >
        {activeMode === 'text' ? (
          <FallbackAnswer
            row={row}
            citations={detail?.citations ?? []}
            {...(officialShareUrl
              ? {
                  notice: {
                    title: '文本存档',
                    detail: '采集时保存的回答正文，可与官方实时页或分享图片交叉核对。',
                  },
                }
              : {})}
          />
        ) : null}
        {activeMode === 'official' && officialShareUrl ? (
          <div
            className="geo-answer-display__official-scroll"
            role="region"
            aria-label="官方回答只读预览"
            tabIndex={0}
            ref={officialScrollRef}
          >
            <div
              className="geo-answer-display__official-canvas"
              style={{
                width: 900 * officialScale,
                height: (estimatedOfficialPageHeight - 88) * officialScale,
              }}
            >
              <div
                className="geo-answer-display__official-surface"
                style={{
                  height: estimatedOfficialPageHeight - 88,
                  transform: `scale(${officialScale})`,
                }}
              >
                <iframe
                  src={officialShareUrl}
                  title={`${row.model} 官方回答只读预览`}
                  sandbox="allow-scripts allow-same-origin"
                  referrerPolicy="no-referrer"
                  tabIndex={-1}
                  aria-hidden="true"
                  style={{ height: estimatedOfficialPageHeight }}
                />
              </div>
            </div>
          </div>
        ) : null}
        {activeMode === 'image' && shareImage && loadEvidenceImage ? (
          <div
            className="geo-answer-display__image-scroll"
            role="region"
            aria-label="官方分享图片"
            tabIndex={0}
          >
            <VerifiedBlobImage
              className="geo-answer-display__image"
              resourceKey={`${shareImage.id}:${shareImage.sha256}`}
              alt={`${row.model} 官方分享图片`}
              load={() => loadEvidenceImage(shareImage)}
            />
          </div>
        ) : null}
        {!activeMode && detailState === 'loading' ? (
          <div className="geo-answer-dossier__official-loading" role="status">
            <span />
            <span />
            <strong>正在核对可展示的答案证据…</strong>
          </div>
        ) : null}
        {!activeMode && detailState !== 'loading' ? (
          <DetailEmpty title="本次采集没有可展示的回答">
            当前记录没有保留答案正文、可内嵌官方分享页或官方分享图片。
          </DetailEmpty>
        ) : null}
      </div>
    </section>
  );
}

function CitationRail({
  state,
  detail,
  answerCaptureTime,
  expectedCount,
}: {
  state: DetailState;
  detail: CustomerAnswerDetail | null;
  answerCaptureTime: string;
  expectedCount: number;
}) {
  const citations = detail?.citations ?? [];
  const domainCount = new Set(citations.map((citation) => citation.host)).size;
  const sourceCountLabel =
    state === 'ready' && detail?.projectionComplete === false && expectedCount > citations.length
      ? `${citations.length}/${expectedCount} 条可展示`
      : `${citations.length || expectedCount} 条`;
  return (
    <aside className="geo-answer-dossier__citations" aria-label="引用来源">
      <header>
        <div>
          <span>Source analysis</span>
          <h3>引用信源</h3>
        </div>
        <Badge tone={citations.length > 0 ? 'info' : 'neutral'}>{sourceCountLabel}</Badge>
      </header>
      {state === 'loading' ? (
        <div className="geo-answer-dossier__citation-loading" role="status">
          <span />
          <span />
          <span />
          正在核对引用关系与证据资产…
        </div>
      ) : null}
      {state === 'failed' ? (
        <DetailEmpty title="引用证据暂未载入">
          官方页面仍可打开，但当前不能把引用清单声称为完整记录。
        </DetailEmpty>
      ) : null}
      {state === 'ready' && citations.length > 0 ? (
        <>
          <div className="geo-answer-dossier__citation-summary">
            <span>{domainCount} 个独立域名</span>
            <span>{citations.length} 条规范化引用</span>
            <span>回答采集 {formatCaptureTime(answerCaptureTime)}</span>
          </div>
          <div
            className="geo-answer-dossier__citation-table-wrap"
            role="region"
            aria-label="引用信源分析表"
            tabIndex={0}
          >
            <table className="geo-answer-dossier__citation-table">
              <thead>
                <tr>
                  <th>序号</th>
                  <th>站点</th>
                  <th>站点类型</th>
                  <th>标题与引用依据</th>
                  <th>发布时间</th>
                </tr>
              </thead>
              <tbody>
                {citations.map((citation) => {
                  const publishedAt = formatPublishedTime(
                    citation.publishedAt,
                    citation.publishedAtPrecision,
                  );
                  return (
                    <tr key={citation.id} id={`citation-${citation.ordinal}`}>
                      <td>
                        <span className="geo-answer-dossier__citation-order">
                          {citation.ordinal}
                        </span>
                      </td>
                      <td>
                        <a
                          className="geo-answer-dossier__citation-host"
                          href={citation.url}
                          target="_blank"
                          rel="noreferrer noopener"
                        >
                          {citation.host}
                        </a>
                      </td>
                      <td>
                        <span
                          className="geo-answer-dossier__source-type"
                          data-tone={citation.ownSource ? 'owned' : 'independent'}
                        >
                          {citation.ownSource ? '品牌自有' : '第三方'}
                        </span>
                      </td>
                      <td>
                        <a
                          className="geo-answer-dossier__citation-article"
                          href={citation.url}
                          target="_blank"
                          rel="noreferrer noopener"
                        >
                          {citation.title ?? citation.host}
                        </a>
                        {citation.support?.sourceQuote ? (
                          <blockquote>来源原文：{citation.support.sourceQuote}</blockquote>
                        ) : citation.citedText ? (
                          <blockquote>引用片段：{citation.citedText}</blockquote>
                        ) : null}
                        {citation.support?.answerSentence ? (
                          <small>对应回答：{citation.support.answerSentence}</small>
                        ) : null}
                        {!citation.support?.sourceQuote &&
                        !citation.citedText &&
                        !citation.support?.answerSentence ? (
                          <small>引用依据待补齐</small>
                        ) : null}
                      </td>
                      <td>
                        {publishedAt ? (
                          <time dateTime={citation.publishedAt ?? undefined}>{publishedAt}</time>
                        ) : (
                          <span className="geo-answer-dossier__missing-time">待采集</span>
                        )}
                        {publishedAt ? (
                          <small>{publicationPrecisionLabel(citation.publishedAtPrecision)}</small>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      {state === 'ready' && citations.length === 0 ? (
        <DetailEmpty title="本回答没有可展示的引用">
          这表示当前记录未形成规范化引用，不等于回答内容已经过外部信源验证。
        </DetailEmpty>
      ) : null}
      <p className="geo-answer-dossier__method-note">
        引用表示 AI 回答关联了该页面；不自动等于该页面提及品牌，也不自动证明回答结论正确。
      </p>
    </aside>
  );
}

function AnswerRunRail({
  rows,
  selectedAnswerId,
  onSelect,
}: {
  rows: readonly CustomerAnswerExplorerRow[];
  selectedAnswerId: string;
  onSelect: (row: CustomerAnswerExplorerRow) => void;
}) {
  return (
    <aside className="geo-answer-dossier__run-rail" aria-label="同题回答运行">
      <header>
        <div>
          <span>Conversation runs</span>
          <h3>对话平台</h3>
        </div>
        <strong>{rows.length}</strong>
      </header>
      <div className="geo-answer-dossier__run-list">
        {rows.map((candidate, index) => {
          const selected = candidate.answer_pub_id === selectedAnswerId;
          return (
            <button
              key={candidate.answer_pub_id}
              type="button"
              aria-pressed={selected}
              aria-label={`${candidate.model}，${candidate.mode || '模式未标注'}，${formatCaptureTime(candidate.capture_time)}`}
              onClick={() => onSelect(candidate)}
            >
              <span className="geo-answer-dossier__run-platform" aria-hidden="true">
                {platformMonogram(candidate.model)}
              </span>
              <span className="geo-answer-dossier__run-copy">
                <strong>{candidate.model}</strong>
                <small>{candidate.mode || '模式未标注'}</small>
                <time dateTime={candidate.capture_time}>
                  {formatCaptureTime(candidate.capture_time)}
                </time>
              </span>
              <span className="geo-answer-dossier__run-index" aria-hidden="true">
                {index + 1}
              </span>
            </button>
          );
        })}
      </div>
      <footer>选择平台后，答案证据与引用信源表会同步切换。</footer>
    </aside>
  );
}

function AnswerDossier({
  brandName,
  row,
  runs,
  detailState,
  detail,
  loadEvidenceImage,
  onSelectRun,
  onClose,
}: {
  brandName: string;
  row: CustomerAnswerExplorerRow;
  runs: readonly CustomerAnswerExplorerRow[];
  detailState: DetailState;
  detail: CustomerAnswerDetail | null;
  loadEvidenceImage?: CustomerAnswerEvidenceImageLoader;
  onSelectRun: (row: CustomerAnswerExplorerRow) => void;
  onClose: () => void;
}) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const [mobilePane, setMobilePane] = useState<'official' | 'citations'>('official');
  const officialShareArtifact = detail?.shareArtifact ?? null;
  const officialShareUrl = safeOfficialShareUrl(officialShareArtifact?.shareUrl ?? null, row.model);
  const officialShareReachable =
    officialShareArtifact?.availabilityStatus === 'reachable' ||
    officialShareArtifact?.availabilityStatus === 'redirected';
  const officialShareEmbeddable =
    Boolean(officialShareUrl) &&
    officialShareReachable &&
    officialShareArtifact?.embedStatus === 'allowed';
  const shareImage = officialShareImageEvidence(detail);
  const hasStoredAnswer = row.response_text.trim().length > 0;
  const uniqueDomains = new Set(detail?.citations.map((citation) => citation.host) ?? []).size;
  const sameQuestionRuns = useMemo(() => {
    const matching = runs.filter((candidate) =>
      row.query_pub_id
        ? candidate.query_pub_id === row.query_pub_id
        : candidate.query_text?.trim() === row.query_text?.trim(),
    );
    return matching.some((candidate) => candidate.answer_pub_id === row.answer_pub_id)
      ? matching
      : [row, ...matching];
  }, [row, runs]);
  const selectedRunIndex = Math.max(
    0,
    sameQuestionRuns.findIndex((candidate) => candidate.answer_pub_id === row.answer_pub_id),
  );
  const copyShareLink = async () => {
    if (!officialShareUrl || !navigator.clipboard?.writeText) {
      setCopyState('failed');
      return;
    }
    try {
      await navigator.clipboard.writeText(officialShareUrl);
      setCopyState('copied');
      window.setTimeout(() => setCopyState('idle'), 1_500);
    } catch {
      setCopyState('failed');
    }
  };
  const selectRunAt = (index: number) => {
    const candidate = sameQuestionRuns[index];
    if (candidate) onSelectRun(candidate);
  };

  useEffect(() => {
    setCopyState('idle');
    setMobilePane('official');
  }, [row.answer_pub_id]);

  return (
    <Dialog
      title={row.query_text?.trim() || '未关联原始问题'}
      eyebrow={`${row.model} · ${
        officialShareUrl
          ? '官方回答与引用信源'
          : hasStoredAnswer
            ? '历史答案与引用信源'
            : '答案证据与引用信源'
      }`}
      size="wide"
      closeLabel="关闭官方回答详情"
      onClose={onClose}
    >
      <div className="geo-answer-dossier">
        <section className="geo-answer-dossier__identity" aria-label="回答身份与分析摘要">
          <div className="geo-answer-dossier__platform" aria-hidden="true">
            {platformMonogram(row.model)}
          </div>
          <div className="geo-answer-dossier__identity-main">
            <div className="geo-answer-dossier__identity-tags">
              <span>{row.model}</span>
              <span>{row.mode || '模式未标注'}</span>
              <span>{row.region || '地域未标注'}</span>
              <time dateTime={row.capture_time}>{formatCaptureTime(row.capture_time)}</time>
            </div>
            <AnswerResultBadges brandName={brandName} row={row} />
          </div>
          <div className="geo-answer-dossier__identity-actions">
            {officialShareUrl ? (
              <a
                href={officialShareUrl}
                target="_blank"
                rel="noreferrer noopener"
                title="官方页无法嵌入时，请在新窗口打开"
              >
                打开官方原页 ↗
              </a>
            ) : null}
            <button
              type="button"
              disabled={!officialShareUrl}
              title={officialShareUrl ? '复制官方分享链接' : '本次采集尚无官方分享链接'}
              onClick={() => void copyShareLink()}
            >
              {copyState === 'copied'
                ? '分享链接已复制'
                : copyState === 'failed'
                  ? '复制失败'
                  : '复制分享链接'}
            </button>
          </div>
        </section>

        <section className="geo-answer-dossier__metrics" aria-label="回答证据摘要">
          <DetailMetric
            label="品牌位置"
            value={row.rank === null ? '未进入排名' : `第 ${row.rank} 位`}
            note={row.mentioned ? `回答中已识别 ${brandName}` : '回答中未识别目标品牌'}
          />
          <DetailMetric
            label="独立信源"
            value={detailState === 'ready' ? `${uniqueDomains} 个域名` : '核对中'}
            note={`${detail?.citations.length ?? row.citation_count} 条规范化引用`}
          />
        </section>

        <div className="geo-answer-dossier__reading-layout" data-mobile-pane={mobilePane}>
          <AnswerRunRail
            rows={sameQuestionRuns}
            selectedAnswerId={row.answer_pub_id}
            onSelect={onSelectRun}
          />
          <nav className="geo-answer-dossier__mobile-pane-switch" aria-label="回答详情内容切换">
            <button
              type="button"
              aria-pressed={mobilePane === 'official'}
              onClick={() => setMobilePane('official')}
            >
              {officialShareUrl ? '官方回答' : '采集答案'}
            </button>
            <button
              type="button"
              aria-pressed={mobilePane === 'citations'}
              onClick={() => setMobilePane('citations')}
            >
              引用信源（{detail?.citations.length ?? row.citation_count}）
            </button>
          </nav>
          <section
            className="geo-answer-dossier__official"
            aria-label={
              officialShareUrl
                ? '官方实时回答页'
                : hasStoredAnswer
                  ? '已采集答案阅读版'
                  : '回答证据状态'
            }
          >
            <header>
              <div>
                <Badge tone={officialShareUrl ? 'positive' : 'warning'}>
                  {officialShareUrl
                    ? '官方域名 · 只读'
                    : hasStoredAnswer
                      ? '历史采集 · 退阶'
                      : '回答证据缺失'}
                </Badge>
                <strong>{officialShareUrl ? '官方实时回答页' : '已采集答案阅读版'}</strong>
              </div>
              <span>
                {officialShareUrl ? new URL(officialShareUrl).hostname : '无官方分享链接'}
              </span>
            </header>
            {detailState === 'loading' ? (
              <div className="geo-answer-dossier__official-loading" role="status">
                <span />
                <span />
                <strong>正在核对官方分享链接…</strong>
              </div>
            ) : shareImage && loadEvidenceImage ? (
              <div
                className="geo-answer-dossier__share-image-scroll"
                role="region"
                aria-label="官方分享图片"
                tabIndex={0}
              >
                <VerifiedBlobImage
                  className="geo-answer-dossier__share-image"
                  resourceKey={`${shareImage.id}:${shareImage.sha256}`}
                  alt={`${row.model} 官方分享图片`}
                  load={() => loadEvidenceImage(shareImage)}
                />
              </div>
            ) : officialShareUrl && officialShareEmbeddable ? (
              <>
                <div
                  className="geo-answer-dossier__official-viewport"
                  role="region"
                  aria-label="官方回答只读预览"
                >
                  <iframe
                    src={officialShareUrl}
                    title={`${row.model} 官方回答只读预览`}
                    sandbox="allow-scripts allow-same-origin"
                    referrerPolicy="no-referrer"
                    tabIndex={-1}
                  />
                </div>
              </>
            ) : officialShareUrl ? (
              <DetailEmpty title="平台不允许在工作台内嵌此回答">
                官方分享链接仍可通过上方“打开官方原页”访问；系统不会绕过 X-Frame-Options 或 CSP
                frame-ancestors 安全策略。
              </DetailEmpty>
            ) : hasStoredAnswer ? (
              <FallbackAnswer row={row} citations={detail?.citations ?? []} />
            ) : (
              <DetailEmpty title="本次采集没有可展示的回答">
                当前记录既没有官方分享链接，也没有保留可安全排版的答案正文，请由采集端补齐证据。
              </DetailEmpty>
            )}
          </section>
          <CitationRail
            state={detailState}
            detail={detail}
            answerCaptureTime={row.capture_time}
            expectedCount={row.citation_count}
          />
        </div>

        <nav className="geo-answer-dossier__run-pager" aria-label="同题回答分页">
          <button
            type="button"
            disabled={selectedRunIndex === 0}
            onClick={() => selectRunAt(selectedRunIndex - 1)}
          >
            ← 上一条
          </button>
          <span>
            第 {selectedRunIndex + 1} / {sameQuestionRuns.length} 条 · {row.model}
          </span>
          <button
            type="button"
            disabled={selectedRunIndex >= sameQuestionRuns.length - 1}
            onClick={() => selectRunAt(selectedRunIndex + 1)}
          >
            下一条 →
          </button>
        </nav>
      </div>
    </Dialog>
  );
}

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

function AnswerResultBadges({
  brandName,
  row,
}: {
  brandName: string;
  row: CustomerAnswerExplorerRow;
}) {
  const sentiment = sentimentPresentation[row.sentiment ?? 'unknown'];

  return (
    <div className="geo-answer-row__facts" aria-label="回答指标">
      <Badge tone={row.mentioned ? 'positive' : 'neutral'}>
        {row.mentioned ? `已提及${brandName}` : `未提及${brandName}`}
      </Badge>
      <Badge tone={row.rank === null ? 'neutral' : row.rank <= 3 ? 'info' : 'warning'}>
        {row.rank === null ? '排名 —' : `排名 #${row.rank}`}
      </Badge>
      <Badge tone={sentiment.tone}>{sentiment.label}</Badge>
      <Badge
        tone={
          row.recommended === true ? 'positive' : row.recommended === false ? 'warning' : 'neutral'
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
  );
}

const answerGroupValue = (
  row: CustomerAnswerExplorerRow,
  groupBy: CustomerAnswerGroupBy,
): string => {
  const rawValue = groupBy === 'platform' ? row.model : groupBy === 'mode' ? row.mode : row.region;
  return rawValue.trim() || '未标注';
};

const platformGroupPriority = (label: string): number => {
  const normalized = label.trim().toLocaleLowerCase('zh-CN');
  const groups = [
    ['doubao', '豆包'],
    ['deepseek'],
    ['文心一言', '文心', 'yiyan'],
    ['通义千问', '千问', 'qwen'],
    ['腾讯元宝', '元宝'],
    ['kimi'],
  ];
  const index = groups.findIndex((aliases) => aliases.includes(normalized));
  return index < 0 ? groups.length : index;
};

const answerGroupHeadingId = (groupBy: CustomerAnswerGroupBy, groupIndex: number): string =>
  `geo-answer-group-${groupBy}-${groupIndex}`;

function AnswerGroup({
  brandName,
  groupBy,
  groupIndex,
  label,
  rows,
  ordinalByAnswer,
  total,
  onOpen,
}: {
  brandName: string;
  groupBy: CustomerAnswerGroupBy;
  groupIndex: number;
  label: string;
  rows: readonly CustomerAnswerExplorerRow[];
  ordinalByAnswer: ReadonlyMap<string, number>;
  total: number;
  onOpen: (row: CustomerAnswerExplorerRow) => void;
}) {
  const headingId = answerGroupHeadingId(groupBy, groupIndex);
  const mentionCount = rows.filter((row) => row.mentioned).length;
  const citationCount = rows.reduce((total, row) => total + row.citation_count, 0);
  const ordinalWidth = Math.max(2, String(Math.max(1, total)).length);

  return (
    <section className="geo-answer-group" data-tone={groupIndex % 4} aria-labelledby={headingId}>
      <header className="geo-answer-group__header">
        <div>
          <span>{groupByPresentation[groupBy].groupLabel}</span>
          <h3 id={headingId}>{label}</h3>
        </div>
        <div className="geo-answer-group__stats" aria-label={`${label}分类摘要`}>
          <strong>{rows.length.toLocaleString('zh-CN')} 条回答</strong>
          <span>{mentionCount.toLocaleString('zh-CN')} 条提及品牌</span>
          <span>{citationCount.toLocaleString('zh-CN')} 条引用</span>
        </div>
      </header>

      <div className="geo-answer-group__rows" role="region" aria-label={`${label}回答明细`}>
        {rows.map((row, index) => {
          const queryLabel = row.query_text?.trim() || '未关联原始问题';
          const ordinal = ordinalByAnswer.get(row.answer_pub_id) ?? index + 1;
          const secondaryDimensions = [
            ...(groupBy === 'platform' ? [] : [{ label: '平台', value: row.model }]),
            ...(groupBy === 'mode' ? [] : [{ label: '模式', value: row.mode }]),
            ...(groupBy === 'region' ? [] : [{ label: '地域', value: row.region }]),
          ];
          return (
            <article className="geo-answer-row" key={row.answer_pub_id}>
              <div className="geo-answer-row__index" aria-hidden="true">
                <span>{String(ordinal).padStart(ordinalWidth, '0')}</span>
              </div>
              <div className="geo-answer-row__body">
                <header>
                  <span>用户真实问题</span>
                  <time dateTime={row.capture_time}>{formatCaptureTime(row.capture_time)}</time>
                </header>
                <h4>{queryLabel}</h4>
                <p className="geo-answer-row__lead">查看该平台官方实时回答页及逐条引用信源。</p>
                <div className="geo-answer-row__dimensions" aria-label="回答采集条件">
                  {secondaryDimensions.map((dimension) => (
                    <span key={dimension.label}>
                      <small>{dimension.label}</small>
                      {dimension.value || '未标注'}
                    </span>
                  ))}
                </div>
              </div>
              <div className="geo-answer-row__result">
                <AnswerResultBadges brandName={brandName} row={row} />
                <button type="button" onClick={() => onOpen(row)}>
                  <span>查看官方回答与信源</span>
                  <span aria-hidden="true">→</span>
                </button>
                <small>官方实时页 · 引用信源表 · 分享链接</small>
              </div>
            </article>
          );
        })}
      </div>
    </section>
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

function LegacyCustomerAnswerExplorer({
  brandName,
  loadPage,
  loadDetail,
  loadEvidenceImage,
  fixturePage,
}: CustomerAnswerExplorerProps) {
  const [searchDraft, setSearchDraft] = useState('');
  const [search, setSearch] = useState('');
  const [mentioned, setMentioned] = useState<CustomerAnswerMentionFilter>('all');
  const [sentiment, setSentiment] = useState<CustomerAnswerSentimentFilter>('all');
  const [groupBy, setGroupBy] = useState<CustomerAnswerGroupBy>('platform');
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState<CustomerAnswerPageSize>(20);
  const [state, setState] = useState<LoadState>(fixturePage ? 'ready' : 'loading');
  const [result, setResult] = useState<CustomerAnswerExplorerPage | null>(fixturePage ?? null);
  const [retryKey, setRetryKey] = useState(0);
  const [selectedAnswer, setSelectedAnswer] = useState<CustomerAnswerExplorerRow | null>(null);
  const [detailState, setDetailState] = useState<DetailState>('idle');
  const [detail, setDetail] = useState<CustomerAnswerDetail | null>(null);
  const [jumpDraft, setJumpDraft] = useState('1');
  const requestSequence = useRef(0);
  const detailRequestSequence = useRef(0);

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
        const lastOffset =
          page.page.total === 0
            ? 0
            : Math.floor((page.page.total - 1) / page.page.limit) * page.page.limit;
        if (page.data.length === 0 && page.page.offset > lastOffset) {
          setOffset(lastOffset);
          return;
        }
        setResult(page);
        setState('ready');
      },
      (error: unknown) => {
        if (cancelled || requestId !== requestSequence.current) return;
        setState(
          error instanceof CustomerAnswerLoadError && error.kind === 'forbidden'
            ? 'forbidden'
            : 'failed',
        );
      },
    );
    return () => {
      cancelled = true;
    };
  }, [fixturePage, loadPage, query, retryKey]);

  const openAnswer = (row: CustomerAnswerExplorerRow) => {
    const requestId = ++detailRequestSequence.current;
    setSelectedAnswer(row);
    setDetail(null);
    if (!loadDetail) {
      setDetailState('failed');
      return;
    }
    setDetailState('loading');
    void loadDetail(row.answer_pub_id).then(
      (nextDetail) => {
        if (requestId !== detailRequestSequence.current) return;
        setDetail(nextDetail);
        setDetailState('ready');
      },
      () => {
        if (requestId !== detailRequestSequence.current) return;
        setDetailState('failed');
      },
    );
  };

  const closeAnswer = () => {
    detailRequestSequence.current += 1;
    setSelectedAnswer(null);
    setDetail(null);
    setDetailState('idle');
  };

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
  const requestedPage = Math.floor(offset / limit) + 1;
  const isInitialLoading = state === 'loading' && result === null;
  const isRefreshing = state === 'loading' && result !== null;
  const canShowResult = state !== 'forbidden' && result !== null;
  const currentMentionCount = rows.filter((row) => row.mentioned).length;
  const currentCitationCount = rows.filter((row) => row.citation_count > 0).length;
  const hasFilters = search.length > 0 || mentioned !== 'all' || sentiment !== 'all';
  const dimensionCounts = useMemo(
    () => ({
      platform: new Set(rows.map((row) => answerGroupValue(row, 'platform'))).size,
      mode: new Set(rows.map((row) => answerGroupValue(row, 'mode'))).size,
      region: new Set(rows.map((row) => answerGroupValue(row, 'region'))).size,
    }),
    [rows],
  );
  const groupedRows = useMemo(() => {
    const groups = new Map<string, CustomerAnswerExplorerRow[]>();
    for (const row of rows) {
      const label = answerGroupValue(row, groupBy);
      const group = groups.get(label) ?? [];
      group.push(row);
      groups.set(label, group);
    }
    return [...groups.entries()]
      .map(([label, group]) => ({ label, rows: group }))
      .sort((left, right) => {
        if (groupBy === 'platform') {
          return (
            platformGroupPriority(left.label) - platformGroupPriority(right.label) ||
            left.label.localeCompare(right.label, 'zh-CN')
          );
        }
        return (
          right.rows.length - left.rows.length || left.label.localeCompare(right.label, 'zh-CN')
        );
      });
  }, [groupBy, rows]);
  const ordinalByAnswer = useMemo(
    () =>
      new Map(
        rows.map((row, index) => [row.answer_pub_id, (page?.offset ?? 0) + index + 1] as const),
      ),
    [page?.offset, rows],
  );
  const paginationItems = useMemo(
    () => customerAnswerPaginationItems(currentPage, totalPages),
    [currentPage, totalPages],
  );

  useEffect(() => {
    setJumpDraft(String(currentPage));
  }, [currentPage]);

  const goToPage = (targetPage: number) => {
    if (state === 'loading' || !page) return;
    const boundedPage = Math.min(totalPages, Math.max(1, targetPage));
    if (boundedPage === currentPage) return;
    setOffset((boundedPage - 1) * limit);
  };

  const submitJump = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const targetPage = Number.parseInt(jumpDraft, 10);
    if (!Number.isSafeInteger(targetPage)) {
      setJumpDraft(String(currentPage));
      return;
    }
    goToPage(targetPage);
  };

  return (
    <section
      className="geo-answer-explorer"
      aria-labelledby="geo-answer-explorer-title"
      aria-busy={state === 'loading'}
    >
      <header className="geo-answer-explorer__hero">
        <div>
          <span>VERIFIED ANSWER LIBRARY</span>
          <h2 id="geo-answer-explorer-title">回答证据库</h2>
          <p>按平台逐条查看 {brandName} 的官方实时回答页，并在同一屏核对结构化引用信源。</p>
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
          detail={hasFilters ? '已应用搜索或筛选' : '所选统计区间'}
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

      {isRefreshing ? (
        <div className="geo-answer-explorer__refreshing" role="status" aria-live="polite">
          <span aria-hidden="true" />
          <div>
            <strong>正在加载第 {requestedPage.toLocaleString('zh-CN')} 页</strong>
            <small>当前页暂时保留；新结果返回后一次性替换，不混排两页数据。</small>
          </div>
        </div>
      ) : null}

      {canShowResult && rows.length > 0 ? (
        <div className="geo-answer-explorer__classification" aria-label="当前页排列方式">
          <div>
            <span>当前页排列</span>
            <strong>
              {dimensionCounts.platform.toLocaleString('zh-CN')} 个平台 ·{' '}
              {dimensionCounts.mode.toLocaleString('zh-CN')} 种模式 ·{' '}
              {dimensionCounts.region.toLocaleString('zh-CN')} 个地域
            </strong>
            <small>{groupByPresentation[groupBy].description}</small>
          </div>
          <div
            className="geo-answer-explorer__group-switch"
            role="group"
            aria-label="选择当前页排列方式"
          >
            {(Object.keys(groupByPresentation) as CustomerAnswerGroupBy[]).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={groupBy === value}
                onClick={() => {
                  setGroupBy(value);
                }}
              >
                {groupByPresentation[value].label}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {isInitialLoading ? <LoadingPanel /> : null}

      {state === 'failed' && result === null ? (
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

      {state === 'failed' && result !== null ? (
        <div
          className="geo-answer-explorer__state geo-answer-explorer__state--failed geo-answer-explorer__state--inline"
          role="alert"
        >
          <span aria-hidden="true">!</span>
          <div>
            <strong>第 {requestedPage.toLocaleString('zh-CN')} 页加载失败</strong>
            <p>已保留上一份成功结果，没有把失败请求误显示成空页。</p>
          </div>
          <button type="button" onClick={() => setRetryKey((value) => value + 1)}>
            重试本页
          </button>
        </div>
      ) : null}

      {state === 'forbidden' ? (
        <div className="geo-answer-explorer__state geo-answer-explorer__state--failed" role="alert">
          <span aria-hidden="true">!</span>
          <div>
            <strong>登录状态已失效</strong>
            <p>继续重试不会恢复会话，请重新登录后再查看回答。</p>
          </div>
          <a href="/platform/operations/login">重新登录</a>
        </div>
      ) : null}

      {state === 'ready' && rows.length === 0 ? (
        <div className="geo-answer-explorer__state" role="status">
          <span aria-hidden="true">0</span>
          <div>
            <strong>{hasFilters ? '没有匹配的回答' : '所选统计区间暂无回答'}</strong>
            <p>
              {hasFilters
                ? '更换关键词或清除筛选条件，查看其他真实回答。'
                : '所选统计区间尚无可展示的 AI 回答。'}
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

      {canShowResult && rows.length > 0 ? (
        <div
          className="geo-answer-explorer__group-stage"
          role="region"
          aria-label="当前页回答列表"
          data-refreshing={isRefreshing || undefined}
        >
          {groupedRows.map((group, index) => (
            <AnswerGroup
              key={`${groupBy}-${group.label}`}
              brandName={brandName}
              groupBy={groupBy}
              groupIndex={index}
              label={group.label}
              rows={group.rows}
              ordinalByAnswer={ordinalByAnswer}
              total={total}
              onOpen={openAnswer}
            />
          ))}
        </div>
      ) : null}

      {page && state !== 'forbidden' ? (
        <footer className="geo-answer-explorer__pagination">
          <div className="geo-answer-explorer__range" aria-live="polite">
            <strong>
              {firstItem.toLocaleString('zh-CN')}–{lastItem.toLocaleString('zh-CN')}
            </strong>
            <span> / 共 {total.toLocaleString('zh-CN')} 条</span>
          </div>
          <label>
            每页
            <select
              value={limit}
              disabled={state === 'loading'}
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
              disabled={state === 'loading' || currentPage === 1}
              onClick={() => goToPage(currentPage - 1)}
            >
              上一页
            </button>
            <div className="geo-answer-explorer__page-numbers">
              {paginationItems.map((item) =>
                typeof item === 'number' ? (
                  <button
                    type="button"
                    key={item}
                    aria-label={`第 ${item.toLocaleString('zh-CN')} 页`}
                    aria-current={item === currentPage ? 'page' : undefined}
                    disabled={state === 'loading'}
                    onClick={() => goToPage(item)}
                  >
                    {item.toLocaleString('zh-CN')}
                  </button>
                ) : (
                  <span key={item} aria-hidden="true">
                    …
                  </span>
                ),
              )}
            </div>
            <button
              type="button"
              disabled={state === 'loading' || !page.has_more}
              onClick={() => goToPage(currentPage + 1)}
            >
              下一页
            </button>
          </nav>
          <form
            className="geo-answer-explorer__page-jump"
            aria-label="跳转到指定回答页"
            onSubmit={submitJump}
          >
            <label htmlFor="geo-answer-page-jump">跳至</label>
            <input
              id="geo-answer-page-jump"
              type="number"
              inputMode="numeric"
              min={1}
              max={totalPages}
              value={jumpDraft}
              disabled={state === 'loading' || totalPages <= 1}
              onChange={(event) => setJumpDraft(event.currentTarget.value)}
            />
            <span>/ {totalPages.toLocaleString('zh-CN')} 页</span>
            <button type="submit" disabled={state === 'loading' || totalPages <= 1}>
              跳转
            </button>
          </form>
        </footer>
      ) : null}

      {selectedAnswer ? (
        <AnswerDossier
          key={selectedAnswer.answer_pub_id}
          brandName={brandName}
          row={selectedAnswer}
          runs={rows}
          detailState={detailState}
          detail={detail}
          {...(loadEvidenceImage ? { loadEvidenceImage } : {})}
          onSelectRun={openAnswer}
          onClose={closeAnswer}
        />
      ) : null}
    </section>
  );
}

type LibraryLoadState = 'idle' | 'loading' | 'ready' | 'failed' | 'forbidden';
type LibraryLayer = 'meta' | 'questions' | 'runs' | 'answer';

const libraryRootLimits = [8, 12, 20] as const;
const libraryRunLimits = [10, 20, 50] as const;

const librarySnapshot = (page: CustomerAnswerLibraryPage): CustomerAnswerLibrarySnapshot => ({
  snapshotId: page.snapshot_id,
  snapshotAt: page.snapshot_at,
  ...(page.metric_snapshot_set_pub_id && page.metric_snapshot_set_hash
    ? {
        metricSnapshotSetPubId: page.metric_snapshot_set_pub_id,
        metricSnapshotSetHash: page.metric_snapshot_set_hash,
      }
    : {}),
});

const libraryStateForError = (error: unknown): LibraryLoadState =>
  error instanceof CustomerAnswerLoadError && error.kind === 'forbidden' ? 'forbidden' : 'failed';

const libraryLayerLabel: Record<LibraryLayer, string> = {
  meta: '关键词目录',
  questions: '具体问题',
  runs: '采集答案',
  answer: '答案正文',
};

function LibraryPath({
  layer,
  meta,
  question,
  run,
  onRoot,
  onMeta,
  onQuestion,
}: {
  layer: LibraryLayer;
  meta: CustomerAnswerLibraryMeta | CustomerAnswerLibraryMetaDetail | null;
  question: CustomerAnswerLibraryQuestion | null;
  run: CustomerAnswerLibraryRun | null;
  onRoot: () => void;
  onMeta: () => void;
  onQuestion: () => void;
}) {
  return (
    <nav className="geo-answer-library__path" aria-label="答案库路径">
      <button type="button" aria-current={layer === 'meta' ? 'page' : undefined} onClick={onRoot}>
        关键词
      </button>
      {meta ? (
        <>
          <span aria-hidden="true">/</span>
          <button
            type="button"
            aria-current={layer === 'questions' ? 'page' : undefined}
            onClick={onMeta}
          >
            查询 {String(meta.ordinal).padStart(2, '0')} · {meta.label}
          </button>
        </>
      ) : null}
      {question ? (
        <>
          <span aria-hidden="true">/</span>
          <button
            type="button"
            aria-current={layer === 'runs' ? 'page' : undefined}
            onClick={onQuestion}
          >
            问题 {String(question.ordinal).padStart(2, '0')} · {question.variant_label}
          </button>
        </>
      ) : null}
      {run ? (
        <>
          <span aria-hidden="true">/</span>
          <span aria-current={layer === 'answer' ? 'page' : undefined}>
            {run.model} · {run.region} · 第 {run.repeat_index} 遍 · {run.mode} ·{' '}
            {formatCaptureTime(run.capture_time)}
          </span>
        </>
      ) : null}
    </nav>
  );
}

function LibraryDimensions({
  models,
  regions,
  modes,
  compact = false,
}: {
  models: readonly CustomerAnswerLibraryDimensionProjection[];
  regions: readonly CustomerAnswerLibraryDimensionProjection[];
  modes: readonly CustomerAnswerLibraryDimensionProjection[];
  compact?: boolean;
}) {
  const values = [
    ...models.map((item) => ({ prefix: '平台', ...item })),
    ...regions.map((item) => ({ prefix: '地域', ...item })),
    ...modes.map((item) => ({ prefix: '模式', ...item })),
  ];
  const visible = compact ? values.slice(0, 6) : values;
  return (
    <div className="geo-answer-library__tags" aria-label="采集维度">
      {visible.map((item) => (
        <span key={`${item.prefix}:${item.label}`}>
          <small>{item.prefix}</small>
          {item.label}
          <b>{item.answer_count.toLocaleString('zh-CN')}</b>
        </span>
      ))}
      {compact && values.length > visible.length ? (
        <em>+{values.length - visible.length}</em>
      ) : null}
    </div>
  );
}

function LibraryStats({
  answerCount,
  citedAnswerCount,
  citationCount,
  mentionedAnswerCount,
}: {
  answerCount: number;
  citedAnswerCount: number;
  citationCount: number;
  mentionedAnswerCount: number;
}) {
  return (
    <dl className="geo-answer-library__stats">
      <div>
        <dt>采集回答</dt>
        <dd>{answerCount.toLocaleString('zh-CN')}</dd>
      </div>
      <div>
        <dt>有引用回答</dt>
        <dd>{citedAnswerCount.toLocaleString('zh-CN')}</dd>
      </div>
      <div>
        <dt>引用总数</dt>
        <dd>{citationCount.toLocaleString('zh-CN')}</dd>
      </div>
      <div>
        <dt>品牌提及</dt>
        <dd>{mentionedAnswerCount.toLocaleString('zh-CN')}</dd>
      </div>
    </dl>
  );
}

function LibraryLoading({ label }: { label: string }) {
  return (
    <div className="geo-answer-library__loading" role="status" aria-live="polite">
      <span aria-hidden="true" />
      <div>
        <strong>{label}</strong>
        <small>正在读取同一份冻结目录，已显示内容不会与新页混排。</small>
      </div>
    </div>
  );
}

function LibraryFailure({
  state,
  title,
  onRetry,
}: {
  state: Extract<LibraryLoadState, 'failed' | 'forbidden'>;
  title: string;
  onRetry: () => void;
}) {
  if (state === 'forbidden') {
    return (
      <div className="geo-answer-library__failure" role="alert">
        <strong>登录状态已失效</strong>
        <span>请重新登录后再查看私有答案库。</span>
        <a href="/platform/operations/login">重新登录</a>
      </div>
    );
  }
  return (
    <div className="geo-answer-library__failure" role="alert">
      <strong>{title}</strong>
      <span>未把失败请求当成空数据，可以原地重试。</span>
      <button type="button" onClick={onRetry}>
        重试
      </button>
    </div>
  );
}

function LibraryPager({
  total,
  offset,
  limit,
  hasMore,
  loading,
  label,
  onOffset,
}: {
  total: number;
  offset: number;
  limit: number;
  hasMore: boolean;
  loading: boolean;
  label: string;
  onOffset: (offset: number) => void;
}) {
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const items = customerAnswerPaginationItems(currentPage, totalPages);
  return (
    <nav className="geo-answer-library__pager" aria-label={label}>
      <span>
        第 {currentPage.toLocaleString('zh-CN')} / {totalPages.toLocaleString('zh-CN')} 页 · 共{' '}
        {total.toLocaleString('zh-CN')} 项
      </span>
      <div>
        <button
          type="button"
          disabled={loading || currentPage <= 1}
          onClick={() => onOffset(Math.max(0, offset - limit))}
        >
          上一页
        </button>
        {items.map((item) =>
          typeof item === 'number' ? (
            <button
              type="button"
              key={item}
              aria-current={item === currentPage ? 'page' : undefined}
              disabled={loading}
              onClick={() => onOffset((item - 1) * limit)}
            >
              {item}
            </button>
          ) : (
            <i key={item} aria-hidden="true">
              …
            </i>
          ),
        )}
        <button
          type="button"
          disabled={loading || !hasMore}
          onClick={() => onOffset(offset + limit)}
        >
          下一页
        </button>
      </div>
    </nav>
  );
}

function LibraryAnswerView({
  brandName,
  answer,
  relationState,
  detail,
  loadEvidenceImage,
}: {
  brandName: string;
  answer: CustomerAnswerLibraryAnswer;
  relationState: DetailState;
  detail: CustomerAnswerDetail | null;
  loadEvidenceImage?: CustomerAnswerEvidenceImageLoader;
}) {
  const run = answer.answer;
  const row: CustomerAnswerExplorerRow = {
    answer_pub_id: run.answer_pub_id,
    query_pub_id: null,
    query_text: answer.question_text,
    response_text: answer.response_text,
    model: run.model,
    region: run.region,
    mode: run.mode,
    capture_time: run.capture_time,
    mentioned: run.mentioned ?? false,
    rank: run.rank,
    sentiment: run.sentiment,
    recommended: run.recommended,
    citation_count: run.citation_count,
  };
  const officialShareArtifact = detail?.shareArtifact ?? null;
  const officialShareUrl = safeOfficialShareUrl(officialShareArtifact?.shareUrl ?? null, run.model);
  const officialShareEmbeddable =
    Boolean(officialShareUrl) &&
    (officialShareArtifact?.availabilityStatus === 'reachable' ||
      officialShareArtifact?.availabilityStatus === 'redirected') &&
    officialShareArtifact?.embedStatus === 'allowed';
  const uniqueDomains = new Set(detail?.citations.map((citation) => citation.host) ?? []).size;
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const copyShareLink = async () => {
    if (!officialShareUrl || !navigator.clipboard?.writeText) {
      setCopyState('failed');
      return;
    }
    try {
      await navigator.clipboard.writeText(officialShareUrl);
      setCopyState('copied');
      window.setTimeout(() => setCopyState('idle'), 1_500);
    } catch {
      setCopyState('failed');
    }
  };

  useEffect(() => setCopyState('idle'), [officialShareUrl]);

  return (
    <article
      className="geo-answer-library__answer"
      aria-labelledby="geo-answer-library-answer-title"
    >
      <header className="geo-answer-library__answer-head">
        <div className="geo-answer-library__monogram" aria-hidden="true">
          {platformMonogram(run.model)}
        </div>
        <div>
          <span>{answer.variant_label} · 完整回答</span>
          <h3 id="geo-answer-library-answer-title">{answer.question_text}</h3>
          <div className="geo-answer-library__answer-tags">
            <b>{run.model}</b>
            <b>{run.region}</b>
            <b>{run.mode}</b>
            <b>第 {run.repeat_index} 遍</b>
            <time dateTime={run.capture_time}>{formatCaptureTime(run.capture_time)}</time>
          </div>
        </div>
        {officialShareUrl ? (
          <div className="geo-answer-library__answer-actions">
            <button type="button" onClick={() => void copyShareLink()}>
              {copyState === 'copied'
                ? '分享链接已复制'
                : copyState === 'failed'
                  ? '复制失败'
                  : '复制分享链接'}
            </button>
            <a href={officialShareUrl} target="_blank" rel="noreferrer noopener">
              打开官方原页 ↗
            </a>
          </div>
        ) : null}
      </header>

      <section className="geo-answer-library__answer-metrics" aria-label="回答分析摘要">
        <DetailMetric
          label="品牌位置"
          value={
            run.analysis_state === 'pending'
              ? '分析中'
              : run.rank === null
                ? '未进入排名'
                : `第 ${run.rank} 位`
          }
          note={
            run.analysis_state === 'pending'
              ? '正文已采集，结构化指标尚未完成'
              : run.mentioned
                ? `回答中已识别 ${brandName}`
                : `回答中未识别 ${brandName}`
          }
        />
        <DetailMetric
          label="引用证据"
          value={relationState === 'ready' ? `${uniqueDomains} 个域名` : '核对中'}
          note={`${detail?.citations.length ?? run.citation_count} 条规范化引用`}
        />
      </section>

      <div className="geo-answer-library__reading-layout">
        <AnswerDisplay
          row={row}
          detailState={relationState}
          detail={detail}
          officialShareUrl={officialShareUrl}
          officialShareEmbeddable={officialShareEmbeddable}
          {...(loadEvidenceImage ? { loadEvidenceImage } : {})}
        />
        <CitationRail
          state={relationState}
          detail={detail}
          answerCaptureTime={run.capture_time}
          expectedCount={run.citation_count}
        />
      </div>
    </article>
  );
}

export function CustomerAnswerExplorer({
  brandName,
  loadLibraryPage,
  loadMetaQuery,
  loadQuestionRuns,
  loadAnswer,
  loadDetail,
  loadEvidenceImage,
  fixturePage,
}: CustomerAnswerLibraryProps) {
  const [searchDraft, setSearchDraft] = useState('');
  const [search, setSearch] = useState('');
  const [rootOffset, setRootOffset] = useState(0);
  const [rootLimit, setRootLimit] = useState<(typeof libraryRootLimits)[number]>(8);
  const [rootState, setRootState] = useState<LibraryLoadState>(fixturePage ? 'ready' : 'loading');
  const [rootResult, setRootResult] = useState<CustomerAnswerLibraryPage | null>(
    fixturePage ?? null,
  );
  const [rootRetry, setRootRetry] = useState(0);
  const [selectedMeta, setSelectedMeta] = useState<CustomerAnswerLibraryMeta | null>(null);
  const [metaResult, setMetaResult] = useState<CustomerAnswerLibraryMetaDetail | null>(null);
  const [metaState, setMetaState] = useState<LibraryLoadState>('idle');
  const [metaRetry, setMetaRetry] = useState(0);
  const [selectedQuestion, setSelectedQuestion] = useState<CustomerAnswerLibraryQuestion | null>(
    null,
  );
  const [runsResult, setRunsResult] = useState<CustomerAnswerLibraryRuns | null>(null);
  const [runsState, setRunsState] = useState<LibraryLoadState>('idle');
  const [runsRetry, setRunsRetry] = useState(0);
  const [runOffset, setRunOffset] = useState(0);
  const [runLimit, setRunLimit] = useState<(typeof libraryRunLimits)[number]>(20);
  const [runModel, setRunModel] = useState('all');
  const [runRegion, setRunRegion] = useState('all');
  const [runMode, setRunMode] = useState('all');
  const [selectedRun, setSelectedRun] = useState<CustomerAnswerLibraryRun | null>(null);
  const [answerResult, setAnswerResult] = useState<CustomerAnswerLibraryAnswer | null>(null);
  const [answerState, setAnswerState] = useState<LibraryLoadState>('idle');
  const [answerRetry, setAnswerRetry] = useState(0);
  const [relationState, setRelationState] = useState<DetailState>('idle');
  const [detail, setDetail] = useState<CustomerAnswerDetail | null>(null);
  const rootSnapshot = useRef<CustomerAnswerLibrarySnapshot | null>(
    fixturePage ? librarySnapshot(fixturePage) : null,
  );
  const rootRequest = useRef(0);
  const metaRequest = useRef(0);
  const runsRequest = useRef(0);
  const answerRequest = useRef(0);

  const clearAnswer = () => {
    answerRequest.current += 1;
    setSelectedRun(null);
    setAnswerResult(null);
    setAnswerState('idle');
    setRelationState('idle');
    setDetail(null);
  };

  const clearQuestion = () => {
    runsRequest.current += 1;
    clearAnswer();
    setSelectedQuestion(null);
    setRunsResult(null);
    setRunsState('idle');
    setRunOffset(0);
    setRunModel('all');
    setRunRegion('all');
    setRunMode('all');
  };

  const clearMeta = () => {
    metaRequest.current += 1;
    clearQuestion();
    setSelectedMeta(null);
    setMetaResult(null);
    setMetaState('idle');
  };

  useEffect(() => {
    const requestId = ++rootRequest.current;
    let cancelled = false;
    setRootState('loading');
    const retainedSnapshot = rootSnapshot.current;
    void loadLibraryPage({
      search,
      offset: rootOffset,
      limit: rootLimit,
      ...(retainedSnapshot
        ? {
            snapshotId: retainedSnapshot.snapshotId,
            snapshotAt: retainedSnapshot.snapshotAt,
          }
        : {}),
    }).then(
      (page) => {
        if (cancelled || requestId !== rootRequest.current) return;
        const lastOffset =
          page.page.total === 0
            ? 0
            : Math.floor((page.page.total - 1) / page.page.limit) * page.page.limit;
        if (page.data.length === 0 && page.page.offset > lastOffset) {
          setRootOffset(lastOffset);
          return;
        }
        rootSnapshot.current = librarySnapshot(page);
        setRootResult(page);
        setRootState('ready');
      },
      (error: unknown) => {
        if (cancelled || requestId !== rootRequest.current) return;
        setRootState(libraryStateForError(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [loadLibraryPage, rootLimit, rootOffset, rootRetry, search]);

  useEffect(() => {
    if (!selectedMeta || !rootResult) return;
    const requestId = ++metaRequest.current;
    let cancelled = false;
    setMetaState('loading');
    void loadMetaQuery(selectedMeta.meta_query_id, librarySnapshot(rootResult)).then(
      (result) => {
        if (cancelled || requestId !== metaRequest.current) return;
        setMetaResult(result);
        setMetaState('ready');
      },
      (error: unknown) => {
        if (cancelled || requestId !== metaRequest.current) return;
        setMetaState(libraryStateForError(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [loadMetaQuery, metaRetry, rootResult, selectedMeta]);

  useEffect(() => {
    if (!selectedQuestion || !rootResult) return;
    const requestId = ++runsRequest.current;
    let cancelled = false;
    setRunsState('loading');
    void loadQuestionRuns(selectedQuestion.question_id, {
      ...librarySnapshot(rootResult),
      model: runModel,
      region: runRegion,
      mode: runMode,
      offset: runOffset,
      limit: runLimit,
    }).then(
      (result) => {
        if (cancelled || requestId !== runsRequest.current) return;
        const lastOffset =
          result.page.total === 0
            ? 0
            : Math.floor((result.page.total - 1) / result.page.limit) * result.page.limit;
        if (result.data.length === 0 && result.page.offset > lastOffset) {
          setRunOffset(lastOffset);
          return;
        }
        setRunsResult(result);
        setRunsState('ready');
      },
      (error: unknown) => {
        if (cancelled || requestId !== runsRequest.current) return;
        setRunsState(libraryStateForError(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [
    loadQuestionRuns,
    rootResult,
    runLimit,
    runMode,
    runModel,
    runOffset,
    runRegion,
    runsRetry,
    selectedQuestion,
  ]);

  useEffect(() => {
    if (!selectedRun || !rootResult) return;
    const requestId = ++answerRequest.current;
    let cancelled = false;
    const snapshot = librarySnapshot(rootResult);
    setAnswerState('loading');
    setAnswerResult(null);
    setDetail(null);
    setRelationState(loadDetail ? 'loading' : 'failed');
    void loadAnswer(selectedRun.answer_pub_id, snapshot).then(
      (result) => {
        if (cancelled || requestId !== answerRequest.current) return;
        setAnswerResult(result);
        setAnswerState('ready');
      },
      (error: unknown) => {
        if (cancelled || requestId !== answerRequest.current) return;
        setAnswerState(libraryStateForError(error));
      },
    );
    if (loadDetail) {
      void loadDetail(selectedRun.answer_pub_id, snapshot).then(
        (result) => {
          if (cancelled || requestId !== answerRequest.current) return;
          setDetail(result);
          setRelationState('ready');
        },
        () => {
          if (cancelled || requestId !== answerRequest.current) return;
          setRelationState('failed');
        },
      );
    }
    return () => {
      cancelled = true;
    };
  }, [answerRetry, loadAnswer, loadDetail, rootResult, selectedRun]);

  const layer: LibraryLayer = selectedRun
    ? 'answer'
    : selectedQuestion
      ? 'runs'
      : selectedMeta
        ? 'questions'
        : 'meta';
  const pathMeta = metaResult ?? selectedMeta;
  const snapshotTime = rootResult ? formatCaptureTime(rootResult.snapshot_at) : null;
  const totalPages = rootResult
    ? Math.max(1, Math.ceil(rootResult.page.total / rootResult.page.limit))
    : 1;

  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    clearMeta();
    setRootOffset(0);
    setSearch(searchDraft.trim());
  };

  const openMeta = (meta: CustomerAnswerLibraryMeta) => {
    clearQuestion();
    setSelectedMeta(meta);
    setMetaResult(null);
    setMetaState('loading');
  };

  const openQuestion = (question: CustomerAnswerLibraryQuestion) => {
    clearAnswer();
    setSelectedQuestion(question);
    setRunsResult(null);
    setRunOffset(0);
    setRunModel('all');
    setRunRegion('all');
    setRunMode('all');
    setRunsState('loading');
  };

  const goRoot = () => clearMeta();
  const goMeta = () => clearQuestion();
  const goQuestion = () => clearAnswer();

  return (
    <section className="geo-answer-library" aria-labelledby="geo-answer-library-title">
      <header className="geo-answer-library__hero">
        <div>
          <span>ANSWER ARCHIVE · SNAPSHOT BOUND</span>
          <h2 id="geo-answer-library-title">{brandName} 回答证据库</h2>
          <p>
            先按客户确认的关键词进入四条具体问题，再选平台、地域、采集遍次与模式。答案正文只在最后一层从后端按需读取。
          </p>
        </div>
        <ol aria-label="四层浏览进度">
          {(Object.keys(libraryLayerLabel) as LibraryLayer[]).map((value, index) => (
            <li key={value} data-active={layer === value || undefined}>
              <b>{index + 1}</b>
              <span>{libraryLayerLabel[value]}</span>
            </li>
          ))}
        </ol>
      </header>

      <LibraryPath
        layer={layer}
        meta={pathMeta}
        question={selectedQuestion}
        run={selectedRun}
        onRoot={goRoot}
        onMeta={goMeta}
        onQuestion={goQuestion}
      />

      {snapshotTime ? (
        <div className="geo-answer-library__snapshot" role="note">
          <span aria-hidden="true">◈</span>
          <p>
            <strong>目录快照已冻结</strong>
            <small>
              截点 {snapshotTime}；分页与下钻始终使用同一版配置，新采集不会使当前页跳项。
            </small>
            {rootResult?.metric_snapshot_set_pub_id && rootResult.metric_snapshot_set_hash ? (
              <small>
                指标快照集 {rootResult.metric_snapshot_set_pub_id} · hash{' '}
                <code>{rootResult.metric_snapshot_set_hash}</code>
              </small>
            ) : null}
          </p>
        </div>
      ) : null}

      {layer === 'meta' ? (
        <>
          <form className="geo-answer-library__search" role="search" onSubmit={submitSearch}>
            <label htmlFor="geo-answer-library-search">搜索已确认关键词或具体问题</label>
            <div>
              <input
                id="geo-answer-library-search"
                type="search"
                maxLength={200}
                value={searchDraft}
                placeholder="例如：高校资产排查"
                onChange={(event) => setSearchDraft(event.currentTarget.value)}
              />
              {(searchDraft || search) && (
                <button
                  type="button"
                  onClick={() => {
                    setSearchDraft('');
                    setSearch('');
                    setRootOffset(0);
                  }}
                >
                  清除
                </button>
              )}
              <button type="submit">查找</button>
            </div>
          </form>

          {rootResult ? (
            <section className="geo-answer-library__totals" aria-label="答案库总览">
              <div>
                <span>已确认元查询</span>
                <strong>{rootResult.totals.meta_query_count.toLocaleString('zh-CN')}</strong>
                <small>客户确认的一级目录</small>
              </div>
              <div>
                <span>具体问题</span>
                <strong>{rootResult.totals.question_count.toLocaleString('zh-CN')}</strong>
                <small>原问题与变体的合计</small>
              </div>
              <div>
                <span>采集回答</span>
                <strong>{rootResult.totals.answer_count.toLocaleString('zh-CN')}</strong>
                <small>
                  {rootResult.totals.mentioned_answer_count.toLocaleString('zh-CN')} 条提及品牌
                </small>
              </div>
              <div>
                <span>引用规模</span>
                <strong>{rootResult.totals.citation_count.toLocaleString('zh-CN')}</strong>
                <small>
                  {rootResult.totals.cited_answer_count.toLocaleString('zh-CN')} 条回答含引用
                </small>
              </div>
            </section>
          ) : null}

          {rootState === 'loading' ? (
            <LibraryLoading
              label={`正在读取第 ${Math.floor(rootOffset / rootLimit) + 1} 页关键词`}
            />
          ) : null}
          {rootState === 'failed' || rootState === 'forbidden' ? (
            <LibraryFailure
              state={rootState}
              title="关键词目录读取失败"
              onRetry={() => setRootRetry((value) => value + 1)}
            />
          ) : null}
          {rootResult && rootResult.data.length === 0 && rootState !== 'loading' ? (
            <div className="geo-answer-library__empty" role="status">
              <strong>{search ? '没有匹配的关键词' : '当前区间没有已确认关键词'}</strong>
              <span>目录为空不代表采集系统不存在原始数据。</span>
            </div>
          ) : null}
          {rootResult && rootResult.data.length > 0 ? (
            <div
              className="geo-answer-library__meta-list"
              data-loading={rootState === 'loading' || undefined}
            >
              {rootResult.data.map((meta) => (
                <article key={meta.meta_query_id} className="geo-answer-library__meta-card">
                  <div className="geo-answer-library__ordinal" aria-hidden="true">
                    {String(meta.ordinal).padStart(2, '0')}
                  </div>
                  <div className="geo-answer-library__meta-main">
                    <header>
                      <div>
                        <span>已确认元查询</span>
                        <h3>{meta.label}</h3>
                      </div>
                      <time dateTime={meta.latest_capture_time ?? undefined}>
                        {meta.latest_capture_time
                          ? `最新 ${formatCaptureTime(meta.latest_capture_time)}`
                          : '尚未采集'}
                      </time>
                    </header>
                    <LibraryStats
                      answerCount={meta.answer_count}
                      citedAnswerCount={meta.cited_answer_count}
                      citationCount={meta.citation_count}
                      mentionedAnswerCount={meta.mentioned_answer_count}
                    />
                    <div
                      className="geo-answer-library__question-preview"
                      aria-label="下一层具体问题"
                    >
                      {meta.questions.map((question) => (
                        <span key={question.question_id}>
                          <b>{question.variant_label}</b>
                          <em>{question.text}</em>
                          <small>{question.answer_count} 条</small>
                        </span>
                      ))}
                    </div>
                    <LibraryDimensions
                      models={meta.models}
                      regions={meta.regions}
                      modes={meta.modes}
                      compact
                    />
                  </div>
                  <button type="button" onClick={() => openMeta(meta)}>
                    进入 {meta.question_count} 条问题 <span aria-hidden="true">→</span>
                  </button>
                </article>
              ))}
            </div>
          ) : null}
          {rootResult ? (
            <footer className="geo-answer-library__root-footer">
              <label>
                每页
                <select
                  value={rootLimit}
                  disabled={rootState === 'loading'}
                  onChange={(event) => {
                    setRootLimit(
                      Number(event.currentTarget.value) as (typeof libraryRootLimits)[number],
                    );
                    setRootOffset(0);
                  }}
                >
                  {libraryRootLimits.map((value) => (
                    <option value={value} key={value}>
                      {value} 组
                    </option>
                  ))}
                </select>
              </label>
              <LibraryPager
                total={rootResult.page.total}
                offset={rootResult.page.offset}
                limit={rootResult.page.limit}
                hasMore={rootResult.page.has_more}
                loading={rootState === 'loading'}
                label="关键词分页"
                onOffset={setRootOffset}
              />
              <small>共 {totalPages} 页；显示框高度受限，不会一次铺开全部目录。</small>
            </footer>
          ) : null}
        </>
      ) : null}

      {layer === 'questions' && selectedMeta ? (
        <section className="geo-answer-library__level">
          <header className="geo-answer-library__level-head">
            <div>
              <span>LEVEL 2 · QUERY GROUP</span>
              <h3>{selectedMeta.label}</h3>
              <p>本组保留客户敲定的原问题和三个变体；选择后才读取该问题的采集运行。</p>
            </div>
            <button type="button" onClick={goRoot}>
              ← 返回关键词目录
            </button>
          </header>
          {metaState === 'loading' ? <LibraryLoading label="正在读取四条具体问题" /> : null}
          {metaState === 'failed' || metaState === 'forbidden' ? (
            <LibraryFailure
              state={metaState}
              title="具体问题读取失败"
              onRetry={() => setMetaRetry((value) => value + 1)}
            />
          ) : null}
          {metaResult ? (
            <>
              <LibraryStats
                answerCount={metaResult.answer_count}
                citedAnswerCount={metaResult.cited_answer_count}
                citationCount={metaResult.citation_count}
                mentionedAnswerCount={metaResult.mentioned_answer_count}
              />
              <div className="geo-answer-library__question-grid">
                {metaResult.questions.map((question) => (
                  <article key={question.question_id}>
                    <header>
                      <span>{question.variant_label}</span>
                      <b>问题 {String(question.ordinal).padStart(2, '0')}</b>
                    </header>
                    <h4>{question.text}</h4>
                    <LibraryStats
                      answerCount={question.answer_count}
                      citedAnswerCount={question.cited_answer_count}
                      citationCount={question.citation_count}
                      mentionedAnswerCount={question.mentioned_answer_count}
                    />
                    <LibraryDimensions
                      models={question.models}
                      regions={question.regions}
                      modes={question.modes}
                      compact
                    />
                    <button type="button" onClick={() => openQuestion(question)}>
                      选择采集条件 <span aria-hidden="true">→</span>
                    </button>
                  </article>
                ))}
              </div>
            </>
          ) : null}
        </section>
      ) : null}

      {layer === 'runs' && selectedQuestion ? (
        <section className="geo-answer-library__level">
          <header className="geo-answer-library__level-head">
            <div>
              <span>LEVEL 3 · ANSWER RUNS</span>
              <h3>{selectedQuestion.text}</h3>
              <p>这一层只显示平台、地域、遍次、时间与模式等摘要，不传输答案正文。</p>
            </div>
            <button type="button" onClick={goMeta}>
              ← 返回四条问题
            </button>
          </header>
          <div className="geo-answer-library__run-toolbar" aria-label="采集条件筛选">
            <label>
              平台
              <select
                value={runModel}
                onChange={(event) => {
                  setRunModel(event.currentTarget.value);
                  setRunOffset(0);
                }}
              >
                <option value="all">全部平台</option>
                {selectedQuestion.models.map((item) => (
                  <option key={item.label} value={item.label}>
                    {item.label} ({item.answer_count})
                  </option>
                ))}
              </select>
            </label>
            <label>
              地域
              <select
                value={runRegion}
                onChange={(event) => {
                  setRunRegion(event.currentTarget.value);
                  setRunOffset(0);
                }}
              >
                <option value="all">全部地域</option>
                {selectedQuestion.regions.map((item) => (
                  <option key={item.label} value={item.label}>
                    {item.label} ({item.answer_count})
                  </option>
                ))}
              </select>
            </label>
            <label>
              模式
              <select
                value={runMode}
                onChange={(event) => {
                  setRunMode(event.currentTarget.value);
                  setRunOffset(0);
                }}
              >
                <option value="all">全部模式</option>
                {selectedQuestion.modes.map((item) => (
                  <option key={item.label} value={item.label}>
                    {item.label} ({item.answer_count})
                  </option>
                ))}
              </select>
            </label>
            <label>
              每页
              <select
                value={runLimit}
                onChange={(event) => {
                  setRunLimit(
                    Number(event.currentTarget.value) as (typeof libraryRunLimits)[number],
                  );
                  setRunOffset(0);
                }}
              >
                {libraryRunLimits.map((value) => (
                  <option value={value} key={value}>
                    {value} 条
                  </option>
                ))}
              </select>
            </label>
          </div>
          {runsState === 'loading' ? <LibraryLoading label="正在读取采集条件与统计" /> : null}
          {runsState === 'failed' || runsState === 'forbidden' ? (
            <LibraryFailure
              state={runsState}
              title="采集答案列表读取失败"
              onRetry={() => setRunsRetry((value) => value + 1)}
            />
          ) : null}
          {runsResult && runsResult.data.length === 0 && runsState !== 'loading' ? (
            <div className="geo-answer-library__empty" role="status">
              <strong>当前条件下没有采集回答</strong>
              <span>可以切换平台、地域或模式。</span>
            </div>
          ) : null}
          {runsResult && runsResult.data.length > 0 ? (
            <div
              className="geo-answer-library__runs"
              data-loading={runsState === 'loading' || undefined}
            >
              {runsResult.data.map((run) => {
                const sentiment = sentimentPresentation[run.sentiment ?? 'unknown'];
                return (
                  <article key={run.answer_pub_id}>
                    <div className="geo-answer-library__monogram" aria-hidden="true">
                      {platformMonogram(run.model)}
                    </div>
                    <div className="geo-answer-library__run-copy">
                      <header>
                        <h4>{run.model}</h4>
                        <time dateTime={run.capture_time}>
                          {formatCaptureTime(run.capture_time)}
                        </time>
                      </header>
                      <div>
                        <span>{run.region}</span>
                        <span>{run.mode}</span>
                        <span>第 {run.repeat_index} 遍</span>
                        <span>{run.analysis_state === 'pending' ? '分析中' : sentiment.label}</span>
                        <span>{run.citation_count} 条引用</span>
                      </div>
                    </div>
                    <button type="button" onClick={() => setSelectedRun(run)}>
                      查看完整答案 <span aria-hidden="true">→</span>
                    </button>
                  </article>
                );
              })}
            </div>
          ) : null}
          {runsResult ? (
            <LibraryPager
              total={runsResult.page.total}
              offset={runsResult.page.offset}
              limit={runsResult.page.limit}
              hasMore={runsResult.page.has_more}
              loading={runsState === 'loading'}
              label="采集答案分页"
              onOffset={setRunOffset}
            />
          ) : null}
        </section>
      ) : null}

      {layer === 'answer' && selectedRun ? (
        <section className="geo-answer-library__level">
          <header className="geo-answer-library__level-head">
            <div>
              <span>LEVEL 4 · ANSWER DETAIL</span>
              <h3>
                第 {selectedRun.repeat_index} 遍 · {selectedRun.model} · {selectedRun.region} ·{' '}
                {selectedRun.mode}
              </h3>
              <p>只有进入本层后，浏览器才会按答案 ID 请求完整正文和引用证据。</p>
            </div>
            <button type="button" onClick={goQuestion}>
              ← 返回采集答案
            </button>
          </header>
          {answerState === 'loading' ? <LibraryLoading label="正在按答案 ID 读取正文" /> : null}
          {answerState === 'failed' || answerState === 'forbidden' ? (
            <LibraryFailure
              state={answerState}
              title="完整答案读取失败"
              onRetry={() => setAnswerRetry((value) => value + 1)}
            />
          ) : null}
          {answerResult ? (
            <LibraryAnswerView
              brandName={brandName}
              answer={answerResult}
              relationState={relationState}
              detail={detail}
              {...(loadEvidenceImage ? { loadEvidenceImage } : {})}
            />
          ) : null}
        </section>
      ) : null}
    </section>
  );
}
