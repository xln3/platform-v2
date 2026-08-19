import { Badge, Dialog } from '@geo/design-system';
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
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
  fixturePage?: CustomerAnswerExplorerPage;
};

type LoadState = 'loading' | 'ready' | 'failed';
type DetailState = 'idle' | 'loading' | 'ready' | 'failed';
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
}: {
  row: CustomerAnswerExplorerRow;
  citations: readonly CustomerAnswerCitationDetail[];
}) {
  const markdown = readableFallbackMarkdown(row.response_text, citations);
  return (
    <div className="geo-answer-dossier__fallback" role="region" aria-label="历史采集答案退阶阅读版">
      <div className="geo-answer-dossier__fallback-notice" role="note">
        <strong>退阶说明</strong>
        <span>未保存官方分享链接；以下为采集时保留的答案，不等同于官方实时页。</span>
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
              <div className="geo-answer-dossier__table-scroll">
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
  onSelectRun,
  onClose,
}: {
  brandName: string;
  row: CustomerAnswerExplorerRow;
  runs: readonly CustomerAnswerExplorerRow[];
  detailState: DetailState;
  detail: CustomerAnswerDetail | null;
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

function AnswerGroup({
  brandName,
  groupBy,
  groupIndex,
  label,
  rows,
  onOpen,
}: {
  brandName: string;
  groupBy: CustomerAnswerGroupBy;
  groupIndex: number;
  label: string;
  rows: readonly CustomerAnswerExplorerRow[];
  onOpen: (row: CustomerAnswerExplorerRow) => void;
}) {
  const headingId = `geo-answer-group-${groupBy}-${groupIndex}`;
  const mentionCount = rows.filter((row) => row.mentioned).length;
  const citationCount = rows.reduce((total, row) => total + row.citation_count, 0);

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
          const secondaryDimensions = [
            ...(groupBy === 'platform' ? [] : [{ label: '平台', value: row.model }]),
            ...(groupBy === 'mode' ? [] : [{ label: '模式', value: row.mode }]),
            ...(groupBy === 'region' ? [] : [{ label: '地域', value: row.region }]),
          ];
          return (
            <article className="geo-answer-row" key={row.answer_pub_id}>
              <div className="geo-answer-row__index" aria-hidden="true">
                <span>{String(index + 1).padStart(2, '0')}</span>
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

export function CustomerAnswerExplorer({
  brandName,
  loadPage,
  loadDetail,
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
  const [activeGroupLabel, setActiveGroupLabel] = useState<string | null>(null);
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
  const activeGroupIndex = Math.max(
    0,
    groupedRows.findIndex((group) => group.label === activeGroupLabel),
  );
  const activeGroup = groupedRows[activeGroupIndex];

  useEffect(() => {
    if (groupedRows.length === 0) {
      setActiveGroupLabel(null);
      return;
    }
    if (!groupedRows.some((group) => group.label === activeGroupLabel)) {
      setActiveGroupLabel(groupedRows[0]?.label ?? null);
    }
  }, [activeGroupLabel, groupedRows]);

  return (
    <section className="geo-answer-explorer" aria-labelledby="geo-answer-explorer-title">
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

      {state === 'ready' && rows.length > 0 ? (
        <div className="geo-answer-explorer__classification" aria-label="回答分类方式">
          <div>
            <span>当前页分类</span>
            <strong>
              {dimensionCounts.platform.toLocaleString('zh-CN')} 个平台 ·{' '}
              {dimensionCounts.mode.toLocaleString('zh-CN')} 种模式 ·{' '}
              {dimensionCounts.region.toLocaleString('zh-CN')} 个地域
            </strong>
            <small>{groupByPresentation[groupBy].description}</small>
          </div>
          <div className="geo-answer-explorer__group-switch" role="group" aria-label="选择分组维度">
            {(Object.keys(groupByPresentation) as CustomerAnswerGroupBy[]).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={groupBy === value}
                onClick={() => {
                  setGroupBy(value);
                  setActiveGroupLabel(null);
                }}
              >
                {groupByPresentation[value].label}
              </button>
            ))}
          </div>
        </div>
      ) : null}

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

      {state === 'ready' && rows.length > 0 ? (
        <div className="geo-answer-explorer__group-browser">
          <aside className="geo-answer-explorer__group-directory" aria-label="回答分类导航">
            <header>
              <span>{groupByPresentation[groupBy].groupLabel}</span>
              <strong>{groupedRows.length} 组</strong>
            </header>
            <div>
              {groupedRows.map((group, index) => (
                <button
                  key={`${groupBy}-${group.label}`}
                  type="button"
                  aria-pressed={index === activeGroupIndex}
                  onClick={() => setActiveGroupLabel(group.label)}
                >
                  <span className="geo-answer-explorer__group-monogram" aria-hidden="true">
                    {platformMonogram(group.label)}
                  </span>
                  <span>
                    <strong>{group.label}</strong>
                    <small>{group.rows.length} 条回答</small>
                  </span>
                </button>
              ))}
            </div>
          </aside>
          <div className="geo-answer-explorer__group-stage">
            {activeGroup ? (
              <AnswerGroup
                key={`${groupBy}-${activeGroup.label}`}
                brandName={brandName}
                groupBy={groupBy}
                groupIndex={activeGroupIndex}
                label={activeGroup.label}
                rows={activeGroup.rows}
                onOpen={openAnswer}
              />
            ) : null}
            <nav className="geo-answer-explorer__group-pager" aria-label="回答分类分页">
              <button
                type="button"
                disabled={activeGroupIndex === 0}
                onClick={() =>
                  setActiveGroupLabel(groupedRows[activeGroupIndex - 1]?.label ?? null)
                }
              >
                ← 上一组
              </button>
              <span>
                第 {activeGroupIndex + 1} / {groupedRows.length} 组
              </span>
              <button
                type="button"
                disabled={activeGroupIndex >= groupedRows.length - 1}
                onClick={() =>
                  setActiveGroupLabel(groupedRows[activeGroupIndex + 1]?.label ?? null)
                }
              >
                下一组 →
              </button>
            </nav>
          </div>
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

      {selectedAnswer ? (
        <AnswerDossier
          key={selectedAnswer.answer_pub_id}
          brandName={brandName}
          row={selectedAnswer}
          runs={rows}
          detailState={detailState}
          detail={detail}
          onSelectRun={openAnswer}
          onClose={closeAnswer}
        />
      ) : null}
    </section>
  );
}
