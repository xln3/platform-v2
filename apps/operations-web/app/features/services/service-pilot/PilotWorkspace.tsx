import { useEffect, useState } from 'react';
import { ConfigLauncher } from '../ConfigLauncher';
import { RunsPanel } from '../RunsPanel';
import { WindowPicker } from '../WindowPicker';
import {
  defaultWindow,
  servicesApi,
  type PilotDelta,
  type Project,
  type SessionContext,
} from '../api';
import { ComparisonPanel } from './ComparisonPanel';

const METRIC_LABELS: Record<string, string> = {
  mention_rate: '品牌提及率',
  average_rank: '平均排名',
  top3_rate: 'Top 3 占比',
  citation_coverage: '引用覆盖',
};

function formatValue(metric: string, value: number | null): string {
  if (value === null) return '—';
  return metric === 'average_rank' ? value.toFixed(2) : `${(value * 100).toFixed(1)}%`;
}

function formatDelta(metric: string, value: number | null): string {
  if (value === null) return '—';
  const sign = value > 0 ? '+' : '';
  return metric === 'average_rank'
    ? `${sign}${value.toFixed(2)}`
    : `${sign}${(value * 100).toFixed(1)}%`;
}

type DeltaState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: PilotDelta; configVersion: string | null }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

export function PilotWorkspace({
  session,
  project,
}: {
  session: SessionContext;
  project: Project;
}) {
  const [window_, setWindow] = useState(defaultWindow);
  const [delta, setDelta] = useState<DeltaState>({ kind: 'loading' });
  const [runsVersion, setRunsVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setDelta({ kind: 'loading' });
    void (async () => {
      // 防稀释口径：界面上有当前冻结配置（最新 revision）时，delta 只统计该配置
      // 产出的答案；项目尚无冻结配置或清单不可达时回退全量口径（不带参数）。
      let configVersion: string | undefined;
      try {
        const versions = await servicesApi.configVersions(session, project.pub_id);
        const latest = versions.reduce<(typeof versions)[number] | null>(
          (best, item) => (best === null || item.revision > best.revision ? item : best),
          null,
        );
        configVersion = latest?.pub_id;
      } catch {
        configVersion = undefined;
      }
      if (cancelled) return;
      const result = await servicesApi.analyticsDelta(session, {
        projectPubId: project.pub_id,
        start: window_.start,
        end: window_.end,
        configVersion,
      });
      if (cancelled) return;
      setDelta(
        result.kind === 'ready'
          ? { kind: 'ready', data: result.data, configVersion: configVersion ?? null }
          : { kind: result.kind },
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [session, project.pub_id, window_.start, window_.end, runsVersion]);

  const metrics = delta.kind === 'ready' ? Object.entries(delta.data) : [];

  return (
    <>
      <p className="service-note">
        优化前用试点查询集跑一次基线，优化后用同一查询集复测，对比前后指标。
      </p>
      <ConfigLauncher
        session={session}
        projectPubId={project.pub_id}
        groupName="GEO试点验证"
        queryPlaceholder="目标 AI 平台中查询品牌相关问题时推荐了什么"
        onChanged={() => setRunsVersion((current) => current + 1)}
      />
      <RunsPanel session={session} projectPubId={project.pub_id} key={runsVersion} />
      <section className="execution-card">
        <div className="section-title">
          <h2>效果对比</h2>
          <span>
            {window_.start} ~ {window_.end} · 与前一等长窗口对比
            {delta.kind === 'ready' && delta.configVersion
              ? ` · 口径：冻结配置 ${delta.configVersion}`
              : ''}
          </span>
        </div>
        <WindowPicker start={window_.start} end={window_.end} onChange={setWindow} />
        {delta.kind === 'loading' ? (
          <p className="empty">正在计算前后对比…</p>
        ) : delta.kind !== 'ready' ? (
          <p className="empty">
            {delta.kind === 'forbidden' ? '权限不足，无法读取对比数据。' : '对比数据暂不可用。'}
          </p>
        ) : metrics.length === 0 ? (
          <p className="empty">该时间窗内无可对比指标——先跑一次基线采集，优化后复测。</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>指标</th>
                  <th>前一期</th>
                  <th>本期</th>
                  <th>变化</th>
                </tr>
              </thead>
              <tbody>
                {metrics.map(([metric, value]) => (
                  <tr key={metric}>
                    <td>{METRIC_LABELS[metric] ?? metric}</td>
                    <td>{formatValue(metric, value?.previous ?? null)}</td>
                    <td>{formatValue(metric, value?.current ?? null)}</td>
                    <td>{formatDelta(metric, value?.delta ?? null)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <ComparisonPanel session={session} project={project} runsVersion={runsVersion} />

      <div className="service-link-card">
        <div>
          <strong>需要发布信源内容来提升引用？</strong>
          <p>信源 SOP 工作区覆盖从查询基线、文章产出到发布后引用归因的全流程。</p>
        </div>
        <a href="/platform/operations/sop">前往信源 SOP →</a>
      </div>
    </>
  );
}
