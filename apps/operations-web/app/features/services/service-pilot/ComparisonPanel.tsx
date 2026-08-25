import { CursorPagination } from '@geo/design-system';
import { useCallback, useEffect, useState } from 'react';
import { useCursorCollection } from '../../../pagination';
import { SERVICE_PILOT_PAGE_SIZE } from '../pagination-policy';
import { executionApi, type Run } from '../../execution/api';
import {
  ServicesApiError,
  servicesApi,
  type ComparisonMetricName,
  type Project,
  type RunComparison,
  type RunComparisonDetail,
  type SessionContext,
} from '../api';

type RunRow = Run & { created_at?: string | null };

type DetailState =
  | { id: string; kind: 'loading' }
  | { id: string; kind: 'ready'; data: RunComparisonDetail }
  | { id: string; kind: 'error'; message: string };

const AGGREGATE_LABELS: Record<ComparisonMetricName, string> = {
  mention_rate: '品牌提及率',
  avg_rank: '平均排名',
  top1: 'Top1 出现率',
  top3: 'Top3 出现率',
  top5: 'Top5 出现率',
};

// 已知 insufficient 原因的中文对照；表外原因原样展示（诚实口径）。
const REASON_LABELS: Record<string, string> = {
  before_no_answers: '基线臂无答案',
  after_no_answers: '优化后臂无答案',
  before_no_extraction_coverage: '基线臂无抽取覆盖',
  after_no_extraction_coverage: '优化后臂无抽取覆盖',
  target_brand_unset: '目标品牌未设置',
};

function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason;
}

// 口径：mention_rate/topN 为 0–100 百分数（展示带 %），avg_rank 为原始名次；
// null 一律「—」，严禁渲染成 0。
function formatMetric(name: ComparisonMetricName, value: number | null): string {
  if (value === null) return '—';
  return name === 'avg_rank' ? value.toFixed(2) : `${value.toFixed(1)}%`;
}

function formatDelta(name: ComparisonMetricName, value: number | null): string {
  if (value === null) return '—';
  const sign = value > 0 ? '+' : '';
  return name === 'avg_rank' ? `${sign}${value.toFixed(2)}` : `${sign}${value.toFixed(1)}%`;
}

function shortRunId(pubId: string): string {
  return pubId.length > 16 ? `${pubId.slice(0, 16)}…` : pubId;
}

function runTime(run: RunRow): string {
  return new Date(run.created_at ?? run.updated_at).toLocaleString('zh-CN', { hour12: false });
}

function truncateQuery(text: string): string {
  return text.length > 60 ? `${text.slice(0, 60)}…` : text;
}

function createErrorMessage(cause: unknown): string {
  if (cause instanceof ServicesApiError && cause.code === 'unknown_run_pub_id') {
    const ids = cause.details.unknown_run_pub_ids;
    const suffix = Array.isArray(ids) && ids.length > 0 ? `：${ids.join('、')}` : '';
    return `创建失败：存在无法识别的 run${suffix}（可能已被清理或不属于本项目），请刷新后重试`;
  }
  return `创建失败：${cause instanceof Error ? cause.message : '未知错误'}`;
}

function detailErrorMessage(cause: unknown): string {
  const code = cause instanceof Error ? cause.message : '';
  if (code === 'domain_unset')
    return '项目未设置品牌规则包域，请先在项目设置中配置 brandrank_domain。';
  if (code === 'unknown_domain') return '项目规则包域未映射到可用规则包，请检查项目设置。';
  if (code === 'comparison_not_found') return '对比实体不存在或已被清理。';
  return `详情加载失败：${code || '未知错误'}`;
}

export function ComparisonPanel({
  session,
  project,
  runsVersion = 0,
}: {
  session: SessionContext;
  project: Project;
  runsVersion?: number;
}) {
  const [comparisonVersion, setComparisonVersion] = useState(0);
  const [name, setName] = useState('');
  const [note, setNote] = useState('');
  const [baselineIds, setBaselineIds] = useState<string[]>([]);
  const [optimizedIds, setOptimizedIds] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DetailState | null>(null);

  const loadRuns = useCallback(
    (cursor?: string) =>
      executionApi.runs(session, {
        projectPubId: project.pub_id,
        ...(cursor ? { cursor } : {}),
        limit: SERVICE_PILOT_PAGE_SIZE,
      }),
    [project.pub_id, session],
  );
  const runsPage = useCursorCollection(loadRuns, `${project.pub_id}:${runsVersion}`);
  const loadComparisons = useCallback(
    (cursor?: string) =>
      servicesApi.listComparisons(session, {
        projectPubId: project.pub_id,
        ...(cursor ? { cursor } : {}),
        limit: SERVICE_PILOT_PAGE_SIZE,
      }),
    [project.pub_id, session],
  );
  const comparisonsPage = useCursorCollection(
    loadComparisons,
    `${project.pub_id}:${comparisonVersion}`,
  );

  useEffect(() => {
    if (!expandedId) {
      setDetail(null);
      return;
    }
    const id = expandedId;
    let cancelled = false;
    setDetail({ id, kind: 'loading' });
    void servicesApi.getComparison(session, id).then(
      (data) => {
        if (!cancelled) setDetail({ id, kind: 'ready', data });
      },
      (cause: unknown) => {
        if (!cancelled) setDetail({ id, kind: 'error', message: detailErrorMessage(cause) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [session, expandedId]);

  function toggleSelection(
    current: string[],
    setCurrent: (next: string[]) => void,
    pubId: string,
    checked: boolean,
  ) {
    setCurrent(checked ? [...current, pubId] : current.filter((item) => item !== pubId));
  }

  async function submit() {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setFormError('请填写对比名称');
      return;
    }
    if (baselineIds.length === 0 || optimizedIds.length === 0) {
      setFormError('基线与优化后 run 各至少选择 1 个');
      return;
    }
    const overlap = baselineIds.filter((id) => optimizedIds.includes(id));
    if (overlap.length > 0) {
      setFormError(`基线与优化后 run 不得重叠：${overlap.join('、')}`);
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const trimmedNote = note.trim();
      const created = await servicesApi.createComparison(session, {
        projectPubId: project.pub_id,
        name: trimmedName,
        baselineRunPubIds: baselineIds,
        optimizedRunPubIds: optimizedIds,
        ...(trimmedNote ? { note: trimmedNote } : {}),
      });
      setComparisonVersion((current) => current + 1);
      setExpandedId(created.pub_id);
      setName('');
      setNote('');
      setBaselineIds([]);
      setOptimizedIds([]);
    } catch (cause) {
      setFormError(createErrorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  function runCheckboxes(
    legend: string,
    selected: string[],
    setSelected: (next: string[]) => void,
  ) {
    return (
      <fieldset>
        <legend>{legend}</legend>
        {runsPage.state === 'loading' ? (
          <p className="empty">正在加载 run…</p>
        ) : runsPage.state === 'failed' ? (
          <p className="empty">run 列表加载失败。</p>
        ) : runsPage.data.length === 0 ? (
          <p className="empty">该项目尚无采集 run。</p>
        ) : (
          <div className="run-checks">
            {(runsPage.data as RunRow[]).map((run) => (
              <label key={run.pub_id}>
                <input
                  type="checkbox"
                  checked={selected.includes(run.pub_id)}
                  onChange={(event) =>
                    toggleSelection(selected, setSelected, run.pub_id, event.target.checked)
                  }
                />
                {`${shortRunId(run.pub_id)} · ${run.state} · ${runTime(run)}`}
              </label>
            ))}
          </div>
        )}
      </fieldset>
    );
  }

  return (
    <section className="execution-card comparison-panel">
      <div className="section-title">
        <h2>前后对比（逐题）</h2>
        <span>基线 run 组 vs 优化后 run 组 · brandrank 口径 · 按题目配对</span>
      </div>
      <form
        className="comparison-form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label>
          对比名称
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：官网 FAQ 优化前后"
          />
        </label>
        <div className="inline-fields">
          {runCheckboxes('基线 run（优化前，多选）', baselineIds, setBaselineIds)}
          {runCheckboxes('优化后 run（多选）', optimizedIds, setOptimizedIds)}
        </div>
        {runsPage.state === 'ready' && runsPage.data.length > 0 ? (
          <CursorPagination
            page={runsPage.pageNumber}
            hasPrevious={runsPage.hasPrevious}
            hasNext={runsPage.hasNext}
            onPrevious={runsPage.previous}
            onNext={runsPage.next}
            label="对比 run 分页"
          />
        ) : null}
        <label>
          备注（可选）
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="本次优化动作说明"
          />
        </label>
        <div className="actions">
          <button type="submit" disabled={busy}>
            创建对比
          </button>
        </div>
      </form>
      {formError ? (
        <p className="launcher-error" role="alert">
          {formError}
        </p>
      ) : null}

      {comparisonsPage.state === 'loading' ? (
        <p className="empty">正在加载对比列表…</p>
      ) : comparisonsPage.state === 'failed' ? (
        <p className="empty">对比列表加载失败。</p>
      ) : comparisonsPage.data.length === 0 ? (
        <p className="empty">尚无前后对比——选择基线与优化后 run 创建第一个。</p>
      ) : (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>名称</th>
                  <th>创建时间</th>
                  <th>基线 run</th>
                  <th>优化后 run</th>
                  <th>详情</th>
                </tr>
              </thead>
              <tbody>
                {comparisonsPage.data.map((item: RunComparison) => (
                  <tr key={item.pub_id}>
                    <td>{item.name}</td>
                    <td>{new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })}</td>
                    <td>{item.baseline_run_pub_ids.length} 个</td>
                    <td>{item.optimized_run_pub_ids.length} 个</td>
                    <td>
                      <button
                        type="button"
                        onClick={() =>
                          setExpandedId((current) => (current === item.pub_id ? null : item.pub_id))
                        }
                      >
                        {expandedId === item.pub_id ? '收起' : '展开'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <CursorPagination
            page={comparisonsPage.pageNumber}
            hasPrevious={comparisonsPage.hasPrevious}
            hasNext={comparisonsPage.hasNext}
            onPrevious={comparisonsPage.previous}
            onNext={comparisonsPage.next}
            label="前后对比记录分页"
          />
        </>
      )}

      {expandedId ? (
        <div className="comparison-detail">
          {!detail || detail.id !== expandedId || detail.kind === 'loading' ? (
            <p className="empty">正在计算对比结果…</p>
          ) : detail.kind === 'error' ? (
            <p className="launcher-error" role="alert">
              {detail.message}
            </p>
          ) : (
            <ComparisonDetailView detail={detail.data} />
          )}
        </div>
      ) : null}
    </section>
  );
}

function ComparisonDetailView({ detail }: { detail: RunComparisonDetail }) {
  const { result } = detail;
  const hasUnpaired =
    result.unpaired.baseline_only.length > 0 || result.unpaired.optimized_only.length > 0;
  return (
    <>
      <p>
        状态{' '}
        {result.status === 'ok' ? (
          <span className="status ok">正常</span>
        ) : (
          <span className="status warn">数据不足</span>
        )}
        {result.domain ? ` · 规则包：${result.domain}` : ''}
        {result.target_brand ? ` · 目标品牌：${result.target_brand}` : ''}
      </p>
      {result.insufficient_reasons.length > 0 ? (
        <ul>
          {result.insufficient_reasons.map((reason) => (
            <li key={reason}>{reasonLabel(reason)}</li>
          ))}
        </ul>
      ) : null}
      <p>
        {`覆盖：优化前答案 ${result.coverage.before_answers} 条（含抽取 ${result.coverage.before_with_extract}）${
          result.coverage.before_truncated ? '（已截断）' : ''
        } · 优化后答案 ${result.coverage.after_answers} 条（含抽取 ${
          result.coverage.after_with_extract
        }）${result.coverage.after_truncated ? '（已截断）' : ''}`}
      </p>

      <h3>聚合指标</h3>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>指标</th>
              <th>优化前</th>
              <th>优化后</th>
              <th>差值</th>
            </tr>
          </thead>
          <tbody>
            {result.aggregate.metrics.map((row) => (
              <tr key={row.extra.metric_name}>
                <td>{AGGREGATE_LABELS[row.extra.metric_name] ?? row.extra.metric_name}</td>
                <td>{formatMetric(row.extra.metric_name, row.extra.before)}</td>
                <td>{formatMetric(row.extra.metric_name, row.extra.after)}</td>
                <td>{formatDelta(row.extra.metric_name, row.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>逐题对比</h3>
      {result.questions.length === 0 ? (
        <p className="empty">两臂没有可配对的题目。</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>题目</th>
                <th>优化前提及率</th>
                <th>优化后提及率</th>
                <th>提及率差值</th>
                <th>优化前平均排名</th>
                <th>优化后平均排名</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {result.questions.map((row) => (
                <tr
                  key={row.query_text}
                  className={row.status === 'insufficient' ? 'comparison-row-insufficient' : ''}
                >
                  <td>{truncateQuery(row.query_text)}</td>
                  <td>{formatMetric('mention_rate', row.before?.mention_rate.value ?? null)}</td>
                  <td>{formatMetric('mention_rate', row.after?.mention_rate.value ?? null)}</td>
                  <td>{formatDelta('mention_rate', row.delta.mention_rate)}</td>
                  <td>{formatMetric('avg_rank', row.before?.avg_rank.value ?? null)}</td>
                  <td>{formatMetric('avg_rank', row.after?.avg_rank.value ?? null)}</td>
                  <td
                    title={
                      row.insufficient_reasons.length > 0
                        ? row.insufficient_reasons.map(reasonLabel).join('；')
                        : undefined
                    }
                  >
                    {row.status === 'ok' ? '正常' : '数据不足'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hasUnpaired ? (
        <details className="comparison-unpaired">
          <summary>
            {`未配对题目（仅基线 ${result.unpaired.baseline_only.length} / 仅优化后 ${result.unpaired.optimized_only.length}）`}
          </summary>
          {result.unpaired.baseline_only.length > 0 ? (
            <>
              <h4>仅基线出现</h4>
              <ul>
                {result.unpaired.baseline_only.map((query) => (
                  <li key={query}>{query}</li>
                ))}
              </ul>
            </>
          ) : null}
          {result.unpaired.optimized_only.length > 0 ? (
            <>
              <h4>仅优化后出现</h4>
              <ul>
                {result.unpaired.optimized_only.map((query) => (
                  <li key={query}>{query}</li>
                ))}
              </ul>
            </>
          ) : null}
        </details>
      ) : null}
    </>
  );
}
