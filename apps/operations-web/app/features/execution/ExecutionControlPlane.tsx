import { CursorPagination } from '@geo/design-system';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { PlatformBadge } from '../../platforms';
import { PAGE_SIZE, useCursorCollection, type CursorPage } from '../../pagination';
import {
  executionApi,
  type Account,
  type BreakGlassRequest,
  type Intervention,
  type Pairing,
  type PlatformSla,
  type Project,
  type Run,
  type RunSummary,
  type Schedule,
  type SessionContext,
  type SessionEvent,
} from './api';
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

type ProjectPlanSummary = {
  configVersionPubId: string | null;
  configRevision: number | null;
  nextPendingRevision: number | null;
  runCount: number;
};

type CursorPanelPage<T> = {
  data: T[];
  state: 'loading' | 'ready' | 'failed';
  pageNumber: number;
  hasPrevious: boolean;
  hasNext: boolean;
  previous: () => void;
  next: () => void;
  refresh: (background?: boolean) => Promise<void>;
};

export function ExecutionControlPlane({ session }: Props) {
  const [platformSla, setPlatformSla] = useState<PlatformSla[]>([]);
  const [runSummary, setRunSummary] = useState<RunSummary | null>(null);
  const [projectPlans, setProjectPlans] = useState<Record<string, ProjectPlanSummary>>({});
  const [auxState, setAuxState] = useState<'loading' | 'ready' | 'offline'>('loading');
  const [receipt, setReceipt] = useState<string | null>(null);
  const [pairings, setPairings] = useState<Record<string, PairingState>>({});

  const loadAccounts = useCallback(
    (cursor?: string) =>
      executionApi.accounts(session, { ...(cursor ? { cursor } : {}), limit: PAGE_SIZE }),
    [session],
  );
  const loadProjects = useCallback(
    async (cursor?: string): Promise<CursorPage<Project>> => {
      const page = await executionApi.projects(session, {
        ...(cursor ? { cursor } : {}),
        limit: PAGE_SIZE,
      });
      return {
        data: page.data,
        nextCursor: page.page.next_cursor ?? null,
        hasMore: page.page.has_more,
      };
    },
    [session],
  );
  const loadRuns = useCallback(
    (cursor?: string) =>
      executionApi.runs(session, { ...(cursor ? { cursor } : {}), limit: PAGE_SIZE }),
    [session],
  );
  const loadSchedules = useCallback(
    (cursor?: string) =>
      executionApi.schedules(session, { ...(cursor ? { cursor } : {}), limit: PAGE_SIZE }),
    [session],
  );
  const loadInterventions = useCallback(
    (cursor?: string) =>
      executionApi.interventions(session, { ...(cursor ? { cursor } : {}), limit: PAGE_SIZE }),
    [session],
  );
  const loadBreakGlass = useCallback(
    (cursor?: string) =>
      executionApi.breakGlassRequests(session, {
        ...(cursor ? { cursor } : {}),
        limit: PAGE_SIZE,
      }),
    [session],
  );
  const loadEvents = useCallback(
    (cursor?: string) =>
      executionApi.events(session, { ...(cursor ? { cursor } : {}), limit: PAGE_SIZE }),
    [session],
  );

  const accountsPage = useCursorCollection(loadAccounts, session.tenantId);
  const projectsPage = useCursorCollection(loadProjects, session.tenantId);
  const runsPage = useCursorCollection(loadRuns, session.tenantId);
  const schedulesPage = useCursorCollection(loadSchedules, session.tenantId);
  const interventionsPage = useCursorCollection(loadInterventions, session.tenantId);
  const breakGlassPage = useCursorCollection(loadBreakGlass, session.tenantId);
  const eventsPage = useCursorCollection(loadEvents, session.tenantId);

  const refreshAux = useCallback(async () => {
    try {
      const [nextPlatformSla, nextSummary] = await Promise.all([
        executionApi.platformSla(session),
        executionApi.runSummary(session),
      ]);
      setPlatformSla(nextPlatformSla);
      setRunSummary(nextSummary);
      setAuxState('ready');
    } catch {
      setAuxState('offline');
    }
  }, [session]);

  useEffect(() => {
    void refreshAux();
  }, [refreshAux]);

  const loadProjectPlans = useCallback(async () => {
    const entries = await Promise.all(
      projectsPage.data.map(async (project) => {
        const [configResult, summaryResult] = await Promise.allSettled([
          executionApi.currentConfig(session, project.pub_id),
          executionApi.runSummary(session, project.pub_id),
        ]);
        const config = configResult.status === 'fulfilled' ? configResult.value : null;
        const summary = summaryResult.status === 'fulfilled' ? summaryResult.value : null;
        return [
          project.pub_id,
          {
            configVersionPubId: config?.effective?.pub_id ?? null,
            configRevision: config?.effective?.revision ?? null,
            nextPendingRevision: config?.next_pending?.revision ?? null,
            runCount: summary?.run_count ?? 0,
          },
        ] as const;
      }),
    );
    setProjectPlans(Object.fromEntries(entries));
  }, [projectsPage.data, session]);

  useEffect(() => {
    void loadProjectPlans();
  }, [loadProjectPlans]);

  const refresh = useCallback(async () => {
    await Promise.allSettled([
      accountsPage.refresh(true),
      projectsPage.refresh(true),
      runsPage.refresh(true),
      schedulesPage.refresh(true),
      interventionsPage.refresh(true),
      breakGlassPage.refresh(true),
      eventsPage.refresh(true),
      refreshAux(),
      loadProjectPlans(),
    ]);
  }, [
    accountsPage.refresh,
    breakGlassPage.refresh,
    eventsPage.refresh,
    interventionsPage.refresh,
    loadProjectPlans,
    projectsPage.refresh,
    refreshAux,
    runsPage.refresh,
    schedulesPage.refresh,
  ]);

  useEffect(() => {
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const progress = useMemo(
    () => ({
      total: runSummary?.total_tasks ?? 0,
      completed: runSummary?.completed_tasks ?? 0,
    }),
    [runSummary],
  );

  async function riskyAction(label: string, action: () => Promise<unknown>) {
    if (!window.confirm(`${label}属于高风险操作。确认权限、影响范围与审计责任后继续？`)) return;
    const result = (await action()) as { workflow_id?: string };
    setReceipt(`审计回执：${result.workflow_id ?? new Date().toISOString()}`);
    await refresh();
  }

  async function pair(item: Intervention) {
    const pairing = await executionApi.pairIntervention(session, item.pub_id);
    const customerDevice =
      item.account_custody_mode === 'customer_device' ||
      accountsPage.data.find((account) => account.pub_id === item.account_pub_id)?.custody_mode ===
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

  const pageStates = [
    accountsPage.state,
    projectsPage.state,
    runsPage.state,
    schedulesPage.state,
    interventionsPage.state,
    breakGlassPage.state,
    eventsPage.state,
  ];
  if (auxState === 'loading' && pageStates.every((state) => state === 'loading')) {
    return <div className="execution-state">正在连接执行控制面…</div>;
  }
  if (auxState === 'offline' && pageStates.every((state) => state === 'failed'))
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
          <strong>{runSummary?.active_run_count ?? '—'}</strong>
        </article>
        <article>
          <span>待人工</span>
          <strong>{interventionsPage.meta?.counts?.['X-Open-Count'] ?? '—'}</strong>
        </article>
        <article>
          <span>账号健康</span>
          <strong>
            {accountsPage.meta?.counts?.['X-Active-Count'] ?? '—'}/
            {accountsPage.meta?.totalCount ?? '—'}
          </strong>
        </article>
      </section>

      <section className="execution-card">
        <div className="section-title">
          <h2>项目与冻结计划</h2>
          <span>项目状态 · 生效配置 · 最近更新</span>
        </div>
        {projectsPage.state === 'loading' ? (
          <p className="empty">正在加载项目计划…</p>
        ) : projectsPage.state === 'failed' ? (
          <p className="empty">
            项目计划加载失败。<button onClick={() => void projectsPage.refresh()}>重试</button>
          </p>
        ) : projectsPage.data.length === 0 ? (
          <p className="empty">尚无项目或冻结执行计划。</p>
        ) : (
          <>
            <div className="project-list">
              {projectsPage.data.map((project) => {
                const plan = projectPlans[project.pub_id];
                return (
                  <article key={project.pub_id}>
                    <div>
                      <strong>{project.name}</strong>
                      <code>{project.pub_id}</code>
                    </div>
                    <Status value={project.state} />
                    <span>{plan ? `${plan.runCount} 个执行计划` : '正在汇总执行计划'}</span>
                    <span>
                      {plan?.configVersionPubId
                        ? `生效配置 v${plan.configRevision} · ${plan.configVersionPubId}`
                        : '尚无生效冻结配置'}
                      {plan?.nextPendingRevision ? ` · 待生效 v${plan.nextPendingRevision}` : ''}
                    </span>
                  </article>
                );
              })}
            </div>
            <CursorPagination
              page={projectsPage.pageNumber}
              hasPrevious={projectsPage.hasPrevious}
              hasNext={projectsPage.hasNext}
              onPrevious={projectsPage.previous}
              onNext={projectsPage.next}
              label="项目与冻结计划分页"
            />
          </>
        )}
      </section>

      <section className="execution-card">
        <div className="section-title">
          <h2>运行与任务矩阵</h2>
          <span>实时进度 · 失败 · 延迟 · 数据新鲜度</span>
        </div>
        {runsPage.state === 'loading' ? (
          <p className="empty">正在加载运行…</p>
        ) : runsPage.state === 'failed' ? (
          <p className="empty">
            运行加载失败。<button onClick={() => void runsPage.refresh()}>重试</button>
          </p>
        ) : runsPage.data.length === 0 ? (
          <p className="empty">暂无运行。在各服务工作区冻结配置并启动采集后会出现在这里。</p>
        ) : (
          <>
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
                  {runsPage.data.map((run) => (
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
            <CursorPagination
              page={runsPage.pageNumber}
              hasPrevious={runsPage.hasPrevious}
              hasNext={runsPage.hasNext}
              onPrevious={runsPage.previous}
              onNext={runsPage.next}
              label="运行与任务矩阵分页"
            />
          </>
        )}
      </section>

      <SchedulePanel
        session={session}
        page={schedulesPage}
        onChanged={refresh}
        onReceipt={setReceipt}
      />

      <PlatformSlaPanel items={platformSla} />

      <section className="execution-card" id="platform-accounts">
        <div className="section-title">
          <h2>平台账号目录与 Profile 健康</h2>
          <span>真实准入等级 · 授权 · 托管模式 · 租约</span>
        </div>
        {accountsPage.state === 'loading' ? (
          <p className="empty">正在加载平台账号…</p>
        ) : accountsPage.state === 'failed' ? (
          <p className="empty">
            平台账号加载失败。<button onClick={() => void accountsPage.refresh()}>重试</button>
          </p>
        ) : accountsPage.data.length === 0 ? (
          <p className="empty">尚未登记平台账号。</p>
        ) : (
          <>
            <div className="account-grid">
              {accountsPage.data.map((account) => (
                <article key={account.pub_id} className="account-card">
                  <div>
                    <span className="platform">
                      <PlatformBadge platform={account.platform} />
                    </span>
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
            <CursorPagination
              page={accountsPage.pageNumber}
              hasPrevious={accountsPage.hasPrevious}
              hasNext={accountsPage.hasNext}
              onPrevious={accountsPage.previous}
              onNext={accountsPage.next}
              label="平台账号目录分页"
            />
          </>
        )}
      </section>

      <section className="execution-card">
        <div className="section-title">
          <h2>Break-glass 双人审批</h2>
          <span>短时能力 · 禁止自批 · 完整审计</span>
        </div>
        {breakGlassPage.state === 'loading' ? (
          <p className="empty">正在加载审批请求…</p>
        ) : breakGlassPage.state === 'failed' ? (
          <p className="empty">
            审批请求加载失败。<button onClick={() => void breakGlassPage.refresh()}>重试</button>
          </p>
        ) : breakGlassPage.data.length === 0 ? (
          <p className="empty">当前没有 Break-glass 请求。</p>
        ) : (
          <>
            <div className="break-glass-list">
              {breakGlassPage.data.map((item) => (
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
            <CursorPagination
              page={breakGlassPage.pageNumber}
              hasPrevious={breakGlassPage.hasPrevious}
              hasNext={breakGlassPage.hasNext}
              onPrevious={breakGlassPage.previous}
              onNext={breakGlassPage.next}
              label="Break-glass 审批分页"
            />
          </>
        )}
      </section>

      <section className="execution-card" id="interventions">
        <div className="section-title">
          <h2>人工接管队列</h2>
          <span>一次性安全配对 · 原生平台确认 · 可恢复等待</span>
        </div>
        {interventionsPage.state === 'loading' ? (
          <p className="empty">正在加载人工待办…</p>
        ) : interventionsPage.state === 'failed' ? (
          <p className="empty">
            人工待办加载失败。<button onClick={() => void interventionsPage.refresh()}>重试</button>
          </p>
        ) : interventionsPage.data.length === 0 ? (
          <p className="empty">当前没有人工待办。</p>
        ) : (
          <>
            <div className="intervention-list">
              {interventionsPage.data.map((item) => (
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
            <CursorPagination
              page={interventionsPage.pageNumber}
              hasPrevious={interventionsPage.hasPrevious}
              hasNext={interventionsPage.hasNext}
              onPrevious={interventionsPage.previous}
              onNext={interventionsPage.next}
              label="人工接管队列分页"
            />
          </>
        )}
      </section>
      <section className="execution-card" id="events">
        <div className="section-title">
          <h2>工作流与会话时间线</h2>
          <span>健康 · 挑战 · 隔离 · 撤销 · 审计回执</span>
        </div>
        {eventsPage.state === 'loading' ? (
          <p className="empty">正在加载会话事件…</p>
        ) : eventsPage.state === 'failed' ? (
          <p className="empty">
            会话事件加载失败。<button onClick={() => void eventsPage.refresh()}>重试</button>
          </p>
        ) : eventsPage.data.length === 0 ? (
          <p className="empty">当前没有会话事件。</p>
        ) : (
          <>
            <ol className="timeline">
              {eventsPage.data.map((event) => (
                <li key={event.pub_id}>
                  <time>{new Date(event.occurred_at).toLocaleString('zh-CN')}</time>
                  <strong>{event.event_type}</strong>
                  <code>{event.pub_id}</code>
                </li>
              ))}
            </ol>
            <CursorPagination
              page={eventsPage.pageNumber}
              hasPrevious={eventsPage.hasPrevious}
              hasNext={eventsPage.hasNext}
              onPrevious={eventsPage.previous}
              onNext={eventsPage.next}
              label="工作流与会话时间线分页"
            />
          </>
        )}
      </section>
      <footer className="security-note">
        不会显示 Cookie、token、OTP、代理密码、设备指纹、内部提示词或 profile 路径。Break-glass
        解密不在普通页面提供。
      </footer>
    </main>
  );
}

function SchedulePanel({
  session,
  page,
  onChanged,
  onReceipt,
}: {
  session: SessionContext;
  page: CursorPanelPage<Schedule>;
  onChanged: () => Promise<void>;
  onReceipt: (message: string) => void;
}) {
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
      {page.state === 'loading' ? (
        <p className="empty">正在加载周期任务…</p>
      ) : page.state === 'failed' ? (
        <p className="empty">
          周期任务加载失败。<button onClick={() => void page.refresh()}>重试</button>
        </p>
      ) : page.data.length === 0 ? (
        <p className="empty">暂无周期任务。</p>
      ) : (
        <>
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
                {page.data.map((item) => (
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
          <CursorPagination
            page={page.pageNumber}
            hasPrevious={page.hasPrevious}
            hasNext={page.hasNext}
            onPrevious={page.previous}
            onNext={page.next}
            label="周期监测分页"
          />
        </>
      )}
    </section>
  );
}

function PlatformSlaPanel({ items }: { items: PlatformSla[] }) {
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
                <td>
                  <PlatformBadge platform={item.platform} />
                </td>
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
  const tone = /active|completed|verified|adapter_ready|healthy/.test(value)
    ? 'ok'
    : /failed|revoked|quarantined|cancel|breached|archived/.test(value)
      ? 'bad'
      : 'warn';
  return <span className={`status ${tone}`}>{value}</span>;
}
