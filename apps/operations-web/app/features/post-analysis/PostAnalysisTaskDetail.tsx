import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import {
  getPostAnalysisItem,
  getPostAnalysisItemAsset,
  getPostAnalysisTask,
  listPostAnalysisItems,
  type IdentitySessionHeaders,
  type PostAnalysisAnnotation,
  type PostAnalysisAssetIntegrity,
  type PostAnalysisAssetKind,
  type PostAnalysisItemDetail,
  type PostAnalysisItemRow,
  type PostAnalysisTaskDetail as PostAnalysisTaskDetailData,
} from '@geo/api-client';
import { Badge, CursorPagination, StatePanel, Toast, VerifiedBlobImage } from '@geo/design-system';
import { POST_ANALYSIS_ITEMS_PAGE_SIZE } from './pagination-policy';
import {
  annotationStatusLabel,
  annotationTypeLabel,
  annotationTypeTone,
  categoryLabel,
  directionLabel,
  formatConfidence,
  formatDateTime,
  itemStatusLabel,
  itemStatusTone,
  sentimentLabel,
  severityLabel,
  severityTone,
  taskStatusLabel,
  taskStatusTone,
  verdictLabel,
  verdictTone,
} from './labels';
import './post-analysis.css';

type TaskState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: PostAnalysisTaskDetailData }
  | { kind: 'forbidden' }
  | { kind: 'failed' };

type ItemsState =
  | { kind: 'loading' }
  | {
      kind: 'ready';
      data: PostAnalysisItemRow[];
      nextCursor: string | null;
      hasMore: boolean;
    }
  | { kind: 'forbidden' }
  | { kind: 'failed' };

type ItemDetailState =
  | { kind: 'loading' }
  | { kind: 'ready'; detail: PostAnalysisItemDetail }
  | { kind: 'failed' };

const POLL_INTERVAL_MS = 4000;

const isActiveTask = (status: string): boolean => status === 'queued' || status === 'running';

const ITEM_STATUS_ORDER: readonly string[] = [
  'completed',
  'fetch_failed',
  'analysis_failed',
  'pending',
  'fetching',
  'analyzing',
  'annotating',
];

const geoBadge = (value: boolean | null, emptyLabel: string) =>
  value === null ? emptyLabel : value ? '是' : '否';

export function PostAnalysisTaskDetail({
  taskPubId,
  headers,
}: {
  taskPubId: string;
  headers: IdentitySessionHeaders;
}) {
  const [taskState, setTaskState] = useState<TaskState>({ kind: 'loading' });
  const [itemsState, setItemsState] = useState<ItemsState>({ kind: 'loading' });
  const [expanded, setExpanded] = useState<Record<string, ItemDetailState>>({});
  const [attempt, setAttempt] = useState(0);
  const [itemsCursor, setItemsCursor] = useState<string | null>(null);
  const [itemsBackStack, setItemsBackStack] = useState<Array<string | null>>([]);
  const [notice, setNotice] = useState<{ tone: 'positive' | 'negative'; text: string } | null>(
    null,
  );
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;

  useEffect(() => {
    let cancelled = false;
    setTaskState({ kind: 'loading' });
    void getPostAnalysisTask(headers, taskPubId).then((result) => {
      if (cancelled) return;
      setTaskState(
        result.kind === 'ready'
          ? { kind: 'ready', data: result.data }
          : result.kind === 'forbidden'
            ? { kind: 'forbidden' }
            : { kind: 'failed' },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [headers, taskPubId, attempt]);

  useEffect(() => {
    let cancelled = false;
    setItemsState({ kind: 'loading' });
    void listPostAnalysisItems(headers, taskPubId, {
      ...(itemsCursor ? { cursor: itemsCursor } : {}),
      limit: POST_ANALYSIS_ITEMS_PAGE_SIZE,
    }).then((result) => {
      if (cancelled) return;
      if (
        result.kind === 'ready' &&
        result.data.data.length === 0 &&
        itemsCursor !== null &&
        itemsBackStack.length > 0
      ) {
        setItemsCursor(itemsBackStack.at(-1) ?? null);
        setItemsBackStack((current) => current.slice(0, -1));
        return;
      }
      setItemsState(
        result.kind === 'ready'
          ? {
              kind: 'ready',
              data: result.data.data,
              nextCursor: result.data.nextCursor,
              hasMore: result.data.hasMore,
            }
          : result.kind === 'forbidden'
            ? { kind: 'forbidden' }
            : { kind: 'failed' },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [headers, taskPubId, attempt, itemsBackStack, itemsCursor]);

  useEffect(() => {
    setItemsCursor(null);
    setItemsBackStack([]);
  }, [taskPubId]);

  const hasActive = taskState.kind === 'ready' && isActiveTask(taskState.data.task.status);
  useEffect(() => {
    if (!hasActive) return;
    const timer = setInterval(() => {
      void getPostAnalysisTask(headers, taskPubId).then((result) => {
        if (result.kind !== 'ready') return;
        setTaskState((current) =>
          current.kind === 'ready' ? { kind: 'ready', data: result.data } : current,
        );
      });
      void listPostAnalysisItems(headers, taskPubId, {
        ...(itemsCursor ? { cursor: itemsCursor } : {}),
        limit: POST_ANALYSIS_ITEMS_PAGE_SIZE,
      }).then((result) => {
        if (result.kind !== 'ready') return;
        setItemsState((current) =>
          current.kind === 'ready'
            ? {
                ...current,
                data: result.data.data,
                nextCursor: result.data.nextCursor,
                hasMore: result.data.hasMore,
              }
            : current,
        );
      });
      for (const itemPubId of Object.keys(expandedRef.current)) {
        void getPostAnalysisItem(headers, itemPubId).then((result) => {
          if (result.kind !== 'ready') return;
          setExpanded((current) =>
            itemPubId in current
              ? { ...current, [itemPubId]: { kind: 'ready', detail: result.data } }
              : current,
          );
        });
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [hasActive, headers, itemsCursor, taskPubId]);

  const nextItemsPage = () => {
    if (itemsState.kind !== 'ready' || !itemsState.hasMore || !itemsState.nextCursor) return;
    setItemsBackStack((current) => [...current, itemsCursor]);
    setItemsCursor(itemsState.nextCursor);
  };

  const previousItemsPage = () => {
    if (itemsBackStack.length === 0) return;
    setItemsCursor(itemsBackStack.at(-1) ?? null);
    setItemsBackStack((current) => current.slice(0, -1));
  };

  const loadItemDetail = (itemPubId: string) => {
    void getPostAnalysisItem(headers, itemPubId).then((result) => {
      setExpanded((current) => {
        if (!(itemPubId in current)) return current;
        return result.kind === 'ready'
          ? { ...current, [itemPubId]: { kind: 'ready', detail: result.data } }
          : { ...current, [itemPubId]: { kind: 'failed' } };
      });
    });
  };

  const toggleItem = (row: PostAnalysisItemRow) => {
    if (row.pubId in expanded) {
      setExpanded((current) => {
        const next = { ...current };
        delete next[row.pubId];
        return next;
      });
      return;
    }
    setExpanded((current) => ({ ...current, [row.pubId]: { kind: 'loading' } }));
    loadItemDetail(row.pubId);
  };

  const retryItem = (itemPubId: string) => {
    setExpanded((current) => ({ ...current, [itemPubId]: { kind: 'loading' } }));
    loadItemDetail(itemPubId);
  };

  const orderedStatusCounts =
    taskState.kind === 'ready'
      ? [...taskState.data.statusCounts].sort(
          (left, right) =>
            ITEM_STATUS_ORDER.indexOf(left.status) - ITEM_STATUS_ORDER.indexOf(right.status),
        )
      : [];

  return (
    <main className="pa-page" aria-label="帖子分析任务详情">
      <section className="pa-hero">
        <div>
          <span className="overline">Post evidence analysis</span>
          <h2>{taskState.kind === 'ready' ? taskState.data.task.targetBrand : '帖子分析任务'}</h2>
          {taskState.kind === 'ready' ? (
            <>
              <p>
                {taskState.data.task.targetBrandAliases.join('、') || '无别名'}
                {' · '}
                {formatDateTime(taskState.data.task.createdAt)}
                {' · '}
                {taskState.data.task.urlCount} 个 URL
              </p>
              {taskState.data.investigationPubId ? (
                <p className="pa-muted">
                  已建立情报调查 <code>{taskState.data.investigationPubId}</code>
                </p>
              ) : null}
            </>
          ) : (
            <p>任务详情加载中。</p>
          )}
        </div>
        <div className="pa-hero-side">
          {taskState.kind === 'ready' ? (
            <Badge tone={taskStatusTone(taskState.data.task.status)}>
              {taskStatusLabel(taskState.data.task.status)}
            </Badge>
          ) : null}
          <Link className="button button-secondary" to="/platform/operations/post-analysis">
            返回列表
          </Link>
          <button
            className="button button-secondary"
            type="button"
            onClick={() => setAttempt((value) => value + 1)}
          >
            刷新
          </button>
        </div>
      </section>

      {taskState.kind === 'loading' ? (
        <StatePanel state="loading" />
      ) : taskState.kind === 'forbidden' ? (
        <StatePanel state="forbidden" />
      ) : taskState.kind === 'failed' ? (
        <StatePanel state="failed" onRetry={() => setAttempt((value) => value + 1)} />
      ) : (
        <>
          <section className="pa-card" aria-label="任务进度">
            <div className="pa-section-head">
              <div>
                <span className="overline">进度</span>
                <h3>条目状态计数</h3>
              </div>
              {taskState.data.task.error ? (
                <span className="pa-error" role="alert">
                  {taskState.data.task.error}
                </span>
              ) : null}
            </div>
            <div className="pa-counts">
              {orderedStatusCounts.map(({ status, count }) => (
                <span className="pa-count" key={status}>
                  <Badge tone={itemStatusTone(status)}>{itemStatusLabel(status)}</Badge>
                  <strong>{count}</strong>
                </span>
              ))}
              {orderedStatusCounts.length === 0 ? (
                <span className="pa-muted">尚无条目状态计数。</span>
              ) : null}
            </div>
          </section>

          <section className="pa-card" aria-label="分析条目列表">
            <div className="pa-section-head">
              <div>
                <span className="overline">取证明细</span>
                <h3>帖子条目</h3>
              </div>
            </div>
            {itemsState.kind === 'loading' ? (
              <StatePanel state="loading" />
            ) : itemsState.kind === 'forbidden' ? (
              <StatePanel state="forbidden" />
            ) : itemsState.kind === 'failed' ? (
              <StatePanel state="failed" onRetry={() => setAttempt((value) => value + 1)} />
            ) : itemsState.data.length === 0 ? (
              <StatePanel state="empty" />
            ) : (
              <>
                <div className="pa-items">
                  {itemsState.data.map((row) => (
                    <ItemCard
                      key={row.pubId}
                      row={row}
                      detailState={expanded[row.pubId]}
                      onToggle={() => toggleItem(row)}
                      onRetry={() => retryItem(row.pubId)}
                      headers={headers}
                    />
                  ))}
                </div>
                <CursorPagination
                  page={itemsBackStack.length + 1}
                  hasPrevious={itemsBackStack.length > 0}
                  hasNext={itemsState.hasMore && itemsState.nextCursor !== null}
                  onPrevious={previousItemsPage}
                  onNext={nextItemsPage}
                  label="帖子分析条目分页"
                />
              </>
            )}
          </section>
        </>
      )}
      {notice ? <Toast tone={notice.tone}>{notice.text}</Toast> : null}
    </main>
  );
}

function ItemCard({
  row,
  detailState,
  onToggle,
  onRetry,
  headers,
}: {
  row: PostAnalysisItemRow;
  detailState: ItemDetailState | undefined;
  onToggle: () => void;
  onRetry: () => void;
  headers: IdentitySessionHeaders;
}) {
  const expandedNow = detailState !== undefined;
  return (
    <article className="pa-item">
      <header className="pa-item-head">
        <div className="pa-item-url">
          <strong>#{row.ordinal}</strong>
          <span className="pa-host">{row.host}</span>
          <a href={row.url} target="_blank" rel="noreferrer">
            {row.url}
          </a>
        </div>
        <div className="pa-item-actions">
          <Badge tone={itemStatusTone(row.status)}>{itemStatusLabel(row.status)}</Badge>
          <button className="button button-secondary" type="button" onClick={onToggle}>
            {expandedNow ? '收起' : '展开'}
          </button>
        </div>
      </header>

      {row.status === 'completed' ? (
        <div className="pa-badge-row">
          <Badge tone={row.isGeoPost ? 'warning' : 'neutral'}>
            GEO帖：{geoBadge(row.isGeoPost, '—')}
          </Badge>
          {row.category ? (
            <Badge tone="info">类别：{categoryLabel(row.category, row.categoryLabel)}</Badge>
          ) : null}
          <Badge tone={row.isTargetBrandGeo ? 'positive' : 'neutral'}>
            目标品牌GEO帖：{geoBadge(row.isTargetBrandGeo, '未提及')}
          </Badge>
          <Badge tone={row.disparagementCount > 0 ? 'danger' : 'neutral'}>
            拉踩 {row.disparagementCount} 条
          </Badge>
          <Badge tone={row.misinformationCount > 0 ? 'danger' : 'neutral'}>
            不实 {row.misinformationCount} 条
          </Badge>
          <Badge tone="neutral">标注：{annotationStatusLabel(row.annotationStatus)}</Badge>
        </div>
      ) : null}

      {row.error ? (
        <p className="pa-error" role="alert">
          {row.error}
        </p>
      ) : null}

      {expandedNow ? (
        detailState.kind === 'loading' ? (
          <StatePanel state="loading" />
        ) : detailState.kind === 'failed' ? (
          <StatePanel state="failed" onRetry={onRetry} />
        ) : (
          <ItemDetailBody detail={detailState.detail} headers={headers} />
        )
      ) : null}
    </article>
  );
}

function ItemDetailBody({
  detail,
  headers,
}: {
  detail: PostAnalysisItemDetail;
  headers: IdentitySessionHeaders;
}) {
  const [showAnnotations, setShowAnnotations] = useState(false);
  const { analysis } = detail;
  const validation = detail.analysisValidation;

  return (
    <div className="pa-detail">
      {detail.finalUrl && detail.finalUrl !== detail.url ? (
        <p className="pa-muted">
          最终 URL：<code>{detail.finalUrl}</code>
          {detail.httpStatus !== null ? `（HTTP ${detail.httpStatus}）` : ''}
        </p>
      ) : null}

      {analysis ? (
        <>
          <section className="pa-block" aria-label="内容摘要">
            <h4>摘要</h4>
            <p>{analysis.summary}</p>
            <div className="pa-badge-row">
              <Badge tone={analysis.isGeoPost ? 'warning' : 'neutral'}>
                GEO帖：{geoBadge(analysis.isGeoPost, '—')}
              </Badge>
              <Badge tone="neutral">GEO 置信度：{formatConfidence(analysis.geoConfidence)}</Badge>
              <Badge tone="info">
                类别：{categoryLabel(analysis.category, analysis.categoryLabel)}
              </Badge>
              <Badge tone={analysis.isTargetBrandGeo ? 'positive' : 'neutral'}>
                目标品牌GEO帖：{geoBadge(analysis.isTargetBrandGeo, '未提及')}
              </Badge>
            </div>
            {analysis.categoryRationale ? (
              <p className="pa-muted">分类依据：{analysis.categoryRationale}</p>
            ) : null}
          </section>

          {analysis.geoSignals.length > 0 ? (
            <section className="pa-block" aria-label="GEO 特征">
              <h4>GEO 特征（{analysis.geoSignals.length}）</h4>
              <ul className="pa-finding-list">
                {analysis.geoSignals.map((signal, index) => (
                  <li key={`${signal.signal}-${index}`}>
                    <strong>{signal.signal}</strong>
                    <blockquote className="pa-quote">{signal.quote}</blockquote>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {analysis.brandMentions.length > 0 ? (
            <section className="pa-block" aria-label="品牌提及">
              <h4>品牌提及（{analysis.brandMentions.length}）</h4>
              <ul className="pa-finding-list">
                {analysis.brandMentions.map((mention, index) => (
                  <li key={`${mention.brand}-${index}`}>
                    <div className="pa-badge-row">
                      <strong>{mention.brand}</strong>
                      {mention.isTargetBrand ? <Badge tone="info">目标品牌</Badge> : null}
                      <Badge tone="neutral">{sentimentLabel(mention.sentiment)}</Badge>
                    </div>
                    <blockquote className="pa-quote">{mention.quote}</blockquote>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {analysis.disparagement.length > 0 ? (
            <section className="pa-block" aria-label="拉踩内容">
              <h4>拉踩内容（{analysis.disparagement.length}）</h4>
              <ul className="pa-finding-list">
                {analysis.disparagement.map((finding, index) => (
                  <li key={`${finding.quote}-${index}`}>
                    <div className="pa-badge-row">
                      <Badge tone="danger">{directionLabel(finding.direction)}</Badge>
                      <Badge tone={severityTone(finding.severity)}>
                        严重度：{severityLabel(finding.severity)}
                      </Badge>
                      <span className="pa-muted">
                        {finding.subjectBrand} → {finding.objectBrand}
                      </span>
                      <span className="pa-muted">
                        置信度 {formatConfidence(finding.confidence)}
                      </span>
                    </div>
                    <blockquote className="pa-quote">{finding.quote}</blockquote>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {analysis.claims.length > 0 ? (
            <section className="pa-block" aria-label="事实核验">
              <h4>事实核验（{analysis.claims.length}）</h4>
              <ul className="pa-finding-list">
                {analysis.claims.map((claim, index) => (
                  <li key={`${claim.claim}-${index}`}>
                    <div className="pa-badge-row">
                      <strong>{claim.claim}</strong>
                      {claim.aboutTargetBrand ? <Badge tone="info">涉及目标品牌</Badge> : null}
                      {claim.verification ? (
                        <Badge tone={verdictTone(claim.verification.verdict)}>
                          {verdictLabel(claim.verification.verdict)}
                        </Badge>
                      ) : (
                        <Badge tone="neutral">未核验</Badge>
                      )}
                    </div>
                    <blockquote className="pa-quote">{claim.quote}</blockquote>
                    {claim.verification ? (
                      <div className="pa-verification">
                        {claim.verification.correction ? (
                          <p>{claim.verification.correction}</p>
                        ) : null}
                        <p className="pa-muted">
                          核验置信度 {formatConfidence(claim.verification.confidence)}
                        </p>
                        {claim.verification.sources.length > 0 ? (
                          <ul className="pa-sources">
                            {claim.verification.sources.map((source, sourceIndex) => (
                              <li key={`${source.url}-${sourceIndex}`}>
                                <a href={source.url} target="_blank" rel="noreferrer">
                                  {source.title}
                                </a>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : (
        <p className="pa-muted">该条目没有分析数据。</p>
      )}

      {validation && (validation.droppedTotal > 0 || validation.verificationErrors > 0) ? (
        <p className="pa-muted">
          逐字校验丢弃 {validation.droppedTotal} 条证据；事实核验异常{' '}
          {validation.verificationErrors} 次；已核验陈述 {validation.claimsVerified} 条。
        </p>
      ) : null}

      {detail.annotations.length > 0 ? (
        <section className="pa-block" aria-label="截图注解开关注释">
          <label className="pa-checkbox" htmlFor={`pa-annotations-${detail.pubId}`}>
            <input
              id={`pa-annotations-${detail.pubId}`}
              type="checkbox"
              checked={showAnnotations}
              onChange={(event) => setShowAnnotations(event.currentTarget.checked)}
            />
            显示注解开关注释（{detail.annotations.length} 条）
          </label>
          {showAnnotations ? <AnnotationList annotations={detail.annotations} /> : null}
        </section>
      ) : null}

      {detail.status === 'completed' ? (
        <ItemAssetPanel
          headers={headers}
          itemPubId={detail.pubId}
          screenshotAsset={detail.screenshotAsset}
          annotatedAsset={detail.annotatedAsset}
        />
      ) : null}
    </div>
  );
}

function AnnotationList({ annotations }: { annotations: PostAnalysisAnnotation[] }) {
  return (
    <ul className="pa-finding-list">
      {annotations.map((annotation, index) => (
        <li key={`${annotation.quote}-${index}`}>
          <div className="pa-badge-row">
            <Badge tone={annotationTypeTone(annotation.type)}>
              {annotationTypeLabel(annotation.type)}
            </Badge>
            <Badge tone={annotation.matched ? 'positive' : 'neutral'}>
              {annotation.matched ? '已定位' : '未定位'}
            </Badge>
          </div>
          {annotation.note ? <p className="pa-muted">{annotation.note}</p> : null}
          <blockquote className="pa-quote">{annotation.quote}</blockquote>
        </li>
      ))}
    </ul>
  );
}

/** 取证截图：标注图/原截图切换；字节流经 verified-Blob 边界（MIME+尺寸+SHA-256 校验）。 */
function ItemAssetPanel({
  headers,
  itemPubId,
  screenshotAsset,
  annotatedAsset,
}: {
  headers: IdentitySessionHeaders;
  itemPubId: string;
  screenshotAsset: PostAnalysisAssetIntegrity | null;
  annotatedAsset: PostAnalysisAssetIntegrity | null;
}) {
  const [kind, setKind] = useState<PostAnalysisAssetKind>(
    annotatedAsset ? 'annotated' : 'screenshot',
  );
  const availableKinds: PostAnalysisAssetKind[] = [];
  if (annotatedAsset) availableKinds.push('annotated');
  if (screenshotAsset) availableKinds.push('screenshot');
  if (availableKinds.length === 0) {
    return (
      <section className="pa-block" aria-label="取证截图">
        <h4>取证截图</h4>
        <StatePanel state="empty" />
      </section>
    );
  }
  const integrity = kind === 'annotated' ? annotatedAsset : screenshotAsset;
  if (!integrity) return null;
  const alt = kind === 'annotated' ? '标注截图' : '原始截图';
  return (
    <section className="pa-block" aria-label="取证截图">
      <div className="pa-section-head">
        <h4>取证截图</h4>
        {availableKinds.length > 1 ? (
          <div className="pa-tabs" role="group" aria-label="截图类型">
            <button
              type="button"
              className={kind === 'annotated' ? 'active' : ''}
              onClick={() => setKind('annotated')}
            >
              标注图
            </button>
            <button
              type="button"
              className={kind === 'screenshot' ? 'active' : ''}
              onClick={() => setKind('screenshot')}
            >
              原截图
            </button>
          </div>
        ) : null}
      </div>
      <VerifiedBlobImage
        resourceKey={`${itemPubId}:${kind}`}
        alt={alt}
        className="pa-screenshot"
        load={() =>
          getPostAnalysisItemAsset(headers, itemPubId, kind, integrity).then((result) =>
            result.kind === 'ready' ? { kind: 'ready' as const, blob: result.data.blob } : result,
          )
        }
      />
    </section>
  );
}
