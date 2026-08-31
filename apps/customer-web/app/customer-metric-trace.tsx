import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import './customer-metric-trace.css';

export type MetricV2State = 'ready' | 'limited' | 'insufficient' | 'experimental' | 'failed';

export type MetricV2Summary = {
  snapshot_pub_id: string;
  snapshot_hash: string;
  metric_name: string;
  metric_version: string;
  state: MetricV2State;
  state_reason_codes: readonly string[];
  value: number | null;
  observed_value: number | null;
  answer_weighted_value: number | null;
  raw_numerator: number;
  raw_denominator: number;
  weighted_numerator: number;
  weighted_denominator: number;
  unique_query_count: number;
  candidate_answer_count: number;
  known_answer_count: number;
  unknown_answer_count: number;
  failed_answer_count: number;
  not_applicable_answer_count: number;
  excluded_answer_count: number;
  design_cell_count: number;
  coverage: {
    collection: number | null;
    query_context: number | null;
    semantic: number | null;
    evidence: number | null;
    semantic_by_capability: Record<string, number>;
  };
  adjudication_sensitivity: { lower: number | null; upper: number | null };
  missing_bounds: { lower: number | null; upper: number | null };
  decision_method_mix: Record<string, number>;
  contribution_set_hash: string;
  query_contribution_set_hash: string;
  design_contribution_set_hash: string;
};

export type MetricV2Definition = {
  business_question: string;
  denominator_description: string;
  outcome_source: 'deterministic_expression' | 'semantic_decision' | 'hybrid';
  query_predicate: Record<string, unknown>;
  outcome_expression: Record<string, unknown>;
  required_semantic_capabilities: readonly string[];
  decision_task_refs: readonly { name?: string; version?: string; task_ref?: string }[];
  semantic_rubric_ref?: string | null;
};

export type MetricV2Event = {
  event_pub_id: string;
  event_type: string;
  event_value: Record<string, unknown>;
  answer_excerpt: string | null;
  answer_text_start: number | null;
  answer_text_end: number | null;
};

export type MetricV2Decision = {
  decision_pub_id: string;
  decision_hash: string;
  task: string;
  version: string;
  method: 'deterministic' | 'model' | 'hybrid' | 'human';
  status: 'accepted' | 'abstained' | 'review_required' | 'failed';
  rationale_summary: string | null;
  calibrated_confidence: number | null;
  rubric_hash: string;
  result: Record<string, unknown>;
  reason_codes?: readonly string[];
  evidence_refs?: readonly Record<string, unknown>[];
};

export type MetricV2DecisionCorrectionRequest = {
  decisionPubId: string;
  expectedDecisionHash: string;
  result: Record<string, unknown>;
  rationaleSummary: string;
};

export type MetricV2DecisionCorrectionResult =
  | { kind: 'submitted'; recomputeJobPubId: string }
  | { kind: 'conflict' | 'forbidden' | 'unavailable' };

export type MetricV2Contribution = {
  answer_pub_id: string;
  query_key: string;
  query_text: string | null;
  analysis_lenses: readonly string[];
  requested_operations: readonly string[];
  exposure_role: string;
  model: string;
  region: string;
  mode: string;
  eligibility_status:
    | 'included_hit'
    | 'included_miss'
    | 'excluded'
    | 'not_applicable'
    | 'analysis_unknown'
    | 'analysis_failed';
  reason_codes: readonly string[];
  numerator_contribution: number;
  denominator_contribution: number;
  query_weight: number;
  design_cell_weight: number;
  repeat_weight: number;
  final_weight: number;
  weighted_numerator: number;
  weighted_denominator: number;
  supporting_events: readonly MetricV2Event[];
  supporting_decisions: readonly MetricV2Decision[];
  answer_excerpt: string | null;
  answer_detail_href: string;
};

export type MetricV2ContributionPage = {
  snapshot_pub_id: string;
  snapshot_candidate_count: number;
  filtered_count: number;
  data: readonly MetricV2Contribution[];
  next_cursor: string | null;
  has_more: boolean;
};

export type CustomerMetricTraceProps = {
  snapshotSetId: string;
  snapshotSetHash: string;
  metric: MetricV2Summary;
  definition: MetricV2Definition;
  loadContributions: (
    snapshotPubId: string,
    cursor: string | null,
  ) => Promise<MetricV2ContributionPage>;
  correctDecision: (
    request: MetricV2DecisionCorrectionRequest,
  ) => Promise<MetricV2DecisionCorrectionResult>;
  onClose: () => void;
};

const stateLabels: Record<MetricV2State, string> = {
  ready: '可正式使用',
  limited: '范围有限',
  insufficient: '信息不足',
  experimental: '实验指标',
  failed: '计算失败',
};

const eligibilityLabels: Record<MetricV2Contribution['eligibility_status'], string> = {
  included_hit: '命中',
  included_miss: '未命中',
  excluded: '排除',
  not_applicable: '不适用',
  analysis_unknown: '内容证据不足，当前无法判断',
  analysis_failed: '系统分析失败',
};

const includesAnyReason = (reasonCodes: readonly string[], fragments: readonly string[]): boolean =>
  reasonCodes.some((reason) =>
    fragments.some((fragment) => reason.toLowerCase().includes(fragment)),
  );

const infrastructureFailureLabel = (reasonCodes: readonly string[]): string => {
  if (includesAnyReason(reasonCodes, ['auth', 'credential', 'config', 'api_key_missing'])) {
    return 'LLM API 未配置';
  }
  if (includesAnyReason(reasonCodes, ['rate_limit'])) {
    return 'LLM API 限流';
  }
  if (includesAnyReason(reasonCodes, ['timeout'])) {
    return 'LLM API 超时';
  }
  if (includesAnyReason(reasonCodes, ['budget'])) {
    return 'LLM 调用预算不足';
  }
  return 'LLM API 不可用';
};

const contributionStateLabel = (row: MetricV2Contribution): string => {
  if (row.eligibility_status !== 'analysis_failed') {
    return eligibilityLabels[row.eligibility_status];
  }
  const decisionReasonCodes = row.supporting_decisions.flatMap(
    (decision) => decision.reason_codes ?? [],
  );
  const diagnosticReasonCodes =
    decisionReasonCodes.length > 0 ? decisionReasonCodes : row.reason_codes;
  return diagnosticReasonCodes.some((reason) =>
    reason.toLowerCase().startsWith('llm_api_'),
  )
    ? infrastructureFailureLabel(diagnosticReasonCodes)
    : eligibilityLabels.analysis_failed;
};

const reasonCodeLabel = (reason: string): string => {
  const normalized = reason.toLowerCase();
  if (normalized.includes('unknown')) return '内容证据不足';
  if (normalized.startsWith('llm_api_')) return infrastructureFailureLabel([normalized]);
  return reason;
};

const percent = (value: number | null): string =>
  value === null ? '—' : `${(value * 100).toFixed(1)}%`;

const isRatioMetric = (metricName: string): boolean =>
  metricName.endsWith('_rate_v2') || metricName.endsWith('_share_v2');

const metricValue = (metricName: string, value: number | null): string =>
  value === null ? '—' : isRatioMetric(metricName) ? percent(value) : value.toFixed(2);

const decisionMethodLabel = (decision: MetricV2Decision): string => {
  if (decision.method === 'human') return '用户纠错';
  if (decision.method === 'deterministic') return '规则自动判定';
  return decision.calibrated_confidence === null
    ? '模型自动判定'
    : `模型自动判定 · 置信度 ${percent(decision.calibrated_confidence)}`;
};

const shortHash = (value: string): string => `${value.slice(0, 12)}…${value.slice(-8)}`;

function TraceSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="geo-metric-trace__section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

const correctionFailureCopy: Record<
  Exclude<MetricV2DecisionCorrectionResult['kind'], 'submitted'>,
  string
> = {
  conflict: '这条判定已被其他纠错更新。请关闭明细并重新打开后再试。',
  forbidden: '当前账号无权纠正这条判定。',
  unavailable: '纠错提交失败，请稍后重试。',
};

function DecisionCorrectionForm({
  decision,
  submit,
  cancel,
}: {
  decision: MetricV2Decision;
  submit: CustomerMetricTraceProps['correctDecision'];
  cancel: () => void;
}) {
  const resultInput = useRef<HTMLTextAreaElement>(null);
  const [resultText, setResultText] = useState(() => JSON.stringify(decision.result, null, 2));
  const [rationale, setRationale] = useState('');
  const [state, setState] = useState<'editing' | 'submitting' | 'submitted'>('editing');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    resultInput.current?.focus();
  }, []);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    let result: unknown;
    try {
      result = JSON.parse(resultText);
    } catch {
      setError('修正后的判定必须是有效的 JSON 对象。');
      return;
    }
    if (typeof result !== 'object' || result === null || Array.isArray(result)) {
      setError('修正后的判定必须是 JSON 对象，不能是数组或单个值。');
      return;
    }
    const normalizedRationale = rationale.trim();
    if (!normalizedRationale) {
      setError('请说明为什么原判定不正确。');
      return;
    }
    setState('submitting');
    const response = await submit({
      decisionPubId: decision.decision_pub_id,
      expectedDecisionHash: decision.decision_hash,
      result: result as Record<string, unknown>,
      rationaleSummary: normalizedRationale,
    });
    if (response.kind === 'submitted') {
      setState('submitted');
      return;
    }
    setState('editing');
    setError(correctionFailureCopy[response.kind]);
  };

  const submitted = state === 'submitted';
  return (
    <form
      id={`decision-correction-${decision.decision_pub_id}`}
      className="geo-metric-trace__correction"
      aria-label={`纠正 ${decision.task} 判定`}
      onSubmit={(event) => void onSubmit(event)}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && state !== 'submitting') {
          event.stopPropagation();
          cancel();
        }
      }}
    >
      <p>
        只修正这一条具体事实。系统仍会自动判定其他内容；提交后会保留原记录，并自动重算受影响指标。
      </p>
      <label htmlFor={`decision-result-${decision.decision_pub_id}`}>修正后的结构化判定</label>
      <textarea
        ref={resultInput}
        id={`decision-result-${decision.decision_pub_id}`}
        value={resultText}
        rows={8}
        spellCheck={false}
        disabled={state !== 'editing'}
        onChange={(event) => setResultText(event.target.value)}
      />
      <small>已预填当前判定结果。请只改正有误字段，并保持 JSON 对象格式。</small>
      <label htmlFor={`decision-rationale-${decision.decision_pub_id}`}>纠错理由</label>
      <textarea
        id={`decision-rationale-${decision.decision_pub_id}`}
        value={rationale}
        rows={3}
        maxLength={2000}
        disabled={state !== 'editing'}
        placeholder="例如：原文表达的是并列推荐，不应判定为第一名。"
        onChange={(event) => setRationale(event.target.value)}
      />
      {error ? <p role="alert">{error}</p> : null}
      {submitted ? (
        <p role="status">纠错已提交，受影响指标正在自动重算。当前冻结快照不会被改写。</p>
      ) : null}
      <div className="geo-metric-trace__correction-actions">
        {submitted ? (
          <button type="button" onClick={cancel}>
            完成
          </button>
        ) : (
          <>
            <button type="submit" disabled={state === 'submitting'}>
              {state === 'submitting' ? '正在提交…' : '提交纠错并重算'}
            </button>
            <button type="button" disabled={state === 'submitting'} onClick={cancel}>
              取消
            </button>
          </>
        )}
      </div>
    </form>
  );
}

function ContributionTable({ rows }: { rows: readonly MetricV2Contribution[] }) {
  return (
    <div className="geo-metric-trace__table-wrap" tabIndex={0}>
      <table>
        <thead>
          <tr>
            <th>查询 / 回答</th>
            <th>状态</th>
            <th>原因</th>
            <th>原始贡献</th>
            <th>最终权重</th>
            <th>加权贡献</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.answer_pub_id}>
              <td>
                <strong>{row.query_text ?? row.query_key}</strong>
                <small>
                  {row.model} · {row.region} · {row.mode} ·{' '}
                  <a href={row.answer_detail_href}>{row.answer_pub_id}</a>
                </small>
                <small>
                  视角 {row.analysis_lenses.join('、') || '—'} · 操作{' '}
                  {row.requested_operations.join('、') || '—'} · 暴露 {row.exposure_role}
                </small>
              </td>
              <td>
                <span data-state={row.eligibility_status}>{contributionStateLabel(row)}</span>
              </td>
              <td>{row.reason_codes.map(reasonCodeLabel).join('、')}</td>
              <td>
                {row.numerator_contribution}/{row.denominator_contribution}
              </td>
              <td>
                <span>query {row.query_weight.toFixed(6)}</span>
                <small>
                  design {row.design_cell_weight.toFixed(6)} × repeat {row.repeat_weight.toFixed(6)}{' '}
                  = final {row.final_weight.toFixed(6)}
                </small>
              </td>
              <td>
                {row.weighted_numerator.toFixed(6)}/{row.weighted_denominator.toFixed(6)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CustomerMetricTrace({
  snapshotSetId,
  snapshotSetHash,
  metric,
  definition,
  loadContributions,
  correctDecision,
  onClose,
}: CustomerMetricTraceProps) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const [rows, setRows] = useState<readonly MetricV2Contribution[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [candidateCount, setCandidateCount] = useState(metric.candidate_answer_count);
  const [filteredCount, setFilteredCount] = useState(0);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [correctingDecisionId, setCorrectingDecisionId] = useState<string | null>(null);

  const load = async (nextCursor: string | null, append: boolean) => {
    setState('loading');
    try {
      const page = await loadContributions(metric.snapshot_pub_id, nextCursor);
      if (page.snapshot_pub_id !== metric.snapshot_pub_id) throw new Error('snapshot drift');
      setRows((current) => (append ? [...current, ...page.data] : page.data));
      setCursor(page.next_cursor);
      setHasMore(page.has_more);
      setCandidateCount(page.snapshot_candidate_count);
      setFilteredCount(page.filtered_count);
      setState('ready');
    } catch {
      setState('failed');
    }
  };

  useEffect(() => {
    closeButton.current?.focus();
    void load(null, false);
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
    // The immutable snapshot id is the cache/load identity. Parent callbacks do
    // not cause a re-read of the same frozen trace.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metric.snapshot_pub_id]);

  const included = rows.filter(
    (row) =>
      row.eligibility_status === 'included_hit' || row.eligibility_status === 'included_miss',
  );
  const omitted = rows.filter(
    (row) =>
      row.eligibility_status !== 'included_hit' && row.eligibility_status !== 'included_miss',
  );
  const decisions = [
    ...new Map(
      rows
        .flatMap((row) => row.supporting_decisions)
        .map((decision) => [decision.decision_pub_id, decision]),
    ).values(),
  ];
  const events = rows.flatMap((row) => row.supporting_events);

  return (
    <div className="geo-metric-trace" role="dialog" aria-modal="true" aria-labelledby="trace-title">
      <header>
        <div>
          <span>Metric trace · {metric.metric_version}</span>
          <h2 id="trace-title">{metric.metric_name}</h2>
          <p>{definition.business_question}</p>
        </div>
        <button ref={closeButton} type="button" onClick={onClose} aria-label="关闭计算明细">
          关闭
        </button>
      </header>

      <div className="geo-metric-trace__summary">
        <div>
          <span>主值</span>
          <strong>{metricValue(metric.metric_name, metric.value)}</strong>
          <small>
            {stateLabels[metric.state]}
            {metric.state_reason_codes.length > 0
              ? ` · ${metric.state_reason_codes.join('、')}`
              : ''}
          </small>
        </div>
        <div>
          <span>原始分子 / 分母</span>
          <strong>
            {metric.raw_numerator}/{metric.raw_denominator}
          </strong>
          <small>查询等权 · {metric.unique_query_count} 个查询</small>
        </div>
        <div>
          <span>语义覆盖率</span>
          <strong>{percent(metric.coverage.semantic)}</strong>
          <small>
            内容证据不足 {metric.unknown_answer_count} · 系统异常 {metric.failed_answer_count}
          </small>
        </div>
        <div>
          <span>缺失结果界限</span>
          <strong>
            {metricValue(metric.metric_name, metric.missing_bounds.lower)}–
            {metricValue(metric.metric_name, metric.missing_bounds.upper)}
          </strong>
          <small>不等同于判定误差区间</small>
        </div>
      </div>

      <TraceSection title="口径">
        <p>{definition.denominator_description}</p>
        <dl>
          <div>
            <dt>结果来源</dt>
            <dd>{definition.outcome_source}</dd>
          </div>
          <div>
            <dt>指标版本</dt>
            <dd>{metric.metric_version}</dd>
          </div>
          <div>
            <dt>查询谓词</dt>
            <dd>
              <code>{JSON.stringify(definition.query_predicate)}</code>
            </dd>
          </div>
          <div>
            <dt>结果表达式</dt>
            <dd>
              <code>{JSON.stringify(definition.outcome_expression)}</code>
            </dd>
          </div>
        </dl>
      </TraceSection>

      <TraceSection title="回答贡献（完整分母）">
        <p>
          快照候选 {candidateCount} 条；当前明细 {filteredCount} 条。筛选或分页不会改变快照合计。
        </p>
        {state === 'loading' && rows.length === 0 ? <p role="status">正在读取冻结明细…</p> : null}
        {state === 'failed' ? (
          <div role="alert">
            明细暂时不可用。
            <button type="button" onClick={() => void load(null, false)}>
              重试
            </button>
          </div>
        ) : null}
        {rows.length > 0 ? <ContributionTable rows={rows} /> : null}
        {hasMore ? (
          <button
            type="button"
            disabled={state === 'loading'}
            onClick={() => void load(cursor, true)}
          >
            加载更多回答
          </button>
        ) : null}
      </TraceSection>

      <TraceSection title="未计入、证据不足与系统异常">
        <p>
          排除 {metric.excluded_answer_count}；不适用 {metric.not_applicable_answer_count}
          ；内容证据不足 {metric.unknown_answer_count}；系统异常 {metric.failed_answer_count}
          。两者独立计数，具体原因见明细。
        </p>
        {omitted.length > 0 ? (
          <ContributionTable rows={omitted} />
        ) : (
          <p>当前已加载页没有未计入项。</p>
        )}
      </TraceSection>

      <TraceSection title="判定任务与方法">
        <p>
          必需能力：{definition.required_semantic_capabilities.join('、') || '无'}；rubric：
          {definition.semantic_rubric_ref ?? '无专属 rubric'}。
        </p>
        <ul>
          {decisions.map((decision) => (
            <li key={decision.decision_pub_id}>
              <strong>
                {decision.task}@{decision.version}
              </strong>{' '}
              · {decision.method} · {decision.status} · {decisionMethodLabel(decision)}
              {decision.rationale_summary ? <p>{decision.rationale_summary}</p> : null}
              <small title={decision.rubric_hash}>
                rubric {shortHash(decision.rubric_hash)} · 证据引用{' '}
                {decision.evidence_refs?.length ?? 0} 条
              </small>
              {(decision.reason_codes?.length ?? 0) > 0 ? (
                <small>判定原因 {decision.reason_codes?.map(reasonCodeLabel).join('、')}</small>
              ) : null}
              {(decision.evidence_refs?.length ?? 0) > 0 ? (
                <details>
                  <summary>展开 decision evidence 引用</summary>
                  <code>{JSON.stringify(decision.evidence_refs)}</code>
                </details>
              ) : null}
              {decision.status === 'accepted' ? (
                <button
                  type="button"
                  aria-expanded={correctingDecisionId === decision.decision_pub_id}
                  aria-controls={`decision-correction-${decision.decision_pub_id}`}
                  onClick={() =>
                    setCorrectingDecisionId((current) =>
                      current === decision.decision_pub_id ? null : decision.decision_pub_id,
                    )
                  }
                >
                  纠错
                </button>
              ) : null}
              {correctingDecisionId === decision.decision_pub_id ? (
                <DecisionCorrectionForm
                  decision={decision}
                  submit={correctDecision}
                  cancel={() => setCorrectingDecisionId(null)}
                />
              ) : null}
            </li>
          ))}
        </ul>
      </TraceSection>

      <TraceSection title="原文证据">
        {events.length === 0 ? <p>当前已加载页没有命中事件证据。</p> : null}
        {events.map((event) => (
          <blockquote key={event.event_pub_id}>
            <strong>{event.event_type}</strong>
            <p>{event.answer_excerpt ?? '证据区间不可见'}</p>
            <small>
              Unicode code point [{event.answer_text_start ?? '—'}, {event.answer_text_end ?? '—'})
            </small>
          </blockquote>
        ))}
      </TraceSection>

      <TraceSection title="采集设计与验算">
        <dl>
          <div>
            <dt>候选 / 已知</dt>
            <dd>
              {metric.candidate_answer_count}/{metric.known_answer_count}
            </dd>
          </div>
          <div>
            <dt>加权分子 / 分母</dt>
            <dd>
              {metric.weighted_numerator}/{metric.weighted_denominator}
            </dd>
          </div>
          <div>
            <dt>回答加权诊断值</dt>
            <dd>{metricValue(metric.metric_name, metric.answer_weighted_value)}</dd>
          </div>
          <div>
            <dt>裁决敏感性</dt>
            <dd>
              {metricValue(metric.metric_name, metric.adjudication_sensitivity.lower)}–
              {metricValue(metric.metric_name, metric.adjudication_sensitivity.upper)}
            </dd>
          </div>
          <div>
            <dt>设计单元</dt>
            <dd>{metric.design_cell_count}</dd>
          </div>
          <div>
            <dt>快照集</dt>
            <dd>{snapshotSetId}</dd>
          </div>
          <div>
            <dt>集合哈希</dt>
            <dd className="geo-metric-trace__hash">{snapshotSetHash}</dd>
          </div>
          <div>
            <dt>贡献哈希</dt>
            <dd title={metric.contribution_set_hash}>{shortHash(metric.contribution_set_hash)}</dd>
          </div>
          <div>
            <dt>指标快照哈希</dt>
            <dd className="geo-metric-trace__hash">{metric.snapshot_hash}</dd>
          </div>
          <div>
            <dt>查询贡献哈希</dt>
            <dd title={metric.query_contribution_set_hash}>
              {shortHash(metric.query_contribution_set_hash)}
            </dd>
          </div>
          <div>
            <dt>设计贡献哈希</dt>
            <dd title={metric.design_contribution_set_hash}>
              {shortHash(metric.design_contribution_set_hash)}
            </dd>
          </div>
        </dl>
      </TraceSection>
    </div>
  );
}

export function CustomerMetricV2Card({
  label,
  metric,
  definition,
  aggregationMethod = 'query_macro',
  snapshotSetId,
  snapshotSetHash,
  onInspect,
}: {
  label: string;
  metric: MetricV2Summary;
  definition?: MetricV2Definition;
  aggregationMethod?: 'query_macro';
  snapshotSetId?: string;
  snapshotSetHash?: string;
  onInspect: () => void;
}) {
  return (
    <article className="geo-metric-v2-card">
      <header>
        <div>
          <h3>{label}</h3>
          <small>{metric.metric_name}</small>
        </div>
        <span data-state={metric.state}>{stateLabels[metric.state]}</span>
      </header>
      <strong>{metricValue(metric.metric_name, metric.value)}</strong>
      {definition ? <p>{definition.business_question}</p> : null}
      <p>
        {metric.raw_numerator}/{metric.raw_denominator} · {metric.unique_query_count} 个查询 ·
        语义覆盖 {percent(metric.coverage.semantic)}
      </p>
      <small>
        {aggregationMethod} · {definition?.outcome_source ?? '版本化判定来源'} · 采集覆盖{' '}
        {percent(metric.coverage.collection)}
      </small>
      <small>
        {stateLabels[metric.state]}
        {metric.state_reason_codes.length > 0
          ? ` · ${metric.state_reason_codes.join('、')}`
          : ' · 无状态原因码'}
      </small>
      {snapshotSetId && snapshotSetHash ? (
        <small title={snapshotSetHash}>
          set {snapshotSetId} · hash {shortHash(snapshotSetHash)} · snapshot{' '}
          {metric.snapshot_pub_id} · {shortHash(metric.snapshot_hash)}
        </small>
      ) : null}
      <button type="button" onClick={onInspect}>
        查看计算明细
      </button>
    </article>
  );
}

export function RecommendationTopKGroup({
  visibility,
  rankable,
  conditional,
  definitions,
  snapshotSetId,
  snapshotSetHash,
  onInspect,
}: {
  visibility: MetricV2Summary;
  rankable: MetricV2Summary;
  conditional: MetricV2Summary;
  definitions?: Readonly<Record<string, MetricV2Definition>>;
  snapshotSetId?: string;
  snapshotSetHash?: string;
  onInspect: (metric: MetricV2Summary) => void;
}) {
  return (
    <section className="geo-metric-v2-topk" aria-label="Top3 完整指标组">
      <CustomerMetricV2Card
        label="中性 AI 推荐 Top3 可见率（全部回答）"
        metric={visibility}
        {...(definitions?.[visibility.metric_name]
          ? { definition: definitions[visibility.metric_name] }
          : {})}
        {...(snapshotSetId && snapshotSetHash ? { snapshotSetId, snapshotSetHash } : {})}
        onInspect={() => onInspect(visibility)}
      />
      <CustomerMetricV2Card
        label="可排序回答覆盖率"
        metric={rankable}
        {...(definitions?.[rankable.metric_name]
          ? { definition: definitions[rankable.metric_name] }
          : {})}
        {...(snapshotSetId && snapshotSetHash ? { snapshotSetId, snapshotSetHash } : {})}
        onInspect={() => onInspect(rankable)}
      />
      <CustomerMetricV2Card
        label="Top3 率（仅可排序回答）"
        metric={conditional}
        {...(definitions?.[conditional.metric_name]
          ? { definition: definitions[conditional.metric_name] }
          : {})}
        {...(snapshotSetId && snapshotSetHash ? { snapshotSetId, snapshotSetHash } : {})}
        onInspect={() => onInspect(conditional)}
      />
    </section>
  );
}
