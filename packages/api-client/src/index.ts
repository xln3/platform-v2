import createClient from 'openapi-fetch';
import type { paths } from './schema.generated';

export type HealthResponse =
  paths['/api/v2/health']['get']['responses']['200']['content']['application/json'];
export type IdentitySessionResponse =
  paths['/api/v2/identity/session']['get']['responses']['200']['content']['application/json'];
export type ProjectPageResponse =
  paths['/api/v2/projects']['get']['responses']['200']['content']['application/json'];
export type IdentitySessionHeaders =
  paths['/api/v2/identity/session']['get']['parameters']['header'];
export type ProjectResourceKind =
  paths['/api/v2/projects/{project_pub_id}/resources/{kind}']['get']['parameters']['path']['kind'];
export type ProjectResourceView =
  paths['/api/v2/projects/{project_pub_id}/resources/{kind}']['get']['responses']['200']['content']['application/json'][number];
export type ProjectResourceWrite =
  paths['/api/v2/projects/{project_pub_id}/resources/{kind}']['post']['requestBody']['content']['application/json'];
export type CustomerAccountView =
  paths['/api/v2/customer/platform-accounts']['get']['responses']['200']['content']['application/json'][number];
export type CustomerAccountCreate =
  paths['/api/v2/customer/platform-accounts']['post']['requestBody']['content']['application/json'];
export type CustomerAuthorizationCreate =
  paths['/api/v2/customer/platform-accounts/{account_pub_id}/authorizations']['post']['requestBody']['content']['application/json'];
export type CustomerPairingCreate =
  paths['/api/v2/customer/platform-accounts/{account_pub_id}/pairings']['post']['requestBody']['content']['application/json'];
export type CustomerPairingView =
  paths['/api/v2/customer/platform-accounts/{account_pub_id}/pairings']['post']['responses']['201']['content']['application/json'];
export type CustomerEventView =
  paths['/api/v2/customer/platform-accounts/{account_pub_id}/events']['get']['responses']['200']['content']['application/json'][number];
export type WorkflowAccepted =
  paths['/api/v2/customer/platform-accounts/{account_pub_id}/revoke']['post']['responses']['202']['content']['application/json'];
export type AnalyticsOverviewResponse =
  paths['/api/v2/analytics/overview']['get']['responses']['200']['content']['application/json'];
export type AnalyticsAnswerPage =
  paths['/api/v2/analytics/answers']['get']['responses']['200']['content']['application/json'];
export type ReportPage =
  paths['/api/v2/reports']['get']['responses']['200']['content']['application/json'];
export type ReportDetail =
  paths['/api/v2/reports/{report_pub_id}']['get']['responses']['200']['content']['application/json'];
export type ReportReviewCreate =
  paths['/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/reviews']['post']['requestBody']['content']['application/json'];
export type ReportCommentCreate =
  paths['/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/comments']['post']['requestBody']['content']['application/json'];
export type EvidenceAssetPage =
  paths['/api/v2/evidence/assets']['get']['responses']['200']['content']['application/json'];
export type InvestigationPage =
  paths['/api/v2/intelligence/investigations']['get']['responses']['200']['content']['application/json'];
export type InvestigationDetail =
  paths['/api/v2/intelligence/investigations/{investigation_pub_id}']['get']['responses']['200']['content']['application/json'];
export type VerdictCreate =
  paths['/api/v2/intelligence/investigations/{investigation_pub_id}/verdicts']['post']['requestBody']['content']['application/json'];
export type AppealCreate =
  paths['/api/v2/intelligence/investigations/{investigation_pub_id}/appeals']['post']['requestBody']['content']['application/json'];

export type GeoApiClient = ReturnType<typeof createClient<paths>>;

const configuredApiBase =
  (import.meta as ImportMeta & { env?: { VITE_GEO_API_BASE?: string } }).env?.VITE_GEO_API_BASE ??
  '';

export function createGeoApiClient(baseUrl = configuredApiBase): GeoApiClient {
  return createClient<paths>({ baseUrl });
}

/**
 * Generated-path client for same-origin browser calls. Application code must use this boundary
 * instead of duplicating OpenAPI request or response shapes.
 */
export const apiClient = createGeoApiClient();

export async function getHealth(): Promise<HealthResponse> {
  const { data, error } = await apiClient.GET('/api/v2/health');
  if (error || !data) {
    throw new Error('GEO Platform health endpoint is unavailable');
  }
  return data;
}

export type IdentitySessionResult =
  | { kind: 'ready'; session: IdentitySessionResponse; projects: ProjectPageResponse }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

export type ProjectResourceResult<T> =
  | { kind: 'ready'; data: T }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

const classifyResourceFailure = (status: number): { kind: 'forbidden' | 'unavailable' } =>
  status === 401 || status === 403 || status === 404
    ? { kind: 'forbidden' }
    : { kind: 'unavailable' };

/** Validates browser session hints against S01 before any role-gated projection is rendered. */
export async function getIdentitySession(
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<IdentitySessionResult> {
  try {
    const sessionResult = await client.GET('/api/v2/identity/session', {
      params: { header: headers },
    });
    if (!sessionResult.data) {
      return sessionResult.response.status === 401 || sessionResult.response.status === 403
        ? { kind: 'forbidden' }
        : { kind: 'unavailable' };
    }
    const projectsResult = await client.GET('/api/v2/projects', {
      params: { header: headers, query: { limit: 50 } },
    });
    if (!projectsResult.data) {
      return projectsResult.response.status === 401 || projectsResult.response.status === 403
        ? { kind: 'forbidden' }
        : { kind: 'unavailable' };
    }
    return { kind: 'ready', session: sessionResult.data, projects: projectsResult.data };
  } catch {
    return { kind: 'unavailable' };
  }
}

/** Generated-contract read boundary for the currently mounted Customer project catalog. */
export async function listProjectResources(
  projectPubId: string,
  kind: ProjectResourceKind,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<ProjectResourceView[]>> {
  try {
    const result = await client.GET('/api/v2/projects/{project_pub_id}/resources/{kind}', {
      params: {
        path: { project_pub_id: projectPubId, kind },
        query: { limit: 100 },
        header: headers,
      },
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

/** Generated-contract write boundary; callers must supply a fresh, non-secret idempotency key. */
export async function createProjectResource(
  projectPubId: string,
  kind: ProjectResourceKind,
  body: ProjectResourceWrite,
  headers: IdentitySessionHeaders,
  idempotencyKey: string,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<ProjectResourceView>> {
  try {
    const result = await client.POST('/api/v2/projects/{project_pub_id}/resources/{kind}', {
      params: {
        path: { project_pub_id: projectPubId, kind },
        header: { ...headers, 'Idempotency-Key': idempotencyKey },
      },
      body,
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listCustomerAccounts(
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<CustomerAccountView[]>> {
  try {
    const result = await client.GET('/api/v2/customer/platform-accounts', {
      params: { header: headers },
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getAnalyticsOverview(
  projectPubId: string,
  start: string,
  end: string,
  filters: { model?: string; region?: string; mode?: string },
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<AnalyticsOverviewResponse>> {
  try {
    const result = await client.GET('/api/v2/analytics/overview', {
      params: {
        query: {
          project_pub_id: projectPubId,
          start,
          end,
          ...(filters.model ? { model: filters.model } : {}),
          ...(filters.region ? { region: filters.region } : {}),
          ...(filters.mode ? { mode: filters.mode } : {}),
        },
        header: headers,
      },
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listAnalyticsAnswers(
  projectPubId: string,
  filters: { model?: string; region?: string; mode?: string; cursor?: string; limit?: number },
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<AnalyticsAnswerPage>> {
  try {
    const result = await client.GET('/api/v2/analytics/answers', {
      params: {
        query: {
          project_pub_id: projectPubId,
          ...(filters.model ? { model: filters.model } : {}),
          ...(filters.region ? { region: filters.region } : {}),
          ...(filters.mode ? { mode: filters.mode } : {}),
          ...(filters.cursor ? { cursor: filters.cursor } : {}),
          limit: filters.limit ?? 50,
        },
        header: headers,
      },
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listReports(
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<ReportPage>> {
  try {
    const result = await client.GET('/api/v2/reports', {
      params: { query: { limit: 50 }, header: headers },
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getReport(
  reportPubId: string,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<ReportDetail>> {
  try {
    const result = await client.GET('/api/v2/reports/{report_pub_id}', {
      params: { path: { report_pub_id: reportPubId }, header: headers },
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function reviewReport(
  reportPubId: string,
  versionPubId: string,
  body: ReportReviewCreate,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<unknown>> {
  try {
    const result = await client.POST(
      '/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/reviews',
      {
        params: {
          path: { report_pub_id: reportPubId, version_pub_id: versionPubId },
          header: headers,
        },
        body,
      },
    );
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function commentOnReport(
  reportPubId: string,
  versionPubId: string,
  body: ReportCommentCreate,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<unknown>> {
  try {
    const result = await client.POST(
      '/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/comments',
      {
        params: {
          path: { report_pub_id: reportPubId, version_pub_id: versionPubId },
          header: headers,
        },
        body,
      },
    );
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function publishReport(
  reportPubId: string,
  versionPubId: string,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<null>> {
  try {
    const result = await client.POST(
      '/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/publish',
      {
        params: {
          path: { report_pub_id: reportPubId, version_pub_id: versionPubId },
          header: headers,
        },
      },
    );
    return result.response.status === 204
      ? { kind: 'ready', data: null }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listEvidenceAssets(
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<EvidenceAssetPage>> {
  try {
    const result = await client.GET('/api/v2/evidence/assets', {
      params: { query: { limit: 50 }, header: headers },
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listInvestigations(
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<InvestigationPage>> {
  try {
    const result = await client.GET('/api/v2/intelligence/investigations', {
      params: { query: { limit: 50 }, header: headers },
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getInvestigation(
  investigationPubId: string,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<InvestigationDetail>> {
  try {
    const result = await client.GET('/api/v2/intelligence/investigations/{investigation_pub_id}', {
      params: { path: { investigation_pub_id: investigationPubId }, header: headers },
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createInvestigationVerdict(
  investigationPubId: string,
  body: VerdictCreate,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<unknown>> {
  try {
    const result = await client.POST(
      '/api/v2/intelligence/investigations/{investigation_pub_id}/verdicts',
      {
        params: { path: { investigation_pub_id: investigationPubId }, header: headers },
        body,
      },
    );
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createInvestigationAppeal(
  investigationPubId: string,
  body: AppealCreate,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<unknown>> {
  try {
    const result = await client.POST(
      '/api/v2/intelligence/investigations/{investigation_pub_id}/appeals',
      {
        params: { path: { investigation_pub_id: investigationPubId }, header: headers },
        body,
      },
    );
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function registerCustomerAccount(
  body: CustomerAccountCreate,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<CustomerAccountView>> {
  try {
    const result = await client.POST('/api/v2/customer/platform-accounts', {
      params: { header: headers },
      body,
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function authorizeCustomerAccount(
  accountPubId: string,
  body: CustomerAuthorizationCreate,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<CustomerAccountView>> {
  try {
    const result = await client.POST(
      '/api/v2/customer/platform-accounts/{account_pub_id}/authorizations',
      {
        params: { path: { account_pub_id: accountPubId }, header: headers },
        body,
      },
    );
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createCustomerPairing(
  accountPubId: string,
  body: CustomerPairingCreate,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<CustomerPairingView>> {
  try {
    const result = await client.POST(
      '/api/v2/customer/platform-accounts/{account_pub_id}/pairings',
      {
        params: { path: { account_pub_id: accountPubId }, header: headers },
        body,
      },
    );
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listCustomerPairings(
  accountPubId: string,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<CustomerPairingView[]>> {
  try {
    const result = await client.GET(
      '/api/v2/customer/platform-accounts/{account_pub_id}/pairings',
      { params: { path: { account_pub_id: accountPubId }, header: headers } },
    );
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listCustomerAccountEvents(
  accountPubId: string,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<CustomerEventView[]>> {
  try {
    const result = await client.GET('/api/v2/customer/platform-accounts/{account_pub_id}/events', {
      params: { path: { account_pub_id: accountPubId }, header: headers },
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function revokeCustomerAccount(
  accountPubId: string,
  headers: IdentitySessionHeaders,
  client: GeoApiClient = apiClient,
): Promise<ProjectResourceResult<WorkflowAccepted>> {
  try {
    const result = await client.POST('/api/v2/customer/platform-accounts/{account_pub_id}/revoke', {
      params: { path: { account_pub_id: accountPubId }, header: headers },
    });
    return result.data
      ? { kind: 'ready', data: result.data }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export type { paths };
export * from './schema.generated';
