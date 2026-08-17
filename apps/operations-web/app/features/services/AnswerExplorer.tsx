import { useCallback, useEffect, useState } from 'react';
import { allowsFixtureIdentityHeaders, type BrowserBuildIdentityEnv } from '@geo/api-client';
import { EvidenceImageFrame, type EvidenceAnchor } from '@geo/evidence-viewer';
import {
  executionApi,
  type AnswerRelations,
  type AnswerRow,
  type TaskTrace,
} from '../execution/api';
import type { SessionContext } from './api';

const API_BASE =
  (import.meta as ImportMeta & { env?: { VITE_GEO_API_BASE?: string } }).env?.VITE_GEO_API_BASE ??
  (typeof window === 'undefined' ? '' : window.location.origin);

// ── 纯逻辑（AnswerExplorer.test.tsx 覆盖）──

export const PLATFORM_DISPLAY_NAMES: Record<string, string> = {
  doubao: '豆包',
  deepseek: 'DeepSeek',
  yiyan: '文心一言',
  tongyi: '通义千问',
  yuanbao: '腾讯元宝',
};

export function platformDisplayName(model: string): string {
  return PLATFORM_DISPLAY_NAMES[model] ?? model;
}

export function truncateText(text: string, max = 60): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

export function formatByteSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** trace 端点 404 三码 = 中性空态（该平台不产出思考链证据或证据未登记），不是错误。 */
export const TRACE_UNAVAILABLE_CODES: readonly string[] = [
  'sse_evidence_missing',
  'sse_blob_missing',
  'task_not_found',
];

export function isTraceUnavailable(code: string): boolean {
  return TRACE_UNAVAILABLE_CODES.includes(code);
}

export const IMAGE_EVIDENCE_KINDS: readonly string[] = [
  'answer_screenshot',
  'share_image',
  'source_screenshot',
];

export function isImageEvidence(asset: { kind: string; mime_type: string }): boolean {
  return IMAGE_EVIDENCE_KINDS.includes(asset.kind) || asset.mime_type.startsWith('image/');
}

export function mimeExtension(mimeType: string): string {
  if (mimeType === 'image/png') return 'png';
  if (mimeType === 'image/jpeg') return 'jpg';
  if (mimeType.startsWith('image/')) return mimeType.slice('image/'.length);
  if (mimeType === 'application/json' || mimeType === 'application/har+json') return 'json';
  if (mimeType.startsWith('text/')) return 'txt';
  return 'bin';
}

/** 证据资产下载按钮文案（kind 词表 = collection.py `_EVIDENCE_KINDS`）。 */
export function evidenceDownloadLabel(kind: string): string {
  if (kind === 'sse') return '下载结构化 trace JSON';
  if (kind === 'sse_raw') return '下载原始 SSE 响应';
  if (kind === 'har') return '下载 HAR 流量记录 JSON';
  if (kind === 'share_link') return '下载 JSON';
  return '下载';
}

export type AnswerEvidence = AnswerRelations['evidence'][number];

export type AnswerEvidencePurposeGroups = {
  officialShareImages: AnswerEvidence[];
  officialShareLinks: AnswerEvidence[];
  runtimeAnswerScreenshots: AnswerEvidence[];
  aiOpenedPagePreviews: AnswerEvidence[];
  brandMentionScreenshots: AnswerEvidence[];
  sourceReviewScreenshots: AnswerEvidence[];
  technicalRecords: AnswerEvidence[];
  other: AnswerEvidence[];
};

function boundedEvidenceAnchor(asset: AnswerEvidence): EvidenceAnchor | undefined {
  if (
    asset.relation_type !== 'brand_mention_source_snapshot' ||
    asset.kind !== 'source_screenshot' ||
    asset.mime_type !== 'image/png' ||
    asset.byte_size < 128
  ) {
    return undefined;
  }
  for (const candidate of asset.anchors) {
    const bbox = candidate.bbox;
    if (!bbox || typeof bbox !== 'object' || Array.isArray(bbox)) continue;
    const record = bbox as Record<string, unknown>;
    const values = [record.x, record.y, record.width, record.height];
    const imageSize = [record.image_width, record.image_height];
    if (
      !values.every(
        (value) =>
          typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1_000_000,
      ) ||
      !imageSize.every(
        (value) =>
          typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= 1_000_000,
      ) ||
      values[2] === 0 ||
      values[3] === 0 ||
      Number(values[0]) + Number(values[2]) > Number(imageSize[0]) ||
      Number(values[1]) + Number(values[3]) > Number(imageSize[1])
    ) {
      continue;
    }
    return {
      assetId: asset.pub_id,
      ...(typeof candidate.text_start === 'number' ? { textStart: candidate.text_start } : {}),
      ...(typeof candidate.text_end === 'number' ? { textEnd: candidate.text_end } : {}),
      bbox: values as [number, number, number, number],
    };
  }
  return undefined;
}

/**
 * Groups by evidence purpose instead of storage kind. In particular, a legacy
 * cited_source_snapshot is a post-answer collector review, never proof that the
 * AI opened the page or that the target brand occurs on it.
 */
export function groupAnswerEvidenceByPurpose(
  evidence: AnswerEvidence[],
): AnswerEvidencePurposeGroups {
  const groups: AnswerEvidencePurposeGroups = {
    officialShareImages: [],
    officialShareLinks: [],
    runtimeAnswerScreenshots: [],
    aiOpenedPagePreviews: [],
    brandMentionScreenshots: [],
    sourceReviewScreenshots: [],
    technicalRecords: [],
    other: [],
  };
  for (const asset of evidence) {
    if (asset.kind === 'share_image' && asset.relation_type === 'official_share_image') {
      groups.officialShareImages.push(asset);
    } else if (asset.kind === 'share_link' && asset.relation_type === 'official_share_link') {
      groups.officialShareLinks.push(asset);
    } else if (asset.kind === 'answer_screenshot') {
      groups.runtimeAnswerScreenshots.push(asset);
    } else if (
      asset.kind === 'source_screenshot' &&
      asset.relation_type === 'ai_opened_source_preview'
    ) {
      groups.aiOpenedPagePreviews.push(asset);
    } else if (boundedEvidenceAnchor(asset)) {
      groups.brandMentionScreenshots.push(asset);
    } else if (asset.kind === 'source_screenshot') {
      groups.sourceReviewScreenshots.push(asset);
    } else if (['sse', 'sse_raw', 'har'].includes(asset.kind)) {
      groups.technicalRecords.push(asset);
    } else {
      groups.other.push(asset);
    }
  }
  return groups;
}

export type AiOpenedPage = {
  ordinal: number;
  title: string;
  url: string;
  site: string | null;
  summary: string;
};

export type AiOpenedPageProjection = {
  observed: boolean;
  pages: AiOpenedPage[];
  invalid: boolean;
};

const safePublicUrl = (value: unknown): string | null => {
  if (typeof value !== 'string' || value.length === 0 || value.length > 2_000) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? url.toString()
      : null;
  } catch {
    return null;
  }
};

/** Strictly projects platform-emitted TOOL_OPEN facts; citations/search hits are never fallback data. */
export function projectAiOpenedPages(trace: TaskTrace): AiOpenedPageProjection {
  const traceRecord = trace as unknown as Record<string, unknown>;
  const observed = traceRecord.opened_pages_observed === true;
  const raw = traceRecord.opened_pages;
  if (!observed) return { observed: false, pages: [], invalid: false };
  if (!Array.isArray(raw)) return { observed: true, pages: [], invalid: true };
  let invalid = false;
  const pages = raw.slice(0, 100).flatMap<AiOpenedPage>((candidate) => {
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) {
      invalid = true;
      return [];
    }
    const value = candidate as Record<string, unknown>;
    const url = safePublicUrl(value.url);
    const ordinal = value.rank;
    const title = value.title;
    const site = value.site;
    const summary = value.summary;
    const status = value.status;
    if (
      !url ||
      typeof ordinal !== 'number' ||
      !Number.isSafeInteger(ordinal) ||
      ordinal < 1 ||
      typeof title !== 'string' ||
      title.length === 0 ||
      title.length > 300 ||
      (site !== null && site !== undefined && (typeof site !== 'string' || site.length > 200)) ||
      typeof summary !== 'string' ||
      summary.length > 2_000 ||
      status !== 'opened_page'
    ) {
      invalid = true;
      return [];
    }
    return [{ ordinal, title, url, site: typeof site === 'string' ? site : null, summary }];
  });
  if (raw.length > 100) invalid = true;
  return { observed, pages, invalid };
}

/** 证据资产按 kind 分组，保持后端返回的先后顺序。 */
export function groupEvidenceByKind(evidence: AnswerEvidence[]): [string, AnswerEvidence[]][] {
  const groups = new Map<string, AnswerEvidence[]>();
  for (const asset of evidence) {
    const bucket = groups.get(asset.kind);
    if (bucket) bucket.push(asset);
    else groups.set(asset.kind, [asset]);
  }
  return [...groups.entries()];
}

// ── 证据字节流（简化版：fetch → blob，无 sha256 校验）──

/** 与 services/api.ts 同一不变量：生产包不发送浏览器身份三头（cookie 鉴权）。 */
function evidenceRequestHeaders(session: SessionContext): Record<string, string> {
  const env = (import.meta as ImportMeta & { env?: BrowserBuildIdentityEnv }).env;
  if (!allowsFixtureIdentityHeaders(env)) return {};
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(session.headers)) {
    if (typeof value === 'string') headers[key] = value;
  }
  return headers;
}

async function fetchEvidenceBlob(session: SessionContext, evidencePubId: string): Promise<Blob> {
  const response = await fetch(
    `${API_BASE}/api/v2/evidence/assets/${encodeURIComponent(evidencePubId)}/content`,
    {
      headers: evidenceRequestHeaders(session),
      cache: 'no-store',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
    },
  );
  if (!response.ok) throw new Error(`http_${response.status}`);
  return response.blob();
}

// ── 问答列表面板 ──

type Props = {
  session: SessionContext;
  projectPubId: string;
  runPubId: string;
};

export function AnswerRowsTable({
  answers,
  onSelect,
  ariaLabel,
}: {
  answers: AnswerRow[];
  onSelect: (answer: AnswerRow) => void;
  ariaLabel?: string;
}) {
  return (
    <div className="table-scroll">
      <table aria-label={ariaLabel}>
        <thead>
          <tr>
            <th>采集时间</th>
            <th>问题</th>
            <th>平台</th>
            <th>模式</th>
            <th>地域</th>
            <th>提及</th>
            <th>排名</th>
            <th>引用</th>
          </tr>
        </thead>
        <tbody>
          {answers.map((answer) => (
            <tr
              key={answer.pub_id}
              className="answer-row"
              tabIndex={0}
              onClick={() => onSelect(answer)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelect(answer);
                }
              }}
            >
              <td data-label="采集时间">{new Date(answer.capture_time).toLocaleString('zh-CN')}</td>
              <td data-label="问题">{truncateText(answer.query_text ?? '')}</td>
              <td data-label="平台">{platformDisplayName(answer.model)}</td>
              <td data-label="模式">{answer.mode}</td>
              <td data-label="地域">{answer.region}</td>
              <td data-label="提及">
                {answer.mentioned === null ? '—' : answer.mentioned ? '提及' : '未提及'}
              </td>
              <td data-label="排名">{answer.rank ?? '—'}</td>
              <td data-label="引用">{answer.citation_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AnswerExplorer({ session, projectPubId, runPubId }: Props) {
  const [rows, setRows] = useState<AnswerRow[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [loadingMore, setLoadingMore] = useState(false);
  const [selected, setSelected] = useState<AnswerRow | null>(null);

  const load = useCallback(
    async (cursor?: string) => {
      const page = await executionApi.answers(session, {
        projectPubId,
        runPubId,
        limit: 50,
        ...(cursor ? { cursor } : {}),
      });
      return page;
    },
    [session, projectPubId, runPubId],
  );

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    load()
      .then((page) => {
        if (cancelled) return;
        setRows(page.data);
        setNextCursor(typeof page.page.next_cursor === 'string' ? page.page.next_cursor : null);
        setState('ready');
      })
      .catch(() => {
        if (!cancelled) setState('failed');
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  async function loadMore() {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const page = await load(nextCursor);
      setRows((current) => [...current, ...page.data]);
      setNextCursor(typeof page.page.next_cursor === 'string' ? page.page.next_cursor : null);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="answer-explorer">
      {state === 'loading' ? (
        <p className="empty">正在加载该 run 的问答…</p>
      ) : state === 'failed' ? (
        <p className="empty">问答列表加载失败。</p>
      ) : rows.length === 0 ? (
        <p className="empty">该 run 尚无采集问答。答案扇出到 analytics 后才会出现在这里。</p>
      ) : (
        <>
          <AnswerRowsTable answers={rows} onSelect={setSelected} />
          {nextCursor ? (
            <p className="answer-explorer-more">
              <button disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? '加载中…' : '加载更多'}
              </button>
            </p>
          ) : null}
        </>
      )}
      {selected ? (
        <AnswerDetail session={session} answer={selected} onClose={() => setSelected(null)} />
      ) : null}
    </div>
  );
}

// ── 详情对话框 ──

type TraceState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: TaskTrace }
  | { kind: 'unavailable' }
  | { kind: 'failed' };

type RelationsState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: AnswerRelations }
  | { kind: 'failed' };

export function AnswerDetail({
  session,
  answer,
  onClose,
}: {
  session: SessionContext;
  answer: AnswerRow;
  onClose: () => void;
}) {
  const [trace, setTrace] = useState<TraceState>({ kind: 'loading' });
  const [relations, setRelations] = useState<RelationsState>({ kind: 'loading' });
  const [assetError, setAssetError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    executionApi
      .taskTrace(session, answer.pub_id)
      .then((data) => {
        if (!cancelled) setTrace({ kind: 'ready', data });
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        const code = cause instanceof Error ? cause.message : '';
        setTrace(isTraceUnavailable(code) ? { kind: 'unavailable' } : { kind: 'failed' });
      });
    executionApi
      .answerRelations(session, answer.pub_id)
      .then((data) => {
        if (!cancelled) setRelations({ kind: 'ready', data });
      })
      .catch(() => {
        if (!cancelled) setRelations({ kind: 'failed' });
      });
    return () => {
      cancelled = true;
    };
  }, [session, answer.pub_id]);

  async function downloadAsset(asset: AnswerEvidence) {
    setAssetError(null);
    try {
      const blob = await fetchEvidenceBlob(session, asset.pub_id);
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `${asset.pub_id}.${mimeExtension(asset.mime_type)}`;
        anchor.click();
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch {
      setAssetError(asset.pub_id);
    }
  }

  return (
    <div
      className="answer-detail-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="answer-detail" role="dialog" aria-modal="true" aria-label="问答详情">
        <div className="answer-detail-head">
          <h3>{answer.query_text ?? '（无问题文本）'}</h3>
          <button onClick={onClose}>关闭</button>
        </div>

        <section className="answer-detail-section">
          <h4>答案全文</h4>
          <p className="answer-detail-meta">
            {platformDisplayName(answer.model)} · {answer.mode} · {answer.region} · 采集于{' '}
            {new Date(answer.capture_time).toLocaleString('zh-CN')}
          </p>
          <p className="answer-detail-meta">
            <span className={`status ${answer.eligible ? 'ok' : 'bad'}`}>
              {answer.eligible ? 'eligible' : 'ineligible'}
            </span>{' '}
            <span className={`status ${answer.degraded ? 'warn' : 'ok'}`}>
              {answer.degraded ? 'degraded' : 'not-degraded'}
            </span>
          </p>
          <pre className="answer-detail-text">{answer.response_text}</pre>
          <p className="answer-detail-meta">
            answer：<code>{answer.pub_id}</code>
            {answer.run_pub_id ? (
              <>
                {' '}
                · run：<code>{answer.run_pub_id}</code>
              </>
            ) : null}
          </p>
        </section>

        <section className="answer-detail-section">
          <h4>思考过程与检索回放</h4>
          {trace.kind === 'loading' ? (
            <p className="empty">正在加载回放…</p>
          ) : trace.kind === 'unavailable' ? (
            <p className="answer-detail-neutral">该平台不产出思考链证据，或证据未登记。</p>
          ) : trace.kind === 'failed' ? (
            <p className="answer-detail-neutral">回放加载失败，请关闭后重试。</p>
          ) : (
            <TraceReplay trace={trace.data} />
          )}
        </section>

        <section className="answer-detail-section" aria-label="答案组织引用">
          <h4>答案组织引用（不等于品牌提及）</h4>
          <p className="answer-detail-meta evidence-definition">
            下表只表示 AI
            答案返回或组织时关联的引用。一个网页出现在此表，不代表页面提及目标品牌，也不代表已通过品牌原文取证。
          </p>
          {relations.kind === 'loading' ? (
            <p className="empty">正在加载引用…</p>
          ) : relations.kind === 'failed' ? (
            <p className="answer-detail-neutral">引用加载失败。</p>
          ) : relations.data.answer_citations.length === 0 ? (
            <p className="answer-detail-neutral">该答案未抽取到引用。</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>标题</th>
                    <th>来源</th>
                    <th>AI 返回的引用片段</th>
                  </tr>
                </thead>
                <tbody>
                  {relations.data.answer_citations.map((citation) => (
                    <tr key={citation.pub_id}>
                      <td data-label="#">{citation.ordinal}</td>
                      <td data-label="标题">{citation.title ?? '—'}</td>
                      <td data-label="来源">
                        <a href={citation.canonical_url} target="_blank" rel="noreferrer">
                          {citation.host}
                        </a>
                      </td>
                      <td data-label="被引片段">{truncateText(citation.cited_text ?? '', 80)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="answer-detail-section answer-evidence-assets">
          <h4>证据资产（按用途分区）</h4>
          {relations.kind === 'loading' ? (
            <p className="empty">正在加载证据资产…</p>
          ) : relations.kind === 'failed' ? (
            <p className="answer-detail-neutral">证据资产加载失败。</p>
          ) : relations.data.evidence.length === 0 ? (
            <p className="answer-detail-neutral">该答案未登记证据资产。</p>
          ) : (
            <AnswerEvidencePurposeSections
              session={session}
              evidence={relations.data.evidence}
              brandMentionEvidence={relations.data.brand_mention_evidence}
              openedSourcePreviews={relations.data.opened_source_previews}
              assetError={assetError}
              onDownload={downloadAsset}
            />
          )}
        </section>
      </div>
    </div>
  );
}

function EvidenceAssetCard({
  session,
  asset,
  imageMode,
  assetError,
  onDownload,
}: {
  session: SessionContext;
  asset: AnswerEvidence;
  imageMode?: 'plain' | 'page-preview' | 'brand-mention';
  assetError: string | null;
  onDownload: (asset: AnswerEvidence) => Promise<void>;
}) {
  const sourceUrl = safePublicUrl(asset.source_url);
  return (
    <article className="evidence-asset">
      <p className="answer-detail-meta">
        <code>{asset.pub_id}</code> · {asset.kind} · {asset.mime_type} ·{' '}
        {formatByteSize(asset.byte_size)} · sha256 {asset.sha256.slice(0, 12)}…
      </p>
      {imageMode && isImageEvidence(asset) ? (
        <EvidenceImage session={session} asset={asset} mode={imageMode} />
      ) : (
        <p className="evidence-asset-actions">
          {asset.kind === 'share_link' && sourceUrl ? (
            <a href={sourceUrl} target="_blank" rel="noreferrer noopener">
              打开官方分享链接
            </a>
          ) : sourceUrl ? (
            <a href={sourceUrl} target="_blank" rel="noreferrer noopener">
              打开来源页
            </a>
          ) : null}
          <button onClick={() => void onDownload(asset)}>
            {evidenceDownloadLabel(asset.kind)}
          </button>
          {assetError === asset.pub_id ? (
            <span className="answer-detail-neutral">下载失败</span>
          ) : null}
        </p>
      )}
    </article>
  );
}

function AnswerEvidencePurposeSections({
  session,
  evidence,
  brandMentionEvidence,
  openedSourcePreviews,
  assetError,
  onDownload,
}: {
  session: SessionContext;
  evidence: AnswerEvidence[];
  brandMentionEvidence: AnswerEvidence[];
  openedSourcePreviews: AnswerEvidence[];
  assetError: string | null;
  onDownload: (asset: AnswerEvidence) => Promise<void>;
}) {
  const groups = groupAnswerEvidenceByPurpose(evidence);
  // The server emits these two fail-closed semantic collections explicitly;
  // never promote a compatibility evidence row based on its display text.
  groups.brandMentionScreenshots = brandMentionEvidence;
  groups.aiOpenedPagePreviews = openedSourcePreviews;
  const officialLinks = groups.officialShareLinks.filter((asset) =>
    safePublicUrl(asset.source_url),
  );
  const missingShareParts = [
    ...(groups.officialShareImages.length ? [] : ['官方分享图片']),
    ...(officialLinks.length ? [] : ['官方分享链接']),
  ];
  return (
    <div className="evidence-purpose-sections">
      <section className="evidence-purpose" aria-label="官方分享交付">
        <h5>官方分享交付</h5>
        {missingShareParts.length ? (
          <p className="evidence-purpose-warning" role="status">
            分享交付不完整：本次缺少{missingShareParts.join('、')}
            。运行时回答截图不能替代官方分享制品。
          </p>
        ) : (
          <p className="evidence-purpose-ok">已同时保存官方分享图片和分享链接。</p>
        )}
        {[...groups.officialShareImages, ...groups.officialShareLinks].map((asset) => (
          <EvidenceAssetCard
            key={asset.pub_id}
            session={session}
            asset={asset}
            {...(asset.kind === 'share_image' ? { imageMode: 'plain' as const } : {})}
            assetError={assetError}
            onDownload={onDownload}
          />
        ))}
      </section>

      <section className="evidence-purpose" aria-label="运行时回答截图">
        <h5>运行时回答截图</h5>
        <p className="answer-detail-meta">
          用于证明采集时的问答界面状态；它不是官方分享图，也不是信源页品牌提及证据。
        </p>
        {groups.runtimeAnswerScreenshots.length ? (
          groups.runtimeAnswerScreenshots.map((asset) => (
            <EvidenceAssetCard
              key={asset.pub_id}
              session={session}
              asset={asset}
              imageMode="plain"
              assetError={assetError}
              onDownload={onDownload}
            />
          ))
        ) : (
          <p className="answer-detail-neutral">本次未登记运行时回答截图。</p>
        )}
      </section>

      <section className="evidence-purpose" aria-label="AI 实际打开页面概览">
        <h5>AI 实际打开页面概览</h5>
        <p className="answer-detail-meta">
          以下图片由采集器依据平台显式 <code>TOOL_OPEN</code>{' '}
          URL，在答案完成后重新打开页面采集；只用于查看页面大致情况，不是 AI
          浏览当时的像素画面。页面加载失败时，概览数量可能少于上方实际打开页数。
        </p>
        {groups.aiOpenedPagePreviews.length ? (
          groups.aiOpenedPagePreviews.map((asset) => (
            <EvidenceAssetCard
              key={asset.pub_id}
              session={session}
              asset={asset}
              imageMode="page-preview"
              assetError={assetError}
              onDownload={onDownload}
            />
          ))
        ) : (
          <p className="answer-detail-neutral">
            本次没有 <code>ai_opened_source_preview</code>{' '}
            图像资产。上方“实际打开”列表可回放平台返回的页面元数据，但元数据不会被伪装成页面截图。
          </p>
        )}
      </section>

      <section className="evidence-purpose" aria-label="品牌提及原文证据">
        <h5>品牌提及原文证据</h5>
        <p className="answer-detail-meta">
          只接受真实信源页截图、目标品牌原文逐字命中与页面 bbox 三者绑定的资产；红框按原图坐标缩放。
        </p>
        {groups.brandMentionScreenshots.length ? (
          groups.brandMentionScreenshots.map((asset) => (
            <EvidenceAssetCard
              key={asset.pub_id}
              session={session}
              asset={asset}
              imageMode="brand-mention"
              assetError={assetError}
              onDownload={onDownload}
            />
          ))
        ) : (
          <p className="answer-detail-neutral">
            本次没有通过“真实网页 + 品牌原文 +
            bbox”校验的证据。检索命中或答案引用不会自动算作品牌提及。
          </p>
        )}
      </section>

      {groups.sourceReviewScreenshots.length ? (
        <section
          className="evidence-purpose evidence-purpose-quarantine"
          aria-label="采集后信源复核截图"
        >
          <h5>采集后信源复核截图（已隔离）</h5>
          <p className="evidence-purpose-warning">
            这些旧资产是答案生成后由采集器重新打开的页面，不是 AI
            实际浏览证明；由于不具备可核验的品牌 bbox，不在界面中展示为品牌证据。
          </p>
          {groups.sourceReviewScreenshots.map((asset) => (
            <EvidenceAssetCard
              key={asset.pub_id}
              session={session}
              asset={asset}
              assetError={assetError}
              onDownload={onDownload}
            />
          ))}
        </section>
      ) : null}

      {[...groups.technicalRecords, ...groups.other].length ? (
        <section className="evidence-purpose" aria-label="技术审计记录">
          <h5>技术审计记录</h5>
          {[...groups.technicalRecords, ...groups.other].map((asset) => (
            <EvidenceAssetCard
              key={asset.pub_id}
              session={session}
              asset={asset}
              {...(isImageEvidence(asset) ? { imageMode: 'plain' as const } : {})}
              assetError={assetError}
              onDownload={onDownload}
            />
          ))}
        </section>
      ) : null}
    </div>
  );
}

// ── 思考过程与检索回放 ──

function TraceReplay({ trace }: { trace: TaskTrace }) {
  const opened = projectAiOpenedPages(trace);
  const legacyUnclassified = trace.search_blocks.some((block) =>
    block.results.some((result) => result.status === 'legacy_unclassified'),
  );
  return (
    <div className="trace-replay">
      <p className="answer-detail-meta">
        深度思考：{trace.deep_think_active ? '开' : '关'}
        {trace.thinking_title ? ` · ${trace.thinking_title}` : ''} · 检索命中 {trace.totals.results}{' '}
        页 · AI 实际打开 {opened.observed ? `${opened.pages.length} 页` : '不可追溯'} · 思考段{' '}
        {trace.totals.surfaced_reasoning_steps}
      </p>
      {trace.reasoning.map((step, index) =>
        step.kind === 'surfaced_reasoning' ? (
          <p key={index} className="trace-reasoning">
            {step.text}
          </p>
        ) : (
          <div key={index} className="trace-search-step">
            <p>
              检索步骤：
              {(step.queries ?? []).map((query) => (
                <code key={query}>{query}</code>
              ))}
            </p>
            {step.summary ? <p>{step.summary}</p> : null}
          </div>
        ),
      )}
      <section className="trace-source-section" aria-label="检索命中信源">
        <h5>
          {legacyUnclassified
            ? '旧记录返回信源（无法区分检索与打开）'
            : `检索命中信源（${trace.totals.results} 页候选）`}
        </h5>
        <p className="answer-detail-meta">
          标题或摘要命中只说明页面进入检索候选集；不说明 AI
          打开了页面，也不说明页面内容被组织进答案。
        </p>
        {trace.search_blocks.map((block, index) => (
          <details
            key={index}
            className="trace-search-block"
            open={trace.search_blocks.length === 1}
          >
            <summary>
              检索词：{block.queries.join('、') || '未返回'}（{block.result_count} 条）
            </summary>
            {block.summary ? <p>{block.summary}</p> : null}
            <ol>
              {block.results.map((result, resultIndex) => (
                <li key={`${result.url ?? result.title}-${resultIndex}`}>
                  {result.url ? (
                    <a href={result.url} target="_blank" rel="noreferrer noopener">
                      {result.title}
                    </a>
                  ) : (
                    result.title
                  )}
                  {result.site ? ` · ${result.site}` : ''}
                  {result.summary ? <p>{result.summary}</p> : null}
                </li>
              ))}
            </ol>
          </details>
        ))}
      </section>

      <section className="trace-source-section trace-opened-pages" aria-label="AI 实际打开信源">
        <h5>AI 实际打开信源（{opened.observed ? `${opened.pages.length} 页` : '不可追溯'}）</h5>
        {!opened.observed ? (
          <p className="answer-detail-neutral">
            此记录没有保存平台显式 <code>TOOL_OPEN</code>{' '}
            事件，因此不能从检索结果、答案引用或采集后复核截图反推 AI 实际浏览了哪些页面。
          </p>
        ) : opened.invalid ? (
          <p className="evidence-purpose-warning" role="alert">
            部分页面打开事实未通过 URL 或字段校验；已隐藏无效行，当前列表不声称完整。
          </p>
        ) : opened.pages.length === 0 ? (
          <p className="answer-detail-neutral">平台传输了打开事件分类，本次没有页面打开记录。</p>
        ) : (
          <ol className="trace-opened-list">
            {opened.pages.map((page) => (
              <li key={`${page.ordinal}-${page.url}`}>
                <a href={page.url} target="_blank" rel="noreferrer noopener">
                  {page.title}
                </a>
                {page.site ? ` · ${page.site}` : ''}
                {page.summary ? <p>{page.summary}</p> : null}
              </li>
            ))}
          </ol>
        )}
        <p className="answer-detail-meta">
          “实际打开”仅指平台明确返回的页面打开事件；它仍不等于该页已被答案引用或提及目标品牌。
        </p>
      </section>
      {trace.search_queries.length > 0 ? (
        <p className="answer-detail-meta">
          平台真实检索词：
          {trace.search_queries.map((item) => (
            <code key={item.ordinal}>{item.query}</code>
          ))}
        </p>
      ) : null}
      <p className="answer-detail-meta trace-disclosure">{trace.disclosure}</p>
    </div>
  );
}

// ── 证据图片（fetch → blob URL → img；点击新标签页打开原图）──

function EvidenceImage({
  session,
  asset,
  mode,
}: {
  session: SessionContext;
  asset: AnswerEvidence;
  mode: 'plain' | 'page-preview' | 'brand-mention';
}) {
  const [state, setState] = useState<
    { kind: 'loading' } | { kind: 'ready'; url: string } | { kind: 'failed' }
  >({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setState({ kind: 'loading' });
    fetchEvidenceBlob(session, asset.pub_id)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ kind: 'ready', url: objectUrl });
      })
      .catch(() => {
        if (!cancelled) setState({ kind: 'failed' });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [session, asset.pub_id]);

  if (state.kind === 'loading') return <p className="answer-detail-neutral">图片加载中…</p>;
  if (state.kind === 'failed') return <p className="answer-detail-neutral">证据加载失败</p>;
  const image = (
    <a href={state.url} target="_blank" rel="noreferrer noopener">
      <img className="evidence-image" src={state.url} alt={`${asset.kind} 证据 ${asset.pub_id}`} />
    </a>
  );
  if (mode === 'plain') return image;
  const anchor = mode === 'brand-mention' ? boundedEvidenceAnchor(asset) : undefined;
  return (
    <EvidenceImageFrame
      label={
        mode === 'brand-mention'
          ? `品牌提及信源页证据 ${asset.pub_id}`
          : `AI 实际打开页面概览 ${asset.pub_id}`
      }
      {...(anchor ? { anchor } : {})}
      overlayLabel="目标品牌原文位置"
    >
      {image}
    </EvidenceImageFrame>
  );
}
