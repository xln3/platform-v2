import { useCallback, useEffect, useState } from 'react';
import {
  allowsFixtureIdentityHeaders,
  type BrowserBuildIdentityEnv,
} from '@geo/api-client';
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
  if (mimeType === 'application/json') return 'json';
  if (mimeType.startsWith('text/')) return 'txt';
  return 'bin';
}

export type AnswerEvidence = AnswerRelations['evidence'][number];

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
          <div className="table-scroll">
            <table>
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
                {rows.map((answer) => (
                  <tr
                    key={answer.pub_id}
                    className="answer-row"
                    onClick={() => setSelected(answer)}
                  >
                    <td data-label="采集时间">
                      {new Date(answer.capture_time).toLocaleString('zh-CN')}
                    </td>
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

function AnswerDetail({
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

        <section className="answer-detail-section">
          <h4>引用</h4>
          {relations.kind === 'loading' ? (
            <p className="empty">正在加载引用…</p>
          ) : relations.kind === 'failed' ? (
            <p className="answer-detail-neutral">引用加载失败。</p>
          ) : relations.data.citations.length === 0 ? (
            <p className="answer-detail-neutral">该答案未抽取到引用。</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>标题</th>
                    <th>来源</th>
                    <th>被引片段</th>
                  </tr>
                </thead>
                <tbody>
                  {relations.data.citations.map((citation) => (
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

        <section className="answer-detail-section">
          <h4>证据资产</h4>
          {relations.kind === 'loading' ? (
            <p className="empty">正在加载证据资产…</p>
          ) : relations.kind === 'failed' ? (
            <p className="answer-detail-neutral">证据资产加载失败。</p>
          ) : relations.data.evidence.length === 0 ? (
            <p className="answer-detail-neutral">该答案未登记证据资产。</p>
          ) : (
            groupEvidenceByKind(relations.data.evidence).map(([kind, assets]) => (
              <div key={kind} className="evidence-group">
                <h5>{kind}</h5>
                {assets.map((asset) => (
                  <div key={asset.pub_id} className="evidence-asset">
                    <p className="answer-detail-meta">
                      <code>{asset.pub_id}</code> · {asset.mime_type} ·{' '}
                      {formatByteSize(asset.byte_size)} · sha256 {asset.sha256.slice(0, 12)}…
                    </p>
                    {isImageEvidence(asset) ? (
                      <EvidenceImage session={session} asset={asset} />
                    ) : (
                      <p className="evidence-asset-actions">
                        {asset.kind === 'share_link' && asset.source_url ? (
                          <a href={asset.source_url} target="_blank" rel="noreferrer">
                            打开官方分享链接
                          </a>
                        ) : null}
                        <button onClick={() => void downloadAsset(asset)}>
                          {asset.kind === 'sse'
                            ? '下载结构化 trace JSON'
                            : asset.kind === 'share_link'
                              ? '下载 JSON'
                              : '下载'}
                        </button>
                        {assetError === asset.pub_id ? (
                          <span className="answer-detail-neutral">下载失败</span>
                        ) : null}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            ))
          )}
        </section>
      </div>
    </div>
  );
}

// ── 思考过程与检索回放 ──

function TraceReplay({ trace }: { trace: TaskTrace }) {
  return (
    <div className="trace-replay">
      <p className="answer-detail-meta">
        深度思考：{trace.deep_think_active ? '开' : '关'}
        {trace.thinking_title ? ` · ${trace.thinking_title}` : ''} · 检索{' '}
        {trace.totals.queries} 词 / {trace.totals.results} 结果 · 思考段{' '}
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
              检索步骤：{(step.queries ?? []).map((query) => (
                <code key={query}>{query}</code>
              ))}
            </p>
            {step.summary ? <p>{step.summary}</p> : null}
          </div>
        ),
      )}
      {trace.search_blocks.map((block, index) => (
        <div key={index} className="trace-search-block">
          <p>
            检索：{block.queries.map((query) => (
              <code key={query}>{query}</code>
            ))}
            （{block.result_count} 条结果）
          </p>
          {block.summary ? <p>{block.summary}</p> : null}
          <ul>
            {block.results.map((result, resultIndex) => (
              <li key={resultIndex}>
                {result.url ? (
                  <a href={result.url} target="_blank" rel="noreferrer">
                    {result.title}
                  </a>
                ) : (
                  result.title
                )}
                {result.site ? ` · ${result.site}` : ''}
                {result.summary ? <p>{result.summary}</p> : null}
              </li>
            ))}
          </ul>
        </div>
      ))}
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

function EvidenceImage({ session, asset }: { session: SessionContext; asset: AnswerEvidence }) {
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
  return (
    <a href={state.url} target="_blank" rel="noreferrer">
      <img className="evidence-image" src={state.url} alt={`${asset.kind} 证据 ${asset.pub_id}`} />
    </a>
  );
}
