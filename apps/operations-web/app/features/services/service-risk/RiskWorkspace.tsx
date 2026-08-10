import { useEffect, useState } from 'react';
import { RunsPanel } from '../RunsPanel';
import { WindowPicker } from '../WindowPicker';
import {
  defaultWindow,
  servicesApi,
  type DisparagementCase,
  type DisparagementDimension,
  type DisparagementRateRow,
  type Project,
  type SessionContext,
} from '../api';

const DIMENSIONS: [DisparagementDimension, string][] = [
  ['target_brand', '按被拉踩品牌'],
  ['subject_brand', '按发起品牌'],
  ['platform', '按平台'],
];

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

export function RiskWorkspace({ session, project }: { session: SessionContext; project: Project }) {
  const [window_, setWindow] = useState(defaultWindow);
  const [dimension, setDimension] = useState<DisparagementDimension>('target_brand');
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
        dimension,
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
  }, [session, project.pub_id, window_.start, window_.end, dimension]);

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

  return (
    <>
      <p className="service-note">
        采集 run 完成后自动进行抹黑拉踩判定，无需单独启动；本页汇总判定结果与典型案例。
      </p>
      <RunsPanel session={session} projectPubId={project.pub_id} readOnly />
      <section className="execution-card">
        <div className="section-title">
          <h2>拉踩率</h2>
          <span>
            {window_.start} ~ {window_.end}
          </span>
        </div>
        <WindowPicker start={window_.start} end={window_.end} onChange={setWindow} />
        <div className="platform-checks" role="group" aria-label="拉踩维度">
          {DIMENSIONS.map(([value, label]) => (
            <label key={value}>
              <input
                type="radio"
                name="disparagement-dimension"
                checked={dimension === value}
                onChange={() => setDimension(value)}
              />
              {label}
            </label>
          ))}
        </div>
        {rates.kind === 'loading' ? (
          <p className="empty">正在加载拉踩率…</p>
        ) : rates.kind === 'failed' ? (
          <p className="empty">拉踩率暂不可用（{rates.message}）。</p>
        ) : rates.data.length === 0 ? (
          <p className="empty">该时间窗内尚无抹黑拉踩判定数据——采集 run 完成后自动生成。</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>对象</th>
                  <th>判定数</th>
                  <th>拉踩次数</th>
                  <th>拉踩率</th>
                  <th>负面</th>
                  <th>支持</th>
                </tr>
              </thead>
              <tbody>
                {rates.data.map((row) => (
                  <tr key={`${row.dimension}-${row.value}`}>
                    <td>{row.value}</td>
                    <td>{row.judgments}</td>
                    <td>{row.disparagement_count}</td>
                    <td>
                      {row.disparagement_rate === null
                        ? '—'
                        : `${(row.disparagement_rate * 100).toFixed(1)}%`}
                    </td>
                    <td>{row.negative_count}</td>
                    <td>{row.support_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
                      {item.content_origin === 'own_content' ? (
                        <>
                          {' '}
                          <span
                            className="status warn"
                            title="己方 SOP 定稿稿件的拉踩判定（报价单服务2·己方内容检测通道）"
                          >
                            己方稿件
                          </span>
                        </>
                      ) : null}
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
                      {item.source_url ? (
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
