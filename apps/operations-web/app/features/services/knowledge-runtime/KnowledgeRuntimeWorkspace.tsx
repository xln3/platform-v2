import {
  getKnowledgeModelCatalog,
  resolveKnowledgeRuntime,
  type KnowledgeDecision,
  type KnowledgeModelCatalog,
  type KnowledgeRuntimeRequest,
  type KnowledgeRuntimeResponse,
} from '@geo/api-client';
import { ModelSelect, StatePanel, type ModelSelectOption } from '@geo/design-system';
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import type { Project, SessionContext } from '../api';
import './knowledge-runtime.css';

const MODEL_STORAGE_KEY = 'geo.operations.knowledge-runtime.model.v1';
const MODEL_POLICIES = new Set<KnowledgeRuntimeRequest['policy']>([
  'llm_assisted',
  'llm_required',
  'exploratory',
]);

type CatalogState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: KnowledgeModelCatalog }
  | { kind: 'failed' }
  | { kind: 'forbidden' };

type ModelLoader = typeof getKnowledgeModelCatalog;
type RuntimeResolver = typeof resolveKnowledgeRuntime;

const isModelPolicy = (policy: KnowledgeRuntimeRequest['policy']): boolean =>
  MODEL_POLICIES.has(policy);

function storedModel(): string {
  try {
    return globalThis.localStorage?.getItem(MODEL_STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}

function rememberModel(model: string): void {
  try {
    globalThis.localStorage?.setItem(MODEL_STORAGE_KEY, model);
  } catch {
    // Storage is an optional convenience; model selection still works in memory.
  }
}

function modelPriceLabel(model: KnowledgeModelCatalog['models'][number]): string {
  if (
    model.pricing_status === 'unknown' ||
    model.input_usd_per_million_tokens === null ||
    model.output_usd_per_million_tokens === null
  ) {
    return '价格待运维复核；不能视为免费';
  }
  return `输入 $${model.input_usd_per_million_tokens.toFixed(3)} / 输出 $${model.output_usd_per_million_tokens.toFixed(3)}（每百万 tokens）`;
}

function decisionDisplayName(decision: KnowledgeDecision): string {
  const rollUp = decision.value.roll_up;
  if (typeof rollUp === 'object' && rollUp !== null && !Array.isArray(rollUp)) {
    const displayName = (rollUp as Record<string, unknown>).display_name;
    if (typeof displayName === 'string' && displayName.trim()) return displayName;
  }
  const identity = decision.value.identity;
  if (typeof identity === 'object' && identity !== null && !Array.isArray(identity)) {
    const canonicalName = (identity as Record<string, unknown>).canonical_name;
    if (typeof canonicalName === 'string' && canonicalName.trim()) return canonicalName;
  }
  return '未返回规范名称';
}

function errorLabel(code: string): string {
  const labels: Record<string, string> = {
    knowledge_model_not_allowed: '所选模型已不在服务端允许清单中，请刷新目录后重试。',
    knowledge_model_not_applicable: '确定性策略不会调用模型，请清除模型选择后重试。',
    knowledge_request_rejected: '请求未通过服务端契约或政策校验。',
    knowledge_service_unavailable: '知识服务暂时不可用，请稍后重试。',
    model_timeout: '所选模型调用超时；服务端没有静默切换模型。',
  };
  return labels[code] ?? `知识模型请求失败：${code}`;
}

export function KnowledgeRuntimeWorkspace({
  session,
  project,
  loadModels = getKnowledgeModelCatalog,
  resolveRuntime = resolveKnowledgeRuntime,
}: {
  session: SessionContext;
  project: Project;
  loadModels?: ModelLoader;
  resolveRuntime?: RuntimeResolver;
}) {
  const [catalog, setCatalog] = useState<CatalogState>({ kind: 'loading' });
  const [selectedModel, setSelectedModel] = useState('');
  const [policy, setPolicy] = useState<KnowledgeRuntimeRequest['policy']>('deterministic_only');
  const [entities, setEntities] = useState('');
  const [analysisDomain, setAnalysisDomain] = useState(
    () => project.brandrank_domain?.trim() || 'cybersecurity',
  );
  const [targetBrand, setTargetBrand] = useState('');
  const [comparisonScopes, setComparisonScopes] = useState('');
  const [classification, setClassification] =
    useState<KnowledgeRuntimeRequest['data_classification']>('internal');
  const [allowExternalModel, setAllowExternalModel] = useState(false);
  const [adoptModelInference, setAdoptModelInference] = useState(false);
  const [failurePolicy, setFailurePolicy] =
    useState<KnowledgeRuntimeRequest['on_model_failure']>('degrade');
  const [maxLatency, setMaxLatency] = useState('60000');
  const [maxCost, setMaxCost] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [result, setResult] = useState<KnowledgeRuntimeResponse | null>(null);

  useEffect(() => {
    setAnalysisDomain(project.brandrank_domain?.trim() || 'cybersecurity');
  }, [project.brandrank_domain, project.pub_id]);

  useEffect(() => {
    let cancelled = false;
    setCatalog({ kind: 'loading' });
    void loadModels(session.headers)
      .then((response) => {
        if (cancelled) return;
        if (response.kind === 'forbidden') {
          setCatalog({ kind: 'forbidden' });
          return;
        }
        if (response.kind !== 'ready') {
          setCatalog({ kind: 'failed' });
          return;
        }
        setCatalog({ kind: 'ready', data: response.data });
        const remembered = storedModel();
        setSelectedModel(
          response.data.models.some((model) => model.model === remembered)
            ? remembered
            : (response.data.default_model ?? ''),
        );
      })
      .catch(() => {
        if (!cancelled) setCatalog({ kind: 'failed' });
      });
    return () => {
      cancelled = true;
    };
  }, [loadModels, session.headers]);

  const modelCatalog = catalog.kind === 'ready' ? catalog.data : null;
  const modelsReady = modelCatalog?.status === 'ready' && (modelCatalog?.models.length ?? 0) > 0;
  const modelEnabled = isModelPolicy(policy);
  const classifiedForLocalOnly =
    classification === 'confidential' || classification === 'restricted';
  const effectiveAllowExternal = modelEnabled && !classifiedForLocalOnly && allowExternalModel;
  const parsedEntities = entities
    .split(/\r?\n/u)
    .map((value) => value.trim())
    .filter(Boolean)
    .slice(0, 200);

  const modelOptions = useMemo<ModelSelectOption[]>(
    () =>
      (modelCatalog?.models ?? []).map((model) => ({
        value: model.model,
        label: model.label === model.model ? model.model : `${model.label} · ${model.model}`,
        group: model.provider,
        capability: `${model.capability}；严格结构输出已验证（${model.verified_at ?? '时间未知'}）`,
        priceLabel: modelPriceLabel(model),
        isDefault: model.is_default,
        recommended: model.recommended,
      })),
    [modelCatalog],
  );

  const canSubmit =
    !busy &&
    parsedEntities.length > 0 &&
    analysisDomain.trim().length > 0 &&
    (!modelEnabled || (modelsReady && selectedModel.length > 0));

  function selectModel(model: string): void {
    setSelectedModel(model);
    rememberModel(model);
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setNotice(null);
    setResult(null);
    const latency = Number(maxLatency);
    const cost = Number(maxCost);
    const scopes = comparisonScopes
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean);
    const context: Record<string, unknown> = {
      analysis_domain: analysisDomain.trim(),
      comparison_scopes: scopes,
      ...(targetBrand.trim() ? { target_brand: targetBrand.trim() } : {}),
      allowed_evidence_refs: [],
    };
    const body: KnowledgeRuntimeRequest = {
      namespace: 'shared',
      domain: 'brand/entity-resolution',
      task: 'resolve-brand-identity',
      items: parsedEntities.map((value, index) => ({ id: `item-${index + 1}`, value })),
      context,
      policy,
      policy_id: 'operations-knowledge-runtime',
      policy_version: '1',
      adopt_model_inferred: modelEnabled && adoptModelInference,
      on_model_failure: failurePolicy,
      data_classification: classification,
      allow_external_model: effectiveAllowExternal,
      ...(modelEnabled ? { model: selectedModel } : {}),
      ...(Number.isSafeInteger(latency) && latency >= 1 && latency <= 600_000
        ? { max_latency_ms: latency }
        : {}),
      ...(maxCost.trim() && Number.isFinite(cost) && cost >= 0 && cost <= 100
        ? { max_cost_usd: cost }
        : {}),
    };
    try {
      const response = await resolveRuntime(body, session.headers);
      if (response.kind === 'ready') {
        setResult(response.data);
      } else if (response.kind === 'rejected') {
        setNotice(errorLabel(response.code));
      } else if (response.kind === 'failed') {
        setNotice(errorLabel(response.code));
      } else if (response.kind === 'forbidden') {
        setNotice('当前账号没有知识判断权限。');
      } else {
        setNotice('知识服务暂时不可用，请稍后重试。');
      }
    } catch {
      setNotice('知识服务暂时不可用，请稍后重试。');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="knowledge-runtime" aria-label="共享知识判断工作区">
      <form className="knowledge-runtime-form" onSubmit={(event) => void submit(event)}>
        <section className="knowledge-panel">
          <h2>判断对象与适用范围</h2>
          <label>
            待判断实体（每行一个）
            <textarea
              value={entities}
              onChange={(event) => setEntities(event.target.value)}
              rows={5}
              maxLength={20_000}
              placeholder="例如：新大陆数字技术"
            />
          </label>
          <div className="knowledge-grid">
            <label>
              分析域
              <input
                value={analysisDomain}
                onChange={(event) => setAnalysisDomain(event.target.value)}
                maxLength={160}
                placeholder="cybersecurity"
              />
            </label>
            <label>
              目标品牌（可选）
              <input
                value={targetBrand}
                onChange={(event) => setTargetBrand(event.target.value)}
                maxLength={1_000}
              />
            </label>
            <label>
              比较场景（英文逗号分隔）
              <input
                value={comparisonScopes}
                onChange={(event) => setComparisonScopes(event.target.value)}
                maxLength={1_000}
                placeholder="ctid,digital_identity"
              />
            </label>
          </div>
        </section>

        <section className="knowledge-panel">
          <h2>推理政策与模型</h2>
          <div className="knowledge-grid">
            <label>
              推理策略
              <select
                aria-label="推理策略"
                value={policy}
                onChange={(event) =>
                  setPolicy(event.target.value as KnowledgeRuntimeRequest['policy'])
                }
              >
                <option value="deterministic_only">仅确定性知识（不调用模型）</option>
                <option value="llm_assisted" disabled={!modelsReady}>
                  模型辅助（只处理未决项）
                </option>
                <option value="llm_required" disabled={!modelsReady}>
                  强制模型判断
                </option>
                <option value="exploratory" disabled={!modelsReady}>
                  探索性模型判断
                </option>
              </select>
            </label>
            <label>
              数据分级
              <select
                aria-label="数据分级"
                value={classification}
                onChange={(event) =>
                  setClassification(
                    event.target.value as KnowledgeRuntimeRequest['data_classification'],
                  )
                }
              >
                <option value="public">公开</option>
                <option value="internal">内部</option>
                <option value="confidential">机密（禁止外发）</option>
                <option value="restricted">受限（禁止外发）</option>
              </select>
            </label>
          </div>
          <ModelSelect
            className="knowledge-model-select"
            label="本次知识推理模型"
            ariaLabel="共享知识推理模型选择"
            value={selectedModel}
            options={modelOptions}
            disabled={!modelEnabled || !modelsReady || busy}
            onChange={selectModel}
            emptyLabel={catalog.kind === 'loading' ? '正在从服务端加载模型…' : '没有已准入模型'}
            hint={
              modelEnabled
                ? '只显示通过当前知识严格 Schema 实测的服务端允许模型；浏览器不接收凭据。'
                : '确定性策略不调用模型，模型选择已禁用。'
            }
          />
          {catalog.kind === 'forbidden' ? <StatePanel state="forbidden" /> : null}
          {catalog.kind === 'failed' ? <p className="knowledge-alert">模型目录加载失败。</p> : null}
          {modelCatalog?.status === 'unavailable' ? (
            <p className="knowledge-alert">
              没有已准入模型：{modelCatalog.unavailable_reason ?? 'unknown'}。确定性判断仍可用。
            </p>
          ) : null}
          <div className="knowledge-switches">
            <label>
              <input
                type="checkbox"
                checked={effectiveAllowExternal}
                disabled={!modelEnabled || classifiedForLocalOnly}
                onChange={(event) => setAllowExternalModel(event.target.checked)}
              />
              允许本次请求调用外部模型
            </label>
            <label>
              <input
                type="checkbox"
                checked={modelEnabled && adoptModelInference}
                disabled={!modelEnabled}
                onChange={(event) => setAdoptModelInference(event.target.checked)}
              />
              允许模型假设影响本次有效结果
            </label>
          </div>
          <div className="knowledge-grid">
            <label>
              模型失败时
              <select
                value={failurePolicy}
                onChange={(event) =>
                  setFailurePolicy(
                    event.target.value as KnowledgeRuntimeRequest['on_model_failure'],
                  )
                }
              >
                <option value="degrade">降级为确定性结果</option>
                <option value="fail">整次请求失败</option>
              </select>
            </label>
            <label>
              最大模型时延（毫秒）
              <input
                type="number"
                min={1}
                max={600000}
                value={maxLatency}
                onChange={(event) => setMaxLatency(event.target.value)}
              />
            </label>
            <label>
              最大费用（USD，可空）
              <input
                type="number"
                min={0}
                max={100}
                step="0.001"
                value={maxCost}
                onChange={(event) => setMaxCost(event.target.value)}
              />
            </label>
          </div>
        </section>

        <button type="submit" className="knowledge-submit" disabled={!canSubmit}>
          {busy ? '判断中…' : '执行知识判断'}
        </button>
      </form>

      {notice ? (
        <p className="knowledge-alert" role="alert">
          {notice}
        </p>
      ) : null}
      {result ? <KnowledgeResult result={result} /> : null}
    </section>
  );
}

function KnowledgeResult({ result }: { result: KnowledgeRuntimeResponse }) {
  const identityChanged =
    result.requested_model_name !== null &&
    result.model_name !== null &&
    result.requested_model_name !== result.model_name;
  return (
    <section className="knowledge-result" aria-label="知识判断结果">
      <header>
        <div>
          <p className="knowledge-eyebrow">请求 {result.request_id}</p>
          <h2>判断结果与模型血缘</h2>
        </div>
        <span className={`knowledge-cache knowledge-cache-${result.cache_status}`}>
          缓存：{result.cache_status}
        </span>
      </header>
      {identityChanged ? (
        <p className="knowledge-alert">
          供应商实际模型与所选模型不同：{result.requested_model_name} → {result.model_name}
        </p>
      ) : null}
      {result.usage.cost_usd === null && result.model_inference_used ? (
        <p className="knowledge-alert">供应商未返回可核验费用；费用状态为未知，不是免费。</p>
      ) : null}
      <dl className="knowledge-facts">
        <div>
          <dt>请求模型</dt>
          <dd>{result.requested_model_name ?? '未选择'}</dd>
        </div>
        <div>
          <dt>供应商实际模型</dt>
          <dd>{result.model_name ?? '未调用'}</dd>
        </div>
        <div>
          <dt>模型标识来源</dt>
          <dd>{result.model_identity_source ?? '不适用'}</dd>
        </div>
        <div>
          <dt>提示词版本</dt>
          <dd>{result.prompt_version ?? '不适用'}</dd>
        </div>
        <div>
          <dt>知识版本</dt>
          <dd>{result.release.release_id}</dd>
        </div>
        <div>
          <dt>模型结果采用</dt>
          <dd>{result.model_inference_adopted ? '已用于本次请求' : '未采用'}</dd>
        </div>
        <div>
          <dt>本次供应商调用</dt>
          <dd>{result.provider_call_attempted ? '已发起' : '未发起（可能命中缓存）'}</dd>
        </div>
        <div>
          <dt>时延</dt>
          <dd>
            总计 {result.latency_ms} ms / 模型 {result.usage.model_latency_ms ?? 0} ms
          </dd>
        </div>
        <div>
          <dt>Tokens</dt>
          <dd>
            输入 {result.usage.input_tokens ?? '未知'} / 输出 {result.usage.output_tokens ?? '未知'}
          </dd>
        </div>
        <div>
          <dt>费用</dt>
          <dd>
            {result.usage.cost_usd === null ? '未知' : `$${result.usage.cost_usd.toFixed(6)}`}
          </dd>
        </div>
      </dl>
      {result.degradation.length ? (
        <div className="knowledge-degradation">
          <strong>降级或政策提示</strong>
          <ul>
            {result.degradation.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <DecisionCards title="本次有效结论" decisions={result.decisions} empty="没有有效结论。" />
      <DecisionCards
        title="模型假设（仅限本次请求，不写入已发布知识）"
        decisions={result.model_hypotheses}
        empty="本次请求没有模型假设。"
      />
    </section>
  );
}

function DecisionCards({
  title,
  decisions,
  empty,
}: {
  title: string;
  decisions: KnowledgeDecision[];
  empty: string;
}) {
  return (
    <section>
      <h3>{title}</h3>
      <div className="knowledge-decisions">
        {decisions.length ? (
          decisions.map((decision, index) => (
            <article key={`${decision.input_id}-${decision.knowledge_status}-${index}`}>
              <header>
                <h3>{decision.input_value}</h3>
                <span>{decision.knowledge_status}</span>
              </header>
              <p>规范名称：{decisionDisplayName(decision)}</p>
              <p>置信度：{(decision.confidence * 100).toFixed(1)}%</p>
              <p>当前请求采用：{decision.adopted ? '是' : '否'}</p>
              {decision.reasons.length ? <p>依据：{decision.reasons.join('；')}</p> : null}
            </article>
          ))
        ) : (
          <p className="empty">{empty}</p>
        )}
      </div>
    </section>
  );
}

export { MODEL_STORAGE_KEY };
