import { Pagination } from '@geo/design-system';
import { useCallback, useEffect, useRef, useState } from 'react';
import { PlatformBadge, platformDisplayName } from '../../platforms';
import { usePageWindow } from '../../pagination';
import { executionApi, type AnswerRow } from '../execution/api';
import { AnswerDetail, AnswerRowsTable } from './AnswerExplorer';
import {
  servicesApi,
  type SamplingProgress,
  type SamplingProgressCell,
  type SamplingProgressColumn,
  type SessionContext,
} from './api';
import {
  SAMPLING_ANSWERS_PAGE_NUMBER_WINDOW_SIZE,
  SAMPLING_ANSWERS_PAGE_SIZE,
  SAMPLING_PROGRESS_DEFAULT_PAGE_SIZE,
  SAMPLING_PROGRESS_PAGE_NUMBER_WINDOW_SIZE,
} from './pagination-policy';

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

type PanoramaState =
  | { kind: 'loading' }
  | { kind: 'ready'; progress: SamplingProgress }
  | { kind: 'failed' };

const SAMPLING_PROGRESS_PANORAMA_PAGE_SIZE = 25;
const SAMPLING_PROGRESS_PANORAMA_MAX_PAGES = 100;

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

function effectiveModes(column: SamplingProgressColumn, cell: SamplingProgressCell): string[] {
  if (column.modes?.length) return [...new Set(column.modes)];
  if (cell.mode_breakdown?.length) {
    return [...new Set(cell.mode_breakdown.map((item) => item.mode))];
  }
  return [column.mode];
}

function modeBreakdownLabel(column: SamplingProgressColumn, cell: SamplingProgressCell): string {
  const breakdown = cell.mode_breakdown?.length
    ? cell.mode_breakdown
    : [{ mode: column.mode, completed_samples: cell.completed_samples }];
  return breakdown
    .map((item) => `${MODE_LABELS[item.mode] ?? item.mode} ${item.completed_samples}遍`)
    .join(' · ');
}

function SamplingProgressSummary({ progress }: { progress: SamplingProgress }) {
  return (
    <div className="sampling-progress-summary" aria-label="采样进度摘要">
      <span>{samplingRevisionLabel(progress)}</span>
      <span>{progress.page.total_count} 问</span>
      <span>{progress.columns.length} 个采样位</span>
      <span>
        已观测 {progress.observed_cells}/{progress.total_cells} 格
      </span>
      <span>共 {progress.answer_count} 条有效回答</span>
    </div>
  );
}

function SamplingProgressTable({
  progress,
  rows = progress.rows,
  ariaLabel,
  onAnswerTarget,
}: {
  progress: SamplingProgress;
  rows?: SamplingProgress['rows'];
  ariaLabel: string;
  onAnswerTarget: (target: SamplingAnswerTarget) => void;
}) {
  const formalLegCounts = new Map<string, number>();
  for (const column of progress.columns) {
    const leg = `${column.model}\u0000${column.region}`;
    formalLegCounts.set(leg, (formalLegCounts.get(leg) ?? 0) + 1);
  }
  const multiModeFormalLegs = new Set(
    [...formalLegCounts.entries()].filter(([, count]) => count > 1).map(([leg]) => leg),
  );
  return (
    <table className="sampling-progress-table" aria-label={ariaLabel}>
      <thead>
        <tr>
          <th>附录</th>
          <th>组</th>
          <th>表述</th>
          <th>问题</th>
          {progress.columns.map((column) => (
            <th
              key={column.key}
              aria-label={`${platformDisplayName(column.model)}×${column.region}`}
            >
              <span>
                <PlatformBadge platform={column.model} />×{column.region}
              </span>
              {multiModeFormalLegs.has(`${column.model}\u0000${column.region}`) ? (
                <small>{MODE_LABELS[column.mode] ?? column.mode}</small>
              ) : null}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
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
                    className={cell ? 'sampling-progress-observed' : 'sampling-progress-empty'}
                  >
                    {cell ? (
                      <>
                        {cell.answer_pub_ids?.length ? (
                          <button
                            type="button"
                            className="sampling-progress-count"
                            aria-label={`${row.query_text}，${platformDisplayName(column.model)}×${column.region}，${cell.completed_samples}遍，${modeBreakdownLabel(column, cell)}，查看具体回答`}
                            onClick={() =>
                              onAnswerTarget({ queryText: row.query_text, column, cell })
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
                        <small className="sampling-progress-mode-breakdown">
                          {modeBreakdownLabel(column, cell)}
                        </small>
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
  );
}

function newestSamplingAnswerFirst(left: AnswerRow, right: AnswerRow): number {
  const leftTime = Date.parse(left.capture_time);
  const rightTime = Date.parse(right.capture_time);
  if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) {
    return rightTime - leftTime;
  }
  if (Number.isFinite(leftTime) !== Number.isFinite(rightTime)) {
    return Number.isFinite(rightTime) ? 1 : -1;
  }
  if (left.pub_id === right.pub_id) return 0;
  return left.pub_id < right.pub_id ? 1 : -1;
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
  const answers = state.kind === 'ready' ? state.answers : [];
  const answerWindow = usePageWindow(
    answers,
    `${target.queryText}:${target.column.key}:${reloadToken}`,
    SAMPLING_ANSWERS_PAGE_SIZE,
  );

  useEffect(() => {
    let cancelled = false;
    setState({ kind: 'loading' });
    const answerPubIds = [...new Set(target.cell.answer_pub_ids ?? [])];
    const acceptedModes = new Set(effectiveModes(target.column, target.cell));
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
                acceptedModes.has(answer.mode),
            ) ?? null
          );
        } catch {
          return null;
        }
      }),
    ).then((loaded) => {
      if (cancelled) return;
      const answers = loaded
        .filter((answer): answer is AnswerRow => answer !== null)
        .sort(newestSamplingAnswerFirst);
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

  const platform = platformDisplayName(target.column.model);
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
          {platform}×{target.column.region} · 有效合计 {target.cell.completed_samples}遍 ·{' '}
          {modeBreakdownLabel(target.column, target.cell)}
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
              answers={answerWindow.visibleItems}
              onSelect={setSelected}
              ariaLabel="该采样位具体回答"
            />
            <Pagination
              page={answerWindow.page}
              pageCount={answerWindow.pageCount}
              windowSize={SAMPLING_ANSWERS_PAGE_NUMBER_WINDOW_SIZE}
              onPageChange={answerWindow.setPage}
              label="采样具体回答分页"
            />
          </>
        )}
      </div>
    </div>
  );
}

function SamplingProgressPanoramaDialog({
  state,
  onClose,
  onRetry,
  onAnswerTarget,
}: {
  state: PanoramaState;
  onClose: () => void;
  onRetry: () => void;
  onAnswerTarget: (target: SamplingAnswerTarget) => void;
}) {
  return (
    <div
      className="answer-detail-overlay sampling-progress-panorama-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="answer-detail sampling-progress-panorama"
        role="dialog"
        aria-modal="true"
        aria-label="采样进度全景"
        onKeyDown={(event) => {
          if (event.key === 'Escape') onClose();
        }}
      >
        <div className="answer-detail-head">
          <div>
            <h3>采样进度全景</h3>
            <p>完整问题 × 全部采样位，不分页展示</p>
          </div>
          <button type="button" autoFocus onClick={onClose}>
            关闭
          </button>
        </div>
        {state.kind === 'loading' ? (
          <p className="empty">正在加载全部采样进度…</p>
        ) : state.kind === 'failed' ? (
          <p className="empty">
            全景加载失败。<button onClick={onRetry}>重试</button>
          </p>
        ) : (
          <>
            <SamplingProgressSummary progress={state.progress} />
            <p className="sampling-progress-note">
              横向滚动查看全部采样位，纵向滚动查看全部问题；固定表头和问题列用于对照。
            </p>
            <div
              className="table-scroll sampling-progress-table-scroll sampling-progress-panorama-scroll"
              tabIndex={0}
              aria-label="采样进度全景滚动区域"
            >
              <SamplingProgressTable
                progress={state.progress}
                ariaLabel="采样进度全景表"
                onAnswerTarget={onAnswerTarget}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function SamplingProgressPanel({ session, projectPubId }: Props) {
  const [state, setState] = useState<LoadState>('loading');
  const [progress, setProgress] = useState<SamplingProgress | null>(null);
  const [page, setPage] = useState(1);
  const [answerTarget, setAnswerTarget] = useState<SamplingAnswerTarget | null>(null);
  const [panorama, setPanorama] = useState<PanoramaState | null>(null);
  const requestSerial = useRef(0);
  const panoramaRequestSerial = useRef(0);

  const refresh = useCallback(
    async (background = false) => {
      const requestId = ++requestSerial.current;
      if (!background) setState('loading');
      try {
        const next = await servicesApi.samplingProgress(
          session,
          projectPubId,
          page,
          SAMPLING_PROGRESS_DEFAULT_PAGE_SIZE,
        );
        if (requestId !== requestSerial.current) return;
        setProgress(next);
        setPage(next.page.page);
        setState('ready');
      } catch {
        if (requestId !== requestSerial.current) return;
        if (!background) setState('failed');
      }
    },
    [session, projectPubId, page],
  );

  useEffect(() => {
    setPage(1);
    setProgress(null);
    setPanorama(null);
    setState('loading');
    requestSerial.current += 1;
    panoramaRequestSerial.current += 1;
  }, [projectPubId]);

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

  const openPanorama = useCallback(async () => {
    const requestId = ++panoramaRequestSerial.current;
    setPanorama({ kind: 'loading' });
    try {
      const first = await servicesApi.samplingProgress(
        session,
        projectPubId,
        1,
        SAMPLING_PROGRESS_PANORAMA_PAGE_SIZE,
      );
      if (first.page.total_pages > SAMPLING_PROGRESS_PANORAMA_MAX_PAGES || first.page.page !== 1) {
        throw new Error('sampling_progress_panorama_page_limit');
      }
      const remaining = await Promise.all(
        Array.from({ length: Math.max(0, first.page.total_pages - 1) }, (_, index) =>
          servicesApi.samplingProgress(
            session,
            projectPubId,
            index + 2,
            SAMPLING_PROGRESS_PANORAMA_PAGE_SIZE,
          ),
        ),
      );
      if (requestId !== panoramaRequestSerial.current) return;
      const pages = [first, ...remaining];
      if (
        pages.some(
          (item, index) =>
            item.project_pub_id !== projectPubId ||
            item.page.page !== index + 1 ||
            item.page.total_count !== first.page.total_count ||
            item.page.total_pages !== first.page.total_pages,
        )
      ) {
        throw new Error('sampling_progress_panorama_page_mismatch');
      }
      const rows = pages.flatMap((item) => item.rows);
      if (rows.length !== first.page.total_count) {
        throw new Error('sampling_progress_panorama_incomplete');
      }
      setPanorama({
        kind: 'ready',
        progress: {
          ...first,
          rows,
          page: {
            ...first.page,
            page: 1,
            page_size: rows.length,
            total_pages: rows.length ? 1 : 0,
          },
        },
      });
    } catch {
      if (requestId === panoramaRequestSerial.current) setPanorama({ kind: 'failed' });
    }
  }, [session, projectPubId]);

  const closePanorama = () => {
    panoramaRequestSerial.current += 1;
    setPanorama(null);
  };
  return (
    <section className="execution-card sampling-progress-panel">
      <div className="section-title">
        <h2>采样进度</h2>
        <div className="sampling-progress-title-actions">
          <span>
            {progress?.live_runs
              ? `${progress.live_runs} 个 run 进行中，每 15 秒自动刷新`
              : '按最新完整采样批次汇总'}
          </span>
          {state === 'ready' && progress && progress.page.total_count > 0 ? (
            <button type="button" onClick={() => void openPanorama()}>
              查看全景
            </button>
          ) : null}
        </div>
      </div>
      {state === 'loading' ? (
        <p className="empty">正在汇总采样进度…</p>
      ) : state === 'failed' ? (
        <p className="empty">
          采样进度加载失败。<button onClick={() => void refresh()}>重试</button>
        </p>
      ) : !progress || progress.page.total_count === 0 ? (
        <p className="empty">该项目尚无可展示的冻结采样配置。</p>
      ) : (
        <>
          <SamplingProgressSummary progress={progress} />
          <p className="sampling-progress-note">
            每格仅汇总合格且非降级的有效回答，并保留平台×地域及实际模式明细；— = 尚无有效观测
          </p>
          <div className="table-scroll sampling-progress-table-scroll">
            <SamplingProgressTable
              progress={progress}
              ariaLabel="问题采样进度总览"
              onAnswerTarget={setAnswerTarget}
            />
          </div>
          <Pagination
            page={progress.page.page}
            pageCount={progress.page.total_pages}
            windowSize={SAMPLING_PROGRESS_PAGE_NUMBER_WINDOW_SIZE}
            totalItems={progress.page.total_count}
            onPageChange={setPage}
            label="采样进度问题分页"
          />
        </>
      )}
      {panorama ? (
        <SamplingProgressPanoramaDialog
          state={panorama}
          onClose={closePanorama}
          onRetry={() => void openPanorama()}
          onAnswerTarget={setAnswerTarget}
        />
      ) : null}
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
