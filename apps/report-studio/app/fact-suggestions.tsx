import { useEffect, useMemo, useRef, useState } from 'react';
import { Badge, StatePanel } from '@geo/design-system';
import {
  allowsFixtureIdentityHeaders,
  getReportFactSuggestions,
  type BrowserBuildIdentityEnv,
  type ReportFactSuggestionMetric,
  type ReportFactSuggestionRow,
  type ReportFactSuggestions,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';

/**
 * 报告事实建议面板：从分析链路按报价单四指标（品牌提及率/推荐排名分布/
 * Top1·Top3·Top5 出现率/竞品对比）拉取 fact_rows 草稿，人工勾选/修订后随既有
 * 创建表单提交（冻结/四产物/人工确认门零改动）。口径纪律：只取 brandrank 层
 * （method='brandrank-llm-v1'），启发式层指标不进报告；人工改值逐行留痕
 * （value_edited + original_value），绝不把系统计算数静默改写。
 *
 * 扩展组（报价单服务 2/3/4）：W3 内容风险核查（拉踩方向比率+典型案例+事实核查
 * 徽标）、W2 官网引用能效（引用率/采纳率+整改建议）、优化前后对比（before/after/
 * diff 三列）。扩展组在响应独立键（w3_disparagement/w2_site_audit/before_after），
 * api-client 生成的投影对主数组词表 fail-closed 且不在本 app 写边界内，故扩展组
 * 走同源直连 fetch + 本文件内容错投影（逐行容错，坏行丢弃不炸整组）；后端对
 * 未就绪契约表（T1/T2）优雅降级为空，前端只如实展示披露位。
 */

export type SuggestionEdit = { removed: boolean; value: string };

export const suggestionMetricLabel = (metric: ReportFactSuggestionMetric): string => {
  switch (metric) {
    case 'brand_appearance_rate':
      return '品牌提及率';
    case 'rank_distribution':
      return '推荐排名分布（平均排名）';
    case 'top1_appearance_rate':
      return 'Top1 出现率';
    case 'top3_appearance_rate':
      return 'Top3 出现率';
    case 'top5_appearance_rate':
      return 'Top5 出现率';
    case 'competitor_appearance_rate':
      return '竞品出现率';
  }
};

/** 报告窗口（YYYY-MM-DD 起止）→ 建议端点的 window_days（1..366，含首尾日）。 */
export function computeSuggestionWindowDays(
  windowStart: string,
  windowEnd: string,
): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(windowStart) || !/^\d{4}-\d{2}-\d{2}$/.test(windowEnd)) {
    return null;
  }
  const start = Date.parse(`${windowStart}T00:00:00.000Z`);
  const end = Date.parse(`${windowEnd}T00:00:00.000Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  const days = Math.floor((end - start) / 86_400_000) + 1;
  return Math.min(Math.max(days, 1), 366);
}

/** 草稿行 + 人工编辑 → 报告创建请求的 fact_rows 条目（人工确认留痕）。 */
export function buildSuggestionFactPayload(
  row: ReportFactSuggestionRow,
  edit: SuggestionEdit,
): { payload: Record<string, unknown>; invalid: boolean } {
  const trimmed = edit.value.trim();
  let value: number | null = row.value;
  let invalid = false;
  if (trimmed === '') {
    // 空值仅当原值为 null（零提及组的诚实空值）时合法
    if (row.value !== null) {
      invalid = true;
    } else {
      value = null;
    }
  } else {
    const parsed = Number(trimmed);
    if (!Number.isFinite(parsed) || Math.abs(parsed) > 1_000_000) {
      invalid = true;
    } else {
      value = parsed;
    }
  }
  const edited = !invalid && value !== row.value;
  return {
    invalid,
    payload: {
      metric: row.metric,
      value,
      unit: row.unit,
      numerator: row.numerator,
      denominator: row.denominator,
      dimensions: { ...row.dimensions },
      source: row.source,
      method: row.method,
      domain: row.domain,
      window: { ...row.window },
      ...(row.extra ? { extra: row.extra } : {}),
      human_confirmed: true,
      ...(edited ? { value_edited: true, original_value: row.value } : {}),
    },
  };
}

const insufficientReasonLabel = (reason: string): string => {
  switch (reason) {
    case 'no_answers':
      return '窗口内无有效回答';
    case 'no_extraction_coverage':
      return '窗口内回答尚无品牌抽取覆盖';
    case 'target_brand_unset':
      return '项目未配置目标品牌';
    default:
      return reason;
  }
};

// ── 扩展组（报价单服务 2/3/4）：类型 + 容错投影 + 同源直连拉取 ──────────────
// 形状与主草稿行一致（metric/value/unit/numerator/denominator/dimensions/source/
// method/domain/window/extra），metric 词表扩到 W3/W2/前后对比；extra 逐行容错
// （缺键按空对象，绝不 throw）， section 缺键/非对象 → null（该组不展示）。
export type ExtendedFactRow = {
  metric: string;
  value: number | null;
  unit: string;
  numerator: number;
  denominator: number;
  dimensions: { platform: string; region: string; query: string };
  source: string;
  method: string;
  domain: string;
  window: { start: string; end: string };
  extra: Record<string, unknown>;
};

export type ExtendedSection = {
  status: string;
  insufficientReasons: string[];
  factRows: ExtendedFactRow[];
  meta: Record<string, unknown>;
};

export type ExtendedFactSections = {
  w3: ExtendedSection | null;
  w2: ExtendedSection | null;
  beforeAfter: ExtendedSection | null;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const safeNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

const safeText = (value: unknown, maxLength: number): string =>
  typeof value === 'string' && value.length <= maxLength ? value : '';

const projectExtendedRow = (value: unknown): ExtendedFactRow | null => {
  if (!isRecord(value)) return null;
  const metric = safeText(value.metric, 80);
  const unit = safeText(value.unit, 20);
  const source = safeText(value.source, 40);
  const method = safeText(value.method, 60);
  const domain = safeText(value.domain, 40);
  if (!metric || !unit || !source || !method || !domain) return null;
  const rowValue = value.value === null ? null : safeNumber(value.value);
  if (value.value !== null && rowValue === null) return null;
  const numerator = safeNumber(value.numerator);
  const denominator = safeNumber(value.denominator);
  if (numerator === null || denominator === null) return null;
  const dimensions = isRecord(value.dimensions) ? value.dimensions : {};
  const windowValue = isRecord(value.window) ? value.window : {};
  return {
    metric,
    value: rowValue,
    unit,
    numerator,
    denominator,
    dimensions: {
      platform: safeText(dimensions.platform, 40),
      region: safeText(dimensions.region, 40),
      query: safeText(dimensions.query, 500),
    },
    source,
    method,
    domain,
    window: {
      start: safeText(windowValue.start, 40),
      end: safeText(windowValue.end, 40),
    },
    extra: isRecord(value.extra) ? value.extra : {},
  };
};

const projectExtendedSection = (value: unknown): ExtendedSection | null => {
  if (!isRecord(value)) return null;
  const rows = Array.isArray(value.fact_rows) ? value.fact_rows : [];
  const factRows: ExtendedFactRow[] = [];
  for (const entry of rows) {
    const row = projectExtendedRow(entry);
    if (row) factRows.push(row);
  }
  const reasons = Array.isArray(value.insufficient_reasons)
    ? value.insufficient_reasons.filter(
        (reason): reason is string => typeof reason === 'string',
      )
    : [];
  const { status, insufficient_reasons, fact_rows, ...meta } = value;
  void insufficient_reasons;
  void fact_rows;
  return {
    status: typeof status === 'string' ? status : 'ok',
    insufficientReasons: reasons,
    factRows,
    meta,
  };
};

/** 响应体 → 三个扩展组（键缺失/形状不符 → 该组 null，容错不炸）。 */
export function projectExtendedFactSections(value: unknown): ExtendedFactSections {
  const body = isRecord(value) ? value : {};
  return {
    w3: projectExtendedSection(body.w3_disparagement),
    w2: projectExtendedSection(body.w2_site_audit),
    beforeAfter: projectExtendedSection(body.before_after),
  };
}

const configuredApiBase =
  (import.meta as ImportMeta & { env?: { VITE_GEO_API_BASE?: string } }).env
    ?.VITE_GEO_API_BASE ?? '';

export type CompareWindows = {
  beforeStart: string;
  beforeEnd: string;
  afterStart: string;
  afterEnd: string;
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** 四个对比窗日期齐全且各自不倒置 → 可请求 before_after 组；否则 null（不发）。 */
export function validCompareWindows(compare: CompareWindows): CompareWindows | null {
  const values = [
    compare.beforeStart,
    compare.beforeEnd,
    compare.afterStart,
    compare.afterEnd,
  ];
  if (values.some((value) => !ISO_DATE.test(value))) return null;
  if (compare.beforeStart > compare.beforeEnd || compare.afterStart > compare.afterEnd) {
    return null;
  }
  return compare;
}

/**
 * 扩展组同源直连拉取：与 getReportFactSuggestions 同一端点，附加 before/after
 * 查询参数。失败/非 200/形状不符 → null（扩展组整块不展示，绝不影响主草稿）。
 * 身份头不变量与 @geo/api-client 的 secureGeoApiFetch 同款：生产包（native_session
 * cookie 鉴权）不发送浏览器身份三头，仅 fixture/e2e 构建保留（契约夹具流），
 * 模式照 operations-web features/services/api.ts 的豁免先例。
 */
export async function fetchExtendedFactSections(
  projectPubId: string,
  windowDays: number,
  headers: Record<string, string | null>,
  compare: CompareWindows | null,
): Promise<ExtendedFactSections | null> {
  const params = new URLSearchParams({ window_days: String(windowDays) });
  const validCompare = compare ? validCompareWindows(compare) : null;
  if (validCompare) {
    params.set('before_start', validCompare.beforeStart);
    params.set('before_end', validCompare.beforeEnd);
    params.set('after_start', validCompare.afterStart);
    params.set('after_end', validCompare.afterEnd);
  }
  const env = (import.meta as ImportMeta & { env?: BrowserBuildIdentityEnv }).env;
  const requestHeaders: Record<string, string> = { Accept: 'application/json' };
  if (allowsFixtureIdentityHeaders(env)) {
    for (const [key, value] of Object.entries(headers)) {
      if (value !== null) requestHeaders[key] = value;
    }
  }
  try {
    const response = await fetch(
      `${configuredApiBase}/api/v2/projects/${encodeURIComponent(projectPubId)}` +
        `/report-fact-suggestions?${params.toString()}`,
      {
        headers: requestHeaders,
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
        referrerPolicy: 'no-referrer',
      },
    );
    if (!response.ok) return null;
    return projectExtendedFactSections(await response.json());
  } catch {
    return null;
  }
}

const DIRECTION_LABELS: Record<string, string> = {
  smear_on_own: '第三方/竞品抹黑己方',
  own_smear_on_competitor: '己方拉踩竞品',
};
const FACT_CHECK_VERDICT_LABELS: Record<string, string> = {
  supported: '属实',
  refuted: '不实',
  unverifiable: '无法核实',
};
const SEVERITY_LABELS: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
};
const CATEGORY_LABELS: Record<string, string> = {
  content_coverage: '内容覆盖',
  citability: '可引用性',
  fact_consistency: '事实一致性',
  crawlability: '可抓取性',
  other: '其他',
};
const BEFORE_AFTER_METRIC_LABELS: Record<string, string> = {
  mention_rate: '品牌提及率',
  avg_rank: '平均排名',
  top1: 'Top1 出现率',
  top3: 'Top3 出现率',
  top5: 'Top5 出现率',
};

const extendedReasonLabel = (reason: string): string => {
  switch (reason) {
    case 'no_judgments':
      return '窗口内无内容风险判定';
    case 'no_source_documents':
      return '窗口内无引用文档';
    case 'before_no_answers':
      return '优化前窗口无有效回答';
    case 'after_no_answers':
      return '优化后窗口无有效回答';
    case 'before_no_extraction_coverage':
      return '优化前窗口无品牌抽取覆盖';
    case 'after_no_extraction_coverage':
      return '优化后窗口无品牌抽取覆盖';
    case 'target_brand_unset':
      return '项目未配置目标品牌';
    case 'tenant_context_missing':
      return '数据面暂不可用';
    default:
      return reason;
  }
};

/** 扩展组行的中文标签（案例/建议等非数值行亦有短标签）。 */
export function extendedMetricLabel(row: ExtendedFactRow): string {
  const extra = row.extra;
  switch (row.metric) {
    case 'disparagement_rate': {
      const direction =
        typeof extra.direction === 'string'
          ? (DIRECTION_LABELS[extra.direction] ?? '方向未归类')
          : '方向未归类';
      return `拉踩判定占比 · ${direction}`;
    }
    case 'disparagement_case':
      return '风险案例';
    case 'own_site_citation_share':
      return '官网引用率';
    case 'own_site_adoption_rate':
      return '官网内容采纳率';
    case 'site_audit_suggestion':
      return '官网优化建议';
    case 'before_after_metric': {
      const name = typeof extra.metric_name === 'string' ? extra.metric_name : '';
      return BEFORE_AFTER_METRIC_LABELS[name] ?? name;
    }
    default:
      return row.metric;
  }
}

const factCheckBadge = (factCheck: unknown): { label: string; tone: 'positive' | 'danger' | 'neutral' } | null => {
  if (!isRecord(factCheck)) return null;
  const verdict = typeof factCheck.verdict === 'string' ? factCheck.verdict : '';
  const label = FACT_CHECK_VERDICT_LABELS[verdict];
  if (!label) return null;
  const tone =
    verdict === 'supported' ? 'positive' : verdict === 'refuted' ? 'danger' : 'neutral';
  return { label: `事实核查：${label}`, tone };
};

export type ExtendedSectionKey = 'w3' | 'w2' | 'beforeAfter';

/** 三个扩展组的行扁平化（编辑态数组与渲染共用同一顺序：w3 → w2 → beforeAfter）。 */
export function flattenExtendedRows(
  sections: ExtendedFactSections | null,
): { section: ExtendedSectionKey; row: ExtendedFactRow }[] {
  if (!sections) return [];
  const flat: { section: ExtendedSectionKey; row: ExtendedFactRow }[] = [];
  for (const key of ['w3', 'w2', 'beforeAfter'] as const) {
    const section = sections[key];
    if (!section) continue;
    for (const row of section.factRows) flat.push({ section: key, row });
  }
  return flat;
}

/** 扩展组草稿行 + 人工编辑 → fact_rows 条目（与主草稿同一留痕纪律）。 */
export function buildExtendedFactPayload(
  row: ExtendedFactRow,
  edit: SuggestionEdit,
): { payload: Record<string, unknown>; invalid: boolean } {
  // 非数值行（案例/建议）：值固定 null，无人工改值入口
  if (row.value === null) {
    return {
      invalid: false,
      payload: {
        metric: row.metric,
        value: null,
        unit: row.unit,
        numerator: row.numerator,
        denominator: row.denominator,
        dimensions: { ...row.dimensions },
        source: row.source,
        method: row.method,
        domain: row.domain,
        window: { ...row.window },
        extra: row.extra,
        human_confirmed: true,
      },
    };
  }
  const trimmed = edit.value.trim();
  let value: number | null = row.value;
  let invalid = false;
  if (trimmed === '') {
    invalid = true;                     // 数值行清空=无法解析（案例行不适用，见上）
  } else {
    const parsed = Number(trimmed);
    if (!Number.isFinite(parsed) || Math.abs(parsed) > 1_000_000) {
      invalid = true;
    } else {
      value = parsed;
    }
  }
  const edited = !invalid && value !== row.value;
  return {
    invalid,
    payload: {
      metric: row.metric,
      value,
      unit: row.unit,
      numerator: row.numerator,
      denominator: row.denominator,
      dimensions: { ...row.dimensions },
      source: row.source,
      method: row.method,
      domain: row.domain,
      window: { ...row.window },
      extra: row.extra,
      human_confirmed: true,
      ...(edited ? { value_edited: true, original_value: row.value } : {}),
    },
  };
}

export function FactSuggestionsPanel({
  projectPubId,
  windowStart,
  windowEnd,
  disabled,
  onAcceptedChange,
}: {
  projectPubId: string;
  windowStart: string;
  windowEnd: string;
  disabled: boolean;
  onAcceptedChange: (payloads: Record<string, unknown>[], invalidCount: number) => void;
}) {
  const [status, setStatus] = useState<
    'idle' | 'loading' | 'ready' | 'domain_unset' | 'failed' | 'forbidden'
  >('idle');
  const [data, setData] = useState<ReportFactSuggestions | null>(null);
  const [edits, setEdits] = useState<SuggestionEdit[]>([]);
  const [extended, setExtended] = useState<ExtendedFactSections | null>(null);
  const [extendedEdits, setExtendedEdits] = useState<SuggestionEdit[]>([]);
  const [compare, setCompare] = useState<CompareWindows>({
    beforeStart: '',
    beforeEnd: '',
    afterStart: '',
    afterEnd: '',
  });
  const generation = useRef(0);

  const load = async () => {
    const headers = getValidatedIdentityHeaders();
    const windowDays = computeSuggestionWindowDays(windowStart, windowEnd);
    if (!headers || windowDays === null) {
      setStatus('failed');
      return;
    }
    const requestGeneration = ++generation.current;
    setStatus('loading');
    const result = await getReportFactSuggestions(projectPubId, windowDays, headers);
    if (requestGeneration !== generation.current) return;
    if (result.kind === 'ready') {
      setData(result.data);
      setEdits(
        result.data.factRows.map((row) => ({
          removed: false,
          value: row.value === null ? '' : String(row.value),
        })),
      );
      setStatus('ready');
      // 扩展组（W3/W2/前后对比）：独立直连拉取，失败只丢扩展组不影响主草稿
      const sections = await fetchExtendedFactSections(
        projectPubId,
        windowDays,
        headers,
        compare,
      );
      if (requestGeneration !== generation.current) return;
      setExtended(sections);
      const flat = flattenExtendedRows(sections);
      setExtendedEdits(
        flat.map(({ row }) => ({
          removed: false,
          value: row.value === null ? '' : String(row.value),
        })),
      );
      return;
    }
    setData(null);
    setEdits([]);
    setExtended(null);
    setExtendedEdits([]);
    setStatus(
      result.kind === 'domain_unset'
        ? 'domain_unset'
        : result.kind === 'forbidden'
          ? 'forbidden'
          : 'failed',
    );
  };

  const accepted = useMemo(() => {
    const payloads: Record<string, unknown>[] = [];
    let invalidCount = 0;
    if (data) {
      data.factRows.forEach((row, index) => {
        const edit = edits[index];
        if (!edit || edit.removed) return;
        const { payload, invalid } = buildSuggestionFactPayload(row, edit);
        if (invalid) {
          invalidCount += 1;
          return;
        }
        payloads.push(payload);
      });
    }
    flattenExtendedRows(extended).forEach(({ row }, index) => {
      const edit = extendedEdits[index];
      if (!edit || edit.removed) return;
      const { payload, invalid } = buildExtendedFactPayload(row, edit);
      if (invalid) {
        invalidCount += 1;
        return;
      }
      payloads.push(payload);
    });
    return { payloads, invalidCount };
  }, [data, edits, extended, extendedEdits]);

  useEffect(() => {
    onAcceptedChange(accepted.payloads, accepted.invalidCount);
  }, [accepted, onAcceptedChange]);

  const updateEdit = (index: number, next: Partial<SuggestionEdit>) => {
    setEdits((current) =>
      current.map((edit, position) => (position === index ? { ...edit, ...next } : edit)),
    );
  };

  const updateExtendedEdit = (index: number, next: Partial<SuggestionEdit>) => {
    setExtendedEdits((current) =>
      current.map((edit, position) => (position === index ? { ...edit, ...next } : edit)),
    );
  };

  const updateCompare = (key: keyof CompareWindows, value: string) => {
    setCompare((current) => ({ ...current, [key]: value }));
  };

  // 扩展组扁平索引（与 extendedEdits 对齐）：渲染时按 section 过滤但索引连续
  const extendedFlat = useMemo(() => flattenExtendedRows(extended), [extended]);
  const extendedIndexOffset = (sectionKey: 'w3' | 'w2' | 'beforeAfter') =>
    extendedFlat.findIndex((entry) => entry.section === sectionKey);

  const metaNumber = (meta: Record<string, unknown>, key: string): number | null =>
    safeNumber(meta[key]);
  const metaText = (meta: Record<string, unknown>, key: string): string =>
    safeText(meta[key], 200);
  const formatMaybe = (value: unknown): string => {
    const number = safeNumber(value);
    return number === null ? '—' : String(number);
  };
  const formatDiff = (value: number | null): string => {
    if (value === null) return '—';
    return value > 0 ? `+${value}` : String(value);
  };

  const renderExtendedRow = (flatIndex: number) => {
    const entry = extendedFlat[flatIndex];
    if (!entry) return null;
    const { row } = entry;
    const edit = extendedEdits[flatIndex] ?? { removed: true, value: '' };
    const extra = row.extra;
    const checkbox = (
      <label className="checkbox-line">
        <input
          type="checkbox"
          aria-label={`包含扩展组第 ${flatIndex + 1} 行事实`}
          checked={!edit.removed}
          onChange={(event) =>
            updateExtendedEdit(flatIndex, { removed: !event.target.checked })
          }
        />
        <span>{extendedMetricLabel(row)}</span>
      </label>
    );
    // 非数值行（案例/建议）无改值入口；数值行（比率/差值）保留人工改值留痕
    const valueInput =
      row.value === null ? null : (
        <label>
          {row.metric === 'before_after_metric' ? '差值' : '数值'}（
          {row.unit === 'percent' ? (row.metric === 'before_after_metric' ? '百分点' : '%') : '排名'}
          ）
          <input
            aria-label={`扩展组第 ${flatIndex + 1} 行事实数值`}
            value={edit.value}
            disabled={edit.removed}
            maxLength={40}
            onChange={(event) => updateExtendedEdit(flatIndex, { value: event.target.value })}
          />
        </label>
      );

    if (row.metric === 'disparagement_case') {
      const factCheck = isRecord(extra.fact_check) ? extra.fact_check : null;
      const badge = factCheckBadge(factCheck);
      const quote = safeText(extra.evidence_quote, 2000);
      const sourceUrl = safeText(extra.source_url, 2048);
      const factCheckUrl = factCheck ? safeText(factCheck.source_url, 2048) : '';
      const factCheckSummary = factCheck ? safeText(factCheck.summary, 2000) : '';
      return (
        <article>
          <div className="account-head">
            {checkbox}
            {badge ? <Badge tone={badge.tone}>{badge.label}</Badge> : null}
          </div>
          <p className="panel-subtitle">
            {safeText(extra.subject_brand, 200) || '未知主体'} →{' '}
            {safeText(extra.target_brand, 200) || '未知对象'} ·{' '}
            {typeof extra.direction === 'string' && extra.direction
              ? (DIRECTION_LABELS[extra.direction] ?? '方向未归类')
              : '方向未归类'}
            {safeNumber(extra.confidence) !== null
              ? ` · 置信度 ${safeNumber(extra.confidence)}`
              : ''}
            {row.dimensions.platform ? ` · ${row.dimensions.platform}` : ''}
          </p>
          {quote ? <blockquote className="fact-quote">“{quote}”</blockquote> : null}
          <p className="panel-subtitle">
            {factCheckSummary ? `核查结论：${factCheckSummary}` : ''}
            {factCheckUrl ? (
              <>
                {' '}
                <a href={factCheckUrl} target="_blank" rel="noreferrer">
                  核查来源
                </a>
              </>
            ) : null}
            {sourceUrl ? (
              <>
                {' '}
                <a href={sourceUrl} target="_blank" rel="noreferrer">
                  原文出处
                </a>
              </>
            ) : null}
          </p>
        </article>
      );
    }

    if (row.metric === 'site_audit_suggestion') {
      const severity = safeText(extra.severity, 20);
      const category = safeText(extra.category, 40);
      return (
        <article>
          <div className="account-head">
            {checkbox}
            <Badge
              tone={
                severity === 'high'
                  ? 'danger'
                  : severity === 'medium'
                    ? 'warning'
                    : 'neutral'
              }
            >
              严重程度：{SEVERITY_LABELS[severity] ?? severity ?? '未知'}
            </Badge>
            <Badge tone="info">{CATEGORY_LABELS[category] ?? category ?? '其他'}</Badge>
          </div>
          <p>{safeText(extra.title, 200) || '（无标题）'}</p>
          <p className="panel-subtitle">{safeText(extra.detail, 2000)}</p>
        </article>
      );
    }

    if (row.metric === 'before_after_metric') {
      const denominators = isRecord(extra.denominators) ? extra.denominators : {};
      return (
        <article>
          <div className="account-head">
            {checkbox}
            <Badge tone="info">{row.method}</Badge>
          </div>
          <p className="panel-subtitle">
            优化前 {formatMaybe(extra.before)}
            {row.unit === 'percent' ? '%' : ''} → 优化后 {formatMaybe(extra.after)}
            {row.unit === 'percent' ? '%' : ''} · 差值 {formatDiff(row.value)}
            {row.unit === 'percent' ? ' 百分点' : ''}
            {safeNumber(extra.before_of_mentions) !== null
              ? ` · 占提及 前 ${formatMaybe(extra.before_of_mentions)}% / 后 ${formatMaybe(
                  extra.after_of_mentions,
                )}%`
              : ''}
            {' · 分母 前 '}
            {formatMaybe(denominators.before_n)} / 后 {formatMaybe(denominators.after_n)}
          </p>
          {valueInput}
        </article>
      );
    }

    // 数值比率行（disparagement_rate / own_site_citation_share / own_site_adoption_rate）
    const note = safeText(extra.note, 120);
    const ownSiteHost = safeText(extra.own_site_host, 200);
    return (
      <article>
        <div className="account-head">
          {checkbox}
          <Badge tone="info">{row.method}</Badge>
          {row.value === null ? <Badge tone="warning">数据不足</Badge> : null}
        </div>
        <p className="panel-subtitle">
          {row.numerator}/{row.denominator}
          {ownSiteHost ? ` · 官网 ${ownSiteHost}` : ''}
          {note === 'adoption_metrics_unavailable'
            ? ' · 采纳率统计尚未上线，暂无法计算'
            : note === 'no_own_site_transcript_audits'
              ? ' · 窗内无官网文档转述判定'
              : ''}
        </p>
        {valueInput}
      </article>
    );
  };

  const renderExtendedSection = (
    sectionKey: ExtendedSectionKey,
    title: string,
    subtitle: string,
  ) => {
    const section = extended?.[sectionKey];
    if (!section) return null;
    const offset = extendedIndexOffset(sectionKey);
    return (
      <section className="fact-extended-section" aria-label={title}>
        <h3>{title}</h3>
        <p className="panel-subtitle">{subtitle}</p>
        {section.status === 'insufficient' || section.status === 'unavailable' ? (
          <div className="confirmation" role="status">
            <Badge tone="warning">数据不足</Badge>
            <span>
              {section.insufficientReasons.length > 0
                ? section.insufficientReasons.map(extendedReasonLabel).join('；')
                : '没有可计算的事实行'}
              ；未生成该组草稿，不会编造事实。
            </span>
          </div>
        ) : null}
        {section.factRows.length > 0 ? (
          <ul className="trace-list" aria-label={`${title}事实清单`}>
            {section.factRows.map((row, localIndex) => (
              <li key={`${sectionKey}:${row.metric}:${localIndex}`}>
                {renderExtendedRow(offset + localIndex)}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    );
  };

  const w3Meta = extended?.w3?.meta ?? {};
  const w2Meta = extended?.w2?.meta ?? {};
  const baMeta = extended?.beforeAfter?.meta ?? {};
  const w3Subtitle = [
    metaNumber(w3Meta, 'n_judgments') !== null
      ? `窗内判定 ${metaNumber(w3Meta, 'n_judgments')} 条`
      : '',
    metaNumber(w3Meta, 'n_disparagement') !== null
      ? `构成拉踩 ${metaNumber(w3Meta, 'n_disparagement')} 条`
      : '',
    metaNumber(w3Meta, 'n_undirected') !== null && metaNumber(w3Meta, 'n_undirected') !== 0
      ? `方向未归类 ${metaNumber(w3Meta, 'n_undirected')} 条`
      : '',
    w3Meta.judgments_truncated === true ? '判定超出单窗上限已截断' : '',
    w3Meta.fact_check_available === false ? '事实核查表未就绪，案例未附核查结论' : '',
  ]
    .filter(Boolean)
    .join(' · ');
  const w2Subtitle = [
    w2Meta.own_site_host ? `官网 ${metaText(w2Meta, 'own_site_host')}` : '官网未确认',
    metaNumber(w2Meta, 'documents_total') !== null
      ? `窗内引用文档 ${metaNumber(w2Meta, 'documents_total')} 条`
      : '',
    w2Meta.suggestions_available === false ? '优化建议表未就绪，暂无建议条目' : '',
    w2Meta.suggestions_truncated === true ? '建议超出上限已截断' : '',
  ]
    .filter(Boolean)
    .join(' · ');
  const baWindows = isRecord(baMeta.windows) ? baMeta.windows : {};
  const baSubtitle = [
    baWindows.before_start && baWindows.before_end
      ? `优化前 ${safeText(baWindows.before_start, 10)} – ${safeText(baWindows.before_end, 10)}`
      : '',
    baWindows.after_start && baWindows.after_end
      ? `优化后 ${safeText(baWindows.after_start, 10)} – ${safeText(baWindows.after_end, 10)}`
      : '',
    '差值=优化后−优化前',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div className="fact-suggestions">
      <div className="form-actions">
        <span>
          事实行可由分析链路按报价单四指标自动生成（brandrank 口径，只读已冻结抽取结果）；
          逐行人工确认后随报告一并冻结。
        </span>
        <button
          className="button button-secondary"
          type="button"
          disabled={disabled || status === 'loading'}
          onClick={() => void load()}
        >
          {status === 'loading'
            ? '正在从分析链路计算…'
            : data
              ? '重新拉取事实建议'
              : '从分析链路预填事实'}
        </button>
      </div>
      {status === 'domain_unset' ? (
        <span className="field-hint" role="status">
          项目尚未配置品牌分析域（brandrank domain）；请先在项目设置中选择分析域后再预填事实。
        </span>
      ) : null}
      {status === 'failed' ? <StatePanel state="failed" onRetry={() => void load()} /> : null}
      {status === 'forbidden' ? <StatePanel state="forbidden" /> : null}
      <fieldset className="fact-compare-windows">
        <legend>优化前后对比窗口（可选，报价单服务 4）</legend>
        <label>
          优化前起
          <input
            type="date"
            aria-label="优化前开始日期"
            value={compare.beforeStart}
            onChange={(event) => updateCompare('beforeStart', event.target.value)}
          />
        </label>
        <label>
          优化前止
          <input
            type="date"
            aria-label="优化前结束日期"
            value={compare.beforeEnd}
            onChange={(event) => updateCompare('beforeEnd', event.target.value)}
          />
        </label>
        <label>
          优化后起
          <input
            type="date"
            aria-label="优化后开始日期"
            value={compare.afterStart}
            onChange={(event) => updateCompare('afterStart', event.target.value)}
          />
        </label>
        <label>
          优化后止
          <input
            type="date"
            aria-label="优化后结束日期"
            value={compare.afterEnd}
            onChange={(event) => updateCompare('afterEnd', event.target.value)}
          />
        </label>
        <span className="field-hint">
          四个日期齐全后重新拉取即生成前后对比事实行；不齐或倒置则不生成该组，不报错。
        </span>
      </fieldset>
      {status === 'ready' && data ? (
        data.insufficient || data.factRows.length === 0 ? (
          <div className="confirmation" role="status">
            <Badge tone="warning">数据不足</Badge>
            <span>
              {data.insufficientReasons.length > 0
                ? data.insufficientReasons.map(insufficientReasonLabel).join('；')
                : '窗口内没有可计算的事实行'}
              ；未生成任何草稿，不会编造事实。
            </span>
          </div>
        ) : (
          <>
            <p className="panel-subtitle">
              实测窗口 {data.window.start.slice(0, 10)} – {data.window.end.slice(0, 10)} ·{' '}
              {data.coverage.nAnswers} 条有效回答 · {data.coverage.nWithExtract} 条已抽取 ·{' '}
              {data.coverage.nGroups} 组（平台×地域×问题） · 目标品牌 {data.targetBrand ?? '—'}
              {data.truncated ? ' · 窗口样本超出单窗上限已截断' : ''}
            </p>
            <ul className="trace-list" aria-label="事实建议清单">
              {data.factRows.map((row, index) => {
                const edit = edits[index] ?? { removed: true, value: '' };
                const rowLabel = `${suggestionMetricLabel(row.metric)}${
                  row.extra?.competitor ? ` · ${row.extra.competitor}` : ''
                }`;
                return (
                  <li key={`${row.metric}:${index}`}>
                    <article>
                      <div className="account-head">
                        <label className="checkbox-line">
                          <input
                            type="checkbox"
                            aria-label={`包含第 ${index + 1} 行事实`}
                            checked={!edit.removed}
                            onChange={(event) =>
                              updateEdit(index, { removed: !event.target.checked })
                            }
                          />
                          <span>{rowLabel}</span>
                        </label>
                        <Badge tone="info">{row.method}</Badge>
                      </div>
                      <p className="panel-subtitle">
                        {row.dimensions.platform || '平台未知'} ·{' '}
                        {row.dimensions.region || '地域未知'} · {row.dimensions.query} ·{' '}
                        {row.numerator}/{row.denominator}
                        {row.extra?.of_mentions !== undefined
                          ? ` · 占提及 ${row.extra.of_mentions}%`
                          : ''}
                        {row.extra?.best_rank !== undefined && row.extra.best_rank !== null
                          ? ` · 最佳排名 ${row.extra.best_rank}`
                          : ''}
                      </p>
                      <label>
                        数值（{row.unit === 'percent' ? '%' : '平均排名'}）
                        <input
                          aria-label={`第 ${index + 1} 行事实数值`}
                          value={edit.value}
                          disabled={edit.removed}
                          maxLength={40}
                          onChange={(event) =>
                            updateEdit(index, { value: event.target.value })
                          }
                        />
                      </label>
                    </article>
                  </li>
                );
              })}
            </ul>
          </>
        )
      ) : null}
      {status === 'ready' && data && accepted.invalidCount > 0 ? (
        <span className="field-hint" role="alert">
          有 {accepted.invalidCount} 行数值无法解析为有限数字；修正或移除后才能创建报告。
        </span>
      ) : null}
      {status === 'ready' && data
        ? renderExtendedSection('w3', '内容风险核查（拉踩/抹黑）', w3Subtitle)
        : null}
      {status === 'ready' && data
        ? renderExtendedSection('w2', '官网引用能效', w2Subtitle)
        : null}
      {status === 'ready' && data
        ? renderExtendedSection('beforeAfter', '优化前后对比', baSubtitle)
        : null}
    </div>
  );
}
