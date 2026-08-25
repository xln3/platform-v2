import { CursorPagination, ModelSelect, type ModelSelectOption } from '@geo/design-system';
import { EvidenceImageFrame } from '@geo/evidence-viewer';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useCursorCollection } from '../../../pagination';
import { SERVICE2_RUN_SELECTOR_PAGE_SIZE } from '../pagination-policy';
import { executionApi, type Run } from '../../execution/api';
import {
  defaultWindow,
  ServicesApiError,
  servicesApi,
  type Project,
  type Service2Batch,
  type Service2AnalysisModel,
  type Service2AnalysisModelCatalog,
  type Service2CorpusPage,
  type Service2Finding,
  type Service2FindingPage,
  type Service2Manifest,
  type SessionContext,
} from '../api';
import './outbound-risk.css';

function commandKey(kind: string): string {
  const suffix =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `service2-${kind}-${suffix}`;
}

function cstDate(value = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(value);
}

function startInstant(value: string): string {
  return new Date(`${value}T00:00:00+08:00`).toISOString();
}

function endInstant(value: string, now = new Date()): string {
  return value === cstDate(now)
    ? now.toISOString()
    : new Date(`${value}T23:59:59+08:00`).toISOString();
}

export function service2BatchWindow(
  window_: { start: string; end: string },
  boundary = new Date(),
): { windowStart: string; windowEnd: string; sourceSnapshotBoundary: string } {
  return {
    windowStart: startInstant(window_.start),
    windowEnd: endInstant(window_.end, boundary),
    sourceSnapshotBoundary: boundary.toISOString(),
  };
}

function shortHash(value: string | null): string {
  return value ? `${value.slice(0, 12)}…${value.slice(-6)}` : '—';
}

const isTerminalRun = (run: Run): boolean =>
  ['completed', 'completed_with_failures', 'failed', 'cancelled'].includes(run.state);

const isSelectableRootRun = (run: Run): boolean =>
  isTerminalRun(run) && run.retry_of_run_pub_id === null;

function modelPriceLabel(model: Service2AnalysisModel): string {
  if (model.input_usd_per_million_tokens === null || model.output_usd_per_million_tokens === null) {
    return '价格待运维复核';
  }
  return `输入 $${model.input_usd_per_million_tokens.toFixed(3)} / 输出 $${model.output_usd_per_million_tokens.toFixed(3)}（每百万 tokens）`;
}

const PROCESSING_LABELS: Record<string, string> = {
  queued: '待处理',
  fetching: '抓取中',
  processed: '已处理',
  partial: '部分解析',
  blocked: '被阻断',
  gone: '已失效',
  retry_wait: '待重试',
  manual_evidence_required: '待人工补证',
  unobservable: '不可观测',
  failed: '失败',
  cancelled: '已取消',
};

function attributionLabel(finding: Service2Finding, kind: 'publisher' | 'commissioner') {
  const value = finding[kind];
  return value.party ? `${value.party} · ${value.confidence}` : `unknown · ${value.confidence}`;
}

function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  try {
    const parsed = new URL(value);
    return ['http:', 'https:'].includes(parsed.protocol) && parsed.hostname ? parsed.href : null;
  } catch {
    return null;
  }
}

type EvidenceReference = { label: string; url: string | null };

function evidenceReferences(rows: Array<Record<string, unknown>>): EvidenceReference[] {
  const references: EvidenceReference[] = [];
  for (const row of rows) {
    const url = safeHttpUrl(row.url ?? row.source_url);
    if (url) {
      references.push({ label: url, url });
      continue;
    }
    for (const key of [
      'evidence_pub_id',
      'source_pub_id',
      'document_pub_id',
      'account_pub_id',
      'approval_pub_id',
      'title',
    ]) {
      const candidate = row[key];
      if (typeof candidate === 'string' && candidate.trim()) {
        references.push({ label: candidate.trim(), url: null });
        break;
      }
    }
  }
  return references;
}

function EvidenceReferences({ rows }: { rows: Array<Record<string, unknown>> }) {
  const references = evidenceReferences(rows);
  if (!references.length) return <span>—</span>;
  return (
    <span className="s2-reference-list">
      {references.map((reference, index) =>
        reference.url ? (
          <a
            key={`${reference.url}-${index}`}
            className="s2-external-link"
            href={reference.url}
            target="_blank"
            rel="noreferrer noopener"
          >
            {reference.label}
          </a>
        ) : (
          <code key={`${reference.label}-${index}`}>{reference.label}</code>
        ),
      )}
    </span>
  );
}

function FindingEvidenceImage({
  session,
  finding,
}: {
  session: SessionContext;
  finding: Service2Finding;
}) {
  const evidencePubId = finding.visual_evidence_pub_id;
  const [image, setImage] = useState<
    { state: 'loading' } | { state: 'ready'; url: string } | { state: 'failed' }
  >({ state: 'loading' });

  useEffect(() => {
    if (!evidencePubId) return;
    let active = true;
    let objectUrl: string | null = null;
    setImage({ state: 'loading' });
    void servicesApi
      .service2EvidenceBlob(session, evidencePubId)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setImage({ state: 'ready', url: objectUrl });
      })
      .catch(() => {
        if (active) setImage({ state: 'failed' });
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [evidencePubId, session]);

  if (!evidencePubId) return <p>未绑定可视证据；该 finding 不能进入客户案例。</p>;
  if (image.state === 'loading') return <p>可视证据加载中…</p>;
  if (image.state === 'failed') return <p>可视证据不可读；该 finding 不能进入客户案例。</p>;
  return (
    <EvidenceImageFrame
      label={`服务 2 页面证据 ${evidencePubId}`}
      anchor={{
        assetId: evidencePubId,
        ...(finding.visual_bbox ? { bbox: finding.visual_bbox } : {}),
      }}
      overlayLabel="逐字证据原文位置"
    >
      <a href={image.url} target="_blank" rel="noreferrer noopener">
        <img className="s2-evidence-image" src={image.url} alt={`页面证据 ${evidencePubId}`} />
      </a>
    </EvidenceImageFrame>
  );
}

export function OutboundRiskWorkspace({
  session,
  project,
}: {
  session: SessionContext;
  project: Project;
}) {
  const [batch, setBatch] = useState<Service2Batch | null>(null);
  const [modelCatalog, setModelCatalog] = useState<Service2AnalysisModelCatalog | null>(null);
  const [selectedAnalysisModel, setSelectedAnalysisModel] = useState('');
  const [selectedRuns, setSelectedRuns] = useState<string[]>([]);
  const [window_, setWindow] = useState(() => defaultWindow(30));
  const [items, setItems] = useState<Service2CorpusPage | null>(null);
  const [itemCursors, setItemCursors] = useState<Array<string | undefined>>([undefined]);
  const [itemPageIndex, setItemPageIndex] = useState(0);
  const [processingFilter, setProcessingFilter] = useState('');
  const [attributionFilter, setAttributionFilter] = useState('');
  const [findings, setFindings] = useState<Service2FindingPage | null>(null);
  const [findingCursors, setFindingCursors] = useState<Array<string | undefined>>([undefined]);
  const [findingPageIndex, setFindingPageIndex] = useState(0);
  const [reviewFilter, setReviewFilter] = useState('');
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const [reviewRationale, setReviewRationale] = useState('');
  const [manifest, setManifest] = useState<Service2Manifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // A refreshed run page must not silently undo an operator's explicit choice.
  // Keep this UI-only intent outside the server batch contract; changing project
  // starts a new selection session and clears the tombstones.
  const explicitlyDeselectedRuns = useRef<Set<string>>(new Set());

  const canControl = ['operator', 'analyst', 'admin'].includes(session.role);
  const canReview = ['reviewer', 'admin'].includes(session.role);

  const loadRuns = useCallback(
    (cursor?: string) =>
      executionApi.runs(session, {
        projectPubId: project.pub_id,
        ...(cursor ? { cursor } : {}),
        limit: SERVICE2_RUN_SELECTOR_PAGE_SIZE,
      }),
    [project.pub_id, session],
  );
  const runsPage = useCursorCollection(loadRuns, project.pub_id);

  const loadBatch = useCallback(async () => {
    try {
      const value = await servicesApi.service2CurrentBatch(session, project.pub_id);
      setBatch(value);
      return value;
    } catch (error) {
      if (error instanceof ServicesApiError && error.code === 'service2_batch_not_found') {
        setBatch(null);
        return null;
      }
      throw error;
    }
  }, [project.pub_id, session]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    loadBatch()
      .catch((error) => {
        if (active) setNotice(`加载失败：${error instanceof Error ? error.message : 'unknown'}`);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [loadBatch]);

  useEffect(() => {
    explicitlyDeselectedRuns.current.clear();
    setSelectedRuns([]);
    setModelCatalog(null);
    setSelectedAnalysisModel('');
  }, [project.pub_id]);

  useEffect(() => {
    let active = true;
    servicesApi
      .service2AnalysisModels(session, project.pub_id)
      .then((catalog) => {
        if (!active) return;
        setModelCatalog(catalog);
        setSelectedAnalysisModel((current) =>
          catalog.models.some((model) => model.model === current) ? current : catalog.default_model,
        );
      })
      .catch((error) => {
        if (active)
          setNotice(`分析模型清单加载失败：${error instanceof Error ? error.message : 'unknown'}`);
      });
    return () => {
      active = false;
    };
  }, [project.pub_id, session]);

  useEffect(() => {
    setItemCursors([undefined]);
    setItemPageIndex(0);
    setFindingCursors([undefined]);
    setFindingPageIndex(0);
    setSelectedFindingId(null);
    setReviewRationale('');
  }, [batch?.batch_pub_id]);

  useEffect(() => {
    const completed = runsPage.data
      // Retry children are resolved automatically from their logical root.
      // Selecting both would misstate the operator's denominator even though
      // the backend deduplicates it correctly.
      .filter((run: Run) => isSelectableRootRun(run))
      .map((run: Run) => run.pub_id);
    if (completed.length === 0) return;
    setSelectedRuns((current) => [
      ...new Set([
        ...current,
        ...completed.filter((runPubId) => !explicitlyDeselectedRuns.current.has(runPubId)),
      ]),
    ]);
  }, [runsPage.data]);

  useEffect(() => {
    if (!batch) {
      setItems(null);
      return;
    }
    let active = true;
    servicesApi
      .service2CorpusItems(session, {
        projectPubId: project.pub_id,
        batchPubId: batch.batch_pub_id,
        ...(itemCursors[itemPageIndex] ? { cursor: itemCursors[itemPageIndex] } : {}),
        ...(processingFilter ? { processingState: processingFilter } : {}),
        ...(attributionFilter ? { attributionConfidence: attributionFilter } : {}),
      })
      .then((value) => {
        if (active) setItems(value);
      })
      .catch((error) => {
        if (active)
          setNotice(`语料加载失败：${error instanceof Error ? error.message : 'unknown'}`);
      });
    return () => {
      active = false;
    };
  }, [
    attributionFilter,
    batch,
    itemCursors,
    itemPageIndex,
    processingFilter,
    project.pub_id,
    session,
  ]);

  useEffect(() => {
    if (!batch) {
      setFindings(null);
      return;
    }
    let active = true;
    servicesApi
      .service2Findings(session, {
        projectPubId: project.pub_id,
        batchPubId: batch.batch_pub_id,
        ...(findingCursors[findingPageIndex] ? { cursor: findingCursors[findingPageIndex] } : {}),
        ...(reviewFilter ? { reviewState: reviewFilter } : {}),
      })
      .then((value) => {
        if (!active) return;
        setFindings(value);
        setSelectedFindingId((current) =>
          value.data.some((finding) => finding.finding_pub_id === current)
            ? current
            : (value.data[0]?.finding_pub_id ?? null),
        );
      })
      .catch((error) => {
        if (active)
          setNotice(`finding 加载失败：${error instanceof Error ? error.message : 'unknown'}`);
      });
    return () => {
      active = false;
    };
  }, [batch, findingCursors, findingPageIndex, project.pub_id, reviewFilter, session]);

  useEffect(() => {
    if (!batch || batch.status !== 'frozen') {
      setManifest(null);
      return;
    }
    void servicesApi
      .service2Manifest(session, {
        projectPubId: project.pub_id,
        batchPubId: batch.batch_pub_id,
      })
      .then(setManifest)
      .catch((error) =>
        setNotice(`manifest 加载失败：${error instanceof Error ? error.message : 'unknown'}`),
      );
  }, [batch, project.pub_id, session]);

  useEffect(() => {
    setItemCursors([undefined]);
    setItemPageIndex(0);
  }, [processingFilter, attributionFilter]);

  useEffect(() => {
    setFindingCursors([undefined]);
    setFindingPageIndex(0);
  }, [reviewFilter]);

  const selectedFinding = useMemo(
    () => findings?.data.find((finding) => finding.finding_pub_id === selectedFindingId) ?? null,
    [findings, selectedFindingId],
  );
  const modelOptions = useMemo<ModelSelectOption[]>(
    () =>
      (modelCatalog?.models ?? []).map((model) => ({
        value: model.model,
        label: model.label === model.model ? model.model : `${model.label} · ${model.model}`,
        group: model.provider,
        capability: `${model.capability}；联网方式：${model.web_search_mode}；已验证供应商搜索与引用（${model.web_search_audited_at}）`,
        priceLabel: modelPriceLabel(model),
        isDefault: model.model === modelCatalog?.default_model,
        recommended: model.recommended,
      })),
    [modelCatalog],
  );

  async function createBatch() {
    if (!canControl || busy || selectedRuns.length === 0 || !selectedAnalysisModel) return;
    setBusy(true);
    setNotice(null);
    try {
      const frozenWindow = service2BatchWindow(window_);
      const created = await servicesApi.createService2Batch(session, {
        projectPubId: project.pub_id,
        runPubIds: selectedRuns,
        analysisModel: selectedAnalysisModel,
        ...frozenWindow,
        idempotencyKey: commandKey('create'),
      });
      setBatch(created);
      setNotice(
        `批次 ${created.batch_pub_id} 已物化全部 ${created.coverage.expected_occurrences} 条 U occurrence。`,
      );
    } catch (error) {
      setNotice(`创建失败：${error instanceof Error ? error.message : 'unknown'}`);
    } finally {
      setBusy(false);
    }
  }

  async function lifecycle(action: 'start' | 'pause' | 'resume' | 'retry' | 'cancel') {
    if (!batch || !canControl || busy) return;
    setBusy(true);
    try {
      await servicesApi.service2Lifecycle(session, {
        projectPubId: project.pub_id,
        batchPubId: batch.batch_pub_id,
        action,
        idempotencyKey: commandKey(action),
      });
      await loadBatch();
      setNotice(`已提交 ${action} 命令。`);
    } catch (error) {
      setNotice(`操作失败：${error instanceof Error ? error.message : 'unknown'}`);
    } finally {
      setBusy(false);
    }
  }

  async function review(decision: 'accepted' | 'rejected' | 'needs_changes') {
    if (!batch || !selectedFinding || !canReview || busy || !reviewRationale.trim()) return;
    setBusy(true);
    try {
      const updated = await servicesApi.reviewService2Finding(session, {
        projectPubId: project.pub_id,
        batchPubId: batch.batch_pub_id,
        findingPubId: selectedFinding.finding_pub_id,
        version: selectedFinding.version,
        decision,
        reasonCode: `manual_${decision}`,
        rationale: reviewRationale.trim(),
        idempotencyKey: commandKey('review'),
      });
      setFindings((current) =>
        current
          ? {
              ...current,
              data: current.data.map((finding) =>
                finding.finding_pub_id === updated.finding_pub_id ? updated : finding,
              ),
            }
          : current,
      );
      setReviewRationale('');
      await loadBatch();
      setNotice('审核决定已追加，机器原始判断未被覆盖。');
    } catch (error) {
      setNotice(`审核失败：${error instanceof Error ? error.message : 'unknown'}`);
    } finally {
      setBusy(false);
    }
  }

  async function freeze() {
    if (!batch || !canReview || busy) return;
    setBusy(true);
    try {
      const value = await servicesApi.freezeService2Batch(session, {
        projectPubId: project.pub_id,
        batchPubId: batch.batch_pub_id,
        idempotencyKey: commandKey('freeze'),
      });
      setManifest(value);
      await loadBatch();
      setNotice(`已冻结 revision ${value.revision}；正式报告只读取该 manifest。`);
    } catch (error) {
      setNotice(`冻结失败：${error instanceof Error ? error.message : 'unknown'}`);
    } finally {
      setBusy(false);
    }
  }

  function nextItems() {
    if (!items?.next_cursor) return;
    setItemCursors((current) => {
      const next = current.slice(0, itemPageIndex + 1);
      next[itemPageIndex + 1] = items.next_cursor ?? undefined;
      return next;
    });
    setItemPageIndex((current) => current + 1);
  }

  function nextFindings() {
    if (!findings?.next_cursor) return;
    setFindingCursors((current) => {
      const next = current.slice(0, findingPageIndex + 1);
      next[findingPageIndex + 1] = findings.next_cursor ?? undefined;
      return next;
    });
    setFindingPageIndex((current) => current + 1);
  }

  function startNewBatchSetup() {
    setBatch(null);
    setManifest(null);
    setSelectedFindingId(null);
    setItemCursors([undefined]);
    setItemPageIndex(0);
    setFindingCursors([undefined]);
    setFindingPageIndex(0);
    setNotice('请确认新的运行、时间窗和 U boundary。');
  }

  if (loading) return <p className="service-note">正在加载服务 2 全 U 核查范围…</p>;

  if (!batch) {
    return (
      <section className="execution-card s2-setup" aria-labelledby="service2-create-title">
        <div className="section-title">
          <h2 id="service2-create-title">建立全 U 核查批次</h2>
          <span>冻结运行 + 问答 + 时间窗 + 判据版本</span>
        </div>
        <p className="service-note">
          入池不按作者、委托、己方/竞品归属或品牌共现预筛。运行只用于确认所有查询已终态；成功查询的全部
          U 入池，失败查询记入覆盖缺口，不会被当成 0 U。
        </p>
        <div className="s2-window-grid">
          <label>
            窗口开始
            <input
              type="date"
              value={window_.start}
              onChange={(event) =>
                setWindow((current) => ({ ...current, start: event.target.value }))
              }
            />
          </label>
          <label>
            窗口结束
            <input
              type="date"
              value={window_.end}
              onChange={(event) =>
                setWindow((current) => ({ ...current, end: event.target.value }))
              }
            />
          </label>
        </div>
        <ModelSelect
          className="s2-model-select"
          label="联网分析模型"
          ariaLabel="主动拉踩内容联网分析模型选择"
          value={selectedAnalysisModel}
          options={modelOptions}
          disabled={busy}
          onChange={setSelectedAnalysisModel}
          emptyLabel="正在从服务端加载可用模型…"
          hint="仅展示真实调用中同时观察到供应商搜索事件与引用的模型；价格为目录快照，凭据只由服务端环境注入。"
        />
        <fieldset className="s2-run-picker">
          <legend>纳入运行（{selectedRuns.length} 个）</legend>
          {runsPage.state === 'loading' ? (
            <p>正在加载运行…</p>
          ) : runsPage.state === 'failed' ? (
            <p>
              运行加载失败。
              <button type="button" onClick={() => void runsPage.refresh()}>
                重试
              </button>
            </p>
          ) : runsPage.data.length ? (
            (runsPage.data as Run[]).map((run) => (
              <label key={run.pub_id}>
                <input
                  type="checkbox"
                  disabled={!isSelectableRootRun(run)}
                  checked={selectedRuns.includes(run.pub_id)}
                  onChange={(event) => {
                    if (event.target.checked) {
                      explicitlyDeselectedRuns.current.delete(run.pub_id);
                    } else {
                      explicitlyDeselectedRuns.current.add(run.pub_id);
                    }
                    setSelectedRuns((current) =>
                      event.target.checked
                        ? [...new Set([...current, run.pub_id])]
                        : current.filter((value) => value !== run.pub_id),
                    );
                  }}
                />
                <span>{run.pub_id}</span>
                <small>
                  {run.state} · 成功 {run.completed_tasks} / 失败 {run.failed_tasks} / 总计{' '}
                  {run.total_tasks}
                  {run.retry_of_run_pub_id
                    ? ` · 重试子运行，随根运行 ${run.retry_of_run_pub_id} 自动归并`
                    : ''}
                </small>
              </label>
            ))
          ) : (
            <p>当前项目没有可选运行。</p>
          )}
          {runsPage.state === 'ready' && runsPage.data.length > 0 ? (
            <>
              <CursorPagination
                page={runsPage.pageNumber}
                hasPrevious={runsPage.hasPrevious}
                hasNext={runsPage.hasNext}
                onPrevious={runsPage.previous}
                onNext={runsPage.next}
                label="服务 2 纳入运行分页"
              />
              <button type="button" className="secondary" onClick={() => void runsPage.refresh()}>
                刷新可选运行
              </button>
            </>
          ) : null}
        </fieldset>
        {notice ? (
          <p role="alert" className="service-note">
            {notice}
          </p>
        ) : null}
        <button
          type="button"
          disabled={!canControl || busy || selectedRuns.length === 0 || !selectedAnalysisModel}
          onClick={() => void createBatch()}
        >
          物化全部 U occurrence
        </button>
      </section>
    );
  }

  const coverage = batch.coverage;
  const cases = Array.isArray(manifest?.facts.cases)
    ? (manifest.facts.cases as Array<Record<string, unknown>>)
    : [];

  return (
    <div className="s2-workspace">
      <p className="service-note">
        服务 2 的总体是冻结范围内全部 U occurrence。网络抓取可按 canonical URL
        复用，但每条回答、查询和暴露位置都保留；归属只是一列证据，不是入池门槛。
      </p>
      {notice ? (
        <p role="alert" className="service-note">
          {notice}
        </p>
      ) : null}

      <section className="execution-card" aria-labelledby="s2-coverage-title">
        <div className="section-title">
          <h2 id="s2-coverage-title">1. 范围与覆盖</h2>
          <span>
            {batch.batch_pub_id} · {batch.status}
          </span>
        </div>
        <div className="s2-kpis">
          <div>
            <strong>{coverage.selected_queries}</strong>
            <span>终态查询</span>
          </div>
          <div>
            <strong>{coverage.successful_queries}</strong>
            <span>成功查询</span>
          </div>
          <div>
            <strong>{coverage.failed_queries}</strong>
            <span>失败查询缺口</span>
          </div>
          <div>
            <strong>{coverage.expected_occurrences}</strong>
            <span>全部 U occurrence</span>
          </div>
          <div>
            <strong>{coverage.distinct_urls}</strong>
            <span>distinct URL</span>
          </div>
          <div>
            <strong>{coverage.entered_judgment}</strong>
            <span>进入判定</span>
          </div>
          <div>
            <strong>{coverage.eligible_cases}</strong>
            <span>通过证据门案例</span>
          </div>
        </div>
        <dl className="s2-scope-meta">
          <div>
            <dt>冻结运行</dt>
            <dd>{batch.run_pub_ids.join('、') || '—'}</dd>
          </div>
          <div>
            <dt>核查窗口</dt>
            <dd>
              {batch.window_start} → {batch.window_end}
            </dd>
          </div>
          <div>
            <dt>U snapshot boundary</dt>
            <dd>{batch.source_snapshot_boundary}</dd>
          </div>
          <div>
            <dt>联网分析模型</dt>
            <dd>{batch.analysis_model}</dd>
          </div>
          <div>
            <dt>服务权益</dt>
            <dd>
              {batch.service_entitlement_pub_id} · {shortHash(batch.service_entitlement_revision)}
            </dd>
          </div>
        </dl>
        <p className={coverage.query_coverage_complete ? 'setup-summary' : 's2-coverage-gap'}>
          查询台账：{coverage.successful_queries} 成功 + {coverage.failed_queries} 失败 ={' '}
          {coverage.selected_queries} 已选查询
          {coverage.query_outcomes_complete ? '（完整）' : '（不完整）'}；成功查询中{' '}
          {coverage.successful_queries_with_u} 个产生 U，
          {coverage.successful_queries_without_u} 个合法地为 0 U。
          {coverage.failed_queries > 0
            ? ` ${coverage.failed_queries} 个失败查询仅记为覆盖缺口：${
                Object.entries(coverage.query_failure_codes)
                  .map(([code, count]) => `${code}=${count}`)
                  .join('、') || '未分类'
              }。`
            : ' 没有查询采集缺口。'}
        </p>
        <div className="s2-state-list" aria-label="全量处理状态">
          {Object.entries(coverage.processing_states).map(([state, count]) => (
            <span key={state}>
              {PROCESSING_LABELS[state] ?? state}：{count}
            </span>
          ))}
        </div>
        <p className="setup-summary">
          已物化 {coverage.materialized_items}/{coverage.expected_occurrences}；URL 去重数不会替代
          occurrence 分母。corpus policy：
          <code>{batch.corpus_policy_version}</code>；judgment policy：
          <code>{batch.judgment_policy_version}</code>。
        </p>
        {canControl ? (
          <div className="actions">
            {batch.status === 'draft' ? (
              <button type="button" disabled={busy} onClick={() => void lifecycle('start')}>
                启动
              </button>
            ) : null}
            {['queued', 'running'].includes(batch.status) ? (
              <button type="button" disabled={busy} onClick={() => void lifecycle('pause')}>
                暂停
              </button>
            ) : null}
            {batch.status === 'paused' ? (
              <button type="button" disabled={busy} onClick={() => void lifecycle('resume')}>
                恢复
              </button>
            ) : null}
            {['review', 'failed'].includes(batch.status) ? (
              <button type="button" disabled={busy} onClick={() => void lifecycle('retry')}>
                重试缺口
              </button>
            ) : null}
            {!['frozen', 'cancelled', 'cancel_requested'].includes(batch.status) ? (
              <button type="button" disabled={busy} onClick={() => void lifecycle('cancel')}>
                取消
              </button>
            ) : null}
            {batch.status === 'cancel_requested' ? <span>取消处理中…</span> : null}
            {['frozen', 'cancelled'].includes(batch.status) ? (
              <button type="button" disabled={busy} onClick={startNewBatchSetup}>
                建立新批次
              </button>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="execution-card" aria-labelledby="s2-corpus-title">
        <div className="section-title">
          <h2 id="s2-corpus-title">2. 全部帖子处理队列</h2>
          <span>
            当前筛选 {items?.filtered_count ?? 0} / 全部 U{' '}
            {items?.all_u_total ?? coverage.expected_occurrences}
          </span>
        </div>
        <div className="s2-filters">
          <label>
            处理状态
            <select
              value={processingFilter}
              onChange={(event) => setProcessingFilter(event.target.value)}
            >
              <option value="">全部</option>
              {Object.keys(PROCESSING_LABELS).map((state) => (
                <option key={state} value={state}>
                  {PROCESSING_LABELS[state]}
                </option>
              ))}
            </select>
          </label>
          <label>
            归属置信度
            <select
              value={attributionFilter}
              onChange={(event) => setAttributionFilter(event.target.value)}
            >
              <option value="">全部（默认）</option>
              <option value="verified">verified</option>
              <option value="probable">probable</option>
              <option value="weak">weak</option>
              <option value="unknown">unknown</option>
            </select>
          </label>
        </div>
        <div className="s2-table-scroll" role="region" tabIndex={0} aria-label="全部 U 帖子表">
          <table>
            <thead>
              <tr>
                <th>URL / 站点</th>
                <th>U 上下文</th>
                <th>抓取/处理</th>
                <th>实体/finding</th>
                <th>审核</th>
              </tr>
            </thead>
            <tbody>
              {items?.data.map((item) => (
                <tr key={item.item_pub_id}>
                  <td>
                    {safeHttpUrl(item.canonical_url) ? (
                      <a
                        href={safeHttpUrl(item.canonical_url) ?? undefined}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {item.canonical_url}
                      </a>
                    ) : (
                      <span>{item.canonical_url}</span>
                    )}
                    <small>{item.site_host}</small>
                  </td>
                  <td>
                    <strong>{item.question || '未记录问题'}</strong>
                    <small>
                      {item.platform} · {item.model} · {item.region}
                      <br />
                      occurrence {item.occurrence_ordinal} · rank {item.u_rank ?? '—'}
                    </small>
                  </td>
                  <td>
                    {item.fetch_state}
                    <small>
                      {PROCESSING_LABELS[item.processing_state] ?? item.processing_state}
                      {item.failure_code ? ` · ${item.failure_code}` : ''}
                    </small>
                  </td>
                  <td>
                    {item.entity_state}
                    <small>{item.finding_count} 个 finding</small>
                  </td>
                  <td>
                    {item.review_state}
                    <small>{item.manual_evidence_state}</small>
                  </td>
                </tr>
              ))}
              {items?.data.length === 0 ? (
                <tr>
                  <td colSpan={5}>当前筛选没有条目；全部 U 分母仍为 {items.all_u_total}。</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <div className="s2-pagination" aria-label="语料分页">
          <button
            type="button"
            disabled={itemPageIndex === 0}
            onClick={() => setItemPageIndex((value) => value - 1)}
          >
            上一页
          </button>
          <span>第 {itemPageIndex + 1} 页 · 每页 4 条</span>
          <button type="button" disabled={!items?.has_more} onClick={nextItems}>
            下一页
          </button>
        </div>
      </section>

      <section className="execution-card" aria-labelledby="s2-relations-title">
        <div className="section-title">
          <h2 id="s2-relations-title">3. 实体—关系发现</h2>
          <span>
            当前筛选 {findings?.filtered_count ?? 0} / 全部 finding{' '}
            {findings?.all_findings_total ?? coverage.findings}
          </span>
        </div>
        <label className="s2-inline-filter">
          审核状态
          <select value={reviewFilter} onChange={(event) => setReviewFilter(event.target.value)}>
            <option value="">全部</option>
            <option value="unreviewed">待审核</option>
            <option value="accepted">已接受</option>
            <option value="rejected">已拒绝</option>
            <option value="needs_changes">需修改</option>
          </select>
        </label>
        <div
          className="s2-table-scroll"
          role="region"
          tabIndex={0}
          aria-label="实体关系 finding 表"
        >
          <table>
            <thead>
              <tr>
                <th>关系</th>
                <th>等级/账本</th>
                <th>证据</th>
                <th>事实核查</th>
                <th>归属</th>
              </tr>
            </thead>
            <tbody>
              {findings?.data.map((finding) => (
                <tr
                  key={finding.finding_pub_id}
                  className={
                    selectedFindingId === finding.finding_pub_id ? 'is-selected' : undefined
                  }
                >
                  <td>
                    <button
                      type="button"
                      className="s2-row-button"
                      onClick={() => setSelectedFindingId(finding.finding_pub_id)}
                    >
                      {finding.textual_speaker || '页面叙述'} → {finding.target_entity}
                    </button>
                    <small>
                      {finding.relation_direction}
                      {finding.beneficiary_entity
                        ? ` · 受益/对照：${finding.beneficiary_entity}`
                        : ''}
                    </small>
                  </td>
                  <td>
                    {finding.level} · {finding.ledger}
                    <small>{finding.is_disparagement ? '拉踩' : '非拉踩核查信息'}</small>
                  </td>
                  <td>
                    {finding.validation_status}
                    <small>视觉：{finding.visual_validation_status}</small>
                  </td>
                  <td>{finding.factcheck_verdict ?? '未核实'}</td>
                  <td>
                    publisher {finding.publisher.confidence}
                    <small>commissioner {finding.commissioner.confidence}</small>
                  </td>
                </tr>
              ))}
              {findings?.data.length === 0 ? (
                <tr>
                  <td colSpan={5}>当前没有 finding。无实体帖子仍保留在覆盖分母。</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <div className="s2-pagination" aria-label="finding 分页">
          <button
            type="button"
            disabled={findingPageIndex === 0}
            onClick={() => setFindingPageIndex((value) => value - 1)}
          >
            上一页
          </button>
          <span>第 {findingPageIndex + 1} 页 · 每页 4 条</span>
          <button type="button" disabled={!findings?.has_more} onClick={nextFindings}>
            下一页
          </button>
        </div>
      </section>

      <section className="execution-card" aria-labelledby="s2-review-title">
        <div className="section-title">
          <h2 id="s2-review-title">4. 待审核 finding</h2>
          <span>文本判断、事实真假、发布/委托归属分栏</span>
        </div>
        {selectedFinding ? (
          <div className="s2-review-grid">
            <div>
              <h3>逐字证据</h3>
              <blockquote>{selectedFinding.evidence_quote}</blockquote>
              <p>{selectedFinding.context_text}</p>
              {safeHttpUrl(selectedFinding.canonical_url) ? (
                <a
                  className="s2-external-link"
                  href={safeHttpUrl(selectedFinding.canonical_url) ?? undefined}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  打开完整 URL
                </a>
              ) : (
                <p>原始 URL 不是可打开的 HTTP(S) 地址。</p>
              )}
              <FindingEvidenceImage session={session} finding={selectedFinding} />
              <small>
                snapshot {selectedFinding.snapshot_pub_id} ·{' '}
                {shortHash(selectedFinding.snapshot_text_sha256)} · offset{' '}
                {selectedFinding.quote_start}–{selectedFinding.quote_end}
              </small>
            </div>
            <div>
              <h3>判定与事实核查</h3>
              <dl>
                <dt>等级</dt>
                <dd>
                  {selectedFinding.level}（
                  {selectedFinding.is_disparagement ? '计拉踩' : '不计拉踩'}）
                </dd>
                <dt>方向</dt>
                <dd>{selectedFinding.relation_direction}</dd>
                <dt>事实核查</dt>
                <dd>{selectedFinding.factcheck_verdict ?? 'unverifiable / 未执行'}</dd>
                <dt>核查依据</dt>
                <dd>
                  <EvidenceReferences rows={selectedFinding.factcheck_evidence} />
                </dd>
                <dt>核查边界</dt>
                <dd>{selectedFinding.factcheck_boundary ?? '—'}</dd>
                <dt>事实锚点</dt>
                <dd>{selectedFinding.fact_anchor_state}</dd>
                <dt>视觉证据</dt>
                <dd>{selectedFinding.visual_validation_status}</dd>
              </dl>
            </div>
            <div>
              <h3>独立归属</h3>
              <dl>
                <dt>publisher</dt>
                <dd>
                  {attributionLabel(selectedFinding, 'publisher')}
                  <small>
                    证据：
                    <EvidenceReferences rows={selectedFinding.publisher.evidence} />
                  </small>
                </dd>
                <dt>commissioner</dt>
                <dd>
                  {attributionLabel(selectedFinding, 'commissioner')}
                  <small>
                    证据：
                    <EvidenceReferences rows={selectedFinding.commissioner.evidence} />
                  </small>
                </dd>
              </dl>
              <p>unknown 时不得输出“竞品委托”“受雇”“水军”或“有组织攻击”。</p>
            </div>
          </div>
        ) : (
          <p>选择一条 finding 查看证据；没有 finding 不代表帖子从 U 分母消失。</p>
        )}
        {selectedFinding && canReview ? (
          <div className="s2-review-actions">
            <label>
              审核理由
              <textarea
                value={reviewRationale}
                onChange={(event) => setReviewRationale(event.target.value)}
              />
            </label>
            <div className="actions">
              <button
                type="button"
                disabled={busy || !reviewRationale.trim()}
                onClick={() => void review('accepted')}
              >
                接受
              </button>
              <button
                type="button"
                disabled={busy || !reviewRationale.trim()}
                onClick={() => void review('needs_changes')}
              >
                需修改
              </button>
              <button
                type="button"
                disabled={busy || !reviewRationale.trim()}
                onClick={() => void review('rejected')}
              >
                拒绝
              </button>
            </div>
          </div>
        ) : null}
      </section>

      <section className="execution-card" aria-labelledby="s2-delivery-title">
        <div className="section-title">
          <h2 id="s2-delivery-title">5. 案例与交付</h2>
          <span>只展示通过逐字、视觉、事实核查与人审证据门的案例</span>
        </div>
        {manifest ? (
          <>
            <div className="s2-manifest">
              <strong>revision {manifest.revision}</strong>
              <code>{manifest.manifest_hash}</code>
              <span>
                {manifest.case_count} 个案例 · {manifest.evidence_reference_count} 个证据引用
              </span>
            </div>
            {cases.length ? (
              <ul className="s2-case-list">
                {cases.map((item, index) => {
                  const url = safeHttpUrl(item.canonical_url);
                  return (
                    <li key={String(item.finding_pub_id ?? index)}>
                      <strong>
                        {String(item.level ?? '')} · {String(item.target_entity ?? '')}
                      </strong>
                      <span>{String(item.evidence_quote ?? '')}</span>
                      <span>
                        事实核查：{String(item.factcheck_verdict ?? '未核实')}
                        {item.factcheck_boundary ? ` · ${String(item.factcheck_boundary)}` : ''}
                      </span>
                      {url ? (
                        <a
                          className="s2-external-link"
                          href={url}
                          target="_blank"
                          rel="noreferrer noopener"
                        >
                          打开完整 URL
                        </a>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p>冻结 manifest 中没有通过逐字、视觉、事实核查与人审证据门的客户案例。</p>
            )}
            <a
              className="button"
              href={`/platform/operations/formal-reports?project=${encodeURIComponent(project.pub_id)}`}
            >
              使用冻结 manifest 生成服务 2 报告
            </a>
          </>
        ) : (
          <>
            <p>
              当前尚未冻结。冻结不会联网或重新调用模型，只会把全量覆盖和审核通过案例写入不可变
              manifest。
            </p>
            {canReview ? (
              <button
                type="button"
                disabled={busy || batch.status !== 'review'}
                onClick={() => void freeze()}
              >
                冻结 Service 2 facts
              </button>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
}
