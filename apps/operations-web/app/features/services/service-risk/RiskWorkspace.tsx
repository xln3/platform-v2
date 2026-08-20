import { useEffect, useState } from 'react';
import { executionApi, type AnswerRow } from '../../execution/api';
import { AnswerDetail } from '../AnswerExplorer';
import { MetricHelp } from '../MetricHelp';
import { WindowPicker } from '../WindowPicker';
import {
  defaultWindow,
  servicesApi,
  type DisparagementCase,
  type DisparagementRateRow,
  type Project,
  type SessionContext,
} from '../api';

const FACT_CHECK_LABELS: Record<string, string> = {
  supported: '属实',
  refuted: '不实',
  unverifiable: '无法核实',
};

const FACT_CHECK_STATUS: Record<string, string> = {
  supported: 'ok',
  refuted: 'bad',
  unverifiable: 'warn',
};

type LoadState<T> =
  | { kind: 'loading' }
  | { kind: 'ready'; data: T }
  | { kind: 'failed'; message: string };

function AnswerOrigin({
  session,
  projectPubId,
  answerPubId,
}: {
  session: SessionContext;
  projectPubId: string;
  answerPubId: string;
}) {
  const [answer, setAnswer] = useState<AnswerRow | null>(null);
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<'idle' | 'loading' | 'failed'>('idle');

  async function openAnswer() {
    if (answer) {
      setOpen(true);
      return;
    }
    setState('loading');
    try {
      const page = await executionApi.answers(session, {
        projectPubId,
        answerPubId,
        limit: 1,
      });
      const loaded = page.data.find((item) => item.pub_id === answerPubId);
      if (!loaded) throw new Error('answer_not_found');
      setAnswer(loaded);
      setState('idle');
      setOpen(true);
    } catch {
      setState('failed');
    }
  }

  return (
    <>
      <button type="button" onClick={() => void openAnswer()} disabled={state === 'loading'}>
        {state === 'loading' ? '正在加载回答…' : state === 'failed' ? '重试查看回答' : '查看回答'}
      </button>
      {open && answer ? (
        <AnswerDetail session={session} answer={answer} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}

export function RiskWorkspace({ session, project }: { session: SessionContext; project: Project }) {
  const [window_, setWindow] = useState(defaultWindow);
  const [rates, setRates] = useState<LoadState<DisparagementRateRow[]>>({ kind: 'loading' });
  const [cases, setCases] = useState<LoadState<DisparagementCase[]>>({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setRates({ kind: 'loading' });
    servicesApi
      .disparagementRate(session, {
        projectPubId: project.pub_id,
        start: window_.start,
        end: window_.end,
        dimension: 'target_brand',
      })
      .then((data) => {
        if (!cancelled) setRates({ kind: 'ready', data });
      })
      .catch((error: unknown) => {
        if (!cancelled)
          setRates({ kind: 'failed', message: error instanceof Error ? error.message : 'failed' });
      });
    return () => {
      cancelled = true;
    };
  }, [session, project.pub_id, window_.start, window_.end]);

  useEffect(() => {
    let cancelled = false;
    setCases({ kind: 'loading' });
    servicesApi
      .disparagementCases(session, {
        projectPubId: project.pub_id,
        start: window_.start,
        end: window_.end,
      })
      .then((data) => {
        if (!cancelled) setCases({ kind: 'ready', data });
      })
      .catch((error: unknown) => {
        if (!cancelled)
          setCases({ kind: 'failed', message: error instanceof Error ? error.message : 'failed' });
      });
    return () => {
      cancelled = true;
    };
  }, [session, project.pub_id, window_.start, window_.end]);

  const summary = rates.kind === 'ready' ? (rates.data[0] ?? null) : null;

  return (
    <>
      <p className="service-note">
        仅核查项目目标品牌。采集完成后自动判定 AI
        回答与公开信源中的相关表述；竞品自身风险不进入本页统计。
      </p>
      <section className="execution-card">
        <div className="section-title">
          <h2>目标品牌风险概览</h2>
          <span>
            {window_.start} ~ {window_.end}
            {summary ? ` · ${summary.value}` : ''}
          </span>
        </div>
        <WindowPicker start={window_.start} end={window_.end} onChange={setWindow} />
        {rates.kind === 'loading' ? (
          <p className="empty">正在加载目标品牌风险统计…</p>
        ) : rates.kind === 'failed' ? (
          <p className="empty">目标品牌风险统计暂不可用（{rates.message}）。</p>
        ) : summary === null ? (
          <p className="empty">该时间窗内尚无目标品牌判定数据——采集完成后自动生成。</p>
        ) : (
          <>
            <div className="metric-cards risk-metric-cards">
              <article>
                <MetricHelp
                  label="判定数"
                  explanation="时间窗内，目标品牌通过校验的品牌提及文本窗数量。按文本窗计数，不等于 AI 回答数；同一回答可能产生多条判定。"
                />
                <strong>{summary.judgments}</strong>
                <span>有效目标品牌判定</span>
              </article>
              <article>
                <MetricHelp
                  label="拉踩次数"
                  explanation="有效目标品牌判定中，明确存在贬低、打压或不当比较（disparagement=true）的文本窗数量。"
                />
                <strong>{summary.disparagement_count}</strong>
                <span>判为拉踩的文本窗</span>
              </article>
              <article>
                <MetricHelp
                  label="拉踩率"
                  explanation="拉踩次数 ÷ 判定数 × 100%。判定数为 0 时不计算。"
                />
                <strong>
                  {summary.disparagement_rate === null
                    ? '—'
                    : `${(summary.disparagement_rate * 100).toFixed(1)}%`}
                </strong>
                <span>
                  {summary.disparagement_count}/{summary.judgments}
                </span>
              </article>
              <article>
                <MetricHelp
                  label="负面"
                  explanation="有效目标品牌判定中，态度为负面（attitude=negative）的文本窗数量。单纯负面批评未必构成拉踩，因此该数可能大于拉踩次数。"
                />
                <strong>{summary.negative_count}</strong>
                <span>负面态度文本窗</span>
              </article>
              <article>
                <MetricHelp
                  label="支持"
                  explanation="有效目标品牌判定中，态度为支持（attitude=support）的文本窗数量。中性及其他态度不计入此数。"
                />
                <strong>{summary.support_count}</strong>
                <span>支持态度文本窗</span>
              </article>
            </div>
            <p className="setup-summary">
              统计只覆盖目标品牌；“负面”描述态度，“拉踩”描述是否构成贬低或不当比较，两者不是同一指标。
            </p>
          </>
        )}
      </section>

      <section className="execution-card">
        <div className="section-title">
          <h2>典型案例</h2>
          <span>按置信度降序 · 含证据引文与出处</span>
        </div>
        {cases.kind === 'loading' ? (
          <p className="empty">正在加载典型案例…</p>
        ) : cases.kind === 'failed' ? (
          <p className="empty">典型案例暂不可用（{cases.message}）。</p>
        ) : cases.data.length === 0 ? (
          <p className="empty">该时间窗内未判定出抹黑拉踩案例。</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>品牌</th>
                  <th>态度</th>
                  <th>证据引文</th>
                  <th>置信度</th>
                  <th>事实核查</th>
                  <th>出处</th>
                </tr>
              </thead>
              <tbody>
                {cases.data.map((item) => (
                  <tr key={item.judgment_pub_id}>
                    <td>
                      {item.subject_brand} → {item.target_brand}
                    </td>
                    <td>{item.attitude}</td>
                    <td>{item.evidence_quote ?? '—'}</td>
                    <td>
                      {item.confidence === null ? '—' : `${(item.confidence * 100).toFixed(0)}%`}
                    </td>
                    <td>
                      {item.fact_check ? (
                        <span
                          className={`status ${FACT_CHECK_STATUS[item.fact_check.verdict] ?? 'warn'}`}
                          title={
                            item.fact_check.summary
                              ? `${item.fact_check.summary}${
                                  item.fact_check.source_url
                                    ? `\n来源：${item.fact_check.source_url}`
                                    : ''
                                }`
                              : undefined
                          }
                        >
                          {FACT_CHECK_LABELS[item.fact_check.verdict] ?? item.fact_check.verdict}
                        </span>
                      ) : (
                        '—'
                      )}
                      {item.fact_check?.source_url ? (
                        <>
                          {' '}
                          <a
                            href={item.fact_check.source_url}
                            target="_blank"
                            rel="noreferrer noopener"
                            title={item.fact_check.summary ?? undefined}
                          >
                            来源
                          </a>
                        </>
                      ) : null}
                    </td>
                    <td>
                      {item.subject_type === 'answer' ? (
                        <AnswerOrigin
                          session={session}
                          projectPubId={project.pub_id}
                          answerPubId={item.subject_pub_id}
                        />
                      ) : item.source_url ? (
                        <a href={item.source_url} target="_blank" rel="noreferrer noopener">
                          查看原文
                        </a>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="service-link-card">
        <div>
          <strong>需要帖子级深度取证？</strong>
          <p>帖子分析工作区提供逐帖核查、证据链与外链原文。</p>
        </div>
        <a href="/platform/operations/post-analysis">前往帖子分析 →</a>
      </div>
    </>
  );
}
