import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  authorizeCustomerAccount,
  createCustomerPairing,
  createGeoApiClient,
  createProjectResource,
  getAnalyticsOverview,
  getIdentitySession,
  getInvestigation,
  getReport,
  listAnalyticsAnswers,
  listCustomerAccountEvents,
  listCustomerAccounts,
  listCustomerPairings,
  listEvidenceAssets,
  listInvestigations,
  listProjectResources,
  listReports,
  registerCustomerAccount,
  revokeCustomerAccount,
  type HealthResponse,
} from './index';

afterEach(() => vi.unstubAllGlobals());

describe('generated client', () => {
  it('exports the health contract', () => {
    const value: HealthResponse = {
      status: 'ok',
      service: 'geo-platform-v2',
      version: 'contract-v1',
    };
    expect(value.status).toBe('ok');
  });

  it('executes requests through generated OpenAPI paths', async () => {
    const request = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(
          JSON.stringify({
            status: 'ok',
            service: 'geo-platform-v2',
            version: 'contract-v1',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
    );
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const { data: result } = await client.GET('/api/v2/health');
    expect(result).toBeTruthy();
    if (!result) throw new Error('missing health response');
    expect(result.status).toBe('ok');
    expect(request.mock.calls[0]?.[0]).toBeInstanceOf(Request);
    expect((request.mock.calls[0]?.[0] as Request).url).toMatch(/\/api\/v2\/health$/);
    expect(client).toBeTruthy();
  });

  it('validates identity and project context through generated contracts', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const url = (input as Request).url;
      const body = url.endsWith('/identity/session')
        ? {
            tenant_pub_id: 'tnt_safe',
            user_pub_id: 'usr_safe',
            role: 'customer',
            permissions: ['project:read', 'account:authorize'],
          }
        : {
            data: [
              {
                pub_id: 'prj_safe',
                tenant_pub_id: 'tnt_safe',
                name: '安全项目',
                state: 'active',
                created_at: '2026-07-24T00:00:00Z',
                updated_at: '2026-07-24T00:00:00Z',
              },
            ],
            page: { next_cursor: null, has_more: false },
          };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const result = await getIdentitySession(
      {
        'X-Tenant-Id': 'tnt_safe',
        'X-Actor-Id': 'customer@example.test',
        'X-Actor-Role': 'customer',
      },
      client,
    );
    expect(result.kind).toBe('ready');
    if (result.kind !== 'ready') throw new Error('missing validated session');
    expect(result.session.role).toBe('customer');
    expect(result.projects.data[0]?.pub_id).toBe('prj_safe');
    expect((request.mock.calls[0]?.[0] as Request).headers.get('X-Service-Token')).toBeNull();
  });

  it('fails closed on an unauthorized identity without probing projects', async () => {
    const request = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: { code: 'membership_invalid' } }), {
          status: 401,
          headers: { 'content-type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', request);
    const result = await getIdentitySession(
      {
        'X-Tenant-Id': 'tnt_safe',
        'X-Actor-Id': 'unknown@example.test',
        'X-Actor-Role': 'customer',
      },
      createGeoApiClient('http://127.0.0.1:45200'),
    );
    expect(result).toEqual({ kind: 'forbidden' });
    expect(request).toHaveBeenCalledTimes(1);
  });

  it('reads and writes project catalog resources through generated paths and headers', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const resource = {
        pub_id: 'ent_brand_safe',
        project_pub_id: 'prj_safe',
        resource_kind: 'brands',
        version: 1,
        data: { name: '澄明云', website: 'https://example.test' },
      };
      return new Response(JSON.stringify(outbound.method === 'GET' ? [resource] : resource), {
        status: outbound.method === 'GET' ? 200 : 201,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer@example.test',
      'X-Actor-Role': 'customer' as const,
    };
    const listed = await listProjectResources('prj_safe', 'brands', headers, client);
    expect(listed.kind).toBe('ready');
    if (listed.kind !== 'ready') throw new Error('missing catalog response');
    expect(listed.data[0]?.data.name).toBe('澄明云');

    const created = await createProjectResource(
      'prj_safe',
      'brands',
      { name: '澄明云', website: 'https://example.test' },
      headers,
      'customer-brand-00000001',
      client,
    );
    expect(created.kind).toBe('ready');
    expect(request).toHaveBeenCalledTimes(2);
    const writeRequest = request.mock.calls[1]?.[0] as Request;
    expect(writeRequest.url).toContain('/api/v2/projects/prj_safe/resources/brands');
    expect(writeRequest.headers.get('Idempotency-Key')).toBe('customer-brand-00000001');
    expect(writeRequest.headers.get('X-Service-Token')).toBeNull();
    expect(await writeRequest.clone().json()).toEqual({
      name: '澄明云',
      website: 'https://example.test',
    });
  });

  it('classifies catalog authorization failures without returning response details', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              detail: {
                code: 'project_forbidden',
                token: 'Bearer should-never-cross-the-boundary',
              },
            }),
            { status: 403, headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
    const result = await listProjectResources(
      'prj_hidden',
      'brands',
      {
        'X-Tenant-Id': 'tnt_safe',
        'X-Actor-Id': 'customer@example.test',
        'X-Actor-Role': 'customer',
      },
      createGeoApiClient('http://127.0.0.1:45200'),
    );
    expect(result).toEqual({ kind: 'forbidden' });
    expect(JSON.stringify(result)).not.toContain('Bearer');
  });

  it('uses only generated customer-account paths and validated browser headers', async () => {
    const account = {
      pub_id: 'pac_safe',
      account_mask: '尾号 · 4821',
      platform_label: '豆包',
      owner_label: '当前客户',
      custody_mode: 'customer_device',
      admission_level: 'adapter_ready',
      scopes: ['read'],
      authorization_expires_at: '2026-12-31T15:59:59Z',
      region_label: '中国大陆 · 华北',
      session_health: 'challenge_required',
      last_verified_at: null,
      intervention_status: 'pending',
      revocation_receipt_pub_id: null,
      revoked_at: null,
    };
    const pairing = {
      pub_id: 'int_safe',
      account_pub_id: 'pac_safe',
      account_mask: '尾号 · 4821',
      allowed_domain: 'doubao.com',
      action: 'read',
      challenge_type: 'qr',
      state: 'pending',
      expires_at: null,
    };
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const url = outbound.url;
      const body = url.endsWith('/events')
        ? [
            {
              pub_id: 'sev_safe',
              event_type: 'customer_pairing.requested',
              occurred_at: '2026-07-24T12:00:00Z',
            },
          ]
        : url.endsWith('/pairings')
          ? outbound.method === 'GET'
            ? [pairing]
            : pairing
          : url.endsWith('/revoke')
            ? { workflow_id: 'account-revocation/safe', run_id: 'run_safe' }
            : url.endsWith('/authorizations')
              ? account
              : outbound.method === 'GET'
                ? [account]
                : account;
      const status = url.endsWith('/revoke') ? 202 : outbound.method === 'POST' ? 201 : 200;
      return new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer-safe',
      'X-Actor-Role': 'customer' as const,
    };

    expect((await listCustomerAccounts(headers, client)).kind).toBe('ready');
    expect(
      (
        await registerCustomerAccount(
          {
            platform_slug: 'doubao',
            platform_name: '豆包',
            account_mask: '尾号 · 4821',
            custody_mode: 'customer_device',
            region: '中国大陆 · 华北',
          },
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    expect(
      (
        await authorizeCustomerAccount(
          'pac_safe',
          {
            scopes: ['read'],
            forbidden_actions: ['delete'],
            regions: ['中国大陆 · 华北'],
            valid_until: '2026-12-31T15:59:59Z',
          },
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    expect(
      (
        await createCustomerPairing(
          'pac_safe',
          { allowed_domain: 'doubao.com', action: 'read', challenge_type: 'qr' },
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    expect((await listCustomerPairings('pac_safe', headers, client)).kind).toBe('ready');
    expect((await listCustomerAccountEvents('pac_safe', headers, client)).kind).toBe('ready');
    expect((await revokeCustomerAccount('pac_safe', headers, client)).kind).toBe('ready');

    expect(request).toHaveBeenCalledTimes(7);
    for (const call of request.mock.calls) {
      const outbound = call[0] as Request;
      expect(outbound.url).toContain('/api/v2/customer/platform-accounts');
      expect(outbound.headers.get('X-Tenant-Id')).toBe('tnt_safe');
      expect(outbound.headers.get('X-Actor-Id')).toBe('customer-safe');
      expect(outbound.headers.get('X-Service-Token')).toBeNull();
    }
  });

  it('reads mounted S02 product projections only through generated paths', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const url = new URL(outbound.url);
      const body = url.pathname.endsWith('/analytics/overview')
        ? []
        : url.pathname.endsWith('/analytics/answers')
          ? { data: [], page: { next_cursor: null, has_more: false } }
          : url.pathname.endsWith('/evidence/assets')
            ? { data: [], page: { next_cursor: null, has_more: false } }
            : url.pathname.endsWith('/reports/rpt_safe')
              ? { pub_id: 'rpt_safe', versions: [] }
              : url.pathname.endsWith('/reports')
                ? { data: [], page: { next_cursor: null, has_more: false } }
                : url.pathname.endsWith('/investigations/inv_safe')
                  ? { pub_id: 'inv_safe', claims: [], evidence_matrix: [] }
                  : { data: [], page: { next_cursor: null, has_more: false } };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'analyst-safe',
      'X-Actor-Role': 'analyst' as const,
    };

    expect(
      (await getAnalyticsOverview('prj_safe', '2026-07-01', '2026-07-25', {}, headers, client))
        .kind,
    ).toBe('ready');
    expect((await listAnalyticsAnswers('prj_safe', {}, headers, client)).kind).toBe('ready');
    expect((await listEvidenceAssets(headers, client)).kind).toBe('ready');
    expect((await listReports(headers, client)).kind).toBe('ready');
    expect((await getReport('rpt_safe', headers, client)).kind).toBe('ready');
    expect((await listInvestigations(headers, client)).kind).toBe('ready');
    expect((await getInvestigation('inv_safe', headers, client)).kind).toBe('ready');

    expect(request).toHaveBeenCalledTimes(7);
    for (const call of request.mock.calls) {
      const outbound = call[0] as Request;
      expect(outbound.url).toContain('/api/v2/');
      expect(outbound.headers.get('X-Tenant-Id')).toBe('tnt_safe');
      expect(outbound.headers.get('X-Service-Token')).toBeNull();
    }
  });
});
