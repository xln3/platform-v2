import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  servicesApi,
  type SamplingProgress,
  type SamplingProgressCell,
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
  normal: '普通模式',
  deep_think: '深度思考',
};

type Props = {
  session: SessionContext;
  projectPubId: string;
};

type LoadState = 'loading' | 'ready' | 'failed';

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

export function SamplingProgressPanel({ session, projectPubId }: Props) {
  const [state, setState] = useState<LoadState>('loading');
  const [progress, setProgress] = useState<SamplingProgress | null>(null);
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
                                <strong>{cell.completed_samples}遍</strong>
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
    </section>
  );
}
