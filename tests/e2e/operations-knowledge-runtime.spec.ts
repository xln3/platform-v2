import { expect, test } from './runtime-fixture';

const tenantPubId = 'tnt_knowledge_browser';
const projectPubId = 'prj_knowledge_browser_context';
const catalogRevision = 'knowledge-inference-model-catalog-20260828.2+browserfixture';

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  };
}

function runtimeResponse(body: Record<string, unknown>) {
  const policy = String(body.policy);
  const requestedModel = typeof body.model === 'string' ? body.model : null;
  const modelUsed = policy !== 'deterministic_only';
  const actualModel = requestedModel === 'qwen3.7-plus' ? 'qwen3.7-plus-20260828' : requestedModel;
  return {
    request_id: `browser-${requestedModel ?? 'deterministic'}`,
    domain: 'brand/entity-resolution',
    task: 'resolve-brand-identity',
    policy,
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
    model_hypotheses: modelUsed
      ? [
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
            adopted: Boolean(body.adopt_model_inferred),
            model_provider: 'openai-compatible',
            requested_model_name: requestedModel,
            model_name: actualModel,
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
        ]
      : [],
    prompt_id: modelUsed ? 'brand-entity-resolution' : null,
    prompt_version: modelUsed ? 'brand-entity-resolution-v5' : null,
    model_provider: modelUsed ? 'openai-compatible' : null,
    requested_model_name: requestedModel,
    model_name: actualModel,
    model_version: modelUsed ? 'deployment' : null,
    model_identity_source: modelUsed ? 'provider_response' : null,
    model_catalog_revision: modelUsed ? catalogRevision : null,
    model_inference_used: modelUsed,
    model_inference_adopted: modelUsed && Boolean(body.adopt_model_inferred),
    provider_call_attempted: modelUsed,
    latency_ms: modelUsed ? 35_100 : 4,
    cache_status: modelUsed ? 'miss' : 'bypass',
    degradation: requestedModel === 'qwen3.7-plus' ? ['cost_budget_unverifiable'] : [],
    observation_count: modelUsed ? 1 : 0,
    usage: {
      input_tokens: modelUsed ? 7_831 : null,
      output_tokens: modelUsed ? 1_937 : null,
      cost_usd: requestedModel === 'gpt-5.6-luna' ? 0.0004 : null,
      model_latency_ms: modelUsed ? 35_094 : null,
      tool_summary: [],
    },
  };
}

test('formal knowledge workspace submits default, alternate, and deterministic model boundaries', async ({
  page,
}) => {
  const submitted: Array<Record<string, unknown>> = [];
  await page.addInitScript(
    ({ tenant }) => {
      localStorage.setItem('geo.session.tenant', tenant);
      localStorage.setItem('geo.session.actor', 'analyst-knowledge-browser');
      localStorage.setItem('geo.session.role', 'analyst');
    },
    { tenant: tenantPubId },
  );
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill(
      json({
        tenant_pub_id: tenantPubId,
        user_pub_id: 'usr_knowledge_browser',
        role: 'analyst',
        permissions: ['project:read', 'knowledge:read', 'knowledge:resolve'],
      }),
    ),
  );
  await page.route(/\/api\/v2\/projects(?:\?.*)?$/u, (route) =>
    route.fulfill(
      json({
        data: [
          {
            pub_id: projectPubId,
            tenant_pub_id: tenantPubId,
            name: '知识判断验收项目',
            state: 'active',
            brandrank_domain: 'digital_identity',
            created_at: '2026-08-27T00:00:00Z',
            updated_at: '2026-08-28T00:00:00Z',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    ),
  );
  await page.route('**/api/v2/knowledge/v1/models', (route) =>
    route.fulfill(
      json({
        status: 'ready',
        catalog_revision: catalogRevision,
        default_model: 'gpt-5.6-luna',
        unavailable_reason: null,
        models: [
          {
            model: 'gpt-5.6-luna',
            label: 'GPT 5.6 Luna',
            provider: 'GPT',
            model_version: 'deployment',
            capability: '知识实体判断、长上下文与严格 JSON Schema 输出',
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
            catalog_revision: catalogRevision,
            is_default: true,
            recommended: true,
          },
          {
            model: 'qwen3.7-plus',
            label: 'Qwen 3.7 Plus',
            provider: 'Qwen',
            model_version: 'deployment',
            capability: '知识实体判断与严格 JSON Schema 输出',
            strict_output_verified: true,
            tool_capability_status: 'not_required',
            verified_at: '2026-08-28',
            verification_reference:
              'docs/knowledge/evidence/knowledge-model-admission-20260828.json',
            input_usd_per_million_tokens: null,
            output_usd_per_million_tokens: null,
            pricing_status: 'unknown',
            pricing_currency: 'USD',
            token_price_unit: 'per_million_tokens',
            pricing_observed_at: null,
            pricing_source_url: null,
            pricing_notice: 'catalog_snapshot_provider_invoice_authoritative',
            catalog_revision: catalogRevision,
            is_default: false,
            recommended: false,
          },
        ],
      }),
    ),
  );
  await page.route('**/api/v2/knowledge/v1/runtime/resolve', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    submitted.push(body);
    await route.fulfill(json(runtimeResponse(body)));
  });

  await page.goto(`/platform/operations/knowledge-runtime?project=${projectPubId}`);
  await expect(
    page.getByRole('heading', { name: '共享知识判断', exact: true }).last(),
  ).toBeVisible();
  await expect(page.getByRole('combobox', { name: '项目' })).toHaveValue(projectPubId);
  await page.getByLabel('待判断实体（每行一个）').fill('新大陆数字技术');
  await page.getByLabel('推理策略').selectOption('llm_required');
  const modelSelect = page.getByLabel('共享知识推理模型选择');
  await expect(modelSelect).toHaveValue('gpt-5.6-luna');
  await page.getByLabel('允许本次请求调用外部模型').check();
  await page.getByLabel('允许模型假设影响本次有效结果').check();
  await page.getByRole('button', { name: '执行知识判断' }).click();
  await expect(page.getByLabel('知识判断结果')).toContainText('gpt-5.6-luna');

  await modelSelect.selectOption('qwen3.7-plus');
  await page.getByRole('button', { name: '执行知识判断' }).click();
  const qwenResult = page.getByLabel('知识判断结果');
  await expect(qwenResult).toContainText('qwen3.7-plus-20260828');
  await expect(qwenResult).toContainText('供应商实际模型与所选模型不同');
  await expect(qwenResult).toContainText('费用状态为未知，不是免费');
  await expect(qwenResult).toContainText('knowledge-2026-08-27.6');
  await expect(qwenResult).toContainText('brand-entity-resolution-v5');

  await page.getByLabel('推理策略').selectOption('deterministic_only');
  await expect(modelSelect).toBeDisabled();
  await page.getByRole('button', { name: '执行知识判断' }).click();
  await expect(page.getByLabel('知识判断结果')).toContainText('未选择');
  await expect.poll(() => submitted.length).toBe(3);

  expect(submitted.map((body) => body.model ?? null)).toEqual([
    'gpt-5.6-luna',
    'qwen3.7-plus',
    null,
  ]);
  expect(submitted.map((body) => body.policy)).toEqual([
    'llm_required',
    'llm_required',
    'deterministic_only',
  ]);
  expect(JSON.stringify(submitted)).not.toContain(projectPubId);
  const storedKnowledgeEntries = await page.evaluate(() =>
    Object.fromEntries(
      Object.entries(localStorage).filter(([key]) =>
        key.startsWith('geo.operations.knowledge-runtime.'),
      ),
    ),
  );
  expect(storedKnowledgeEntries).toEqual({
    'geo.operations.knowledge-runtime.model.v1': 'qwen3.7-plus',
  });
  expect(JSON.stringify(storedKnowledgeEntries)).not.toContain(projectPubId);
  expect(JSON.stringify(storedKnowledgeEntries)).not.toContain('api_key');
  expect(JSON.stringify(storedKnowledgeEntries)).not.toContain('base_url');
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    .toBe(true);
});
