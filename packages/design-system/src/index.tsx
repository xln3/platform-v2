import {
  Component,
  createContext,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ErrorInfo,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

export type ExperienceContextValue = {
  tenantPubId: string;
  tenantLabel: string;
  projectPubId: string;
  projectLabel: string;
  userPubId: string;
  userLabel: string;
  roles: readonly ('customer' | 'operator' | 'analyst' | 'reviewer' | 'admin')[];
  source?: 'live' | 'contract-fixture';
};

const ExperienceContext = createContext<ExperienceContextValue | null>(null);

export function useExperienceContext(): ExperienceContextValue {
  const value = useContext(ExperienceContext);
  if (!value) throw new Error('ExperienceProvider is required');
  return value;
}

export const useOptionalExperienceContext = (): ExperienceContextValue | null =>
  useContext(ExperienceContext);

const secretKeyPattern =
  /cookie|authorization|token|otp|password|phone|profile|biometric|storage.?state|qr/i;
const secretValuePattern =
  /(?:bearer\s+|session\s*=|cookie(?:\s|=|:)|token(?:\s|=|:)|otp(?:\s|=|:)|password(?:\s|=|:)|proxy(?:[_ -]?password)?(?:\s|=|:)|profile(?:[_ -]?path)?(?:\s|=|:)|biometric|dlp-canary|(?:^|[^\w])\d{6}(?:[^\w]|$)|(?:^|[^\w])1[3-9]\d{9}(?:[^\w]|$)|\/[^\s]*profile(?:\/[^\s]*)?)/i;

/** Detects secret-shaped values before they enter UI, cache, URL, telemetry or error reports. */
export const containsClientSecret = (value: string): boolean => secretValuePattern.test(value);
const safeExperienceValue = (value: unknown, fallback: string, maxLength: number): string =>
  typeof value === 'string' &&
  value.trim().length > 0 &&
  value.length <= maxLength &&
  !containsClientSecret(value)
    ? value
    : fallback;

/** Value-level DLP projection for every identity label and public identifier entering React state. */
export function projectSafeExperienceContext(
  value: ExperienceContextValue,
): ExperienceContextValue {
  return {
    tenantPubId: safeExperienceValue(value.tenantPubId, 'tnt_redacted', 120),
    tenantLabel: safeExperienceValue(value.tenantLabel, '租户已隐藏', 120),
    projectPubId: safeExperienceValue(value.projectPubId, '', 120),
    projectLabel: safeExperienceValue(value.projectLabel, '未命名项目', 120),
    userPubId: safeExperienceValue(value.userPubId, 'usr_redacted', 255),
    userLabel: safeExperienceValue(value.userLabel, '用户已隐藏', 120),
    roles: value.roles.filter((role) =>
      ['customer', 'operator', 'analyst', 'reviewer', 'admin'].includes(role),
    ),
    source: value.source === 'live' ? 'live' : 'contract-fixture',
  };
}
const containsNumericClientSecret = (value: number): boolean => {
  if (!Number.isInteger(value)) return false;
  const digits = String(Math.abs(value));
  return /^\d{6}$/.test(digits) || /^1[3-9]\d{9}$/.test(digits);
};

/** Safe structured error/telemetry projection. Unknown and secret-looking properties are dropped recursively. */
export function redactClientDiagnostic(value: unknown, depth = 0): unknown {
  if (depth > 4) return '[depth-limited]';
  if (typeof value === 'string')
    return containsClientSecret(value) ? '[redacted]' : value.slice(0, 500);
  if (typeof value === 'number') return containsNumericClientSecret(value) ? '[redacted]' : value;
  if (typeof value === 'boolean' || value === null) return value;
  if (Array.isArray(value))
    return value.slice(0, 30).map((item) => redactClientDiagnostic(item, depth + 1));
  if (typeof value !== 'object') return undefined;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([key]) => !secretKeyPattern.test(key))
      .map(([key, item]) => [key, redactClientDiagnostic(item, depth + 1)]),
  );
}

export class ProductErrorBoundary extends Component<
  { children: ReactNode; onDiagnostic: ((diagnostic: unknown) => void) | undefined },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onDiagnostic?.(
      redactClientDiagnostic({
        name: error.name,
        message: error.message,
        componentStack: info.componentStack,
      }),
    );
  }
  render() {
    if (this.state.failed) {
      return (
        <main className="fatal-boundary" role="alert">
          <span className="overline">Error boundary</span>
          <h1>此页面暂时无法显示</h1>
          <p>错误已按安全规则记录。账号秘密、URL 敏感参数和表单内容不会进入错误报告。</p>
          <button className="button" onClick={() => this.setState({ failed: false })}>
            重试页面
          </button>
        </main>
      );
    }
    return this.props.children;
  }
}

export function ExperienceProvider({
  value,
  children,
  onDiagnostic,
}: {
  value: ExperienceContextValue;
  children: ReactNode;
  onDiagnostic?: (diagnostic: unknown) => void;
}) {
  const safeValue = useMemo(() => projectSafeExperienceContext(value), [value]);
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false },
          mutations: { retry: 0 },
        },
      }),
  );
  return (
    <ProductErrorBoundary onDiagnostic={onDiagnostic}>
      <ExperienceContext.Provider value={safeValue}>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </ExperienceContext.Provider>
    </ProductErrorBoundary>
  );
}

export type ExperienceLoadResult =
  | { kind: 'ready'; value: ExperienceContextValue }
  | { kind: 'fixture'; value: ExperienceContextValue }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

export function ValidatedExperienceProvider({
  load,
  allowedRoles,
  children,
  onDiagnostic,
}: {
  load: () => Promise<ExperienceLoadResult>;
  allowedRoles: readonly ExperienceContextValue['roles'][number][];
  children: ReactNode;
  onDiagnostic?: (diagnostic: unknown) => void;
}) {
  const [result, setResult] = useState<ExperienceLoadResult | null>(null);
  useEffect(() => {
    let active = true;
    void load()
      .then((value) => {
        if (!active) return;
        setResult(
          value.kind === 'ready' || value.kind === 'fixture'
            ? { ...value, value: projectSafeExperienceContext(value.value) }
            : value,
        );
      })
      .catch((error: unknown) => {
        onDiagnostic?.(redactClientDiagnostic(error));
        if (active) setResult({ kind: 'unavailable' });
      });
    return () => {
      active = false;
    };
  }, [load, onDiagnostic]);

  if (!result) {
    return (
      <main className="fatal-boundary" aria-busy="true">
        <StatePanel state="loading" />
      </main>
    );
  }
  if (result.kind === 'unavailable') {
    return (
      <main className="fatal-boundary">
        <StatePanel state="failed" onRetry={() => location.reload()} />
      </main>
    );
  }
  const allowed =
    (result.kind === 'ready' || result.kind === 'fixture') &&
    result.value.roles.some((role) => allowedRoles.includes(role));
  if (result.kind === 'forbidden' || !allowed) {
    return (
      <main className="fatal-boundary">
        <StatePanel state="forbidden" />
      </main>
    );
  }
  return (
    <ExperienceProvider value={result.value} {...(onDiagnostic ? { onDiagnostic } : {})}>
      {children}
    </ExperienceProvider>
  );
}

export function useUrlParam<T extends string>(
  key: string,
  fallback: T,
  allowedValues: readonly T[],
): [T, (value: T, replace?: boolean) => void] {
  const allowed = useMemo(() => new Set<string>(allowedValues), [allowedValues]);
  const read = (): T => {
    if (typeof window === 'undefined') return fallback;
    const value = new URL(window.location.href).searchParams.get(key);
    return value && allowed.has(value) ? (value as T) : fallback;
  };
  const [value, setValue] = useState<T>(read);
  useEffect(() => {
    const onPopState = () => setValue(read());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  });
  const update = (next: T, replace = false) => {
    if (!allowed.has(next)) return;
    const url = new URL(window.location.href);
    if (next === fallback) url.searchParams.delete(key);
    else url.searchParams.set(key, next);
    window.history[replace ? 'replaceState' : 'pushState']({}, '', url);
    setValue(next);
  };
  return [value, update];
}

export function sanitizeClientUrl(allowedSections: readonly string[]): boolean {
  if (typeof window === 'undefined') return false;
  const url = new URL(window.location.href);
  let changed = false;
  for (const [parameter, parameterValue] of [...url.searchParams.entries()]) {
    if (secretKeyPattern.test(parameter) || containsClientSecret(parameterValue)) {
      url.searchParams.delete(parameter);
      changed = true;
    }
  }
  const section = url.searchParams.get('section');
  if (section && !allowedSections.includes(section)) {
    url.searchParams.delete('section');
    changed = true;
  }
  if (changed) window.history.replaceState(window.history.state, '', url);
  return changed;
}

export type DataState =
  | 'loading'
  | 'empty'
  | 'real-zero'
  | 'insufficient'
  | 'failed'
  | 'delayed'
  | 'forbidden'
  | 'ready';

export type NavItem = { id: string; label: string; badge?: string; href?: string };
export type Metric = {
  label: string;
  value: string;
  detail: string;
  trend?: string;
  state?: DataState;
};

const stateCopy: Record<Exclude<DataState, 'ready'>, { title: string; body: string }> = {
  loading: { title: '正在加载', body: '数据正在安全获取，请稍候。' },
  empty: { title: '暂无数据', body: '当前筛选下没有记录，可以调整筛选条件。' },
  'real-zero': { title: '结果为 0', body: '采集已完成，这是真实业务结果，不是缺失数据。' },
  insufficient: { title: '样本不足', body: '已有数据尚未达到可解释门槛，暂不生成结论。' },
  failed: { title: '加载失败', body: '局部请求失败，其他区域仍可使用。' },
  delayed: { title: '数据延迟', body: '数据仍在处理，页面会保留最近可用版本。' },
  forbidden: { title: '无权查看', body: '当前角色没有此资源权限，也不会披露资源是否存在。' },
};

export function StatePanel({
  state,
  onRetry,
}: {
  state: Exclude<DataState, 'ready'>;
  onRetry?: () => void;
}) {
  const copy = stateCopy[state];
  return (
    <section
      className={`state-panel state-${state}`}
      role={state === 'failed' ? 'alert' : 'status'}
    >
      <span className="state-dot" aria-hidden="true" />
      <div>
        <strong>{copy.title}</strong>
        <p>{copy.body}</p>
      </div>
      {state === 'failed' && onRetry ? (
        <button className="button button-secondary" onClick={onRetry}>
          重试此区域
        </button>
      ) : null}
    </section>
  );
}

export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'positive' | 'warning' | 'danger' | 'info';
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Pagination({
  page,
  pageCount,
  onPageChange,
  label = '分页',
}: {
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  label?: string;
}) {
  const safePageCount = Math.max(1, pageCount);
  const safePage = Math.min(Math.max(1, page), safePageCount);
  return (
    <nav className="pagination" aria-label={label}>
      <button disabled={safePage === 1} onClick={() => onPageChange(safePage - 1)}>
        上一页
      </button>
      <span aria-current="page">
        第 {safePage} / {safePageCount} 页
      </span>
      <button disabled={safePage === safePageCount} onClick={() => onPageChange(safePage + 1)}>
        下一页
      </button>
    </nav>
  );
}

export function Dialog({
  title,
  eyebrow,
  closeLabel,
  onClose,
  children,
}: {
  title: string;
  eyebrow?: string;
  closeLabel: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(
    typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  );
  useEffect(() => {
    return () => returnFocusRef.current?.focus();
  }, []);
  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])',
      ) ?? [],
    );
    const first = focusable[0];
    const last = focusable.at(-1);
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <div className="modal-head">
          <div>
            {eyebrow ? <span className="overline">{eyebrow}</span> : null}
            <h2 id={titleId}>{title}</h2>
          </div>
          <button aria-label={closeLabel} autoFocus onClick={onClose}>
            ×
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}

export function Toast({
  children,
  tone = 'positive',
}: {
  children: ReactNode;
  tone?: 'positive' | 'warning' | 'negative' | 'neutral';
}) {
  return (
    <div className={`toast toast-${tone}`} role={tone === 'negative' ? 'alert' : 'status'}>
      {children}
    </div>
  );
}

export function FormField({
  id,
  label,
  error,
  hint,
  children,
}: {
  id: string;
  label: string;
  error?: { message?: string | undefined } | undefined;
  hint?: string | undefined;
  children: ReactNode;
}) {
  return (
    <div className="form-field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
      {error?.message ? (
        <span className="field-error" id={`${id}-error`} role="alert">
          {error.message}
        </span>
      ) : null}
    </div>
  );
}

export function FilterBar({
  label,
  className = '',
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`filter-bar ${className}`.trim()} aria-label={label}>
      {children}
    </section>
  );
}

export function TableRegion({
  label,
  children,
  className = '',
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`table-scroll ${className}`.trim()}
      role="region"
      aria-label={label}
      tabIndex={0}
    >
      {children}
    </div>
  );
}

export function MetricGrid({ metrics }: { metrics: Metric[] }) {
  return (
    <div className="metric-grid">
      {metrics.map((metric) => (
        <article className="metric-card" key={metric.label}>
          <div className="metric-label">{metric.label}</div>
          <div className="metric-value">{metric.value}</div>
          <div className="metric-foot">
            <span>{metric.detail}</span>
            {metric.trend ? <Badge tone="positive">{metric.trend}</Badge> : null}
          </div>
        </article>
      ))}
    </div>
  );
}

export type AccountSummaryProjection = {
  accountMask: string;
  platformLabel: string;
  ownerLabel: string;
  custodyMode: 'server' | 'customer-device' | 'hybrid';
  admissionLevel:
    | 'catalogued'
    | 'adapter_ready'
    | 'login_verified'
    | 'read_verified'
    | 'draft_verified'
    | 'publish_verified'
    | 'suspended';
  scopes: readonly ('read' | 'query' | 'draft' | 'publish')[];
  expiresLabel: string;
  regionLabel: string;
  sessionHealth: 'healthy' | 'degraded' | 'challenge_required' | 'revoked';
  lastVerifiedLabel: string;
  interventionStatus?: 'none' | 'waiting' | 'paired' | 'refused' | 'timed_out' | 'completed';
};

const allowedScopes = new Set(['read', 'query', 'draft', 'publish']);
const allowedCustodyModes = new Set(['server', 'customer-device', 'hybrid']);
const allowedAdmissionLevels = new Set([
  'catalogued',
  'adapter_ready',
  'login_verified',
  'read_verified',
  'draft_verified',
  'publish_verified',
  'suspended',
]);
const allowedSessionHealth = new Set(['healthy', 'degraded', 'challenge_required', 'revoked']);
const allowedInterventionStatus = new Set([
  'none',
  'waiting',
  'paired',
  'refused',
  'timed_out',
  'completed',
]);
const safeText = (value: unknown, fallback = '—'): string =>
  typeof value === 'string' && value.trim() && !containsClientSecret(value)
    ? value.slice(0, 120)
    : fallback;

/** Allow-list boundary used before account data reaches UI, cache, URL or telemetry. */
export function projectSafeAccountSummary(input: unknown): AccountSummaryProjection {
  const source =
    typeof input === 'object' && input !== null ? (input as Record<string, unknown>) : {};
  const scopes = Array.isArray(source.scopes)
    ? source.scopes.filter(
        (scope): scope is AccountSummaryProjection['scopes'][number] =>
          typeof scope === 'string' && allowedScopes.has(scope),
      )
    : [];
  const custodyMode =
    typeof source.custodyMode === 'string' && allowedCustodyModes.has(source.custodyMode)
      ? (source.custodyMode as AccountSummaryProjection['custodyMode'])
      : 'customer-device';
  const admissionLevel =
    typeof source.admissionLevel === 'string' && allowedAdmissionLevels.has(source.admissionLevel)
      ? (source.admissionLevel as AccountSummaryProjection['admissionLevel'])
      : 'catalogued';
  const sessionHealth =
    typeof source.sessionHealth === 'string' && allowedSessionHealth.has(source.sessionHealth)
      ? (source.sessionHealth as AccountSummaryProjection['sessionHealth'])
      : 'degraded';
  const interventionStatus =
    typeof source.interventionStatus === 'string' &&
    allowedInterventionStatus.has(source.interventionStatus)
      ? (source.interventionStatus as NonNullable<AccountSummaryProjection['interventionStatus']>)
      : 'none';
  return {
    accountMask: safeText(source.accountMask, '账号已隐藏'),
    platformLabel: safeText(source.platformLabel, '未知平台'),
    ownerLabel: safeText(source.ownerLabel, '所有者已隐藏'),
    custodyMode,
    admissionLevel,
    scopes,
    expiresLabel: safeText(source.expiresLabel),
    regionLabel: safeText(source.regionLabel),
    sessionHealth,
    lastVerifiedLabel: safeText(source.lastVerifiedLabel, '尚未验证'),
    interventionStatus,
  };
}

const custodyLabels = {
  server: '服务器托管',
  'customer-device': '客户终端托管',
  hybrid: '混合托管',
};

const admissionLabels: Record<AccountSummaryProjection['admissionLevel'], string> = {
  catalogued: '已登记',
  adapter_ready: '适配器就绪 · 未经 live 验证',
  login_verified: '登录已验证',
  read_verified: '读取已验证',
  draft_verified: '草稿已验证',
  publish_verified: '发布已验证',
  suspended: '已暂停',
};

export function AuthorizationScope({ scopes }: { scopes: AccountSummaryProjection['scopes'] }) {
  return (
    <div className="scope-row" aria-label="授权范围">
      {scopes.length ? (
        scopes.map((scope) => (
          <Badge tone="info" key={scope}>
            {scope}
          </Badge>
        ))
      ) : (
        <Badge tone="warning">未授权任何动作</Badge>
      )}
    </div>
  );
}

export function CustodyMode({ value }: { value: AccountSummaryProjection['custodyMode'] }) {
  return <Badge>{custodyLabels[value]}</Badge>;
}

export function AdmissionLevel({ value }: { value: AccountSummaryProjection['admissionLevel'] }) {
  return (
    <Badge
      tone={value.endsWith('_verified') ? 'positive' : value === 'suspended' ? 'danger' : 'warning'}
    >
      {admissionLabels[value]}
    </Badge>
  );
}

export function SessionHealth({ value }: { value: AccountSummaryProjection['sessionHealth'] }) {
  const labels = {
    healthy: '会话健康',
    degraded: '会话降级',
    challenge_required: '等待人工验证',
    revoked: '已撤销',
  };
  return (
    <Badge tone={value === 'healthy' ? 'positive' : value === 'revoked' ? 'danger' : 'warning'}>
      {labels[value]}
    </Badge>
  );
}

export function InterventionStatus({
  value = 'none',
}: {
  value?: AccountSummaryProjection['interventionStatus'];
}) {
  const labels = {
    none: '无需人工',
    waiting: '等待客户',
    paired: '终端已配对',
    refused: '客户已拒绝',
    timed_out: '配对已超时',
    completed: '验证已完成',
  };
  const tone =
    value === 'completed' || value === 'none'
      ? 'positive'
      : value === 'refused' || value === 'timed_out'
        ? 'danger'
        : 'warning';
  return <Badge tone={tone}>{labels[value]}</Badge>;
}

export type RevocationReceiptProjection = {
  receiptId: string;
  revokedAtLabel: string;
  actorLabel: string;
  leasesStopped: boolean;
  sessionsClosed: boolean;
  secretCopiesPurged: boolean;
};

export function RevocationReceipt({ receipt }: { receipt: RevocationReceiptProjection }) {
  return (
    <article className="receipt" aria-label={`撤销回执 ${receipt.receiptId}`}>
      <span className="overline">Revocation receipt</span>
      <h3>撤销已执行</h3>
      <dl className="definition-grid">
        <div>
          <dt>回执编号</dt>
          <dd>{receipt.receiptId}</dd>
        </div>
        <div>
          <dt>撤销时间</dt>
          <dd>{receipt.revokedAtLabel}</dd>
        </div>
        <div>
          <dt>发起人</dt>
          <dd>{receipt.actorLabel}</dd>
        </div>
      </dl>
      <ul className="receipt-checks">
        <li data-complete={receipt.leasesStopped}>停止新租约</li>
        <li data-complete={receipt.sessionsClosed}>关闭活动会话</li>
        <li data-complete={receipt.secretCopiesPurged}>删除托管秘密副本</li>
      </ul>
    </article>
  );
}

export function AccountSummary({ account }: { account: AccountSummaryProjection }) {
  return (
    <article className="account-card">
      <div className="account-head">
        <div>
          <span className="overline">{account.platformLabel}</span>
          <h3>{account.accountMask}</h3>
        </div>
        <SessionHealth value={account.sessionHealth} />
      </div>
      <dl className="definition-grid">
        <div>
          <dt>账号所有者</dt>
          <dd>{account.ownerLabel}</dd>
        </div>
        <div>
          <dt>托管模式</dt>
          <dd>
            <CustodyMode value={account.custodyMode} />
          </dd>
        </div>
        <div>
          <dt>真实准入等级</dt>
          <dd>
            <AdmissionLevel value={account.admissionLevel} />
          </dd>
        </div>
        <div>
          <dt>最近验证</dt>
          <dd>{account.lastVerifiedLabel}</dd>
        </div>
        <div>
          <dt>授权地域</dt>
          <dd>{account.regionLabel}</dd>
        </div>
        <div>
          <dt>授权到期</dt>
          <dd>{account.expiresLabel}</dd>
        </div>
      </dl>
      <AuthorizationScope scopes={account.scopes} />
      <p className="security-note">
        此卡片仅展示安全摘要。Cookie、token、OTP、代理密码、完整手机号、profile
        路径和生物材料不会进入前端。
      </p>
    </article>
  );
}

export function ProductShell({
  product,
  title,
  description,
  nav,
  children,
  probe,
}: {
  product: string;
  title: string;
  description: string;
  nav: NavItem[];
  children: (active: string) => ReactNode;
  probe: () => Promise<{ status: string }>;
}) {
  const navIds = useMemo(() => nav.map((item) => item.id), [nav]);
  const [active, setActive] = useUrlParam('section', nav[0]?.id ?? '', navIds);
  const [status, setStatus] = useState('checking');
  const [contextOpen, setContextOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [taskEntryOpen, setTaskEntryOpen] = useState(false);
  const experience = useOptionalExperienceContext();
  const navId = useId();
  const mainRef = useRef<HTMLElement>(null);
  useEffect(() => {
    void probe()
      .then((value) => setStatus(value.status))
      .catch(() => setStatus('unavailable'));
  }, [probe]);
  useEffect(() => {
    const sanitize = () => sanitizeClientUrl(navIds);
    sanitize();
    window.addEventListener('popstate', sanitize);
    return () => window.removeEventListener('popstate', sanitize);
  }, [navIds]);
  useEffect(() => {
    mainRef.current
      ?.querySelectorAll<HTMLElement>('.panel:has(.data-table), .table-scroll')
      .forEach((region) => {
        region.tabIndex = 0;
        if (!region.getAttribute('aria-label'))
          region.setAttribute('aria-label', '可横向滚动的数据区域');
      });
  }, [active]);
  const exportSafeView = () => {
    const payload = {
      product,
      section: active,
      tenant: experience?.tenantLabel ?? 'contract fixture tenant',
      project: experience?.projectLabel ?? 'contract fixture project',
      source: experience?.source ?? 'contract-fixture',
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' }),
    );
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${product.toLowerCase().replaceAll(' ', '-')}-${active}-view.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  return (
    <>
      <div className="product">
        <a className="skip-link" href="#main-content">
          跳到主要内容
        </a>
        <aside className="sidebar">
          <div className="brand-mark" aria-label="GEO Platform V2">
            <i>G</i>
            <span>
              GEO
              <br />
              Platform
            </span>
          </div>
          <div className="workspace-label">{product}</div>
          <nav aria-label={`${product} 主导航`} id={navId}>
            {nav.map((item) =>
              item.href ? (
                <a aria-label={item.label} href={item.href} key={item.id}>
                  <span>{item.label}</span>
                  {item.badge ? <em>{item.badge}</em> : null}
                </a>
              ) : (
                <button
                  aria-label={item.label}
                  aria-current={active === item.id ? 'page' : undefined}
                  className={active === item.id ? 'nav-active' : ''}
                  key={item.id}
                  onClick={() => setActive(item.id)}
                >
                  <span>{item.label}</span>
                  {item.badge ? <em>{item.badge}</em> : null}
                </button>
              ),
            )}
          </nav>
          <div className="sidebar-foot">
            <span className="live-dot" />
            {status}
          </div>
        </aside>
        <div className="content-frame">
          <header className="topbar">
            <button
              className="project-switcher"
              aria-expanded={contextOpen}
              onClick={() => setContextOpen(true)}
            >
              {experience
                ? `${experience.tenantLabel} · ${experience.projectLabel}`
                : '云岫智能 · 品牌增长项目'}{' '}
              <span>⌄</span>
            </button>
            <div className="top-actions">
              <button
                aria-label="通知"
                aria-expanded={notificationsOpen}
                onClick={() => setNotificationsOpen(true)}
              >
                ◌
              </button>
              <div className="avatar" title={experience?.userLabel}>
                {experience?.userLabel.slice(0, 1) ?? '林'}
              </div>
            </div>
          </header>
          <main id="main-content" ref={mainRef} tabIndex={-1}>
            <div className="page-heading">
              <div>
                <span className="overline">{product}</span>
                <h1>{title}</h1>
                <p>{description}</p>
              </div>
              <div className="heading-actions">
                <button className="button button-secondary" onClick={exportSafeView}>
                  导出视图
                </button>
                <button className="button" onClick={() => setTaskEntryOpen(true)}>
                  创建任务
                </button>
              </div>
            </div>
            {children(active)}
          </main>
        </div>
      </div>
      {contextOpen ? (
        <Dialog
          title="当前项目上下文"
          eyebrow={product}
          closeLabel="关闭项目上下文"
          onClose={() => setContextOpen(false)}
        >
          <dl className="definition-grid">
            <div>
              <dt>租户</dt>
              <dd>{experience?.tenantLabel ?? 'Contract fixture tenant'}</dd>
            </div>
            <div>
              <dt>项目</dt>
              <dd>{experience?.projectLabel ?? 'Contract fixture project'}</dd>
            </div>
            <div>
              <dt>用户</dt>
              <dd>{experience?.userLabel ?? 'Contract fixture user'}</dd>
            </div>
            <div>
              <dt>数据来源</dt>
              <dd>
                {experience?.source === 'live' ? '已验证 live session' : 'OpenAPI contract fixture'}
              </dd>
            </div>
          </dl>
          <p className="security-note">
            此处只展示安全投影；不会显示 Cookie、token、OTP、完整手机号或 profile 路径。
          </p>
        </Dialog>
      ) : null}
      {notificationsOpen ? (
        <Dialog
          title="通知中心"
          eyebrow="Safe activity summaries"
          closeLabel="关闭通知中心"
          onClose={() => setNotificationsOpen(false)}
        >
          <ol className="timeline">
            <li>
              <strong>数据窗口已冻结</strong>
              <span>当前项目 · 今天 10:20</span>
            </li>
            <li>
              <strong>有一项待人工确认</strong>
              <span>只显示安全摘要，不披露账号是否存在</span>
            </li>
          </ol>
        </Dialog>
      ) : null}
      {taskEntryOpen ? (
        <Dialog
          title="创建任务或申请"
          eyebrow="Choose a validated workspace"
          closeLabel="关闭任务入口"
          onClose={() => setTaskEntryOpen(false)}
        >
          <p className="panel-subtitle">
            选择工作区后再填写其领域表单；共享壳不会绕过审批或伪造统一任务。
          </p>
          <div className="button-row">
            {nav
              .filter((item) => !item.href)
              .slice(0, 5)
              .map((item) => (
                <button
                  className="button button-secondary"
                  key={item.id}
                  onClick={() => {
                    setActive(item.id);
                    setTaskEntryOpen(false);
                  }}
                >
                  前往{item.label}
                </button>
              ))}
          </div>
        </Dialog>
      ) : null}
    </>
  );
}

/** @deprecated Use ProductShell for product applications. */
export const AppShell = ProductShell;
