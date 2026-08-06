import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  executionApi,
  type Account,
  type BreakGlassRequest,
  type Intervention,
  type Pairing,
  type PlatformSla,
  type Project,
  type Run,
  type Schedule,
  type SessionContext,
  type SessionEvent,
} from './api';
import { PlatformSlaPanel, RunSetupPanel, SchedulePanel } from './RunSetupPanel';
import './execution.css';

const CHALLENGES: Record<string, string> = {
  otp: 'OTP',
  qr: '扫码',
  push: 'Push MFA',
  passkey: 'Passkey',
  face: '人脸/活体跳转',
  graphical: '图形 Challenge',
};

type Props = { session: SessionContext };
type PairingState = {
  pairing: Pairing;
  customerDevice: boolean;
  bundle: string | null;
};

export function ExecutionControlPlane({ session }: Props) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [platformSla, setPlatformSla] = useState<PlatformSla[]>([]);
  const [interventions, setInterventions] = useState<Intervention[]>([]);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [breakGlassRequests, setBreakGlassRequests] = useState<BreakGlassRequest[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'offline' | 'forbidden'>('loading');
  const [receipt, setReceipt] = useState<string | null>(null);
  const [pairings, setPairings] = useState<Record<string, PairingState>>({});

  const refresh = useCallback(async () => {
    try {
      const [
        nextAccounts,
        nextProjects,
        nextRuns,
        nextInterventions,
        nextBreakGlass,
        nextSchedules,
        nextPlatformSla,
      ] = await Promise.all([
        executionApi.accounts(session),
        executionApi.projects(session),
        executionApi.runs(session),
        executionApi.interventions(session),
        executionApi.breakGlassRequests(session),
        executionApi.schedules(session),
        executionApi.platformSla(session),
      ]);
      setAccounts(nextAccounts);
      setProjects(nextProjects.data);
      setRuns(nextRuns);
      setInterventions(nextInterventions);
      setBreakGlassRequests(nextBreakGlass);
      setSchedules(nextSchedules);
      setPlatformSla(nextPlatformSla);
      const eventPages = await Promise.all(
        nextAccounts.slice(0, 20).map((account) => executionApi.events(session, account.pub_id)),
      );
      setEvents(
        eventPages
          .flat()
          .sort((left, right) => right.occurred_at.localeCompare(left.occurred_at))
          .slice(0, 50),
      );
      setState('ready');
    } catch (error) {
      setState(
        error instanceof Error && error.message === 'permission_denied' ? 'forbidden' : 'offline',
      );
    }
  }, [session]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const progress = useMemo(() => {
    const total = runs.reduce((sum, run) => sum + run.total_tasks, 0);
    const completed = runs.reduce((sum, run) => sum + run.completed_tasks, 0);
    return { total, completed };
  }, [runs]);

  async function riskyAction(label: string, action: () => Promise<unknown>) {
    if (!window.confirm(`${label}属于高风险操作。确认权限、影响范围与审计责任后继续？`)) return;
    const result = (await action()) as { workflow_id?: string };
    setReceipt(`审计回执：${result.workflow_id ?? new Date().toISOString()}`);
    await refresh();
  }

  async function pair(item: Intervention) {
    const pairing = await executionApi.pairIntervention(session, item.pub_id);
    const customerDevice =
      accounts.find((account) => account.pub_id === item.account_pub_id)?.custody_mode ===
      'customer_device';
    const bundle = customerDevice
      ? JSON.stringify({
          api_base: window.location.origin,
          tenant_pub_id: session.tenantId,
          intervention_pub_id: pairing.intervention_pub_id,
          pairing_token: pairing.pairing_token,
          server_public_key_sha256: pairing.server_public_key_sha256,
          allowed_domain: pairing.allowed_domain,
          action: pairing.action,
          challenge_type: pairing.challenge_type,
        })
      : null;
    setPairings((current) => ({
      ...current,
      [item.pub_id]: { pairing, customerDevice, bundle },
    }));
    if (!customerDevice)
      window.open(`https://${item.allowed_domain}`, '_blank', 'noopener,noreferrer');
    setReceipt(
      customerDevice
        ? `客户终端配对包已生成，将于 ${pairing.expires_at} 失效；只能交给受控 GEO 终端扩展。`
        : `安全配对已建立，将于 ${pairing.expires_at} 失效；令牌仅保存在当前页面内存。`,
    );
  }

  async function complete(item: Intervention) {
    const pairingState = pairings[item.pub_id];
    if (!pairingState || pairingState.customerDevice) return;
    const bytes = new TextEncoder().encode(
      `${item.pub_id}:${item.account_pub_id}:${new Date().toISOString()}`,
    );
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const evidenceHash = [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, '0'))
      .join('');
    await executionApi.completeIntervention(
      session,
      item.pub_id,
      pairingState.pairing.pairing_token,
      evidenceHash,
    );
    setPairings((current) => {
      const next = { ...current };
      delete next[item.pub_id];
      return next;
    });
    setReceipt(`人工接管审计回执：${item.pub_id} 已由目标平台状态确认。`);
    await refresh();
  }

  if (state === 'loading') return <div className="execution-state">正在连接执行控制面…</div>;
  if (state === 'forbidden')
    return <div className="execution-state danger">权限不足：需要授权运营角色。</div>;
  if (state === 'offline')
    return (
      <div className="execution-state warning">
        连接中断，已保留当前视图。<button onClick={() => void refresh()}>重新连接</button>
      </div>
    );

  return (
    <main className="execution-plane">
      <header className="execution-heading">
        <div>
          <span className="eyebrow">Temporal-backed operations</span>
          <h1>执行与账号控制面</h1>
          <p>所有控制经 FastAPI 与工作流 Signal；页面只展示掩码和无秘密摘要。</p>
        </div>
        <button onClick={() => void refresh()}>刷新</button>
      </header>
      {receipt && <div className="receipt">{receipt}</div>}
      <section className="metric-row">
        <article>
          <span>任务进度</span>
          <strong>
            {progress.completed}/{progress.total}
          </strong>
        </article>
        <article>
          <span>活动运行</span>
          <strong>{runs.filter((run) => run.state === 'running').length}</strong>
        </article>
        <article>
          <span>待人工</span>
          <strong>{interventions.filter((item) => item.state !== 'completed').length}</strong>
        </article>
        <article>
          <span>账号健康</span>
          <strong>
            {accounts.filter((item) => item.state === 'active').length}/{accounts.length}
          </strong>
        </article>
      </section>

      <RunSetupPanel
        session={session}
        projects={projects}
        onChanged={refresh}
        onReceipt={setReceipt}
      />

      <section className="execution-card">
        <div className="section-title">
          <h2>项目与冻结计划</h2>
          <span>项目状态 · 生效配置 · 最近更新</span>
        </div>
        {projects.length === 0 ? (
          <p className="empty">尚无项目或冻结执行计划。</p>
        ) : (
          <div className="project-list">
            {projects.map((project) => {
              const plans = runs.filter((run) => run.project_pub_id === project.pub_id);
              return (
                <article key={project.pub_id}>
                  <div>
                    <strong>{project.name}</strong>
                    <code>{project.pub_id}</code>
                  </div>
                  <Status value={project.state} />
                  <span>{plans.length} 个执行计划</span>
                  <span>{plans[0]?.config_version_pub_id ?? '尚未以冻结配置启动'}</span>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section className="execution-card">
        <div className="section-title">
          <h2>运行与任务矩阵</h2>
          <span>实时进度 · 失败 · 延迟 · 数据新鲜度</span>
        </div>
        {runs.length === 0 ? (
          <p className="empty">暂无运行。冻结配置并启动采集后会出现在这里。</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>运行</th>
                  <th>项目</th>
                  <th>状态</th>
                  <th>进度</th>
                  <th>失败</th>
                  <th>新鲜度/延迟</th>
                  <th>控制</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.pub_id}>
                    <td data-label="运行">{run.pub_id}</td>
                    <td data-label="项目">
                      {run.project_pub_id}
                      <small className="run-source">
                        {run.source}
                        {run.retry_of_run_pub_id ? ` · 重试 ${run.retry_of_run_pub_id}` : ''}
                      </small>
                    </td>
                    <td data-label="状态">
                      <Status value={run.paused ? 'paused' : run.state} />
                    </td>
                    <td data-label="进度">
                      {run.completed_tasks}/{run.total_tasks}
                    </td>
                    <td data-label="失败">{run.failed_tasks}</td>
                    <td data-label="新鲜度">
                      {new Date(run.updated_at).toLocaleString('zh-CN')}
                      {run.error_code ? ` · ${run.error_code}` : ''}
                    </td>
                    <td data-label="控制" className="actions">
                      {(run.paused || ['pending', 'queued', 'running'].includes(run.state)) && (
                        <button
                          onClick={() =>
                            void executionApi
                              .controlRun(session, run.pub_id, run.paused ? 'resume' : 'pause')
                              .then(refresh)
                          }
                        >
                          {run.paused ? '恢复' : '暂停'}
                        </button>
                      )}
                      {['pending', 'queued', 'running', 'waiting_intervention'].includes(
                        run.state,
                      ) && (
                        <button
                          className="danger"
                          onClick={() =>
                            void riskyAction('取消运行', () =>
                              executionApi.controlRun(session, run.pub_id, 'cancel'),
                            )
                          }
                        >
                          取消
                        </button>
                      )}
                      {['failed', 'completed_with_failures', 'cancelled', 'skipped'].includes(
                        run.state,
                      ) && (
                        <button
                          onClick={() =>
                            void riskyAction('重试运行', () =>
                              executionApi.controlRun(session, run.pub_id, 'retry'),
                            )
                          }
                        >
                          重试
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <SchedulePanel
        session={session}
        schedules={schedules}
        onChanged={refresh}
        onReceipt={setReceipt}
      />

      <PlatformSlaPanel items={platformSla} />

      <section className="execution-card">
        <div className="section-title">
          <h2>平台账号目录与 Profile 健康</h2>
          <span>真实准入等级 · 授权 · 托管模式 · 租约</span>
        </div>
        {accounts.length === 0 ? (
          <p className="empty">尚未登记平台账号。</p>
        ) : (
          <div className="account-grid">
            {accounts.map((account) => (
              <article key={account.pub_id} className="account-card">
                <div>
                  <span className="platform">{account.platform}</span>
                  <Status value={account.admission_level} />
                </div>
                <h3>{account.account_mask}</h3>
                <dl>
                  <dt>Owner</dt>
                  <dd>{account.owner_pub_id}</dd>
                  <dt>用途</dt>
                  <dd>{account.purpose}</dd>
                  <dt>托管</dt>
                  <dd>{account.custody_mode}</dd>
                  <dt>地域</dt>
                  <dd>{account.region}</dd>
                  <dt>Scope</dt>
                  <dd>{account.scopes.join(' · ') || '未授权'}</dd>
                  <dt>授权到期</dt>
                  <dd>
                    {account.authorization_expires_at
                      ? new Date(account.authorization_expires_at).toLocaleString('zh-CN')
                      : '无有效授权'}
                  </dd>
                  <dt>Profile</dt>
                  <dd>
                    {account.profile_state
                      ? `${account.profile_state} · v${account.profile_version}`
                      : '尚未 enroll'}
                  </dd>
                  <dt>Profile 到期</dt>
                  <dd>
                    {account.profile_expires_at
                      ? new Date(account.profile_expires_at).toLocaleString('zh-CN')
                      : '未设置'}
                  </dd>
                  <dt>约束</dt>
                  <dd>{account.profile_constraints.join(' · ') || '无'}</dd>
                  <dt>活动租约</dt>
                  <dd>
                    {account.lease_expires_at
                      ? `至 ${new Date(account.lease_expires_at).toLocaleString('zh-CN')}`
                      : '无'}
                  </dd>
                  <dt>最近通过</dt>
                  <dd>{account.last_passed_at ?? '尚未 live 验证'}</dd>
                </dl>
                <div className="actions">
                  <button
                    onClick={() =>
                      void executionApi.healthCheck(session, account.pub_id).then(refresh)
                    }
                  >
                    L0–L3 健康检查
                  </button>
                  <button
                    onClick={() =>
                      void riskyAction('执行 live canary', () =>
                        executionApi.liveCanary(session, account.pub_id),
                      )
                    }
                  >
                    Live canary
                  </button>
                  <button
                    className="danger"
                    onClick={() =>
                      void riskyAction('隔离账号和所有活动租约', () =>
                        executionApi.quarantine(session, account.pub_id, 'operations-confirmed'),
                      )
                    }
                  >
                    隔离
                  </button>
                  <button
                    className="danger"
                    onClick={() =>
                      void riskyAction('申请 Break-glass 双人审批', () =>
                        executionApi.requestBreakGlass(session, account.pub_id),
                      )
                    }
                  >
                    Break-glass
                  </button>
                  <button
                    className="danger"
                    onClick={() =>
                      void riskyAction('撤销账号与密码学删除', () =>
                        executionApi.revoke(session, account.pub_id, 'operations-confirmed'),
                      )
                    }
                  >
                    撤销
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="execution-card">
        <div className="section-title">
          <h2>Break-glass 双人审批</h2>
          <span>短时能力 · 禁止自批 · 完整审计</span>
        </div>
        {breakGlassRequests.length === 0 ? (
          <p className="empty">当前没有 Break-glass 请求。</p>
        ) : (
          <div className="break-glass-list">
            {breakGlassRequests.map((item) => (
              <article key={item.pub_id}>
                <code>{item.pub_id}</code>
                <span>{item.reason}</span>
                <Status value={item.state} />
                <span>{item.approvals}/2 审批</span>
                {item.state === 'pending' &&
                  item.requested_by !== session.actorId &&
                  (session.role === 'reviewer' || session.role === 'admin') && (
                    <button
                      className="danger"
                      onClick={() =>
                        void riskyAction('批准 Break-glass 短时能力', () =>
                          executionApi.approveBreakGlass(session, item.pub_id),
                        )
                      }
                    >
                      审批
                    </button>
                  )}
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="execution-card">
        <div className="section-title">
          <h2>人工接管队列</h2>
          <span>一次性安全配对 · 原生平台确认 · 可恢复等待</span>
        </div>
        {interventions.length === 0 ? (
          <p className="empty">当前没有人工待办。</p>
        ) : (
          <div className="intervention-list">
            {interventions.map((item) => (
              <article key={item.pub_id}>
                <span className={`challenge ${item.challenge_type}`}>
                  {CHALLENGES[item.challenge_type] ?? item.challenge_type}
                </span>
                <div>
                  <strong>{item.account_mask}</strong>
                  <p>
                    {item.action} · {item.allowed_domain}
                  </p>
                  <p>
                    责任人：{item.assigned_to_pub_id ?? '未分配'} · 截止：
                    {item.due_at ? new Date(item.due_at).toLocaleString('zh-CN') : '未设置'}
                  </p>
                  {item.resolution_note && <p>处理说明：{item.resolution_note}</p>}
                </div>
                <div className="actions">
                  <Status value={item.state} />
                  {!pairings[item.pub_id] && item.state !== 'completed' && (
                    <button onClick={() => void pair(item)}>安全配对</button>
                  )}
                  {pairings[item.pub_id]?.customerDevice && pairings[item.pub_id]?.bundle && (
                    <>
                      <label className="sr-only" htmlFor={`bundle-${item.pub_id}`}>
                        客户终端一次性配对包
                      </label>
                      <textarea
                        id={`bundle-${item.pub_id}`}
                        readOnly
                        value={pairings[item.pub_id]?.bundle ?? ''}
                        aria-label="客户终端一次性配对包"
                      />
                      <button
                        onClick={() =>
                          void navigator.clipboard.writeText(pairings[item.pub_id]?.bundle ?? '')
                        }
                      >
                        复制到受控终端
                      </button>
                    </>
                  )}
                  {pairings[item.pub_id] && !pairings[item.pub_id]?.customerDevice && (
                    <button onClick={() => void complete(item)}>平台已确认，恢复</button>
                  )}
                  {!['completed', 'cancelled'].includes(item.state) && (
                    <button
                      className="danger"
                      onClick={() =>
                        void riskyAction('关闭无法恢复的人工待办', () =>
                          executionApi.cancelIntervention(
                            session,
                            item.pub_id,
                            '运营确认本次挑战无法恢复；终止等待并保留审计记录。',
                          ),
                        )
                      }
                    >
                      终止待办
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
      <section className="execution-card">
        <div className="section-title">
          <h2>工作流与会话时间线</h2>
          <span>健康 · 挑战 · 隔离 · 撤销 · 审计回执</span>
        </div>
        {events.length === 0 ? (
          <p className="empty">当前没有会话事件。</p>
        ) : (
          <ol className="timeline">
            {events.map((event) => (
              <li key={event.pub_id}>
                <time>{new Date(event.occurred_at).toLocaleString('zh-CN')}</time>
                <strong>{event.event_type}</strong>
                <code>{event.pub_id}</code>
              </li>
            ))}
          </ol>
        )}
      </section>
      <footer className="security-note">
        不会显示 Cookie、token、OTP、代理密码、设备指纹、内部提示词或 profile 路径。Break-glass
        解密不在普通页面提供。
      </footer>
    </main>
  );
}

function Status({ value }: { value: string }) {
  const tone = /active|completed|verified|adapter_ready/.test(value)
    ? 'ok'
    : /failed|revoked|quarantined|cancel/.test(value)
      ? 'bad'
      : 'warn';
  return <span className={`status ${tone}`}>{value}</span>;
}
