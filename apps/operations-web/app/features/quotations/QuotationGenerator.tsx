import { useMemo, useRef, useState } from 'react';
import {
  generateQuotation,
  type GeneratedQuotationDocument,
  type IdentitySessionHeaders,
  type QuotationArtifactKind,
  type QuotationPackageCode,
  type QuotationServiceCode,
  type QuotationServiceQuoteInput,
} from '@geo/api-client';
import { StatePanel, VerifiedBlobDownload } from '@geo/design-system';
import './quotation-generator.css';

const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const MAX_XLSX_BYTES = 10 * 1024 * 1024;

type ServiceView = {
  code: QuotationServiceCode;
  number: number;
  shortName: string;
  name: string;
  unit: string;
  summary: string;
  inputs: string[];
  outputs: string[];
};

const serviceCatalog: ServiceView[] = [
  {
    code: 'ranking_test',
    number: 1,
    shortName: '测试',
    name: 'AI 推荐排名效果测试',
    unit: '轮',
    summary: '同题比较模型开放 API 与豆包 App 两个独立观测渠道，分别记录品牌提及和推荐排名。',
    inputs: ['业务问题/目标词', '竞品与地域', 'API 模型版本、App 版本、账号、窗口与重复次数'],
    outputs: ['API/App 逐题证据', '提及率、推荐排名、Top1/3/5 与竞品对比', '端侧差异说明'],
  },
  {
    code: 'outbound_disparagement_audit',
    number: 2,
    shortName: '找拉踩帖',
    name: '主动拉踩内容核查',
    unit: '项',
    summary: '只检查有作者、委托或审批归属证据的己方内容，是否以贬低或不实比较拉踩竞品。',
    inputs: ['己方帖子/稿件 URL 及归属证据', '品牌与竞品别名', '客户确认的事实材料与核查范围'],
    outputs: ['疑似拉踩帖子清单', '原文、URL、截图与事实证据', '风险分级和整改建议'],
  },
  {
    code: 'inbound_disparagement_audit',
    number: 3,
    shortName: '找被拉踩帖',
    name: '被拉踩内容核查',
    unit: '项',
    summary: '从约定检索范围和 AI 信源中发现针对客户品牌的负向比较与疑似不实内容。',
    inputs: [
      '客户品牌与潜在竞品',
      '默认复用服务 1 的回答/引用池，否则导入目标问题和 URL',
      '检索渠道、时间窗与事实材料',
    ],
    outputs: ['被拉踩内容及传播来源', '逐条原文、URL 与页面证据', '事实核查与处置优先级'],
  },
  {
    code: 'official_site_audit',
    number: 4,
    shortName: '官网分析',
    name: '官网内容 AI 引用效率分析',
    unit: '项',
    summary: '对 AI 引用 URL 中命中的客户官网页面分析引用率、内容采纳率与优化机会。',
    inputs: [
      '客户确认的官网及合法子域',
      '默认复用服务 1 的回答/引用池，否则由客户导入',
      '允许分析的官网页面范围',
    ],
    outputs: ['官网 URL 与引用证据', '引用率和回答级采纳证据', '证据不足结论或内容优化建议'],
  },
  {
    code: 'content_publishing_pilot',
    number: 5,
    shortName: '发帖提排名',
    name: '内容发布与排名提升试点',
    unit: '项',
    summary: '围绕少量目标问题发布合规内容，并由服务 1 的第二轮测试验证发布前后变化。',
    inputs: [
      '目标 Query 与品牌事实材料',
      '稿件、媒体范围、预算及发布授权',
      '服务 1 首轮基线及第二轮的一致采样配置',
    ],
    outputs: [
      '发布方案、公开 URL 与快照',
      '基于服务 1 两轮指标的试点解释',
      '试点结论、证据边界与下一阶段建议（不承诺一定提升）',
    ],
  },
];

const packageCatalog: Array<{
  code: Exclude<QuotationPackageCode, 'custom'>;
  name: string;
  audience: string;
  summary: string;
  sequence: string;
}> = [
  {
    code: 'geo_effect_assessment',
    name: '已开展 GEO · 效果评测',
    audience: '适合已经做过 GEO 的公司',
    summary: '测试排名效果，分别核查找拉踩帖与找被拉踩帖，并分析官网内容的 AI 使用效率。',
    sequence: '1 → 2 → 3 → 4',
  },
  {
    code: 'minimum_validation',
    name: '未开展 GEO · 最小化验证',
    audience: '适合希望先用最小范围验证价值的公司',
    summary: '先做基线，再查被拉踩与官网引用，完成小规模发帖，最后按同口径复测。',
    sequence: '1（基线）→ 3 → 4（命中官网时）→ 5 → 1（复测）',
  },
];

const artifactCatalog: Array<{
  kind: QuotationArtifactKind;
  name: string;
  summary: string;
}> = [
  {
    kind: 'complete',
    name: '完整报价单',
    summary: '生成报价单表格与服务说明；上传目标词时一并生成查询附件。',
  },
  {
    kind: 'quote_table',
    name: '报价单表格',
    summary: '只生成报价单表格，不解析目标词，也不生成查询附件。',
  },
  {
    kind: 'query_appendix',
    name: '查询附件',
    summary: '只生成查询附件；必须上传有效 XLSX，且服务组合包含服务 1 或 5。',
  },
];

const artifactName = (kind: QuotationArtifactKind): string =>
  artifactCatalog.find((item) => item.kind === kind)?.name ?? '完整报价单';

const effectQuantities = (): Partial<Record<QuotationServiceCode, number>> => ({
  ranking_test: 1,
  outbound_disparagement_audit: 1,
  inbound_disparagement_audit: 1,
  official_site_audit: 1,
});

const minimumQuantities = (
  officialSiteInCitations: boolean | null,
): Partial<Record<QuotationServiceCode, number>> => ({
  ranking_test: 2,
  inbound_disparagement_audit: 1,
  ...(officialSiteInCitations !== false ? { official_site_audit: 1 } : {}),
  content_publishing_pilot: 1,
});

export type QuotationSession = {
  role: 'operator' | 'reviewer' | 'admin';
  headers: IdentitySessionHeaders;
};

export function chinaDate(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const pick = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((part) => part.type === type)?.value ?? '';
  return `${pick('year')}-${pick('month')}-${pick('day')}`;
}

export function quotationDownloadName(
  brandName: string,
  quoteDate: string,
  packageCode: QuotationPackageCode = 'custom',
  artifactKind: QuotationArtifactKind = 'complete',
): string {
  const brand = brandName
    .normalize('NFC')
    .replace(/\s+/gu, ' ')
    .trim()
    .replace(/[<>:"/\\|?*\x00-\x1f]/gu, '_')
    .replace(/^[ ._]+|[ ._]+$/gu, '');
  const packageLabel = {
    geo_effect_assessment: 'GEO效果评测',
    minimum_validation: 'GEO最小验证',
    custom: 'GEO自定义',
  }[packageCode];
  const artifactLabel = {
    complete: '',
    quote_table: '-报价单表格',
    query_appendix: '-查询附件',
  }[artifactKind];
  return `报价单-${brand || '客户'}-${packageLabel}${artifactLabel}-${quoteDate.replaceAll('-', '')}.docx`;
}

export function yuanInputToCents(value: string): number | null {
  const normalized = value.trim();
  if (!/^(?:0|[1-9]\d{0,9})(?:\.\d{1,2})?$/u.test(normalized)) return null;
  const [yuan, fraction = ''] = normalized.split('.');
  const cents = Number(yuan) * 100 + Number(fraction.padEnd(2, '0'));
  return Number.isSafeInteger(cents) && cents <= 999_999_999_999 ? cents : null;
}

export function formatCny(cents: number): string {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: 2,
  }).format(cents / 100);
}

type QuotationValidationInput = {
  brandName: string;
  websiteUrl: string;
  quoteDate: string;
  packageCode: QuotationPackageCode;
  artifactKind: QuotationArtifactKind;
  officialSiteInCitations: boolean | null;
  officialSiteCitationUrl: string;
  pricingStatus: 'priced' | 'pending';
  quantities: Partial<Record<QuotationServiceCode, number>>;
  prices: Partial<Record<QuotationServiceCode, string>>;
  targetWords: File | null;
};

export function quotationInputError(input: QuotationValidationInput): string | null {
  const brand = input.brandName.normalize('NFC').replace(/\s+/gu, ' ').trim();
  if (brand.length < 2 || brand.length > 80) return '客户/品牌名称需为 2—80 个字符。';
  if (
    !input.quoteDate ||
    !/^(?:20\d{2}|2100)-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$/u.test(input.quoteDate)
  ) {
    return '请选择有效的报价日期。';
  }
  const selected = serviceCatalog.filter((service) => (input.quantities[service.code] ?? 0) > 0);
  if (selected.length === 0) return '请至少选择一项服务。';
  if (
    selected.some((service) => {
      const quantity = input.quantities[service.code] ?? 0;
      return !Number.isInteger(quantity) || quantity < 1 || quantity > 99;
    })
  ) {
    return '服务数量必须为 1—99 的整数。';
  }
  if (
    input.artifactKind === 'query_appendix' &&
    !selected.some(
      (service) => service.code === 'ranking_test' || service.code === 'content_publishing_pilot',
    )
  ) {
    return '查询附件要求服务组合至少包含服务 1（测试）或服务 5（发帖提排名）。';
  }
  if (input.artifactKind === 'query_appendix' && !input.targetWords) {
    return '查询附件必须上传包含有效目标词的 XLSX；未上传时不会生成空附件。';
  }
  if (
    input.pricingStatus === 'priced' &&
    selected.some((service) => yuanInputToCents(input.prices[service.code] ?? '') === null)
  ) {
    return '请为每项已选服务填写有效单价，最多保留两位小数。';
  }
  const maximumTotal =
    input.pricingStatus === 'priced'
      ? selected.reduce((sum, service) => {
          const price = yuanInputToCents(input.prices[service.code] ?? '') ?? 0;
          return sum + price * (input.quantities[service.code] ?? 0);
        }, 0)
      : 0;
  if (
    input.pricingStatus === 'priced' &&
    (!Number.isSafeInteger(maximumTotal) || maximumTotal > 4_999_999_999_995)
  ) {
    return '服务费总额超出系统允许范围。';
  }
  if (
    input.packageCode === 'custom' &&
    (input.quantities.content_publishing_pilot ?? 0) > 0 &&
    input.quantities.ranking_test !== 2
  ) {
    return '自定义组合选择服务 5 时，必须同时选择服务 1 并设为 2 轮。';
  }
  if (selected.some((service) => service.code === 'official_site_audit')) {
    try {
      const url = new URL(input.websiteUrl.trim());
      if (
        !['http:', 'https:'].includes(url.protocol) ||
        !url.hostname ||
        url.username !== '' ||
        url.password !== ''
      ) {
        throw new Error('invalid');
      }
    } catch {
      return '已选择官网分析，请填写包含 http:// 或 https:// 的有效官网 URL。';
    }
  }
  if (input.packageCode === 'minimum_validation' && input.officialSiteInCitations === true) {
    try {
      const website = new URL(input.websiteUrl.trim());
      const evidence = new URL(input.officialSiteCitationUrl.trim());
      if (
        !['http:', 'https:'].includes(evidence.protocol) ||
        !evidence.hostname ||
        evidence.username !== '' ||
        evidence.password !== '' ||
        (evidence.hostname.toLowerCase() !== website.hostname.toLowerCase() &&
          !evidence.hostname.toLowerCase().endsWith(`.${website.hostname.toLowerCase()}`))
      ) {
        throw new Error('invalid');
      }
    } catch {
      return '已选“官网已命中”，请填写一条 host 与客户官网一致的引用证据 URL。';
    }
  }
  if (input.packageCode !== 'minimum_validation' && input.officialSiteCitationUrl.trim() !== '') {
    return '官网引用证据只适用于最小验证套餐的“官网已命中”状态。';
  }
  if (input.targetWords) {
    if (!input.targetWords.name.toLowerCase().endsWith('.xlsx')) {
      return '目标词文件必须为 .xlsx。';
    }
    if (input.targetWords.size <= 0) return '目标词文件为空，请重新选择。';
    if (input.targetWords.size > MAX_XLSX_BYTES) return '目标词文件不能超过 10 MB。';
    if (
      input.targetWords.type &&
      input.targetWords.type !== XLSX_MIME &&
      input.targetWords.type !== 'application/octet-stream'
    ) {
      return '浏览器识别到的文件类型不是 XLSX。';
    }
  }
  return null;
}

const failureNotice = {
  forbidden: '当前账号没有生成报价单的权限，请使用运营或管理员账号。',
  invalid: '输入未通过校验，请检查制品类型、套餐、官网、单项价格和目标词工作簿。',
  disabled: '已上传目标词，但模型服务当前不可用；可移除 XLSX 先生成基础报价单。',
  failed: 'Query 附录生成失败，未输出不完整文档；可检查 XLSX 后重试。',
  unavailable: '报价单服务暂不可用，或返回文档未通过完整性校验，请稍后重试。',
} as const;

export function QuotationGenerator({ session }: { session: QuotationSession | undefined }) {
  const [brandName, setBrandName] = useState('');
  const [websiteUrl, setWebsiteUrl] = useState('');
  const [officialSiteCitationUrl, setOfficialSiteCitationUrl] = useState('');
  const [quoteDate, setQuoteDate] = useState(chinaDate);
  const [packageCode, setPackageCode] = useState<QuotationPackageCode>('geo_effect_assessment');
  const [officialSiteInCitations, setOfficialSiteInCitations] = useState<boolean | null>(true);
  const [quantities, setQuantities] =
    useState<Partial<Record<QuotationServiceCode, number>>>(effectQuantities);
  const [prices, setPrices] = useState<Partial<Record<QuotationServiceCode, string>>>({});
  const [commercialNote, setCommercialNote] = useState('');
  const [pricingStatus, setPricingStatus] = useState<'priced' | 'pending'>('priced');
  const [artifactKind, setArtifactKind] = useState<QuotationArtifactKind>('complete');
  const [targetWords, setTargetWords] = useState<File | null>(null);
  const [notice, setNotice] = useState<{ tone: 'info' | 'success' | 'error'; text: string } | null>(
    null,
  );
  const [receipt, setReceipt] = useState<GeneratedQuotationDocument | null>(null);
  const [inputRevision, setInputRevision] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const canGenerate = session?.role === 'operator' || session?.role === 'admin';

  const selectedServices = serviceCatalog.filter((service) => (quantities[service.code] ?? 0) > 0);
  const hasQueryAppendixService = selectedServices.some(
    (service) => service.code === 'ranking_test' || service.code === 'content_publishing_pilot',
  );
  const serviceQuotes = selectedServices.flatMap<QuotationServiceQuoteInput>((service) => {
    if (pricingStatus === 'pending') {
      return [
        {
          serviceCode: service.code,
          quantity: quantities[service.code] ?? 1,
          unitPriceCents: null,
        },
      ];
    }
    const unitPriceCents = yuanInputToCents(prices[service.code] ?? '');
    return unitPriceCents === null
      ? []
      : [{ serviceCode: service.code, quantity: quantities[service.code] ?? 1, unitPriceCents }];
  });
  const totalPriceCents = serviceQuotes.reduce(
    (sum, quote) =>
      sum +
      (packageCode === 'minimum_validation' &&
      officialSiteInCitations === null &&
      quote.serviceCode === 'official_site_audit'
        ? 0
        : quote.quantity * (quote.unitPriceCents ?? 0)),
    0,
  );
  const maximumTotalPriceCents = serviceQuotes.reduce(
    (sum, quote) => sum + quote.quantity * (quote.unitPriceCents ?? 0),
    0,
  );
  const inputError = quotationInputError({
    brandName,
    websiteUrl,
    quoteDate,
    packageCode,
    artifactKind,
    officialSiteInCitations,
    officialSiteCitationUrl,
    pricingStatus,
    quantities,
    prices,
    targetWords,
  });
  const downloadName = quotationDownloadName(brandName, quoteDate, packageCode, artifactKind);
  const resourceKey = `quotation-${artifactKind}-${inputRevision}`;

  const selectedSequence = useMemo(() => {
    if (packageCode === 'geo_effect_assessment') return '1 → 2 → 3 → 4';
    if (packageCode === 'minimum_validation') {
      return officialSiteInCitations
        ? '1（基线）→ 3 → 4 → 5 → 1（复测）'
        : officialSiteInCitations === false
          ? '1（基线）→ 3 → 5 → 1（复测）'
          : '1（基线）→ 3 → 4（命中后）→ 5 → 1（复测）';
    }
    if ((quantities.content_publishing_pilot ?? 0) > 0 && quantities.ranking_test === 2) {
      return [
        '1（基线）',
        ...selectedServices
          .filter((service) => service.code !== 'ranking_test')
          .map((service) => String(service.number)),
        '1（复测）',
      ].join(' → ');
    }
    return (
      selectedServices
        .map((service) =>
          service.code === 'ranking_test' && (quantities.ranking_test ?? 0) > 1
            ? `1（${quantities.ranking_test}轮）`
            : String(service.number),
        )
        .join(' → ') || '尚未选择'
    );
  }, [officialSiteInCitations, packageCode, quantities, selectedServices]);

  if (!session) {
    return (
      <main className="quotation-page">
        <StatePanel state="forbidden" />
      </main>
    );
  }

  const touch = () => {
    setInputRevision((value) => value + 1);
    setReceipt(null);
    setNotice(null);
  };

  const selectPackage = (code: QuotationPackageCode) => {
    setPackageCode(code);
    setOfficialSiteCitationUrl('');
    if (code === 'geo_effect_assessment') {
      setOfficialSiteInCitations(true);
      setQuantities(effectQuantities());
    } else if (code === 'minimum_validation') {
      setOfficialSiteInCitations(null);
      setOfficialSiteCitationUrl('');
      setQuantities(minimumQuantities(null));
    }
    touch();
  };

  const setCustomServiceSelection = (code: QuotationServiceCode, selected: boolean) => {
    setPackageCode('custom');
    setOfficialSiteCitationUrl('');
    setQuantities((current) => {
      const next = { ...current };
      if (selected) next[code] = current[code] ?? 1;
      else delete next[code];
      return next;
    });
    touch();
  };

  const selectArtifactKind = (kind: QuotationArtifactKind) => {
    setArtifactKind(kind);
    if (kind === 'quote_table') {
      setTargetWords(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
    touch();
  };

  const load = async () => {
    if (!canGenerate || inputError || serviceQuotes.length !== selectedServices.length) {
      setNotice({
        tone: 'error',
        text: !canGenerate ? failureNotice.forbidden : (inputError ?? '请补全报价单信息。'),
      });
      return { kind: 'unavailable' as const };
    }
    setReceipt(null);
    setNotice({
      tone: 'info',
      text:
        artifactKind === 'quote_table'
          ? '正在核对逐项报价并生成报价单表格 DOCX…'
          : artifactKind === 'query_appendix'
            ? '正在解析目标词并生成查询附件 DOCX…'
            : targetWords
              ? '正在核对报价、解析目标词并生成完整报价单 DOCX…'
              : '正在核对逐项报价并生成完整报价单 DOCX…',
    });
    const result = await generateQuotation(
      {
        brandName,
        packageCode,
        artifactKind,
        websiteUrl,
        serviceQuotes,
        pricingStatus,
        officialSiteInCitations,
        officialSiteCitationUrl,
        commercialNote,
        ...(targetWords ? { targetWords } : {}),
        quoteDate,
      },
      session.headers,
    );
    if (result.kind !== 'ready') {
      setNotice({ tone: 'error', text: failureNotice[result.kind] });
      return {
        kind: result.kind === 'forbidden' ? ('forbidden' as const) : ('unavailable' as const),
      };
    }
    setReceipt(result.data);
    setNotice({ tone: 'success', text: `报价单已生成并下载：${result.data.fileName}` });
    return { kind: 'ready' as const, blob: result.data.blob };
  };

  return (
    <main className="quotation-page">
      <section className="quotation-hero">
        <div>
          <span className="quotation-eyebrow">GEO 商务工具</span>
          <h1>报价单生成</h1>
          <p>五项服务独立定价。套餐负责组合与执行顺序，系统根据“单价 × 数量”计算小计和总价。</p>
        </div>
        <div className="quotation-scope" aria-label="计价规则">
          <strong>单项价格是唯一金额输入</strong>
          <span>套餐不覆盖单价 · 后端重新计算总价 · DOCX 带输入与交付说明</span>
        </div>
      </section>

      <section className="quotation-card" aria-labelledby="quotation-package-title">
        <div className="quotation-section-title">
          <div>
            <span>步骤 1</span>
            <h2 id="quotation-package-title">选择客户阶段与套餐</h2>
          </div>
          <span className="quotation-security">套餐只是服务组合</span>
        </div>
        <div className="quotation-package-grid">
          {packageCatalog.map((item) => (
            <button
              type="button"
              key={item.code}
              className={`quotation-package ${packageCode === item.code ? 'selected' : ''}`}
              aria-pressed={packageCode === item.code}
              onClick={() => selectPackage(item.code)}
            >
              <strong>{item.name}</strong>
              <span>{item.audience}</span>
              <p>{item.summary}</p>
              <code>{item.sequence}</code>
            </button>
          ))}
          <button
            type="button"
            className={`quotation-package ${packageCode === 'custom' ? 'selected' : ''}`}
            aria-pressed={packageCode === 'custom'}
            onClick={() => selectPackage('custom')}
          >
            <strong>自定义组合</strong>
            <span>适合只需要部分服务的客户</span>
            <p>保留当前选择后自由增删服务、调整数量；每项仍单独报价。</p>
            <code>{selectedSequence}</code>
          </button>
        </div>

        {packageCode === 'minimum_validation' ? (
          <fieldset className="quotation-condition">
            <legend>本轮 AI 引用 URL 中是否已确认命中客户官网？</legend>
            <p>未知时单独列示服务 4 的单价，但不计入基础总价；页面同时显示触发后的最高总价。</p>
            <label>
              <input
                type="radio"
                name="official-site-hit"
                checked={officialSiteInCitations === null}
                onChange={() => {
                  setOfficialSiteInCitations(null);
                  setOfficialSiteCitationUrl('');
                  setQuantities(minimumQuantities(null));
                  touch();
                }}
              />
              待首轮测试确认（条件报价）
            </label>
            <label>
              <input
                type="radio"
                name="official-site-hit"
                checked={officialSiteInCitations === true}
                onChange={() => {
                  setOfficialSiteInCitations(true);
                  setQuantities(minimumQuantities(true));
                  touch();
                }}
              />
              是，计入官网分析
            </label>
            <label>
              <input
                type="radio"
                name="official-site-hit"
                checked={officialSiteInCitations === false}
                onChange={() => {
                  setOfficialSiteInCitations(false);
                  setOfficialSiteCitationUrl('');
                  setQuantities(minimumQuantities(false));
                  touch();
                }}
              />
              否，本次不计入
            </label>
            {officialSiteInCitations === true ? (
              <label className="quotation-wide-field">
                官网引用证据 URL
                <input
                  type="url"
                  value={officialSiteCitationUrl}
                  maxLength={2000}
                  placeholder="https://www.example.com/cited-page"
                  onChange={(event) => {
                    setOfficialSiteCitationUrl(event.target.value);
                    touch();
                  }}
                />
              </label>
            ) : null}
          </fieldset>
        ) : null}
      </section>

      <section className="quotation-card" aria-labelledby="quotation-customer-title">
        <div className="quotation-section-title">
          <div>
            <span>步骤 2</span>
            <h2 id="quotation-customer-title">填写客户与报价信息</h2>
          </div>
          <span className="quotation-security">适用于任意客户</span>
        </div>
        <div className="quotation-form-grid">
          <label>
            客户/品牌名称
            <input
              value={brandName}
              maxLength={80}
              autoComplete="organization"
              placeholder="例如：盛邦安全"
              onChange={(event) => {
                setBrandName(event.target.value);
                touch();
              }}
            />
          </label>
          <label>
            报价日期
            <input
              type="date"
              min="2020-01-01"
              max="2100-12-31"
              value={quoteDate}
              onChange={(event) => {
                setQuoteDate(event.target.value);
                touch();
              }}
            />
          </label>
          <label className="quotation-wide-field">
            客户官网{' '}
            {selectedServices.some((service) => service.code === 'official_site_audit')
              ? ''
              : '（可选）'}
            <input
              type="url"
              value={websiteUrl}
              maxLength={2000}
              placeholder="https://www.example.com"
              onChange={(event) => {
                setWebsiteUrl(event.target.value);
                touch();
              }}
            />
          </label>
        </div>
      </section>

      <section className="quotation-card" aria-labelledby="quotation-services-title">
        <div className="quotation-section-title">
          <div>
            <span>步骤 3</span>
            <h2 id="quotation-services-title">确认服务输入、输出与逐项价格</h2>
          </div>
          <span className="quotation-security">执行顺序 {selectedSequence}</span>
        </div>
        <fieldset className="quotation-pricing-mode">
          <legend>价格状态</legend>
          <label>
            <input
              type="radio"
              name="pricing-status"
              checked={pricingStatus === 'priced'}
              onChange={() => {
                setPricingStatus('priced');
                touch();
              }}
            />
            已确认价格（必须逐项填写）
          </label>
          <label>
            <input
              type="radio"
              name="pricing-status"
              checked={pricingStatus === 'pending'}
              onChange={() => {
                setPricingStatus('pending');
                touch();
              }}
            />
            价格待确认样稿（不构成正式价格承诺）
          </label>
        </fieldset>
        {packageCode === 'custom' ? (
          <div className="quotation-custom-services">
            <div>
              <strong>添加或移除服务</strong>
              <p>自定义组合只在下方展示已选服务；可随时从此服务目录增删。</p>
            </div>
            <div className="quotation-service-picker" role="group" aria-label="自定义服务选择">
              {serviceCatalog.map((service) => {
                const selected = (quantities[service.code] ?? 0) > 0;
                return (
                  <button
                    type="button"
                    key={service.code}
                    className={selected ? 'selected' : ''}
                    aria-pressed={selected}
                    aria-label={`${selected ? '移除' : '添加'}服务 ${service.number}：${service.shortName}`}
                    onClick={() => setCustomServiceSelection(service.code, !selected)}
                  >
                    <span>{service.number}</span>
                    <span>
                      <strong>{service.shortName}</strong>
                      <small>{selected ? '已选择 · 点击移除' : '未选择 · 点击添加'}</small>
                    </span>
                  </button>
                );
              })}
            </div>
            <small>服务 5 必须与服务 1 的 2 轮测试同时选择，分别用于基线和同口径复测。</small>
          </div>
        ) : null}
        <div className="quotation-service-list">
          {selectedServices.map((service) => {
            const quantity = quantities[service.code] ?? 1;
            const unitPriceCents = yuanInputToCents(prices[service.code] ?? '');
            const conditionalLine =
              packageCode === 'minimum_validation' &&
              officialSiteInCitations === null &&
              service.code === 'official_site_audit';
            return (
              <article key={service.code} className="quotation-service selected">
                <header>
                  <label className="quotation-service-check">
                    <input
                      type="checkbox"
                      checked
                      aria-label={`移除服务 ${service.number}：${service.shortName}`}
                      onChange={() => setCustomServiceSelection(service.code, false)}
                    />
                    <span>{service.number}</span>
                    <div>
                      <strong>{service.shortName}</strong>
                      <small>{service.name}</small>
                    </div>
                  </label>
                  <p>{service.summary}</p>
                </header>
                <div className="quotation-io-grid">
                  <div>
                    <strong>客户输入</strong>
                    <ul>
                      {service.inputs.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <strong>交付输出</strong>
                    <ul>
                      {service.outputs.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                </div>
                <div className="quotation-line-price">
                  <label>
                    单价（元/{service.unit}）
                    <input
                      aria-label={`${service.shortName}单价（元/${service.unit}）`}
                      inputMode="decimal"
                      value={prices[service.code] ?? ''}
                      disabled={pricingStatus === 'pending'}
                      placeholder={pricingStatus === 'pending' ? '待商务确认' : '0.00'}
                      onChange={(event) => {
                        setPrices((current) => ({
                          ...current,
                          [service.code]: event.target.value,
                        }));
                        touch();
                      }}
                    />
                  </label>
                  <label>
                    数量（{service.unit}）
                    <input
                      aria-label={`${service.shortName}数量（${service.unit}）`}
                      type="number"
                      min={1}
                      max={99}
                      step={1}
                      value={quantity}
                      onChange={(event) => {
                        setPackageCode('custom');
                        setOfficialSiteCitationUrl('');
                        setQuantities((current) => ({
                          ...current,
                          [service.code]: Number(event.target.value),
                        }));
                        touch();
                      }}
                    />
                  </label>
                  <div>
                    <span>本项小计</span>
                    <strong>
                      {pricingStatus === 'pending'
                        ? '待确认'
                        : unitPriceCents !== null
                          ? `${conditionalLine ? '触发后 ' : ''}${formatCny(unitPriceCents * quantity)}`
                          : '待填写'}
                    </strong>
                  </div>
                </div>
                {service.code === 'ranking_test' &&
                quantity === 2 &&
                (quantities.content_publishing_pilot ?? 0) > 0 ? (
                  <p className="quotation-phase-note">2 轮分别用于发帖前基线和发帖后同口径复测。</p>
                ) : null}
              </article>
            );
          })}
          {selectedServices.length === 0 ? (
            <p className="quotation-empty-services" role="status">
              尚未选择服务，请从上方“添加或移除服务”目录添加至少一项。
            </p>
          ) : null}
        </div>
        <div className="quotation-total" aria-live="polite">
          <div>
            <span>
              {packageCode === 'minimum_validation' && officialSiteInCitations === null
                ? '基础总价（不含条件项）'
                : '服务费总价'}
            </span>
            <p>{selectedServices.length} 项服务 · 逐项小计求和 · 不接收手填总价</p>
          </div>
          <div className="quotation-total-values">
            <strong>
              {pricingStatus === 'pending'
                ? '待确认'
                : serviceQuotes.length === selectedServices.length
                  ? formatCny(totalPriceCents)
                  : '待填写'}
            </strong>
            {packageCode === 'minimum_validation' && officialSiteInCitations === null ? (
              <small>
                官网命中后最高总价{' '}
                {pricingStatus === 'pending'
                  ? '待确认'
                  : serviceQuotes.length === selectedServices.length
                    ? formatCny(maximumTotalPriceCents)
                    : '待填写'}
              </small>
            ) : null}
          </div>
        </div>
        <p className="quotation-validation">
          正式出单前还需在商务备注或合同中冻结测试矩阵、URL/页面上限、发文篇数、媒体数、观察窗和验收格式。
        </p>
      </section>

      <section className="quotation-card" aria-labelledby="quotation-output-title">
        <div className="quotation-section-title">
          <div>
            <span>步骤 4</span>
            <h2 id="quotation-output-title">补充附件并生成</h2>
          </div>
          <span className="quotation-security">原始目标词不写入日志</span>
        </div>
        <fieldset className="quotation-artifact-kind">
          <legend>DOCX 制品类型</legend>
          <div className="quotation-artifact-grid">
            {artifactCatalog.map((item) => (
              <label key={item.kind} className={artifactKind === item.kind ? 'selected' : ''}>
                <input
                  type="radio"
                  name="artifact-kind"
                  aria-label={item.name}
                  checked={artifactKind === item.kind}
                  onChange={() => selectArtifactKind(item.kind)}
                />
                <span>
                  <strong>{item.name}</strong>
                  {item.kind === 'complete' ? <small>默认</small> : null}
                </span>
                <p>{item.summary}</p>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="quotation-form-grid">
          {artifactKind === 'quote_table' ? (
            <div className="quotation-artifact-note quotation-wide-field">
              <strong>报价单表格不读取 XLSX</strong>
              <span>该制品只输出报价单表格；如需查询附件，请改选“查询附件”或“完整报价单”。</span>
            </div>
          ) : (
            <label className="quotation-file-field">
              {artifactKind === 'query_appendix'
                ? '目标词 XLSX（查询附件必填）'
                : '目标词 XLSX（可选）'}
              <input
                ref={fileInputRef}
                type="file"
                aria-label={
                  artifactKind === 'query_appendix'
                    ? '目标词 XLSX（查询附件必填）'
                    : '目标词 XLSX（可选）'
                }
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={(event) => {
                  setTargetWords(event.target.files?.[0] ?? null);
                  touch();
                }}
              />
              <small>
                {artifactKind === 'query_appendix'
                  ? '系统将校验工作簿至少包含一条有效目标词；无有效 Query 时不会生成空附件。'
                  : '不上传时生成不含查询附件的完整报价单；上传后增加品牌化 Query 附件，最大 10 MB。'}
              </small>
              {!hasQueryAppendixService ? (
                <small>
                  当前组合未选服务 1 或 5，因此无法生成 Query；请先回到服务目录添加对应服务。
                </small>
              ) : null}
            </label>
          )}
          <label className="quotation-wide-field">
            商务备注（可选）
            <textarea
              value={commercialNote}
              maxLength={500}
              rows={3}
              placeholder="例如：第三方媒体实际采购费用是否包含在服务 5 单价中。"
              onChange={(event) => {
                setCommercialNote(event.target.value);
                touch();
              }}
            />
          </label>
        </div>
        {inputError ? <p className="quotation-validation">{inputError}</p> : null}
        {!canGenerate ? <p className="quotation-validation">{failureNotice.forbidden}</p> : null}
        <div className="quotation-actions">
          {canGenerate && !inputError ? (
            <VerifiedBlobDownload
              load={load}
              fileName={downloadName}
              resourceKey={resourceKey}
              label="生成并下载 DOCX"
              loadingLabel="生成中，请稍候…"
              failureLabel="本次未生成可下载文档，请查看上方提示。"
              successLabel="报价单已通过完整性校验并下载"
            />
          ) : (
            <button type="button" className="button button-primary" disabled>
              生成并下载 DOCX
            </button>
          )}
          <button
            type="button"
            className="button button-secondary"
            onClick={() => {
              setBrandName('');
              setWebsiteUrl('');
              setOfficialSiteCitationUrl('');
              setQuoteDate(chinaDate());
              setPackageCode('geo_effect_assessment');
              setOfficialSiteInCitations(true);
              setQuantities(effectQuantities());
              setPrices({});
              setPricingStatus('priced');
              setArtifactKind('complete');
              setCommercialNote('');
              setTargetWords(null);
              touch();
              if (fileInputRef.current) fileInputRef.current.value = '';
            }}
          >
            清空
          </button>
        </div>
        {notice ? (
          <div
            className={`quotation-notice ${notice.tone}`}
            role={notice.tone === 'error' ? 'alert' : 'status'}
          >
            {notice.text}
          </div>
        ) : null}
        {receipt ? (
          <dl className="quotation-receipt" aria-label="生成结果">
            <div>
              <dt>套餐</dt>
              <dd>
                {receipt.packageCode === 'geo_effect_assessment'
                  ? '效果评测'
                  : receipt.packageCode === 'minimum_validation'
                    ? '最小验证'
                    : '自定义'}
              </dd>
            </div>
            <div>
              <dt>DOCX 制品</dt>
              <dd>{artifactName(receipt.artifactKind ?? artifactKind)}</dd>
            </div>
            <div>
              <dt>服务项</dt>
              <dd>{receipt.serviceCount}</dd>
            </div>
            <div>
              <dt>价格状态</dt>
              <dd>{receipt.pricingStatus === 'priced' ? '已确认' : '待确认样稿'}</dd>
            </div>
            <div>
              <dt>
                {receipt.packageCode === 'minimum_validation' &&
                receipt.maximumTotalPriceCents !== receipt.totalPriceCents
                  ? '基础总价（不含条件项）'
                  : '服务费总价'}
              </dt>
              <dd>
                {receipt.totalPriceCents === null ? '待确认' : formatCny(receipt.totalPriceCents)}
              </dd>
            </div>
            {receipt.maximumTotalPriceCents !== null &&
            receipt.maximumTotalPriceCents !== receipt.totalPriceCents ? (
              <div>
                <dt>条件触发后最高总价</dt>
                <dd>{formatCny(receipt.maximumTotalPriceCents)}</dd>
              </div>
            ) : null}
            <div>
              <dt>Query 附录</dt>
              <dd>{receipt.queryAppendixIncluded ? '已生成' : '未附加'}</dd>
            </div>
            <div>
              <dt>读取目标词</dt>
              <dd>{receipt.targetQueryCount}</dd>
            </div>
            <div>
              <dt>附录原词</dt>
              <dd>{receipt.selectedQueryCount}</dd>
            </div>
            <div>
              <dt>新增机会词</dt>
              <dd>{receipt.opportunityCount}</dd>
            </div>
            <div>
              <dt>文档校验</dt>
              <dd title={receipt.sha256}>SHA-256 {receipt.sha256.slice(0, 12)}…</dd>
            </div>
          </dl>
        ) : null}
      </section>

      <aside className="quotation-honesty">
        <strong>结果边界</strong>
        <p>
          报价确认的是服务范围和价格，不代表服务已经执行。API 与豆包 App
          的差异、拉踩归因和发帖后的排名变化都必须以项目执行证据为准。
        </p>
      </aside>
    </main>
  );
}
