// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { OutboundRiskWorkspace } from './features/services/service-outbound-risk/OutboundRiskWorkspace';
import { operationsNav } from './shell';

const session = {
  tenantId: 'tnt_fixture',
  actorId: 'usr_fixture',
  role: 'operator' as const,
  headers: {
    'X-Tenant-Id': 'tnt_fixture',
    'X-Actor-Id': 'usr_fixture',
    'X-Actor-Role': 'operator',
  },
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('operations UVW information architecture', () => {
  it('places every collection lifecycle entry under collection and splits services 2 and 3', () => {
    const byId = new Map(operationsNav.map((item) => [item.id, item]));

    for (const id of ['execution', 'accounts', 'browsers', 'sessions', 'interventions', 'events']) {
      expect(byId.get(id)?.group).toBe('采集');
    }
    expect(byId.get('service-outbound-risk')?.group).toBe('五项服务生产');
    expect(byId.get('service-inbound-risk')?.group).toBe('五项服务生产');
    expect(byId.has('service-risk')).toBe(false);
    expect(operationsNav.some((item) => item.group === '系统运营')).toBe(false);
  });

  it('defines service 2 from the frozen all-U scope independently of attribution', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const request = input instanceof Request ? input : new Request(input);
        if (
          request.url.includes('/service2-source-corpus/') &&
          request.url.endsWith('/analysis-models')
        ) {
          return Response.json({
            default_model: 'fast-model',
            models: [
              {
                model: 'fast-model',
                label: 'Fast Model',
                provider: 'fixture',
                tier: 'economy',
                capability: '全量初筛',
                web_search_mode: 'fixture_search',
                input_usd_per_million_tokens: 0.2,
                output_usd_per_million_tokens: 1.2,
                context_window_tokens: 1000000,
                web_search_audit_status: 'verified_provider_citation',
                web_search_audited_at: '2026-08-25',
                auditable_source_mode: 'provider_citation',
                recommended: true,
                catalog_revision: 'fixture-v1',
                pricing_observed_at: '2026-08-25',
                pricing_source_url: 'https://example.com/models',
                pricing_currency: 'USD',
                token_price_unit: 'per_million_tokens',
                web_search_usd_per_call: null,
                web_search_pricing_status: 'not_published_in_catalog_snapshot',
                pricing_notice: 'catalog_snapshot_provider_invoice_authoritative',
                web_search_audit_policy: 'provider_search_event_and_provider_citation_required',
              },
            ],
            credential_source: 'server_environment_only',
          });
        }
        if (request.url.includes('/service2-source-corpus/')) {
          return new Response(JSON.stringify({ error: { code: 'service2_batch_not_found' } }), {
            status: 404,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        if (request.url.includes('/api/v2/collection/runs')) {
          return new Response(JSON.stringify([]), {
            status: 200,
            headers: { 'Content-Type': 'application/json', 'X-Has-More': 'false' },
          });
        }
        throw new Error(`unexpected request: ${request.url}`);
      }),
    );

    render(
      <OutboundRiskWorkspace
        session={session}
        project={{
          pub_id: 'prj_fixture',
          name: 'Fixture Project',
          state: 'active',
          updated_at: '2026-08-20T00:00:00Z',
        }}
      />,
    );

    expect(await screen.findByText('建立全 U 核查批次')).toBeTruthy();
    expect(screen.getByText(/不按作者、委托、己方\/竞品归属/)).toBeTruthy();
    expect(screen.queryByText(/本页不读取互联网 U 页面/)).toBeNull();
  });
});
