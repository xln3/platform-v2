import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { executionApi, type AnswerRow } from '../execution/api';
import { AnswerDetail, AnswerRowsTable } from './AnswerExplorer';
import {
  servicesApi,
  type SamplingProgress,
  type SamplingProgressCell,
  type SamplingProgressColumn,
  type SessionContext,
} from './api';

const PLATFORM_LABELS: Record<string, string> = {
  doubao: '豆包',
  deepseek: 'DeepSeek',
  yiyan: '文心一言',
  tongyi: '通义千问',
  yuanbao: '腾讯元宝',
};

const MODE_LABELS: Record<string, string> = {
  normal: '快速模式',
  deep_think: '深度思考',
};

type Props = {
  session: SessionContext;
  projectPubId: string;
};

type LoadState = 'loading' | 'ready' | 'failed';

type SamplingAnswerTarget = {
  queryText: string;
  column: SamplingProgressColumn;
  cell: SamplingProgressCell;
};

type AnswerLoadState =
  | { kind: 'loading' }
  | { kind: 'ready'; answers: AnswerRow[]; missing: number }
  | { kind: 'failed' };

function datePart(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): string {
  return parts.find((part) => part.type === type)?.value ?? '';
}

export function formatSamplingTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date);
  return `${datePart(parts, 'month')}-${datePart(parts, 'day')} ${datePart(parts, 'hour')}:${datePart(parts, 'minute')}`;
}

export function samplingRevisionLabel(progress: SamplingProgress): string {
  const { config_revision_start: start, config_revision_end: end } = progress;
  if (start === null || end === null) return '尚无冻结配置';
  return start === end ? `配置 v${start}` : `配置 v${start}–v${end}`;
}

function fullSamplingTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
  });
}

function cellByColumn(cells: SamplingProgressCell[]): Map<string, SamplingProgressCell> {
  return new Map(cells.map((cell) => [cell.column_key, cell]));
}

function SamplingAnswersDialog({
  session,
  projectPubId,
  target,
  onClose,
}: {
  session: SessionContext;
  projectPubId: string;
  target: SamplingAnswerTarget;
  onClose: () => void;
}) {
  const [state, setState] = useState<AnswerLoadState>({ kind: 'loading' });
  const [selected, setSelected] = useState<AnswerRow | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: 'loading' });
    const answerPubIds = [...new Set(target.cell.answer_pub_ids ?? [])];
    void Promise.all(
      answerPubIds.map(async (answerPubId) => {
        try {
          const page = await executionApi.answers(session, {
            projectPubId,
            answerPubId,
            limit: 1,
          });
          return (
            page.data.find(
              (answer) =>
                answer.pub_id === answerPubId &&
                answer.project_pub_id === projectPubId &&
                answer.query_text === target.queryText &&
                answer.model === target.column.model &&
                answer.region === target.column.region &&
                answer.mode === target.column.mode,
            ) ?? null
          );
        } catch {
          return null;
        }
      }),
    ).then((loaded) => {
      if (cancelled) return;
      const answers = loaded.filter((answer): answer is AnswerRow => answer !== null);
      setState(
        answers.length > 0
          ? { kind: 'ready', answers, missing: answerPubIds.length - answers.length }
          : { kind: 'failed' },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [session, projectPubId, target, reloadToken]);

  if (selected) {
    return <AnswerDetail session={session} answer={selected} onClose={() => setSelected(null)} />;
  }

  const platform = PLATFORM_LABELS[target.column.model] ?? target.column.model;
  const mode = MODE_LABELS[target.column.mode] ?? target.column.mode;
  return (
    <div
      className="answer-detail-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="answer-detail" role="dialog" aria-modal="true" aria-label="采样具体回答">
        <div className="answer-detail-head">
          <h3>{target.queryText}</h3>
          <button onClick={onClose}>关闭</button>
        </div>
        <p className="sampling-answer-context">
          {platform}×{target.column.region} · {mode} · {target.cell.completed_samples}遍
        </p>
        {state.kind === 'loading' ? (
          <p className="empty">正在加载具体回答…</p>
        ) : state.kind === 'failed' ? (
          <p className="empty">
            具体回答加载失败。
            <button onClick={() => setReloadToken((value) => value + 1)}>重试</button>
          </p>
        ) : (
          <>
            <p className="sampling-answer-instruction">点击任一行查看完整回答、引用与证据。</p>
            {state.missing > 0 ? (
              <p className="launcher-error" role="status">
                有 {state.missing} 条回答暂未同步到详情索引。
              </p>
            ) : null}
            <AnswerRowsTable
              answers={state.answers}
              onSelect={setSelected}
              ariaLabel="该采样位具体回答"
            />
          </>
        )}
      </div>
    </div>
  );
}

export function SamplingProgressPanel({ session, projectPubId }: Props) {
  const [state, setState] = useState<LoadState>('loading');
  const [progress, setProgress] = useState<SamplingProgress | null>(null);
  const [answerTarget, setAnswerTarget] = useState<SamplingAnswerTarget | null>(null);
  const requestSerial = useRef(0);

  const refresh = useCallback(
    async (background = false) => {
      const requestId = ++requestSerial.current;
      if (!background) setState('loading');
      try {
        const next = await servicesApi.samplingProgress(session, projectPubId);
        if (requestId !== requestSerial.current) return;
        setProgress(next);
        setState('ready');
      } catch {
        if (requestId !== requestSerial.current) return;
        if (!background) setState('failed');
      }
    },
    [session, projectPubId],
  );

  useEffect(() => {
    void refresh();
    return () => {
      requestSerial.current += 1;
    };
  }, [refresh]);

  useEffect(() => {
    if (!progress || progress.live_runs === 0) return;
    const timer = window.setInterval(() => void refresh(true), 15_000);
    return () => window.clearInterval(timer);
  }, [progress, refresh]);

  const modes = useMemo(
    () => [...new Set(progress?.columns.map((column) => column.mode) ?? [])],
    [progress],
  );
  const showModeInColumn = modes.length > 1;

  return (
    <section className="execution-card sampling-progress-panel">
      <div className="section-title">
        <h2>采样进度</h2>
        <span>
          {progress?.live_runs
            ? `${progress.live_runs} 个 run 进行中，每 15 秒自动刷新`
            : '按最新完整采样批次汇总'}
        </span>
      </div>
      {state === 'loading' ? (
        <p className="empty">正在汇总采样进度…</p>
      ) : state === 'failed' ? (
        <p className="empty">
          采样进度加载失败。<button onClick={() => void refresh()}>重试</button>
        </p>
      ) : !progress || progress.rows.length === 0 ? (
        <p className="empty">该项目尚无可展示的冻结采样配置。</p>
      ) : (
        <>
          <div className="sampling-progress-summary" aria-label="采样进度摘要">
            <span>{samplingRevisionLabel(progress)}</span>
            <span>{progress.rows.length} 问</span>
            <span>{progress.columns.length} 个采样位</span>
            <span>
              已观测 {progress.observed_cells}/{progress.total_cells} 格
            </span>
            <span>共 {progress.answer_count} 条回答</span>
            {modes.length === 1 ? <span>模式：{MODE_LABELS[modes[0]!] ?? modes[0]}</span> : null}
          </div>
          <p className="sampling-progress-note">每格：重复遍数 / 最近测评时间；— = 尚无观测</p>
          <div className="table-scroll sampling-progress-table-scroll">
            <table className="sampling-progress-table" aria-label="问题采样进度总览">
              <thead>
                <tr>
                  <th>附录</th>
                  <th>组</th>
                  <th>表述</th>
                  <th>问题</th>
                  {progress.columns.map((column) => (
                    <th key={column.key}>
                      <span>
                        {PLATFORM_LABELS[column.model] ?? column.model}×{column.region}
                      </span>
                      {showModeInColumn ? (
                        <small>{MODE_LABELS[column.mode] ?? column.mode}</small>
                      ) : null}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {progress.rows.map((row) => {
                  const cells = cellByColumn(row.cells);
                  return (
                    <tr key={`${row.group}-${row.expression}-${row.query_text}`}>
                      <td>{row.appendix ?? '—'}</td>
                      <td title={row.group_name}>{row.group}</td>
                      <td>{row.expression}</td>
                      <td>{row.query_text}</td>
                      {progress.columns.map((column) => {
                        const cell = cells.get(column.key);
                        return (
                          <td
                            key={column.key}
                            className={
                              cell ? 'sampling-progress-observed' : 'sampling-progress-empty'
                            }
                          >
                            {cell ? (
                              <>
                                {cell.answer_pub_ids?.length ? (
                                  <button
                                    type="button"
                                    className="sampling-progress-count"
                                    aria-label={`${row.query_text}，${PLATFORM_LABELS[column.model] ?? column.model}×${column.region}，${cell.completed_samples}遍，查看具体回答`}
                                    onClick={() =>
                                      setAnswerTarget({
                                        queryText: row.query_text,
                                        column,
                                        cell,
                                      })
                                    }
                                  >
                                    {cell.completed_samples}遍
                                  </button>
                                ) : (
                                  <strong>{cell.completed_samples}遍</strong>
                                )}
                                <time
                                  dateTime={cell.latest_capture_time}
                                  title={fullSamplingTime(cell.latest_capture_time)}
                                >
                                  {formatSamplingTime(cell.latest_capture_time)}
                                </time>
                              </>
                            ) : (
                              <span aria-label="尚无观测">—</span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      {answerTarget ? (
        <SamplingAnswersDialog
          session={session}
          projectPubId={projectPubId}
          target={answerTarget}
          onClose={() => setAnswerTarget(null)}
        />
      ) : null}
    </section>
  );
}
