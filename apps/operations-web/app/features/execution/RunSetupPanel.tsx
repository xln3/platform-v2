import { useEffect, useMemo, useState } from 'react';
import {
  executionApi,
  type FrozenConfig,
  type PlatformSla,
  type Project,
  type Schedule,
  type SessionContext,
} from './api';

const PLATFORMS = [
  ['doubao', '豆包'],
  ['deepseek', 'DeepSeek'],
  ['yiyan', '文心一言'],
  ['tongyi', '通义千问'],
  ['yuanbao', '腾讯元宝'],
] as const;

type SetupProps = {
  session: SessionContext;
  projects: Project[];
  onChanged: () => Promise<void>;
  onReceipt: (message: string) => void;
};

export function RunSetupPanel({ session, projects, onChanged, onReceipt }: SetupProps) {
  const canManage = session.role === 'operator' || session.role === 'admin';
  const [projectId, setProjectId] = useState(projects[0]?.pub_id ?? '');
  const [versions, setVersions] = useState<FrozenConfig[]>([]);
  const [projectName, setProjectName] = useState('');
  const [customerName, setCustomerName] = useState('');
  const [questions, setQuestions] = useState('');
  const [models, setModels] = useState<string[]>(PLATFORMS.map(([slug]) => slug));
  const [region, setRegion] = useState('CN');
  const [frequency, setFrequency] = useState('weekly');
  const [scheduleMinutes, setScheduleMinutes] = useState(10080);
  const [busy, setBusy] = useState(false);
  const latest = versions[0];

  useEffect(() => {
    if (!projectId && projects[0]) setProjectId(projects[0].pub_id);
  }, [projectId, projects]);

  useEffect(() => {
    if (!projectId) {
      setVersions([]);
      return;
    }
    let live = true;
    void executionApi.configVersions(session, projectId).then((items) => {
      if (live) setVersions(items);
    });
    return () => {
      live = false;
    };
  }, [projectId, session]);

  const questionItems = useMemo(
    () =>
      questions
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean),
    [questions],
  );
  const estimatedTasks = questionItems.length * models.length;

  async function createProject() {
    if (!projectName.trim() || !customerName.trim()) return;
    setBusy(true);
    try {
      const created = await executionApi.createProject(session, {
        name: projectName.trim(),
        customerName: customerName.trim(),
      });
      setProjectId(created.pub_id);
      setProjectName('');
      setCustomerName('');
      onReceipt(`项目已创建：${created.pub_id}`);
      await onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function freeze() {
    if (!projectId || questionItems.length === 0 || models.length === 0) return;
    setBusy(true);
    try {
      const frozen = await executionApi.freezeConfig(session, projectId, {
        queryGroups: [
          {
            name: '核心监测问题',
            items: questionItems.map((text, index) => ({ text, priority: index + 1 })),
          },
        ],
        regions: [region],
        models,
        modes: ['web'],
        frequency,
        effectiveAt: new Date().toISOString(),
      });
      setVersions((current) => [
        frozen,
        ...current.filter((item) => item.pub_id !== frozen.pub_id),
      ]);
      onReceipt(`配置 v${frozen.revision} 已冻结：${frozen.snapshot_hash.slice(0, 12)}`);
      await onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    if (!projectId || !latest) return;
    setBusy(true);
    try {
      const run = (await executionApi.startRun(session, projectId, latest.pub_id)) as {
        workflow_id?: string;
      };
      onReceipt(`采集已启动：${run.workflow_id ?? latest.pub_id}`);
      await onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function schedule() {
    if (!projectId || !latest) return;
    setBusy(true);
    try {
      const created = await executionApi.createSchedule(session, {
        projectId,
        configVersionId: latest.pub_id,
        intervalMinutes: scheduleMinutes,
        nextRunAt: new Date(Date.now() + scheduleMinutes * 60_000).toISOString(),
        responsiblePubId: session.actorId,
      });
      onReceipt(`周期任务已创建：${created.pub_id}`);
      await onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="execution-card setup-card">
      <div className="section-title">
        <h2>创建、冻结与启动</h2>
        <span>先冻结问题与平台矩阵，再启动一次性或周期采集</span>
      </div>
      {!canManage && <p className="empty">当前角色仅可查看，创建与启动由运营或管理员执行。</p>}
      <div className="setup-grid">
        <fieldset disabled={!canManage || busy}>
          <legend>1. 创建项目</legend>
          <label>
            客户名称
            <input value={customerName} onChange={(event) => setCustomerName(event.target.value)} />
          </label>
          <label>
            项目名称
            <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
          </label>
          <button type="button" onClick={() => void createProject()}>
            创建项目
          </button>
        </fieldset>

        <fieldset disabled={!canManage || busy || projects.length === 0}>
          <legend>2. 冻结监测配置</legend>
          <label>
            项目
            <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
              {projects.map((project) => (
                <option key={project.pub_id} value={project.pub_id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            监测问题（每行一条）
            <textarea
              rows={6}
              value={questions}
              onChange={(event) => setQuestions(event.target.value)}
              placeholder="品牌在 AI 搜索中的口碑如何？"
            />
          </label>
          <div className="platform-checks" aria-label="采集平台">
            {PLATFORMS.map(([slug, label]) => (
              <label key={slug}>
                <input
                  type="checkbox"
                  checked={models.includes(slug)}
                  onChange={(event) =>
                    setModels((current) =>
                      event.target.checked
                        ? [...current, slug]
                        : current.filter((item) => item !== slug),
                    )
                  }
                />
                {label}
              </label>
            ))}
          </div>
          <div className="inline-fields">
            <label>
              地域
              <input value={region} onChange={(event) => setRegion(event.target.value)} />
            </label>
            <label>
              频率标签
              <select value={frequency} onChange={(event) => setFrequency(event.target.value)}>
                <option value="daily">每日</option>
                <option value="weekly">每周</option>
                <option value="monthly">每月</option>
              </select>
            </label>
          </div>
          <p className="setup-summary">预计 {estimatedTasks} 个任务</p>
          <button type="button" onClick={() => void freeze()} disabled={estimatedTasks === 0}>
            冻结配置
          </button>
        </fieldset>

        <fieldset disabled={!canManage || busy || !latest}>
          <legend>3. 启动执行</legend>
          <p>
            当前冻结版本：<strong>{latest ? `v${latest.revision}` : '无'}</strong>
          </p>
          <p className="muted">{latest?.pub_id ?? '请先冻结监测配置'}</p>
          <button type="button" onClick={() => void start()}>
            立即启动一次采集
          </button>
          <label>
            周期间隔
            <select
              value={scheduleMinutes}
              onChange={(event) => setScheduleMinutes(Number(event.target.value))}
            >
              <option value={1440}>每天</option>
              <option value={10080}>每周</option>
              <option value={43200}>每 30 天</option>
            </select>
          </label>
          <button type="button" onClick={() => void schedule()}>
            创建周期任务
          </button>
        </fieldset>
      </div>
    </section>
  );
}

type ScheduleProps = {
  session: SessionContext;
  schedules: Schedule[];
  onChanged: () => Promise<void>;
  onReceipt: (message: string) => void;
};

export function SchedulePanel({ session, schedules, onChanged, onReceipt }: ScheduleProps) {
  const canManage = session.role === 'operator' || session.role === 'admin';

  async function changeState(item: Schedule) {
    const next = item.state === 'active' ? 'paused' : 'active';
    const updated = await executionApi.updateSchedule(session, item, next);
    onReceipt(`周期任务 ${updated.pub_id} 已${next === 'active' ? '恢复' : '暂停'}`);
    await onChanged();
  }

  async function runNow(item: Schedule) {
    const result = (await executionApi.runScheduleNow(session, item.pub_id)) as {
      workflow_id?: string;
    };
    onReceipt(`周期任务已立即执行：${result.workflow_id ?? item.pub_id}`);
    await onChanged();
  }

  return (
    <section className="execution-card">
      <div className="section-title">
        <h2>周期监测</h2>
        <span>幂等触发 · 暂停/恢复 · 责任人 · 下一次执行</span>
      </div>
      {schedules.length === 0 ? (
        <p className="empty">暂无周期任务。</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>任务</th>
                <th>状态</th>
                <th>周期</th>
                <th>下一次</th>
                <th>责任人</th>
                <th>控制</th>
              </tr>
            </thead>
            <tbody>
              {schedules.map((item) => (
                <tr key={item.pub_id}>
                  <td>{item.pub_id}</td>
                  <td>
                    <Status value={item.state} />
                  </td>
                  <td>{item.interval_minutes} 分钟</td>
                  <td>{new Date(item.next_run_at).toLocaleString('zh-CN')}</td>
                  <td>{item.responsible_pub_id}</td>
                  <td className="actions">
                    {canManage && item.state !== 'archived' && (
                      <>
                        <button type="button" onClick={() => void changeState(item)}>
                          {item.state === 'active' ? '暂停' : '恢复'}
                        </button>
                        <button type="button" onClick={() => void runNow(item)}>
                          立即执行
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function PlatformSlaPanel({ items }: { items: PlatformSla[] }) {
  return (
    <section className="execution-card">
      <div className="section-title">
        <h2>五平台会话与 SLA</h2>
        <span>会话 TTL · 成功率 · 人工接管率 · 超时待办</span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>平台</th>
              <th>健康</th>
              <th>成功率</th>
              <th>接管率</th>
              <th>会话 TTL</th>
              <th>超时待办</th>
              <th>责任人</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.platform}>
                <td>{item.display_name}</td>
                <td>
                  <Status value={item.state} />
                </td>
                <td>
                  {item.success_rate === null ? '待采样' : `${item.success_rate.toFixed(1)}%`}
                </td>
                <td>
                  {item.manual_takeover_rate === null
                    ? '待采样'
                    : `${item.manual_takeover_rate.toFixed(1)}%`}
                </td>
                <td>{item.session_ttl_minutes} 分钟</td>
                <td>{item.overdue_interventions}</td>
                <td>{item.owner_pub_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Status({ value }: { value: string }) {
  const tone = /active|healthy|completed/.test(value)
    ? 'ok'
    : /breached|failed|archived/.test(value)
      ? 'bad'
      : 'warn';
  return <span className={`status ${tone}`}>{value}</span>;
}
