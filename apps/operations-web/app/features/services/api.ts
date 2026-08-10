import {
  allowsFixtureIdentityHeaders,
  type BrowserBuildIdentityEnv,
} from '@geo/api-client';
import { executionApi, type FrozenConfig, type Project, type SessionContext } from '../execution/api';

const API_BASE =
  (import.meta as ImportMeta & { env?: { VITE_GEO_API_BASE?: string } }).env?.VITE_GEO_API_BASE ??
  (typeof window === 'undefined' ? '' : window.location.origin);

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

async function readApiError(response: Response): Promise<Error> {
  let code = `http_${response.status}`;
  try {
    const payload = (await response.json()) as
      | { error?: { code?: string }; detail?: { code?: string } }
      | undefined;
    code = payload?.error?.code ?? payload?.detail?.code ?? code;
  } catch {
    // 非 JSON 错误体：保留 http_<status> 口径。
  }
  return new Error(code);
}

async function servicesGet<T>(
  session: SessionContext,
  path: string,
  query: Record<string, string | number>,
): Promise<T> {
  const url = new URL(`${API_BASE}${path}`);
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
  | { kind: 'llm_disabled' }
  | { kind: 'unavailable' };

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
  documents_total: number;
  own_site_documents: number;
  own_site_share: number | null;
  own_site_transcript_total: number;
  own_site_transcript_accurate: number;
  own_site_adoption_rate: number | null;
  verdicts: { transcript: SourceAuditVerdicts; factual: SourceAuditVerdicts };
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
  const hosts = Array.isArray(value.hosts) ? value.hosts : [];
  const items = Array.isArray(value.items) ? value.items : [];
  return {
    project_pub_id: asString(value.project_pub_id),
    start: asString(value.start),
    end: asString(value.end),
    own_site_host: typeof value.own_site_host === 'string' ? value.own_site_host : null,
    documents_total: asNumber(value.documents_total),
    own_site_documents: asNumber(value.own_site_documents),
    own_site_share:
      typeof value.own_site_share === 'number' && Number.isFinite(value.own_site_share)
        ? value.own_site_share
        : null,
    own_site_transcript_total: asNumber(value.own_site_transcript_total),
    own_site_transcript_accurate: asNumber(value.own_site_transcript_accurate),
    own_site_adoption_rate:
      typeof value.own_site_adoption_rate === 'number' &&
      Number.isFinite(value.own_site_adoption_rate)
        ? value.own_site_adoption_rate
        : null,
    verdicts: {
      transcript: projectVerdicts(verdicts.transcript),
      factual: projectVerdicts(verdicts.factual),
    },
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
    input: { projectPubId: string; industry: string; windowDays?: number },
  ): Promise<BrandVisibilityResult> => {
    try {
      const data = await servicesGet<BrandVisibility>(
        session,
        `/api/v2/projects/${encodeURIComponent(input.projectPubId)}/brand-visibility`,
        { industry: input.industry, window_days: input.windowDays ?? 30 },
      );
      return { kind: 'ready', data };
    } catch (error) {
      const code = error instanceof Error ? error.message : '';
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
            typeof row.evidence_document_pub_id === 'string'
              ? row.evidence_document_pub_id
              : null,
        };
      }),
    };
  },
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
};

export type { Project, SessionContext };
