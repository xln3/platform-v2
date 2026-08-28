// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { ExperienceProvider } from '@geo/design-system';
import { customerMetricNamesV2, projectCustomerMetricV2Boundary } from '@geo/api-client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CustomerAnalyticsWorkspace } from './customer-dashboard';

const apiSpies = vi.hoisted(() => ({
  dashboardV2: vi.fn(),
  legacyDashboard: vi.fn(),
  legacyCatalog: vi.fn(),
}));

vi.mock('@geo/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@geo/api-client')>()),
  getCustomerDashboardV2: apiSpies.dashboardV2,
  getCustomerDashboard: apiSpies.legacyDashboard,
  getCustomerMetricCatalog: apiSpies.legacyCatalog,
}));

vi.mock('@geo/auth', () => ({
  getValidatedIdentityHeaders: () => ({}),
}));

const hash = 'a'.repeat(64);

const metric = (name: string, index: number) => ({
  snapshot_pub_id: `msn_customer_${index}`,
  snapshot_hash: hash,
  focal_entity_id: 'entity_target',
  metric_name: name,
  metric_version: '2.0.0',
  metric_definition_hash: hash,
  state: 'ready',
  state_reason_codes: [],
  value: 0.5,
  observed_value: 0.5,
  answer_weighted_value: 0.5,
  raw_numerator: 1,
  raw_denominator: 2,
  weighted_numerator: 0.5,
  weighted_denominator: 1,
  coverage: {
    collection: 1,
    query_context: 1,
    semantic: 1,
    evidence: 1,
    semantic_by_capability: { rank_semantics: 1 },
  },
  decision_method_mix: { hybrid: 1 },
  adjudication_sensitivity: { lower: 0.48, upper: 0.52 },
  missing_bounds: { lower: 0.5, upper: 0.5 },
  unique_query_count: 2,
  candidate_answer_count: 2,
  known_answer_count: 2,
  unknown_answer_count: 0,
  not_applicable_answer_count: 0,
  excluded_answer_count: 0,
  design_cell_count: 2,
  contribution_set_hash: hash,
  query_contribution_set_hash: hash,
  design_contribution_set_hash: hash,
  label: name,
  business_view: 'ai_recommendation',
  exposure_role: 'brand_neutral',
  aggregation_method: 'query_macro',
  definition: {
    business_question: `业务问题：${name}`,
    denominator_description: '同一暴露 cohort 中语义已知的有效回答。',
    outcome_source: 'hybrid',
    query_predicate: { exposure_is: 'brand_neutral' },
    outcome_expression: { event_exists: { type: 'recommendation_relation' } },
    required_semantic_capabilities: ['rank_semantics'],
    decision_task_refs: [{ task_ref: 'rank-semantics@2.0.0' }],
    semantic_rubric_ref: 'rubric://rank/2.0.0',
  },
});

describe('CustomerAnalyticsWorkspace V2', () => {
  beforeEach(() => {
    history.replaceState(null, '', '/platform/customer/?window=30d');
    apiSpies.dashboardV2.mockReset();
    apiSpies.legacyDashboard.mockReset();
    apiSpies.legacyCatalog.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('uses only the explicit dashboard-v2 cohort read for a live customer', async () => {
    expect(
      customerMetricNamesV2.ai_recommendation.brand_neutral.map((name, index) =>
        projectCustomerMetricV2Boundary(
          metric(name, index),
          'ai_recommendation',
          'brand_neutral',
          name,
        ),
      ),
    ).not.toContain(null);
    apiSpies.dashboardV2.mockImplementation(
      async (
        projectPubId: string,
        start: string,
        end: string,
        selection: {
          businessView: string;
          exposureRole: string;
          metricNames: readonly string[];
        },
      ) => ({
        kind: 'ready',
        data: {
          schema_version: 'customer-dashboard-v2',
          project_pub_id: projectPubId,
          brand_name: '盛邦安全',
          business_view: selection.businessView,
          exposure_role: selection.exposureRole,
          publication_channel: 'official',
          requested_metric_names: [...selection.metricNames],
          focal_entity_id: 'entity_target',
          snapshot_set_pub_id: 'mss_customer_safe',
          snapshot_set_hash: hash,
          state: 'ready',
          as_of: '2026-08-27T08:00:00Z',
          window: { start, end },
          filters: { model: [], region: [], mode: [] },
          aggregation_method: 'query_macro',
          design_basis: 'planned_cells',
          scope_hash: hash,
          dependency_bundle_hash: hash,
          metrics: selection.metricNames.map(metric),
        },
      }),
    );

    render(
      <ExperienceProvider
        value={{
          tenantPubId: 'tnt_customer_safe',
          tenantLabel: '客户租户',
          projectPubId: 'prj_customer_safe',
          projectLabel: '客户项目',
          userPubId: 'usr_customer_safe',
          userLabel: '客户用户',
          roles: ['customer'],
          source: 'live',
        }}
      >
        <CustomerAnalyticsWorkspace focus="overview" />
      </ExperienceProvider>,
    );

    await waitFor(() => expect(apiSpies.dashboardV2).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/盛邦安全 · AI 推荐/u)).toBeTruthy();
    expect(screen.getByRole('group', { name: '业务入口' })).toBeTruthy();
    expect(screen.getByRole('group', { name: '品牌暴露 cohort' })).toBeTruthy();
    expect(screen.getByText('mss_customer_safe')).toBeTruthy();
    const call = apiSpies.dashboardV2.mock.calls[0];
    expect(call?.[0]).toBe('prj_customer_safe');
    expect(call?.[3]).toMatchObject({
      businessView: 'ai_recommendation',
      exposureRole: 'brand_neutral',
      metricNames: customerMetricNamesV2.ai_recommendation.brand_neutral,
    });
    expect(apiSpies.legacyDashboard).not.toHaveBeenCalled();
    expect(apiSpies.legacyCatalog).not.toHaveBeenCalled();
  });

  it('fails closed instead of mounting the legacy workspace when a live project is absent', () => {
    render(
      <ExperienceProvider
        value={{
          tenantPubId: 'tnt_customer_safe',
          tenantLabel: '客户租户',
          projectPubId: '',
          projectLabel: '',
          userPubId: 'usr_customer_safe',
          userLabel: '客户用户',
          roles: ['customer'],
          source: 'live',
        }}
      >
        <CustomerAnalyticsWorkspace focus="overview" />
      </ExperienceProvider>,
    );

    expect(screen.getByText('加载失败', { exact: true })).toBeTruthy();
    expect(apiSpies.dashboardV2).not.toHaveBeenCalled();
    expect(apiSpies.legacyDashboard).not.toHaveBeenCalled();
    expect(apiSpies.legacyCatalog).not.toHaveBeenCalled();
  });
});
