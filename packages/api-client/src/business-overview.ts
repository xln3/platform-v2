import { allowsFixtureIdentityHeaders, type BrowserBuildIdentityEnv } from './index';

export const businessOverviewProjectStates = ['draft', 'active', 'paused', 'archived'] as const;
export type BusinessOverviewProjectState = (typeof businessOverviewProjectStates)[number];

export const businessOverviewAttentionCodes = [
  'collection_failed_or_delayed',
  'formal_production_failed',
  'formal_review_required',
  'delivery_confirmation_required',
  'setup_records_missing',
  'intake_truth_confirmation_required',
  'service_entitlement_unrecorded',
  'no_current_attention',
] as const;
export type BusinessOverviewAttentionCode = (typeof businessOverviewAttentionCodes)[number];

export type OperationsBusinessOverviewQuery = {
  cursor?: string;
  limit?: number;
  q?: string;
  projectState?: BusinessOverviewProjectState;
  attention?: BusinessOverviewAttentionCode;
};

export type OperationsIdentityHeaders = {
  'X-Tenant-Id': string;
  'X-Actor-Id': string;
  'X-Actor-Role': string;
};

export type OperationsBusinessOverview = {
  schemaVersion: 1;
  asOf: string;
  summary: {
    scope: 'filtered';
    tenantProjectCount: number;
    projectCount: number;
    projectStateCounts: Record<BusinessOverviewProjectState, number>;
    setupReadyProjectCount: number;
    projectWithEntitlementRecordCount: number;
    activeEntitlementCount: number;
    attentionProjectCount: number;
  };
  commercialCapabilities: {
    quotationHistory: 'unsupported';
    signedContractLedger: 'unsupported';
    invoiceReceivablePaymentLedger: 'unsupported';
  };
  items: OperationsBusinessOverviewItem[];
  page: {
    limit: number;
    nextCursor: string | null;
    hasMore: boolean;
    filteredTotal: number;
  };
};

export type OperationsBusinessOverviewItem = {
  project: {
    id: string;
    name: string;
    state: BusinessOverviewProjectState;
  };
  customer: {
    id: string;
    name: string;
  };
  setup: {
    clientProfileRevision: number | null;
    assetConfirmationRevision: number | null;
    frozenMonitoringConfigRevision: number | null;
    setupReady: boolean;
    intakeProfileExists: boolean;
    intakeTruthConfirmed: boolean | null;
  };
  serviceEntitlements: {
    serviceCode: BusinessOverviewServiceCode;
    serviceName: string;
    state: 'inactive' | 'active' | 'suspended' | 'expired';
    authorizedFrom: string | null;
    authorizedUntil: string | null;
    effectiveNow: boolean;
  }[];
  collection: {
    activeCount: number;
    failedCount: number;
    delayedCount: number;
    latestState: CollectionRunState | null;
    latestAt: string | null;
  };
  formalReport: {
    productionCount: number;
    latestState: FormalProductionState | null;
    latestAt: string | null;
  };
  delivery: {
    deliveredAt: string | null;
    confirmedAt: string | null;
    pendingConfirmationCount: number;
  };
  contractDraftExport: null;
  primaryAttention: {
    code: BusinessOverviewAttentionCode;
    severity: 'danger' | 'warning' | 'neutral';
    additionalCount: number;
  };
  lastBusinessFactAt: string;
};

export type OperationsBusinessOverviewResult =
  | { kind: 'ready'; data: OperationsBusinessOverview }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

type BusinessOverviewServiceCode =
  | 'ranking_test'
  | 'outbound_disparagement_audit'
  | 'inbound_disparagement_audit'
  | 'official_site_audit'
  | 'content_publishing_pilot';

type CollectionRunState =
  | 'pending'
  | 'starting'
  | 'running'
  | 'pausing'
  | 'paused'
  | 'resuming'
  | 'cancelling'
  | 'completed'
  | 'completed_with_failures'
  | 'failed'
  | 'cancelled'
  | 'skipped';

type FormalProductionState = 'queued' | 'running' | 'failed' | 'awaiting_review' | 'signed';

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export type OperationsBusinessOverviewRequestOptions = {
  baseUrl?: string;
  fetcher?: Fetcher;
  signal?: AbortSignal;
};

const configuredApiBase =
  (import.meta as ImportMeta & { env?: { VITE_GEO_API_BASE?: string } }).env?.VITE_GEO_API_BASE ??
  '';
const maximumResponseBytes = 1_000_000;
const maximumCount = 1_000_000_000;
const publicIdPattern = /^[a-z]{3}_[A-Za-z0-9_-]{1,116}$/;
const projectIdPattern = /^prj_[A-Za-z0-9_-]{1,116}$/;
const customerIdPattern = /^cst_[A-Za-z0-9_-]{1,116}$/;
const cursorPattern = /^[A-Za-z0-9_-]{1,512}$/;
const controlCharacterPattern = /[\u0000-\u001f\u007f]/u;
const secretValuePattern =
  /(?:bearer\s+|session\s*=|cookie\s*[:=]|password\s*[:=]|api[_-]?key\s*[:=]|otp\s*[:=]|proxy-password)/iu;
const strictTimestampPattern =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-](\d{2}):(\d{2}))$/u;
const serviceNames: Readonly<Record<BusinessOverviewServiceCode, string>> = {
  ranking_test: 'AI 推荐排名效果测试',
  outbound_disparagement_audit: '主动拉踩内容核查',
  inbound_disparagement_audit: '被拉踩内容核查',
  official_site_audit: '官网内容 AI 引用效率分析',
  content_publishing_pilot: '内容发布与排名提升试点',
};
const collectionRunStates = [
  'pending',
  'starting',
  'running',
  'pausing',
  'paused',
  'resuming',
  'cancelling',
  'completed',
  'completed_with_failures',
  'failed',
  'cancelled',
  'skipped',
] as const;
const formalProductionStates = [
  'queued',
  'running',
  'failed',
  'awaiting_review',
  'signed',
] as const;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const hasExactKeys = (value: Record<string, unknown>, keys: readonly string[]): boolean => {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
};

const safeText = (value: unknown, maximum: number): string | null =>
  typeof value === 'string' &&
  value.length > 0 &&
  value.length <= maximum &&
  value.trim() === value &&
  !controlCharacterPattern.test(value) &&
  !secretValuePattern.test(value)
    ? value
    : null;

const safeCount = (value: unknown, maximum = maximumCount): number | null =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= maximum
    ? value
    : null;

const safePositiveRevision = (value: unknown): number | null | undefined =>
  value === null
    ? null
    : safeCount(value, maximumCount) && Number(value) >= 1
      ? Number(value)
      : undefined;

const safeEnum = <const Values extends readonly string[]>(
  value: unknown,
  values: Values,
): Values[number] | null =>
  typeof value === 'string' && values.includes(value as Values[number])
    ? (value as Values[number])
    : null;

const safeTimestamp = (value: unknown): string | null => {
  if (typeof value !== 'string' || value.length > 80) return null;
  const match = strictTimestampPattern.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const numericYear = Number(year);
  const numericMonth = Number(month);
  const numericDay = Number(day);
  const numericHour = Number(hour);
  const numericMinute = Number(minute);
  const numericSecond = Number(second);
  const values = [numericYear, numericMonth, numericDay, numericHour, numericMinute, numericSecond];
  if (
    values.some((item) => !Number.isInteger(item)) ||
    numericMonth < 1 ||
    numericMonth > 12 ||
    numericDay < 1 ||
    numericHour > 23 ||
    numericMinute > 59 ||
    numericSecond > 59 ||
    (offsetHour !== undefined && Number(offsetHour) > 23) ||
    (offsetMinute !== undefined && Number(offsetMinute) > 59)
  ) {
    return null;
  }
  const calendarProbe = new Date(Date.UTC(numericYear, numericMonth - 1, numericDay));
  if (
    calendarProbe.getUTCFullYear() !== numericYear ||
    calendarProbe.getUTCMonth() !== numericMonth - 1 ||
    calendarProbe.getUTCDate() !== numericDay ||
    !Number.isFinite(Date.parse(value))
  ) {
    return null;
  }
  return value;
};

const optionalTimestamp = (value: unknown): string | null | undefined =>
  value === null ? null : (safeTimestamp(value) ?? undefined);

const optionalBoolean = (value: unknown): boolean | null | undefined =>
  value === null ? null : typeof value === 'boolean' ? value : undefined;

function projectSetup(value: unknown): OperationsBusinessOverviewItem['setup'] | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'client_profile_revision',
      'asset_confirmation_revision',
      'frozen_monitoring_config_revision',
      'setup_ready',
      'intake_profile_exists',
      'intake_truth_confirmed',
    ])
  ) {
    return null;
  }
  const clientProfileRevision = safePositiveRevision(value.client_profile_revision);
  const assetConfirmationRevision = safePositiveRevision(value.asset_confirmation_revision);
  const frozenMonitoringConfigRevision = safePositiveRevision(
    value.frozen_monitoring_config_revision,
  );
  const intakeTruthConfirmed = optionalBoolean(value.intake_truth_confirmed);
  if (
    clientProfileRevision === undefined ||
    assetConfirmationRevision === undefined ||
    frozenMonitoringConfigRevision === undefined ||
    typeof value.setup_ready !== 'boolean' ||
    typeof value.intake_profile_exists !== 'boolean' ||
    intakeTruthConfirmed === undefined ||
    value.setup_ready !==
      (clientProfileRevision !== null &&
        assetConfirmationRevision !== null &&
        frozenMonitoringConfigRevision !== null) ||
    (!value.intake_profile_exists && intakeTruthConfirmed !== null)
  ) {
    return null;
  }
  return {
    clientProfileRevision,
    assetConfirmationRevision,
    frozenMonitoringConfigRevision,
    setupReady: value.setup_ready,
    intakeProfileExists: value.intake_profile_exists,
    intakeTruthConfirmed,
  };
}

function projectEntitlements(
  value: unknown,
  asOf: string,
): OperationsBusinessOverviewItem['serviceEntitlements'] | null {
  if (!Array.isArray(value) || value.length > 5) return null;
  const seen = new Set<string>();
  const projected: OperationsBusinessOverviewItem['serviceEntitlements'] = [];
  for (const item of value) {
    if (
      !isRecord(item) ||
      !hasExactKeys(item, [
        'service_code',
        'service_name',
        'state',
        'authorized_from',
        'authorized_until',
        'effective_now',
      ])
    ) {
      return null;
    }
    const serviceCode = safeEnum(
      item.service_code,
      Object.keys(serviceNames) as BusinessOverviewServiceCode[],
    );
    const serviceName = safeText(item.service_name, 120);
    const state = safeEnum(item.state, ['inactive', 'active', 'suspended', 'expired'] as const);
    const authorizedFrom = optionalTimestamp(item.authorized_from);
    const authorizedUntil = optionalTimestamp(item.authorized_until);
    if (
      !serviceCode ||
      serviceName !== serviceNames[serviceCode] ||
      !state ||
      authorizedFrom === undefined ||
      authorizedUntil === undefined ||
      typeof item.effective_now !== 'boolean' ||
      seen.has(serviceCode) ||
      (authorizedFrom &&
        authorizedUntil &&
        Date.parse(authorizedUntil) <= Date.parse(authorizedFrom))
    ) {
      return null;
    }
    const effectiveNow =
      state === 'active' &&
      (!authorizedFrom || Date.parse(authorizedFrom) <= Date.parse(asOf)) &&
      (!authorizedUntil || Date.parse(authorizedUntil) > Date.parse(asOf));
    if (effectiveNow !== item.effective_now) return null;
    seen.add(serviceCode);
    projected.push({
      serviceCode,
      serviceName,
      state,
      authorizedFrom,
      authorizedUntil,
      effectiveNow,
    });
  }
  return projected;
}

function projectItem(value: unknown, asOf: string): OperationsBusinessOverviewItem | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'project',
      'customer',
      'setup',
      'service_entitlements',
      'collection',
      'formal_report',
      'delivery',
      'contract_draft_export',
      'primary_attention',
      'last_business_fact_at',
    ]) ||
    !isRecord(value.project) ||
    !hasExactKeys(value.project, ['id', 'name', 'state']) ||
    !isRecord(value.customer) ||
    !hasExactKeys(value.customer, ['id', 'name']) ||
    !isRecord(value.collection) ||
    !hasExactKeys(value.collection, [
      'active_count',
      'failed_count',
      'delayed_count',
      'latest_state',
      'latest_at',
    ]) ||
    !isRecord(value.formal_report) ||
    !hasExactKeys(value.formal_report, ['production_count', 'latest_state', 'latest_at']) ||
    !isRecord(value.delivery) ||
    !hasExactKeys(value.delivery, ['delivered_at', 'confirmed_at', 'pending_confirmation_count']) ||
    !isRecord(value.primary_attention) ||
    !hasExactKeys(value.primary_attention, ['code', 'severity', 'additional_count']) ||
    value.contract_draft_export !== null
  ) {
    return null;
  }
  const projectId = safeText(value.project.id, 120);
  const projectName = safeText(value.project.name, 200);
  const projectState = safeEnum(value.project.state, businessOverviewProjectStates);
  const customerId = safeText(value.customer.id, 120);
  const customerName = safeText(value.customer.name, 200);
  const setup = projectSetup(value.setup);
  const entitlements = projectEntitlements(value.service_entitlements, asOf);
  const collectionCounts = [
    safeCount(value.collection.active_count),
    safeCount(value.collection.failed_count),
    safeCount(value.collection.delayed_count),
  ];
  const collectionState =
    value.collection.latest_state === null
      ? null
      : safeEnum(value.collection.latest_state, collectionRunStates);
  const collectionAt = optionalTimestamp(value.collection.latest_at);
  const productionCount = safeCount(value.formal_report.production_count);
  const formalState =
    value.formal_report.latest_state === null
      ? null
      : safeEnum(value.formal_report.latest_state, formalProductionStates);
  const formalAt = optionalTimestamp(value.formal_report.latest_at);
  const deliveredAt = optionalTimestamp(value.delivery.delivered_at);
  const confirmedAt = optionalTimestamp(value.delivery.confirmed_at);
  const pendingConfirmationCount = safeCount(value.delivery.pending_confirmation_count);
  const attentionCode = safeEnum(value.primary_attention.code, businessOverviewAttentionCodes);
  const attentionSeverity = safeEnum(value.primary_attention.severity, [
    'danger',
    'warning',
    'neutral',
  ] as const);
  const additionalCount = safeCount(value.primary_attention.additional_count, 6);
  const lastBusinessFactAt = safeTimestamp(value.last_business_fact_at);
  const expectedSeverity =
    attentionCode === 'collection_failed_or_delayed' || attentionCode === 'formal_production_failed'
      ? 'danger'
      : attentionCode === 'no_current_attention'
        ? 'neutral'
        : 'warning';
  if (
    !projectId ||
    !projectIdPattern.test(projectId) ||
    !projectName ||
    !projectState ||
    !customerId ||
    !customerIdPattern.test(customerId) ||
    !customerName ||
    !setup ||
    !entitlements ||
    collectionCounts.some((item) => item === null) ||
    collectionAt === undefined ||
    (collectionState === null) !== (collectionAt === null) ||
    productionCount === null ||
    formalAt === undefined ||
    (formalState === null) !== (formalAt === null) ||
    deliveredAt === undefined ||
    confirmedAt === undefined ||
    pendingConfirmationCount === null ||
    (confirmedAt !== null && deliveredAt === null) ||
    (confirmedAt && deliveredAt && Date.parse(confirmedAt) < Date.parse(deliveredAt)) ||
    !attentionCode ||
    attentionSeverity !== expectedSeverity ||
    additionalCount === null ||
    !lastBusinessFactAt ||
    !publicIdPattern.test(projectId) ||
    !publicIdPattern.test(customerId)
  ) {
    return null;
  }
  return {
    project: { id: projectId, name: projectName, state: projectState },
    customer: { id: customerId, name: customerName },
    setup,
    serviceEntitlements: entitlements,
    collection: {
      activeCount: collectionCounts[0]!,
      failedCount: collectionCounts[1]!,
      delayedCount: collectionCounts[2]!,
      latestState: collectionState,
      latestAt: collectionAt,
    },
    formalReport: {
      productionCount,
      latestState: formalState,
      latestAt: formalAt,
    },
    delivery: { deliveredAt, confirmedAt, pendingConfirmationCount },
    contractDraftExport: null,
    primaryAttention: {
      code: attentionCode,
      severity: attentionSeverity,
      additionalCount,
    },
    lastBusinessFactAt,
  };
}

export function projectOperationsBusinessOverview(
  value: unknown,
): OperationsBusinessOverview | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'schema_version',
      'as_of',
      'summary',
      'commercial_capabilities',
      'items',
      'page',
    ]) ||
    value.schema_version !== 1 ||
    !isRecord(value.summary) ||
    !hasExactKeys(value.summary, [
      'scope',
      'tenant_project_count',
      'project_count',
      'project_state_counts',
      'setup_ready_project_count',
      'project_with_entitlement_record_count',
      'active_entitlement_count',
      'attention_project_count',
    ]) ||
    !isRecord(value.summary.project_state_counts) ||
    !hasExactKeys(value.summary.project_state_counts, businessOverviewProjectStates) ||
    !isRecord(value.commercial_capabilities) ||
    !hasExactKeys(value.commercial_capabilities, [
      'quotation_history',
      'signed_contract_ledger',
      'invoice_receivable_payment_ledger',
    ]) ||
    !Array.isArray(value.items) ||
    !isRecord(value.page) ||
    !hasExactKeys(value.page, ['limit', 'next_cursor', 'has_more', 'filtered_total'])
  ) {
    return null;
  }
  const summary = value.summary;
  const projectStateCounts = summary.project_state_counts as Record<string, unknown>;
  const asOf = safeTimestamp(value.as_of);
  const tenantProjectCount = safeCount(summary.tenant_project_count);
  const projectCount = safeCount(summary.project_count);
  const stateCounts = businessOverviewProjectStates.map((state) =>
    safeCount(projectStateCounts[state]),
  );
  const stateCountTotal = stateCounts.reduce<number>((total, item) => total + (item ?? 0), 0);
  const setupReadyProjectCount = safeCount(summary.setup_ready_project_count);
  const projectWithEntitlementRecordCount = safeCount(
    summary.project_with_entitlement_record_count,
  );
  const activeEntitlementCount = safeCount(summary.active_entitlement_count);
  const attentionProjectCount = safeCount(summary.attention_project_count);
  const limit = safeCount(value.page.limit, 20);
  const filteredTotal = safeCount(value.page.filtered_total);
  const nextCursor = value.page.next_cursor === null ? null : safeText(value.page.next_cursor, 512);
  if (
    !asOf ||
    summary.scope !== 'filtered' ||
    tenantProjectCount === null ||
    projectCount === null ||
    stateCounts.some((item) => item === null) ||
    stateCountTotal !== projectCount ||
    setupReadyProjectCount === null ||
    projectWithEntitlementRecordCount === null ||
    activeEntitlementCount === null ||
    attentionProjectCount === null ||
    projectCount > tenantProjectCount ||
    setupReadyProjectCount > projectCount ||
    projectWithEntitlementRecordCount > projectCount ||
    attentionProjectCount > projectCount ||
    activeEntitlementCount > projectCount * 5 ||
    value.commercial_capabilities.quotation_history !== 'unsupported' ||
    value.commercial_capabilities.signed_contract_ledger !== 'unsupported' ||
    value.commercial_capabilities.invoice_receivable_payment_ledger !== 'unsupported' ||
    limit === null ||
    limit < 1 ||
    filteredTotal !== projectCount ||
    typeof value.page.has_more !== 'boolean' ||
    (nextCursor !== null && !cursorPattern.test(nextCursor)) ||
    (value.page.has_more && nextCursor === null) ||
    (!value.page.has_more && nextCursor !== null) ||
    value.items.length > limit ||
    (value.page.has_more && value.items.length !== limit)
  ) {
    return null;
  }
  const items = value.items.flatMap((item) => {
    const projected = projectItem(item, asOf);
    return projected ? [projected] : [];
  });
  if (
    items.length !== value.items.length ||
    new Set(items.map((item) => item.project.id)).size !== items.length
  ) {
    return null;
  }
  return {
    schemaVersion: 1,
    asOf,
    summary: {
      scope: 'filtered',
      tenantProjectCount,
      projectCount,
      projectStateCounts: {
        draft: stateCounts[0]!,
        active: stateCounts[1]!,
        paused: stateCounts[2]!,
        archived: stateCounts[3]!,
      },
      setupReadyProjectCount,
      projectWithEntitlementRecordCount,
      activeEntitlementCount,
      attentionProjectCount,
    },
    commercialCapabilities: {
      quotationHistory: 'unsupported',
      signedContractLedger: 'unsupported',
      invoiceReceivablePaymentLedger: 'unsupported',
    },
    items,
    page: { limit, nextCursor, hasMore: value.page.has_more, filteredTotal },
  };
}

const normalizedQuery = (
  query: OperationsBusinessOverviewQuery,
): OperationsBusinessOverviewQuery | null => {
  const limit = query.limit ?? 4;
  const q = query.q?.trim();
  if (
    !Number.isSafeInteger(limit) ||
    limit < 1 ||
    limit > 20 ||
    (query.cursor !== undefined && !cursorPattern.test(query.cursor)) ||
    (q !== undefined && (q.length === 0 || q.length > 120 || secretValuePattern.test(q))) ||
    (query.projectState !== undefined &&
      !businessOverviewProjectStates.includes(query.projectState)) ||
    (query.attention !== undefined && !businessOverviewAttentionCodes.includes(query.attention))
  ) {
    return null;
  }
  return {
    limit,
    ...(query.cursor ? { cursor: query.cursor } : {}),
    ...(q ? { q } : {}),
    ...(query.projectState ? { projectState: query.projectState } : {}),
    ...(query.attention ? { attention: query.attention } : {}),
  };
};

const safeRequestUrl = (baseUrl: string, query: OperationsBusinessOverviewQuery): string | null => {
  let url: URL;
  try {
    const base =
      baseUrl || (typeof window === 'undefined' ? 'https://geo.invalid' : window.location.origin);
    url = new URL('/api/v2/operations/business-overview', base);
    const parsedBase = new URL(base);
    if (
      !['http:', 'https:'].includes(parsedBase.protocol) ||
      parsedBase.username ||
      parsedBase.password
    ) {
      return null;
    }
  } catch {
    return null;
  }
  url.searchParams.set('limit', String(query.limit ?? 4));
  if (query.cursor) url.searchParams.set('cursor', query.cursor);
  if (query.q) url.searchParams.set('q', query.q);
  if (query.projectState) url.searchParams.set('project_state', query.projectState);
  if (query.attention) url.searchParams.set('attention', query.attention);
  return baseUrl || typeof window !== 'undefined' ? url.toString() : `${url.pathname}${url.search}`;
};

const safeHeaders = (headers: OperationsIdentityHeaders): boolean => {
  const tenant = safeText(headers['X-Tenant-Id'], 120);
  const actor = safeText(headers['X-Actor-Id'], 255);
  const role = safeEnum(headers['X-Actor-Role'], [
    'customer',
    'operator',
    'analyst',
    'reviewer',
    'admin',
    'worker',
  ] as const);
  return Boolean(tenant && /^tnt_[A-Za-z0-9_-]{1,116}$/.test(tenant) && actor && role);
};

export const projectOperationsBusinessOverviewRequestHeaders = (
  headers: OperationsIdentityHeaders,
  env: BrowserBuildIdentityEnv | undefined,
): Record<string, string> | null => {
  if (!allowsFixtureIdentityHeaders(env)) return { Accept: 'application/json' };
  return safeHeaders(headers) ? { Accept: 'application/json', ...headers } : null;
};

export async function getOperationsBusinessOverview(
  headers: OperationsIdentityHeaders,
  query: OperationsBusinessOverviewQuery = {},
  options: OperationsBusinessOverviewRequestOptions = {},
): Promise<OperationsBusinessOverviewResult> {
  const normalized = normalizedQuery(query);
  const baseUrl = options.baseUrl ?? configuredApiBase;
  const url = normalized ? safeRequestUrl(baseUrl, normalized) : null;
  const requestHeaders = projectOperationsBusinessOverviewRequestHeaders(
    headers,
    (import.meta as ImportMeta & { env?: BrowserBuildIdentityEnv }).env,
  );
  if (!normalized || !url || !requestHeaders) return { kind: 'unavailable' };
  try {
    const response = await (options.fetcher ?? globalThis.fetch)(url, {
      method: 'GET',
      credentials: 'include',
      headers: requestHeaders,
      ...(options.signal ? { signal: options.signal } : {}),
    });
    if (response.status === 401 || response.status === 403) return { kind: 'forbidden' };
    if (
      !response.ok ||
      !response.headers.get('content-type')?.toLowerCase().startsWith('application/json')
    ) {
      return { kind: 'unavailable' };
    }
    const declaredLength = Number(response.headers.get('content-length') ?? 0);
    if (Number.isFinite(declaredLength) && declaredLength > maximumResponseBytes) {
      return { kind: 'unavailable' };
    }
    const body = await response.text();
    if (new TextEncoder().encode(body).byteLength > maximumResponseBytes) {
      return { kind: 'unavailable' };
    }
    const projected = projectOperationsBusinessOverview(JSON.parse(body));
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}
