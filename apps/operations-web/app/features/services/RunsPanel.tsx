import { useCallback, useEffect, useState } from 'react';
import { executionApi, type Run } from '../execution/api';
import { AnswerExplorer } from './AnswerExplorer';
import type { SessionContext } from './api';

export const TERMINAL_RUN_STATES: readonly string[] = [
  'completed',
  'completed_with_failures',
  'failed',
  'cancelled',
  'skipped',
];

type RunRow = Run & { created_at?: string | null };

type Props = {
  session: SessionContext;
  projectPubId: string;
  readOnly?: boolean;
};

export function RunsPanel({ session, projectPubId, readOnly = false }: Props) {
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [notice, setNotice] = useState<string | null>(null);
  const [answersOpenRunId, setAnswersOpenRunId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const items = (await executionApi.runs(session)) as RunRow[];
      setRuns(items.filter((run) => run.project_pub_id === projectPubId));
      setState('ready');
    } catch {
      setState('failed');
    }
  }, [session, projectPubId]);

  useEffect(() => {
    setState('loading');
    void refresh();
  }, [refresh]);

  const hasLive = runs.some((run) => !TERMINAL_RUN_STATES.includes(run.state as never));
  useEffect(() => {
    if (!hasLive) return;
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => window.clearInterval(timer);
  }, [hasLive, refresh]);

  async function control(run: RunRow, action: 'pause' | 'resume' | 'cancel' | 'retry') {
    setNotice(null);
    try {
      await executionApi.controlRun(session, run.pub_id, action);
      await refresh();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '控制操作失败');
    }
  }

  return (
    <section className="execution-card runs-panel">
      <div className="section-title">
        <h2>采样进度</h2>
        <span>{hasLive ? '存在进行中的 run，每 15 秒自动刷新' : '全部 run 已到终态'}</span>
      </div>
      {notice ? (
        <p className="launcher-error" role="alert">
          控制失败：{notice}
        </p>
      ) : null}
      {state === 'loading' ? (
        <p className="empty">正在加载运行列表…</p>
      ) : state === 'failed' ? (
        <p className="empty">
          运行列表加载失败。<button onClick={() => void refresh()}>重试</button>
        </p>
      ) : runs.length === 0 ? (
        <p className="empty">该项目尚无采集 run。冻结配置并启动采样后会出现在这里。</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>运行</th>
                <th>状态</th>
                <th>进度</th>
                <th>失败</th>
                <th>时间</th>
                <th>问答</th>
                {readOnly ? null : <th>控制</th>}
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <RunsPanelRow
                  key={run.pub_id}
                  run={run}
                  session={session}
                  projectPubId={projectPubId}
                  readOnly={readOnly}
                  answersOpen={answersOpenRunId === run.pub_id}
                  onToggleAnswers={() =>
                    setAnswersOpenRunId((current) => (current === run.pub_id ? null : run.pub_id))
                  }
                  onControl={(action) => void control(run, action)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function runTone(run: RunRow): 'ok' | 'warn' | 'bad' {
  if (run.paused) return 'warn';
  if (run.state === 'completed') return 'ok';
  if (['failed', 'cancelled', 'completed_with_failures'].includes(run.state)) return 'bad';
  return 'warn';
}

type RunsPanelRowProps = {
  run: RunRow;
  session: SessionContext;
  projectPubId: string;
  readOnly: boolean;
  answersOpen: boolean;
  onToggleAnswers: () => void;
  onControl: (action: 'pause' | 'resume' | 'cancel' | 'retry') => void;
};

function RunsPanelRow({
  run,
  session,
  projectPubId,
  readOnly,
  answersOpen,
  onToggleAnswers,
  onControl,
}: RunsPanelRowProps) {
  return (
    <>
      <tr>
        <td data-label="运行">{run.pub_id}</td>
        <td data-label="状态">
          <span className={`status ${runTone(run)}`}>{run.paused ? 'paused' : run.state}</span>
        </td>
        <td data-label="进度">
          {run.completed_tasks}/{run.total_tasks}
        </td>
        <td data-label="失败">{run.failed_tasks}</td>
        <td data-label="时间">
          {new Date(run.created_at ?? run.updated_at).toLocaleString('zh-CN')}
        </td>
        <td data-label="问答">
          <button aria-expanded={answersOpen} onClick={onToggleAnswers}>
            {answersOpen ? '收起' : '问答'}
          </button>
        </td>
        {readOnly ? null : (
          <td data-label="控制" className="actions">
            {(run.paused || ['pending', 'queued', 'running'].includes(run.state)) && (
              <button onClick={() => onControl(run.paused ? 'resume' : 'pause')}>
                {run.paused ? '恢复' : '暂停'}
              </button>
            )}
            {['pending', 'queued', 'running', 'waiting_intervention'].includes(run.state) && (
              <button className="danger" onClick={() => onControl('cancel')}>
                取消
              </button>
            )}
            {['failed', 'completed_with_failures', 'cancelled', 'skipped'].includes(run.state) && (
              <button onClick={() => onControl('retry')}>重试</button>
            )}
          </td>
        )}
      </tr>
      {answersOpen ? (
        <tr className="answer-explorer-row">
          <td colSpan={readOnly ? 6 : 7}>
            <AnswerExplorer session={session} projectPubId={projectPubId} runPubId={run.pub_id} />
          </td>
        </tr>
      ) : null}
    </>
  );
}
