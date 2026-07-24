const API_BASE =
  (import.meta as ImportMeta & { env?: { VITE_GEO_API_BASE?: string } }).env?.VITE_GEO_API_BASE ??
  'http://127.0.0.1:45200';

export type SessionContext = {
  tenantId: string;
  actorId: string;
  role: 'operator' | 'reviewer' | 'admin';
};

export type Account = {
  pub_id: string;
  platform: string;
  account_mask: string;
  owner_pub_id: string;
  purpose: string;
  responsible_pub_id: string;
  custody_mode: string;
  region: string;
  state: string;
  admission_level: string;
  last_passed_at: string | null;
  scopes: string[];
  authorization_expires_at: string | null;
  profile_state: string | null;
  profile_version: number | null;
  profile_constraints: string[];
  lease_expires_at: string | null;
};

export type Run = {
  pub_id: string;
  project_pub_id: string;
  config_version_pub_id: string;
  workflow_id: string;
  state: string;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  paused: boolean;
  error_code: string | null;
  updated_at: string;
};

export type Project = {
  pub_id: string;
  name: string;
  state: string;
  updated_at: string;
};

export type Intervention = {
  pub_id: string;
  account_pub_id: string;
  account_mask: string;
  challenge_type: string;
  allowed_domain: string;
  action: string;
  state: string;
  pairing_expires_at: string | null;
  platform_result: string | null;
};

export type SessionEvent = {
  pub_id: string;
  event_type: string;
  summary: Record<string, unknown>;
  occurred_at: string;
};

export type BreakGlassRequest = {
  pub_id: string;
  account_pub_id: string;
  requested_by: string;
  reason: string;
  state: string;
  approvals: number;
  expires_at: string;
};

function headers(session: SessionContext): HeadersInit {
  return {
    'X-Tenant-Id': session.tenantId,
    'X-Actor-Id': session.actorId,
    'X-Actor-Role': session.role,
    'Content-Type': 'application/json',
  };
}

async function request<T>(
  path: string,
  session: SessionContext,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers(session), ...init.headers },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      detail?: { code?: string };
    } | null;
    throw new Error(payload?.detail?.code ?? `http_${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const executionApi = {
  accounts: (session: SessionContext) => request<Account[]>('/api/v2/platform-accounts', session),
  projects: (session: SessionContext) =>
    request<{ data: Project[] }>('/api/v2/projects?limit=100', session),
  runs: (session: SessionContext) => request<Run[]>('/api/v2/collection/runs', session),
  interventions: (session: SessionContext) =>
    request<Intervention[]>('/api/v2/interventions', session),
  breakGlassRequests: (session: SessionContext) =>
    request<BreakGlassRequest[]>('/api/v2/break-glass', session),
  events: (session: SessionContext, accountId: string) =>
    request<SessionEvent[]>(`/api/v2/platform-accounts/${accountId}/events`, session),
  controlRun: (session: SessionContext, runId: string, action: 'pause' | 'resume' | 'cancel') =>
    request(`/api/v2/collection/runs/${runId}/${action}`, session, { method: 'POST' }),
  healthCheck: (session: SessionContext, accountId: string) =>
    request(`/api/v2/platform-accounts/${accountId}/health-checks`, session, { method: 'POST' }),
  liveCanary: (session: SessionContext, accountId: string) =>
    request(`/api/v2/platform-accounts/${accountId}/health-checks?live_canary=true`, session, {
      method: 'POST',
    }),
  quarantine: (session: SessionContext, accountId: string, reason: string) =>
    request(
      `/api/v2/platform-accounts/${accountId}/quarantine?reason=${encodeURIComponent(reason)}`,
      session,
      { method: 'POST' },
    ),
  requestBreakGlass: (session: SessionContext, accountId: string) =>
    request<BreakGlassRequest>(`/api/v2/platform-accounts/${accountId}/break-glass`, session, {
      method: 'POST',
      body: JSON.stringify({
        reason: 'Operations incident investigation with dual-control approval',
        ttl_seconds: 600,
      }),
    }),
  approveBreakGlass: (session: SessionContext, requestId: string) =>
    request<BreakGlassRequest>(`/api/v2/break-glass/${requestId}/approve`, session, {
      method: 'POST',
    }),
  revoke: (session: SessionContext, accountId: string, reason: string) =>
    request(
      `/api/v2/platform-accounts/${accountId}/revoke?reason=${encodeURIComponent(reason)}`,
      session,
      {
        method: 'POST',
      },
    ),
  pairIntervention: (session: SessionContext, interventionId: string) =>
    request<{ intervention_pub_id: string; pairing_token: string; expires_at: string }>(
      `/api/v2/interventions/${interventionId}/pair`,
      session,
      { method: 'POST' },
    ),
  completeIntervention: (
    session: SessionContext,
    interventionId: string,
    pairingToken: string,
    evidenceHash: string,
  ) =>
    request<Intervention>(`/api/v2/interventions/${interventionId}/complete`, session, {
      method: 'POST',
      body: JSON.stringify({
        pairing_token: pairingToken,
        platform_result: 'verified',
        evidence_hash: evidenceHash,
      }),
    }),
};
