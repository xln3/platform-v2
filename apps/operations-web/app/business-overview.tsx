import { type FormEvent, useEffect, useState } from 'react';
import {
  businessOverviewAttentionCodes,
  businessOverviewProjectStates,
  getOperationsBusinessOverview,
  type BusinessOverviewAttentionCode,
  type BusinessOverviewProjectState,
  type OperationsBusinessOverview,
  type OperationsBusinessOverviewItem,
  type OperationsBusinessOverviewQuery,
} from '@geo/api-client/business-overview';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { Badge, MetricGrid, StatePanel, updateClientUrlParameters } from '@geo/design-system';
import './business-overview.css';

export type BusinessOverviewLoader = typeof getOperationsBusinessOverview;

type BusinessFilters = {
  q?: string;
  projectState?: BusinessOverviewProjectState;
  attention?: BusinessOverviewAttentionCode;
};

const rootSections = ['overview', 'sessions', 'interventions', 'events'] as const;
const projectStateLabels: Record<BusinessOverviewProjectState, string> = {
  draft: '准备中',
  active: '进行中',
  paused: '已暂停',
  archived: '已归档',
};
const entitlementStateLabels = {
  inactive: '未生效',
  active: '已激活',
  suspended: '已暂停',
  expired: '已到期',
} as const;
const collectionStateLabels: Record<
  NonNullable<OperationsBusinessOverviewItem['collection']['latestState']>,
  string
> = {
  pending: '等待执行',
  starting: '正在启动',
  running: '正在运行',
  pausing: '正在暂停',
  paused: '已暂停',
  resuming: '正在恢复',
  cancelling: '正在取消',
  completed: '已完成',
  completed_with_failures: '部分失败',
  failed: '执行失败',
  cancelled: '已取消',
  skipped: '已跳过',
};
const formalStateLabels: Record<
  NonNullable<OperationsBusinessOverviewItem['formalReport']['latestState']>,
  string
> = {
  queued: '等待生产',
  running: '正在生产',
  failed: '生产失败',
  awaiting_review: '等待复核',
  signed: '报告已签发',
};
const attentionLabels: Record<BusinessOverviewAttentionCode, string> = {
  collection_failed_or_delayed: '采集失败或延迟',
  formal_production_failed: '正式报告生产失败',
  formal_review_required: '正式报告等待复核',
  delivery_confirmation_required: '报告交付等待确认',
  setup_records_missing: '首版建档资料缺失',
  intake_truth_confirmation_required: '客户事实等待确认',
  service_entitlement_unrecorded: '尚无服务权益记录',
  no_current_attention: '当前无明确关注项',
};

const safeProjectId = (value: string): boolean => /^prj_[A-Za-z0-9_-]{1,116}$/.test(value);

const formatDateTime = (value: string | null): string => {
  if (!value) return '—';
  return `${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))} CST`;
};

const readFilters = (): BusinessFilters => {
  if (typeof window === 'undefined') return {};
  const search = new URL(window.location.href).searchParams;
  const q = search.get('q')?.trim();
  const rawState = search.get('project_state');
  const rawAttention = search.get('attention');
  const projectState = businessOverviewProjectStates.find((value) => value === rawState);
  const attention = businessOverviewAttentionCodes.find((value) => value === rawAttention);
  return {
    ...(q && q.length <= 120 ? { q } : {}),
    ...(projectState ? { projectState } : {}),
    ...(attention ? { attention } : {}),
  };
};

const writeFilters = (filters: BusinessFilters): void => {
  if (typeof window === 'undefined') return;
  updateClientUrlParameters(
    {
      q: filters.q ?? null,
      project_state: filters.projectState ?? null,
      attention: filters.attention ?? null,
    },
    rootSections,
    false,
  );
};

const withProjectState = (
  filters: BusinessFilters,
  projectState: BusinessOverviewProjectState | undefined,
): BusinessFilters => {
  const { projectState: _previous, ...rest } = filters;
  return projectState ? { ...rest, projectState } : rest;
};

const withAttention = (
  filters: BusinessFilters,
  attention: BusinessOverviewAttentionCode | undefined,
): BusinessFilters => {
  const { attention: _previous, ...rest } = filters;
  return attention ? { ...rest, attention } : rest;
};

export function BusinessOverviewContainer({
  fixtureMode,
  roles,
  loadBusiness = getOperationsBusinessOverview,
}: {
  fixtureMode: boolean;
  roles: readonly string[];
  loadBusiness?: BusinessOverviewLoader;
}) {
  const [filters, setFilters] = useState<BusinessFilters>(readFilters);
  const [searchDraft, setSearchDraft] = useState(filters.q ?? '');
  const [cursor, setCursor] = useState<string | undefined>();
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<'loading' | 'ready' | 'failed' | 'forbidden'>(
    fixtureMode ? 'ready' : 'loading',
  );
  const [overview, setOverview] = useState<OperationsBusinessOverview | null>(() =>
    fixtureMode ? createFixtureOperationsBusinessOverview({}, undefined) : null,
  );

  useEffect(() => {
    const restore = () => {
      const restored = readFilters();
      setFilters(restored);
      setSearchDraft(restored.q ?? '');
      setCursor(undefined);
      setCursorStack([]);
    };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, []);

  useEffect(() => {
    const query: OperationsBusinessOverviewQuery = {
      limit: 4,
      ...filters,
      ...(cursor ? { cursor } : {}),
    };
    if (fixtureMode) {
      setOverview(createFixtureOperationsBusinessOverview(filters, cursor));
      setState('ready');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    const tenantId = headers?.['X-Tenant-Id'];
    const actorId = headers?.['X-Actor-Id'];
    const actorRole = headers?.['X-Actor-Role'];
    if (!tenantId || !actorId || !actorRole) {
      setOverview(null);
      setState('failed');
      return;
    }
    const controller = new AbortController();
    let current = true;
    setState('loading');
    void loadBusiness(
      {
        'X-Tenant-Id': tenantId,
        'X-Actor-Id': actorId,
        'X-Actor-Role': actorRole,
      },
      query,
      { signal: controller.signal },
    ).then((result) => {
      if (!current) return;
      if (result.kind === 'ready') {
        setOverview(result.data);
        setState('ready');
      } else {
        setOverview(null);
        setState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [attempt, cursor, filters, fixtureMode, loadBusiness]);

  const applyFilters = (next: BusinessFilters) => {
    const normalized = {
      ...(next.q?.trim() ? { q: next.q.trim().slice(0, 120) } : {}),
      ...(next.projectState ? { projectState: next.projectState } : {}),
      ...(next.attention ? { attention: next.attention } : {}),
    } satisfies BusinessFilters;
    writeFilters(normalized);
    setFilters(normalized);
    setSearchDraft(normalized.q ?? '');
    setCursor(undefined);
    setCursorStack([]);
  };

  if (!overview) {
    return (
      <StatePanel
        state={state === 'ready' ? 'empty' : state}
        {...(state === 'failed' ? { onRetry: () => setAttempt((value) => value + 1) } : {})}
      />
    );
  }
  return (
    <BusinessOverviewWorkspace
      overview={overview}
      filters={filters}
      searchDraft={searchDraft}
      setSearchDraft={setSearchDraft}
      applyFilters={applyFilters}
      roles={roles}
      pageIndex={cursorStack.length}
      canGoPrevious={cursorStack.length > 0}
      onPrevious={() => {
        const stack = [...cursorStack];
        const previous = stack.pop();
        setCursorStack(stack);
        setCursor(previous);
      }}
      onNext={() => {
        if (!overview.page.nextCursor) return;
        setCursorStack((stack) => [...stack, cursor]);
        setCursor(overview.page.nextCursor ?? undefined);
      }}
    />
  );
}

export function BusinessOverviewWorkspace({
  overview,
  filters,
  searchDraft,
  setSearchDraft,
  applyFilters,
  roles,
  pageIndex,
  canGoPrevious,
  onPrevious,
  onNext,
}: {
  overview: OperationsBusinessOverview;
  filters: BusinessFilters;
  searchDraft: string;
  setSearchDraft: (value: string) => void;
  applyFilters: (filters: BusinessFilters) => void;
  roles: readonly string[];
  pageIndex: number;
  canGoPrevious: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  const canCreate = roles.some((role) => role === 'operator' || role === 'admin');
  const start = overview.page.filteredTotal === 0 ? 0 : pageIndex * overview.page.limit + 1;
  const end = start === 0 ? 0 : start + overview.items.length - 1;
  const stateDetail = businessOverviewProjectStates
    .map((state) => `${projectStateLabels[state]} ${overview.summary.projectStateCounts[state]}`)
    .join(' · ');
  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    applyFilters({ ...filters, q: searchDraft });
  };
  return (
    <div className="business-overview">
      <section className="business-overview-heading" aria-labelledby="business-portfolio-title">
        <div>
          <span className="overline">Tenant portfolio facts</span>
          <h2 id="business-portfolio-title">项目组合</h2>
          <p>汇总当前租户内可追溯的建档、服务权益、采集执行、正式报告与交付事实。</p>
          <p className="business-as-of">
            数据截至 <time dateTime={overview.asOf}>{formatDateTime(overview.asOf)}</time>
          </p>
        </div>
        {canCreate ? (
          <div className="business-heading-actions" aria-label="项目商务快捷操作">
            <a className="button" href="/platform/operations/onboarding">
              新建客户 / 开户
            </a>
            <a className="button button-secondary" href="/platform/operations/quotations">
              生成报价
            </a>
          </div>
        ) : null}
      </section>

      <form className="business-filters" onSubmit={submitSearch} aria-label="筛选项目组合">
        <label>
          <span>客户或项目</span>
          <input
            type="search"
            value={searchDraft}
            maxLength={120}
            placeholder="输入客户名或项目名"
            onChange={(event) => setSearchDraft(event.currentTarget.value)}
          />
        </label>
        <label>
          <span>项目状态</span>
          <select
            value={filters.projectState ?? ''}
            onChange={(event) =>
              applyFilters(
                withProjectState(
                  filters,
                  event.currentTarget.value
                    ? (event.currentTarget.value as BusinessOverviewProjectState)
                    : undefined,
                ),
              )
            }
          >
            <option value="">全部状态</option>
            {businessOverviewProjectStates.map((state) => (
              <option key={state} value={state}>
                {projectStateLabels[state]}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>关注项</span>
          <select
            value={filters.attention ?? ''}
            onChange={(event) =>
              applyFilters(
                withAttention(
                  filters,
                  event.currentTarget.value
                    ? (event.currentTarget.value as BusinessOverviewAttentionCode)
                    : undefined,
                ),
              )
            }
          >
            <option value="">全部关注项</option>
            {businessOverviewAttentionCodes.map((code) => (
              <option key={code} value={code}>
                {attentionLabels[code]}
              </option>
            ))}
          </select>
        </label>
        <div className="business-filter-actions">
          <button className="button" type="submit">
            搜索
          </button>
          <button
            className="button button-secondary"
            type="button"
            onClick={() => applyFilters({})}
          >
            清除筛选
          </button>
        </div>
      </form>

      <MetricGrid
        metrics={[
          {
            label: '项目总数',
            value: String(overview.summary.projectCount),
            detail:
              overview.summary.projectCount === overview.summary.tenantProjectCount
                ? stateDetail
                : `当前筛选；租户共 ${overview.summary.tenantProjectCount} 个项目`,
          },
          {
            label: '首版建档齐备',
            value: String(overview.summary.setupReadyProjectCount),
            detail: '客户档案、资产确认、冻结配置三项齐备',
          },
          {
            label: '服务权益记录',
            value: String(overview.summary.projectWithEntitlementRecordCount),
            detail: `当前有效权益 ${overview.summary.activeEntitlementCount} 条`,
          },
          {
            label: '当前关注',
            value: String(overview.summary.attentionProjectCount),
            detail: '仅统计可由现有事实复算的关注项',
          },
        ]}
      />

      <section
        className="panel business-project-panel"
        aria-labelledby="business-project-list-title"
      >
        <div className="business-section-title">
          <div>
            <h2 id="business-project-list-title">项目进展</h2>
            <p className="panel-subtitle">每页最多 4 个项目；汇总数字不按当前页截断。</p>
          </div>
          <span className="business-range" aria-live="polite">
            {start}–{end} / {overview.page.filteredTotal}
          </span>
        </div>
        {overview.items.length === 0 ? (
          <BusinessEmptyState
            tenantProjectCount={overview.summary.tenantProjectCount}
            canCreate={canCreate}
          />
        ) : (
          <>
            <div
              className="table-scroll business-project-table"
              role="region"
              aria-label="可横向滚动的项目商务事实表"
              tabIndex={0}
            >
              <table className="data-table">
                <caption className="sr-only">租户项目建档、服务、执行、报告和交付事实</caption>
                <thead>
                  <tr>
                    <th scope="col">客户 / 项目</th>
                    <th scope="col">建档与确认</th>
                    <th scope="col">服务记录</th>
                    <th scope="col">当前执行</th>
                    <th scope="col">报告与交付</th>
                    <th scope="col">首要关注</th>
                    <th scope="col">最近事实更新时间</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.items.map((item) => (
                    <tr key={item.project.id}>
                      <td>
                        <ProjectIdentity item={item} />
                      </td>
                      <td>
                        <SetupFacts item={item} />
                      </td>
                      <td>
                        <EntitlementFacts item={item} />
                      </td>
                      <td>
                        <CollectionFacts item={item} />
                      </td>
                      <td>
                        <ReportFacts item={item} />
                      </td>
                      <td>
                        <AttentionFact item={item} />
                      </td>
                      <td>
                        <time dateTime={item.lastBusinessFactAt}>
                          {formatDateTime(item.lastBusinessFactAt)}
                        </time>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="business-project-cards" aria-label="项目商务事实卡片">
              {overview.items.map((item) => (
                <article key={item.project.id} className="business-project-card">
                  <ProjectIdentity item={item} />
                  <dl>
                    <div>
                      <dt>建档与确认</dt>
                      <dd>
                        <SetupFacts item={item} />
                      </dd>
                    </div>
                    <div>
                      <dt>服务记录</dt>
                      <dd>
                        <EntitlementFacts item={item} />
                      </dd>
                    </div>
                    <div>
                      <dt>当前执行</dt>
                      <dd>
                        <CollectionFacts item={item} />
                      </dd>
                    </div>
                    <div>
                      <dt>报告与交付</dt>
                      <dd>
                        <ReportFacts item={item} />
                      </dd>
                    </div>
                    <div>
                      <dt>首要关注</dt>
                      <dd>
                        <AttentionFact item={item} />
                      </dd>
                    </div>
                    <div>
                      <dt>最近事实更新时间</dt>
                      <dd>
                        <time dateTime={item.lastBusinessFactAt}>
                          {formatDateTime(item.lastBusinessFactAt)}
                        </time>
                      </dd>
                    </div>
                  </dl>
                </article>
              ))}
            </div>
          </>
        )}
        <nav className="business-pagination" aria-label="项目分页">
          <button
            className="button button-secondary"
            type="button"
            disabled={!canGoPrevious}
            onClick={onPrevious}
          >
            上一页
          </button>
          <span>第 {pageIndex + 1} 页</span>
          <button
            className="button button-secondary"
            type="button"
            disabled={!overview.page.hasMore}
            onClick={onNext}
          >
            下一页
          </button>
        </nav>
      </section>

      <div className="business-bottom-grid">
        <section className="panel" aria-labelledby="business-boundary-title">
          <h2 id="business-boundary-title">业务数据边界</h2>
          <p>系统目前未保存可查询的报价历史、已签合同、开票应收与回款台账。</p>
          <ul className="business-boundary-list">
            <li>缺少权益记录只表示“尚无可查询记录”，不表示客户未购买。</li>
            <li>报告 `signed` 表示报告已签发，不表示商务合同已签。</li>
            <li>报告交付或客户确认不表示已经回款或确认收入。</li>
          </ul>
        </section>
        <section className="panel" aria-labelledby="business-entry-title">
          <h2 id="business-entry-title">继续处理</h2>
          <div className="business-entry-list">
            {canCreate ? <a href="/platform/operations/onboarding">开户 / 客户资料</a> : null}
            {canCreate ? <a href="/platform/operations/quotations">报价单生成</a> : null}
            <a href="/platform/operations/formal-reports">正式报告生成</a>
            <a href="/platform/operations/execution#platform-accounts">运行会话</a>
          </div>
        </section>
      </div>
    </div>
  );
}

function BusinessEmptyState({
  tenantProjectCount,
  canCreate,
}: {
  tenantProjectCount: number;
  canCreate: boolean;
}) {
  if (tenantProjectCount > 0) return <p className="business-empty">没有符合当前筛选条件的项目。</p>;
  return (
    <div className="business-empty">
      <p>当前租户尚无项目。{canCreate ? '可前往开户向导创建客户与首个项目。' : ''}</p>
      {canCreate ? (
        <a className="button" href="/platform/operations/onboarding">
          前往开户向导
        </a>
      ) : null}
    </div>
  );
}

function ProjectIdentity({ item }: { item: OperationsBusinessOverviewItem }) {
  const projectHref = safeProjectId(item.project.id)
    ? `/platform/operations/sop/projects/${encodeURIComponent(item.project.id)}`
    : null;
  const customerWorkspaceHref = safeProjectId(item.project.id)
    ? `/platform/operations/onboarding?project=${encodeURIComponent(item.project.id)}`
    : null;
  return (
    <div className="business-project-identity">
      <span className="business-customer-name">
        {customerWorkspaceHref ? (
          <a className="business-customer-link" href={customerWorkspaceHref}>
            {item.customer.name}
          </a>
        ) : (
          item.customer.name
        )}
      </span>
      {projectHref ? (
        <a href={projectHref}>{item.project.name}</a>
      ) : (
        <strong>{item.project.name}</strong>
      )}
      <Badge tone={item.project.state === 'active' ? 'positive' : 'neutral'}>
        {projectStateLabels[item.project.state]}
      </Badge>
    </div>
  );
}

function FactStatus({ complete, children }: { complete: boolean; children: string }) {
  return (
    <li data-complete={complete}>
      <span aria-hidden="true">{complete ? '✓' : '!'}</span>
      {children}
    </li>
  );
}

function SetupFacts({ item }: { item: OperationsBusinessOverviewItem }) {
  const setup = item.setup;
  return (
    <ul className="business-fact-list">
      <FactStatus complete={setup.clientProfileRevision !== null}>
        {setup.clientProfileRevision ? `客户档案 v${setup.clientProfileRevision}` : '缺客户档案'}
      </FactStatus>
      <FactStatus complete={setup.assetConfirmationRevision !== null}>
        {setup.assetConfirmationRevision
          ? `资产确认 v${setup.assetConfirmationRevision}`
          : '缺资产确认'}
      </FactStatus>
      <FactStatus complete={setup.frozenMonitoringConfigRevision !== null}>
        {setup.frozenMonitoringConfigRevision
          ? `冻结配置 v${setup.frozenMonitoringConfigRevision}`
          : '缺冻结配置'}
      </FactStatus>
      <FactStatus complete={setup.intakeProfileExists && setup.intakeTruthConfirmed === true}>
        {!setup.intakeProfileExists
          ? '缺 intake 档案'
          : setup.intakeTruthConfirmed === true
            ? '事实已确认'
            : '事实尚未确认'}
      </FactStatus>
    </ul>
  );
}

function EntitlementFacts({ item }: { item: OperationsBusinessOverviewItem }) {
  if (item.serviceEntitlements.length === 0) return <span>尚无服务权益记录。</span>;
  return (
    <ul className="business-entitlement-list">
      {item.serviceEntitlements.map((entitlement) => (
        <li key={entitlement.serviceCode}>
          <span>{entitlement.serviceName}</span>
          <Badge
            tone={
              entitlement.effectiveNow
                ? 'positive'
                : entitlement.state === 'suspended' || entitlement.state === 'expired'
                  ? 'warning'
                  : 'neutral'
            }
          >
            {entitlementStateLabels[entitlement.state]}
          </Badge>
          {entitlement.state === 'active' && !entitlement.effectiveNow ? (
            <small>当前不在授权窗口</small>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function CollectionFacts({ item }: { item: OperationsBusinessOverviewItem }) {
  const collection = item.collection;
  if (!collection.latestState || !collection.latestAt) return <span>暂无运行记录。</span>;
  return (
    <div className="business-compact-facts">
      <strong>{collectionStateLabels[collection.latestState]}</strong>
      <span>{formatDateTime(collection.latestAt)}</span>
      <small>
        当前 {collection.activeCount} · 失败记录 {collection.failedCount} · 延迟{' '}
        {collection.delayedCount}
      </small>
    </div>
  );
}

function ReportFacts({ item }: { item: OperationsBusinessOverviewItem }) {
  const report = item.formalReport;
  const delivery = item.delivery;
  return (
    <div className="business-compact-facts">
      {report.latestState && report.latestAt ? (
        <>
          <strong>{formalStateLabels[report.latestState]}</strong>
          <span>{formatDateTime(report.latestAt)}</span>
        </>
      ) : (
        <span>暂无正式报告。</span>
      )}
      {!delivery.deliveredAt ? (
        <small>暂无交付记录。</small>
      ) : delivery.confirmedAt ? (
        <small>交付已确认 · {formatDateTime(delivery.confirmedAt)}</small>
      ) : (
        <small>待确认交付 · {formatDateTime(delivery.deliveredAt)}</small>
      )}
    </div>
  );
}

function AttentionFact({ item }: { item: OperationsBusinessOverviewItem }) {
  const attention = item.primaryAttention;
  const href = attentionHref(attention.code, item.project.id);
  return (
    <div className="business-attention">
      <Badge tone={attention.severity}>{attentionLabels[attention.code]}</Badge>
      {attention.additionalCount > 0 ? <small>另有 {attention.additionalCount} 项</small> : null}
      {href ? <a href={href}>前往处理</a> : null}
    </div>
  );
}

function attentionHref(code: BusinessOverviewAttentionCode, projectId: string): string | null {
  if (!safeProjectId(projectId) || code === 'no_current_attention') return null;
  const encoded = encodeURIComponent(projectId);
  if (code === 'collection_failed_or_delayed')
    return `/platform/operations/execution?project=${encoded}`;
  if (code.startsWith('formal_') || code === 'delivery_confirmation_required') {
    return `/platform/operations/formal-reports?project=${encoded}`;
  }
  return `/platform/operations/onboarding?project=${encoded}`;
}

const allowsBusinessOverviewFixtures =
  import.meta.env.DEV || import.meta.env.VITE_ALLOW_CONTRACT_FIXTURES === 'true';

const fixtureItems: OperationsBusinessOverviewItem[] = allowsBusinessOverviewFixtures
  ? [
      {
        project: { id: 'prj_fixture_business_05', name: '华东品牌增长', state: 'active' },
        customer: { id: 'cst_fixture_business_05', name: '星河科技' },
        setup: {
          clientProfileRevision: 3,
          assetConfirmationRevision: 2,
          frozenMonitoringConfigRevision: 4,
          setupReady: true,
          intakeProfileExists: true,
          intakeTruthConfirmed: true,
        },
        serviceEntitlements: [
          {
            serviceCode: 'ranking_test',
            serviceName: 'AI 推荐排名效果测试',
            state: 'active',
            authorizedFrom: '2026-08-01T00:00:00Z',
            authorizedUntil: '2026-12-31T16:00:00Z',
            effectiveNow: true,
          },
        ],
        collection: {
          activeCount: 0,
          failedCount: 0,
          delayedCount: 0,
          latestState: 'completed',
          latestAt: '2026-08-24T09:20:00Z',
        },
        formalReport: {
          productionCount: 1,
          latestState: 'signed',
          latestAt: '2026-08-24T09:25:00Z',
        },
        delivery: {
          deliveredAt: '2026-08-24T09:26:00Z',
          confirmedAt: '2026-08-24T09:28:00Z',
          pendingConfirmationCount: 0,
        },
        contractDraftExport: null,
        primaryAttention: { code: 'no_current_attention', severity: 'neutral', additionalCount: 0 },
        lastBusinessFactAt: '2026-08-24T09:28:00Z',
      },
      {
        project: { id: 'prj_fixture_business_04', name: '新品首版评测', state: 'draft' },
        customer: { id: 'cst_fixture_business_04', name: '远山制造' },
        setup: {
          clientProfileRevision: 1,
          assetConfirmationRevision: null,
          frozenMonitoringConfigRevision: null,
          setupReady: false,
          intakeProfileExists: false,
          intakeTruthConfirmed: null,
        },
        serviceEntitlements: [],
        collection: {
          activeCount: 0,
          failedCount: 0,
          delayedCount: 0,
          latestState: null,
          latestAt: null,
        },
        formalReport: { productionCount: 0, latestState: null, latestAt: null },
        delivery: { deliveredAt: null, confirmedAt: null, pendingConfirmationCount: 0 },
        contractDraftExport: null,
        primaryAttention: {
          code: 'setup_records_missing',
          severity: 'warning',
          additionalCount: 2,
        },
        lastBusinessFactAt: '2026-08-24T08:40:00Z',
      },
      {
        project: { id: 'prj_fixture_business_03', name: '季度声誉核查', state: 'active' },
        customer: { id: 'cst_fixture_business_03', name: '澄明安全' },
        setup: {
          clientProfileRevision: 2,
          assetConfirmationRevision: 2,
          frozenMonitoringConfigRevision: 2,
          setupReady: true,
          intakeProfileExists: true,
          intakeTruthConfirmed: false,
        },
        serviceEntitlements: [
          {
            serviceCode: 'inbound_disparagement_audit',
            serviceName: '被拉踩内容核查',
            state: 'suspended',
            authorizedFrom: null,
            authorizedUntil: null,
            effectiveNow: false,
          },
        ],
        collection: {
          activeCount: 0,
          failedCount: 0,
          delayedCount: 0,
          latestState: 'completed',
          latestAt: '2026-08-23T10:00:00Z',
        },
        formalReport: {
          productionCount: 1,
          latestState: 'awaiting_review',
          latestAt: '2026-08-24T08:10:00Z',
        },
        delivery: { deliveredAt: null, confirmedAt: null, pendingConfirmationCount: 0 },
        contractDraftExport: null,
        primaryAttention: {
          code: 'formal_review_required',
          severity: 'warning',
          additionalCount: 1,
        },
        lastBusinessFactAt: '2026-08-24T08:10:00Z',
      },
      {
        project: { id: 'prj_fixture_business_02', name: '采集恢复专项', state: 'paused' },
        customer: { id: 'cst_fixture_business_02', name: '北辰服务' },
        setup: {
          clientProfileRevision: 1,
          assetConfirmationRevision: 1,
          frozenMonitoringConfigRevision: 1,
          setupReady: true,
          intakeProfileExists: false,
          intakeTruthConfirmed: null,
        },
        serviceEntitlements: [
          {
            serviceCode: 'official_site_audit',
            serviceName: '官网内容 AI 引用效率分析',
            state: 'active',
            authorizedFrom: '2026-01-01T00:00:00Z',
            authorizedUntil: '2026-08-01T00:00:00Z',
            effectiveNow: false,
          },
        ],
        collection: {
          activeCount: 0,
          failedCount: 2,
          delayedCount: 0,
          latestState: 'failed',
          latestAt: '2026-08-24T07:30:00Z',
        },
        formalReport: { productionCount: 0, latestState: null, latestAt: null },
        delivery: { deliveredAt: null, confirmedAt: null, pendingConfirmationCount: 0 },
        contractDraftExport: null,
        primaryAttention: {
          code: 'collection_failed_or_delayed',
          severity: 'danger',
          additionalCount: 1,
        },
        lastBusinessFactAt: '2026-08-24T07:30:00Z',
      },
      {
        project: { id: 'prj_fixture_business_01', name: '历史项目归档', state: 'archived' },
        customer: { id: 'cst_fixture_business_01', name: '青禾零售' },
        setup: {
          clientProfileRevision: 1,
          assetConfirmationRevision: 1,
          frozenMonitoringConfigRevision: 1,
          setupReady: true,
          intakeProfileExists: true,
          intakeTruthConfirmed: true,
        },
        serviceEntitlements: [
          {
            serviceCode: 'content_publishing_pilot',
            serviceName: '内容发布与排名提升试点',
            state: 'expired',
            authorizedFrom: '2026-01-01T00:00:00Z',
            authorizedUntil: '2026-06-01T00:00:00Z',
            effectiveNow: false,
          },
        ],
        collection: {
          activeCount: 0,
          failedCount: 0,
          delayedCount: 0,
          latestState: 'completed',
          latestAt: '2026-07-01T00:00:00Z',
        },
        formalReport: {
          productionCount: 1,
          latestState: 'signed',
          latestAt: '2026-07-02T00:00:00Z',
        },
        delivery: {
          deliveredAt: '2026-07-03T00:00:00Z',
          confirmedAt: null,
          pendingConfirmationCount: 1,
        },
        contractDraftExport: null,
        primaryAttention: {
          code: 'delivery_confirmation_required',
          severity: 'warning',
          additionalCount: 0,
        },
        lastBusinessFactAt: '2026-07-03T00:00:00Z',
      },
    ]
  : [];

export function createFixtureOperationsBusinessOverview(
  filters: BusinessFilters,
  cursor: string | undefined,
): OperationsBusinessOverview {
  const q = filters.q?.toLocaleLowerCase('zh-CN');
  const filtered = fixtureItems.filter(
    (item) =>
      (!q ||
        item.project.name.toLocaleLowerCase('zh-CN').includes(q) ||
        item.customer.name.toLocaleLowerCase('zh-CN').includes(q)) &&
      (!filters.projectState || item.project.state === filters.projectState) &&
      (!filters.attention || item.primaryAttention.code === filters.attention),
  );
  const offsetMatch = cursor ? /^fixture_(\d+)$/.exec(cursor) : null;
  const offset = offsetMatch ? Number(offsetMatch[1]) : 0;
  const items = filtered.slice(offset, offset + 4);
  const nextOffset = offset + items.length;
  const hasMore = nextOffset < filtered.length;
  const countState = (state: BusinessOverviewProjectState) =>
    filtered.filter((item) => item.project.state === state).length;
  return {
    schemaVersion: 1,
    asOf: '2026-08-24T10:30:00Z',
    summary: {
      scope: 'filtered',
      tenantProjectCount: fixtureItems.length,
      projectCount: filtered.length,
      projectStateCounts: {
        draft: countState('draft'),
        active: countState('active'),
        paused: countState('paused'),
        archived: countState('archived'),
      },
      setupReadyProjectCount: filtered.filter((item) => item.setup.setupReady).length,
      projectWithEntitlementRecordCount: filtered.filter(
        (item) => item.serviceEntitlements.length > 0,
      ).length,
      activeEntitlementCount: filtered
        .flatMap((item) => item.serviceEntitlements)
        .filter((item) => item.effectiveNow).length,
      attentionProjectCount: filtered.filter(
        (item) => item.primaryAttention.code !== 'no_current_attention',
      ).length,
    },
    commercialCapabilities: {
      quotationHistory: 'unsupported',
      signedContractLedger: 'unsupported',
      invoiceReceivablePaymentLedger: 'unsupported',
    },
    items,
    page: {
      limit: 4,
      nextCursor: hasMore ? `fixture_${nextOffset}` : null,
      hasMore,
      filteredTotal: filtered.length,
    },
  };
}
