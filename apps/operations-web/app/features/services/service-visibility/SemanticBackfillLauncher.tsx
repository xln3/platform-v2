import { useEffect, useMemo, useState } from 'react';
import {
  servicesApi,
  type Project,
  type SemanticBackfillCandidate,
  type SemanticBackfillOptions,
  type SemanticBackfillPlan,
  type SemanticBackfillStart,
  type SemanticBackfillStatus,
  type SessionContext,
  type OfficialMetricSnapshotSet,
} from '../api';

type PlanState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; data: SemanticBackfillPlan }
  | { kind: 'error'; message: string };

const BLOCKER_LABELS: Record<string, string> = {
  answer_selection_changed: '所选答案已变化，请重新加载',
  answer_preparation_incomplete: '部分答案尚未具备可执行分析材料',
  no_executable_decisions: '没有可执行的语义判定',
  semantic_budget_disabled: '服务端预算闸门已关闭',
  estimated_cost_exceeds_budget: '费用上界超过服务端预算',
  focal_entity_dictionary_missing: '项目品牌/竞品实体词典不可用',
};

function money(value: number): string {
  return `$${value.toFixed(value < 0.01 ? 6 : 4)}`;
}

function deduplicateCandidates(
  current: SemanticBackfillCandidate[],
  incoming: SemanticBackfillCandidate[],
): SemanticBackfillCandidate[] {
  const rows = new Map(current.map((candidate) => [candidate.answer_pub_id, candidate]));
  for (const candidate of incoming) rows.set(candidate.answer_pub_id, candidate);
  return [...rows.values()];
}

export function SemanticBackfillLauncher({
  session,
  project,
}: {
  session: SessionContext;
  project: Project;
}) {
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<SemanticBackfillOptions | null>(null);
  const [candidates, setCandidates] = useState<SemanticBackfillCandidate[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [plan, setPlan] = useState<PlanState>({ kind: 'idle' });
  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);
  const [started, setStarted] = useState<SemanticBackfillStart | null>(null);
  const [runStatus, setRunStatus] = useState<SemanticBackfillStatus | null>(null);
  const [snapshot, setSnapshot] = useState<OfficialMetricSnapshotSet | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setOptions(null);
    setCandidates([]);
    setSelectedIds(new Set());
    setSelectedModel('');
    setNextCursor(null);
    setLoadError(null);
    setStarted(null);
    setRunStatus(null);
    setSnapshot(null);
    setConfirming(false);
    setLoading(true);
    void servicesApi.semanticBackfillOptions(session, {
      projectPubId: project.pub_id,
      limit: 100,
    }).then(
      (data) => {
        if (cancelled) return;
        const ready = data.candidates.filter((candidate) => candidate.preparation_state === 'ready');
        const recommended = data.models.find((model) => model.recommended)?.model;
        setOptions(data);
        setCandidates(data.candidates);
        setNextCursor(data.next_cursor);
        setSelectedModel(recommended ?? data.default_model);
        setSelectedIds(
          new Set(ready.slice(0, Math.min(10, data.max_batch_size)).map((row) => row.answer_pub_id)),
        );
        setLoading(false);
      },
      (error: unknown) => {
        if (cancelled) return;
        setLoadError(error instanceof Error ? error.message : 'semantic_backfill_options_failed');
        setLoading(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [open, project.pub_id, session]);

  useEffect(() => {
    if (!started) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const status = await servicesApi.semanticBackfillStatus(session, {
          projectPubId: project.pub_id,
          selectionHash: started.selection_hash,
        });
        if (cancelled) return;
        setRunStatus(status);
        if (status.status === 'succeeded' && status.snapshot_set_pub_id) {
          const result = await servicesApi.metricSnapshotSet(session, status.snapshot_set_pub_id);
          if (!cancelled) setSnapshot(result);
          return;
        }
        if (status.status === 'running') timer = window.setTimeout(() => void poll(), 3000);
      } catch (error) {
        if (!cancelled)
          setLoadError(error instanceof Error ? error.message : 'semantic_backfill_status_failed');
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [project.pub_id, session, started]);

  const selectedAnswerIds = useMemo(() => [...selectedIds].sort(), [selectedIds]);
  const readyCandidates = useMemo(
    () => candidates.filter((candidate) => candidate.preparation_state === 'ready'),
    [candidates],
  );
  const filteredCandidates = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase();
    if (!needle) return candidates;
    return candidates.filter((candidate) =>
      `${candidate.query_text} ${candidate.model} ${candidate.region} ${candidate.answer_pub_id}`
        .toLocaleLowerCase()
        .includes(needle),
    );
  }, [candidates, filter]);

  useEffect(() => {
    if (!open || !options || !selectedModel || selectedAnswerIds.length === 0) {
      setPlan({ kind: 'idle' });
      return;
    }
    let cancelled = false;
    setPlan({ kind: 'loading' });
    setConfirming(false);
    const timer = window.setTimeout(() => {
      void servicesApi
        .planSemanticBackfill(session, {
          projectPubId: project.pub_id,
          answerPubIds: selectedAnswerIds,
          model: selectedModel,
          asOf: options.as_of,
        })
        .then(
          (data) => {
            if (!cancelled) setPlan({ kind: 'ready', data });
          },
          (error: unknown) => {
            if (!cancelled)
              setPlan({
                kind: 'error',
                message: error instanceof Error ? error.message : 'semantic_backfill_plan_failed',
              });
          },
        );
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, options, project.pub_id, selectedAnswerIds, selectedModel, session]);

  function setBatchSize(size: number) {
    const maximum = options?.max_batch_size ?? 100;
    const bounded = Math.min(maximum, Math.max(1, size));
    setSelectedIds(new Set(readyCandidates.slice(0, bounded).map((row) => row.answer_pub_id)));
  }

  function toggleCandidate(candidate: SemanticBackfillCandidate) {
    if (candidate.preparation_state !== 'ready') return;
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(candidate.answer_pub_id)) next.delete(candidate.answer_pub_id);
      else if (next.size < (options?.max_batch_size ?? 100)) next.add(candidate.answer_pub_id);
      return next;
    });
  }

  async function loadMore() {
    if (!options || !nextCursor || loading) return;
    setLoading(true);
    setLoadError(null);
    try {
      const data = await servicesApi.semanticBackfillOptions(session, {
        projectPubId: project.pub_id,
        cursor: nextCursor,
        asOf: options.as_of,
        limit: 100,
      });
      setCandidates((current) => deduplicateCandidates(current, data.candidates));
      setNextCursor(data.next_cursor);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'semantic_backfill_options_failed');
    } finally {
      setLoading(false);
    }
  }

  async function start() {
    if (plan.kind !== 'ready' || !plan.data.start_allowed || starting || !options) return;
    setStarting(true);
    setLoadError(null);
    try {
      const result = await servicesApi.startSemanticBackfill(session, {
        projectPubId: project.pub_id,
        answerPubIds: selectedAnswerIds,
        model: selectedModel,
        asOf: options.as_of,
        selectionHash: plan.data.selection_hash,
        confirmationToken: plan.data.confirmation_token,
      });
      setStarted(result);
      setConfirming(false);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'semantic_backfill_start_failed');
      setConfirming(false);
    } finally {
      setStarting(false);
    }
  }

  return (
    <>
      <button type="button" className="semantic-backfill-open" onClick={() => setOpen(true)}>
        启动 official V2 回算
      </button>
      {open ? (
        <div className="semantic-backfill-overlay" role="presentation">
          <section
            className="semantic-backfill-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="official V2 历史回算控制台"
          >
            <header>
              <div>
                <h2>official V2 历史回算控制台</h2>
                <p>选择模型和本批答案；费用与选择均由服务端重新校验后再启动。</p>
              </div>
              <button
                type="button"
                aria-label="关闭回算控制台"
                onClick={() => setOpen(false)}
                disabled={starting}
              >
                关闭
              </button>
            </header>

            {loading && !options ? <p className="empty">正在加载可回算答案…</p> : null}
            {loadError ? <p className="semantic-backfill-error" role="alert">{`操作失败：${loadError}`}</p> : null}

            {options ? (
              <>
                <div className="semantic-backfill-controls">
                  <label>
                    判定模型
                    <select value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)}>
                      {options.models.map((model) => (
                        <option key={model.model} value={model.model}>
                          {`${model.label}${model.recommended ? '（低成本推荐）' : ''}`}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="semantic-model-pricing">
                    {options.models
                      .filter((model) => model.model === selectedModel)
                      .map((model) => (
                        <span key={model.model}>
                          {`${model.provider} · 模型 ID ${model.model} · 输入 ${money(model.input_usd_per_million_tokens)} / 百万 tokens；输出 ${money(model.output_usd_per_million_tokens)} / 百万 tokens`}
                        </span>
                      ))}
                  </div>
                  <label className="semantic-batch-slider">
                    <span>{`本批答案数：${selectedIds.size} / ${options.max_batch_size}`}</span>
                    <input
                      type="range"
                      min="1"
                      max={Math.max(1, Math.min(options.max_batch_size, readyCandidates.length))}
                      value={Math.max(1, selectedIds.size)}
                      onChange={(event) => setBatchSize(Number(event.target.value))}
                    />
                  </label>
                  <div className="actions">
                    <button
                      type="button"
                      onClick={() => setBatchSize(Math.min(options.max_batch_size, readyCandidates.length))}
                    >
                      一键选择当前已加载
                    </button>
                    <button type="button" onClick={() => setSelectedIds(new Set())}>
                      清空选择
                    </button>
                    <span>{`候选答案 ${options.candidate_count} 份，已加载 ${candidates.length} 份`}</span>
                  </div>
                  <label>
                    筛选问答
                    <input
                      type="search"
                      value={filter}
                      onChange={(event) => setFilter(event.target.value)}
                      placeholder="搜索问题、原采集模型或答案 ID"
                    />
                  </label>
                </div>

                <div className="semantic-candidate-scroll" aria-label="可纳入回算的问答滚动列表">
                  {filteredCandidates.map((candidate) => (
                    <label
                      key={candidate.answer_pub_id}
                      className={candidate.preparation_state === 'ready' ? '' : 'unavailable'}
                    >
                      <input
                        type="checkbox"
                        checked={selectedIds.has(candidate.answer_pub_id)}
                        disabled={candidate.preparation_state !== 'ready'}
                        onChange={() => toggleCandidate(candidate)}
                      />
                      <span>
                        <strong>{candidate.query_text || '（问题文本缺失）'}</strong>
                        <small>{`${candidate.model} · ${candidate.region} · ${new Date(candidate.capture_time).toLocaleString('zh-CN')}`}</small>
                        {candidate.preparation_state !== 'ready' ? (
                          <small>{`暂不可执行：${candidate.reason_codes.join('、')}`}</small>
                        ) : null}
                      </span>
                    </label>
                  ))}
                </div>
                {nextCursor ? (
                  <button type="button" onClick={() => void loadMore()} disabled={loading}>
                    {loading ? '正在加载…' : '继续加载 100 份'}
                  </button>
                ) : null}

                <div className="semantic-backfill-budget" aria-live="polite">
                  {plan.kind === 'loading' ? <p>正在计算费用与预算上界…</p> : null}
                  {plan.kind === 'error' ? <p role="alert">{`预算评估失败：${plan.message}`}</p> : null}
                  {plan.kind === 'idle' ? <p>请选择至少 1 份可执行答案。</p> : null}
                  {plan.kind === 'ready' ? (
                    <>
                      <div>
                        <span>纳入答案</span>
                        <strong>{plan.data.executable_answer_count}</strong>
                      </div>
                      <div>
                        <span>基础原子判定</span>
                        <strong>{plan.data.estimated_atomic_decisions.toLocaleString('zh-CN')}</strong>
                      </div>
                      <div>
                        <span>费用估算 / 上界</span>
                        <strong>{`${money(plan.data.estimated_cost_usd)} / ${money(plan.data.estimated_cost_high_usd)}`}</strong>
                      </div>
                      <div>
                        <span>服务端预算闸门</span>
                        <strong>{money(plan.data.budget_limit_usd)}</strong>
                      </div>
                      {plan.data.blocker_codes.length ? (
                        <p role="alert">
                          {plan.data.blocker_codes
                            .map((code) => BLOCKER_LABELS[code] ?? code)
                            .join('；')}
                        </p>
                      ) : (
                        <p>费用为有界估算，最终以供应商账单和实际动态判定次数为准。</p>
                      )}
                    </>
                  ) : null}
                </div>

                {started ? (
                  <div className="semantic-backfill-result" role="status">
                    <p className="semantic-backfill-success">
                      {runStatus?.status === 'succeeded'
                        ? `回算完成：${runStatus.processed_answer_count} 份答案，生成 ${runStatus.metric_evaluation_count} 条指标评价。`
                        : runStatus?.status === 'failed'
                          ? `回算失败：${runStatus.failure_code ?? 'semantic_backfill_workflow_failed'}`
                          : `已${started.status === 'reused' ? '复用' : '启动'}：${started.selected_answer_count} 份答案，模型 ${started.model}；正在依次执行语义判定、确定性指标聚合和快照生成。`}
                    </p>
                    {snapshot ? (
                      <details open>
                        <summary>{`本次不可变 V2 快照：${snapshot.metrics.length} 条逐实体指标结果`}</summary>
                        <div className="table-scroll">
                          <table aria-label="本次 V2 回算快照指标">
                            <thead>
                              <tr>
                                <th>实体 ID</th>
                                <th>指标</th>
                                <th>结果</th>
                                <th>纳入答案</th>
                                <th>状态</th>
                              </tr>
                            </thead>
                            <tbody>
                              {snapshot.metrics.slice(0, 20).map((metric) => (
                                <tr key={metric.snapshot_pub_id}>
                                  <td>{metric.focal_entity_id}</td>
                                  <td>{metric.metric_name}</td>
                                  <td>
                                    {metric.value === null || metric.value === undefined
                                      ? '—'
                                      : metric.metric_name.includes('rank') &&
                                          !metric.metric_name.includes('rate')
                                        ? metric.value.toFixed(2)
                                        : `${(metric.value * 100).toFixed(1)}%`}
                                  </td>
                                  <td>{metric.known_answer_count}</td>
                                  <td>{metric.state}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        {snapshot.metrics.length > 20 ? (
                          <p>{`弹窗先展示 20 条；完整快照共 ${snapshot.metrics.length} 条。`}</p>
                        ) : null}
                      </details>
                    ) : null}
                  </div>
                ) : confirming && plan.kind === 'ready' ? (
                  <div className="semantic-backfill-confirm" role="alert">
                    <p>{`确认启动 ${plan.data.executable_answer_count} 份答案、约 ${plan.data.estimated_atomic_decisions.toLocaleString('zh-CN')} 次基础判定？费用上界 ${money(plan.data.estimated_cost_high_usd)}。`}</p>
                    <div className="actions">
                      <button type="button" onClick={() => setConfirming(false)} disabled={starting}>
                        返回检查
                      </button>
                      <button type="button" className="danger" onClick={() => void start()} disabled={starting}>
                        {starting ? '正在启动…' : '确认启动回算'}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="semantic-backfill-footer">
                    <button
                      type="button"
                      onClick={() => setConfirming(true)}
                      disabled={plan.kind !== 'ready' || !plan.data.start_allowed}
                    >
                      检查完成，进入二次确认
                    </button>
                  </div>
                )}
              </>
            ) : null}
          </section>
        </div>
      ) : null}
    </>
  );
}
