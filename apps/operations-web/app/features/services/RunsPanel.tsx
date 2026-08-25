import { Dialog, Pagination } from '@geo/design-system';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNumberedCollection } from '../../pagination';
import { executionApi, type Run, type RunSummary } from '../execution/api';
import {
  COLLECTION_RUNS_DEFAULT_PAGE_SIZE,
  COLLECTION_RUNS_PAGE_NUMBER_WINDOW_SIZE,
} from '../execution/pagination-policy';
import { AnswerExplorer } from './AnswerExplorer';
import type { SessionContext } from './api';

export const TERMINAL_RUN_STATES: readonly string[] = [
  'completed',
  'completed_with_failures',
  'failed',
  'cancelled',
  'skipped',
];

type Props = {
  session: SessionContext;
  projectPubId: string;
  readOnly?: boolean;
};

export function RunsPanel({ session, projectPubId, readOnly = false }: Props) {
  const loadPage = useCallback(
    (page: number) =>
      executionApi.runPage(session, {
        projectPubId,
        page,
        pageSize: COLLECTION_RUNS_DEFAULT_PAGE_SIZE,
      }),
    [session, projectPubId],
  );
  const runsPage = useNumberedCollection(loadPage, projectPubId);
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [answersOpenRunId, setAnswersOpenRunId] = useState<string | null>(null);
  const summaryRequestSerial = useRef(0);
  const currentProjectPubId = useRef(projectPubId);

  useEffect(() => {
    currentProjectPubId.current = projectPubId;
    summaryRequestSerial.current += 1;
    setSummary(null);
    return () => {
      summaryRequestSerial.current += 1;
    };
  }, [projectPubId]);

  const refreshSummary = useCallback(async () => {
    const requestId = ++summaryRequestSerial.current;
    const requestedProjectPubId = projectPubId;
    try {
      const next = await executionApi.runSummary(session, requestedProjectPubId);
      if (
        requestId === summaryRequestSerial.current &&
        currentProjectPubId.current === requestedProjectPubId &&
        next.project_pub_id === requestedProjectPubId
      ) {
        setSummary(next);
      }
    } catch {
      if (
        requestId === summaryRequestSerial.current &&
        currentProjectPubId.current === requestedProjectPubId
      ) {
        setSummary(null);
      }
    }
  }, [session, projectPubId]);

  useEffect(() => {
    void refreshSummary();
  }, [refreshSummary]);

  const hasLive = (summary?.active_run_count ?? 0) > 0;
  useEffect(() => {
    if (!hasLive) return;
    const timer = window.setInterval(() => {
      void runsPage.refresh(true);
      void refreshSummary();
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [hasLive, refreshSummary, runsPage.refresh]);

  async function control(run: Run, action: 'pause' | 'resume' | 'cancel' | 'retry') {
    setNotice(null);
    try {
      await executionApi.controlRun(session, run.pub_id, action);
      await Promise.all([runsPage.refresh(), refreshSummary()]);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '控制操作失败');
    }
  }

  return (
    <section className="execution-card runs-panel">
      <div className="section-title">
        <h2>采样记录</h2>
        <span>
          {summary
            ? `项目共 ${summary.run_count} 个 run${hasLive ? '，进行中时每 15 秒刷新当前页' : ''}`
            : '按创建时间稳定倒序'}
        </span>
      </div>
      {notice ? (
        <p className="launcher-error" role="alert">
          控制失败：{notice}
        </p>
      ) : null}
      {runsPage.state === 'loading' ? (
        <p className="empty">正在加载运行列表…</p>
      ) : runsPage.state === 'failed' ? (
        <p className="empty">
          运行列表加载失败。<button onClick={() => void runsPage.refresh()}>重试</button>
        </p>
      ) : runsPage.data.length === 0 ? (
        <p className="empty">该项目尚无采集 run。配置在客户/项目页生效后会显示在这里。</p>
      ) : (
        <>
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
                {runsPage.data.map((run) => (
                  <RunsPanelRow
                    key={run.pub_id}
                    run={run}
                    readOnly={readOnly}
                    onOpenAnswers={() => setAnswersOpenRunId(run.pub_id)}
                    onControl={(action) => void control(run, action)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={runsPage.pageNumber}
            pageCount={runsPage.meta?.totalPages ?? 1}
            totalItems={runsPage.meta?.totalCount ?? summary?.run_count ?? 0}
            windowSize={COLLECTION_RUNS_PAGE_NUMBER_WINDOW_SIZE}
            onPageChange={runsPage.goToPage}
            label="采样记录分页"
          />
        </>
      )}
      {answersOpenRunId ? (
        <Dialog
          title={`运行 ${answersOpenRunId} 的问答`}
          closeLabel="关闭运行问答"
          size="wide"
          onClose={() => setAnswersOpenRunId(null)}
        >
          <AnswerExplorer
            session={session}
            projectPubId={projectPubId}
            runPubId={answersOpenRunId}
          />
        </Dialog>
      ) : null}
    </section>
  );
}

function runTone(run: Run): 'ok' | 'warn' | 'bad' {
  if (run.paused) return 'warn';
  if (run.state === 'completed') return 'ok';
  if (['failed', 'cancelled', 'completed_with_failures'].includes(run.state)) return 'bad';
  return 'warn';
}

function RunsPanelRow({
  run,
  readOnly,
  onOpenAnswers,
  onControl,
}: {
  run: Run;
  readOnly: boolean;
  onOpenAnswers: () => void;
  onControl: (action: 'pause' | 'resume' | 'cancel' | 'retry') => void;
}) {
  return (
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
        {new Date(run.created_at || run.updated_at).toLocaleString('zh-CN')}
      </td>
      <td data-label="问答">
        <button type="button" onClick={onOpenAnswers}>
          问答
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
  );
}
