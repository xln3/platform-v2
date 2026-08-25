import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  getOperationsBusinessOverview,
  projectOperationsBusinessOverviewRequestHeaders,
  projectOperationsBusinessOverview,
} from './business-overview';

const headers = {
  'X-Tenant-Id': 'tnt_business_safe',
  'X-Actor-Id': 'operator-business-safe',
  'X-Actor-Role': 'operator',
};

const payload = () => ({
  schema_version: 1,
  as_of: '2026-08-24T10:30:00Z',
  summary: {
    scope: 'filtered',
    tenant_project_count: 1,
    project_count: 1,
    project_state_counts: { draft: 0, active: 1, paused: 0, archived: 0 },
    setup_ready_project_count: 1,
    project_with_entitlement_record_count: 1,
    active_entitlement_count: 1,
    attention_project_count: 1,
  },
  commercial_capabilities: {
    quotation_history: 'unsupported',
    signed_contract_ledger: 'unsupported',
    invoice_receivable_payment_ledger: 'unsupported',
  },
  items: [
    {
      project: { id: 'prj_business_safe', name: '安全项目', state: 'active' },
      customer: { id: 'cst_business_safe', name: '安全客户' },
      setup: {
        client_profile_revision: 2,
        asset_confirmation_revision: 3,
        frozen_monitoring_config_revision: 4,
        setup_ready: true,
        intake_profile_exists: true,
        intake_truth_confirmed: false,
      },
      service_entitlements: [
        {
          service_code: 'ranking_test',
          service_name: 'AI 推荐排名效果测试',
          state: 'active',
          authorized_from: '2026-08-01T00:00:00Z',
          authorized_until: '2026-09-01T00:00:00Z',
          effective_now: true,
        },
      ],
      collection: {
        active_count: 0,
        failed_count: 0,
        delayed_count: 0,
        latest_state: 'completed',
        latest_at: '2026-08-24T09:00:00Z',
      },
      formal_report: {
        production_count: 1,
        latest_state: 'signed',
        latest_at: '2026-08-24T09:30:00Z',
      },
      delivery: {
        delivered_at: null,
        confirmed_at: null,
        pending_confirmation_count: 0,
      },
      contract_draft_export: null,
      primary_attention: {
        code: 'intake_truth_confirmation_required',
        severity: 'warning',
        additional_count: 0,
      },
      last_business_fact_at: '2026-08-24T09:30:00Z',
    },
  ],
  page: { limit: 4, next_cursor: null, has_more: false, filtered_total: 1 },
});

afterEach(() => vi.restoreAllMocks());

describe('Operations business overview browser boundary', () => {
  it('accepts both current and legacy customer public-ID prefixes present in production', () => {
    const legacyCustomer = payload();
    legacyCustomer.items[0]!.customer.id = 'cus_business_safe';
    expect(projectOperationsBusinessOverview(legacyCustomer)?.items[0]?.customer.id).toBe(
      'cus_business_safe',
    );
  });

  it('keeps validated actor headers local in production and forwards them only to fixtures', () => {
    expect(
      projectOperationsBusinessOverviewRequestHeaders(headers, {
        DEV: false,
        VITE_ALLOW_CONTRACT_FIXTURES: 'false',
      }),
    ).toEqual({ Accept: 'application/json' });
    expect(
      projectOperationsBusinessOverviewRequestHeaders(headers, {
        DEV: true,
        VITE_ALLOW_CONTRACT_FIXTURES: 'false',
      }),
    ).toEqual({ Accept: 'application/json', ...headers });
    expect(
      projectOperationsBusinessOverviewRequestHeaders(
        { ...headers, 'X-Actor-Role': 'invalid-role' },
        { DEV: true },
      ),
    ).toBeNull();
  });

  it('loads one strict response and preserves false versus missing facts', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      expect(url.pathname).toBe('/api/v2/operations/business-overview');
      expect(url.searchParams.get('limit')).toBe('4');
      expect(url.searchParams.get('project_state')).toBe('active');
      return new Response(JSON.stringify(payload()), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    const result = await getOperationsBusinessOverview(
      headers,
      { projectState: 'active' },
      { baseUrl: 'https://geo.example', fetcher: request },
    );
    expect(result.kind).toBe('ready');
    if (result.kind !== 'ready') throw new Error('expected ready result');
    expect(result.data.items[0]?.setup.intakeTruthConfirmed).toBe(false);
    expect(result.data.items[0]?.formalReport.latestState).toBe('signed');
    expect(JSON.stringify(result.data)).not.toContain('合同已签');
  });

  it('accepts a strict empty response without converting it into an error', () => {
    const candidate = payload();
    candidate.summary = {
      scope: 'filtered',
      tenant_project_count: 0,
      project_count: 0,
      project_state_counts: { draft: 0, active: 0, paused: 0, archived: 0 },
      setup_ready_project_count: 0,
      project_with_entitlement_record_count: 0,
      active_entitlement_count: 0,
      attention_project_count: 0,
    };
    candidate.items = [];
    candidate.page = { limit: 4, next_cursor: null, has_more: false, filtered_total: 0 };
    const projected = projectOperationsBusinessOverview(candidate);
    expect(projected?.summary.tenantProjectCount).toBe(0);
    expect(projected?.items).toEqual([]);
  });

  it.each([
    [
      'extra secret field',
      (value: ReturnType<typeof payload>) => Object.assign(value, { token: 'Bearer canary' }),
    ],
    [
      'unknown enum',
      (value: ReturnType<typeof payload>) => (value.items[0]!.project.state = 'unknown'),
    ],
    ['negative count', (value: ReturnType<typeof payload>) => (value.summary.project_count = -1)],
    [
      'timezone-free time',
      (value: ReturnType<typeof payload>) => (value.as_of = '2026-08-24T10:30:00'),
    ],
    [
      'duplicate service',
      (value: ReturnType<typeof payload>) =>
        value.items[0]!.service_entitlements.push({ ...value.items[0]!.service_entitlements[0]! }),
    ],
    [
      'secret canary',
      (value: ReturnType<typeof payload>) =>
        (value.items[0]!.customer.name = 'Cookie=secret-canary'),
    ],
    [
      'hostile public ID',
      (value: ReturnType<typeof payload>) =>
        (value.items[0]!.project.id = 'prj_safe/../../operations'),
    ],
    [
      'overlarge count',
      (value: ReturnType<typeof payload>) => (value.summary.project_count = 1_000_000_001),
    ],
    [
      'overlong text',
      (value: ReturnType<typeof payload>) => (value.items[0]!.project.name = '项目'.repeat(101)),
    ],
    [
      'C1 control',
      (value: ReturnType<typeof payload>) => (value.items[0]!.customer.name = '客户\u0085伪装'),
    ],
    [
      'Unicode line separator',
      (value: ReturnType<typeof payload>) => (value.items[0]!.customer.name = '客户\u2028伪装'),
    ],
    [
      'bidi override',
      (value: ReturnType<typeof payload>) => (value.items[0]!.project.name = '安全\u202e伪装'),
    ],
    [
      'bidi isolate',
      (value: ReturnType<typeof payload>) => (value.items[0]!.project.name = '安全\u2066伪装'),
    ],
  ])('rejects %s', (_label, mutate) => {
    const candidate = payload();
    mutate(candidate);
    expect(projectOperationsBusinessOverview(candidate)).toBeNull();
  });

  it('classifies permission failures and rejects unsafe query input before fetch', async () => {
    const forbidden = vi.fn(async () => new Response('{}', { status: 403 }));
    await expect(
      getOperationsBusinessOverview(
        headers,
        {},
        { baseUrl: 'https://geo.example', fetcher: forbidden },
      ),
    ).resolves.toEqual({ kind: 'forbidden' });
    const untouched = vi.fn();
    await expect(
      getOperationsBusinessOverview(
        headers,
        { q: 'password=secret' },
        { baseUrl: 'https://geo.example', fetcher: untouched },
      ),
    ).resolves.toEqual({ kind: 'unavailable' });
    await expect(
      getOperationsBusinessOverview(
        headers,
        { q: '客户\u202e伪装' },
        { baseUrl: 'https://geo.example', fetcher: untouched },
      ),
    ).resolves.toEqual({ kind: 'unavailable' });
    expect(untouched).not.toHaveBeenCalled();
  });

  it.each([
    [401, 'forbidden'],
    [404, 'unavailable'],
    [422, 'unavailable'],
    [500, 'unavailable'],
  ] as const)('maps HTTP %s to %s', async (status, kind) => {
    const request = vi.fn(async () => new Response('{}', { status }));
    await expect(
      getOperationsBusinessOverview(
        headers,
        {},
        { baseUrl: 'https://geo.example', fetcher: request },
      ),
    ).resolves.toEqual({ kind });
  });

  it('maps network failure to unavailable', async () => {
    const request = vi.fn(async (): Promise<Response> => {
      throw new TypeError('network unavailable');
    });
    await expect(
      getOperationsBusinessOverview(
        headers,
        {},
        { baseUrl: 'https://geo.example', fetcher: request },
      ),
    ).resolves.toEqual({ kind: 'unavailable' });
  });
});
