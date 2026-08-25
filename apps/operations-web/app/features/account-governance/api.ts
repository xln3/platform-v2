import { allowsFixtureIdentityHeaders, type BrowserBuildIdentityEnv } from '@geo/api-client';
import { COLLECTION_PLATFORM_SLUGS, type CollectionPlatformSlug } from '../../platforms';
import { pageFromResponse, type CursorPage } from '../../pagination';
import type { SessionContext } from '../execution/api';

export { PLATFORM_LABELS } from '../../platforms';

const API_BASE =
  (import.meta as ImportMeta & { env?: { VITE_GEO_API_BASE?: string } }).env?.VITE_GEO_API_BASE ??
  (typeof window === 'undefined' ? '' : window.location.origin);

/**
 * 与 services/api.ts 同一不变量：生产包不发送浏览器身份三头
 * （native_session 由 cookie 鉴权）；fixture/e2e 构建保留身份头供契约夹具流使用。
 */
function fixtureIdentityHeaders(session: SessionContext): Record<string, string> {
  const env = (import.meta as ImportMeta & { env?: BrowserBuildIdentityEnv }).env;
  if (!allowsFixtureIdentityHeaders(env)) return {};
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(session.headers)) {
    if (typeof value === 'string') headers[key] = value;
  }
  return headers;
}

/** 带错误码与 details 的服务端错误（错误包络统一 {error:{code}}）。 */
export class AccountGovApiError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown>;
  constructor(code: string, details?: Record<string, unknown>) {
    super(code);
    this.code = code;
    this.details = details ?? {};
  }
}

async function readApiError(response: Response): Promise<Error> {
  let code = `http_${response.status}`;
  let details: Record<string, unknown> | undefined;
  try {
    const payload = (await response.json()) as
      | {
          error?: { code?: string; details?: Record<string, unknown> };
          detail?: { code?: string };
        }
      | undefined;
    code = payload?.error?.code ?? payload?.detail?.code ?? code;
    details = payload?.error?.details;
  } catch {
    // 非 JSON 错误体：保留 http_<status> 口径。
  }
  return new AccountGovApiError(code, details);
}

async function govGet<T>(session: SessionContext, path: string): Promise<T> {
  const response = await fetch(new URL(`${API_BASE}${path}`), {
    headers: { Accept: 'application/json', ...fixtureIdentityHeaders(session) },
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  });
  if (!response.ok) throw await readApiError(response);
  return (await response.json()) as T;
}

async function govGetPage<T>(
  session: SessionContext,
  path: string,
  input: { cursor?: string; limit?: number } = {},
): Promise<CursorPage<T>> {
  const url = new URL(`${API_BASE}${path}`);
  url.searchParams.set('limit', String(input.limit ?? 100));
  if (input.cursor) url.searchParams.set('cursor', input.cursor);
  const response = await fetch(url, {
    headers: { Accept: 'application/json', ...fixtureIdentityHeaders(session) },
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  });
  if (!response.ok) throw await readApiError(response);
  return pageFromResponse((await response.json()) as T[], response);
}

async function govSend<T>(
  session: SessionContext,
  method: 'POST' | 'PATCH',
  path: string,
  body: Record<string, unknown>,
  idempotencyKey?: string,
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    ...fixtureIdentityHeaders(session),
  };
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
  const response = await fetch(new URL(`${API_BASE}${path}`), {
    method,
    headers,
    body: JSON.stringify(body),
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  });
  if (!response.ok) throw await readApiError(response);
  return (await response.json()) as T;
}

// ── 契约类型（字段逐字按后端契约，勿凭想象改） ──

export const COLLECTION_PLATFORMS = COLLECTION_PLATFORM_SLUGS;
export type CollectionPlatform = CollectionPlatformSlug;

/** 手机号 × 平台格（runtime_state：idle/running/muted/quota_exhausted/captcha/error）。 */
export type PlatformAccountCell = {
  platform_account_pub_id: string;
  region_gb: string | null;
  quota_day: number | null;
  quota_week: number | null;
  quota_year: number | null;
  used_today: number;
  used_week: number;
  used_year: number;
  quota_reset_at: string | null;
  quota_resume_at: string | null;
  runtime_state: string;
  current_run_pub_id: string | null;
  muted_until: string | null;
  state_reason: string | null;
  browser_instance_key: string | null;
};

/** 账号管理页的行 = 手机号。platforms 五键固定，未登记平台为 null。 */
export type CollectionAccountRow = {
  phone_account_pub_id: string;
  /** 完整号码只向 account:operate 的管理员/操作员返回；只读角色为 null。 */
  phone: string | null;
  phone_masked: string;
  owner_note: string | null;
  state: string;
  sms_link_state: string;
  last_sms_at: string | null;
  push_link_state: string;
  last_push_test_at: string | null;
  platforms: Record<CollectionPlatform, PlatformAccountCell | null>;
};

export function accountPhoneLabel(
  row: Pick<CollectionAccountRow, 'phone' | 'phone_masked'>,
): string {
  return row.phone ?? row.phone_masked;
}

export type OtpRegistrySyncResult = {
  scanned: number;
  created: number;
  updated: number;
  unchanged: number;
};

export type AccountQuotaObservation = {
  observation_pub_id: string;
  phone_account_pub_id: string;
  phone_masked: string;
  platform: CollectionPlatform;
  observed_browser_instance_key: string;
  observed_region_gb: string | null;
  mode: 'normal' | 'deep_think' | 'unknown';
  account_tier: 'free' | 'subscriber' | 'unknown';
  quota_state: 'available' | 'exhausted' | 'unknown';
  window_type: 'rolling' | 'calendar' | 'unknown';
  window_days: number | null;
  observed_window_count: number | null;
  daily_equivalent: number | null;
  count_kind: 'lower_bound' | 'estimate' | 'platform_exact' | 'unknown';
  reset_at: string | null;
  observed_at: string;
  source: 'platform' | 'platform_and_logs' | 'manual' | 'unknown';
};

export type CollectionAccountEvent = {
  event_pub_id: string;
  event_type: string;
  actor: string;
  phone_account_pub_id: string | null;
  platform_account_pub_id: string | null;
  browser_pub_id: string | null;
  region_pub_id: string | null;
  old_value: string | null;
  new_value: string | null;
  evidence: string | Record<string, unknown> | null;
  run_pub_id: string | null;
  created_at: string;
};

export type CollectionBrowserRow = {
  browser_pub_id: string;
  instance_key: string;
  platform: string | null;
  region_gb: string | null;
  exit_ip: string | null;
  cdp_port: number | null;
  systemd_unit: string | null;
  activity: string;
  error_streak: number;
  breaker_until: string | null;
  muted_until: string | null;
  started_at: string | null;
  uptime_s: number | null;
  rss_bytes: number | null;
  bindings: Partial<Record<CollectionPlatform, string | null>>;
};

export type CollectionRegionRow = {
  region_pub_id: string;
  region_gb: string;
  name: string;
  source: string | null;
  proxy_env_key: string | null;
  relay_unit: string | null;
  exit_ip_last: string | null;
  last_probe_at: string | null;
  state: string;
  note: string | null;
};

export type LinkTestResult = {
  ok: boolean;
  channel: 'sms' | 'push';
  sms_link_state?: string;
  push_link_state?: string;
  last_sms_at?: string | null;
  last_push_test_at?: string | null;
  wait_window_s?: number;
  guidance?: string;
  detail?: string;
};

export type BrowserSyncResult = {
  synced: number;
  created: number;
  updated: number;
  errors: number | string[];
  instances: number | string[];
};

export type PlatformAccountPatch = {
  region_gb?: string | null;
  quota_day?: number | null;
  quota_week?: number | null;
  quota_year?: number | null;
  browser_instance_key?: string | null;
  confirm?: boolean;
};

export const accountGovApi = {
  listAccounts: (session: SessionContext, input: { cursor?: string; limit?: number } = {}) =>
    govGetPage<CollectionAccountRow>(session, '/api/v2/collection-accounts', input),
  listQuotaObservations: (session: SessionContext) =>
    govGet<AccountQuotaObservation[]>(session, '/api/v2/collection-account-quota-observations'),
  createAccount: (session: SessionContext, input: { phone: string; owner_note?: string }) =>
    govSend<CollectionAccountRow>(
      session,
      'POST',
      '/api/v2/collection-accounts',
      { phone: input.phone, ...(input.owner_note ? { owner_note: input.owner_note } : {}) },
      `collection-account-create-${crypto.randomUUID()}`,
    ),
  syncOtpRegistry: (session: SessionContext) =>
    govSend<OtpRegistrySyncResult>(
      session,
      'POST',
      '/api/v2/collection-accounts/sync-otp-registry',
      {},
      `collection-account-otp-sync-${crypto.randomUUID()}`,
    ),
  patchPlatformAccount: (
    session: SessionContext,
    platformAccountPubId: string,
    patch: PlatformAccountPatch,
  ) =>
    govSend<PlatformAccountCell>(
      session,
      'PATCH',
      `/api/v2/collection-platform-accounts/${platformAccountPubId}`,
      { ...patch },
    ),
  linkTest: (session: SessionContext, phoneAccountPubId: string, channel: 'sms' | 'push') =>
    govSend<LinkTestResult>(
      session,
      'POST',
      `/api/v2/collection-accounts/${phoneAccountPubId}/link-test`,
      { channel },
    ),
  listAccountEvents: (
    session: SessionContext,
    phoneAccountPubId: string,
    input: { cursor?: string; limit?: number } = {},
  ) =>
    govGetPage<CollectionAccountEvent>(
      session,
      `/api/v2/collection-accounts/${phoneAccountPubId}/events`,
      input,
    ),
  listBrowsers: (session: SessionContext, input: { cursor?: string; limit?: number } = {}) =>
    govGetPage<CollectionBrowserRow>(session, '/api/v2/collection-browsers', input),
  syncBrowsers: (session: SessionContext) =>
    govSend<BrowserSyncResult>(session, 'POST', '/api/v2/collection-browsers/sync', {}),
  restartBrowser: (session: SessionContext, instanceKey: string) =>
    govSend<{ ok: boolean; executed: boolean; detail: string }>(
      session,
      'POST',
      `/api/v2/collection-browsers/${instanceKey}/restart`,
      {},
    ),
  releaseBrowserLock: (session: SessionContext, instanceKey: string) =>
    govSend<{ ok: boolean; released: boolean; detail: string }>(
      session,
      'POST',
      `/api/v2/collection-browsers/${instanceKey}/release-lock`,
      {},
    ),
  listRegions: (session: SessionContext) =>
    govGet<CollectionRegionRow[]>(session, '/api/v2/collection-regions'),
  createRegion: (
    session: SessionContext,
    input: { region_gb: string; name?: string; proxy_env_key?: string; relay_unit?: string },
  ) =>
    govSend<CollectionRegionRow>(
      session,
      'POST',
      '/api/v2/collection-regions',
      {
        region_gb: input.region_gb,
        ...(input.name ? { name: input.name } : {}),
        ...(input.proxy_env_key ? { proxy_env_key: input.proxy_env_key } : {}),
        ...(input.relay_unit ? { relay_unit: input.relay_unit } : {}),
      },
      `collection-region-create-${crypto.randomUUID()}`,
    ),
};
