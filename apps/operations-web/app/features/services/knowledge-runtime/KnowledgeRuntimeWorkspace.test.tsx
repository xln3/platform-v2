// @vitest-environment jsdom

import {
  type KnowledgeModelCatalog,
  type KnowledgeRuntimeRequest,
  type KnowledgeRuntimeResponse,
} from '@geo/api-client';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { KnowledgeRuntimeWorkspace, MODEL_STORAGE_KEY } from './KnowledgeRuntimeWorkspace';

const session = {
  tenantId: 'tnt_fixture',
  actorId: 'usr_analyst',
  role: 'analyst' as const,
  headers: {
    'X-Tenant-Id': 'tnt_fixture',
    'X-Actor-Id': 'usr_analyst',
    'X-Actor-Role': 'analyst',
  },
};

const project = {
  pub_id: 'prj_customer_context_must_not_leave_browser',
  name: '机密客户项目',
  state: 'active',
  updated_at: '2026-08-28T00:00:00Z',
  brandrank_domain: 'digital_identity',
};

const catalog: KnowledgeModelCatalog = {
  status: 'ready',
  catalog_revision: 'knowledge-inference-model-catalog-20260828.2+fixture',
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
      pricing_observed_at: '2026-08-25',
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
      verification_reference: 'docs/knowledge/evidence/knowledge-model-admission-20260828.json',
      input_usd_per_million_tokens: null,
      output_usd_per_million_tokens: null,
      pricing_status: 'unknown',
      pricing_observed_at: null,
      is_default: false,
      recommended: false,
    },
  ],
};

function responseFor(body: KnowledgeRuntimeRequest): KnowledgeRuntimeResponse {
  const requested = body.model ?? null;
  const used = body.policy !== 'deterministic_only';
  return {
    request_id: `req-${requested ?? 'deterministic'}`,
    domain: body.domain,
    task: body.task,
    policy: body.policy,
    policy_id: body.policy_id,
    policy_version: body.policy_version,
    release: {
      release_id: 'knowledge-2026-08-27.6',
      content_hash: `sha256:${'a'.repeat(64)}`,
      schema_version: 'knowledge-release-v1',
      source: 'local_knowledge_release',
      degraded: false,
    },
    decisions: [],
    model_hypotheses: used
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
            requested_model_name: requested,
            model_name: requested === 'qwen3.7-plus' ? 'qwen3.7-plus-20260828' : requested,
            model_version: 'deployment',
            model_identity_source: 'provider_response',
            prompt_id: 'brand-entity-resolution',
            prompt_version: 'brand-entity-resolution-v5',
            knowledge_release_id: 'knowledge-2026-08-27.6',
            knowledge_content_hash: `sha256:${'a'.repeat(64)}`,
            policy_id: body.policy_id,
            policy_version: body.policy_version,
          },
        ]
      : [],
    prompt_id: used ? 'brand-entity-resolution' : null,
    prompt_version: used ? 'brand-entity-resolution-v5' : null,
    model_provider: used ? 'openai-compatible' : null,
    requested_model_name: requested,
    model_name: requested === 'qwen3.7-plus' ? 'qwen3.7-plus-20260828' : requested,
    model_version: used ? 'deployment' : null,
    model_identity_source: used ? 'provider_response' : null,
    model_catalog_revision: used ? catalog.catalog_revision : null,
    model_inference_used: used,
    model_inference_adopted: used && Boolean(body.adopt_model_inferred),
    provider_call_attempted: used,
    latency_ms: used ? 35_100 : 4,
    cache_status: used ? 'miss' : 'bypass',
    degradation: requested === 'qwen3.7-plus' ? ['cost_budget_unverifiable'] : [],
    observation_count: used ? 1 : 0,
    usage: {
      input_tokens: used ? 7_831 : null,
      output_tokens: used ? 1_937 : null,
      cost_usd: null,
      model_latency_ms: used ? 35_094 : null,
    },
  };
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('KnowledgeRuntimeWorkspace', () => {
  it('loads the server catalog into the shared selector and keeps deterministic mode model-free', async () => {
    const loadModels = vi.fn(async () => ({ kind: 'ready' as const, data: catalog }));
    render(
      <KnowledgeRuntimeWorkspace session={session} project={project} loadModels={loadModels} />,
    );

    const selector = await screen.findByLabelText('共享知识推理模型选择');
    await waitFor(() => expect(loadModels).toHaveBeenCalledWith(session.headers));
    expect((selector as HTMLSelectElement).disabled).toBe(true);
    expect(within(selector).getByRole('option', { name: /GPT 5.6 Luna.*默认/ })).toBeTruthy();
    expect(
      within(selector).getByRole('option', { name: /Qwen 3.7 Plus.*价格待运维复核/ }),
    ).toBeTruthy();
    expect(selector.querySelector('optgroup[label="GPT"]')).not.toBeNull();
    expect(selector.querySelector('optgroup[label="Qwen"]')).not.toBeNull();
    expect(screen.getByText('确定性策略不调用模型，模型选择已禁用。')).toBeTruthy();
  });

  it('sends each selected model per request without persisting project or credential context', async () => {
    const submitted: KnowledgeRuntimeRequest[] = [];
    const resolveRuntime = vi.fn(async (body: KnowledgeRuntimeRequest) => {
      submitted.push(body);
      return { kind: 'ready' as const, data: responseFor(body) };
    });
    render(
      <KnowledgeRuntimeWorkspace
        session={session}
        project={project}
        loadModels={async () => ({ kind: 'ready', data: catalog })}
        resolveRuntime={resolveRuntime}
      />,
    );

    const policy = screen.getByLabelText('推理策略');
    await waitFor(() =>
      expect(
        (
          within(policy).getByRole('option', {
            name: /强制模型判断/,
          }) as HTMLOptionElement
        ).disabled,
      ).toBe(false),
    );
    fireEvent.change(policy, { target: { value: 'llm_required' } });
    fireEvent.change(screen.getByLabelText('共享知识推理模型选择'), {
      target: { value: 'qwen3.7-plus' },
    });
    fireEvent.change(screen.getByLabelText(/待判断实体/), {
      target: { value: '新大陆数字技术' },
    });
    fireEvent.change(screen.getByLabelText('数据分级'), { target: { value: 'public' } });
    fireEvent.click(screen.getByLabelText('允许本次请求调用外部模型'));
    fireEvent.click(screen.getByLabelText('允许模型假设影响本次有效结果'));
    fireEvent.click(screen.getByRole('button', { name: '执行知识判断' }));

    await waitFor(() => expect(submitted).toHaveLength(1));
    expect(submitted[0]).toMatchObject({
      model: 'qwen3.7-plus',
      policy: 'llm_required',
      allow_external_model: true,
      adopt_model_inferred: true,
      data_classification: 'public',
    });
    expect(submitted[0]?.context).toMatchObject({ analysis_domain: 'digital_identity' });
    expect(JSON.stringify(submitted[0])).not.toContain(project.pub_id);

    fireEvent.change(screen.getByLabelText('共享知识推理模型选择'), {
      target: { value: 'gpt-5.6-luna' },
    });
    fireEvent.click(screen.getByRole('button', { name: '执行知识判断' }));
    await waitFor(() => expect(submitted).toHaveLength(2));
    expect(submitted.map((body) => body.model)).toEqual(['qwen3.7-plus', 'gpt-5.6-luna']);
    expect(localStorage.length).toBe(1);
    expect(localStorage.key(0)).toBe(MODEL_STORAGE_KEY);
    expect(localStorage.getItem(MODEL_STORAGE_KEY)).toBe('gpt-5.6-luna');
  });

  it('shows requested versus provider model lineage, unknown cost, and degradation', async () => {
    render(
      <KnowledgeRuntimeWorkspace
        session={session}
        project={project}
        loadModels={async () => ({ kind: 'ready', data: catalog })}
        resolveRuntime={async (body) => ({ kind: 'ready', data: responseFor(body) })}
      />,
    );
    await waitFor(() =>
      expect(
        (screen.getByRole('option', { name: /强制模型判断/ }) as HTMLOptionElement).disabled,
      ).toBe(false),
    );
    fireEvent.change(screen.getByLabelText('推理策略'), {
      target: { value: 'llm_required' },
    });
    fireEvent.change(screen.getByLabelText('共享知识推理模型选择'), {
      target: { value: 'qwen3.7-plus' },
    });
    fireEvent.change(screen.getByLabelText(/待判断实体/), {
      target: { value: '新大陆数字技术' },
    });
    fireEvent.click(screen.getByRole('button', { name: '执行知识判断' }));

    const result = await screen.findByLabelText('知识判断结果');
    expect(within(result).getByText('qwen3.7-plus', { exact: true })).toBeTruthy();
    expect(within(result).getByText('qwen3.7-plus-20260828')).toBeTruthy();
    expect(within(result).getByText(/供应商实际模型与所选模型不同/)).toBeTruthy();
    expect(within(result).getByText(/费用状态为未知，不是免费/)).toBeTruthy();
    expect(within(result).getByText('cost_budget_unverifiable')).toBeTruthy();
    expect(within(result).getByText('brand-entity-resolution-v5')).toBeTruthy();
    expect(within(result).getByText('knowledge-2026-08-27.6')).toBeTruthy();
  });

  it('keeps deterministic resolution available when no model is admitted', async () => {
    const submitted: KnowledgeRuntimeRequest[] = [];
    const unavailable: KnowledgeModelCatalog = {
      status: 'unavailable',
      catalog_revision: 'knowledge-inference-model-catalog-20260828.2+empty',
      default_model: null,
      models: [],
      unavailable_reason: 'knowledge_model_gateway_unconfigured',
    };
    render(
      <KnowledgeRuntimeWorkspace
        session={session}
        project={project}
        loadModels={async () => ({ kind: 'ready', data: unavailable })}
        resolveRuntime={async (body) => {
          submitted.push(body);
          return { kind: 'ready', data: responseFor(body) };
        }}
      />,
    );

    expect(
      await screen.findByText(/没有已准入模型：knowledge_model_gateway_unconfigured/),
    ).toBeTruthy();
    expect(
      (screen.getByRole('option', { name: /强制模型判断/ }) as HTMLOptionElement).disabled,
    ).toBe(true);
    fireEvent.change(screen.getByLabelText(/待判断实体/), { target: { value: '确定性实体' } });
    fireEvent.click(screen.getByRole('button', { name: '执行知识判断' }));
    await waitFor(() => expect(submitted).toHaveLength(1));
    expect(submitted[0]?.policy).toBe('deterministic_only');
    expect(submitted[0]).not.toHaveProperty('model');
    expect(submitted[0]?.allow_external_model).toBe(false);
  });

  it('discloses catalog and allow-list failures instead of silently falling back', async () => {
    const { rerender } = render(
      <KnowledgeRuntimeWorkspace
        session={session}
        project={project}
        loadModels={async () => ({ kind: 'unavailable' })}
      />,
    );
    expect(await screen.findByText('模型目录加载失败。')).toBeTruthy();

    const rejected = vi.fn(async () => ({
      kind: 'rejected' as const,
      code: 'knowledge_model_not_allowed',
    }));
    rerender(
      <KnowledgeRuntimeWorkspace
        session={session}
        project={project}
        loadModels={async () => ({ kind: 'ready', data: catalog })}
        resolveRuntime={rejected}
      />,
    );
    await waitFor(() =>
      expect(
        (screen.getByRole('option', { name: /强制模型判断/ }) as HTMLOptionElement).disabled,
      ).toBe(false),
    );
    fireEvent.change(screen.getByLabelText('推理策略'), { target: { value: 'llm_required' } });
    fireEvent.change(screen.getByLabelText(/待判断实体/), { target: { value: '示例品牌' } });
    fireEvent.click(screen.getByRole('button', { name: '执行知识判断' }));
    expect((await screen.findByRole('alert')).textContent).toContain(
      '所选模型已不在服务端允许清单中',
    );
  });

  it('shows the sanitized fail-fast model error without implying a fallback', async () => {
    render(
      <KnowledgeRuntimeWorkspace
        session={session}
        project={project}
        loadModels={async () => ({ kind: 'ready', data: catalog })}
        resolveRuntime={async () => ({ kind: 'failed', code: 'model_timeout' })}
      />,
    );
    await waitFor(() =>
      expect(
        (screen.getByRole('option', { name: /强制模型判断/ }) as HTMLOptionElement).disabled,
      ).toBe(false),
    );
    fireEvent.change(screen.getByLabelText('推理策略'), { target: { value: 'llm_required' } });
    fireEvent.change(screen.getByLabelText(/待判断实体/), { target: { value: '示例品牌' } });
    fireEvent.click(screen.getByRole('button', { name: '执行知识判断' }));
    expect((await screen.findByRole('alert')).textContent).toContain(
      '所选模型调用超时；服务端没有静默切换模型',
    );
  });
});
