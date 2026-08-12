import {
  createGeoApiClient,
  type IdentitySessionHeaders,
  type ProjectPageResponse,
} from '@geo/api-client';

const API_BASE =
  (import.meta as ImportMeta & { env?: { VITE_GEO_API_BASE?: string } }).env?.VITE_GEO_API_BASE ??
  (typeof window === 'undefined' ? '' : window.location.origin);

export type SessionContext = {
  tenantId: string;
  actorId: string;
  role: 'operator' | 'analyst' | 'reviewer' | 'admin';
  headers: IdentitySessionHeaders;
};

export type Pairing = Awaited<ReturnType<typeof executionApi.pairIntervention>>;
export type AnswerPage = Awaited<ReturnType<typeof executionApi.answers>>;
export type AnswerRow = AnswerPage['data'][number];
export type AnswerRelations = Awaited<ReturnType<typeof executionApi.answerRelations>>;
export type TaskTrace = Awaited<ReturnType<typeof executionApi.taskTrace>>;

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
  profile_expires_at: string | null;
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
  source: string;
  schedule_pub_id: string | null;
  retry_of_run_pub_id: string | null;
  initiated_by_pub_id: string | null;
  updated_at: string;
};

export type Project = {
  pub_id: string;
  name: string;
  state: string;
  updated_at: string;
  // 列表端点 ProjectSummary 契约含 brandrank_domain（schema.generated.ts），
  // @geo/api-client 导出的 ProjectSummary 投影未带上该字段，这里按真实载荷补齐。
  brandrank_domain?: string | null;
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
  assigned_to_pub_id?: string | null;
  due_at?: string | null;
  resolution_note: string;
};

export type FrozenConfig = {
  pub_id: string;
  revision: number;
  effective_at: string;
  frozen_at: string;
  snapshot_hash: string;
  snapshot: Record<string, unknown>;
};

export type Schedule = {
  pub_id: string;
  project_pub_id: string;
  config_version_pub_id: string;
  interval_minutes: number;
  timezone: string;
  state: 'active' | 'paused' | 'archived';
  next_run_at: string;
  last_run_at: string | null;
  last_run_pub_id: string | null;
  responsible_pub_id: string;
  created_by_pub_id: string;
  version: number;
  created_at: string;
  updated_at: string;
};

export type PlatformSla = {
  platform: string;
  display_name: string;
  owner_pub_id: string;
  session_ttl_minutes: number;
  intervention_sla_minutes: number;
  success_target_bps: number;
  total_tasks_30d: number;
  completed_tasks_30d: number;
  failed_tasks_30d: number;
  interventions_30d: number;
  overdue_interventions: number;
  success_rate: number | null;
  manual_takeover_rate: number | null;
  active_accounts: number;
  session_expires_at: string | null;
  state: 'healthy' | 'warning' | 'breached' | 'unmeasured';
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

const client = createGeoApiClient(API_BASE);

function requireData<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data !== undefined) return result.data;
  const payload = result.error as
    | { error?: { code?: string }; detail?: { code?: string } }
    | undefined;
  throw new Error(
    payload?.error?.code ?? payload?.detail?.code ?? `http_${result.response.status}`,
  );
}

export const executionApi = {
  accounts: async (session: SessionContext): Promise<Account[]> =>
    requireData(
      await client.GET('/api/v2/platform-accounts', {
        params: { header: session.headers },
      }),
    ),
  projects: async (session: SessionContext): Promise<ProjectPageResponse> =>
    requireData(
      await client.GET('/api/v2/projects', {
        params: { header: session.headers, query: { limit: 100 } },
      }),
    ),
  createProject: async (
    session: SessionContext,
    input: { name: string; customerName: string },
  ): Promise<Project> =>
    requireData(
      await client.POST('/api/v2/projects', {
        params: {
          header: {
            ...session.headers,
            'Idempotency-Key': `project-create-${crypto.randomUUID()}`,
          },
        },
        body: { name: input.name, customer_name: input.customerName },
      }),
    ),
  configVersions: async (session: SessionContext, projectId: string): Promise<FrozenConfig[]> =>
    requireData(
      await client.GET('/api/v2/projects/{project_pub_id}/config/versions', {
        params: {
          path: { project_pub_id: projectId },
          query: { limit: 20 },
          header: session.headers,
        },
      }),
    ),
  freezeConfig: async (
    session: SessionContext,
    projectId: string,
    input: {
      queryGroups: { name: string; items: { text: string; priority: number }[] }[];
      regions: string[];
      models: string[];
      modes: string[];
      frequency: string;
      effectiveAt: string;
    },
  ): Promise<FrozenConfig> =>
    requireData(
      await client.POST('/api/v2/projects/{project_pub_id}/config/freeze', {
        params: {
          path: { project_pub_id: projectId },
          header: {
            ...session.headers,
            'Idempotency-Key': `config-freeze-${crypto.randomUUID()}`,
          },
        },
        body: {
          query_groups: input.queryGroups,
          regions: input.regions,
          models: input.models,
          modes: input.modes,
          frequency: input.frequency,
          effective_at: input.effectiveAt,
        },
      }),
    ),
  startRun: async (session: SessionContext, projectId: string, configVersionId: string) =>
    requireData(
      await client.POST('/api/v2/collection/runs', {
        params: {
          header: {
            ...session.headers,
            'Idempotency-Key': `collection-start-${crypto.randomUUID()}`,
          },
        },
        body: {
          project_pub_id: projectId,
          config_version_pub_id: configVersionId,
          requires_intervention: false,
          account_pub_id: null,
        },
      }),
    ),
  runs: async (session: SessionContext): Promise<Run[]> =>
    requireData(
      await client.GET('/api/v2/collection/runs', {
        params: { header: session.headers },
      }),
    ),
  interventions: async (session: SessionContext): Promise<Intervention[]> =>
    requireData(
      await client.GET('/api/v2/interventions', {
        params: { header: session.headers },
      }),
    ),
  breakGlassRequests: async (session: SessionContext): Promise<BreakGlassRequest[]> =>
    requireData(
      await client.GET('/api/v2/break-glass', {
        params: { header: session.headers },
      }),
    ),
  events: async (session: SessionContext, accountId: string): Promise<SessionEvent[]> =>
    requireData(
      await client.GET('/api/v2/platform-accounts/{account_pub_id}/events', {
        params: {
          path: { account_pub_id: accountId },
          header: session.headers,
        },
      }),
    ),
  controlRun: async (
    session: SessionContext,
    runId: string,
    action: 'pause' | 'resume' | 'cancel' | 'retry',
  ) =>
    requireData(
      await client.POST('/api/v2/collection/runs/{run_pub_id}/{action}', {
        params: {
          path: { run_pub_id: runId, action },
          header: {
            ...session.headers,
            'Idempotency-Key': `run-control-${crypto.randomUUID()}`,
          },
        },
      }),
    ),
  cancelIntervention: async (
    session: SessionContext,
    interventionId: string,
    reason: string,
  ): Promise<Intervention> =>
    requireData(
      await client.POST('/api/v2/interventions/{intervention_pub_id}/cancel', {
        params: {
          path: { intervention_pub_id: interventionId },
          header: session.headers,
        },
        body: { reason },
      }),
    ),
  schedules: async (session: SessionContext): Promise<Schedule[]> =>
    requireData(
      await client.GET('/api/v2/schedules', {
        params: { query: { limit: 100 }, header: session.headers },
      }),
    ),
  createSchedule: async (
    session: SessionContext,
    input: {
      projectId: string;
      configVersionId: string;
      intervalMinutes: number;
      nextRunAt: string;
      responsiblePubId: string;
    },
  ): Promise<Schedule> =>
    requireData(
      await client.POST('/api/v2/schedules', {
        params: { header: session.headers },
        body: {
          project_pub_id: input.projectId,
          config_version_pub_id: input.configVersionId,
          interval_minutes: input.intervalMinutes,
          timezone: 'Asia/Shanghai',
          next_run_at: input.nextRunAt,
          responsible_pub_id: input.responsiblePubId,
        },
      }),
    ),
  updateSchedule: async (
    session: SessionContext,
    schedule: Schedule,
    state: Schedule['state'],
  ): Promise<Schedule> =>
    requireData(
      await client.PATCH('/api/v2/schedules/{schedule_pub_id}', {
        params: {
          path: { schedule_pub_id: schedule.pub_id },
          header: session.headers,
        },
        body: { state, expected_version: schedule.version, next_run_at: null },
      }),
    ),
  runScheduleNow: async (session: SessionContext, scheduleId: string) =>
    requireData(
      await client.POST('/api/v2/schedules/{schedule_pub_id}/run-now', {
        params: { path: { schedule_pub_id: scheduleId }, header: session.headers },
      }),
    ),
  platformSla: async (session: SessionContext): Promise<PlatformSla[]> =>
    requireData(
      await client.GET('/api/v2/operations/platform-sla', {
        params: { header: session.headers },
      }),
    ),
  healthCheck: async (session: SessionContext, accountId: string) =>
    requireData(
      await client.POST('/api/v2/platform-accounts/{account_pub_id}/health-checks', {
        params: {
          path: { account_pub_id: accountId },
          query: { live_canary: false },
          header: session.headers,
        },
      }),
    ),
  liveCanary: async (session: SessionContext, accountId: string) =>
    requireData(
      await client.POST('/api/v2/platform-accounts/{account_pub_id}/health-checks', {
        params: {
          path: { account_pub_id: accountId },
          query: { live_canary: true },
          header: session.headers,
        },
      }),
    ),
  quarantine: async (session: SessionContext, accountId: string, reason: string) =>
    requireData(
      await client.POST('/api/v2/platform-accounts/{account_pub_id}/quarantine', {
        params: {
          path: { account_pub_id: accountId },
          query: { reason },
          header: session.headers,
        },
      }),
    ),
  requestBreakGlass: async (
    session: SessionContext,
    accountId: string,
  ): Promise<BreakGlassRequest> =>
    requireData(
      await client.POST('/api/v2/platform-accounts/{account_pub_id}/break-glass', {
        params: {
          path: { account_pub_id: accountId },
          header: session.headers,
        },
        body: {
          reason: 'Operations incident investigation with dual-control approval',
          ttl_seconds: 600,
        },
      }),
    ),
  approveBreakGlass: async (
    session: SessionContext,
    requestId: string,
  ): Promise<BreakGlassRequest> =>
    requireData(
      await client.POST('/api/v2/break-glass/{request_pub_id}/approve', {
        params: {
          path: { request_pub_id: requestId },
          header: session.headers,
        },
      }),
    ),
  revoke: async (session: SessionContext, accountId: string, reason: string) =>
    requireData(
      await client.POST('/api/v2/platform-accounts/{account_pub_id}/revoke', {
        params: {
          path: { account_pub_id: accountId },
          query: { reason },
          header: session.headers,
        },
      }),
    ),
  pairIntervention: async (session: SessionContext, interventionId: string) =>
    requireData(
      await client.POST('/api/v2/interventions/{intervention_pub_id}/pair', {
        params: {
          path: { intervention_pub_id: interventionId },
          header: session.headers,
        },
      }),
    ),
  completeIntervention: async (
    session: SessionContext,
    interventionId: string,
    pairingToken: string,
    evidenceHash: string,
  ): Promise<Intervention> =>
    requireData(
      await client.POST('/api/v2/interventions/{intervention_pub_id}/complete', {
        params: {
          path: { intervention_pub_id: interventionId },
          header: session.headers,
        },
        body: {
          pairing_token: pairingToken,
          platform_result: 'verified',
          evidence_hash: evidenceHash,
        },
      }),
    ),
  answers: async (
    session: SessionContext,
    input: { projectPubId: string; runPubId?: string; cursor?: string; limit?: number },
  ) =>
    requireData(
      await client.GET('/api/v2/analytics/answers', {
        params: {
          header: session.headers,
          query: {
            project_pub_id: input.projectPubId,
            ...(input.runPubId ? { run_pub_id: input.runPubId } : {}),
            ...(input.cursor ? { cursor: input.cursor } : {}),
            limit: input.limit ?? 50,
          },
        },
      }),
    ),
  answerRelations: async (session: SessionContext, answerPubId: string) =>
    requireData(
      await client.GET('/api/v2/analytics/answers/{answer_pub_id}/relations', {
        params: { path: { answer_pub_id: answerPubId }, header: session.headers },
      }),
    ),
  // answer_pub_id 与 collection_task.pub_id 同值，可直接当 task id 调 trace 端点。
  // 无思考链证据的平台（tongyi/yuanbao）返回 404，由调用方按 message 判定中性空态。
  taskTrace: async (session: SessionContext, taskPubId: string) =>
    requireData(
      await client.GET('/api/v2/collection/tasks/{task_pub_id}/trace', {
        params: { path: { task_pub_id: taskPubId }, header: session.headers },
      }),
    ),
};
