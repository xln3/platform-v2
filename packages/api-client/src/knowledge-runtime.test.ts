import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  createGeoApiClient,
  getKnowledgeModelCatalog,
  resolveKnowledgeRuntime,
  type KnowledgeRuntimeRequest,
} from './index';

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

const headers = {
  'X-Tenant-Id': 'tnt_fixture',
  'X-Actor-Id': 'usr_fixture',
  'X-Actor-Role': 'operator',
} as const;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('knowledge runtime projected client', () => {
  it('loads the credential-free task-specific model catalog', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          status: 'ready',
          catalog_revision: 'knowledge-inference-model-catalog-20260828.2+abcdef123456',
          default_model: 'gpt-5.6-luna',
          unavailable_reason: null,
          models: [
            {
              model: 'gpt-5.6-luna',
              label: 'GPT 5.6 Luna',
              provider: 'GPT',
              model_version: 'deployment',
              capability: '知识实体判断与严格 JSON Schema 输出',
              strict_output_verified: true,
              tool_capability_status: 'not_required',
              verified_at: '2026-08-27',
              verification_reference: 'docs/knowledge/VALIDATION_REPORT_20260827.md',
              input_usd_per_million_tokens: 0.2,
              output_usd_per_million_tokens: 1.2,
              pricing_status: 'catalog_snapshot',
              pricing_currency: 'USD',
              token_price_unit: 'per_million_tokens',
              pricing_observed_at: '2026-08-25',
              pricing_source_url: 'https://api.inferera.com/api/v1/models',
              pricing_notice: 'catalog_snapshot_provider_invoice_authoritative',
              catalog_revision: 'knowledge-inference-model-catalog-20260828.2+abcdef123456',
              is_default: true,
              recommended: true,
            },
          ],
        }),
      ),
    );

    const result = await getKnowledgeModelCatalog(
      headers,
      createGeoApiClient('https://geo.example'),
    );

    expect(result).toMatchObject({
      kind: 'ready',
      data: { default_model: 'gpt-5.6-luna', models: [{ model: 'gpt-5.6-luna' }] },
    });
    expect(JSON.stringify(result)).not.toContain('api_key');
  });

  it('submits the selected model and preserves requested versus actual lineage', async () => {
    const submitted: KnowledgeRuntimeRequest[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (request: Request) => {
        submitted.push((await request.clone().json()) as KnowledgeRuntimeRequest);
        return jsonResponse({
          request_id: 'knowledge-request-1',
          domain: 'brand/entity-resolution',
          task: 'resolve-brand-identity',
          policy: 'llm_required',
          policy_id: 'operations-knowledge-runtime',
          policy_version: '1',
          release: {
            release_id: 'knowledge-2026-08-27.6',
            content_hash: `sha256:${'a'.repeat(64)}`,
            schema_version: 'knowledge-release-v1',
            source: 'local_knowledge_release',
            degraded: false,
          },
          decisions: [],
          model_hypotheses: [
            {
              input_id: 'item-1',
              input_value: '新大陆数字技术',
              value: { roll_up: { display_name: '新大陆' } },
              knowledge_status: 'model_inferred',
              decision_scope: 'request',
              confidence: 0.88,
              reasons: ['请求范围内的模型判断'],
              alternative_hypotheses: [],
              uncertainty: [],
              evidence_refs: [],
              adopted: false,
              model_provider: 'openai-compatible',
              requested_model_name: 'qwen3.7-plus',
              model_name: 'qwen3.7-plus-20260828',
              model_version: 'deployment',
              model_identity_source: 'provider_response',
              prompt_id: 'brand-entity-resolution',
              prompt_version: 'brand-entity-resolution-v5',
              knowledge_release_id: 'knowledge-2026-08-27.6',
              knowledge_content_hash: `sha256:${'a'.repeat(64)}`,
              policy_id: 'operations-knowledge-runtime',
              policy_version: '1',
              tool_summary: [],
            },
          ],
          prompt_id: 'brand-entity-resolution',
          prompt_version: 'brand-entity-resolution-v5',
          model_provider: 'openai-compatible',
          requested_model_name: 'qwen3.7-plus',
          model_name: 'qwen3.7-plus-20260828',
          model_version: 'deployment',
          model_identity_source: 'provider_response',
          model_catalog_revision: 'knowledge-inference-model-catalog-20260828.2+abcdef123456',
          model_inference_used: true,
          model_inference_adopted: false,
          provider_call_attempted: true,
          latency_ms: 35100,
          cache_status: 'miss',
          degradation: ['cost_budget_unverifiable'],
          observation_count: 1,
          usage: {
            input_tokens: 7831,
            output_tokens: 1937,
            cost_usd: null,
            model_latency_ms: 35094,
            tool_summary: [],
          },
        });
      }),
    );
    const body: KnowledgeRuntimeRequest = {
      namespace: 'shared',
      domain: 'brand/entity-resolution',
      task: 'resolve-brand-identity',
      items: [{ id: 'item-1', value: '新大陆数字技术' }],
      context: { analysis_domain: 'cybersecurity', comparison_scopes: ['ctid'] },
      policy: 'llm_required',
      policy_id: 'operations-knowledge-runtime',
      policy_version: '1',
      adopt_model_inferred: false,
      on_model_failure: 'degrade',
      data_classification: 'public',
      allow_external_model: true,
      model: 'qwen3.7-plus',
    };

    const result = await resolveKnowledgeRuntime(
      body,
      headers,
      createGeoApiClient('https://geo.example'),
    );

    expect(submitted).toMatchObject([{ model: 'qwen3.7-plus' }]);
    expect(result).toMatchObject({
      kind: 'ready',
      data: {
        requested_model_name: 'qwen3.7-plus',
        model_name: 'qwen3.7-plus-20260828',
        model_identity_source: 'provider_response',
        usage: { cost_usd: null },
      },
    });
  });

  it('returns a stable rejection code for a model outside the allow-list', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: { code: 'knowledge_model_not_allowed' } }, 422)),
    );

    const result = await resolveKnowledgeRuntime(
      {
        namespace: 'shared',
        domain: 'brand/entity-resolution',
        task: 'resolve-brand-identity',
        items: [{ id: 'item-1', value: '示例品牌' }],
        policy: 'llm_required',
        policy_id: 'operations-knowledge-runtime',
        policy_version: '1',
        adopt_model_inferred: false,
        on_model_failure: 'degrade',
        data_classification: 'public',
        allow_external_model: true,
        model: 'browser-injected-model',
      },
      headers,
      createGeoApiClient('https://geo.example'),
    );

    expect(result).toEqual({ kind: 'rejected', code: 'knowledge_model_not_allowed' });
  });

  it('preserves a sanitized model failure code from a fail-fast 5xx response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: { code: 'model_timeout' } }, 503)),
    );

    const result = await resolveKnowledgeRuntime(
      {
        namespace: 'shared',
        domain: 'brand/entity-resolution',
        task: 'resolve-brand-identity',
        items: [{ id: 'item-1', value: '示例品牌' }],
        policy: 'llm_required',
        policy_id: 'operations-knowledge-runtime',
        policy_version: '1',
        adopt_model_inferred: false,
        on_model_failure: 'fail',
        data_classification: 'public',
        allow_external_model: true,
        model: 'gpt-5.6-luna',
      },
      headers,
      createGeoApiClient('https://geo.example'),
    );

    expect(result).toEqual({ kind: 'failed', code: 'model_timeout' });
  });
});
