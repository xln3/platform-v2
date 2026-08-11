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

/** Browser localStorage keys that carry identity hints in fixture mode; production never persists them. */
export const identitySessionHintStorageKeys = [
  'geo.session.tenant',
  'geo.session.actor',
  'geo.session.role',
] as const;

/** Unified platform sign-in entry shared by every product shell and session-expired surface. */
export const platformLoginHref = '/platform/operations/login';

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
  /(?:bearer\s+|session\s*=|cookie(?:\s|=|:)|token(?:\s|=|:)|otp(?:\s|=|:)|password(?:\s|=|:)|proxy(?:[_ -]?password)?(?:\s|=|:)|profile(?:s|[_ /-]?(?:path|dir|directory))?(?:\s|=|:|\\|\/)|biometric|dlp-canary|(?:^|[^\w])\d{6}(?:[^\w]|$)|(?:^|[^\w])\d{3}[\s.-]\d{3}(?:[^\w]|$)|1[3-9]\d{9}|1[3-9](?:[\s().-]?\d){9}|(?:[A-Za-z]:\\|\\\\)[^\r\n]{0,1024}(?:profiles?|user[ _-]?data)(?:\\[^\r\n]{0,1024})?|\/[^\s]*profile(?:s?\/[^\s]*)?)/i;
const clientSecretInvisiblePattern = /[\u200b-\u200d\u2060\ufeff]/g;
const normalizeClientSecretCandidate = (value: string): string => {
  let normalized = value.normalize('NFKC').replace(clientSecretInvisiblePattern, '');
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const decoded = decodeURIComponent(normalized);
      if (decoded === normalized) break;
      normalized = decoded.normalize('NFKC').replace(clientSecretInvisiblePattern, '');
    } catch {
      break;
    }
  }
  return normalized;
};
/** Detects normalized secret-shaped property and parameter names before browser retention. */
export const containsClientSecretKey = (value: string): boolean =>
  secretKeyPattern.test(normalizeClientSecretCandidate(value));

/** Detects secret-shaped values before they enter UI, cache, URL, telemetry or error reports. */
export const containsClientSecret = (value: string): boolean =>
  secretValuePattern.test(normalizeClientSecretCandidate(value));
const unsafeClientControlPattern =
  /[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069]/u;
/** Rejects invisible delimiters, line controls and bidi overrides before identity projection. */
export const containsUnsafeClientControlCharacter = (value: string): boolean =>
  unsafeClientControlPattern.test(value);
/** Encodes ordered string fields without delimiter ambiguity or raw control characters in the key. */
export const createStructuredClientScopeKey = (parts: readonly string[]): string =>
  JSON.stringify(parts);
const safeExperienceValue = (value: unknown, fallback: string, maxLength: number): string =>
  typeof value === 'string' &&
  value.trim().length > 0 &&
  value.length <= maxLength &&
  !containsUnsafeClientControlCharacter(value) &&
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

/** Canonical, collision-free cache/remount scope derived only from the safe experience projection. */
export function createSafeExperienceScopeKey(value: ExperienceContextValue): string {
  const safeValue = projectSafeExperienceContext(value);
  return JSON.stringify([
    safeValue.tenantPubId,
    safeValue.projectPubId,
    safeValue.userPubId,
    [...safeValue.roles].sort(),
    safeValue.source,
  ]);
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
      .filter(([key]) => !containsClientSecretKey(key))
      .map(([key, item]) => [key, redactClientDiagnostic(item, depth + 1)]),
  );
}

export const safeClientDiagnosticEventName = 'geo:safe-client-diagnostic';
export type SafeClientErrorKind =
  | 'react_caught_error'
  | 'react_uncaught_error'
  | 'react_recoverable_error'
  | 'react_error_boundary'
  | 'experience_bootstrap_error'
  | 'window_error'
  | 'unhandled_rejection';
export type SafeClientErrorDiagnostic = Readonly<{
  kind: SafeClientErrorKind;
  errorName: string;
  componentFrames: number;
  hasCause: boolean;
}>;
type ClientErrorInfo = { componentStack?: string | null | undefined };
const safeClientErrorNames = new Set([
  'AggregateError',
  'Error',
  'EvalError',
  'RangeError',
  'ReferenceError',
  'SyntaxError',
  'TypeError',
  'URIError',
]);

/** Count-only React error projection. Raw messages, stacks, causes and browser state never leave this boundary. */
export function projectSafeClientErrorDiagnostic(
  kind: SafeClientErrorKind,
  error: unknown,
  errorInfo?: ClientErrorInfo,
): SafeClientErrorDiagnostic {
  const errorName =
    error instanceof Error && safeClientErrorNames.has(error.name) ? error.name : 'Error';
  const componentFrames =
    typeof errorInfo?.componentStack === 'string'
      ? Math.min(
          errorInfo.componentStack
            .split('\n')
            .reduce((count, line) => count + (line.trim().length > 0 ? 1 : 0), 0),
          100,
        )
      : 0;
  return Object.freeze({
    kind,
    errorName,
    componentFrames,
    hasCause: error instanceof Error && error.cause !== undefined,
  });
}

function dispatchSafeClientErrorDiagnostic(diagnostic: SafeClientErrorDiagnostic): void {
  if (typeof window === 'undefined' || typeof CustomEvent === 'undefined') return;
  window.dispatchEvent(new CustomEvent(safeClientDiagnosticEventName, { detail: diagnostic }));
}

function reportClientError(
  kind: SafeClientErrorKind,
  error: unknown,
  errorInfo?: ClientErrorInfo,
  onDiagnostic?: (diagnostic: unknown) => void,
): void {
  const diagnostic = projectSafeClientErrorDiagnostic(kind, error, errorInfo);
  if (onDiagnostic) {
    try {
      onDiagnostic(diagnostic);
    } catch {
      // A diagnostic sink may not turn an already-handled product failure into a second failure.
    }
  }
  dispatchSafeClientErrorDiagnostic(diagnostic);
}

/** React 19 root callbacks replace raw console/reportError defaults with the safe count-only channel. */
export const safeReactRootErrorHandlers = Object.freeze({
  onCaughtError: (error: unknown, errorInfo: ClientErrorInfo) =>
    reportClientError('react_caught_error', error, errorInfo),
  onUncaughtError: (error: unknown, errorInfo: ClientErrorInfo) =>
    reportClientError('react_uncaught_error', error, errorInfo),
  onRecoverableError: (error: unknown, errorInfo: ClientErrorInfo) =>
    reportClientError('react_recoverable_error', error, errorInfo),
});

/** Suppresses raw browser defaults only after projecting non-React global failures to the safe channel. */
export function installClientDiagnosticSecurity(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const relayedSafeErrors = new WeakSet<Error>();
  const relaySafeRuntimeError = (message: string, rejection: boolean) => {
    const error = new Error(message);
    relayedSafeErrors.add(error);
    window.setTimeout(() => {
      if (rejection) {
        void Promise.reject(error);
      } else {
        throw error;
      }
    }, 0);
  };
  const onWindowError = (event: ErrorEvent) => {
    if (event.error instanceof Error && relayedSafeErrors.has(event.error)) {
      relayedSafeErrors.delete(event.error);
      return;
    }
    reportClientError('window_error', event.error);
    event.stopImmediatePropagation();
    event.preventDefault();
    relaySafeRuntimeError('GEO_SAFE_WINDOW_ERROR', false);
  };
  const onUnhandledRejection = (event: PromiseRejectionEvent) => {
    if (event.reason instanceof Error && relayedSafeErrors.has(event.reason)) {
      relayedSafeErrors.delete(event.reason);
      return;
    }
    reportClientError('unhandled_rejection', event.reason);
    event.stopImmediatePropagation();
    event.preventDefault();
    relaySafeRuntimeError('GEO_SAFE_UNHANDLED_REJECTION', true);
  };
  window.addEventListener('error', onWindowError);
  window.addEventListener('unhandledrejection', onUnhandledRejection);
  return () => {
    window.removeEventListener('error', onWindowError);
    window.removeEventListener('unhandledrejection', onUnhandledRejection);
  };
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
    reportClientError('react_error_boundary', error, info, this.props.onDiagnostic);
  }
  render() {
    if (this.state.failed) {
      return (
        <main className="fatal-boundary" role="alert">
          <span className="overline">Error boundary</span>
          <h1>此页面暂时无法显示</h1>
          <p>
            错误已按安全规则处理。错误通道只接收类型与栈帧计数，不接收账号秘密、URL 参数或表单内容。
          </p>
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
  const queryIdentityScope = createSafeExperienceScopeKey(safeValue);
  const queryClient = useMemo(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false },
          mutations: { retry: 0 },
        },
      }),
    [queryIdentityScope],
  );
  useEffect(() => () => queryClient.clear(), [queryClient]);
  return (
    <ProductErrorBoundary key={queryIdentityScope} onDiagnostic={onDiagnostic}>
      <ExperienceContext.Provider value={safeValue}>
        <QueryClientProvider key={queryIdentityScope} client={queryClient}>
          {children}
        </QueryClientProvider>
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
  allowAnonymous = false,
  children,
  onDiagnostic,
}: {
  load: () => Promise<ExperienceLoadResult>;
  allowedRoles: readonly ExperienceContextValue['roles'][number][];
  allowAnonymous?: boolean;
  children: ReactNode;
  onDiagnostic?: (diagnostic: unknown) => void;
}) {
  const [result, setResult] = useState<ExperienceLoadResult | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [bootstrapQueryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 0, retry: 0, refetchOnWindowFocus: false },
        },
      }),
  );
  const loadRef = useRef(load);
  const loadGeneration = useRef(0);
  if (loadRef.current !== load) {
    loadRef.current = load;
    loadGeneration.current += 1;
  }
  const currentLoadGeneration = loadGeneration.current;
  useEffect(() => () => bootstrapQueryClient.clear(), [bootstrapQueryClient]);
  useEffect(() => {
    let active = true;
    setResult(null);
    void bootstrapQueryClient
      .fetchQuery({
        queryKey: ['validated-experience', currentLoadGeneration, loadAttempt],
        queryFn: async (): Promise<ExperienceLoadResult> => {
          try {
            const value = await load();
            return value.kind === 'ready' || value.kind === 'fixture'
              ? { ...value, value: projectSafeExperienceContext(value.value) }
              : value;
          } catch (error: unknown) {
            reportClientError('experience_bootstrap_error', error, undefined, onDiagnostic);
            return { kind: 'unavailable' };
          }
        },
      })
      .then((value) => {
        if (!active) return;
        setResult(value);
      });
    return () => {
      active = false;
    };
  }, [bootstrapQueryClient, currentLoadGeneration, load, loadAttempt, onDiagnostic]);

  if (!result) {
    return (
      <main className="fatal-boundary" aria-busy="true">
        <StatePanel state="loading" />
      </main>
    );
  }
  if (result.kind === 'unavailable') {
    if (allowAnonymous) return children;
    return (
      <main className="fatal-boundary">
        <StatePanel
          state="failed"
          onRetry={() => {
            setResult(null);
            setLoadAttempt((attempt) => attempt + 1);
          }}
        />
      </main>
    );
  }
  const allowed =
    (result.kind === 'ready' || result.kind === 'fixture') &&
    result.value.roles.some((role) => allowedRoles.includes(role));
  if (result.kind === 'forbidden' || !allowed) {
    if (allowAnonymous) return children;
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

const clientUrlLimits = {
  parameterCount: 32,
  parameterNameLength: 80,
  parameterValueLength: 500,
  fragmentLength: 512,
  totalLength: 4_096,
} as const;
const clientHistoryStateLimits = {
  depth: 4,
  arrayItems: 30,
  objectEntries: 50,
  keyLength: 80,
  stringLength: 500,
  serializedLength: 50_000,
} as const;
const clientStorageLimits = {
  entries: 10_000,
  keyLength: 120,
  valueLength: 4_096,
} as const;
const omittedClientHistoryValue = Symbol('omitted-client-history-value');

type ClientHistoryProjection = {
  value: unknown | typeof omittedClientHistoryValue;
  changed: boolean;
};

function projectSafeClientHistoryValue(
  value: unknown,
  depth: number,
  ancestors: Set<object>,
): ClientHistoryProjection {
  if (value === null || typeof value === 'boolean') return { value, changed: false };
  if (typeof value === 'string') {
    return value.length <= clientHistoryStateLimits.stringLength && !containsClientSecret(value)
      ? { value, changed: false }
      : { value: omittedClientHistoryValue, changed: true };
  }
  if (typeof value === 'number') {
    return Number.isFinite(value) && !containsNumericClientSecret(value)
      ? { value, changed: false }
      : { value: omittedClientHistoryValue, changed: true };
  }
  if (value === undefined) return { value, changed: false };
  if (typeof value !== 'object' || depth >= clientHistoryStateLimits.depth) {
    return { value: omittedClientHistoryValue, changed: true };
  }
  if (ancestors.has(value)) return { value: omittedClientHistoryValue, changed: true };

  ancestors.add(value);
  if (Array.isArray(value)) {
    const output: unknown[] = [];
    let changed = value.length > clientHistoryStateLimits.arrayItems;
    for (const item of value.slice(0, clientHistoryStateLimits.arrayItems)) {
      const projected = projectSafeClientHistoryValue(item, depth + 1, ancestors);
      changed ||= projected.changed;
      if (projected.value !== omittedClientHistoryValue) output.push(projected.value);
    }
    ancestors.delete(value);
    return { value: output, changed };
  }

  if (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) {
    ancestors.delete(value);
    return { value: omittedClientHistoryValue, changed: true };
  }
  const entries = Object.entries(value);
  const output: Record<string, unknown> = {};
  let changed = entries.length > clientHistoryStateLimits.objectEntries;
  for (const [key, item] of entries.slice(0, clientHistoryStateLimits.objectEntries)) {
    if (key.length > clientHistoryStateLimits.keyLength || containsClientSecretKey(key)) {
      changed = true;
      continue;
    }
    const projected = projectSafeClientHistoryValue(item, depth + 1, ancestors);
    changed ||= projected.changed;
    if (projected.value !== omittedClientHistoryValue) output[key] = projected.value;
  }
  ancestors.delete(value);
  return { value: output, changed };
}

export function projectSafeClientHistoryState(value: unknown): {
  value: unknown;
  changed: boolean;
} {
  const projected = projectSafeClientHistoryValue(value, 0, new Set());
  if (projected.value === omittedClientHistoryValue) return { value: null, changed: true };
  try {
    const serialized = JSON.stringify(projected.value);
    if (serialized === undefined || serialized.length > clientHistoryStateLimits.serializedLength) {
      return { value: null, changed: true };
    }
  } catch {
    return { value: null, changed: true };
  }
  return projected;
}

export function scrubClientStorage(
  storage: Storage,
  requiredKeys: ReadonlySet<string> = new Set(),
): {
  removedRequiredHint: boolean;
  clearedOversizedStorage: boolean;
  removedEntries: number;
} {
  const requiredPresent = [...requiredKeys].some((key) => storage.getItem(key) !== null);
  if (storage.length > clientStorageLimits.entries) {
    const removedEntries = storage.length;
    storage.clear();
    return {
      removedRequiredHint: requiredPresent,
      clearedOversizedStorage: true,
      removedEntries,
    };
  }
  let removedRequiredHint = false;
  let removedEntries = 0;
  const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index)).filter(
    (key): key is string => key !== null,
  );
  for (const key of keys) {
    const value = storage.getItem(key);
    if (
      key.length > clientStorageLimits.keyLength ||
      (value !== null && value.length > clientStorageLimits.valueLength) ||
      containsClientSecretKey(key) ||
      (value !== null && containsClientSecret(value))
    ) {
      storage.removeItem(key);
      removedEntries += 1;
      if (requiredKeys.has(key)) removedRequiredHint = true;
    }
  }
  return { removedRequiredHint, clearedOversizedStorage: false, removedEntries };
}

const decodeClientUrlValue = (value: string): string => {
  let decoded = value;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const next = decodeURIComponent(decoded);
      if (next === decoded) break;
      decoded = next;
    } catch {
      break;
    }
  }
  return decoded;
};

type ClientUrlProjection = {
  url: URL;
  changed: boolean;
};

function projectSafeClientUrl(
  value: string | URL,
  allowedSections: readonly string[],
): ClientUrlProjection {
  const url = new URL(value.toString(), window.location.href);
  let changed = false;
  if (url.username || url.password) {
    url.username = '';
    url.password = '';
    changed = true;
  }
  const rawPathSegments = url.pathname.split('/').filter(Boolean);
  const decodedPathSegments = rawPathSegments.map(decodeClientUrlValue);
  if (
    decodedPathSegments.some(
      (segment) => containsClientSecretKey(segment) || containsClientSecret(segment),
    )
  ) {
    const application =
      decodedPathSegments[0] === 'platform' &&
      ['customer', 'operations', 'reports', 'intelligence'].includes(decodedPathSegments[1] ?? '')
        ? decodedPathSegments[1]
        : null;
    url.pathname = application ? `/platform/${application}/` : '/';
    changed = true;
  }
  const deleteParameter = (parameter: string) => {
    if (!url.searchParams.has(parameter)) return;
    url.searchParams.delete(parameter);
    if (parameter.endsWith('_cursor')) {
      url.searchParams.delete(`${parameter.slice(0, -'_cursor'.length)}_page`);
    }
    changed = true;
  };
  for (const [index, [parameter, parameterValue]] of [...url.searchParams.entries()].entries()) {
    const decodedParameter = decodeClientUrlValue(parameter);
    const decodedValue = decodeClientUrlValue(parameterValue);
    if (
      index >= clientUrlLimits.parameterCount ||
      parameter.length > clientUrlLimits.parameterNameLength ||
      parameterValue.length > clientUrlLimits.parameterValueLength ||
      containsClientSecretKey(decodedParameter) ||
      containsClientSecret(decodedValue)
    ) {
      deleteParameter(parameter);
    }
  }
  const section = url.searchParams.get('section');
  if (section && !allowedSections.includes(section)) {
    deleteParameter('section');
  }
  const rawFragment = url.hash.slice(1);
  const decodedFragment = decodeClientUrlValue(rawFragment);
  if (
    rawFragment.length > clientUrlLimits.fragmentLength ||
    containsClientSecretKey(decodedFragment) ||
    containsClientSecret(decodedFragment)
  ) {
    url.hash = '';
    changed = true;
  }
  if (url.toString().length > clientUrlLimits.totalLength) {
    const parameterNames = [
      ...new Set([...url.searchParams.keys()].filter((parameter) => parameter !== 'section')),
    ].reverse();
    for (const parameter of parameterNames) {
      if (url.toString().length <= clientUrlLimits.totalLength) break;
      deleteParameter(parameter);
    }
  }
  if (url.toString().length > clientUrlLimits.totalLength) {
    for (const parameter of [...new Set(url.searchParams.keys())]) {
      deleteParameter(parameter);
    }
  }
  return { url, changed };
}

export function sanitizeClientUrl(allowedSections: readonly string[]): boolean {
  if (typeof window === 'undefined') return false;
  const urlProjection = projectSafeClientUrl(window.location.href, allowedSections);
  const historyProjection = projectSafeClientHistoryState(window.history.state);
  if (urlProjection.changed || historyProjection.changed) {
    window.history.replaceState(historyProjection.value, '', urlProjection.url);
  }
  return urlProjection.changed || historyProjection.changed;
}

/**
 * Applies a bounded set of public query parameters through the shared URL and
 * history DLP boundary. Invalid or secret-shaped values are deleted instead of
 * ever being handed to browser history.
 */
export function updateClientUrlParameters(
  updates: Readonly<Record<string, string | null>>,
  allowedSections: readonly string[],
  replace = false,
): boolean {
  if (typeof window === 'undefined') return false;
  sanitizeClientUrl(allowedSections);
  const url = new URL(window.location.href);
  for (const [key, value] of Object.entries(updates).slice(0, clientUrlLimits.parameterCount)) {
    const normalizedKey = decodeClientUrlValue(key);
    const normalizedValue = value === null ? null : decodeClientUrlValue(value);
    if (
      key.length === 0 ||
      key.length > clientUrlLimits.parameterNameLength ||
      containsClientSecretKey(normalizedKey) ||
      value === null ||
      value.length > clientUrlLimits.parameterValueLength ||
      containsClientSecret(normalizedValue ?? '')
    ) {
      url.searchParams.delete(key);
      continue;
    }
    url.searchParams.set(key, value);
  }
  const projected = projectSafeClientUrl(url, allowedSections);
  window.history[replace ? 'replaceState' : 'pushState'](
    projectSafeClientHistoryState({}).value,
    '',
    projected.url,
  );
  return !projected.changed;
}

export function installClientNavigationSecurity(allowedSections: readonly string[]): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const safeAllowedSections = [
    ...new Set(
      allowedSections.filter(
        (section) =>
          section.length > 0 &&
          section.length <= clientUrlLimits.parameterValueLength &&
          /^[a-z][a-z0-9-]*$/u.test(section) &&
          (section === 'profile' || !containsClientSecretKey(section)) &&
          !containsClientSecret(section),
      ),
    ),
  ];
  const originalPushState = window.history.pushState;
  const originalReplaceState = window.history.replaceState;
  const projectUrlArgument = (url: string | URL | null | undefined) =>
    url === null || url === undefined ? url : projectSafeClientUrl(url, safeAllowedSections).url;
  const securePushState: History['pushState'] = (data, unused, url) =>
    originalPushState.call(
      window.history,
      projectSafeClientHistoryState(data).value,
      unused,
      projectUrlArgument(url),
    );
  const secureReplaceState: History['replaceState'] = (data, unused, url) =>
    originalReplaceState.call(
      window.history,
      projectSafeClientHistoryState(data).value,
      unused,
      projectUrlArgument(url),
    );
  const sanitize = () => sanitizeClientUrl(safeAllowedSections);
  sanitize();
  window.history.pushState = securePushState;
  window.history.replaceState = secureReplaceState;
  window.addEventListener('popstate', sanitize, { capture: true });
  return () => {
    window.removeEventListener('popstate', sanitize, { capture: true });
    if (window.history.pushState === securePushState) {
      window.history.pushState = originalPushState;
    }
    if (window.history.replaceState === secureReplaceState) {
      window.history.replaceState = originalReplaceState;
    }
  };
}

/**
 * Clears and seals Window.name, which otherwise survives same-tab navigations and can carry
 * account secrets from a previous document. GEO applications do not use named browsing contexts.
 */
export function installClientWindowNameSecurity(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  try {
    window.name = '';
  } catch {
    return () => undefined;
  }
  const originalDescriptor = Object.getOwnPropertyDescriptor(window, 'name');
  if (originalDescriptor && !originalDescriptor.configurable) return () => undefined;
  const readEmptyWindowName = () => '';
  const discardWindowNameWrite = () => undefined;
  Object.defineProperty(window, 'name', {
    configurable: true,
    enumerable: originalDescriptor?.enumerable ?? true,
    get: readEmptyWindowName,
    set: discardWindowNameWrite,
  });
  return () => {
    const currentDescriptor = Object.getOwnPropertyDescriptor(window, 'name');
    if (
      currentDescriptor?.get !== readEmptyWindowName ||
      currentDescriptor.set !== discardWindowNameWrite
    ) {
      return;
    }
    if (originalDescriptor) {
      Object.defineProperty(window, 'name', originalDescriptor);
    } else {
      Reflect.deleteProperty(window, 'name');
    }
    try {
      window.name = '';
    } catch {
      // The original browser descriptor may have changed externally; never restore prior contents.
    }
  };
}

export function installClientBrowserSecurity(allowedSections: readonly string[]): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const uninstallWindowName = installClientWindowNameSecurity();
  const uninstallDiagnostics = installClientDiagnosticSecurity();
  scrubClientStorage(window.localStorage);
  scrubClientStorage(window.sessionStorage);
  const storagePrototype = window.Storage.prototype;
  const originalSetItem = storagePrototype.setItem;
  const secureSetItem: Storage['setItem'] = function (
    this: Storage,
    keyValue: string,
    itemValue: string,
  ) {
    const key = String(keyValue);
    const value = String(itemValue);
    if (this.length > clientStorageLimits.entries) {
      this.clear();
      return;
    }
    if (
      key.length === 0 ||
      key.length > clientStorageLimits.keyLength ||
      value.length > clientStorageLimits.valueLength ||
      containsClientSecretKey(key) ||
      containsClientSecret(value)
    ) {
      return;
    }
    if (this.length >= clientStorageLimits.entries && this.getItem(key) === null) return;
    originalSetItem.call(this, key, value);
  };
  storagePrototype.setItem = secureSetItem;
  const uninstallNavigation = installClientNavigationSecurity(allowedSections);
  return () => {
    uninstallDiagnostics();
    uninstallNavigation();
    if (storagePrototype.setItem === secureSetItem) {
      storagePrototype.setItem = originalSetItem;
    }
    uninstallWindowName();
  };
}

export function navigateClientSection(
  section: string,
  allowedSections: readonly string[],
): boolean {
  if (
    typeof window === 'undefined' ||
    !allowedSections.includes(section) ||
    containsClientSecret(section)
  ) {
    return false;
  }
  sanitizeClientUrl(allowedSections);
  const url = new URL(window.location.href);
  if (section === allowedSections[0]) url.searchParams.delete('section');
  else url.searchParams.set('section', section);
  window.history.pushState({}, '', url);
  window.dispatchEvent(new PopStateEvent('popstate'));
  return true;
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
export type SafeNavItem = {
  id: string;
  label: string;
  badge?: string;
  href?: string;
  disabledExternal?: true;
};

const safeNavigationText = (value: unknown, maxLength: number): string | null =>
  typeof value === 'string' &&
  value.trim() === value &&
  value.length > 0 &&
  value.length <= maxLength &&
  !/[\u0000-\u001f\u007f]/.test(value) &&
  !containsClientSecret(value)
    ? value
    : null;

const projectSafeInternalNavigationHref = (value: unknown): string | null => {
  const href = safeNavigationText(value, 300);
  if (!href || !href.startsWith('/') || href.startsWith('//')) return null;
  try {
    const parsed = new URL(href, 'https://geo-navigation.invalid');
    const searchEntries = [...parsed.searchParams.entries()];
    const hasSafeSectionQuery =
      searchEntries.length === 0 ||
      (searchEntries.length === 1 &&
        searchEntries[0]?.[0] === 'section' &&
        /^[a-z][a-z0-9-]{0,63}$/u.test(searchEntries[0][1]) &&
        !containsClientSecretKey(searchEntries[0][1]) &&
        !containsClientSecret(searchEntries[0][1]));
    return parsed.origin === 'https://geo-navigation.invalid' &&
      `${parsed.pathname}${parsed.search}` === href &&
      parsed.pathname.startsWith('/platform/') &&
      !parsed.hash &&
      hasSafeSectionQuery &&
      !containsClientSecret(decodeClientUrlValue(href))
      ? href
      : null;
  } catch {
    return null;
  }
};

export function projectSafeProductNavigation(items: readonly NavItem[]): SafeNavItem[] {
  const projected: SafeNavItem[] = [];
  const seenIds = new Set<string>();
  for (const item of items.slice(0, 32)) {
    const id =
      safeNavigationText(item.id, 64) && /^[A-Za-z][A-Za-z0-9_-]*$/.test(item.id) ? item.id : null;
    const label = safeNavigationText(item.label, 60);
    if (!id || !label || seenIds.has(id)) continue;
    const badge = item.badge === undefined ? null : safeNavigationText(item.badge, 12);
    if (item.badge !== undefined && !badge) continue;
    seenIds.add(id);
    if (item.href !== undefined) {
      const href = projectSafeInternalNavigationHref(item.href);
      projected.push({
        id,
        label,
        ...(badge ? { badge } : {}),
        ...(href ? { href } : { disabledExternal: true as const }),
      });
      continue;
    }
    projected.push({ id, label, ...(badge ? { badge } : {}) });
  }
  return projected;
}

export type Metric = {
  label: string;
  value: string;
  detail: string;
  trend?: string;
  state?: DataState;
};

const metricStatePresentation: Record<
  Exclude<DataState, 'ready'>,
  {
    label: string;
    tone: 'neutral' | 'warning' | 'danger' | 'info';
  }
> = {
  loading: { label: '正在加载', tone: 'info' },
  empty: { label: '暂无数据', tone: 'neutral' },
  'real-zero': { label: '真实 0', tone: 'info' },
  insufficient: { label: '样本不足', tone: 'warning' },
  failed: { label: '计算失败', tone: 'danger' },
  delayed: { label: '数据延迟', tone: 'warning' },
  forbidden: { label: '无权查看', tone: 'neutral' },
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
      {state === 'forbidden' ? (
        <a className="button button-secondary" href={platformLoginHref}>
          重新登录
        </a>
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

export const safePdfDocumentLimits = {
  pageCount: 500,
  canvasDimension: 4_096,
  canvasPixels: 8_388_608,
  imagePixels: 16_777_216,
} as const;

/**
 * PDF.js receives verified in-memory bytes only. These options prevent recovery from malformed data, built-in
 * resource fetches and unbounded image/canvas allocation before an application renders a projected page.
 */
export const safePdfDocumentOptions = {
  canvasMaxAreaInBytes: safePdfDocumentLimits.canvasPixels * 4,
  disableAutoFetch: true,
  disableFontFace: true,
  disableStream: true,
  enableXfa: false,
  fontExtraProperties: false,
  maxImageSize: safePdfDocumentLimits.imagePixels,
  stopAtErrors: true,
  useSystemFonts: false,
  useWorkerFetch: false,
} as const;

export type SafePdfPageViewport = {
  canvasWidth: number;
  canvasHeight: number;
};

export function projectSafePdfPageViewport({
  totalPages,
  pageNumber,
  width,
  height,
}: {
  totalPages: number;
  pageNumber: number;
  width: number;
  height: number;
}): SafePdfPageViewport | null {
  if (
    !Number.isInteger(totalPages) ||
    totalPages < 1 ||
    totalPages > safePdfDocumentLimits.pageCount ||
    !Number.isInteger(pageNumber) ||
    pageNumber < 1 ||
    pageNumber > totalPages ||
    !Number.isFinite(width) ||
    !Number.isFinite(height) ||
    width <= 0 ||
    height <= 0
  ) {
    return null;
  }
  const canvasWidth = Math.ceil(width);
  const canvasHeight = Math.ceil(height);
  if (
    canvasWidth > safePdfDocumentLimits.canvasDimension ||
    canvasHeight > safePdfDocumentLimits.canvasDimension ||
    canvasWidth * canvasHeight > safePdfDocumentLimits.canvasPixels
  ) {
    return null;
  }
  return { canvasWidth, canvasHeight };
}

export function clearSafePdfCanvas(
  canvas: Pick<HTMLCanvasElement, 'height' | 'width'> | null,
): void {
  if (!canvas) return;
  canvas.width = 0;
  canvas.height = 0;
}

export type SafeHtmlElementTag =
  | 'a'
  | 'article'
  | 'aside'
  | 'b'
  | 'blockquote'
  | 'br'
  | 'caption'
  | 'code'
  | 'dd'
  | 'div'
  | 'dl'
  | 'dt'
  | 'em'
  | 'footer'
  | 'h1'
  | 'h2'
  | 'h3'
  | 'h4'
  | 'h5'
  | 'h6'
  | 'header'
  | 'hr'
  | 'i'
  | 'li'
  | 'main'
  | 'mark'
  | 'ol'
  | 'p'
  | 'pre'
  | 's'
  | 'section'
  | 'small'
  | 'span'
  | 'strong'
  | 'sub'
  | 'sup'
  | 'table'
  | 'tbody'
  | 'td'
  | 'tfoot'
  | 'th'
  | 'thead'
  | 'tr'
  | 'u'
  | 'ul';

export type SafeHtmlNode =
  | { kind: 'text'; text: string }
  | {
      kind: 'element';
      tag: SafeHtmlElementTag;
      children: SafeHtmlNode[];
      href?: string;
      colSpan?: number;
      rowSpan?: number;
      scope?: 'col' | 'colgroup' | 'row' | 'rowgroup';
    };

export type SafeHtmlDocumentProjection = {
  title: string;
  nodes: SafeHtmlNode[];
  nodeCount: number;
};

const safeHtmlElementTags = new Set<SafeHtmlElementTag>([
  'a',
  'article',
  'aside',
  'b',
  'blockquote',
  'br',
  'caption',
  'code',
  'dd',
  'div',
  'dl',
  'dt',
  'em',
  'footer',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'header',
  'hr',
  'i',
  'li',
  'main',
  'mark',
  'ol',
  'p',
  'pre',
  's',
  'section',
  'small',
  'span',
  'strong',
  'sub',
  'sup',
  'table',
  'tbody',
  'td',
  'tfoot',
  'th',
  'thead',
  'tr',
  'u',
  'ul',
]);
const forbiddenHtmlElementTags = new Set([
  'audio',
  'base',
  'button',
  'canvas',
  'embed',
  'form',
  'iframe',
  'img',
  'input',
  'link',
  'math',
  'meta',
  'object',
  'option',
  'picture',
  'script',
  'select',
  'source',
  'style',
  'svg',
  'template',
  'textarea',
  'video',
]);
const unsafeHtmlMarkupPattern =
  /<\s*\/?\s*(?:audio|base|button|canvas|embed|form|iframe|img|input|link|math|meta|object|option|picture|script|select|source|style|svg|template|textarea|video)\b|<[^>]*\b(?:on[a-z0-9_-]+|src|srcset|srcdoc|style|formaction|action|poster|background|xlink:href|xmlns|http-equiv)\s*=/i;

const projectSafeHtmlSpan = (value: string | null): number | undefined => {
  if (value === null || !/^[1-9]\d?$/.test(value)) return undefined;
  const parsed = Number(value);
  return parsed <= 20 ? parsed : undefined;
};

const projectSafeHtmlLink = (value: string | null): string | null => {
  if (!value || value.length > 2_048 || containsClientSecret(value)) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password &&
      !containsClientSecret(url.toString())
      ? url.toString()
      : null;
  } catch {
    return null;
  }
};

/**
 * Reconstructs a static report document from an integrity-verified HTML artifact. The projection
 * never carries raw markup, active elements, event handlers, external media or secret-shaped text.
 */
export function projectSafeHtmlDocument(html: string): SafeHtmlDocumentProjection | null {
  if (
    typeof DOMParser === 'undefined' ||
    !html ||
    html.length > 2_000_000 ||
    unsafeHtmlMarkupPattern.test(html) ||
    containsClientSecret(html)
  ) {
    return null;
  }
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  const sourceTitle =
    parsed.title.trim() || parsed.body.querySelector('h1')?.textContent?.trim() || '已发布在线报告';
  if (
    !sourceTitle ||
    sourceTitle.length > 240 ||
    containsClientSecret(sourceTitle) ||
    parsed.querySelector('parsererror')
  ) {
    return null;
  }
  const budget = { nodes: 0, text: 0, invalid: false };
  const projectNode = (node: Node, depth: number): SafeHtmlNode | null => {
    if (depth > 20 || budget.nodes >= 2_000) {
      budget.invalid = true;
      return null;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent ?? '';
      if (!text) return null;
      const parentTag = node.parentElement?.tagName.toLowerCase() ?? '';
      if (
        !text.trim() &&
        ['dl', 'ol', 'table', 'tbody', 'tfoot', 'thead', 'tr', 'ul'].includes(parentTag)
      ) {
        return null;
      }
      budget.nodes += 1;
      budget.text += text.length;
      if (text.length > 20_000 || budget.text > 200_000 || containsClientSecret(text)) {
        budget.invalid = true;
        return null;
      }
      return { kind: 'text', text };
    }
    if (node.nodeType === Node.COMMENT_NODE) return null;
    if (node.nodeType !== Node.ELEMENT_NODE) {
      budget.invalid = true;
      return null;
    }
    const element = node as Element;
    const rawTag = element.tagName.toLowerCase();
    if (
      forbiddenHtmlElementTags.has(rawTag) ||
      !safeHtmlElementTags.has(rawTag as SafeHtmlElementTag) ||
      Array.from(element.attributes).some((attribute) => /^on/i.test(attribute.name))
    ) {
      budget.invalid = true;
      return null;
    }
    budget.nodes += 1;
    const tag = rawTag as SafeHtmlElementTag;
    const children: SafeHtmlNode[] = [];
    for (const child of element.childNodes) {
      const projected = projectNode(child, depth + 1);
      if (projected) children.push(projected);
      if (budget.invalid) return null;
    }
    if (!children.length && !['br', 'hr'].includes(tag)) {
      budget.invalid = true;
      return null;
    }
    const href = tag === 'a' ? projectSafeHtmlLink(element.getAttribute('href')) : undefined;
    if (tag === 'a' && element.hasAttribute('href') && !href) {
      budget.invalid = true;
      return null;
    }
    const colSpan =
      tag === 'td' || tag === 'th'
        ? projectSafeHtmlSpan(element.getAttribute('colspan'))
        : undefined;
    const rowSpan =
      tag === 'td' || tag === 'th'
        ? projectSafeHtmlSpan(element.getAttribute('rowspan'))
        : undefined;
    const rawScope = tag === 'th' ? element.getAttribute('scope') : null;
    const scope =
      rawScope && ['col', 'colgroup', 'row', 'rowgroup'].includes(rawScope)
        ? (rawScope as 'col' | 'colgroup' | 'row' | 'rowgroup')
        : undefined;
    return {
      kind: 'element',
      tag,
      children,
      ...(href ? { href } : {}),
      ...(colSpan ? { colSpan } : {}),
      ...(rowSpan ? { rowSpan } : {}),
      ...(scope ? { scope } : {}),
    };
  };
  const nodes: SafeHtmlNode[] = [];
  for (const child of parsed.body.childNodes) {
    const projected = projectNode(child, 0);
    if (projected) nodes.push(projected);
    if (budget.invalid) return null;
  }
  return nodes.length ? { title: sourceTitle, nodes, nodeCount: budget.nodes } : null;
}

const renderSafeHtmlNode = (node: SafeHtmlNode, key: string): ReactNode => {
  if (node.kind === 'text') return node.text;
  const children = node.children.map((child, index) =>
    renderSafeHtmlNode(child, `${key}-${index}`),
  );
  const cellProps = {
    ...(node.colSpan ? { colSpan: node.colSpan } : {}),
    ...(node.rowSpan ? { rowSpan: node.rowSpan } : {}),
  };
  switch (node.tag) {
    case 'a':
      return node.href ? (
        <a key={key} href={node.href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      ) : (
        <span key={key}>{children}</span>
      );
    case 'article':
    case 'section':
      return <section key={key}>{children}</section>;
    case 'aside':
      return <aside key={key}>{children}</aside>;
    case 'b':
    case 'strong':
      return <strong key={key}>{children}</strong>;
    case 'blockquote':
      return <blockquote key={key}>{children}</blockquote>;
    case 'br':
      return <br key={key} />;
    case 'caption':
      return <caption key={key}>{children}</caption>;
    case 'code':
      return <code key={key}>{children}</code>;
    case 'dd':
      return <dd key={key}>{children}</dd>;
    case 'div':
    case 'main':
      return <div key={key}>{children}</div>;
    case 'dl':
      return <dl key={key}>{children}</dl>;
    case 'dt':
      return <dt key={key}>{children}</dt>;
    case 'em':
    case 'i':
      return <em key={key}>{children}</em>;
    case 'footer':
      return <footer key={key}>{children}</footer>;
    case 'h1':
      return <h4 key={key}>{children}</h4>;
    case 'h2':
      return <h5 key={key}>{children}</h5>;
    case 'h3':
    case 'h4':
    case 'h5':
    case 'h6':
      return <h6 key={key}>{children}</h6>;
    case 'header':
      return <header key={key}>{children}</header>;
    case 'hr':
      return <hr key={key} />;
    case 'li':
      return <li key={key}>{children}</li>;
    case 'mark':
      return <mark key={key}>{children}</mark>;
    case 'ol':
      return <ol key={key}>{children}</ol>;
    case 'p':
      return <p key={key}>{children}</p>;
    case 'pre':
      return <pre key={key}>{children}</pre>;
    case 's':
      return <s key={key}>{children}</s>;
    case 'small':
      return <small key={key}>{children}</small>;
    case 'span':
      return <span key={key}>{children}</span>;
    case 'sub':
      return <sub key={key}>{children}</sub>;
    case 'sup':
      return <sup key={key}>{children}</sup>;
    case 'table':
      return (
        <div
          className="safe-html-table-scroll"
          role="region"
          aria-label="在线报告表格"
          tabIndex={0}
          key={key}
        >
          <table>{children}</table>
        </div>
      );
    case 'tbody':
      return <tbody key={key}>{children}</tbody>;
    case 'td':
      return (
        <td key={key} {...cellProps}>
          {children}
        </td>
      );
    case 'tfoot':
      return <tfoot key={key}>{children}</tfoot>;
    case 'th':
      return (
        <th key={key} {...cellProps} {...(node.scope ? { scope: node.scope } : {})}>
          {children}
        </th>
      );
    case 'thead':
      return <thead key={key}>{children}</thead>;
    case 'tr':
      return <tr key={key}>{children}</tr>;
    case 'u':
      return <u key={key}>{children}</u>;
    case 'ul':
      return <ul key={key}>{children}</ul>;
  }
};

export function SafeHtmlDocument({
  projection,
  label,
}: {
  projection: SafeHtmlDocumentProjection;
  label: string;
}) {
  return (
    <article className="safe-html-document" aria-label={label}>
      <div className="safe-html-document-head">
        <Badge tone="positive">HTML 完整性与活动内容已校验</Badge>
        <h3>{projection.title}</h3>
      </div>
      <div className="safe-html-document-body">
        {projection.nodes.map((node, index) => renderSafeHtmlNode(node, `safe-html-${index}`))}
      </div>
    </article>
  );
}

const isSafeClientDownloadFileName = (fileName: string): boolean =>
  fileName.length > 0 &&
  fileName.length <= 240 &&
  fileName.normalize('NFC') === fileName &&
  fileName.trim() === fileName &&
  !fileName.startsWith('.') &&
  !/[<>:"/\\|?*\x00-\x1f\x7f]/u.test(fileName) &&
  !containsUnsafeClientControlCharacter(fileName) &&
  !fileName.includes('..') &&
  !containsClientSecret(fileName);

const safeGeneratedFileMaxBytes = 2 * 1024 * 1024;

const isSafeGeneratedJsonValue = (
  value: unknown,
  budget: { nodes: number; characters: number },
  depth = 0,
): boolean => {
  budget.nodes += 1;
  if (depth > 12 || budget.nodes > 10_000 || budget.characters > safeGeneratedFileMaxBytes) {
    return false;
  }
  if (typeof value === 'string') {
    budget.characters += value.length;
    return (
      value.length <= 10_000 &&
      budget.characters <= safeGeneratedFileMaxBytes &&
      !containsClientSecret(value)
    );
  }
  if (typeof value === 'number')
    return Number.isFinite(value) && !containsNumericClientSecret(value);
  if (typeof value === 'boolean' || value === null) return true;
  if (Array.isArray(value))
    return (
      value.length <= 5_000 &&
      value.every((item) => isSafeGeneratedJsonValue(item, budget, depth + 1))
    );
  if (typeof value !== 'object') return false;
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return false;
  const entries = Object.entries(value as Record<string, unknown>);
  return (
    entries.length <= 500 &&
    entries.every(([key, item]) => {
      budget.characters += key.length;
      return (
        key.length > 0 &&
        key.length <= 120 &&
        budget.characters <= safeGeneratedFileMaxBytes &&
        !containsClientSecretKey(key) &&
        !containsClientSecret(key) &&
        isSafeGeneratedJsonValue(item, budget, depth + 1)
      );
    })
  );
};

const isSafeGeneratedCsv = (content: string): boolean => {
  if (content.length > safeGeneratedFileMaxBytes) return false;
  const lines = content.split(/\r?\n/);
  if (lines.length > 10_000 || lines.some((line) => line.length > 20_000)) return false;
  const headers = lines[0]?.split(',') ?? [];
  if (
    headers.length === 0 ||
    headers.length > 100 ||
    headers.some(
      (header) =>
        header.trim().length === 0 ||
        header.length > 120 ||
        containsClientSecretKey(header) ||
        containsClientSecret(header),
    )
  ) {
    return false;
  }
  return !/(?:^|[\r\n,])[ \t]*"?[=+\-@]/.test(content);
};

export type SafeGeneratedFileDownload =
  | { kind: 'json'; fileName: string; value: unknown }
  | { kind: 'csv'; fileName: string; content: string };

/**
 * Downloads a browser-generated JSON/CSV file only after filename, shape, DLP, formula and byte-size checks.
 * Server artifacts must use VerifiedBlobDownload instead.
 */
export function downloadSafeGeneratedFile(input: SafeGeneratedFileDownload): boolean {
  if (
    !isSafeClientDownloadFileName(input.fileName) ||
    (input.kind === 'json' && !input.fileName.endsWith('.json')) ||
    (input.kind === 'csv' && !input.fileName.endsWith('.csv'))
  ) {
    return false;
  }
  let content: string;
  let mimeType: 'application/json;charset=utf-8' | 'text/csv;charset=utf-8';
  if (input.kind === 'json') {
    if (!isSafeGeneratedJsonValue(input.value, { nodes: 0, characters: 0 })) return false;
    try {
      content = JSON.stringify(input.value, null, 2);
    } catch {
      return false;
    }
    mimeType = 'application/json;charset=utf-8';
  } else {
    content = input.content;
    mimeType = 'text/csv;charset=utf-8';
    if (!isSafeGeneratedCsv(content)) return false;
  }
  if (!content || containsClientSecret(content)) return false;
  const blob = new Blob([content], { type: mimeType });
  if (blob.size <= 0 || blob.size > safeGeneratedFileMaxBytes) return false;
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = input.fileName;
  anchor.rel = 'noopener';
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
  return true;
}

export type VerifiedBlobDownloadResult =
  | { kind: 'ready'; blob: Blob }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

export function VerifiedBlobImage({
  load,
  resourceKey,
  alt,
  className,
}: {
  load: () => Promise<VerifiedBlobDownloadResult>;
  resourceKey: string;
  alt: string;
  className?: string;
}) {
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    let cancelled = false;
    let currentObjectUrl: string | null = null;
    if (
      !resourceKey ||
      resourceKey.length > 1_000 ||
      !alt ||
      alt.length > 500 ||
      containsUnsafeClientControlCharacter(`${resourceKey}${alt}`) ||
      containsClientSecret(`${resourceKey} ${alt}`)
    ) {
      setState('failed');
      return;
    }
    setState('loading');
    setObjectUrl(null);
    void loadRef
      .current()
      .then((result) => {
        if (cancelled) return;
        if (
          result.kind !== 'ready' ||
          !(result.blob instanceof Blob) ||
          result.blob.size <= 0 ||
          result.blob.size > 30 * 1024 * 1024 ||
          !result.blob.type.startsWith('image/') ||
          containsClientSecret(result.blob.type)
        ) {
          setState('failed');
          return;
        }
        currentObjectUrl = URL.createObjectURL(result.blob);
        setObjectUrl(currentObjectUrl);
        setState('ready');
      })
      .catch(() => {
        if (!cancelled) setState('failed');
      });
    return () => {
      cancelled = true;
      if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
    };
  }, [alt, resourceKey]);
  if (state === 'loading') return <StatePanel state="loading" />;
  if (state === 'failed' || !objectUrl) return <StatePanel state="failed" />;
  return <img className={className} src={objectUrl} alt={alt} />;
}

export function VerifiedBlobDownload({
  load,
  fileName,
  resourceKey = fileName,
  label = '校验后下载',
  loadingLabel = '校验中…',
  failureLabel = '制品完整性校验失败',
  successLabel = '制品完整性校验通过并已下载',
}: {
  load: () => Promise<VerifiedBlobDownloadResult>;
  fileName: string;
  resourceKey?: string;
  label?: string;
  loadingLabel?: string;
  failureLabel?: string;
  successLabel?: string;
}) {
  const [state, setState] = useState<'idle' | 'loading' | 'failed' | 'ready'>('idle');
  const generation = useRef(0);
  const active = useRef(false);
  const requestScope = createStructuredClientScopeKey([resourceKey, fileName]);
  const requestScopeRef = useRef(requestScope);
  if (requestScopeRef.current !== requestScope) {
    requestScopeRef.current = requestScope;
    generation.current += 1;
    active.current = false;
  }
  useEffect(() => {
    setState('idle');
  }, [requestScope]);
  useEffect(
    () => () => {
      generation.current += 1;
      active.current = false;
    },
    [],
  );
  const download = async () => {
    if (active.current) return;
    const requestGeneration = ++generation.current;
    const safeFileName = isSafeClientDownloadFileName(fileName) ? fileName : null;
    if (
      !safeFileName ||
      resourceKey.length === 0 ||
      resourceKey.length > 1_000 ||
      containsUnsafeClientControlCharacter(resourceKey) ||
      containsClientSecret(resourceKey)
    ) {
      setState('failed');
      return;
    }
    active.current = true;
    setState('loading');
    try {
      const result = await load();
      if (generation.current !== requestGeneration) return;
      if (
        result.kind !== 'ready' ||
        !(result.blob instanceof Blob) ||
        result.blob.size <= 0 ||
        result.blob.size > 50 * 1024 * 1024 ||
        !result.blob.type ||
        containsClientSecret(result.blob.type)
      ) {
        setState('failed');
        return;
      }
      const objectUrl = URL.createObjectURL(result.blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = safeFileName;
      anchor.rel = 'noopener';
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
      setState('ready');
    } catch {
      if (generation.current === requestGeneration) setState('failed');
    } finally {
      if (generation.current === requestGeneration) active.current = false;
    }
  };
  return (
    <span className="verified-blob-download">
      <button
        type="button"
        className="button button-secondary"
        disabled={state === 'loading'}
        onClick={() => void download()}
      >
        {state === 'loading' ? loadingLabel : label}
      </button>
      {state === 'failed' ? (
        <span className="field-error" role="alert">
          {failureLabel}
        </span>
      ) : null}
      {state === 'ready' ? (
        <span className="sr-only" role="status">
          {successLabel}
        </span>
      ) : null}
    </span>
  );
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

export type ProjectionLimitNoticeItem = {
  key: string;
  label: string;
  total: number;
  shown: number;
};

export function ProjectionLimitNotice({
  items,
  detail = '完整集合需通过服务端分页或受控导出查看；当前视图不会静默声称数据完整。',
}: {
  items: ProjectionLimitNoticeItem[];
  detail?: string;
}) {
  if (!items.length) return null;
  return (
    <div className="confirmation projection-limit-notice" role="status">
      <Badge tone="warning">受控展示上限</Badge>
      <ul>
        {items.map(({ key, label, total, shown }) => (
          <li key={key}>
            {label}：服务返回 {total} 条，浏览器安全视图展示 {shown} 条
          </li>
        ))}
      </ul>
      <span>{detail}</span>
    </div>
  );
}

export function MetricGrid({ metrics }: { metrics: Metric[] }) {
  return (
    <div className="metric-grid">
      {metrics.map((metric) => {
        const state =
          metric.state && metric.state !== 'ready'
            ? metricStatePresentation[metric.state]
            : undefined;
        return (
          <article className="metric-card" key={metric.label}>
            <div className="metric-label">{metric.label}</div>
            <div className="metric-value">{metric.value}</div>
            <div className="metric-foot">
              <span>{metric.detail}</span>
              {metric.trend || state ? (
                <span className="metric-badges">
                  {metric.trend ? <Badge tone="positive">{metric.trend}</Badge> : null}
                  {state ? <Badge tone={state.tone}>{state.label}</Badge> : null}
                </span>
              ) : null}
            </div>
          </article>
        );
      })}
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
  interventionStatus?:
    | 'none'
    | 'waiting'
    | 'paired'
    | 'refused'
    | 'timed_out'
    | 'failed'
    | 'completed';
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
  'failed',
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
    failed: '验证失败',
    completed: '验证已完成',
  };
  const tone =
    value === 'completed' || value === 'none'
      ? 'positive'
      : value === 'refused' || value === 'timed_out' || value === 'failed'
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

/**
 * Best-effort platform sign-out: revokes the native session server-side, clears browser identity
 * hints (never UI preferences or other local state) and lands on the shared login entry. Local
 * cleanup and navigation always run, even when the revocation request itself fails. The API
 * client is imported lazily so Node-side tooling that consumes this package's source directly
 * never evaluates the browser API boundary.
 */
export async function logoutPlatformSession(
  revoke: () => Promise<unknown> = async () => {
    const { logoutIdentitySession } = await import('@geo/api-client');
    return logoutIdentitySession();
  },
  navigate: (href: string) => void = (href) => window.location.assign(href),
): Promise<void> {
  try {
    await revoke();
  } catch {
    // Revocation is best-effort; local cleanup and navigation must still run.
  }
  try {
    for (const key of identitySessionHintStorageKeys) window.localStorage.removeItem(key);
  } catch {
    // Storage can be unavailable; the navigation below still tears the document down.
  }
  navigate(platformLoginHref);
}

export function ProductShell({
  product,
  title,
  description,
  nav,
  currentNavId,
  children,
  probe,
}: {
  product: string;
  title: string;
  description: string;
  nav: NavItem[];
  currentNavId?: string;
  children: (active: string) => ReactNode;
  probe: () => Promise<{ status: string }>;
}) {
  const safeNav = useMemo(() => projectSafeProductNavigation(nav), [nav]);
  const sectionNav = useMemo(
    () => safeNav.filter((item) => !item.href && !item.disabledExternal),
    [safeNav],
  );
  const navIds = useMemo(() => sectionNav.map((item) => item.id), [sectionNav]);
  const [active, setActive] = useUrlParam('section', sectionNav[0]?.id ?? '', navIds);
  const [status, setStatus] = useState('checking');
  const [contextOpen, setContextOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [logoutPending, setLogoutPending] = useState(false);
  const experience = useOptionalExperienceContext();
  const navId = useId();
  const mainRef = useRef<HTMLElement>(null);
  useEffect(() => {
    let active = true;
    setStatus('checking');
    void probe()
      .then((value) => {
        if (active) setStatus(value.status === 'ok' ? 'ok' : 'unavailable');
      })
      .catch(() => {
        if (active) setStatus('unavailable');
      });
    return () => {
      active = false;
    };
  }, [probe]);
  useEffect(() => {
    const sanitize = () => sanitizeClientUrl(navIds);
    sanitize();
    window.addEventListener('popstate', sanitize, { capture: true });
    return () => window.removeEventListener('popstate', sanitize, { capture: true });
  }, [navIds]);
  useEffect(() => {
    const main = mainRef.current;
    if (!main) return;
    const markScrollableRegions = () => {
      main.querySelectorAll<HTMLElement>('.panel').forEach((panel) => {
        const hasDirectTable =
          panel.querySelector('.data-table') !== null &&
          panel.querySelector('.table-scroll, .geo-chart') === null;
        panel.classList.toggle('direct-table-scroll', hasDirectTable);
        if (hasDirectTable) {
          panel.tabIndex = 0;
          panel.setAttribute('aria-label', '可横向滚动的数据区域');
        } else if (panel.getAttribute('aria-label') === '可横向滚动的数据区域') {
          panel.removeAttribute('aria-label');
          panel.removeAttribute('tabindex');
        }
      });
      main.querySelectorAll<HTMLElement>('.table-scroll').forEach((region) => {
        region.tabIndex = 0;
        if (!region.getAttribute('aria-label'))
          region.setAttribute('aria-label', '可横向滚动的数据区域');
      });
    };
    markScrollableRegions();
    const observer = new MutationObserver(markScrollableRegions);
    observer.observe(main, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [active]);
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
            {safeNav.map((item) =>
              item.href ? (
                <a
                  aria-label={item.label}
                  aria-current={currentNavId === item.id ? 'page' : undefined}
                  className={currentNavId === item.id ? 'nav-active' : undefined}
                  href={item.href}
                  key={item.id}
                >
                  <span>{item.label}</span>
                  {item.badge ? <em>{item.badge}</em> : null}
                </a>
              ) : item.disabledExternal ? (
                <button
                  aria-label={item.label}
                  disabled
                  key={item.id}
                  title="导航地址未通过安全校验"
                >
                  <span>{item.label}</span>
                </button>
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
          <div className="sidebar-foot" role="status" aria-live="polite">
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
              <button
                type="button"
                className="session-logout"
                disabled={logoutPending}
                onClick={() => {
                  if (logoutPending) return;
                  setLogoutPending(true);
                  void logoutPlatformSession();
                }}
              >
                {logoutPending ? '正在退出…' : '退出登录'}
              </button>
            </div>
          </header>
          <main id="main-content" ref={mainRef} tabIndex={-1}>
            <div className="page-heading">
              <div>
                <span className="overline">{product}</span>
                <h1>{title}</h1>
                <p>{description}</p>
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
          {experience?.source === 'live' ? (
            <>
              <StatePanel state="insufficient" />
              <p className="security-note">
                当前安全投影未提供通知集合；不会推断数据窗口、账号、待人工任务或其数量。
              </p>
            </>
          ) : (
            <ol className="timeline">
              <li>
                <strong>数据窗口已冻结</strong>
                <span>当前项目 · 今天 10:20</span>
              </li>
              <li>
                <strong>有一项待人工确认</strong>
                <span>Contract fixture · 只显示安全摘要</span>
              </li>
            </ol>
          )}
        </Dialog>
      ) : null}
    </>
  );
}

/** @deprecated Use ProductShell for product applications. */
export const AppShell = ProductShell;
