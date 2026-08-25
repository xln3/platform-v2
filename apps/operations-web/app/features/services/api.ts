import { allowsFixtureIdentityHeaders, type BrowserBuildIdentityEnv } from '@geo/api-client';
import type { CursorPage } from '../../pagination';
import {
  executionApi,
  type CurrentConfig,
  type FrozenConfig,
  type Project,
  type SessionContext,
} from '../execution/api';
import {
  SAMPLING_PROGRESS_DEFAULT_PAGE_SIZE,
  SERVICE2_CORPUS_DEFAULT_PAGE_SIZE,
} from './pagination-policy';

const CONFIGURED_API_BASE =
  (
    import.meta as ImportMeta & { env?: { VITE_GEO_API_BASE?: string } }
  ).env?.VITE_GEO_API_BASE?.trim().replace(/\/+$/u, '') ?? '';

function servicesUrl(path: string): URL {
  if (CONFIGURED_API_BASE) return new URL(`${CONFIGURED_API_BASE}${path}`);
  if (typeof window === 'undefined') throw new Error('browser_origin_unavailable');
  return new URL(path, window.location.origin);
}

/**
 * 生产包不发送浏览器身份三头（与 @geo/api-client 的 secureGeoApiFetch 同一不变量）：
 * native_session 由 cookie 鉴权；fixture/e2e 构建保留身份头供契约夹具流使用。
 */
function fixtureIdentityHeaders(session: SessionContext): Record<string, string> {
  const env = (import.meta as ImportMeta & { env?: BrowserBuildIdentityEnv }).env;
  if (!allowsFixtureIdentityHeaders(env)) return {};
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(session.headers)) {
    if (typeof value === 'string') headers[key] = value;
  }
  return headers;
}

/** 带错误码与 details 的服务端错误（details 形如 {unknown_run_pub_ids: [...]}）。 */
export class ServicesApiError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown>;
  constructor(code: string, details?: Record<string, unknown>) {
    super(code);
    this.code = code;
    this.details = details ?? {};
  }
}

async function readApiError(response: Response): Promise<Error> {
  let code = `http_${response.status}`;
  let details: Record<string, unknown> | undefined;
  try {
    const payload = (await response.json()) as
      | {
          error?: { code?: string; details?: Record<string, unknown> };
          detail?: { code?: string };
        }
      | undefined;
    code = payload?.error?.code ?? payload?.detail?.code ?? code;
    details = payload?.error?.details;
  } catch {
    // 非 JSON 错误体：保留 http_<status> 口径。
  }
  return new ServicesApiError(code, details);
}

async function servicesGet<T>(
  session: SessionContext,
  path: string,
  query: Record<string, string | number>,
): Promise<T> {
  const url = servicesUrl(path);
  for (const [key, value] of Object.entries(query)) url.searchParams.set(key, String(value));
  const response = await fetch(url, {
    headers: { Accept: 'application/json', ...fixtureIdentityHeaders(session) },
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  });
  if (!response.ok) throw await readApiError(response);
  return (await response.json()) as T;
}

async function servicesPost<T>(
  session: SessionContext,
  path: string,
  body: Record<string, unknown>,
): Promise<T> {
  const response = await fetch(servicesUrl(path), {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...fixtureIdentityHeaders(session),
    },
    body: JSON.stringify(body),
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  });
  if (!response.ok) throw await readApiError(response);
  return (await response.json()) as T;
}

async function servicesPostIdempotent<T>(
  session: SessionContext,
  path: string,
  body: Record<string, unknown>,
  idempotencyKey: string,
): Promise<T> {
  const response = await fetch(servicesUrl(path), {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
      ...fixtureIdentityHeaders(session),
    },
    body: JSON.stringify(body),
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  });
  if (!response.ok) throw await readApiError(response);
  return (await response.json()) as T;
}

async function servicesPostIdempotentVersioned<T>(
  session: SessionContext,
  path: string,
  body: Record<string, unknown>,
  idempotencyKey: string,
  version: number,
): Promise<T> {
  const response = await fetch(servicesUrl(path), {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
      'If-Match': `"${version}"`,
      ...fixtureIdentityHeaders(session),
    },
    body: JSON.stringify(body),
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  });
  if (!response.ok) throw await readApiError(response);
  return (await response.json()) as T;
}

export function defaultWindow(days = 30): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end.getTime() - (days - 1) * 86_400_000);
  return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) };
}

// ── 抹黑拉踩（W3）──
export type DisparagementDimension = 'target_brand' | 'subject_brand' | 'platform';

export type DisparagementRateRow = {
  dimension: DisparagementDimension;
  value: string;
  judgments: number;
  disparagement_count: number;
  disparagement_rate: number | null;
  negative_count: number;
  support_count: number;
  experimental_count: number;
  metric_version: string;
};

export type DisparagementFactCheck = {
  verdict: string;
  summary: string | null;
  source_url: string | null;
  checked_at: string;
};

export type DisparagementCase = {
  judgment_pub_id: string;
  subject_type: string;
  subject_pub_id: string;
  platform: string;
  subject_brand: string;
  target_brand: string;
  attitude: string;
  evidence_quote: string | null;
  confidence: number | null;
  method: string;
  model: string;
  prompt_version: string;
  source_url: string | null;
  created_at: string;
  content_origin: string;
  fact_check: DisparagementFactCheck | null;
};

// ── 品牌可见度榜单（brandrank，按需计算）──
export type BrandVisibilityRow = {
  rank: number;
  brand: string;
  score: number;
  avg_rank: number;
  occurrences: number;
  appearance_rate?: number;
};

export type BrandVisibility = {
  project_pub_id: string;
  project_name?: string;
  window_days: number;
  domain?: string;
  target_brand?: string | null;
  competitors?: string[];
  result?: {
    overall?: { merged?: BrandVisibilityRow[] };
    insufficient?: boolean;
  };
};

export type BrandVisibilityResult =
  | { kind: 'ready'; data: BrandVisibility }
  | { kind: 'unmapped_industry' }
  | { kind: 'brandrank_domain_unresolved' }
  | { kind: 'llm_disabled' }
  | { kind: 'unavailable' };

// ── 采样进度（最新完整题库 + 同批拆腿/补采配置）──
export type SamplingProgressColumn = {
  key: string;
  model: string;
  region: string;
  /** Formal planned mode retained for compatibility. */
  mode: string;
  /** Effective modes accepted into this formal model×region sampling leg. */
  modes?: string[];
};

export type SamplingProgressModeBreakdown = {
  mode: string;
  completed_samples: number;
  latest_capture_time: string;
  answer_pub_ids: string[];
};

export type SamplingProgressCell = {
  column_key: string;
  completed_samples: number;
  latest_capture_time: string;
  answer_pub_ids: string[];
  mode_breakdown?: SamplingProgressModeBreakdown[];
};

export type SamplingProgressRow = {
  appendix: string | null;
  group: string;
  group_name: string;
  expression: string;
  query_text: string;
  cells: SamplingProgressCell[];
};

export type SamplingProgress = {
  project_pub_id: string;
  config_revision_start: number | null;
  config_revision_end: number | null;
  columns: SamplingProgressColumn[];
  rows: SamplingProgressRow[];
  page: {
    page: number;
    page_size: number;
    total_count: number;
    total_pages: number;
  };
  observed_cells: number;
  total_cells: number;
  answer_count: number;
  latest_capture_time: string | null;
  live_runs: number;
};

// ── 官网信源审计（source-audit，端点并行开发中，按冻结契约对接）──
export type SourceAuditVerdicts = {
  accurate: number;
  inaccurate: number;
  unsupported: number;
  unverifiable: number;
};

export type SourceAuditHost = {
  host: string;
  is_own_site: boolean;
  documents: number;
  transcript_total: number;
  transcript_accurate: number;
};

export type SourceCitationHost = {
  host: string;
  is_own_site: boolean;
  answers: number;
  references: number;
};

export type SourceAuditItem = {
  pub_id: string;
  url: string;
  host: string;
  final_url: string | null;
  http_status: number | null;
  extract_status: string;
  fetched_at: string | null;
  is_own_site: boolean;
  audits: {
    dimension: string;
    verdict: string;
    audit_status: string;
    rationale: string | null;
  }[];
};

export type SourceAuditReport = {
  project_pub_id: string;
  start: string;
  end: string;
  own_site_host: string | null;
  answers_total: number;
  answers_with_citation: number;
  citation_coverage_rate: number | null;
  answers_with_own_site_citation: number;
  own_site_answer_citation_rate: number | null;
  own_site_share_of_cited_answers: number | null;
  citation_references_total: number;
  own_site_citation_references: number;
  own_site_reference_share: number | null;
  own_site_cited_text_answers: number;
  own_site_cited_text_evidence_rate: number | null;
  documents_total: number;
  own_site_documents: number;
  own_site_share: number | null;
  own_site_transcript_total: number;
  own_site_transcript_accurate: number;
  own_site_transcript_accuracy_rate: number | null;
  own_site_adoption_evaluated_answers: number;
  own_site_adoption_verified_answers: number;
  own_site_adoption_rate: number | null;
  verdicts: { transcript: SourceAuditVerdicts; factual: SourceAuditVerdicts };
  answer_hosts: SourceCitationHost[];
  hosts: SourceAuditHost[];
  items: SourceAuditItem[];
};

// ── 官网内容问题与优化建议（T2 契约表最新批次；未上线时后端降级为空）──
export type SiteAuditSuggestion = {
  category: string;
  severity: string;
  title: string;
  detail: string;
  evidence_document_pub_id: string | null;
};

export type SiteAuditSuggestions = {
  batch_pub_id: string | null;
  generated_at: string | null;
  model: string | null;
  suggestions: SiteAuditSuggestion[];
};

// ── 试点前后对比（delta；config_version 防稀释，仅统计该冻结配置产出的答案）──
export type AnalyticsDeltaMetric = {
  current: number | null;
  previous: number | null;
  delta: number | null;
};

export type PilotDelta = Partial<
  Record<'mention_rate' | 'average_rank' | 'top3_rate' | 'citation_coverage', AnalyticsDeltaMetric>
>;

export type PilotDeltaResult =
  | { kind: 'ready'; data: PilotDelta }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

// ── 正式报告生产（五项独立服务，共享冻结事实与 Temporal 生产链）──
export type FormalReportService = 1 | 2 | 3 | 4 | 5;
export type FormalReportServiceCatalogVersion =
  | 'quotation_services_v2'
  | 'legacy_report_services_v1';
export type FormalReportServiceCode =
  | 'ranking_test'
  | 'outbound_disparagement_audit'
  | 'inbound_disparagement_audit'
  | 'official_site_audit'
  | 'content_publishing_pilot'
  | 'legacy_ranking_assessment'
  | 'legacy_content_ecosystem_risk'
  | 'legacy_official_site_efficiency'
  | 'legacy_pilot_comparison';
export type FormalReportDocumentStatus =
  | 'pre_formal'
  | 'formal'
  | 'internal_review'
  | 'delivery_candidate'
  | 'approved_signed';
export type FormalReportCreatableDocumentStatus = 'internal_review' | 'delivery_candidate';
export type FormalReportProductionStatus =
  | 'queued'
  | 'running'
  | 'failed'
  | 'awaiting_review'
  | 'signed';

export type FormalReportWindow = { start: string; end: string };

export type FormalReportArtifact = {
  format: string;
  sha256: string;
  byte_size: number;
  mime_type: string;
  download_url: string;
};

export type FormalReportOutput = {
  service_number: FormalReportService;
  service_code: FormalReportServiceCode;
  report_pub_id: string;
  report_version_pub_id: string;
  fact_snapshot_hash: string;
  artifacts: FormalReportArtifact[];
};

export type FormalReportProduction = {
  pub_id: string;
  project_pub_id: string;
  services: FormalReportService[];
  service_catalog_version: FormalReportServiceCatalogVersion;
  sop_project_pub_id: string | null;
  service2_manifest_pub_id: string | null;
  service2_manifest_hash: string | null;
  status: FormalReportProductionStatus;
  document_status: FormalReportDocumentStatus;
  window_start: string;
  window_end: string;
  before_window: FormalReportWindow | null;
  after_window: FormalReportWindow | null;
  candidate_group_strategy: 'evidence_completeness_v1' | 'preregistered_scope_v1';
  workflow_id: string;
  fact_snapshot_hash: string | null;
  outputs: FormalReportOutput[];
  error_code: string | null;
  created_at: string;
  updated_at: string;
};

export type FormalReportProductionCreate = {
  projectPubId: string;
  services: FormalReportService[];
  serviceCatalogVersion: 'quotation_services_v2';
  sopProjectPubId?: string;
  service2ManifestPubId?: string;
  service2ManifestHash?: string;
  window: FormalReportWindow;
  documentStatus: FormalReportCreatableDocumentStatus;
  version: string;
  preparedBy: string;
  preparedDate: string;
  reviewedBy?: string;
  reviewedDate?: string;
  beforeWindow?: FormalReportWindow;
  afterWindow?: FormalReportWindow;
  idempotencyKey: string;
};

export type FormalReportReviewDecision = 'approved' | 'changes_requested';

// ── 服务 2：冻结范围内全部 U occurrence 的语料与关系审核 ──
export type Service2Coverage = {
  selected_queries: number;
  successful_queries: number;
  failed_queries: number;
  successful_queries_with_u: number;
  successful_queries_without_u: number;
  query_failure_codes: Record<string, number>;
  query_outcomes_complete: boolean;
  query_coverage_complete: boolean;
  expected_occurrences: number;
  materialized_items: number;
  distinct_urls: number;
  processing_states: Record<string, number>;
  fetch_states: Record<string, number>;
  entered_judgment: number;
  findings: number;
  reviewed_findings: number;
  eligible_cases: number;
  coverage_complete: boolean;
};

export type Service2Batch = {
  schema_version: 'formal-service2-source-corpus-v2';
  batch_pub_id: string;
  project_pub_id: string;
  service_entitlement_pub_id: string;
  service_entitlement_revision: string;
  run_pub_ids: string[];
  analysis_model: string;
  window_start: string;
  window_end: string;
  source_snapshot_boundary: string;
  corpus_policy_version: string;
  judgment_policy_version: string;
  status:
    | 'draft'
    | 'queued'
    | 'running'
    | 'paused'
    | 'cancel_requested'
    | 'cancelled'
    | 'review'
    | 'frozen'
    | 'failed';
  version: number;
  workflow_id: string | null;
  frozen_at: string | null;
  manifest_hash: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  coverage: Service2Coverage;
};

export type Service2AnalysisModel = {
  model: string;
  label: string;
  provider: string;
  tier: string;
  capability: string;
  web_search_mode: string;
  input_usd_per_million_tokens: number | null;
  output_usd_per_million_tokens: number | null;
  context_window_tokens: number | null;
  web_search_audit_status: 'verified_provider_citation';
  web_search_audited_at: string;
  auditable_source_mode: 'provider_citation' | 'provider_tool';
  recommended: boolean;
  catalog_revision: string;
  pricing_observed_at: string;
  pricing_source_url: string;
  pricing_currency: 'USD';
  token_price_unit: 'per_million_tokens';
  web_search_usd_per_call: number | null;
  web_search_pricing_status: 'not_published_in_catalog_snapshot';
  pricing_notice: 'catalog_snapshot_provider_invoice_authoritative';
  web_search_audit_policy: 'provider_search_event_and_provider_citation_required';
};

export type Service2AnalysisModelCatalog = {
  default_model: string;
  models: Service2AnalysisModel[];
  credential_source: 'server_environment_only';
};

export type Service2CorpusItem = {
  item_pub_id: string;
  occurrence_pub_id: string;
  run_pub_id: string;
  answer_pub_id: string;
  source_url_pub_id: string;
  snapshot_pub_id: string | null;
  source_document_pub_id: string | null;
  fetch_attempt_pub_id: string | null;
  raw_url: string;
  canonical_url: string;
  site_host: string;
  occurrence_ordinal: number;
  u_rank: number | null;
  captured_at: string;
  platform: string;
  model: string;
  region: string;
  collection_surface: string | null;
  question: string;
  retrieval_query: string | null;
  u_state: string;
  fetch_state: string;
  processing_state: string;
  entity_state: string;
  judgment_state: string;
  review_state: string;
  entered_judgment: boolean;
  finding_count: number;
  retry_count: number;
  failure_code: string | null;
  manual_evidence_state: string;
  version: number;
};

export type Service2CorpusPage = {
  batch_pub_id: string;
  data: Service2CorpusItem[];
  filtered_count: number;
  all_u_total: number;
  next_cursor: string | null;
  has_more: boolean;
};

export type Service2Attribution = {
  party: string | null;
  confidence: 'verified' | 'probable' | 'weak' | 'unknown';
  evidence: Array<Record<string, unknown>>;
};

export type Service2Finding = {
  finding_pub_id: string;
  batch_pub_id: string;
  corpus_item_pub_id: string;
  occurrence_pub_id: string;
  snapshot_pub_id: string;
  canonical_url: string;
  ledger: 'statement' | 'exposure';
  level: 'L0' | 'L1' | 'L2a' | 'L2b' | 'L3a' | 'L3b' | 'L4';
  relation_direction: string;
  textual_speaker: string;
  target_entity: string;
  beneficiary_entity: string | null;
  is_disparagement: boolean;
  fact_anchor_state: string;
  evidence_quote: string;
  quote_start: number;
  quote_end: number;
  context_text: string;
  context_start: number;
  context_end: number;
  snapshot_text_sha256: string;
  visual_anchor_pub_id: string | null;
  visual_evidence_pub_id: string | null;
  visual_bbox: [number, number, number, number] | null;
  visual_page_number: number | null;
  visual_validation_status: string;
  flags: Record<string, boolean>;
  comparison_dimensions: string[];
  omitted_facts: string[];
  method: string;
  policy_version: string;
  confidence: number;
  validation_status: string;
  validation_failures: string[];
  publisher: Service2Attribution;
  commissioner: Service2Attribution;
  factcheck_claim: string | null;
  factcheck_verdict: string | null;
  factcheck_evidence: Array<Record<string, unknown>>;
  factcheck_boundary: string | null;
  current_review_state: string;
  version: number;
  created_at: string;
};

export type Service2FindingPage = {
  batch_pub_id: string;
  data: Service2Finding[];
  filtered_count: number;
  all_findings_total: number;
  next_cursor: string | null;
  has_more: boolean;
};

export type Service2Manifest = {
  schema_version: 'formal-service2-source-corpus-v2';
  batch_pub_id: string;
  manifest_pub_id: string;
  revision: number;
  manifest_hash: string;
  case_count: number;
  evidence_reference_count: number;
  facts: Record<string, unknown>;
  created_at: string;
};

export type Service2ManifestOption = {
  schema_version: 'formal-service2-source-corpus-v2';
  batch_pub_id: string;
  manifest_pub_id: string;
  revision: number;
  manifest_hash: string;
  case_count: number;
  evidence_reference_count: number;
  window_start: string;
  window_end: string;
  created_at: string;
};

// ── 前后对比（逐题；报价单服务④，brandrank 层口径，端点 /api/v2/analytics/comparisons）──
export type RunComparison = {
  pub_id: string;
  project_pub_id: string;
  name: string;
  baseline_run_pub_ids: string[];
  optimized_run_pub_ids: string[];
  note: string | null;
  created_by: string;
  created_at: string;
};

// 逐题快照单指标：mention_rate/topN 为 0–100 百分数（unit='percent'），avg_rank 原始名次；
// value 可能为 null（一律展示「—」，严禁渲染成 0）。
export type ComparisonMetricSnapshot = {
  value: number | null;
  unit: string;
  numerator?: number;
  denominator?: number;
  of_mentions?: boolean;
};

export type ComparisonQuestionSnapshot = {
  mention_rate: ComparisonMetricSnapshot;
  avg_rank: ComparisonMetricSnapshot;
  top1: ComparisonMetricSnapshot;
  top3: ComparisonMetricSnapshot;
  top5: ComparisonMetricSnapshot;
};

export type ComparisonMetricName = 'mention_rate' | 'avg_rank' | 'top1' | 'top3' | 'top5';

export type ComparisonQuestion = {
  query_text: string;
  status: 'ok' | 'insufficient';
  insufficient_reasons: string[];
  before: ComparisonQuestionSnapshot | null;
  after: ComparisonQuestionSnapshot | null;
  delta: Record<ComparisonMetricName, number | null>;
};

export type ComparisonAggregateRow = {
  metric: string;
  value: number | null;
  unit: string;
  extra: {
    metric_name: ComparisonMetricName;
    before: number | null;
    after: number | null;
    denominators: { before_n: number; after_n: number };
    before_of_mentions?: boolean;
    after_of_mentions?: boolean;
  };
};

export type RunComparisonDetail = RunComparison & {
  result: {
    status: 'ok' | 'insufficient';
    insufficient_reasons: string[];
    domain?: string;
    target_brand?: string | null;
    coverage: {
      before_answers: number;
      before_with_extract: number;
      after_answers: number;
      after_with_extract: number;
      before_truncated: boolean;
      after_truncated: boolean;
    };
    aggregate: { metrics: ComparisonAggregateRow[] };
    questions: ComparisonQuestion[];
    unpaired: { baseline_only: string[]; optimized_only: string[] };
  };
};

function asNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function projectVerdicts(value: unknown): SourceAuditVerdicts {
  const bucket = (value ?? {}) as Record<string, unknown>;
  return {
    accurate: asNumber(bucket.accurate),
    inaccurate: asNumber(bucket.inaccurate),
    unsupported: asNumber(bucket.unsupported),
    unverifiable: asNumber(bucket.unverifiable),
  };
}

function projectSourceAudit(raw: unknown): SourceAuditReport {
  const value = (raw ?? {}) as Record<string, unknown>;
  const verdicts = (value.verdicts ?? {}) as Record<string, unknown>;
  const answerHosts = Array.isArray(value.answer_hosts) ? value.answer_hosts : [];
  const hosts = Array.isArray(value.hosts) ? value.hosts : [];
  const items = Array.isArray(value.items) ? value.items : [];
  return {
    project_pub_id: asString(value.project_pub_id),
    start: asString(value.start),
    end: asString(value.end),
    own_site_host: typeof value.own_site_host === 'string' ? value.own_site_host : null,
    answers_total: asNumber(value.answers_total),
    answers_with_citation: asNumber(value.answers_with_citation),
    citation_coverage_rate:
      typeof value.citation_coverage_rate === 'number' &&
      Number.isFinite(value.citation_coverage_rate)
        ? value.citation_coverage_rate
        : null,
    answers_with_own_site_citation: asNumber(value.answers_with_own_site_citation),
    own_site_answer_citation_rate:
      typeof value.own_site_answer_citation_rate === 'number' &&
      Number.isFinite(value.own_site_answer_citation_rate)
        ? value.own_site_answer_citation_rate
        : null,
    own_site_share_of_cited_answers:
      typeof value.own_site_share_of_cited_answers === 'number' &&
      Number.isFinite(value.own_site_share_of_cited_answers)
        ? value.own_site_share_of_cited_answers
        : null,
    citation_references_total: asNumber(value.citation_references_total),
    own_site_citation_references: asNumber(value.own_site_citation_references),
    own_site_reference_share:
      typeof value.own_site_reference_share === 'number' &&
      Number.isFinite(value.own_site_reference_share)
        ? value.own_site_reference_share
        : null,
    own_site_cited_text_answers: asNumber(value.own_site_cited_text_answers),
    own_site_cited_text_evidence_rate:
      typeof value.own_site_cited_text_evidence_rate === 'number' &&
      Number.isFinite(value.own_site_cited_text_evidence_rate)
        ? value.own_site_cited_text_evidence_rate
        : null,
    documents_total: asNumber(value.documents_total),
    own_site_documents: asNumber(value.own_site_documents),
    own_site_share:
      typeof value.own_site_share === 'number' && Number.isFinite(value.own_site_share)
        ? value.own_site_share
        : null,
    own_site_transcript_total: asNumber(value.own_site_transcript_total),
    own_site_transcript_accurate: asNumber(value.own_site_transcript_accurate),
    own_site_transcript_accuracy_rate:
      typeof value.own_site_transcript_accuracy_rate === 'number' &&
      Number.isFinite(value.own_site_transcript_accuracy_rate)
        ? value.own_site_transcript_accuracy_rate
        : null,
    own_site_adoption_evaluated_answers: asNumber(value.own_site_adoption_evaluated_answers),
    own_site_adoption_verified_answers: asNumber(value.own_site_adoption_verified_answers),
    own_site_adoption_rate:
      typeof value.own_site_adoption_rate === 'number' &&
      Number.isFinite(value.own_site_adoption_rate)
        ? value.own_site_adoption_rate
        : null,
    verdicts: {
      transcript: projectVerdicts(verdicts.transcript),
      factual: projectVerdicts(verdicts.factual),
    },
    answer_hosts: answerHosts.map((host) => {
      const row = (host ?? {}) as Record<string, unknown>;
      return {
        host: asString(row.host),
        is_own_site: row.is_own_site === true,
        answers: asNumber(row.answers),
        references: asNumber(row.references),
      };
    }),
    hosts: hosts.map((host) => {
      const row = (host ?? {}) as Record<string, unknown>;
      return {
        host: asString(row.host),
        is_own_site: row.is_own_site === true,
        documents: asNumber(row.documents),
        transcript_total: asNumber(row.transcript_total),
        transcript_accurate: asNumber(row.transcript_accurate),
      };
    }),
    items: items.map((item) => {
      const row = (item ?? {}) as Record<string, unknown>;
      const audits = Array.isArray(row.audits) ? row.audits : [];
      return {
        pub_id: asString(row.pub_id),
        url: asString(row.url),
        host: asString(row.host),
        final_url: typeof row.final_url === 'string' ? row.final_url : null,
        http_status:
          typeof row.http_status === 'number' && Number.isFinite(row.http_status)
            ? row.http_status
            : null,
        extract_status: asString(row.extract_status),
        fetched_at: typeof row.fetched_at === 'string' ? row.fetched_at : null,
        is_own_site: row.is_own_site === true,
        audits: audits.map((audit) => {
          const entry = (audit ?? {}) as Record<string, unknown>;
          return {
            dimension: asString(entry.dimension),
            verdict: asString(entry.verdict),
            audit_status: asString(entry.audit_status),
            rationale: typeof entry.rationale === 'string' ? entry.rationale : null,
          };
        }),
      };
    }),
  };
}

export const servicesApi = {
  projects: (session: SessionContext) => executionApi.projects(session),
  configVersions: (session: SessionContext, projectPubId: string): Promise<FrozenConfig[]> =>
    executionApi.configVersions(session, projectPubId),
  currentConfig: (session: SessionContext, projectPubId: string): Promise<CurrentConfig> =>
    executionApi.currentConfig(session, projectPubId),
  samplingProgress: (
    session: SessionContext,
    projectPubId: string,
    page = 1,
    pageSize = SAMPLING_PROGRESS_DEFAULT_PAGE_SIZE,
  ): Promise<SamplingProgress> =>
    servicesGet(session, '/api/v2/analytics/sampling-progress', {
      project_pub_id: projectPubId,
      page,
      page_size: pageSize,
    }),
  disparagementRate: (
    session: SessionContext,
    input: { projectPubId: string; start: string; end: string; dimension: DisparagementDimension },
  ): Promise<DisparagementRateRow[]> =>
    servicesGet(session, '/api/v2/analytics/disparagement/rate', {
      project_pub_id: input.projectPubId,
      start: input.start,
      end: input.end,
      dimension: input.dimension,
    }),
  disparagementCases: (
    session: SessionContext,
    input: { projectPubId: string; start: string; end: string; limit?: number },
  ): Promise<DisparagementCase[]> =>
    servicesGet(session, '/api/v2/analytics/disparagement/cases', {
      project_pub_id: input.projectPubId,
      start: input.start,
      end: input.end,
      limit: input.limit ?? 50,
    }),
  brandVisibility: async (
    session: SessionContext,
    input: { projectPubId: string; windowDays?: number },
  ): Promise<BrandVisibilityResult> => {
    try {
      // 不带 industry：domain 由后端按项目真源 project.brandrank_domain 解析，
      // 未配置时返回 400 brandrank_domain_unresolved。
      const data = await servicesGet<BrandVisibility>(
        session,
        `/api/v2/projects/${encodeURIComponent(input.projectPubId)}/brand-visibility`,
        { window_days: input.windowDays ?? 30 },
      );
      return { kind: 'ready', data };
    } catch (error) {
      const code = error instanceof Error ? error.message : '';
      if (code === 'brandrank_domain_unresolved') return { kind: 'brandrank_domain_unresolved' };
      if (code === 'unmapped_industry' || code === 'unknown_domain' || code === 'http_400')
        return { kind: 'unmapped_industry' };
      if (code === 'llm_disabled' || code === 'http_503') return { kind: 'llm_disabled' };
      return { kind: 'unavailable' };
    }
  },
  sourceAudit: async (
    session: SessionContext,
    input: { projectPubId: string; start: string; end: string },
  ): Promise<SourceAuditReport> =>
    projectSourceAudit(
      await servicesGet(session, '/api/v2/analytics/source-audit', {
        project_pub_id: input.projectPubId,
        start: input.start,
        end: input.end,
      }),
    ),
  siteAuditSuggestions: async (
    session: SessionContext,
    input: { projectPubId: string },
  ): Promise<SiteAuditSuggestions> => {
    const raw = await servicesGet<unknown>(
      session,
      '/api/v2/analytics/source-audit/site-suggestions',
      { project: input.projectPubId },
    );
    const value = (raw ?? {}) as Record<string, unknown>;
    const suggestions = Array.isArray(value.suggestions) ? value.suggestions : [];
    return {
      batch_pub_id: typeof value.batch_pub_id === 'string' ? value.batch_pub_id : null,
      generated_at: typeof value.generated_at === 'string' ? value.generated_at : null,
      model: typeof value.model === 'string' ? value.model : null,
      suggestions: suggestions.map((entry) => {
        const row = (entry ?? {}) as Record<string, unknown>;
        return {
          category: asString(row.category),
          severity: asString(row.severity),
          title: asString(row.title),
          detail: asString(row.detail),
          evidence_document_pub_id:
            typeof row.evidence_document_pub_id === 'string' ? row.evidence_document_pub_id : null,
        };
      }),
    };
  },
  listComparisons: async (
    session: SessionContext,
    input: { projectPubId: string; cursor?: string; limit?: number },
  ): Promise<CursorPage<RunComparison>> => {
    const page = await servicesGet<{
      items: RunComparison[];
      next_cursor?: string | null;
      has_more?: boolean;
    }>(session, '/api/v2/analytics/comparisons', {
      project_pub_id: input.projectPubId,
      ...(input.cursor ? { cursor: input.cursor } : {}),
      limit: input.limit ?? 100,
    });
    return {
      data: Array.isArray(page.items) ? page.items : [],
      nextCursor: typeof page.next_cursor === 'string' ? page.next_cursor : null,
      hasMore: page.has_more === true,
    };
  },
  // 创建失败抛 ServicesApiError：unknown_run_pub_id 时 details.unknown_run_pub_ids 带具体 id。
  createComparison: async (
    session: SessionContext,
    input: {
      projectPubId: string;
      name: string;
      baselineRunPubIds: string[];
      optimizedRunPubIds: string[];
      note?: string;
    },
  ): Promise<RunComparison> =>
    servicesPost<RunComparison>(session, '/api/v2/analytics/comparisons', {
      project_pub_id: input.projectPubId,
      name: input.name,
      baseline_run_pub_ids: input.baselineRunPubIds,
      optimized_run_pub_ids: input.optimizedRunPubIds,
      ...(input.note ? { note: input.note } : {}),
    }),
  getComparison: async (
    session: SessionContext,
    comparisonPubId: string,
  ): Promise<RunComparisonDetail> =>
    servicesGet<RunComparisonDetail>(
      session,
      `/api/v2/analytics/comparisons/${encodeURIComponent(comparisonPubId)}`,
      {},
    ),
  analyticsDelta: async (
    session: SessionContext,
    input: { projectPubId: string; start: string; end: string; configVersion?: string | undefined },
  ): Promise<PilotDeltaResult> => {
    try {
      const raw = await servicesGet<unknown>(session, '/api/v2/analytics/delta', {
        project_pub_id: input.projectPubId,
        start: input.start,
        end: input.end,
        // 防稀释：只在界面上确有当前冻结配置时才带 config_version，否则全量口径。
        ...(input.configVersion ? { config_version: input.configVersion } : {}),
      });
      const value = (raw ?? {}) as Record<string, unknown>;
      const data: PilotDelta = {};
      for (const metric of [
        'mention_rate',
        'average_rank',
        'top3_rate',
        'citation_coverage',
      ] as const) {
        const candidate = value[metric];
        if (!candidate || typeof candidate !== 'object') continue;
        const entry = candidate as Record<string, unknown>;
        const numberOrNull = (input_: unknown): number | null =>
          typeof input_ === 'number' && Number.isFinite(input_) ? input_ : null;
        data[metric] = {
          current: numberOrNull(entry.current),
          previous: numberOrNull(entry.previous),
          delta: numberOrNull(entry.delta),
        };
      }
      return { kind: 'ready', data };
    } catch (error) {
      const code = error instanceof Error ? error.message : '';
      if (code === 'http_401' || code === 'http_403') return { kind: 'forbidden' };
      return { kind: 'unavailable' };
    }
  },
  formalReportProductions: async (
    session: SessionContext,
    input: { projectPubId: string; cursor?: string; limit?: number },
  ): Promise<CursorPage<FormalReportProduction>> => {
    const raw = await servicesGet<
      | FormalReportProduction[]
      | {
          items?: FormalReportProduction[];
          next_cursor?: string | null;
          has_more?: boolean;
        }
    >(session, '/api/v2/reports/formal-productions', {
      project_pub_id: input.projectPubId,
      ...(input.cursor ? { cursor: input.cursor } : {}),
      limit: input.limit ?? 50,
    });
    if (Array.isArray(raw)) return { data: raw, nextCursor: null, hasMore: false };
    return {
      data: Array.isArray(raw.items) ? raw.items : [],
      nextCursor: typeof raw.next_cursor === 'string' ? raw.next_cursor : null,
      hasMore: raw.has_more === true,
    };
  },
  formalReportProduction: (
    session: SessionContext,
    productionPubId: string,
  ): Promise<FormalReportProduction> =>
    servicesGet(
      session,
      `/api/v2/reports/formal-productions/${encodeURIComponent(productionPubId)}`,
      {},
    ),
  createFormalReportProduction: (
    session: SessionContext,
    input: FormalReportProductionCreate,
  ): Promise<FormalReportProduction> =>
    servicesPostIdempotent(
      session,
      '/api/v2/reports/formal-productions',
      {
        project_pub_id: input.projectPubId,
        services: input.services,
        service_catalog_version: input.serviceCatalogVersion,
        ...(input.sopProjectPubId ? { sop_project_pub_id: input.sopProjectPubId } : {}),
        ...(input.service2ManifestPubId
          ? { service2_manifest_pub_id: input.service2ManifestPubId }
          : {}),
        ...(input.service2ManifestHash
          ? { service2_manifest_hash: input.service2ManifestHash }
          : {}),
        window_start: input.window.start,
        window_end: input.window.end,
        document_status: input.documentStatus,
        candidate_group_strategy: 'preregistered_scope_v1',
        version: input.version,
        prepared_by: input.preparedBy,
        prepared_date: input.preparedDate,
        ...(input.reviewedBy ? { reviewed_by: input.reviewedBy } : {}),
        ...(input.reviewedDate ? { reviewed_date: input.reviewedDate } : {}),
        ...(input.beforeWindow ? { before_window: input.beforeWindow } : {}),
        ...(input.afterWindow ? { after_window: input.afterWindow } : {}),
      },
      input.idempotencyKey,
    ),
  reviewFormalReportProduction: (
    session: SessionContext,
    input: {
      productionPubId: string;
      decision: FormalReportReviewDecision;
      rationale: string;
      idempotencyKey: string;
    },
  ): Promise<FormalReportProduction> =>
    servicesPostIdempotent(
      session,
      `/api/v2/reports/formal-productions/${encodeURIComponent(input.productionPubId)}/review`,
      { decision: input.decision, rationale: input.rationale },
      input.idempotencyKey,
    ),
  service2CurrentBatch: (session: SessionContext, projectPubId: string): Promise<Service2Batch> =>
    servicesGet(
      session,
      `/api/v2/internal/service2-source-corpus/projects/${encodeURIComponent(projectPubId)}/batches/current`,
      {},
    ),
  service2AnalysisModels: (
    session: SessionContext,
    projectPubId: string,
  ): Promise<Service2AnalysisModelCatalog> =>
    servicesGet(
      session,
      `/api/v2/internal/service2-source-corpus/projects/${encodeURIComponent(projectPubId)}/analysis-models`,
      {},
    ),
  createService2Batch: (
    session: SessionContext,
    input: {
      projectPubId: string;
      runPubIds: string[];
      windowStart: string;
      windowEnd: string;
      sourceSnapshotBoundary: string;
      analysisModel: string;
      idempotencyKey: string;
    },
  ): Promise<Service2Batch> =>
    servicesPostIdempotent(
      session,
      `/api/v2/internal/service2-source-corpus/projects/${encodeURIComponent(input.projectPubId)}/batches`,
      {
        run_pub_ids: input.runPubIds,
        window_start: input.windowStart,
        window_end: input.windowEnd,
        source_snapshot_boundary: input.sourceSnapshotBoundary,
        analysis_model: input.analysisModel,
        corpus_policy_version: 'service2-all-u-occurrence-v1',
        judgment_policy_version: 'service2-entity-relation-v1',
      },
      input.idempotencyKey,
    ),
  service2CorpusItems: (
    session: SessionContext,
    input: {
      projectPubId: string;
      batchPubId: string;
      cursor?: string;
      processingState?: string;
      attributionConfidence?: string;
    },
  ): Promise<Service2CorpusPage> =>
    servicesGet(
      session,
      `/api/v2/internal/service2-source-corpus/projects/${encodeURIComponent(input.projectPubId)}/batches/${encodeURIComponent(input.batchPubId)}/items`,
      {
        page_size: SERVICE2_CORPUS_DEFAULT_PAGE_SIZE,
        ...(input.cursor ? { cursor: input.cursor } : {}),
        ...(input.processingState ? { processing_state: input.processingState } : {}),
        ...(input.attributionConfidence
          ? { attribution_confidence: input.attributionConfidence }
          : {}),
      },
    ),
  service2Findings: (
    session: SessionContext,
    input: {
      projectPubId: string;
      batchPubId: string;
      cursor?: string;
      reviewState?: string;
    },
  ): Promise<Service2FindingPage> =>
    servicesGet(
      session,
      `/api/v2/internal/service2-source-corpus/projects/${encodeURIComponent(input.projectPubId)}/batches/${encodeURIComponent(input.batchPubId)}/findings`,
      {
        page_size: SERVICE2_CORPUS_DEFAULT_PAGE_SIZE,
        ...(input.cursor ? { cursor: input.cursor } : {}),
        ...(input.reviewState ? { review_state: input.reviewState } : {}),
      },
    ),
  service2Lifecycle: (
    session: SessionContext,
    input: {
      projectPubId: string;
      batchPubId: string;
      action: 'start' | 'pause' | 'resume' | 'retry' | 'cancel';
      idempotencyKey: string;
    },
  ): Promise<{ batch_pub_id: string; status: Service2Batch['status']; version: number }> =>
    servicesPostIdempotent(
      session,
      `/api/v2/internal/service2-source-corpus/projects/${encodeURIComponent(input.projectPubId)}/batches/${encodeURIComponent(input.batchPubId)}/actions/${input.action}`,
      {},
      input.idempotencyKey,
    ),
  reviewService2Finding: (
    session: SessionContext,
    input: {
      projectPubId: string;
      batchPubId: string;
      findingPubId: string;
      version: number;
      decision: 'accepted' | 'rejected' | 'needs_changes';
      reasonCode: string;
      rationale: string;
      idempotencyKey: string;
    },
  ): Promise<Service2Finding> =>
    servicesPostIdempotentVersioned(
      session,
      `/api/v2/internal/service2-source-corpus/projects/${encodeURIComponent(input.projectPubId)}/batches/${encodeURIComponent(input.batchPubId)}/findings/${encodeURIComponent(input.findingPubId)}/reviews`,
      {
        decision: input.decision,
        reason_code: input.reasonCode,
        rationale: input.rationale,
      },
      input.idempotencyKey,
      input.version,
    ),
  freezeService2Batch: (
    session: SessionContext,
    input: { projectPubId: string; batchPubId: string; idempotencyKey: string },
  ): Promise<Service2Manifest> =>
    servicesPostIdempotent(
      session,
      `/api/v2/internal/service2-source-corpus/projects/${encodeURIComponent(input.projectPubId)}/batches/${encodeURIComponent(input.batchPubId)}/freeze`,
      {},
      input.idempotencyKey,
    ),
  service2Manifest: (
    session: SessionContext,
    input: { projectPubId: string; batchPubId: string },
  ): Promise<Service2Manifest> =>
    servicesGet(
      session,
      `/api/v2/internal/service2-source-corpus/projects/${encodeURIComponent(input.projectPubId)}/batches/${encodeURIComponent(input.batchPubId)}/manifest`,
      {},
    ),
  service2Manifests: (
    session: SessionContext,
    input: { projectPubId: string; windowStart?: string; windowEnd?: string; limit?: number },
  ): Promise<Service2ManifestOption[]> =>
    servicesGet(
      session,
      `/api/v2/internal/service2-source-corpus/projects/${encodeURIComponent(input.projectPubId)}/manifests`,
      {
        ...(input.windowStart ? { window_start: input.windowStart } : {}),
        ...(input.windowEnd ? { window_end: input.windowEnd } : {}),
        limit: input.limit ?? 50,
      },
    ),
  service2EvidenceBlob: async (session: SessionContext, evidencePubId: string): Promise<Blob> => {
    const response = await fetch(
      servicesUrl(`/api/v2/evidence/assets/${encodeURIComponent(evidencePubId)}/content`),
      {
        headers: fixtureIdentityHeaders(session),
        cache: 'no-store',
        redirect: 'error',
        referrerPolicy: 'no-referrer',
      },
    );
    if (!response.ok) throw await readApiError(response);
    return response.blob();
  },
};

export type { Project, SessionContext };
