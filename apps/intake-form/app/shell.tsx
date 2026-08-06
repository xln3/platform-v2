import {
  Badge,
  containsClientSecret,
  FormField,
  navigateClientSection,
  StatePanel,
} from '@geo/design-system';
import {
  createIntakeFormCompetitor,
  createIntakeFormPromo,
  createIntakeFormTriggers,
  deleteIntakeFormCompetitor,
  deleteIntakeFormPromo,
  deleteIntakeFormTrigger,
  getIntakeFormContext,
  getIntakeFormSiliconCandidates,
  getIntakeFormSiliconTemplateQuestions,
  listIntakeFormPromos,
  listIntakeFormTriggers,
  patchIntakeFormBrand,
  putIntakeFormProfile,
  runIntakeFormAiResearch,
  submitIntakeForm,
  suggestIntakeFormQuestions,
  type IntakeFormAiResearchSummary,
  type IntakeFormBrand,
  type IntakeFormCompetitor,
  type IntakeFormContext,
  type IntakeFormFailureCode,
  type IntakeFormSiliconCandidates,
  type IntakeFormSubmitReceipt,
  type IntakeFormSuggestion,
  type IntakeFormTemplateQuestion,
  type IntakeFormTemplateQuestions,
  type IntakeLicenseRow,
  type IntakeProfileView,
  type IntakeProfileWrite,
  type IntakePromoKind,
  type IntakePromoPayload,
  type IntakePromoView,
  type IntakeTriggerView,
  type ProjectedCollection,
} from '@geo/api-client';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router';

export const intakeNavIds = ['brand', 'research', 'profile', 'questions', 'submit'] as const;
export type IntakeSection = (typeof intakeNavIds)[number];

const nav = [
  { id: 'brand', label: '品牌信息' },
  { id: 'research', label: 'AI 调研' },
  { id: 'profile', label: '客户信息表' },
  { id: 'questions', label: '期望问法' },
  { id: 'submit', label: '确认提交' },
];

/** 邀请 token 只从 URL fragment 读取（#t=…），不进路径/查询串，避免落入访问日志。 */
export function readIntakeFormToken(hash: string): string | null {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  for (const part of raw.split('&')) {
    const [key, ...rest] = part.split('=');
    if (key !== 't' || rest.length === 0) continue;
    try {
      const value = decodeURIComponent(rest.join('='));
      if (value.length > 0 && value.length <= 200 && !/\s/u.test(value) && !/\p{Cc}/u.test(value)) {
        return value;
      }
    } catch {
      return null;
    }
  }
  return null;
}

export const splitListInput = (value: string): string[] =>
  value
    .split(/[,，;；]/u)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);

export const splitLinesInput = (value: string): string[] =>
  value
    .split(/\r?\n/u)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);

const emptyToNull = (value: string): string | null => {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
};

export type ProfileFormDraft = {
  contact_person: string;
  contact_info: string;
  website: string;
  wechat: string;
  douyin: string;
  social_media: string;
  audience_desc: string;
  business_license_code: string;
  selling_points: string;
  ad_review_no: string;
  ad_review_authority: string;
  ad_review_expiry: string;
  review_category: string;
  pre_review_required: boolean | null;
  goals: string[];
  audience_type: string[];
  platforms: string[];
  ad_review_doc_types: string[];
  regionsText: string;
  trademarksText: string;
  evidenceText: string;
  licenses: IntakeLicenseRow[];
};

export function initProfileDraft(profile: IntakeProfileView): ProfileFormDraft {
  return {
    contact_person: profile.contact_person ?? '',
    contact_info: profile.contact_info ?? '',
    website: profile.website ?? '',
    wechat: profile.wechat ?? '',
    douyin: profile.douyin ?? '',
    social_media: profile.social_media ?? '',
    audience_desc: profile.audience_desc ?? '',
    business_license_code: profile.business_license_code ?? '',
    selling_points: profile.selling_points ?? '',
    ad_review_no: profile.ad_review_no ?? '',
    ad_review_authority: profile.ad_review_authority ?? '',
    ad_review_expiry: profile.ad_review_expiry ?? '',
    review_category: profile.review_category ?? '',
    pre_review_required: profile.pre_review_required,
    goals: [...profile.goals],
    audience_type: [...profile.audience_type],
    platforms: [...profile.platforms],
    ad_review_doc_types: [...profile.ad_review_doc_types],
    regionsText: profile.regions.join(', '),
    trademarksText: profile.trademarks.join(', '),
    evidenceText: profile.evidence_links.join('\n'),
    licenses: profile.licenses.map((row) => ({ ...row })),
  };
}

/** 写前 DLP：字符串字段一律先过 noClientSecret 检查，命中即拦截并就地提示。 */
const draftSecretViolation = (values: string[]): boolean =>
  values.some((value) => containsClientSecret(value));

const tokenFailureCopy: Partial<Record<IntakeFormFailureCode, { title: string; body: string }>> = {
  intake_token_missing: {
    title: '缺少邀请凭证',
    body: '请使用服务方发送的完整邀请链接打开本页；链接中的凭证部分缺一不可。',
  },
  invite_token_invalid: {
    title: '邀请链接无效',
    body: '该链接未通过校验。请确认链接完整，或联系服务方确认邀请状态。',
  },
  invite_token_expired: {
    title: '邀请链接已过期',
    body: '该链接已超过有效期限，请联系服务方重新签发邀请。',
  },
  invite_token_revoked: {
    title: '邀请链接已失效',
    body: '该邀请已被撤回，请联系服务方重新签发。',
  },
};

function TokenFailurePage({
  code,
  onRetry,
}: {
  code: IntakeFormFailureCode;
  onRetry?: () => void;
}) {
  const copy = tokenFailureCopy[code];
  return (
    <main className="intake-shell">
      <section className="intake-card" style={{ gridColumn: '1 / -1' }}>
        {copy ? (
          <>
            <StatePanel state="forbidden" />
            <h1>{copy.title}</h1>
            <p className="intake-muted">{copy.body}</p>
          </>
        ) : (
          <>
            <h1>页面暂时不可用</h1>
            <StatePanel state="failed" {...(onRetry ? { onRetry } : {})} />
            <p className="intake-muted">请稍后重试；若多次失败请联系服务方。</p>
          </>
        )}
      </section>
    </main>
  );
}

function Chip({
  label,
  pressed,
  disabled,
  onToggle,
}: {
  label: string;
  pressed: boolean;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className="intake-chip"
      aria-pressed={pressed}
      disabled={disabled ?? false}
      onClick={onToggle}
    >
      {label}
    </button>
  );
}

function ZoneMessage({ zone, messages }: { zone: string; messages: Record<string, string> }) {
  const message = messages[zone];
  if (!message) return null;
  const tone = message.startsWith('已保存') || message.startsWith('已提交') ? 'ok' : 'error';
  return (
    <p className="intake-msg" data-tone={tone} role="status">
      {message}
    </p>
  );
}

const writeFailureCopy = (code: IntakeFormFailureCode): string => {
  if (code === 'invite_submitted') return '本表已提交，内容已锁定，无法继续修改。';
  if (code === 'quota_exhausted') return 'AI 调研次数已用完，请手工填写或联系服务方。';
  if (code === 'llm_disabled') return 'AI 能力当前未启用，请手工填写。';
  if (code === 'research_failed') return 'AI 调研失败，请稍后重试或手工填写。';
  if (code === 'validation_failed') return '部分内容未通过服务端校验，请检查必填项与选项取值。';
  if (code === 'submit_incomplete') return '提交前须完成全部真实性确认并填写填表人。';
  return '请求暂时不可用，请稍后重试。';
};

type SessionProps = {
  token: string;
  session: IntakeFormContext;
  readOnly: boolean;
  reloadSession: () => Promise<void>;
  setZoneMessage: (zone: string, message: string) => void;
  zoneMessages: Record<string, string>;
};

function BrandSection({
  token,
  session,
  readOnly,
  reloadSession,
  setZoneMessage,
  zoneMessages,
}: SessionProps) {
  const [name, setName] = useState(session.brand.name ?? '');
  const [website, setWebsite] = useState(session.brand.website ?? '');
  const [aliasesText, setAliasesText] = useState(session.brand.aliases.join('\n'));
  const [competitorName, setCompetitorName] = useState('');
  const [competitorWebsite, setCompetitorWebsite] = useState('');
  const [competitors, setCompetitors] = useState<IntakeFormCompetitor[]>(session.competitors);
  const [saving, setSaving] = useState(false);
  const [silicon, setSilicon] = useState<IntakeFormSiliconCandidates | null>(null);
  const [siliconLoading, setSiliconLoading] = useState(false);

  const saveBrand = async () => {
    if (draftSecretViolation([name, website, aliasesText])) {
      setZoneMessage('brand', '内容包含不允许的字符序列，请检查后重试。');
      return;
    }
    setSaving(true);
    const result = await patchIntakeFormBrand(token, {
      ...(name.trim() ? { name: name.trim() } : {}),
      website: emptyToNull(website),
      aliases: splitLinesInput(aliasesText),
    });
    setSaving(false);
    if (result.kind === 'ready') {
      setZoneMessage('brand', '已保存品牌信息。');
      await reloadSession();
    } else {
      setZoneMessage('brand', writeFailureCopy(result.code));
    }
  };

  const addCompetitor = async () => {
    if (!competitorName.trim()) {
      setZoneMessage('competitors', '请填写竞品名称。');
      return;
    }
    if (draftSecretViolation([competitorName, competitorWebsite])) {
      setZoneMessage('competitors', '内容包含不允许的字符序列，请检查后重试。');
      return;
    }
    const result = await createIntakeFormCompetitor(token, {
      name: competitorName.trim(),
      website: emptyToNull(competitorWebsite),
    });
    if (result.kind === 'ready') {
      setCompetitors((current) => [...current, result.data]);
      setCompetitorName('');
      setCompetitorWebsite('');
      setZoneMessage('competitors', '已保存竞品。');
    } else {
      setZoneMessage('competitors', writeFailureCopy(result.code));
    }
  };

  const removeCompetitor = async (pubId: string) => {
    const result = await deleteIntakeFormCompetitor(token, pubId);
    if (result.kind === 'ready') {
      setCompetitors((current) => current.filter((item) => item.pub_id !== pubId));
    } else {
      setZoneMessage('competitors', writeFailureCopy(result.code));
    }
  };

  const loadSilicon = async () => {
    setSiliconLoading(true);
    const result = await getIntakeFormSiliconCandidates(token, name.trim() || undefined);
    setSiliconLoading(false);
    if (result.kind === 'ready') {
      setSilicon(result.data);
    } else {
      setZoneMessage('silicon', writeFailureCopy(result.code));
    }
  };

  const adoptAliases = async (aliases: string[]) => {
    const merged = [...new Set([...splitLinesInput(aliasesText), ...aliases])];
    const result = await patchIntakeFormBrand(token, { aliases: merged });
    if (result.kind === 'ready') {
      setAliasesText(merged.join('\n'));
      setZoneMessage('brand', '已保存品牌信息。');
      await reloadSession();
    } else {
      setZoneMessage('silicon', writeFailureCopy(result.code));
    }
  };

  const adoptCompetitor = async (suggestionName: string, suggestionWebsite: string | null) => {
    const result = await createIntakeFormCompetitor(token, {
      name: suggestionName,
      website: suggestionWebsite,
    });
    if (result.kind === 'ready') {
      setCompetitors((current) =>
        current.some((item) => item.pub_id === result.data.pub_id)
          ? current
          : [...current, result.data],
      );
      setZoneMessage('competitors', '已保存竞品。');
    } else {
      setZoneMessage('silicon', writeFailureCopy(result.code));
    }
  };

  return (
    <>
      <section className="intake-card" aria-labelledby="intake-brand-heading">
        <h2 id="intake-brand-heading">品牌信息</h2>
        <div className="intake-row">
          <FormField id="brand-name" label="品牌 / 公司名">
            <input
              id="brand-name"
              value={name}
              disabled={readOnly}
              onChange={(event) => setName(event.target.value)}
            />
          </FormField>
          <FormField id="brand-website" label="官网">
            <input
              id="brand-website"
              value={website}
              placeholder="https://"
              disabled={readOnly}
              onChange={(event) => setWebsite(event.target.value)}
            />
          </FormField>
        </div>
        <FormField
          id="brand-aliases"
          label="品牌别名（每行一个）"
          hint="AI 回答中可能出现的其他称呼"
        >
          <textarea
            id="brand-aliases"
            rows={3}
            value={aliasesText}
            disabled={readOnly}
            onChange={(event) => setAliasesText(event.target.value)}
          />
        </FormField>
        <div>
          <button
            type="button"
            className="button"
            disabled={readOnly || saving || !name.trim()}
            onClick={saveBrand}
          >
            保存品牌信息
          </button>
        </div>
        <ZoneMessage zone="brand" messages={zoneMessages} />
      </section>

      <section className="intake-card" aria-labelledby="intake-competitors-heading">
        <h3 id="intake-competitors-heading">竞品</h3>
        {competitors.length === 0 ? (
          <p className="intake-muted">尚未登记竞品。</p>
        ) : (
          <ul className="intake-list">
            {competitors.map((item) => (
              <li key={item.pub_id}>
                <span>
                  {item.name}
                  {item.website ? <span className="intake-muted">　{item.website}</span> : null}
                </span>
                <button
                  type="button"
                  className="button button-secondary"
                  disabled={readOnly}
                  onClick={() => removeCompetitor(item.pub_id)}
                >
                  删除
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="intake-row">
          <FormField id="competitor-name" label="竞品名称">
            <input
              id="competitor-name"
              value={competitorName}
              disabled={readOnly}
              onChange={(event) => setCompetitorName(event.target.value)}
            />
          </FormField>
          <FormField id="competitor-website" label="竞品官网（选填）">
            <input
              id="competitor-website"
              value={competitorWebsite}
              placeholder="https://"
              disabled={readOnly}
              onChange={(event) => setCompetitorWebsite(event.target.value)}
            />
          </FormField>
        </div>
        <div>
          <button
            type="button"
            className="button"
            disabled={readOnly || !competitorName.trim()}
            onClick={addCompetitor}
          >
            添加竞品
          </button>
        </div>
        <ZoneMessage zone="competitors" messages={zoneMessages} />
      </section>

      {silicon !== null && !silicon.available ? null : silicon !== null ? (
        <section className="intake-card" aria-labelledby="intake-silicon-heading">
          <h3 id="intake-silicon-heading">外部知识索引匹配</h3>
          {!silicon.matched ? (
            <p className="intake-muted">索引中未命中该品牌，可跳过本卡片直接填写。</p>
          ) : (
            <>
              {silicon.category_path.length > 0 ? (
                <p>分类路径：{silicon.category_path.join(' / ')}</p>
              ) : null}
              {silicon.aliases.length > 0 ? (
                <div>
                  <p className="intake-muted">建议别名：{silicon.aliases.join('、')}</p>
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={readOnly}
                    onClick={() => adoptAliases(silicon.aliases)}
                  >
                    一键采用别名建议
                  </button>
                </div>
              ) : null}
              {silicon.competitors.length > 0 ? (
                <ul className="intake-list">
                  {silicon.competitors.map((item) => (
                    <li key={item.name}>
                      <span>{item.name}</span>
                      <button
                        type="button"
                        className="button button-secondary"
                        disabled={readOnly}
                        onClick={() => adoptCompetitor(item.name, item.website)}
                      >
                        采纳为竞品
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
              {silicon.disclaimer ? <p className="intake-muted">{silicon.disclaimer}</p> : null}
            </>
          )}
          <ZoneMessage zone="silicon" messages={zoneMessages} />
        </section>
      ) : (
        <section className="intake-card">
          <div>
            <button
              type="button"
              className="button button-secondary"
              disabled={siliconLoading}
              onClick={loadSilicon}
            >
              {siliconLoading ? '正在检查索引…' : '检查外部知识索引匹配'}
            </button>
          </div>
          <ZoneMessage zone="silicon" messages={zoneMessages} />
        </section>
      )}
    </>
  );
}

function ResearchSection({
  token,
  session,
  readOnly,
  reloadSession,
  setZoneMessage,
  zoneMessages,
}: SessionProps) {
  const [brand, setBrand] = useState(session.brand.name ?? '');
  const [website, setWebsite] = useState(session.brand.website ?? '');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<IntakeFormAiResearchSummary | null>(null);
  const remaining = result ? result.ai_remaining : session.invite.ai_remaining;

  const start = async () => {
    if (!brand.trim()) {
      setZoneMessage('research', '请先填写品牌 / 公司名。');
      return;
    }
    if (draftSecretViolation([brand, website])) {
      setZoneMessage('research', '内容包含不允许的字符序列，请检查后重试。');
      return;
    }
    setRunning(true);
    setResult(null);
    const research = await runIntakeFormAiResearch(token, {
      brand: brand.trim(),
      ...(website.trim() ? { website: website.trim() } : {}),
    });
    setRunning(false);
    if (research.kind === 'ready') {
      setResult(research.data);
      setZoneMessage('research', '调研完成，预填内容已写入草稿，请到「客户信息表」逐项核对。');
      await reloadSession();
    } else {
      setZoneMessage('research', writeFailureCopy(research.code));
    }
  };

  return (
    <section className="intake-card" aria-labelledby="intake-research-heading">
      <h2 id="intake-research-heading">AI 联网调研</h2>
      <p className="intake-muted">
        调研将联网检索公开资料并预填客户信息表草稿；预填内容经您核对保存后才生效。
        <Badge tone="info">剩余调研次数 {remaining}</Badge>
      </p>
      <div className="intake-row">
        <FormField id="research-brand" label="品牌 / 公司名">
          <input
            id="research-brand"
            value={brand}
            disabled={readOnly || running}
            onChange={(event) => setBrand(event.target.value)}
          />
        </FormField>
        <FormField id="research-website" label="官网（选填，帮助调研定位）">
          <input
            id="research-website"
            value={website}
            placeholder="https://"
            disabled={readOnly || running}
            onChange={(event) => setWebsite(event.target.value)}
          />
        </FormField>
      </div>
      <div>
        <button
          type="button"
          className="button"
          disabled={readOnly || running || !brand.trim() || remaining <= 0}
          onClick={start}
        >
          开始 AI 调研
        </button>
      </div>
      {running ? (
        <p role="status">
          <span className="intake-spinner" aria-hidden="true" />
          AI 联网调研中，可能需要 1-3 分钟，请勿关闭页面。
        </p>
      ) : null}
      <ZoneMessage zone="research" messages={zoneMessages} />
      {result ? (
        <div className="intake-banner" aria-label="调研结果">
          <p>
            模型 {result.model} · 共 {result.rounds} 轮 · 预填 {result.prefilled.length} 个字段 ·
            新建推广内容 {result.promosCreated} 条 · 收录问法 {result.triggersCreated} 条
          </p>
          {result.summary ? <p>{result.summary}</p> : null}
          {result.sources.length > 0 ? (
            <ul className="intake-list">
              {result.sources.map((source) => (
                <li key={source.url || source.title}>
                  <span>{source.title}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {result.prefilled.length > 0 ? (
            <p className="intake-muted">已预填字段：{result.prefilled.join('、')}</p>
          ) : null}
          {result.unavailable.length > 0 ? (
            <p className="intake-muted">公开资料未覆盖：{result.unavailable.join('、')}</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

type ProfileDraftStringKey = {
  [K in keyof ProfileFormDraft]: ProfileFormDraft[K] extends string ? K : never;
}[keyof ProfileFormDraft];
type ProfileDraftListKey = 'goals' | 'audience_type' | 'platforms' | 'ad_review_doc_types';

type ProfileSectionProps = SessionProps & {
  profile: IntakeProfileView;
  draft: ProfileFormDraft;
  setDraft: (updater: (current: ProfileFormDraft) => ProfileFormDraft) => void;
  promos: ProjectedCollection<IntakePromoView> | null;
  reloadPromos: () => Promise<void>;
};

function ProfileSection({
  token,
  session,
  readOnly,
  setZoneMessage,
  zoneMessages,
  profile,
  draft,
  setDraft,
  promos,
  reloadPromos,
}: ProfileSectionProps) {
  const [industry, setIndustry] = useState('');
  const [savingZone, setSavingZone] = useState('');
  const [promoKind, setPromoKind] = useState<IntakePromoKind>('product');
  const [promoScalars, setPromoScalars] = useState<Record<string, string>>({});
  const [promoLists, setPromoLists] = useState<Record<string, string>>({});
  const [licenseDraft, setLicenseDraft] = useState<IntakeLicenseRow>({
    name: '',
    number: '',
    expiry: '',
  });
  const prefilled = profile.prefilled;

  const setScalar = (key: ProfileDraftStringKey, value: string) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const toggleListValue = (key: ProfileDraftListKey, value: string) =>
    setDraft((current) => {
      const list = current[key];
      return {
        ...current,
        [key]: list.includes(value) ? list.filter((item) => item !== value) : [...list, value],
      };
    });

  const promoZoneBody = (): IntakeProfileWrite => ({
    review_category: emptyToNull(draft.review_category),
    pre_review_required: draft.pre_review_required,
    ad_review_no: emptyToNull(draft.ad_review_no),
    ad_review_authority: emptyToNull(draft.ad_review_authority),
    ad_review_expiry: emptyToNull(draft.ad_review_expiry),
    contact_person: emptyToNull(draft.contact_person),
    contact_info: emptyToNull(draft.contact_info),
    goals: draft.goals,
    platforms: draft.platforms,
    regions: splitListInput(draft.regionsText),
    selling_points: emptyToNull(draft.selling_points),
    evidence_links: splitLinesInput(draft.evidenceText),
  });
  const linksZoneBody = (): IntakeProfileWrite => ({
    website: emptyToNull(draft.website),
    wechat: emptyToNull(draft.wechat),
    douyin: emptyToNull(draft.douyin),
    social_media: emptyToNull(draft.social_media),
    audience_desc: emptyToNull(draft.audience_desc),
  });
  const qualificationZoneBody = (): IntakeProfileWrite => ({
    business_license_code: emptyToNull(draft.business_license_code),
    licenses: draft.licenses
      .filter((row) => row.name.trim() || row.number.trim() || row.expiry.trim())
      .map((row) => ({
        name: row.name.trim(),
        number: row.number.trim(),
        expiry: row.expiry.trim(),
      })),
    trademarks: splitListInput(draft.trademarksText),
    ad_review_doc_types: draft.ad_review_doc_types,
  });

  const saveZone = async (zone: string, body: IntakeProfileWrite) => {
    const strings: string[] = [];
    for (const value of Object.values(body)) {
      if (typeof value === 'string') strings.push(value);
      else if (Array.isArray(value)) {
        for (const item of value) {
          if (typeof item === 'string') strings.push(item);
          else if (item && typeof item === 'object') {
            strings.push(...Object.values(item as Record<string, string>));
          }
        }
      }
    }
    if (draftSecretViolation(strings)) {
      setZoneMessage(zone, '内容包含不允许的字符序列，请检查后重试。');
      return;
    }
    setSavingZone(zone);
    const result = await putIntakeFormProfile(token, session.profile.project_pub_id, body);
    setSavingZone('');
    if (result.kind === 'ready') {
      setZoneMessage(zone, '已保存本部分。');
      setDraft(() => initProfileDraft(result.data));
    } else {
      setZoneMessage(zone, writeFailureCopy(result.code));
    }
  };

  const addPromo = async () => {
    const payload: IntakePromoPayload = {};
    const scalarKeys =
      promoKind === 'product'
        ? ['name', 'category', 'price', 'desc']
        : ['name', 'data', 'advantage', 'cases'];
    const listKeys = promoKind === 'product' ? ['features'] : ['strength'];
    for (const key of scalarKeys) {
      const value = (promoScalars[key] ?? '').trim();
      if (value) payload[key] = value;
    }
    for (const key of listKeys) {
      const values = splitListInput(promoLists[key] ?? '');
      if (values.length > 0) payload[key] = values;
    }
    if (promoKind === 'product' && !payload.name) {
      setZoneMessage('promos', '请填写产品 / 服务名称。');
      return;
    }
    if (
      draftSecretViolation([
        ...Object.values(payload).flatMap((value) => (Array.isArray(value) ? value : [value])),
      ])
    ) {
      setZoneMessage('promos', '内容包含不允许的字符序列，请检查后重试。');
      return;
    }
    const result = await createIntakeFormPromo(token, { kind: promoKind, payload });
    if (result.kind === 'ready') {
      setPromoScalars({});
      setPromoLists({});
      setZoneMessage('promos', '已保存推广内容。');
      await reloadPromos();
    } else {
      setZoneMessage('promos', writeFailureCopy(result.code));
    }
  };

  const removePromo = async (pubId: string) => {
    const result = await deleteIntakeFormPromo(token, pubId);
    if (result.kind === 'ready') {
      await reloadPromos();
    } else {
      setZoneMessage('promos', writeFailureCopy(result.code));
    }
  };

  const prefillBadge = (key: string) =>
    key in prefilled ? <Badge tone="info">调研预填</Badge> : null;

  const promoItems = promos?.data ?? [];
  const sections = session.form.sections;

  const renderField = (field: (typeof sections)[number]['fields'][number]): ReactNode => {
    if (
      field.key === 'truth_confirmed' ||
      field.key === 'filler_name' ||
      field.key === 'trigger_questions' ||
      field.key === 'promos' ||
      field.key === 'licenses'
    ) {
      return null;
    }
    if (field.key === 'company_name') {
      return (
        <FormField key={field.key} id="pf-company-name" label={field.label}>
          <input
            id="pf-company-name"
            value={session.brand.name ?? ''}
            disabled
            aria-describedby="pf-company-name-hint"
          />
          <span className="field-hint" id="pf-company-name-hint">
            在「品牌信息」页编辑
          </span>
        </FormField>
      );
    }
    if (field.key === 'industry') {
      return (
        <FormField
          key={field.key}
          id="pf-industry"
          label={field.label}
          hint={field.hint ?? undefined}
        >
          <input
            id="pf-industry"
            value={industry}
            disabled={readOnly}
            onChange={(event) => setIndustry(event.target.value)}
          />
          <span className="field-hint">暂不写入档案，仅作为调研与方案设计参考。</span>
        </FormField>
      );
    }
    switch (field.type) {
      case 'text':
      case 'date': {
        const key = field.key as ProfileDraftStringKey;
        const value = draft[key];
        return (
          <FormField
            key={field.key}
            id={`pf-${field.key}`}
            label={field.label + (field.required ? ' ★' : '')}
            hint={field.hint ?? undefined}
          >
            {prefillBadge(field.key)}
            <input
              id={`pf-${field.key}`}
              type={field.type === 'date' ? 'date' : 'text'}
              value={value}
              disabled={readOnly}
              onChange={(event) => setScalar(key, event.target.value)}
            />
          </FormField>
        );
      }
      case 'textarea': {
        if (field.key === 'evidence_links') {
          return (
            <FormField
              key={field.key}
              id="pf-evidence"
              label={field.label}
              hint={field.hint ?? undefined}
            >
              {prefillBadge(field.key)}
              <textarea
                id="pf-evidence"
                rows={3}
                value={draft.evidenceText}
                disabled={readOnly}
                onChange={(event) => setScalar('evidenceText', event.target.value)}
              />
            </FormField>
          );
        }
        if (field.key === 'selling_points') {
          return (
            <FormField
              key={field.key}
              id="pf-selling"
              label={field.label}
              hint={field.hint ?? undefined}
            >
              {prefillBadge(field.key)}
              <textarea
                id="pf-selling"
                rows={3}
                value={draft.selling_points}
                disabled={readOnly}
                onChange={(event) => setScalar('selling_points', event.target.value)}
              />
            </FormField>
          );
        }
        return null;
      }
      case 'radio': {
        return (
          <FormField
            key={field.key}
            id={`pf-${field.key}`}
            label={field.label}
            hint={field.hint ?? undefined}
          >
            {prefillBadge(field.key)}
            <div className="intake-chips" role="radiogroup" aria-label={field.label}>
              {field.options.map((option) => (
                <Chip
                  key={option.value}
                  label={option.label}
                  pressed={draft.review_category === option.value}
                  disabled={readOnly}
                  onToggle={() => setScalar('review_category', option.value)}
                />
              ))}
            </div>
          </FormField>
        );
      }
      case 'bool': {
        return (
          <FormField
            key={field.key}
            id={`pf-${field.key}`}
            label={field.label}
            hint={field.hint ?? undefined}
          >
            {prefillBadge(field.key)}
            <div className="intake-chips">
              <Chip
                label="是"
                pressed={draft.pre_review_required === true}
                disabled={readOnly}
                onToggle={() => setDraft((current) => ({ ...current, pre_review_required: true }))}
              />
              <Chip
                label="否"
                pressed={draft.pre_review_required === false}
                disabled={readOnly}
                onToggle={() => setDraft((current) => ({ ...current, pre_review_required: false }))}
              />
            </div>
          </FormField>
        );
      }
      case 'chips': {
        const key = field.key as 'goals' | 'platforms' | 'ad_review_doc_types';
        if (!Array.isArray(draft[key])) return null;
        return (
          <FormField
            key={field.key}
            id={`pf-${field.key}`}
            label={field.label}
            hint={field.hint ?? undefined}
          >
            {prefillBadge(field.key)}
            <div className="intake-chips">
              {field.options.map((option) => (
                <Chip
                  key={option.value}
                  label={option.label}
                  pressed={(draft[key] as string[]).includes(option.value)}
                  disabled={readOnly}
                  onToggle={() => toggleListValue(key, option.value)}
                />
              ))}
            </div>
          </FormField>
        );
      }
      case 'tags': {
        const key = field.key === 'regions' ? 'regionsText' : 'trademarksText';
        return (
          <FormField
            key={field.key}
            id={`pf-${field.key}`}
            label={field.label}
            hint={field.hint ?? undefined}
          >
            {prefillBadge(field.key)}
            <input
              id={`pf-${field.key}`}
              value={draft[key]}
              placeholder="逗号分隔"
              disabled={readOnly}
              onChange={(event) => setScalar(key, event.target.value)}
            />
          </FormField>
        );
      }
      default:
        return null;
    }
  };

  return (
    <>
      {sections.map((section) => {
        const zone = section.id === 'promo' ? 'profile-promo' : 'profile-qualification';
        const body = zone === 'profile-promo' ? promoZoneBody : qualificationZoneBody;
        return (
          <section className="intake-card" key={section.id} aria-label={section.title}>
            {section.id === 'promo' ? <h2>客户信息表</h2> : null}
            {section.id === 'promo' ? <p className="intake-muted">{session.form.note}</p> : null}
            <h3>{section.title}</h3>
            {section.fields.map((field) => renderField(field))}
            {section.id === 'promo' ? (
              <>
                <h3>资料链接（选填）</h3>
                <div className="intake-row">
                  <FormField id="pf-website" label="官网链接">
                    {prefillBadge('website')}
                    <input
                      id="pf-website"
                      value={draft.website}
                      placeholder="https://"
                      disabled={readOnly}
                      onChange={(event) => setScalar('website', event.target.value)}
                    />
                  </FormField>
                  <FormField id="pf-wechat" label="微信公众号 / 小程序">
                    {prefillBadge('wechat')}
                    <input
                      id="pf-wechat"
                      value={draft.wechat}
                      disabled={readOnly}
                      onChange={(event) => setScalar('wechat', event.target.value)}
                    />
                  </FormField>
                </div>
                <div className="intake-row">
                  <FormField id="pf-douyin" label="抖音 / 视频号">
                    {prefillBadge('douyin')}
                    <input
                      id="pf-douyin"
                      value={draft.douyin}
                      disabled={readOnly}
                      onChange={(event) => setScalar('douyin', event.target.value)}
                    />
                  </FormField>
                  <FormField id="pf-social" label="其他社媒（小红书 / B站 / 微博）">
                    {prefillBadge('social_media')}
                    <input
                      id="pf-social"
                      value={draft.social_media}
                      disabled={readOnly}
                      onChange={(event) => setScalar('social_media', event.target.value)}
                    />
                  </FormField>
                </div>
                <FormField id="pf-audience" label="决策人画像补充">
                  {prefillBadge('audience_desc')}
                  <input
                    id="pf-audience"
                    value={draft.audience_desc}
                    disabled={readOnly}
                    onChange={(event) => setScalar('audience_desc', event.target.value)}
                  />
                </FormField>
                <div>
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={readOnly || savingZone === 'profile-links'}
                    onClick={() => saveZone('profile-links', linksZoneBody())}
                  >
                    保存资料链接
                  </button>
                </div>
                <ZoneMessage zone="profile-links" messages={zoneMessages} />

                <h3>拟推广产品 / 服务 {prefillBadge('promos')}</h3>
                {promoItems.length === 0 ? (
                  <p className="intake-muted">尚未添加推广内容。</p>
                ) : (
                  <ul className="intake-list">
                    {promoItems.map((item) => (
                      <li key={item.pub_id}>
                        <span>
                          <Badge tone={item.kind === 'product' ? 'info' : 'neutral'}>
                            {item.kind === 'product' ? '产品 / 服务' : '公司 / 品牌'}
                          </Badge>{' '}
                          {typeof item.payload.name === 'string' && item.payload.name
                            ? item.payload.name
                            : '（未命名）'}
                        </span>
                        <button
                          type="button"
                          className="button button-secondary"
                          disabled={readOnly}
                          onClick={() => removePromo(item.pub_id)}
                        >
                          删除
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {promos && promos.projection.invalid ? (
                  <p className="intake-muted">
                    部分推广内容因形状异常未展示，完整数据以服务端为准。
                  </p>
                ) : null}
                <div className="intake-chips" role="radiogroup" aria-label="推广对象类型">
                  <Chip
                    label="产品 / 服务"
                    pressed={promoKind === 'product'}
                    disabled={readOnly}
                    onToggle={() => setPromoKind('product')}
                  />
                  <Chip
                    label="公司 / 品牌"
                    pressed={promoKind === 'company'}
                    disabled={readOnly}
                    onToggle={() => setPromoKind('company')}
                  />
                </div>
                {promoKind === 'product' ? (
                  <>
                    <div className="intake-row">
                      <FormField id="promo-name" label="产品 / 服务名称 ★">
                        <input
                          id="promo-name"
                          value={promoScalars.name ?? ''}
                          disabled={readOnly}
                          onChange={(event) =>
                            setPromoScalars((current) => ({ ...current, name: event.target.value }))
                          }
                        />
                      </FormField>
                      <FormField id="promo-category" label="类型">
                        <input
                          id="promo-category"
                          value={promoScalars.category ?? ''}
                          disabled={readOnly}
                          onChange={(event) =>
                            setPromoScalars((current) => ({
                              ...current,
                              category: event.target.value,
                            }))
                          }
                        />
                      </FormField>
                      <FormField id="promo-price" label="价格区间">
                        <input
                          id="promo-price"
                          value={promoScalars.price ?? ''}
                          disabled={readOnly}
                          onChange={(event) =>
                            setPromoScalars((current) => ({
                              ...current,
                              price: event.target.value,
                            }))
                          }
                        />
                      </FormField>
                    </div>
                    <FormField id="promo-features" label="核心卖点（逗号分隔）">
                      <input
                        id="promo-features"
                        value={promoLists.features ?? ''}
                        disabled={readOnly}
                        onChange={(event) =>
                          setPromoLists((current) => ({ ...current, features: event.target.value }))
                        }
                      />
                    </FormField>
                    <FormField id="promo-desc" label="功能与特点">
                      <textarea
                        id="promo-desc"
                        rows={2}
                        value={promoScalars.desc ?? ''}
                        disabled={readOnly}
                        onChange={(event) =>
                          setPromoScalars((current) => ({ ...current, desc: event.target.value }))
                        }
                      />
                    </FormField>
                  </>
                ) : (
                  <>
                    <div className="intake-row">
                      <FormField id="promo-cname" label="公司 / 品牌名称">
                        <input
                          id="promo-cname"
                          value={promoScalars.name ?? ''}
                          disabled={readOnly}
                          onChange={(event) =>
                            setPromoScalars((current) => ({ ...current, name: event.target.value }))
                          }
                        />
                      </FormField>
                      <FormField id="promo-data" label="关键数据">
                        <input
                          id="promo-data"
                          value={promoScalars.data ?? ''}
                          disabled={readOnly}
                          onChange={(event) =>
                            setPromoScalars((current) => ({ ...current, data: event.target.value }))
                          }
                        />
                      </FormField>
                    </div>
                    <FormField id="promo-strength" label="企业实力（逗号分隔）">
                      <input
                        id="promo-strength"
                        value={promoLists.strength ?? ''}
                        disabled={readOnly}
                        onChange={(event) =>
                          setPromoLists((current) => ({ ...current, strength: event.target.value }))
                        }
                      />
                    </FormField>
                    <FormField id="promo-advantage" label="核心差异化优势">
                      <textarea
                        id="promo-advantage"
                        rows={2}
                        value={promoScalars.advantage ?? ''}
                        disabled={readOnly}
                        onChange={(event) =>
                          setPromoScalars((current) => ({
                            ...current,
                            advantage: event.target.value,
                          }))
                        }
                      />
                    </FormField>
                    <FormField id="promo-cases" label="代表性成功案例">
                      <textarea
                        id="promo-cases"
                        rows={2}
                        value={promoScalars.cases ?? ''}
                        disabled={readOnly}
                        onChange={(event) =>
                          setPromoScalars((current) => ({ ...current, cases: event.target.value }))
                        }
                      />
                    </FormField>
                  </>
                )}
                <div>
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={readOnly}
                    onClick={addPromo}
                  >
                    添加推广内容
                  </button>
                </div>
                <ZoneMessage zone="promos" messages={zoneMessages} />
              </>
            ) : null}
            {section.id === 'qualification' ? (
              <>
                <h3>行业许可证（须持证经营行业必填）{prefillBadge('licenses')}</h3>
                {draft.licenses.length === 0 ? (
                  <p className="intake-muted">尚未登记许可证。</p>
                ) : (
                  <ul className="intake-list">
                    {draft.licenses.map((row, index) => (
                      <li key={`${row.name}-${index}`}>
                        <span>
                          {row.name}　{row.number}　{row.expiry ? `有效期至 ${row.expiry}` : ''}
                        </span>
                        <button
                          type="button"
                          className="button button-secondary"
                          disabled={readOnly}
                          onClick={() =>
                            setDraft((current) => ({
                              ...current,
                              licenses: current.licenses.filter(
                                (_, rowIndex) => rowIndex !== index,
                              ),
                            }))
                          }
                        >
                          移除
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="intake-row">
                  <FormField id="license-name" label="证照名称">
                    <input
                      id="license-name"
                      value={licenseDraft.name}
                      disabled={readOnly}
                      onChange={(event) =>
                        setLicenseDraft((current) => ({ ...current, name: event.target.value }))
                      }
                    />
                  </FormField>
                  <FormField id="license-number" label="编号">
                    <input
                      id="license-number"
                      value={licenseDraft.number}
                      disabled={readOnly}
                      onChange={(event) =>
                        setLicenseDraft((current) => ({ ...current, number: event.target.value }))
                      }
                    />
                  </FormField>
                  <FormField id="license-expiry" label="有效期至">
                    <input
                      id="license-expiry"
                      type="date"
                      value={licenseDraft.expiry}
                      disabled={readOnly}
                      onChange={(event) =>
                        setLicenseDraft((current) => ({ ...current, expiry: event.target.value }))
                      }
                    />
                  </FormField>
                </div>
                <div>
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={readOnly}
                    onClick={() => {
                      const name = licenseDraft.name.trim();
                      const number = licenseDraft.number.trim();
                      const expiry = licenseDraft.expiry.trim();
                      if (!name || !number) {
                        setZoneMessage('profile-qualification', '请填写证照名称与编号。');
                        return;
                      }
                      if (draftSecretViolation([name, number, expiry])) {
                        setZoneMessage(
                          'profile-qualification',
                          '内容包含不允许的字符序列，请检查后重试。',
                        );
                        return;
                      }
                      setDraft((current) => ({
                        ...current,
                        licenses: [...current.licenses, { name, number, expiry }],
                      }));
                      setLicenseDraft({ name: '', number: '', expiry: '' });
                    }}
                  >
                    加入许可证清单
                  </button>
                </div>
              </>
            ) : null}
            <div>
              <button
                type="button"
                className="button"
                disabled={readOnly || savingZone === zone}
                onClick={() => saveZone(zone, body())}
              >
                {zone === 'profile-promo' ? '保存本部分（宣传内容与目标）' : '保存本部分（资质）'}
              </button>
            </div>
            <ZoneMessage zone={zone} messages={zoneMessages} />
          </section>
        );
      })}
    </>
  );
}

type QuestionsSectionProps = SessionProps & {
  triggers: ProjectedCollection<IntakeTriggerView> | null;
  reloadTriggers: () => Promise<void>;
};

function QuestionsSection({
  token,
  session,
  readOnly,
  setZoneMessage,
  zoneMessages,
  triggers,
  reloadTriggers,
}: QuestionsSectionProps) {
  const [manualText, setManualText] = useState('');
  const [coreWords, setCoreWords] = useState('');
  const [suggesting, setSuggesting] = useState(false);
  const [suggestions, setSuggestions] = useState<IntakeFormSuggestion[] | null>(null);
  const [suggestionChecks, setSuggestionChecks] = useState<Record<string, boolean>>({});
  const [aiRemaining, setAiRemaining] = useState(session.invite.ai_remaining);
  const [templateRegion, setTemplateRegion] = useState('');
  const [templateCompetitor, setTemplateCompetitor] = useState('');
  const [templates, setTemplates] = useState<IntakeFormTemplateQuestions | null>(null);
  const [templateChecks, setTemplateChecks] = useState<Record<string, boolean>>({});
  const [templateLoading, setTemplateLoading] = useState(false);

  const collect = async (lines: string[], zone: string): Promise<boolean> => {
    if (lines.length === 0) {
      setZoneMessage(zone, '请先填写或勾选要收录的问法。');
      return false;
    }
    if (draftSecretViolation(lines)) {
      setZoneMessage(zone, '内容包含不允许的字符序列，请检查后重试。');
      return false;
    }
    const result = await createIntakeFormTriggers(token, lines.join('\n'));
    if (result.kind === 'ready') {
      setZoneMessage(
        zone,
        `已保存收录（新增 ${result.data.items.length} 条${
          result.data.skipped_duplicates.length > 0
            ? `，跳过重复 ${result.data.skipped_duplicates.length} 条`
            : ''
        }）。`,
      );
      await reloadTriggers();
      return true;
    }
    setZoneMessage(zone, writeFailureCopy(result.code));
    return false;
  };

  const runSuggest = async () => {
    const words = splitListInput(coreWords);
    if (words.length === 0) {
      setZoneMessage('suggest', '请先填写核心词。');
      return;
    }
    if (draftSecretViolation(words)) {
      setZoneMessage('suggest', '内容包含不允许的字符序列，请检查后重试。');
      return;
    }
    setSuggesting(true);
    const result = await suggestIntakeFormQuestions(token, { core_words: words });
    setSuggesting(false);
    if (result.kind === 'ready') {
      setSuggestions(result.data.items);
      setAiRemaining(result.data.ai_remaining);
      setSuggestionChecks({});
    } else {
      setZoneMessage('suggest', writeFailureCopy(result.code));
    }
  };

  const loadTemplates = async () => {
    setTemplateLoading(true);
    const result = await getIntakeFormSiliconTemplateQuestions(token, {
      ...(templateRegion.trim() ? { region: templateRegion.trim() } : {}),
      ...(templateCompetitor.trim() ? { competitor: templateCompetitor.trim() } : {}),
    });
    setTemplateLoading(false);
    if (result.kind === 'ready') {
      setTemplates(result.data);
      setTemplateChecks({});
    } else {
      setZoneMessage('templates', writeFailureCopy(result.code));
    }
  };

  const removeTrigger = async (pubId: string) => {
    const result = await deleteIntakeFormTrigger(token, pubId);
    if (result.kind === 'ready') {
      await reloadTriggers();
    } else {
      setZoneMessage('questions-list', writeFailureCopy(result.code));
    }
  };

  const triggerItems = triggers?.data ?? [];
  const checkedSuggestions = (suggestions ?? []).filter((item) => suggestionChecks[item.question]);
  const checkedTemplates = (templates && templates.available ? templates.questions : []).filter(
    (item: IntakeFormTemplateQuestion) => templateChecks[item.text],
  );

  return (
    <>
      <section className="intake-card" aria-labelledby="intake-questions-heading">
        <h2 id="intake-questions-heading">期望问法</h2>
        <p className="intake-muted">
          用户会怎么问 AI？这是方案设计的最重要输入，建议 3-5 条以上，每行一条。
        </p>
        {triggerItems.length === 0 ? (
          <p className="intake-muted">尚未收录问法。</p>
        ) : (
          <ul className="intake-list">
            {triggerItems.map((item) => (
              <li key={item.pub_id}>
                <span>
                  {item.text}{' '}
                  {item.status !== 'draft' ? <Badge tone="neutral">已入方案</Badge> : null}
                </span>
                {item.status === 'draft' ? (
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={readOnly}
                    onClick={() => removeTrigger(item.pub_id)}
                  >
                    删除
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {triggers && triggers.projection.invalid ? (
          <p className="intake-muted">部分问法因形状异常未展示，完整数据以服务端为准。</p>
        ) : null}
        <FormField id="manual-questions" label="手工添加（每行一条）">
          <textarea
            id="manual-questions"
            rows={3}
            value={manualText}
            disabled={readOnly}
            onChange={(event) => setManualText(event.target.value)}
          />
        </FormField>
        <div>
          <button
            type="button"
            className="button"
            disabled={readOnly || !manualText.trim()}
            onClick={() => {
              void collect(splitLinesInput(manualText), 'questions-list').then((collected) => {
                if (collected) setManualText('');
              });
            }}
          >
            批量收录
          </button>
        </div>
        <ZoneMessage zone="questions-list" messages={zoneMessages} />
      </section>

      <section className="intake-card" aria-labelledby="intake-suggest-heading">
        <h3 id="intake-suggest-heading">AI 扩写候选</h3>
        <p className="intake-muted">
          输入核心词，由 AI 扩写候选问法；候选仅供勾选，确认收录后才写入。
          <Badge tone="info">剩余 AI 次数 {aiRemaining}</Badge>
        </p>
        <div className="intake-row">
          <FormField id="suggest-words" label="核心词（逗号分隔）">
            <input
              id="suggest-words"
              value={coreWords}
              disabled={readOnly || suggesting}
              onChange={(event) => setCoreWords(event.target.value)}
            />
          </FormField>
        </div>
        <div>
          <button
            type="button"
            className="button"
            disabled={readOnly || suggesting || !coreWords.trim() || aiRemaining <= 0}
            onClick={runSuggest}
          >
            {suggesting ? '正在扩写…' : 'AI 扩写'}
          </button>
        </div>
        {suggestions !== null ? (
          suggestions.length === 0 ? (
            <p className="intake-muted">本次未产生新候选（可能均与已收录重复）。</p>
          ) : (
            <>
              <div className="intake-checks">
                {suggestions.map((item) => (
                  <label key={item.question}>
                    <input
                      type="checkbox"
                      checked={suggestionChecks[item.question] ?? false}
                      disabled={readOnly}
                      onChange={(event) =>
                        setSuggestionChecks((current) => ({
                          ...current,
                          [item.question]: event.target.checked,
                        }))
                      }
                    />
                    <span>
                      {item.question} <Badge tone="neutral">{item.core_word}</Badge>{' '}
                      <Badge tone={item.heat >= 70 ? 'positive' : 'info'}>热度 {item.heat}</Badge>
                    </span>
                  </label>
                ))}
              </div>
              <div>
                <button
                  type="button"
                  className="button"
                  disabled={readOnly || checkedSuggestions.length === 0}
                  onClick={() =>
                    collect(
                      checkedSuggestions.map((item) => item.question),
                      'suggest',
                    )
                  }
                >
                  收录所选（{checkedSuggestions.length}）
                </button>
              </div>
            </>
          )
        ) : null}
        <ZoneMessage zone="suggest" messages={zoneMessages} />
      </section>

      {templates === null || templates.available ? (
        <section className="intake-card" aria-labelledby="intake-templates-heading">
          <h3 id="intake-templates-heading">索引模板问法</h3>
          <p className="intake-muted">基于外部知识索引的问法模板，可填地域 / 竞品变量后预览。</p>
          <div className="intake-row">
            <FormField id="template-region" label="地域变量（选填）">
              <input
                id="template-region"
                value={templateRegion}
                disabled={readOnly}
                onChange={(event) => setTemplateRegion(event.target.value)}
              />
            </FormField>
            <FormField id="template-competitor" label="竞品变量（选填）">
              <input
                id="template-competitor"
                value={templateCompetitor}
                disabled={readOnly}
                onChange={(event) => setTemplateCompetitor(event.target.value)}
              />
            </FormField>
          </div>
          <div>
            <button
              type="button"
              className="button button-secondary"
              disabled={templateLoading}
              onClick={loadTemplates}
            >
              {templateLoading ? '正在生成预览…' : '预览模板问法'}
            </button>
          </div>
          {templates && templates.available ? (
            !templates.matched || templates.questions.length === 0 ? (
              <p className="intake-muted">索引未命中品牌或无可用模板。</p>
            ) : (
              <>
                <div className="intake-checks">
                  {templates.questions.map((item) => (
                    <label key={item.text}>
                      <input
                        type="checkbox"
                        checked={templateChecks[item.text] ?? false}
                        disabled={readOnly}
                        onChange={(event) =>
                          setTemplateChecks((current) => ({
                            ...current,
                            [item.text]: event.target.checked,
                          }))
                        }
                      />
                      <span>
                        {item.text}{' '}
                        {item.intent ? <Badge tone="neutral">{item.intent}</Badge> : null}
                      </span>
                    </label>
                  ))}
                </div>
                <div>
                  <button
                    type="button"
                    className="button"
                    disabled={readOnly || checkedTemplates.length === 0}
                    onClick={() =>
                      collect(
                        checkedTemplates.map((item) => item.text),
                        'templates',
                      )
                    }
                  >
                    收录所选（{checkedTemplates.length}）
                  </button>
                </div>
              </>
            )
          ) : null}
          <ZoneMessage zone="templates" messages={zoneMessages} />
        </section>
      ) : null}
    </>
  );
}

type SubmitSectionProps = SessionProps & {
  profile: IntakeProfileView;
  truthItems: string[];
  onSubmitted: (receipt: IntakeFormSubmitReceipt) => void;
};

function SubmitSection({
  token,
  session,
  readOnly,
  setZoneMessage,
  zoneMessages,
  profile,
  truthItems,
  onSubmitted,
}: SubmitSectionProps) {
  const [checks, setChecks] = useState<boolean[]>(() =>
    profile.truth_confirmed === true ? truthItems.map(() => true) : truthItems.map(() => false),
  );
  const [filler, setFiller] = useState(profile.filler_name ?? '');
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [savedProfile, setSavedProfile] = useState(profile);

  const allChecked = checks.length > 0 && checks.every(Boolean);
  const fillerOk = filler.trim().length > 0;
  const gateSaved =
    savedProfile.truth_confirmed === true && (savedProfile.filler_name ?? '') === filler.trim();
  const submitEnabled = !readOnly && allChecked && fillerOk && gateSaved && !submitting;

  const saveGate = async () => {
    if (!allChecked || !fillerOk) {
      setZoneMessage('submit', '请先逐条勾选真实性确认，并填写填表人。');
      return;
    }
    if (draftSecretViolation([filler])) {
      setZoneMessage('submit', '内容包含不允许的字符序列，请检查后重试。');
      return;
    }
    setSaving(true);
    const result = await putIntakeFormProfile(token, session.profile.project_pub_id, {
      truth_confirmed: true,
      filler_name: filler.trim(),
    });
    setSaving(false);
    if (result.kind === 'ready') {
      setSavedProfile(result.data);
      setZoneMessage('submit', '已保存确认信息，可以提交。');
    } else {
      setZoneMessage('submit', writeFailureCopy(result.code));
    }
  };

  const submit = async () => {
    setSubmitting(true);
    const result = await submitIntakeForm(token);
    setSubmitting(false);
    if (result.kind === 'ready') {
      onSubmitted(result.data);
      setZoneMessage('submit', '已提交，感谢配合。');
    } else {
      setZoneMessage('submit', writeFailureCopy(result.code));
    }
  };

  return (
    <section className="intake-card intake-submit-panel" aria-labelledby="intake-submit-heading">
      <h2 id="intake-submit-heading">确认并提交</h2>
      <p className="intake-muted">
        以下确认项须逐条阅读并全部勾选（合同附件二口径）；AI 不会代填，须由您亲笔确认。
      </p>
      <div className="intake-checks">
        {truthItems.map((item, index) => (
          <label key={item}>
            <input
              type="checkbox"
              checked={checks[index] ?? false}
              disabled={readOnly}
              onChange={(event) =>
                setChecks((current) =>
                  current.map((value, itemIndex) =>
                    itemIndex === index ? event.target.checked : value,
                  ),
                )
              }
            />
            <span>{item}</span>
          </label>
        ))}
      </div>
      <FormField id="filler-name" label="填表人（网页版以勾选提交代替签字）">
        <input
          id="filler-name"
          value={filler}
          disabled={readOnly}
          onChange={(event) => setFiller(event.target.value)}
        />
      </FormField>
      <div className="intake-row">
        <button
          type="button"
          className="button button-secondary"
          disabled={readOnly || saving || !allChecked || !fillerOk}
          onClick={saveGate}
        >
          保存确认信息
        </button>
        <button type="button" className="button" disabled={!submitEnabled} onClick={submit}>
          {submitting ? '正在提交…' : '提交信息表'}
        </button>
      </div>
      {!gateSaved && allChecked && fillerOk ? (
        <p className="intake-muted">请先「保存确认信息」，再提交。</p>
      ) : null}
      <ZoneMessage zone="submit" messages={zoneMessages} />
    </section>
  );
}

export default function IntakeFormShell() {
  const [token] = useState<string | null>(() =>
    typeof window === 'undefined' ? null : readIntakeFormToken(window.location.hash),
  );
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [phase, setPhase] = useState<
    | { kind: 'loading' }
    | { kind: 'failed'; code: IntakeFormFailureCode }
    | { kind: 'ready'; session: IntakeFormContext }
  >({ kind: 'loading' });
  const [promos, setPromos] = useState<ProjectedCollection<IntakePromoView> | null>(null);
  const [triggers, setTriggers] = useState<ProjectedCollection<IntakeTriggerView> | null>(null);
  const [draft, setDraftState] = useState<ProfileFormDraft | null>(null);
  const [receipt, setReceipt] = useState<IntakeFormSubmitReceipt | null>(null);
  const [zoneMessages, setZoneMessages] = useState<Record<string, string>>({});
  const [reloadTick, setReloadTick] = useState(0);

  const setZoneMessage = (zone: string, message: string) =>
    setZoneMessages((current) => ({ ...current, [zone]: message }));

  // token 只经 fragment 进入内存：读取后立即从地址栏清除，避免外泄到历史/截图。
  useEffect(() => {
    if (token && location.hash) {
      navigate({ pathname: location.pathname, search: location.search }, { replace: true });
    }
    // 仅在挂载时执行一次：token 已在首次渲染时进入 state。
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const load = async () => {
      const context = await getIntakeFormContext(token);
      if (cancelled) return;
      if (context.kind !== 'ready') {
        setPhase({ kind: 'failed', code: context.code });
        return;
      }
      setPhase({ kind: 'ready', session: context.data });
      setDraftState(initProfileDraft(context.data.profile));
      const [promoResult, triggerResult] = await Promise.all([
        listIntakeFormPromos(token),
        listIntakeFormTriggers(token),
      ]);
      if (cancelled) return;
      if (promoResult.kind === 'ready') setPromos(promoResult.data);
      if (triggerResult.kind === 'ready') setTriggers(triggerResult.data);
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [token, reloadTick]);

  const reloadSession = async () => {
    if (!token) return;
    const context = await getIntakeFormContext(token);
    if (context.kind === 'ready') {
      setPhase({ kind: 'ready', session: context.data });
      setDraftState(initProfileDraft(context.data.profile));
    }
  };
  const reloadPromos = async () => {
    if (!token) return;
    const result = await listIntakeFormPromos(token);
    if (result.kind === 'ready') setPromos(result.data);
  };
  const reloadTriggers = async () => {
    if (!token) return;
    const result = await listIntakeFormTriggers(token);
    if (result.kind === 'ready') setTriggers(result.data);
  };

  const section: IntakeSection = useMemo(() => {
    const param = searchParams.get('section');
    return (intakeNavIds as readonly string[]).includes(param ?? '')
      ? (param as IntakeSection)
      : 'brand';
  }, [searchParams]);

  if (!token) {
    return <TokenFailurePage code="intake_token_missing" />;
  }
  if (phase.kind === 'failed') {
    return (
      <TokenFailurePage
        code={phase.code}
        onRetry={() => {
          setPhase({ kind: 'loading' });
          setReloadTick((tick) => tick + 1);
        }}
      />
    );
  }
  if (phase.kind === 'loading' || !draft) {
    return (
      <main className="intake-shell">
        <section className="intake-card" style={{ gridColumn: '1 / -1' }}>
          <StatePanel state="loading" />
        </section>
      </main>
    );
  }

  const { session } = phase;
  const readOnly = receipt !== null || session.invite.submitted;
  const submittedAt = receipt?.submitted_at ?? session.invite.submitted_at;
  const truthItems =
    session.form.sections
      .flatMap((formSection) => formSection.fields)
      .find((field) => field.type === 'confirm')?.items ?? [];
  const shared: SessionProps = {
    token,
    session,
    readOnly,
    reloadSession,
    setZoneMessage,
    zoneMessages,
  };

  return (
    <main className={`intake-shell${readOnly ? ' intake-readonly' : ''}`}>
      <nav className="intake-nav" aria-label="表单分区">
        {nav.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-current={section === item.id}
            onClick={() => navigateClientSection(item.id, intakeNavIds)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="intake-main">
        <header className="intake-card">
          <h1>{session.form.title}</h1>
          {readOnly && submittedAt ? (
            <p className="intake-banner" role="status">
              本表已于 {submittedAt.slice(0, 19).replace('T', ' ')} 提交，内容已锁定。感谢配合。
            </p>
          ) : (
            <p className="intake-muted">
              邀请有效期至 {session.invite.expires_at.slice(0, 19).replace('T', ' ')} ·{' '}
              <Badge tone="info">剩余 AI 次数 {session.invite.ai_remaining}</Badge>
            </p>
          )}
        </header>
        {section === 'brand' ? <BrandSection {...shared} /> : null}
        {section === 'research' ? <ResearchSection {...shared} /> : null}
        {section === 'profile' ? (
          <ProfileSection
            {...shared}
            profile={session.profile}
            draft={draft}
            setDraft={(updater) =>
              setDraftState((current) => (current ? updater(current) : current))
            }
            promos={promos}
            reloadPromos={reloadPromos}
          />
        ) : null}
        {section === 'questions' ? (
          <QuestionsSection {...shared} triggers={triggers} reloadTriggers={reloadTriggers} />
        ) : null}
        {section === 'submit' ? (
          <SubmitSection
            {...shared}
            profile={session.profile}
            truthItems={truthItems}
            onSubmitted={(value) => setReceipt(value)}
          />
        ) : null}
      </div>
    </main>
  );
}
