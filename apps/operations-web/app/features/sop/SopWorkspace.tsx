import { useEffect, useMemo, useState, type ComponentType } from 'react';
import {
  getSopDashboard,
  loadSopStage,
  mutateSopStage,
  type IdentitySessionHeaders,
  type SopDashboard,
  type SopMutationCommand,
  type SopStageKey,
  type SopStageSnapshot,
} from '@geo/api-client';
import { Badge, MetricGrid, StatePanel, Toast } from '@geo/design-system';
import {
  ArchiveLog,
  Baseline,
  Comparison,
  EvidenceLedger,
  Experiments,
  IndexWatch,
  Opportunities,
  PrePublish,
  ProjectDefinition,
  Publishing,
  QuerySet,
  Retest,
  RetrievalReview,
  Writing,
  type SopStepProps,
} from './steps';
import './sop.css';

const stepComponents: Record<SopStageKey, ComponentType<SopStepProps>> = {
  'project-definition': ProjectDefinition,
  'query-set': QuerySet,
  baseline: Baseline,
  'retrieval-review': RetrievalReview,
  'evidence-ledger': EvidenceLedger,
  opportunities: Opportunities,
  writing: Writing,
  'pre-publish': PrePublish,
  publishing: Publishing,
  'index-watch': IndexWatch,
  retest: Retest,
  comparison: Comparison,
  experiments: Experiments,
  'archive-log': ArchiveLog,
};

type DashboardState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: SopDashboard }
  | { kind: 'forbidden' }
  | { kind: 'failed' };

export function SopWorkspace({
  projectPubId,
  headers,
  canWrite,
}: {
  projectPubId: string;
  headers: IdentitySessionHeaders;
  canWrite: boolean;
}) {
  const [dashboard, setDashboard] = useState<DashboardState>({ kind: 'loading' });
  const [selected, setSelected] = useState<SopStageKey>('project-definition');
  const [tab, setTab] = useState<'monitor' | 'console'>('monitor');
  const [snapshot, setSnapshot] = useState<SopStageSnapshot | null>(null);
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'failed' | 'forbidden'>(
    'loading',
  );
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'positive' | 'negative'; text: string } | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    setDashboard((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    void getSopDashboard(headers, projectPubId).then((result) => {
      if (cancelled) return;
      setDashboard(
        result.kind === 'ready'
          ? { kind: 'ready', data: result.data }
          : result.kind === 'forbidden'
            ? { kind: 'forbidden' }
            : { kind: 'failed' },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [headers, projectPubId, attempt]);

  useEffect(() => {
    let cancelled = false;
    setLoadState('loading');
    setSnapshot(null);
    void loadSopStage(headers, projectPubId, selected).then((result) => {
      if (cancelled) return;
      if (result.kind === 'ready') {
        setSnapshot(result.data);
        setLoadState('ready');
      } else {
        setLoadState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [headers, projectPubId, selected, attempt]);

  const progress = useMemo(() => {
    if (dashboard.kind !== 'ready') return { done: 0, total: 14 };
    return {
      done: dashboard.data.steps.filter((step) => step.status === 'done').length,
      total: dashboard.data.steps.length,
    };
  }, [dashboard]);

  if (dashboard.kind === 'loading') {
    return (
      <main className="sop-page">
        <StatePanel state="loading" />
      </main>
    );
  }
  if (dashboard.kind === 'forbidden') {
    return (
      <main className="sop-page">
        <StatePanel state="forbidden" />
      </main>
    );
  }
  if (dashboard.kind === 'failed') {
    return (
      <main className="sop-page">
        <StatePanel state="failed" onRetry={() => setAttempt((value) => value + 1)} />
      </main>
    );
  }

  const activeStep =
    dashboard.data.steps.find((step) => step.key === selected) ?? dashboard.data.steps[0]!;
  const ActiveStep = stepComponents[activeStep.key];
  const submit = async (command: SopMutationCommand) => {
    setBusy(true);
    setNotice(null);
    const result = await mutateSopStage(headers, command, `sop-${globalThis.crypto.randomUUID()}`);
    setBusy(false);
    if (result.kind === 'ready') {
      const related = result.data.relatedPubId ? `；关联 ID ${result.data.relatedPubId}` : '';
      setNotice({ tone: 'positive', text: `${result.data.message}${related}` });
      setAttempt((value) => value + 1);
      setTab('monitor');
    } else {
      setNotice({
        tone: 'negative',
        text:
          result.kind === 'forbidden'
            ? '当前角色无权执行此操作。'
            : '操作失败，请核对前置 ID 与状态。',
      });
    }
  };

  return (
    <main className="sop-page" aria-label="信源 SOP 项目工作区">
      <section className="sop-workspace-head">
        <div>
          <span className="overline">SOP project</span>
          <h2>{dashboard.data.project.name}</h2>
          <p>
            {dashboard.data.project.brandStandardName} · <code>{projectPubId}</code>
          </p>
        </div>
        <div className="sop-progress-card">
          <strong>
            {progress.done}/{progress.total}
          </strong>
          <span>步骤达标</span>
          <progress max={progress.total} value={progress.done} aria-label="SOP 完成进度" />
        </div>
      </section>

      {dashboard.data.articles.length > 0 ? (
        <MetricGrid
          metrics={dashboard.data.articles.slice(0, 4).map((article) => ({
            label: article.title,
            value: article.maturityLevel,
            detail: `${article.versionCount} 个版本 · ${article.status}`,
          }))}
        />
      ) : null}

      <div className="sop-workspace">
        <aside className="sop-stepper" aria-label="SOP 步骤">
          {dashboard.data.steps.map((step, index) => (
            <button
              key={step.key}
              className={step.key === selected ? 'active' : ''}
              aria-current={step.key === selected ? 'step' : undefined}
              onClick={() => {
                setSelected(step.key);
                setTab('monitor');
              }}
            >
              <span className="sop-step-number">{String(index + 1).padStart(2, '0')}</span>
              <span>
                <small>{step.stage}</small>
                <strong>{step.name}</strong>
              </span>
              <Badge
                tone={
                  step.status === 'done'
                    ? 'positive'
                    : step.status === 'in_progress'
                      ? 'warning'
                      : 'neutral'
                }
              >
                {step.status === 'done'
                  ? '完成'
                  : step.status === 'in_progress'
                    ? '进行中'
                    : '未开始'}
              </Badge>
            </button>
          ))}
        </aside>

        <section className="sop-stage">
          <div className="sop-stage-head">
            <div>
              <span className="overline">{activeStep.stage}</span>
              <h3>{activeStep.name}</h3>
            </div>
            <button
              className="button button-secondary"
              onClick={() => setAttempt((value) => value + 1)}
            >
              刷新数据
            </button>
          </div>
          <div className="sop-tabs" role="tablist" aria-label={`${activeStep.name}视图`}>
            <button
              role="tab"
              aria-selected={tab === 'monitor'}
              className={tab === 'monitor' ? 'active' : ''}
              onClick={() => setTab('monitor')}
            >
              监测
            </button>
            <button
              role="tab"
              aria-selected={tab === 'console'}
              className={tab === 'console' ? 'active' : ''}
              onClick={() => setTab('console')}
            >
              操作台
            </button>
          </div>
          <ActiveStep
            projectPubId={projectPubId}
            tab={tab}
            step={activeStep}
            snapshot={snapshot}
            loadState={loadState}
            canWrite={canWrite}
            busy={busy}
            onRetry={() => setAttempt((value) => value + 1)}
            onSubmit={submit}
          />
        </section>
      </div>
      {notice ? <Toast tone={notice.tone}>{notice.text}</Toast> : null}
    </main>
  );
}
