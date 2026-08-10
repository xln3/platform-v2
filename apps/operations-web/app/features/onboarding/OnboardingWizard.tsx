import { useMemo, useState } from 'react';
import {
  ONBOARDING_FREQUENCIES,
  ONBOARDING_MODELS,
  onboardingApi,
  type OnboardingFrequency,
  type OnboardingModel,
  type OnboardingView,
  type SessionContext,
} from './api';

const MODEL_LABELS: Record<OnboardingModel, string> = {
  doubao: '豆包',
  deepseek: 'DeepSeek',
  yiyan: '文心一言',
  tongyi: '通义千问',
  yuanbao: '腾讯元宝',
};

const FREQUENCY_LABELS: Record<OnboardingFrequency, string> = {
  'one-off': '一次性',
  daily: '每日',
  weekly: '每周',
  monthly: '每月',
};

function splitLines(value: string): string[] {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

type Props = { session: SessionContext };

export function OnboardingWizard({ session }: Props) {
  const canManage = session.role === 'operator' || session.role === 'admin';
  const [customerName, setCustomerName] = useState('');
  const [projectName, setProjectName] = useState('');
  const [contactRole, setContactRole] = useState('');
  const [audience, setAudience] = useState('');
  const [publicStatement, setPublicStatement] = useState('');
  const [brandName, setBrandName] = useState('');
  const [website, setWebsite] = useState('');
  const [productName, setProductName] = useState('');
  const [competitors, setCompetitors] = useState('');
  const [prohibitedClaim, setProhibitedClaim] = useState('');
  const [goal, setGoal] = useState('');
  const [questions, setQuestions] = useState('');
  const [models, setModels] = useState<OnboardingModel[]>([...ONBOARDING_MODELS]);
  const [regions, setRegions] = useState('全国');
  const [frequency, setFrequency] = useState<OnboardingFrequency>('weekly');
  const [truthConfirmed, setTruthConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<OnboardingView | null>(null);

  const questionItems = useMemo(() => splitLines(questions), [questions]);
  const competitorItems = useMemo(() => splitLines(competitors), [competitors]);
  const regionItems = useMemo(() => splitLines(regions), [regions]);
  const estimatedTasks = questionItems.length * models.length;

  const requiredFilled =
    customerName.trim() !== '' &&
    projectName.trim() !== '' &&
    contactRole.trim() !== '' &&
    audience.trim() !== '' &&
    publicStatement.trim() !== '' &&
    brandName.trim() !== '' &&
    website.trim() !== '' &&
    productName.trim() !== '' &&
    prohibitedClaim.trim() !== '' &&
    goal.trim() !== '' &&
    questionItems.length > 0 &&
    models.length > 0 &&
    regionItems.length > 0;
  const canSubmit = canManage && !busy && requiredFilled && truthConfirmed;

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const view = await onboardingApi.createOnboarding(session, {
        customerName: customerName.trim(),
        projectName: projectName.trim(),
        contactRole: contactRole.trim(),
        audience: audience.trim(),
        publicStatement: publicStatement.trim(),
        brandName: brandName.trim(),
        website: website.trim(),
        productName: productName.trim(),
        competitors: competitorItems,
        prohibitedClaim: prohibitedClaim.trim(),
        goal: goal.trim(),
        questions: questionItems,
        models,
        regions: regionItems,
        frequency,
        truthConfirmed,
      });
      setReceipt(view);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'onboarding_failed');
    } finally {
      setBusy(false);
    }
  }

  if (receipt) {
    return (
      <section className="onboarding-card" data-testid="onboarding-receipt">
        <div className="section-title">
          <h2>开户完成</h2>
          <span>客户、项目、资料版本、监测配置已一次性建档并冻结</span>
        </div>
        <dl className="receipt-grid">
          <div>
            <dt>客户</dt>
            <dd>{receipt.customer_pub_id}</dd>
          </div>
          <div>
            <dt>项目</dt>
            <dd>{receipt.project_pub_id}</dd>
          </div>
          <div>
            <dt>配置版本</dt>
            <dd>
              v{receipt.config_revision} · {receipt.config_version_pub_id}
            </dd>
          </div>
          <div>
            <dt>预计任务</dt>
            <dd>{receipt.task_count} 个</dd>
          </div>
        </dl>
        <div className="receipt-actions">
          <a href={receipt.mvp_document_url}>下载 MVP 服务文档（docx）</a>
          <a href={receipt.measurement_requirements_url}>下载 GEO评测需求表（xlsx）</a>
          <button type="button" onClick={() => setReceipt(null)}>
            继续开户下一家
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="onboarding-card">
      <div className="section-title">
        <h2>开户向导</h2>
        <span>一次性建立客户、项目、监测配置并冻结首版 · 幂等提交</span>
      </div>
      {!canManage && <p className="empty">当前角色仅可查看，开户提交由运营或管理员执行。</p>}
      {error && (
        <p className="onboarding-error" role="alert">
          提交失败：{error}
        </p>
      )}
      <fieldset disabled={!canManage || busy}>
        <div className="onboarding-grid">
          <div>
            <h3>客户与项目</h3>
            <label>
              客户名称
              <input
                value={customerName}
                onChange={(event) => setCustomerName(event.target.value)}
              />
            </label>
            <label>
              项目名称
              <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
            </label>
            <label>
              对接人角色
              <input
                value={contactRole}
                onChange={(event) => setContactRole(event.target.value)}
                placeholder="市场总监 / 品牌负责人"
              />
            </label>
            <label>
              目标受众
              <textarea
                rows={3}
                value={audience}
                onChange={(event) => setAudience(event.target.value)}
              />
            </label>
            <label>
              对外公开口径
              <textarea
                rows={3}
                value={publicStatement}
                onChange={(event) => setPublicStatement(event.target.value)}
              />
            </label>
          </div>
          <div>
            <h3>品牌与竞品</h3>
            <label>
              品牌名称
              <input value={brandName} onChange={(event) => setBrandName(event.target.value)} />
            </label>
            <label>
              官网
              <input
                type="url"
                value={website}
                onChange={(event) => setWebsite(event.target.value)}
                placeholder="https://"
              />
            </label>
            <label>
              产品名称
              <input value={productName} onChange={(event) => setProductName(event.target.value)} />
            </label>
            <label>
              竞品（逗号或换行分隔）
              <textarea
                rows={3}
                value={competitors}
                onChange={(event) => setCompetitors(event.target.value)}
              />
            </label>
            <label>
              禁用宣称
              <textarea
                rows={2}
                value={prohibitedClaim}
                onChange={(event) => setProhibitedClaim(event.target.value)}
              />
            </label>
          </div>
          <div>
            <h3>监测配置</h3>
            <label>
              评测目标
              <textarea rows={2} value={goal} onChange={(event) => setGoal(event.target.value)} />
            </label>
            <label>
              监测问题（每行一条）
              <textarea
                rows={5}
                value={questions}
                onChange={(event) => setQuestions(event.target.value)}
                placeholder="品牌在 AI 搜索中的口碑如何？"
              />
            </label>
            <div className="platform-checks" aria-label="采集平台">
              {ONBOARDING_MODELS.map((slug) => (
                <label key={slug}>
                  <input
                    type="checkbox"
                    checked={models.includes(slug)}
                    onChange={(event) =>
                      setModels((current) =>
                        event.target.checked
                          ? [...current, slug]
                          : current.filter((item) => item !== slug),
                      )
                    }
                  />
                  {MODEL_LABELS[slug]}
                </label>
              ))}
            </div>
            <div className="inline-fields">
              <label>
                地域（逗号或换行分隔）
                <input value={regions} onChange={(event) => setRegions(event.target.value)} />
              </label>
              <label>
                频率
                <select
                  value={frequency}
                  onChange={(event) => setFrequency(event.target.value as OnboardingFrequency)}
                >
                  {ONBOARDING_FREQUENCIES.map((value) => (
                    <option key={value} value={value}>
                      {FREQUENCY_LABELS[value]}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p className="setup-summary">预计 {estimatedTasks} 个任务</p>
          </div>
        </div>
        <label className="truth-gate">
          <input
            type="checkbox"
            checked={truthConfirmed}
            onChange={(event) => setTruthConfirmed(event.target.checked)}
          />
          客户已书面确认以上信息真实、准确、合法（真实性确认）
        </label>
        <button
          type="button"
          className="submit"
          onClick={() => void submit()}
          disabled={!canSubmit}
        >
          {busy ? '提交中…' : '提交开户'}
        </button>
      </fieldset>
    </section>
  );
}
