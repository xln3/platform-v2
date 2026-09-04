import { useEffect, useState } from 'react';
import {
  servicesApi,
  type FormalReportProductionProgress,
  type FormalReportProductionStage,
  type SessionContext,
} from '../api';

export const STAGE_LABELS: Record<FormalReportProductionStage, string> = {
  queued: '排队',
  binding_snapshot: '绑定快照',
  preflight: '预检',
  running: '生成报告',
  awaiting_review: '待审阅',
  finalizing: '签发处理',
  signed: '已签发',
};

const STAGE_NAMES = new Set<FormalReportProductionStage>([
  'queued',
  'binding_snapshot',
  'preflight',
  'running',
  'awaiting_review',
  'finalizing',
  'signed',
]);
const STAGE_STATUSES = new Set(['done', 'current', 'pending', 'failed']);

export function isTerminalProgress(progress: FormalReportProductionProgress): boolean {
  return progress.failed || progress.stages.every((stage) => stage.status === 'done');
}

function isProgressPayload(value: unknown): value is FormalReportProductionProgress {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.failed !== 'boolean') return false;
  if (candidate.source !== 'workflow' && candidate.source !== 'db_fallback') return false;
  if (!Array.isArray(candidate.stages)) return false;
  return candidate.stages.every((stage) => {
    if (typeof stage !== 'object' || stage === null) return false;
    const row = stage as Record<string, unknown>;
    return (
      typeof row.stage === 'string' &&
      STAGE_NAMES.has(row.stage as FormalReportProductionStage) &&
      typeof row.status === 'string' &&
      STAGE_STATUSES.has(row.status)
    );
  });
}

export function ProductionProgressStepperView({
  progress,
}: {
  progress: FormalReportProductionProgress;
}) {
  return (
    <div className="formal-progress" aria-label="生产进度">
      <ol className="formal-progress-steps">
        {progress.stages.map((stage) => (
          <li key={stage.stage} className={`formal-progress-step ${stage.status}`}>
            <span className="formal-progress-dot" aria-hidden="true">
              {stage.status === 'done' ? '✓' : stage.status === 'failed' ? '✗' : ''}
            </span>
            <span className="formal-progress-label">{STAGE_LABELS[stage.stage]}</span>
          </li>
        ))}
      </ol>
      {progress.failed && progress.error_code ? (
        <small className="formal-progress-error">失败：{progress.error_code}</small>
      ) : null}
      {progress.source === 'db_fallback' ? (
        <small className="formal-progress-source">工作流不可达，显示库内状态</small>
      ) : null}
    </div>
  );
}

export function ProductionProgressStepper({
  session,
  productionPubId,
}: {
  session: SessionContext;
  productionPubId: string;
}) {
  const [progress, setProgress] = useState<FormalReportProductionProgress | null>(null);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const value = await servicesApi.formalReportProductionProgress(session, productionPubId);
        if (!active) return;
        if (!isProgressPayload(value)) {
          // 形状不符（旧 API/代理错误页）：静默回退到状态徽章，不渲染进度条。
          setProgress(null);
          return;
        }
        setProgress(value);
        if (!isTerminalProgress(value)) {
          timer = window.setTimeout(() => void tick(), 15_000);
        }
      } catch {
        // 拉取失败静默：行内状态徽章仍是权威状态源。
        if (active) setProgress(null);
      }
    };
    void tick();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [session, productionPubId]);

  if (!progress) return null;
  return <ProductionProgressStepperView progress={progress} />;
}
