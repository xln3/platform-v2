import {
  AccountSummary,
  Badge,
  clearSafePdfCanvas,
  containsClientSecret,
  createSafeExperienceScopeKey,
  createStructuredClientScopeKey,
  Dialog,
  downloadSafeGeneratedFile,
  FilterBar,
  FormField as Field,
  InterventionStatus,
  MetricGrid,
  navigateClientSection,
  Pagination,
  ProjectionLimitNotice,
  ProductShell,
  projectSafeAccountSummary,
  projectSafeHtmlDocument,
  projectSafePdfPageViewport,
  RevocationReceipt,
  safePdfDocumentOptions,
  SafeHtmlDocument,
  StatePanel,
  TableRegion,
  Toast,
  VerifiedBlobDownload,
  VerifiedBlobImage,
  type AccountSummaryProjection,
  type DataState,
  type ProjectionLimitNoticeItem,
  type RevocationReceiptProjection,
  type SafeHtmlDocumentProjection,
  useOptionalExperienceContext,
} from '@geo/design-system';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import {
  authorizeCustomerAccount,
  bindOidcIdentity,
  commentOnReport,
  confirmReportDelivery,
  createAssetConfirmation,
  createClientProfileVersion,
  createCustomerPairing,
  createEvidencePackage,
  createEvidencePackagePubId,
  createIdentityMember,
  createIntakePromo,
  createIntakeTriggers,
  createMetricExport,
  createProjectResource,
  customerAccountLifecycleProjectionLimits,
  customerAnalyticsProjectionLimits,
  customerEvidenceReadProjectionLimits,
  customerIntakeProjectionLimits,
  deleteIntakePromo,
  deleteIntakeTrigger,
  getAnalyticsAnswerRelations,
  getAnalyticsBreakdown,
  getAnalyticsCompetitors,
  getAnalyticsDelta,
  getAnalyticsOverview,
  getEvidenceAssetContent,
  getHealth,
  getIntakeFormSchema,
  getIntakeProfile,
  getIntakeProfileDocx,
  getIntakeResearchModels,
  getReport,
  getReportArtifact,
  isReportVersionPubId,
  listAnalyticsAnswers,
  listAssetConfirmations,
  listClientProfileVersions,
  listCustomerAccountEvents,
  listCustomerAccounts,
  listCustomerPairings,
  listEvidenceAssets,
  listIdentityMembers,
  listIntakePromos,
  listIntakeTriggers,
  listOidcBindings,
  listProjectResources,
  listReportDeliveries,
  loadProjectReportCatalog,
  projectReportDetailIdentity,
  listResponsibleMembers,
  projectSafeAccountMask,
  projectSafeIsoTimestamp,
  putIntakeProfile,
  registerCustomerAccount,
  revokeCustomerAccount,
  revokeIdentityMember,
  revokeOidcIdentity,
  runIntakeAiResearch,
  updateIntakePromo,
  updateIntakeTrigger,
  type IntakeAiResearchSummary,
  type IntakeFormSchema,
  type IntakeProfileView,
  type IntakeProfileWrite,
  type IntakePromoPayload,
  type IntakePromoView,
  type IntakeTriggerView,
  type ReportPageProjection,
  type ProjectReportCatalogProjection,
  type ReportDetailProjection,
  type ResponsibleMemberView,
  type CustomerAccountView,
  type CustomerEventView,
  type CustomerPairingView,
  type AnalyticsAnswerRelationsProjection,
  type AnalyticsRelationCollection,
  type AnalyticsBreakdownResponse,
  type AnalyticsOverviewMetric,
  type AnalyticsOverviewSafeResponse,
  type AssetConfirmationPage,
  type AssetConfirmationView,
  type ClientProfilePage,
  type ClientProfileView,
  type IdentityMemberView,
  type ProjectedCollection,
  type ProjectedCursorPage,
  type ProjectResourceView,
  type ReportArtifactIntegrity,
  type ResearchModelCatalog,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { GeoBarChart } from '@geo/charts';
import { EvidenceImageFrame, EvidenceViewer, type EvidenceAnchor } from '@geo/evidence-viewer';
import { useEffect, useRef, useState } from 'react';
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import { useSearchParams } from 'react-router';
import { useFieldArray, useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  useCustomerAccountMutationGuard,
  useCustomerMutationGuard,
  type CustomerAccountMutationTicket,
} from './account-mutation-guard';
import { CustomerAnalyticsWorkspace } from './customer-dashboard';
import { CustomerServicesWorkspace } from './customer-services-workspace';
import './ai-dock.css';

const nav = [
  { id: 'home', label: '经营总览', group: '项目首页' },
  { id: 'services', label: '服务总览', group: '我的服务' },
  { id: 'service-1', label: '1 · AI 推荐排名测试', group: '我的服务' },
  { id: 'service-2', label: '2 · 主动拉踩核查', group: '我的服务' },
  { id: 'service-3', label: '3 · 被拉踩核查', group: '我的服务' },
  { id: 'service-4', label: '4 · 官网引用效率', group: '我的服务' },
  { id: 'service-5', label: '5 · 内容发布试点', group: '我的服务' },
  { id: 'evidence', label: '证据中心', group: '报告与交付物' },
  { id: 'reports', label: '报告', group: '报告与交付物' },
  { id: 'profile', label: '客户资料', group: '项目资料与授权' },
  { id: 'intake', label: '客户信息表', group: '项目资料与授权' },
  { id: 'assets', label: '品牌产品与竞品', group: '项目资料与授权' },
  { id: 'questions', label: '监测问题与目标', group: '项目资料与授权' },
  { id: 'members', label: '项目成员', group: '项目资料与授权' },
  {
    id: 'accounts',
    label: 'AI 平台账号与授权',
    group: '项目资料与授权',
    badge: '2',
  },
];
const liveNav = nav.map((item) =>
  item.id === 'accounts' ? { id: item.id, label: item.label, group: item.group } : item,
);
const noClientSecret = (value: string): boolean => !containsClientSecret(value);
const noClientSecretMessage =
  '请勿在普通表单粘贴验证码、Cookie、token、密码、完整手机号或 profile 路径';
const customerNavIds = nav.map((item) => item.id);
const navigateCustomerSection = (section: string) => navigateClientSection(section, customerNavIds);
const optionalExperienceScope = (
  experience: ReturnType<typeof useOptionalExperienceContext>,
): string => (experience ? createSafeExperienceScopeKey(experience) : 'missing-experience');

const account: AccountSummaryProjection = {
  accountMask: '尾号 · 4821',
  platformLabel: '豆包',
  ownerLabel: '客户管理员 · 林澄',
  custodyMode: 'hybrid',
  admissionLevel: 'read_verified',
  scopes: ['read', 'query'],
  expiresLabel: '2026-09-30',
  regionLabel: '中国大陆 · 华东',
  sessionHealth: 'healthy',
  lastVerifiedLabel: '今天 09:42',
  interventionStatus: 'none',
};

const authorizationSchema = z.object({
  platformSlug: z.enum(['doubao']),
  accountMask: z
    .string()
    .trim()
    .min(3, '请填写至少 3 个字符的账号掩码')
    .max(120)
    .refine(
      (value) => projectSafeAccountMask(value) !== null,
      '只填写带 *、尾号或其他明确隐藏标记的账号掩码',
    )
    .refine(noClientSecret, noClientSecretMessage),
  owner: z.string().trim().min(2, '请填写账号 owner').refine(noClientSecret, noClientSecretMessage),
  responsible: z
    .string()
    .trim()
    .min(2, '请填写运营责任人')
    .refine(noClientSecret, noClientSecretMessage),
  custodyMode: z.enum(['server', 'customer-device', 'hybrid']),
  expiresOn: z
    .string()
    .date('请选择授权到期日')
    .refine(
      (value) => new Date(`${value}T23:59:59+08:00`).getTime() > Date.now(),
      '授权到期日必须晚于当前时间',
    ),
  region: z.string().trim().min(2, '请填写授权地域').refine(noClientSecret, noClientSecretMessage),
  scopes: z.array(z.enum(['read', 'query', 'draft', 'publish'])).min(1, '至少选择一个授权动作'),
});

type AuthorizationFields = z.infer<typeof authorizationSchema>;

const safeOpaqueId = (value: unknown, prefix: string): string =>
  typeof value === 'string' &&
  value.startsWith(prefix) &&
  value.length <= 120 &&
  !containsClientSecret(value)
    ? value
    : '';
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

type LiveEvidenceAsset = {
  id: string;
  kind: string;
  mimeType: string;
  capturedAt: string;
  integrity: string;
};

export type SafeReportDelivery = {
  id: string;
  reportId: string;
  deliveredAt: string;
  confirmedAt: string | null;
};
export type SafeReportDeliveryProjection = {
  data: SafeReportDelivery[];
  total: number;
  shown: number;
  invalid: boolean;
};

export type SafeCustomerReportVersion = {
  id: string;
  versionNumber: number;
  status: 'published';
};

export type SafeCustomerReportArtifact = ReportArtifactIntegrity & {
  format: 'html' | 'pdf';
};

export type CustomerReportVersionProjection = {
  versions: SafeCustomerReportVersion[];
  currentVersionId: string;
  artifacts: SafeCustomerReportArtifact[];
  versionTotal: number;
  versionShown: number;
  artifactTotal: number;
  artifactShown: number;
  invalidVersions: boolean;
  invalidArtifacts: boolean;
};

const projectIsoTimestamp = (value: unknown): string => projectSafeIsoTimestamp(value) ?? '';

export function projectReportDeliveryViews(
  values: unknown,
  expectedReportId: string,
  expectedRecipientId: string,
): SafeReportDeliveryProjection {
  if (
    !Array.isArray(values) ||
    !/^rpt_[A-Za-z0-9_-]{1,116}$/.test(expectedReportId) ||
    containsClientSecret(expectedReportId) ||
    !/^usr_[A-Za-z0-9_-]{1,116}$/.test(expectedRecipientId) ||
    containsClientSecret(expectedRecipientId)
  ) {
    return { data: [], total: Array.isArray(values) ? values.length : 0, shown: 0, invalid: true };
  }
  let invalid = values.length > 1;
  const seen = new Set<string>();
  const data = values.slice(0, 50).flatMap<SafeReportDelivery>((candidate) => {
    if (!isRecord(candidate)) {
      invalid = true;
      return [];
    }
    const id =
      typeof candidate.pub_id === 'string' &&
      /^dlv_[A-Za-z0-9_-]{1,116}$/.test(candidate.pub_id) &&
      !containsClientSecret(candidate.pub_id)
        ? candidate.pub_id
        : '';
    const reportId =
      typeof candidate.report_pub_id === 'string' &&
      /^rpt_[A-Za-z0-9_-]{1,116}$/.test(candidate.report_pub_id) &&
      !containsClientSecret(candidate.report_pub_id)
        ? candidate.report_pub_id
        : '';
    const recipientIsSafe =
      typeof candidate.recipient_pub_id === 'string' &&
      /^usr_[A-Za-z0-9_-]{1,116}$/.test(candidate.recipient_pub_id) &&
      !containsClientSecret(candidate.recipient_pub_id);
    const deliveredAt = projectIsoTimestamp(candidate.delivered_at);
    const confirmedAt =
      candidate.confirmed_at === null ? null : projectIsoTimestamp(candidate.confirmed_at);
    if (
      !id ||
      seen.has(id) ||
      reportId !== expectedReportId ||
      !recipientIsSafe ||
      candidate.recipient_pub_id !== expectedRecipientId ||
      !deliveredAt ||
      (candidate.confirmed_at !== null &&
        (!confirmedAt || new Date(confirmedAt).getTime() < new Date(deliveredAt).getTime()))
    ) {
      invalid = true;
      return [];
    }
    seen.add(id);
    return id &&
      reportId === expectedReportId &&
      recipientIsSafe &&
      deliveredAt &&
      (candidate.confirmed_at === null || confirmedAt)
      ? [{ id, reportId, deliveredAt, confirmedAt }]
      : [];
  });
  return { data, total: values.length, shown: data.length, invalid };
}

export function mergeReportDeliveryProjection(
  value: SafeReportDeliveryProjection,
  boundary: ProjectedCollection<unknown>,
): SafeReportDeliveryProjection {
  return {
    ...value,
    total: boundary.projection.total,
    invalid: boundary.projection.invalid || value.invalid,
  };
}

/** Projects the customer-visible published versions and binds every artifact to its version. */
export function projectCustomerReportVersions(
  values: ReportDetailProjection['versions'],
  boundary?: ReportDetailProjection['projection'],
): CustomerReportVersionProjection {
  if (!Array.isArray(values)) {
    return {
      versions: [],
      currentVersionId: '',
      artifacts: [],
      versionTotal: 0,
      versionShown: 0,
      artifactTotal: 0,
      artifactShown: 0,
      invalidVersions: true,
      invalidArtifacts: true,
    };
  }
  const firstRetainedIndex = Math.max(0, values.length - 100);
  const seenIds = new Set<string>();
  const seenNumbers = new Set<number>();
  const admitted: Array<SafeCustomerReportVersion & { raw: Record<string, unknown> }> = [];
  let invalidVersions = values.length === 0;
  let previousVersionNumber = 0;
  for (const [index, candidate] of values.entries()) {
    if (!isRecord(candidate)) {
      invalidVersions = true;
      continue;
    }
    const id =
      isReportVersionPubId(candidate.pub_id) && !containsClientSecret(candidate.pub_id)
        ? candidate.pub_id
        : '';
    const versionNumber =
      typeof candidate.version_number === 'number' &&
      Number.isSafeInteger(candidate.version_number) &&
      candidate.version_number > 0
        ? candidate.version_number
        : 0;
    const windowStart = projectIsoTimestamp(candidate.window_start);
    const windowEnd = projectIsoTimestamp(candidate.window_end);
    const metricVersion =
      typeof candidate.metric_version === 'string' &&
      candidate.metric_version.length > 0 &&
      candidate.metric_version.length <= 120 &&
      !containsClientSecret(candidate.metric_version);
    const scorerVersion =
      typeof candidate.scorer_version === 'string' &&
      candidate.scorer_version.length > 0 &&
      candidate.scorer_version.length <= 120 &&
      !containsClientSecret(candidate.scorer_version);
    const factHash =
      typeof candidate.fact_snapshot_hash === 'string' &&
      /^[0-9a-f]{64}$/.test(candidate.fact_snapshot_hash);
    if (
      !id ||
      !versionNumber ||
      candidate.status !== 'published' ||
      !windowStart ||
      !windowEnd ||
      new Date(windowEnd).getTime() < new Date(windowStart).getTime() ||
      !metricVersion ||
      !scorerVersion ||
      !factHash ||
      seenIds.has(id) ||
      seenNumbers.has(versionNumber) ||
      versionNumber <= previousVersionNumber
    ) {
      invalidVersions = true;
      continue;
    }
    seenIds.add(id);
    seenNumbers.add(versionNumber);
    previousVersionNumber = versionNumber;
    if (index >= firstRetainedIndex) {
      admitted.push({ id, versionNumber, status: 'published', raw: candidate });
    }
  }

  const latest = admitted.at(-1);
  const artifacts = latest && Array.isArray(latest.raw.artifacts) ? latest.raw.artifacts : [];
  let invalidArtifacts = Boolean(latest && !Array.isArray(latest.raw.artifacts));
  if (artifacts.length > 4) invalidArtifacts = true;
  const seenArtifactIds = new Set<string>();
  const seenFormats = new Set<string>();
  const expectedMimeTypes = {
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    html: 'text/html',
    pdf: 'application/pdf',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  } as const;
  const admittedArtifacts: Array<
    ReportArtifactIntegrity & { format: 'docx' | 'html' | 'pdf' | 'xlsx' }
  > = [];
  for (const candidate of artifacts.slice(0, 4)) {
    if (!isRecord(candidate)) {
      invalidArtifacts = true;
      continue;
    }
    const id = safeOpaqueId(candidate.pub_id, 'rpta_');
    const evidenceId = safeOpaqueId(candidate.evidence_pub_id, 'evd_');
    const format =
      typeof candidate.format === 'string' &&
      ['docx', 'pdf', 'xlsx', 'html'].includes(candidate.format)
        ? (candidate.format as 'docx' | 'pdf' | 'xlsx' | 'html')
        : null;
    const mimeType =
      typeof candidate.mime_type === 'string' &&
      candidate.mime_type.length > 0 &&
      candidate.mime_type.length <= 120 &&
      !containsClientSecret(candidate.mime_type)
        ? candidate.mime_type
        : '';
    const byteSize =
      typeof candidate.byte_size === 'number' &&
      Number.isSafeInteger(candidate.byte_size) &&
      candidate.byte_size > 0 &&
      candidate.byte_size <= 50 * 1024 * 1024
        ? candidate.byte_size
        : 0;
    const sha256 =
      typeof candidate.sha256 === 'string' && /^[0-9a-f]{64}$/.test(candidate.sha256)
        ? candidate.sha256
        : '';
    const createdAt = projectIsoTimestamp(candidate.created_at);
    if (
      !latest ||
      !id ||
      !evidenceId ||
      !format ||
      candidate.report_version_pub_id !== latest.id ||
      !mimeType ||
      mimeType !== expectedMimeTypes[format] ||
      !byteSize ||
      !sha256 ||
      !createdAt ||
      seenArtifactIds.has(id) ||
      seenFormats.has(format)
    ) {
      invalidArtifacts = true;
      continue;
    }
    seenArtifactIds.add(id);
    seenFormats.add(format);
    admittedArtifacts.push({ format, byteSize, mimeType, sha256 });
  }
  if (
    boundary?.versions.invalid ||
    (boundary &&
      (boundary.versions.total !== values.length || boundary.versions.shown !== values.length))
  )
    invalidVersions = true;
  const latestBoundary = latest ? boundary?.version_collections[latest.id]?.artifacts : undefined;
  if (
    latestBoundary?.invalid ||
    (latestBoundary &&
      (latestBoundary.total !== artifacts.length || latestBoundary.shown !== artifacts.length))
  )
    invalidArtifacts = true;
  const versionTotal = boundary?.versions.total ?? values.length;
  const artifactTotal = latestBoundary?.total ?? artifacts.length;
  const invalid = invalidVersions || invalidArtifacts;
  return {
    versions: admitted
      .map(({ id, versionNumber, status }) => ({ id, versionNumber, status }))
      .reverse(),
    currentVersionId: invalid ? '' : (latest?.id ?? ''),
    artifacts: invalid
      ? []
      : admittedArtifacts.filter(
          (artifact): artifact is SafeCustomerReportArtifact =>
            artifact.format === 'html' || artifact.format === 'pdf',
        ),
    versionTotal,
    versionShown: admitted.length,
    artifactTotal,
    artifactShown: admittedArtifacts.length,
    invalidVersions,
    invalidArtifacts,
  };
}

export type LiveEvidenceAssetPageProjection = {
  assets: LiveEvidenceAsset[];
  total: number;
  invalid: boolean;
};

export function projectEvidenceAssetPage(value: unknown): LiveEvidenceAssetPageProjection {
  if (!isRecord(value) || !Array.isArray(value.data)) {
    return { assets: [], total: 0, invalid: true };
  }
  let invalid = false;
  const safeText = (candidate: unknown, fallback: string, max = 200): string =>
    typeof candidate === 'string' && candidate.length <= max && !containsClientSecret(candidate)
      ? candidate
      : fallback;
  const assets = value.data
    .slice(0, customerEvidenceProjectionLimits.assets)
    .flatMap((candidate) => {
      if (!isRecord(candidate)) {
        invalid = true;
        return [];
      }
      const id = safeOpaqueId(candidate.pub_id, 'evd_');
      const capturedAt = projectIsoTimestamp(candidate.capture_time);
      if (!id || !capturedAt) {
        invalid = true;
        return [];
      }
      const sha256 = safeText(candidate.sha256, '', 64);
      const kindIsSafe =
        typeof candidate.kind === 'string' &&
        candidate.kind.length > 0 &&
        candidate.kind.length <= 80 &&
        !containsClientSecret(candidate.kind);
      const mimeTypeIsSafe =
        typeof candidate.mime_type === 'string' &&
        candidate.mime_type.length > 0 &&
        candidate.mime_type.length <= 120 &&
        !containsClientSecret(candidate.mime_type);
      const kind = kindIsSafe ? String(candidate.kind) : '未分类';
      const mimeType = mimeTypeIsSafe ? String(candidate.mime_type) : 'application/octet-stream';
      if (!kindIsSafe || !mimeTypeIsSafe || !/^[a-f0-9]{64}$/i.test(sha256)) {
        invalid = true;
      }
      return [
        {
          id,
          kind,
          mimeType,
          capturedAt: capturedAt.slice(0, 16).replace('T', ' '),
          integrity: /^[a-f0-9]{64}$/i.test(sha256) ? `${sha256.slice(0, 8)}…` : '校验值已隐藏',
        },
      ];
    });
  return { assets, total: value.data.length, invalid };
}

export function projectEvidenceAssets(value: unknown): LiveEvidenceAsset[] {
  return projectEvidenceAssetPage(value).assets;
}

type LiveCatalogItem = {
  id: string;
  kind: 'brands' | 'competitors';
  name: string;
  website: string;
};

function projectCatalogItems(
  values: ProjectResourceView[],
  expectedKind: LiveCatalogItem['kind'],
): LiveCatalogItem[] {
  return values.flatMap<LiveCatalogItem>((value) => {
    if (
      value.resource_kind !== expectedKind ||
      !/^[A-Za-z][A-Za-z0-9_-]{2,119}$/.test(value.pub_id) ||
      containsClientSecret(value.pub_id) ||
      !isRecord(value.data)
    ) {
      return [];
    }
    const candidateName =
      typeof value.data.name === 'string'
        ? value.data.name
        : typeof value.data.value === 'string'
          ? value.data.value
          : '';
    const name =
      candidateName.trim().length > 0 &&
      candidateName.length <= 160 &&
      !containsClientSecret(candidateName)
        ? candidateName
        : '';
    if (!name) return [];
    const candidateWebsite = typeof value.data.website === 'string' ? value.data.website : '';
    let website = '';
    try {
      const parsed = new URL(candidateWebsite);
      if (
        parsed.protocol === 'https:' &&
        !parsed.username &&
        !parsed.password &&
        candidateWebsite.length <= 500 &&
        !containsClientSecret(candidateWebsite)
      ) {
        website = parsed.toString();
      }
    } catch {
      // An absent or malformed website is represented as insufficient, never copied through.
    }
    return [{ id: value.pub_id, kind: expectedKind, name, website }];
  });
}

const safeProjectionText = (value: unknown, maxLength: number): value is string =>
  typeof value === 'string' &&
  value.length > 0 &&
  value.length <= maxLength &&
  !containsClientSecret(value);

export const customerGovernanceHistoryLimit = 2;
type GovernanceHistoryProjection<T> = {
  data: T[];
  total: number;
  invalid: boolean;
  nextCursor: string;
};

function projectClientProfileView(
  value: unknown,
  expectedProjectPubId?: string,
): ClientProfileView | null {
  if (!isRecord(value)) return null;
  const createdAt = projectIsoTimestamp(value.created_at);
  if (
    !safeProjectionText(value.pub_id, 120) ||
    !/^cpv_[A-Za-z0-9_-]{1,116}$/.test(value.pub_id) ||
    !safeProjectionText(value.project_pub_id, 120) ||
    !/^prj_[A-Za-z0-9_-]{1,116}$/.test(value.project_pub_id) ||
    (expectedProjectPubId !== undefined && value.project_pub_id !== expectedProjectPubId) ||
    !Number.isSafeInteger(value.revision) ||
    (value.revision as number) < 1 ||
    !safeProjectionText(value.company_name, 160) ||
    !safeProjectionText(value.contact_role, 120) ||
    !safeProjectionText(value.audience, 1000) ||
    !safeProjectionText(value.public_statement, 2000) ||
    !createdAt
  ) {
    return null;
  }
  return {
    pub_id: value.pub_id,
    project_pub_id: value.project_pub_id,
    revision: value.revision as number,
    company_name: value.company_name,
    contact_role: value.contact_role,
    audience: value.audience,
    public_statement: value.public_statement,
    created_at: createdAt,
  };
}

export function projectClientProfileViews(
  values: ClientProfileView[],
  expectedProjectPubId?: string,
): ClientProfileView[] {
  return values.flatMap((value) => {
    const projected = projectClientProfileView(value, expectedProjectPubId);
    return projected ? [projected] : [];
  });
}

function projectAssetConfirmationView(
  value: unknown,
  expectedProjectPubId?: string,
): AssetConfirmationView | null {
  if (!isRecord(value)) return null;
  const createdAt = projectIsoTimestamp(value.created_at);
  let website = '';
  try {
    if (typeof value.website !== 'string') return null;
    const parsed = new URL(value.website);
    if (
      parsed.protocol === 'https:' &&
      !parsed.username &&
      !parsed.password &&
      value.website.length <= 500 &&
      !containsClientSecret(value.website)
    ) {
      website = parsed.toString();
    }
  } catch {
    // Invalid websites make the complete atomic confirmation unusable.
  }
  if (
    !safeProjectionText(value.pub_id, 120) ||
    !/^acv_[A-Za-z0-9_-]{1,116}$/.test(value.pub_id) ||
    !safeProjectionText(value.project_pub_id, 120) ||
    !/^prj_[A-Za-z0-9_-]{1,116}$/.test(value.project_pub_id) ||
    (expectedProjectPubId !== undefined && value.project_pub_id !== expectedProjectPubId) ||
    !Number.isSafeInteger(value.revision) ||
    (value.revision as number) < 1 ||
    !safeProjectionText(value.brand_name, 160) ||
    !website ||
    !safeProjectionText(value.product_name, 200) ||
    !safeProjectionText(value.competitor_name, 200) ||
    !safeProjectionText(value.prohibited_claim, 1000) ||
    !createdAt
  ) {
    return null;
  }
  return {
    pub_id: value.pub_id,
    project_pub_id: value.project_pub_id,
    revision: value.revision as number,
    brand_name: value.brand_name,
    website,
    product_name: value.product_name,
    competitor_name: value.competitor_name,
    prohibited_claim: value.prohibited_claim,
    created_at: createdAt,
  };
}

export function projectAssetConfirmationViews(
  values: AssetConfirmationView[],
  expectedProjectPubId?: string,
): AssetConfirmationView[] {
  return values.flatMap((value) => {
    const projected = projectAssetConfirmationView(value, expectedProjectPubId);
    return projected ? [projected] : [];
  });
}

function projectGovernanceHistoryPage<T extends { pub_id: string; revision: number }>(
  value: unknown,
  requestedCursor: number | undefined,
  projector: (candidate: unknown) => T | null,
  limit: number,
): GovernanceHistoryProjection<T> {
  if (!isRecord(value) || !Array.isArray(value.data)) {
    return { data: [], total: 0, invalid: true, nextCursor: '' };
  }
  const upstreamProjection = isRecord(value.projection) ? value.projection : null;
  const upstreamTotal =
    upstreamProjection &&
    typeof upstreamProjection.total === 'number' &&
    Number.isSafeInteger(upstreamProjection.total) &&
    upstreamProjection.total >= value.data.length
      ? upstreamProjection.total
      : null;
  const upstreamShown =
    upstreamProjection &&
    typeof upstreamProjection.shown === 'number' &&
    Number.isSafeInteger(upstreamProjection.shown) &&
    upstreamProjection.shown === value.data.length
      ? upstreamProjection.shown
      : null;
  let invalid =
    value.projection !== undefined &&
    (upstreamTotal === null ||
      upstreamShown === null ||
      upstreamProjection?.invalid !== Boolean(upstreamProjection?.invalid));
  if (upstreamProjection?.invalid === true) invalid = true;
  let previousRevision = requestedCursor ?? Number.POSITIVE_INFINITY;
  const seen = new Set<string>();
  const data = value.data.slice(0, limit).flatMap<T>((candidate) => {
    const projected = projector(candidate);
    if (!projected || seen.has(projected.pub_id) || projected.revision >= previousRevision) {
      invalid = true;
      return [];
    }
    previousRevision = projected.revision;
    seen.add(projected.pub_id);
    return [projected];
  });
  const cursor =
    value.next_cursor === null
      ? ''
      : typeof value.next_cursor === 'string' && /^[1-9]\d{0,8}$/.test(value.next_cursor)
        ? value.next_cursor
        : null;
  if (
    cursor === null ||
    (value.data.length <= limit &&
      cursor !== '' &&
      (value.data.length === 0 ||
        data.length !== value.data.length ||
        data.at(-1)?.revision !== Number(cursor)))
  ) {
    invalid = true;
  }
  return {
    data,
    total: upstreamTotal ?? value.data.length,
    invalid,
    nextCursor: invalid || value.data.length > limit ? '' : cursor || '',
  };
}

export function projectClientProfilePage(
  value: ClientProfilePage | ProjectedCursorPage<ClientProfileView>,
  expectedProjectPubId: string,
  requestedCursor?: number,
  limit = customerGovernanceHistoryLimit,
): GovernanceHistoryProjection<ClientProfileView> {
  return projectGovernanceHistoryPage(
    value,
    requestedCursor,
    (candidate) => projectClientProfileView(candidate, expectedProjectPubId),
    limit,
  );
}

export function projectAssetConfirmationPage(
  value: AssetConfirmationPage | ProjectedCursorPage<AssetConfirmationView>,
  expectedProjectPubId: string,
  requestedCursor?: number,
  limit = customerGovernanceHistoryLimit,
): GovernanceHistoryProjection<AssetConfirmationView> {
  return projectGovernanceHistoryPage(
    value,
    requestedCursor,
    (candidate) => projectAssetConfirmationView(candidate, expectedProjectPubId),
    limit,
  );
}

type LiveQuestionGoal =
  | { id: string; kind: 'query'; text: string; priority: number }
  | { id: string; kind: 'goal'; metric: string; targetPercent: number; state: string };

function projectQuestionGoals(
  values: ProjectResourceView[],
  expectedKind: 'query-items' | 'goals',
): LiveQuestionGoal[] {
  return values.flatMap<LiveQuestionGoal>((value) => {
    if (
      value.resource_kind !== expectedKind ||
      !/^[A-Za-z][A-Za-z0-9_-]{2,119}$/.test(value.pub_id) ||
      containsClientSecret(value.pub_id) ||
      !isRecord(value.data)
    ) {
      return [];
    }
    if (expectedKind === 'query-items') {
      const text = typeof value.data.text === 'string' ? value.data.text.trim() : '';
      const priority = value.data.priority;
      return text &&
        text.length <= 500 &&
        !containsClientSecret(text) &&
        typeof priority === 'number' &&
        Number.isInteger(priority) &&
        priority >= 0 &&
        priority <= 100
        ? [{ id: value.pub_id, kind: 'query', text, priority }]
        : [];
    }
    const metric = typeof value.data.metric === 'string' ? value.data.metric : '';
    const state = typeof value.data.state === 'string' ? value.data.state : '';
    const payload = isRecord(value.data.payload) ? value.data.payload : null;
    const target = payload?.target;
    if (
      !['mention_rate', 'top3_rate', 'citation_coverage'].includes(metric) ||
      !['draft', 'active', 'paused', 'achieved'].includes(state) ||
      containsClientSecret(metric) ||
      containsClientSecret(state) ||
      typeof target !== 'number' ||
      !Number.isFinite(target) ||
      target < 0 ||
      target > 100
    ) {
      return [];
    }
    return [
      {
        id: value.pub_id,
        kind: 'goal',
        metric,
        targetPercent: target <= 1 ? target * 100 : target,
        state,
      },
    ];
  });
}

export function projectCustomerAccount(
  value: CustomerAccountView,
): AccountSummaryProjection | null {
  const allowedAdmissionLevels = new Set<AccountSummaryProjection['admissionLevel']>([
    'catalogued',
    'adapter_ready',
    'login_verified',
    'read_verified',
    'draft_verified',
    'publish_verified',
    'suspended',
  ]);
  const allowedScopes = new Set<AccountSummaryProjection['scopes'][number]>([
    'read',
    'query',
    'draft',
    'publish',
  ]);
  const interventionMap: Record<
    string,
    NonNullable<AccountSummaryProjection['interventionStatus']>
  > = {
    none: 'none',
    pending: 'waiting',
    task_issued: 'waiting',
    awaiting_platform_probe: 'waiting',
    paired: 'paired',
    refused: 'refused',
    rejected: 'refused',
    timed_out: 'timed_out',
    expired: 'timed_out',
    failed: 'failed',
    completed: 'completed',
  };
  const authorizationExpiresAt =
    value.authorization_expires_at === null
      ? ''
      : projectIsoTimestamp(value.authorization_expires_at);
  const lastVerifiedAt =
    value.last_verified_at === null ? '' : projectIsoTimestamp(value.last_verified_at);
  const scopes = Array.isArray(value.scopes)
    ? value.scopes.filter(
        (scope): scope is AccountSummaryProjection['scopes'][number] =>
          typeof scope === 'string' &&
          allowedScopes.has(scope as AccountSummaryProjection['scopes'][number]),
      )
    : [];
  const admissionLevel = allowedAdmissionLevels.has(
    value.admission_level as AccountSummaryProjection['admissionLevel'],
  )
    ? (value.admission_level as AccountSummaryProjection['admissionLevel'])
    : null;
  const interventionStatus = interventionMap[value.intervention_status];
  if (
    (value.authorization_expires_at !== null && !authorizationExpiresAt) ||
    (value.last_verified_at !== null && !lastVerifiedAt) ||
    !safeProjectionText(value.account_mask, 120) ||
    !safeProjectionText(value.platform_label, 120) ||
    !safeProjectionText(value.owner_label, 120) ||
    !safeProjectionText(value.region_label, 120) ||
    !admissionLevel ||
    scopes.length !== value.scopes.length ||
    new Set(scopes).size !== scopes.length ||
    !interventionStatus
  ) {
    return null;
  }
  return projectSafeAccountSummary({
    accountMask: value.account_mask,
    platformLabel: value.platform_label,
    ownerLabel: value.owner_label,
    custodyMode: value.custody_mode === 'customer_device' ? 'customer-device' : value.custody_mode,
    admissionLevel,
    scopes,
    expiresLabel: authorizationExpiresAt ? authorizationExpiresAt.slice(0, 10) : '—',
    regionLabel: value.region_label,
    sessionHealth: value.session_health,
    lastVerifiedLabel: lastVerifiedAt ? lastVerifiedAt.slice(0, 16).replace('T', ' ') : '尚未验证',
    interventionStatus,
  });
}

export function projectCustomerRevocationReceipt(
  value: CustomerAccountView,
): RevocationReceiptProjection | null {
  const receiptId = safeOpaqueId(value.revocation_receipt_pub_id, 'rev_');
  const revokedAt = projectIsoTimestamp(value.revoked_at);
  if (!receiptId || !revokedAt) {
    return null;
  }
  return {
    receiptId,
    revokedAtLabel: revokedAt.slice(0, 16).replace('T', ' '),
    actorLabel: '未在客户安全投影中公开',
    leasesStopped: true,
    sessionsClosed: true,
    secretCopiesPurged: true,
  };
}

export { customerAccountLifecycleProjectionLimits };

type LifecycleCollectionProjection<T> = {
  data: T[];
  total: number;
  shown: number;
  invalid: boolean;
};

export type SafeCustomerAccountProjection = {
  pubId: string;
  summary: AccountSummaryProjection;
  revocationReceipt: RevocationReceiptProjection | null;
};

function projectCustomerAccountRecord(
  value: CustomerAccountView,
): SafeCustomerAccountProjection | null {
  const pubId = safeOpaqueId(value.pub_id, 'pac_');
  const summary = projectCustomerAccount(value);
  const receiptPubId =
    value.revocation_receipt_pub_id === null
      ? ''
      : safeOpaqueId(value.revocation_receipt_pub_id, 'rev_');
  const revocationReceipt = projectCustomerRevocationReceipt(value);
  if (
    !pubId ||
    !summary ||
    (value.revocation_receipt_pub_id !== null && !receiptPubId) ||
    (value.revoked_at !== null && !revocationReceipt) ||
    (revocationReceipt &&
      (summary.sessionHealth !== 'revoked' ||
        summary.admissionLevel !== 'suspended' ||
        summary.scopes.length > 0))
  ) {
    return null;
  }
  return { pubId, summary, revocationReceipt };
}

export function projectCustomerAccountCollection(
  values: CustomerAccountView[],
  limit: number = customerAccountLifecycleProjectionLimits.accounts,
): LifecycleCollectionProjection<SafeCustomerAccountProjection> {
  const bounded = values.slice(0, limit);
  const data = bounded.flatMap((value) => {
    const projected = projectCustomerAccountRecord(value);
    return projected ? [projected] : [];
  });
  return {
    data,
    total: values.length,
    shown: data.length,
    invalid: data.length !== bounded.length,
  };
}

export function projectResponsibleMemberResult(
  values: ResponsibleMemberView[],
  limit: number = customerAccountLifecycleProjectionLimits.responsibleMembers,
): LifecycleCollectionProjection<ResponsibleMemberView> {
  const allowedRoles = new Set(['customer', 'operator', 'analyst', 'reviewer', 'admin']);
  const seen = new Set<string>();
  let invalid = false;
  const bounded = values.slice(0, limit);
  const data = bounded.flatMap((value) => {
    if (
      !/^usr_[A-Za-z0-9_-]{1,116}$/.test(value.user_pub_id) ||
      containsClientSecret(value.user_pub_id) ||
      !safeProjectionText(value.label, 120) ||
      !allowedRoles.has(value.role) ||
      containsClientSecret(value.role) ||
      seen.has(value.user_pub_id)
    ) {
      invalid = true;
      return [];
    }
    seen.add(value.user_pub_id);
    return [
      {
        user_pub_id: value.user_pub_id,
        label: value.label,
        role: value.role,
      },
    ];
  });
  return { data, total: values.length, shown: data.length, invalid };
}

export function projectResponsibleMemberViews(
  values: ResponsibleMemberView[],
): ResponsibleMemberView[] {
  return projectResponsibleMemberResult(values).data;
}

export type SafeCustomerEventProjection = {
  id: string;
  type: string;
  occurredAt: string;
};

export function projectCustomerEventResult(
  values: CustomerEventView[],
  limit: number = customerAccountLifecycleProjectionLimits.events,
): LifecycleCollectionProjection<SafeCustomerEventProjection> {
  const seen = new Set<string>();
  let invalid = false;
  let previousTimestamp = Number.POSITIVE_INFINITY;
  const bounded = values.slice(0, limit);
  const data = bounded.flatMap((value) => {
    const id = safeOpaqueId(value.pub_id, 'sev_');
    const type =
      typeof value.event_type === 'string' &&
      /^[a-z][a-z0-9_.-]{2,119}$/.test(value.event_type) &&
      !containsClientSecret(value.event_type)
        ? value.event_type
        : '';
    const timestamp = projectIsoTimestamp(value.occurred_at);
    const timestampValue = timestamp ? new Date(timestamp).getTime() : Number.NaN;
    const occurredAt = timestamp ? timestamp.slice(0, 16).replace('T', ' ') : '';
    if (
      !id ||
      !type ||
      !occurredAt ||
      !Number.isFinite(timestampValue) ||
      timestampValue > previousTimestamp ||
      seen.has(id)
    ) {
      invalid = true;
      return [];
    }
    seen.add(id);
    previousTimestamp = timestampValue;
    return [{ id, type, occurredAt }];
  });
  return { data, total: values.length, shown: data.length, invalid };
}

type QuestionRow = {
  question: string;
  prompts: number;
  mention: string;
  rank: string;
  evidence: string;
  evidenceTone: 'positive' | 'info' | 'neutral';
};
const questionRows: QuestionRow[] = [
  {
    question: '企业知识库如何选择？',
    prompts: 12,
    mention: '75%',
    rank: '2.1',
    evidence: '9 条可追溯',
    evidenceTone: 'positive',
  },
  {
    question: '适合制造业的 AI 平台',
    prompts: 14,
    mention: '64%',
    rank: '2.7',
    evidence: '8 条可追溯',
    evidenceTone: 'info',
  },
  {
    question: '私有化大模型方案对比',
    prompts: 12,
    mention: '0%',
    rank: '—',
    evidence: '真实 0',
    evidenceTone: 'neutral',
  },
];

function QuestionTable() {
  const columns: ColumnDef<QuestionRow>[] = [
    { accessorKey: 'question', header: '问题' },
    { accessorKey: 'prompts', header: '提问' },
    { accessorKey: 'mention', header: '提及率' },
    { accessorKey: 'rank', header: '平均排名' },
    {
      id: 'evidence',
      header: '证据',
      cell: ({ row }) => <Badge tone={row.original.evidenceTone}>{row.original.evidence}</Badge>,
    },
  ];
  const table = useReactTable({ data: questionRows, columns, getCoreRowModel: getCoreRowModel() });
  return (
    <table className="data-table">
      <thead>
        {table.getHeaderGroups().map((group) => (
          <tr key={group.id}>
            {group.headers.map((header) => (
              <th key={header.id}>
                {flexRender(header.column.columnDef.header, header.getContext())}
              </th>
            ))}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => (
          <tr key={row.id}>
            {row.getVisibleCells().map((cell) => (
              <td key={cell.id}>
                {flexRender(
                  cell.column.columnDef.cell ?? ((context) => String(context.getValue() ?? '')),
                  cell.getContext(),
                )}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const safeAnalyticsText = (value: unknown, fallback: string, maxLength = 120): string =>
  typeof value === 'string' &&
  value.length > 0 &&
  value.length <= maxLength &&
  !containsClientSecret(value)
    ? value
    : fallback;
const safeAnalyticsMetricStates = [
  'ready',
  'real-zero',
  'insufficient',
  'failed',
  'delayed',
  'experimental',
] as const;
type SafeAnalyticsMetricState = (typeof safeAnalyticsMetricStates)[number];
const projectAnalyticsMetricState = (value: unknown): SafeAnalyticsMetricState | null =>
  typeof value === 'string' && safeAnalyticsMetricStates.includes(value as SafeAnalyticsMetricState)
    ? (value as SafeAnalyticsMetricState)
    : null;

export const customerMonitoringProjectionLimits = customerAnalyticsProjectionLimits;
type MonitoringProjectionResult<T> = {
  data: T[];
  total: number;
  invalid: boolean;
};
const mergeMonitoringProjection = <T,>(
  upstream: ProjectedCollection<unknown>,
  downstream: MonitoringProjectionResult<T>,
): MonitoringProjectionResult<T> => ({
  data: downstream.data,
  total: upstream.projection.total,
  invalid: upstream.projection.invalid || downstream.invalid,
});
const supportedAnalyticsMetrics = [
  'mention_rate',
  'average_rank',
  'top3_rate',
  'citation_coverage',
] as const;

export function projectAnalyticsOverviewResult(
  value: unknown,
): MonitoringProjectionResult<AnalyticsOverviewMetric> {
  if (!Array.isArray(value)) return { data: [], total: 0, invalid: true };
  let invalid = false;
  const seen = new Set<string>();
  const data = value
    .slice(0, customerMonitoringProjectionLimits.overview)
    .flatMap<AnalyticsOverviewMetric>((candidate) => {
      if (!isRecord(candidate)) {
        invalid = true;
        return [];
      }
      const metric =
        typeof candidate.metric === 'string' &&
        supportedAnalyticsMetrics.includes(
          candidate.metric as (typeof supportedAnalyticsMetrics)[number],
        )
          ? candidate.metric
          : '';
      const state = projectAnalyticsMetricState(candidate.state);
      const valueNumber =
        candidate.value === null
          ? null
          : typeof candidate.value === 'number' && Number.isFinite(candidate.value)
            ? candidate.value
            : undefined;
      const numerator =
        candidate.numerator === null
          ? null
          : typeof candidate.numerator === 'number' &&
              Number.isSafeInteger(candidate.numerator) &&
              candidate.numerator >= 0
            ? candidate.numerator
            : undefined;
      const denominator =
        typeof candidate.denominator === 'number' &&
        Number.isSafeInteger(candidate.denominator) &&
        candidate.denominator >= 0
          ? candidate.denominator
          : null;
      const metricVersion = safeAnalyticsText(candidate.metric_version, '', 80);
      const scorerVersion = safeAnalyticsText(candidate.scorer_version, '', 80);
      const filterHash = safeAnalyticsText(candidate.filter_hash, '', 160);
      const valueInDomain =
        valueNumber !== undefined &&
        (valueNumber === null ||
          (metric === 'average_rank'
            ? valueNumber >= 1 && valueNumber <= 1_000_000
            : valueNumber >= 0 && valueNumber <= 1));
      if (
        !metric ||
        seen.has(metric) ||
        !state ||
        valueNumber === undefined ||
        numerator === undefined ||
        denominator === null ||
        (numerator !== null && numerator > denominator) ||
        !valueInDomain ||
        !metricVersion ||
        !scorerVersion ||
        !filterHash
      ) {
        invalid = true;
        return [];
      }
      seen.add(metric);
      return [
        {
          metric,
          value: valueNumber,
          numerator,
          denominator,
          state,
          metric_version: metricVersion,
          scorer_version: scorerVersion,
          filter_hash: filterHash,
        },
      ];
    });
  return { data, total: value.length, invalid };
}

export const projectAnalyticsOverview = (value: unknown): AnalyticsOverviewSafeResponse =>
  projectAnalyticsOverviewResult(value).data;

export function analyticsMetricDataState(metric: AnalyticsOverviewMetric | undefined): DataState {
  if (!metric || metric.denominator <= 0 || metric.value === null) return 'insufficient';
  if (!safeAnalyticsMetricStates.includes(metric.state as SafeAnalyticsMetricState)) {
    return 'insufficient';
  }
  if (metric.state === 'failed') return 'failed';
  if (metric.state === 'delayed') return 'delayed';
  if (metric.state === 'insufficient' || metric.state === 'experimental') return 'insufficient';
  return metric.value === 0 ? 'real-zero' : 'ready';
}

const analyticsMetricStateLabel = (metric: AnalyticsOverviewMetric | undefined): string =>
  ({
    ready: '已完成',
    'real-zero': '真实 0',
    insufficient: '样本不足',
    failed: '计算失败',
    delayed: '数据延迟',
    loading: '正在加载',
    empty: '暂无数据',
    forbidden: '无权查看',
  })[analyticsMetricDataState(metric)];

export function analyticsRateChartState(
  value: number | null,
  answerCount: number,
): 'ready' | 'real-zero' | 'insufficient' {
  if (value === null || answerCount <= 0) return 'insufficient';
  return value === 0 ? 'real-zero' : 'ready';
}

type LiveDeltaMetric = {
  metric: string;
  current: number | null;
  previous: number | null;
  delta: number | null;
};

function projectAnalyticsDelta(value: unknown): LiveDeltaMetric[] {
  if (!isRecord(value)) return [];
  const supported = ['mention_rate', 'average_rank', 'top3_rate', 'citation_coverage'];
  const safeNumber = (candidate: unknown): number | null =>
    typeof candidate === 'number' && Number.isFinite(candidate) && Math.abs(candidate) <= 1_000_000
      ? candidate
      : null;
  return supported.flatMap((metric) => {
    const candidate = value[metric];
    if (!isRecord(candidate)) return [];
    return [
      {
        metric,
        current: safeNumber(candidate.current),
        previous: safeNumber(candidate.previous),
        delta: safeNumber(candidate.delta),
      },
    ];
  });
}

type LiveCompetitorMetric = {
  name: string;
  mentionRate: number;
  mentionCount: number;
  answerCount: number;
};

export function projectAnalyticsCompetitors(
  value: unknown,
): MonitoringProjectionResult<LiveCompetitorMetric> {
  if (!Array.isArray(value)) return { data: [], total: 0, invalid: true };
  let invalid = false;
  const seen = new Set<string>();
  const data = value
    .slice(0, customerMonitoringProjectionLimits.competitors)
    .flatMap<LiveCompetitorMetric>((candidate) => {
      if (!isRecord(candidate)) {
        invalid = true;
        return [];
      }
      const name = typeof candidate.competitor === 'string' ? candidate.competitor.trim() : '';
      const mentionRate = candidate.mention_rate;
      const mentionCount = candidate.mention_count;
      const answerCount = candidate.answer_count;
      if (
        !name ||
        seen.has(name) ||
        name.length > 160 ||
        containsClientSecret(name) ||
        typeof mentionRate !== 'number' ||
        !Number.isFinite(mentionRate) ||
        mentionRate < 0 ||
        mentionRate > 1 ||
        typeof mentionCount !== 'number' ||
        !Number.isSafeInteger(mentionCount) ||
        mentionCount < 0 ||
        typeof answerCount !== 'number' ||
        !Number.isSafeInteger(answerCount) ||
        answerCount < mentionCount
      ) {
        invalid = true;
        return [];
      }
      seen.add(name);
      return [{ name, mentionRate, mentionCount, answerCount }];
    });
  return { data, total: value.length, invalid };
}

type LiveBreakdownRow = {
  key: string;
  day: string | null;
  model: string | null;
  region: string | null;
  mode: string | null;
  questionPubId: string | null;
  questionText: string | null;
  answerCount: number;
  mentionedCount: number;
  mentionRate: number | null;
  averageRank: number | null;
  citationCoverage: number | null;
};

export function projectAnalyticsBreakdown(
  value: AnalyticsBreakdownResponse,
  groupBy: 'day' | 'model' | 'region_mode' | 'question',
): LiveBreakdownRow[] {
  return projectAnalyticsBreakdownResult(value, groupBy).data;
}

export function projectAnalyticsBreakdownResult(
  value: AnalyticsBreakdownResponse,
  groupBy: 'day' | 'model' | 'region_mode' | 'question',
): MonitoringProjectionResult<LiveBreakdownRow> {
  if (!Array.isArray(value)) return { data: [], total: 0, invalid: true };
  const limit =
    groupBy === 'region_mode'
      ? customerMonitoringProjectionLimits.regionMode
      : customerMonitoringProjectionLimits[groupBy];
  let invalid = false;
  const seen = new Set<string>();
  const safeRate = (candidate: unknown): number | null =>
    typeof candidate === 'number' && Number.isFinite(candidate) && candidate >= 0 && candidate <= 1
      ? candidate
      : null;
  const safeCount = (candidate: unknown): number | null =>
    typeof candidate === 'number' &&
    Number.isSafeInteger(candidate) &&
    candidate >= 0 &&
    candidate <= 1_000_000_000
      ? candidate
      : null;
  const data = value.slice(0, limit).flatMap((candidate) => {
    if (!isRecord(candidate) || candidate.group_by !== groupBy) {
      invalid = true;
      return [];
    }
    const day = safeAnalyticsText(candidate.day, '', 10) || null;
    const rowModel = safeAnalyticsText(candidate.model, '', 120) || null;
    const rowRegion = safeAnalyticsText(candidate.region, '', 120) || null;
    const rowMode = safeAnalyticsText(candidate.mode, '', 80) || null;
    const questionPubId = safeOpaqueId(candidate.question_pub_id, 'qry_') || null;
    const questionText = safeAnalyticsText(candidate.question_text, '', 500) || null;
    const answerCount = safeCount(candidate.answer_count);
    const mentionedCount = safeCount(candidate.mentioned_count);
    const mentionRate = candidate.mention_rate === null ? null : safeRate(candidate.mention_rate);
    const averageRank =
      candidate.average_rank === null
        ? null
        : typeof candidate.average_rank === 'number' &&
            Number.isFinite(candidate.average_rank) &&
            candidate.average_rank >= 1 &&
            candidate.average_rank <= 1_000_000
          ? candidate.average_rank
          : undefined;
    const citationCoverage =
      candidate.citation_coverage === null ? null : safeRate(candidate.citation_coverage);
    const dimensionIsValid =
      groupBy === 'day'
        ? Boolean(day && projectIsoTimestamp(`${day}T00:00:00Z`))
        : groupBy === 'model'
          ? Boolean(rowModel)
          : groupBy === 'region_mode'
            ? Boolean(rowRegion && rowMode)
            : Boolean(questionPubId);
    const key = [day, rowModel, rowRegion, rowMode, questionPubId].filter(Boolean).join(':') || '';
    if (
      !dimensionIsValid ||
      !key ||
      seen.has(key) ||
      answerCount === null ||
      mentionedCount === null ||
      mentionedCount > answerCount ||
      (candidate.mention_rate !== null && mentionRate === null) ||
      averageRank === undefined ||
      (candidate.citation_coverage !== null && citationCoverage === null)
    ) {
      invalid = true;
      return [];
    }
    seen.add(key);
    return [
      {
        key,
        day,
        model: rowModel,
        region: rowRegion,
        mode: rowMode,
        questionPubId,
        questionText,
        answerCount,
        mentionedCount,
        mentionRate,
        averageRank,
        citationCoverage,
      },
    ];
  });
  return { data, total: value.length, invalid };
}

function useLocalRetry(): [number, () => void] {
  const [retryKey, setRetryKey] = useState(0);
  return [retryKey, () => setRetryKey((current) => current + 1)];
}

function Monitoring() {
  const experience = useOptionalExperienceContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const [retryKey, retry] = useLocalRetry();
  const [liveMetrics, setLiveMetrics] = useState<AnalyticsOverviewSafeResponse | null>(null);
  const [liveDelta, setLiveDelta] = useState<LiveDeltaMetric[]>([]);
  const [liveCompetitors, setLiveCompetitors] = useState<LiveCompetitorMetric[]>([]);
  const [liveBreakdowns, setLiveBreakdowns] = useState<{
    day: LiveBreakdownRow[];
    model: LiveBreakdownRow[];
    regionMode: LiveBreakdownRow[];
    question: LiveBreakdownRow[];
  }>({ day: [], model: [], regionMode: [], question: [] });
  const [projectionStatus, setProjectionStatus] = useState<
    Record<
      keyof typeof customerMonitoringProjectionLimits,
      { total: number; shown: number; invalid: boolean }
    >
  >({
    overview: { total: 0, shown: 0, invalid: false },
    delta: { total: 0, shown: 0, invalid: false },
    competitors: { total: 0, shown: 0, invalid: false },
    day: { total: 0, shown: 0, invalid: false },
    model: { total: 0, shown: 0, invalid: false },
    regionMode: { total: 0, shown: 0, invalid: false },
    question: { total: 0, shown: 0, invalid: false },
  });
  const [deltaState, setDeltaState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const [competitorState, setCompetitorState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const [breakdownState, setBreakdownState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const [liveState, setLiveState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const [exportState, setExportState] = useState<
    'idle' | 'saving' | 'saved' | 'failed' | 'forbidden'
  >('idle');
  const safeParam = <T extends string>(key: string, allowed: readonly T[], fallback: T): T => {
    const value = searchParams.get(key);
    return value && allowed.includes(value as T) ? (value as T) : fallback;
  };
  const windowValue = safeParam('window', ['7d', '30d', '90d'] as const, '30d');
  const model = safeParam('model', ['all', 'doubao', 'deepseek', 'yuanbao'] as const, 'all');
  const mode = safeParam('mode', ['all', 'quick', 'deep'] as const, 'all');
  const region = safeParam('region', ['all', 'east', 'north', 'south'] as const, 'all');
  const monitoringReadScope = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    String(retryKey),
    windowValue,
    model,
    mode,
    region,
  ]);
  const [liveResultScope, setLiveResultScope] = useState(
    experience?.source === 'live' ? '' : monitoringReadScope,
  );
  const exportWriteContext = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    windowValue,
    model,
    mode,
    region,
  ]);
  const exportWrite = useCustomerMutationGuard(exportWriteContext);
  useEffect(() => {
    setExportState('idle');
  }, [exportWriteContext]);
  useEffect(() => {
    if (experience?.source !== 'live') {
      setLiveResultScope(monitoringReadScope);
      setLiveState('fixture');
      setDeltaState('fixture');
      setCompetitorState('fixture');
      setBreakdownState('fixture');
      return;
    }
    let cancelled = false;
    const commitLiveState = (nextState: 'ready' | 'failed' | 'forbidden') => {
      if (cancelled) return;
      setLiveResultScope(monitoringReadScope);
      setLiveState(nextState);
    };
    const headers = getValidatedIdentityHeaders();
    if (!headers || !experience.projectPubId) {
      setLiveMetrics(null);
      setLiveDelta([]);
      setLiveCompetitors([]);
      setLiveBreakdowns({ day: [], model: [], regionMode: [], question: [] });
      commitLiveState('failed');
      setDeltaState('failed');
      setCompetitorState('failed');
      setBreakdownState('failed');
      return;
    }
    setLiveMetrics(null);
    setLiveDelta([]);
    setLiveCompetitors([]);
    setLiveBreakdowns({ day: [], model: [], regionMode: [], question: [] });
    setLiveState('loading');
    setDeltaState('loading');
    setCompetitorState('loading');
    setBreakdownState('loading');
    setProjectionStatus({
      overview: { total: 0, shown: 0, invalid: false },
      delta: { total: 0, shown: 0, invalid: false },
      competitors: { total: 0, shown: 0, invalid: false },
      day: { total: 0, shown: 0, invalid: false },
      model: { total: 0, shown: 0, invalid: false },
      regionMode: { total: 0, shown: 0, invalid: false },
      question: { total: 0, shown: 0, invalid: false },
    });
    const end = new Date();
    const days = Number.parseInt(windowValue, 10);
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - days + 1);
    const startDate = start.toISOString().slice(0, 10);
    const endDate = end.toISOString().slice(0, 10);
    void getAnalyticsOverview(
      experience.projectPubId,
      startDate,
      endDate,
      {
        ...(model !== 'all' ? { model } : {}),
        ...(region !== 'all' ? { region } : {}),
        ...(mode !== 'all' ? { mode } : {}),
      },
      headers,
    ).then((result) => {
      if (cancelled) return;
      if (result.kind === 'ready') {
        const projected = mergeMonitoringProjection(
          result.data,
          projectAnalyticsOverviewResult(result.data.data),
        );
        setLiveMetrics(projected.data);
        setProjectionStatus((current) => ({
          ...current,
          overview: {
            total: projected.total,
            shown: projected.data.length,
            invalid: projected.invalid,
          },
        }));
        commitLiveState('ready');
      } else {
        setLiveMetrics(null);
        commitLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
    const breakdownDimensions = {
      ...(model !== 'all' ? { model } : {}),
      ...(region !== 'all' ? { region } : {}),
      ...(mode !== 'all' ? { mode } : {}),
    };
    void getAnalyticsDelta(experience.projectPubId, startDate, endDate, headers).then(
      (deltaResult) => {
        if (cancelled) return;
        if (deltaResult.kind !== 'ready') {
          setLiveDelta([]);
          setDeltaState(deltaResult.kind === 'forbidden' ? 'forbidden' : 'failed');
          return;
        }
        const projectedDelta = projectAnalyticsDelta(deltaResult.data.data);
        setLiveDelta(projectedDelta);
        setProjectionStatus((current) => ({
          ...current,
          delta: deltaResult.data.projection,
        }));
        setDeltaState(
          deltaResult.data.projection.invalid &&
            deltaResult.data.projection.total > 0 &&
            projectedDelta.length === 0
            ? 'failed'
            : 'ready',
        );
      },
    );
    void getAnalyticsCompetitors(
      experience.projectPubId,
      startDate,
      endDate,
      {
        ...(model !== 'all' ? { model } : {}),
        ...(region !== 'all' ? { region } : {}),
        ...(mode !== 'all' ? { mode } : {}),
      },
      headers,
    ).then((competitorResult) => {
      if (cancelled) return;
      if (competitorResult.kind !== 'ready') {
        setLiveCompetitors([]);
        setCompetitorState(competitorResult.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      const projectedCompetitors = mergeMonitoringProjection(
        competitorResult.data,
        projectAnalyticsCompetitors(competitorResult.data.data),
      );
      setLiveCompetitors(projectedCompetitors.data);
      setProjectionStatus((current) => ({
        ...current,
        competitors: {
          total: projectedCompetitors.total,
          shown: projectedCompetitors.data.length,
          invalid: projectedCompetitors.invalid,
        },
      }));
      setCompetitorState('ready');
    });
    void Promise.all([
      getAnalyticsBreakdown(
        experience.projectPubId,
        startDate,
        endDate,
        'day',
        breakdownDimensions,
        headers,
      ),
      getAnalyticsBreakdown(
        experience.projectPubId,
        startDate,
        endDate,
        'model',
        breakdownDimensions,
        headers,
      ),
      getAnalyticsBreakdown(
        experience.projectPubId,
        startDate,
        endDate,
        'region_mode',
        breakdownDimensions,
        headers,
      ),
      getAnalyticsBreakdown(
        experience.projectPubId,
        startDate,
        endDate,
        'question',
        breakdownDimensions,
        headers,
      ),
    ]).then(([dayResult, modelResult, regionModeResult, questionResult]) => {
      if (cancelled) return;
      if (
        dayResult.kind !== 'ready' ||
        modelResult.kind !== 'ready' ||
        regionModeResult.kind !== 'ready' ||
        questionResult.kind !== 'ready'
      ) {
        setLiveBreakdowns({ day: [], model: [], regionMode: [], question: [] });
        setBreakdownState(
          [dayResult, modelResult, regionModeResult, questionResult].some(
            (result) => result.kind === 'forbidden',
          )
            ? 'forbidden'
            : 'failed',
        );
        return;
      }
      const dayProjection = mergeMonitoringProjection(
        dayResult.data,
        projectAnalyticsBreakdownResult(dayResult.data.data, 'day'),
      );
      const modelProjection = mergeMonitoringProjection(
        modelResult.data,
        projectAnalyticsBreakdownResult(modelResult.data.data, 'model'),
      );
      const regionModeProjection = mergeMonitoringProjection(
        regionModeResult.data,
        projectAnalyticsBreakdownResult(regionModeResult.data.data, 'region_mode'),
      );
      const questionProjection = mergeMonitoringProjection(
        questionResult.data,
        projectAnalyticsBreakdownResult(questionResult.data.data, 'question'),
      );
      setLiveBreakdowns({
        day: dayProjection.data,
        model: modelProjection.data,
        regionMode: regionModeProjection.data,
        question: questionProjection.data,
      });
      setProjectionStatus((current) => ({
        ...current,
        day: {
          total: dayProjection.total,
          shown: dayProjection.data.length,
          invalid: dayProjection.invalid,
        },
        model: {
          total: modelProjection.total,
          shown: modelProjection.data.length,
          invalid: modelProjection.invalid,
        },
        regionMode: {
          total: regionModeProjection.total,
          shown: regionModeProjection.data.length,
          invalid: regionModeProjection.invalid,
        },
        question: {
          total: questionProjection.total,
          shown: questionProjection.data.length,
          invalid: questionProjection.invalid,
        },
      }));
      setBreakdownState('ready');
    });
    return () => {
      cancelled = true;
    };
  }, [experience, model, mode, monitoringReadScope, region, retryKey, windowValue]);
  const effectiveLiveState =
    experience?.source === 'live' && liveResultScope !== monitoringReadScope
      ? 'loading'
      : liveState;
  const metricValue = (name: string, percent = false): string => {
    const metric = liveMetrics?.find((item) => item.metric === name);
    if (!metric || metric.value === null) return '—';
    return percent ? `${(metric.value * 100).toFixed(1)}%` : metric.value.toFixed(2);
  };
  const metricDetail = (name: string): string => {
    const metric = liveMetrics?.find((item) => item.metric === name);
    return metric
      ? `${metric.numerator ?? '—'} / ${metric.denominator} · ${analyticsMetricStateLabel(metric)}`
      : '暂无可用事实';
  };
  const metricDataState = (name: string): DataState =>
    analyticsMetricDataState(liveMetrics?.find((item) => item.metric === name));
  const deltaMetricLabel = (metric: string): string =>
    ({
      mention_rate: '品牌提及率',
      average_rank: '平均排名',
      top3_rate: 'Top 3 占比',
      citation_coverage: '引用覆盖',
    })[metric] ?? '未知指标';
  const formatDeltaValue = (metric: string, value: number | null): string => {
    if (value === null) return '—';
    return metric === 'average_rank' ? value.toFixed(2) : `${(value * 100).toFixed(1)}%`;
  };
  const updateFilter = (
    key: 'window' | 'model' | 'mode' | 'region',
    value: string,
    fallback: string,
    replace = false,
  ) => {
    const next = new URLSearchParams(searchParams);
    if (value === fallback) next.delete(key);
    else next.set(key, value);
    void setSearchParams(next, { replace });
  };
  const monitoringProjectionLabels: Record<
    keyof typeof customerMonitoringProjectionLimits,
    string
  > = {
    overview: 'KPI 概览',
    delta: '窗口差值',
    competitors: '确认竞品',
    day: '逐日趋势',
    model: '模型表现',
    regionMode: '地域与模式',
    question: '问题级表现',
  };
  const projectionNotices = (
    Object.keys(customerMonitoringProjectionLimits) as Array<
      keyof typeof customerMonitoringProjectionLimits
    >
  ).flatMap<ProjectionLimitNoticeItem>((key) =>
    projectionStatus[key].total > customerMonitoringProjectionLimits[key]
      ? [
          {
            key: `customer-monitoring-${key}`,
            label: monitoringProjectionLabels[key],
            total: projectionStatus[key].total,
            shown: projectionStatus[key].shown,
          },
        ]
      : [],
  );
  const invalidProjectionLabels = (
    Object.keys(customerMonitoringProjectionLimits) as Array<
      keyof typeof customerMonitoringProjectionLimits
    >
  )
    .filter((key) => projectionStatus[key].invalid)
    .map((key) => monitoringProjectionLabels[key]);
  const exportMetrics = async () => {
    if (experience?.source !== 'live' || !experience.projectPubId) {
      const writeTicket = exportWrite.beginFixture();
      if (!writeTicket) return;
      if (
        !downloadSafeGeneratedFile({
          kind: 'csv',
          fileName: 'geo-monitoring-fixture.csv',
          content: 'metric,value\nmention_rate,0.684\naverage_rank,2.4\n',
        })
      ) {
        setExportState('failed');
      }
      exportWrite.finish(writeTicket);
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setExportState('forbidden');
      return;
    }
    const writeTicket = exportWrite.begin(headers);
    if (!writeTicket) return;
    const end = new Date();
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - Number.parseInt(windowValue, 10) + 1);
    setExportState('saving');
    try {
      const result = await createMetricExport(
        {
          project_pub_id: experience.projectPubId,
          start: start.toISOString().slice(0, 10),
          end: end.toISOString().slice(0, 10),
          dimensions: {
            ...(model !== 'all' ? { model } : {}),
            ...(region !== 'all' ? { region } : {}),
            ...(mode !== 'all' ? { mode } : {}),
          },
        },
        headers,
      );
      if (!exportWrite.isCurrent(writeTicket)) return;
      setExportState(
        result.kind === 'ready' ? 'saved' : result.kind === 'forbidden' ? 'forbidden' : 'failed',
      );
    } finally {
      exportWrite.finish(writeTicket);
    }
  };
  if (experience?.source === 'live' && effectiveLiveState === 'loading') {
    return <StatePanel state="loading" />;
  }
  if (experience?.source === 'live' && effectiveLiveState === 'failed') {
    return <StatePanel state="failed" onRetry={retry} />;
  }
  if (experience?.source === 'live' && effectiveLiveState === 'forbidden') {
    return <StatePanel state="forbidden" />;
  }
  return (
    <>
      <FilterBar label="监测筛选">
        <label>
          时间窗口
          <select
            aria-label="时间窗口"
            value={windowValue}
            onChange={(event) => updateFilter('window', event.target.value, '30d')}
          >
            <option value="7d">近 7 天</option>
            <option value="30d">近 30 天</option>
            <option value="90d">近 90 天</option>
          </select>
        </label>
        <label>
          模型
          <select
            aria-label="模型"
            value={model}
            onChange={(event) => updateFilter('model', event.target.value, 'all')}
          >
            <option value="all">全部模型</option>
            <option value="doubao">豆包</option>
            <option value="deepseek">DeepSeek</option>
            <option value="yuanbao">元宝</option>
          </select>
        </label>
        <label>
          回答模式
          <select
            aria-label="回答模式"
            value={mode}
            onChange={(event) => updateFilter('mode', event.target.value, 'all')}
          >
            <option value="all">全部模式</option>
            <option value="quick">快速</option>
            <option value="deep">深度思考</option>
          </select>
        </label>
        <label>
          地域
          <select
            aria-label="监测地域"
            value={region}
            onChange={(event) => updateFilter('region', event.target.value, 'all')}
          >
            <option value="all">全部地域</option>
            <option value="east">华东</option>
            <option value="north">华北</option>
            <option value="south">华南</option>
          </select>
        </label>
        <button
          className="button button-secondary"
          onClick={() => {
            const next = new URLSearchParams(searchParams);
            next.delete('window');
            next.delete('model');
            next.delete('mode');
            next.delete('region');
            void setSearchParams(next, { replace: true });
          }}
        >
          重置筛选
        </button>
        <button
          className="button"
          disabled={exportState === 'saving'}
          onClick={() => void exportMetrics()}
        >
          {exportState === 'saving' ? '正在生成…' : '导出当前筛选 XLSX'}
        </button>
      </FilterBar>
      {experience?.source === 'live' && projectionNotices.length ? (
        <ProjectionLimitNotice items={projectionNotices} />
      ) : null}
      {experience?.source === 'live' && invalidProjectionLabels.length ? (
        <div className="confirmation projection-limit-notice" role="alert">
          <Badge tone="warning">安全投影不完整</Badge>
          <span>
            {invalidProjectionLabels.join('、')}包含未通过身份、维度、计数、比率或 DLP
            校验的行；当前窗口不会把可见子集声称为完整统计。
          </span>
        </div>
      ) : null}
      {exportState === 'saved' ? <Toast>真实 XLSX 导出已冻结并进入证据存储</Toast> : null}
      {exportState === 'failed' ? (
        <Toast tone="negative">导出服务暂不可用；未生成本地伪造文件。</Toast>
      ) : null}
      {exportState === 'forbidden' ? (
        <Toast tone="negative">无权导出，且不会显示项目或导出是否存在。</Toast>
      ) : null}
      {effectiveLiveState === 'ready' && liveMetrics?.length === 0 ? (
        <StatePanel state="empty" />
      ) : null}
      {effectiveLiveState !== 'ready' || liveMetrics?.length ? (
        <MetricGrid
          metrics={
            effectiveLiveState === 'ready'
              ? [
                  {
                    label: '品牌提及率',
                    value: metricValue('mention_rate', true),
                    detail: metricDetail('mention_rate'),
                    state: metricDataState('mention_rate'),
                  },
                  {
                    label: '平均排名',
                    value: metricValue('average_rank'),
                    detail: metricDetail('average_rank'),
                    state: metricDataState('average_rank'),
                  },
                  {
                    label: 'Top 3 占比',
                    value: metricValue('top3_rate', true),
                    detail: metricDetail('top3_rate'),
                    state: metricDataState('top3_rate'),
                  },
                  {
                    label: '引用覆盖',
                    value: metricValue('citation_coverage', true),
                    detail: metricDetail('citation_coverage'),
                    state: metricDataState('citation_coverage'),
                  },
                ]
              : [
                  {
                    label: '品牌提及率',
                    value: '68.4%',
                    detail: 'Contract fixture · 26 / 38',
                  },
                  { label: '平均排名', value: '2.4', detail: 'Contract fixture · 38 样本' },
                  { label: 'Top 3 占比', value: '73.7%', detail: 'Contract fixture · 28 / 38' },
                  { label: '引用覆盖', value: '55.3%', detail: 'Contract fixture · 21 / 38' },
                ]
          }
        />
      ) : null}
      {experience?.source === 'live' ? (
        <section className="panel">
          <h2>窗口对比</h2>
          <p className="panel-subtitle">
            当前冻结窗口与相邻等长窗口使用同一指标口径；缺失值不会按零展示。
          </p>
          {deltaState === 'loading' ? (
            <StatePanel state="loading" />
          ) : deltaState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : deltaState === 'failed' ? (
            <StatePanel state="failed" onRetry={retry} />
          ) : liveDelta.length ? (
            <TableRegion label="监测指标窗口对比">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>指标</th>
                    <th>当前窗口</th>
                    <th>上一窗口</th>
                    <th>变化</th>
                  </tr>
                </thead>
                <tbody>
                  {liveDelta.map((item) => (
                    <tr key={item.metric}>
                      <td>{deltaMetricLabel(item.metric)}</td>
                      <td>{formatDeltaValue(item.metric, item.current)}</td>
                      <td>{formatDeltaValue(item.metric, item.previous)}</td>
                      <td>{formatDeltaValue(item.metric, item.delta)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableRegion>
          ) : (
            <StatePanel state="empty" />
          )}
        </section>
      ) : null}
      <div className="two-column">
        <section className="panel">
          <h2>模型表现</h2>
          <p className="panel-subtitle">同一冻结窗口，不把未准入平台混入比较。</p>
          {experience?.source === 'live' ? (
            breakdownState === 'loading' ? (
              <StatePanel state="loading" />
            ) : breakdownState === 'forbidden' ? (
              <StatePanel state="forbidden" />
            ) : breakdownState === 'failed' ? (
              <StatePanel state="failed" onRetry={retry} />
            ) : liveBreakdowns.model.length ? (
              <GeoBarChart
                title="各模型品牌提及率"
                valueSuffix="%"
                data={liveBreakdowns.model.map((item) => ({
                  label: item.model ?? '未知模型',
                  value: (item.mentionRate ?? 0) * 100,
                  state: analyticsRateChartState(item.mentionRate, item.answerCount),
                }))}
              />
            ) : (
              <StatePanel state="empty" />
            )
          ) : (
            <GeoBarChart
              title="各模型品牌提及率"
              valueSuffix="%"
              data={[
                { label: '豆包', value: 82, state: 'ready' },
                { label: 'DeepSeek', value: 71, state: 'ready' },
                { label: '元宝', value: 64, state: 'ready' },
                { label: 'Kimi', value: 57, state: 'ready' },
              ]}
            />
          )}
        </section>
        <section className="panel">
          <h2>数据诚实状态</h2>
          <p className="panel-subtitle">每种状态有独立语义。</p>
          <StatePanel state="insufficient" />
        </section>
      </div>
      <div className="two-column">
        <section className="panel">
          <h2>趋势</h2>
          <p className="panel-subtitle">
            {experience?.source === 'live'
              ? '按真实回答捕获日聚合，不用累计值冒充单日表现。'
              : '按冻结日展示品牌提及率，不用累计值冒充单日表现。'}
          </p>
          {experience?.source === 'live' ? (
            breakdownState === 'loading' ? (
              <StatePanel state="loading" />
            ) : breakdownState === 'forbidden' ? (
              <StatePanel state="forbidden" />
            ) : breakdownState === 'failed' ? (
              <StatePanel state="failed" onRetry={retry} />
            ) : liveBreakdowns.day.length ? (
              <GeoBarChart
                title="逐日品牌提及率"
                valueSuffix="%"
                data={liveBreakdowns.day.map((item) => ({
                  label: item.day ?? '未知日期',
                  value: (item.mentionRate ?? 0) * 100,
                  state: analyticsRateChartState(item.mentionRate, item.answerCount),
                }))}
              />
            ) : (
              <StatePanel state="empty" />
            )
          ) : (
            <GeoBarChart
              title="近五个冻结日品牌提及率趋势"
              valueSuffix="%"
              data={[
                { label: '07-17', value: 61, state: 'ready' },
                { label: '07-19', value: 63, state: 'ready' },
                { label: '07-21', value: 65, state: 'ready' },
                { label: '07-23', value: 67, state: 'ready' },
                { label: '07-24', value: 68.4, state: 'ready' },
              ]}
            />
          )}
        </section>
        <section className="panel">
          <h2>竞品表现</h2>
          <p className="panel-subtitle">仅比较客户确认的竞品集合。</p>
          {experience?.source === 'live' ? (
            competitorState === 'loading' ? (
              <StatePanel state="loading" />
            ) : competitorState === 'forbidden' ? (
              <StatePanel state="forbidden" />
            ) : competitorState === 'failed' ? (
              <StatePanel state="failed" onRetry={retry} />
            ) : liveCompetitors.length ? (
              <GeoBarChart
                title="确认竞品提及率"
                valueSuffix="%"
                data={liveCompetitors.map((item) => ({
                  label: item.name,
                  value: item.mentionRate * 100,
                  state: analyticsRateChartState(item.mentionRate, item.answerCount),
                }))}
              />
            ) : (
              <StatePanel state="empty" />
            )
          ) : (
            <GeoBarChart
              title="品牌与确认竞品提及率"
              valueSuffix="%"
              data={[
                { label: '澄明云', value: 68.4, state: 'ready' },
                { label: '北辰智库', value: 52.6, state: 'ready' },
                { label: '知川平台', value: 41.2, state: 'ready' },
              ]}
            />
          )}
        </section>
      </div>
      <section className="panel">
        <h2>地域与回答模式</h2>
        <p className="panel-subtitle">
          地域和模式使用同一指标版本；“深度思考”不是更高质量结论的替代口径。
        </p>
        {experience?.source === 'live' ? (
          breakdownState === 'loading' ? (
            <StatePanel state="loading" />
          ) : breakdownState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : breakdownState === 'failed' ? (
            <StatePanel state="failed" onRetry={retry} />
          ) : liveBreakdowns.regionMode.length ? (
            <TableRegion label="地域与回答模式表现">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>地域</th>
                    <th>模式</th>
                    <th>有效回答</th>
                    <th>品牌提及率</th>
                    <th>平均排名</th>
                  </tr>
                </thead>
                <tbody>
                  {liveBreakdowns.regionMode.map((item) => (
                    <tr key={item.key}>
                      <td>{item.region ?? '未标注地域'}</td>
                      <td>{item.mode ?? '未标注模式'}</td>
                      <td>{item.answerCount}</td>
                      <td>
                        {item.mentionRate === null
                          ? '样本不足'
                          : `${(item.mentionRate * 100).toFixed(1)}%`}
                      </td>
                      <td>{item.averageRank?.toFixed(2) ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableRegion>
          ) : (
            <StatePanel state="empty" />
          )
        ) : (
          <TableRegion label="地域与回答模式表现">
            <table className="data-table">
              <thead>
                <tr>
                  <th>地域</th>
                  <th>模式</th>
                  <th>有效回答</th>
                  <th>品牌提及率</th>
                  <th>平均排名</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>华东</td>
                  <td>深度思考</td>
                  <td>14</td>
                  <td>71.4%</td>
                  <td>2.1</td>
                </tr>
                <tr>
                  <td>华北</td>
                  <td>快速</td>
                  <td>12</td>
                  <td>66.7%</td>
                  <td>2.5</td>
                </tr>
                <tr>
                  <td>华南</td>
                  <td>深度思考</td>
                  <td>12</td>
                  <td>66.7%</td>
                  <td>2.7</td>
                </tr>
              </tbody>
            </table>
          </TableRegion>
        )}
      </section>
      <section className="panel">
        <h2>问题级表现</h2>
        <p className="panel-subtitle">可从指标下钻到贡献回答与证据。</p>
        {experience?.source === 'live' ? (
          breakdownState === 'loading' ? (
            <StatePanel state="loading" />
          ) : breakdownState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : breakdownState === 'failed' ? (
            <StatePanel state="failed" onRetry={retry} />
          ) : liveBreakdowns.question.length ? (
            <TableRegion label="问题级表现">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>问题</th>
                    <th>有效回答</th>
                    <th>品牌提及率</th>
                    <th>平均排名</th>
                    <th>引用覆盖</th>
                  </tr>
                </thead>
                <tbody>
                  {liveBreakdowns.question.map((item) => (
                    <tr key={item.key}>
                      <td>{item.questionText ?? item.questionPubId ?? '未关联问题'}</td>
                      <td>{item.answerCount}</td>
                      <td>
                        {item.mentionRate === null
                          ? '样本不足'
                          : `${(item.mentionRate * 100).toFixed(1)}%`}
                      </td>
                      <td>{item.averageRank?.toFixed(2) ?? '—'}</td>
                      <td>
                        {item.citationCoverage === null
                          ? '样本不足'
                          : `${(item.citationCoverage * 100).toFixed(1)}%`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableRegion>
          ) : (
            <StatePanel state="empty" />
          )
        ) : (
          <QuestionTable />
        )}
      </section>
    </>
  );
}

export type CustomerPairingStage = 'waiting' | 'completed' | 'refused' | 'timed_out' | 'failed';

export function projectCustomerPairingStage(value: unknown): CustomerPairingStage {
  if (value === 'completed') return 'completed';
  if (value === 'refused' || value === 'rejected') return 'refused';
  if (value === 'timed_out' || value === 'expired') return 'timed_out';
  if (['pending', 'paired', 'task_issued', 'awaiting_platform_probe'].includes(String(value))) {
    return 'waiting';
  }
  return 'failed';
}

type SafeCustomerPairingProjection = {
  pubId: string;
  stage: CustomerPairingStage;
};

function projectCustomerPairing(
  value: CustomerPairingView,
  expectedAccountPubId: string,
): SafeCustomerPairingProjection | null {
  const pubId = safeOpaqueId(value.pub_id, 'int_');
  const accountPubId = safeOpaqueId(value.account_pub_id, 'pac_');
  const expiresAt = value.expires_at === null ? '' : projectIsoTimestamp(value.expires_at);
  const allowedActions = new Set(['read', 'query', 'draft', 'publish']);
  const allowedChallengeTypes = new Set(['otp', 'qr', 'push', 'passkey', 'face', 'graphical']);
  const allowedStates = new Set([
    'pending',
    'paired',
    'task_issued',
    'awaiting_platform_probe',
    'completed',
    'refused',
    'rejected',
    'timed_out',
    'expired',
    'failed',
  ]);
  const domain = typeof value.allowed_domain === 'string' ? value.allowed_domain.toLowerCase() : '';
  if (
    !pubId ||
    accountPubId !== expectedAccountPubId ||
    !safeProjectionText(value.account_mask, 120) ||
    !/^(?=.{3,255}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(domain) ||
    !allowedActions.has(value.action) ||
    !allowedChallengeTypes.has(value.challenge_type) ||
    !allowedStates.has(value.state) ||
    containsClientSecret(value.action) ||
    containsClientSecret(value.challenge_type) ||
    containsClientSecret(value.state) ||
    (value.expires_at !== null && !expiresAt)
  ) {
    return null;
  }
  return { pubId, stage: projectCustomerPairingStage(value.state) };
}

export function projectCustomerPairingResult(
  values: CustomerPairingView[],
  expectedAccountPubId: string,
  expectedPairingPubId: string,
  limit: number = customerAccountLifecycleProjectionLimits.pairings,
): LifecycleCollectionProjection<SafeCustomerPairingProjection> & {
  current: SafeCustomerPairingProjection | null;
} {
  const expectedAccount = safeOpaqueId(expectedAccountPubId, 'pac_');
  const expectedPairing = safeOpaqueId(expectedPairingPubId, 'int_');
  if (
    expectedAccount !== expectedAccountPubId ||
    expectedPairing !== expectedPairingPubId ||
    limit < 1
  ) {
    return { data: [], total: values.length, shown: 0, invalid: true, current: null };
  }
  const seen = new Set<string>();
  let invalid = false;
  const bounded = values.slice(0, limit);
  const data = bounded.flatMap((value) => {
    const projected = projectCustomerPairing(value, expectedAccountPubId);
    if (!projected || seen.has(projected.pubId)) {
      invalid = true;
      return [];
    }
    seen.add(projected.pubId);
    return [projected];
  });
  const current = data.find((item) => item.pubId === expectedPairingPubId) ?? null;
  return {
    data: current ? [current] : [],
    total: values.length,
    shown: current ? 1 : 0,
    invalid: invalid || current === null,
    current,
  };
}

function Accounts() {
  const experience = useOptionalExperienceContext();
  const live = experience?.source === 'live';
  const [retryKey, retry] = useLocalRetry();
  const accountWriteContext = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    String(retryKey),
  ]);
  const accountWrite = useCustomerAccountMutationGuard(accountWriteContext);
  const [accountMutationPending, setAccountMutationPending] = useState(false);
  const [safeAccount, setSafeAccount] = useState(account);
  const [liveAccountPubId, setLiveAccountPubId] = useState('');
  const [livePairingPubId, setLivePairingPubId] = useState('');
  const liveAccountPubIdRef = useRef('');
  const livePairingPubIdRef = useRef('');
  const eventRequestGenerationRef = useRef(0);
  const pairingRequestGenerationRef = useRef(0);
  const accountRequestGenerationRef = useRef(0);
  const revocationInFlightRef = useRef(false);
  const [integrationState, setIntegrationState] = useState<
    'fixture' | 'loading' | 'ready' | 'empty' | 'failed' | 'forbidden'
  >(live ? 'loading' : 'fixture');
  const [eventState, setEventState] = useState<
    'fixture' | 'loading' | 'ready' | 'empty' | 'failed' | 'forbidden'
  >(live ? 'loading' : 'fixture');
  const [safeEvents, setSafeEvents] = useState<SafeCustomerEventProjection[]>([]);
  const [responsibleMembers, setResponsibleMembers] = useState<ResponsibleMemberView[]>([]);
  const [lifecycleProjectionStatus, setLifecycleProjectionStatus] = useState<
    Record<
      keyof typeof customerAccountLifecycleProjectionLimits,
      { total: number; shown: number; invalid: boolean }
    >
  >({
    accounts: { total: 0, shown: 0, invalid: false },
    responsibleMembers: { total: 0, shown: 0, invalid: false },
    events: { total: 0, shown: 0, invalid: false },
    pairings: { total: 0, shown: 0, invalid: false },
  });
  const [liveRevocationReceipt, setLiveRevocationReceipt] =
    useState<RevocationReceiptProjection | null>(null);
  const [liveActionMessage, setLiveActionMessage] = useState('');
  const [authorizationSaved, setAuthorizationSaved] = useState(false);
  const [showRevocationGuide, setShowRevocationGuide] = useState(false);
  const [stage, setStage] = useState<
    | 'registered'
    | 'pairing'
    | 'paired'
    | 'waiting'
    | 'completed'
    | 'refused'
    | 'timed_out'
    | 'failed'
    | 'revoked'
  >('registered');
  const authorizationDefaults: AuthorizationFields = live
    ? {
        platformSlug: 'doubao',
        accountMask: '',
        owner: experience?.userLabel ?? '当前认证主体',
        responsible: '',
        custodyMode: 'customer-device',
        expiresOn: '',
        region: '',
        scopes: [],
      }
    : {
        platformSlug: 'doubao',
        accountMask: '尾号 · 4821',
        owner: '林澄',
        responsible: '周岚',
        custodyMode: 'hybrid',
        expiresOn: '2026-09-30',
        region: '中国大陆 · 华东',
        scopes: ['read', 'query'],
      };
  const {
    register,
    handleSubmit,
    reset: resetAuthorization,
    setValue,
    formState: { errors, isValid, isSubmitting },
  } = useForm<AuthorizationFields>({
    resolver: zodResolver(authorizationSchema),
    defaultValues: authorizationDefaults,
    mode: 'onChange',
  });
  const accountMutationBusy = accountMutationPending && accountWrite.isActive();
  const recordProjectionStatus = (
    key: keyof typeof customerAccountLifecycleProjectionLimits,
    projection: { total: number; shown: number; invalid: boolean },
  ) => {
    setLifecycleProjectionStatus((current) => ({
      ...current,
      [key]: {
        total: projection.total,
        shown: projection.shown,
        invalid: projection.invalid,
      },
    }));
  };
  const mergeLifecycleProjection = <
    Projection extends { total: number; shown: number; invalid: boolean },
  >(
    projection: Projection,
    source: { total: number; shown: number; invalid: boolean },
  ): Projection => ({
    ...projection,
    total: source.total,
    invalid: projection.invalid || source.invalid,
  });
  const setActivePairingPubId = (pubId: string) => {
    livePairingPubIdRef.current = pubId;
    setLivePairingPubId(pubId);
  };
  const applyProjectedLiveAccount = (value: SafeCustomerAccountProjection): string => {
    setSafeAccount(value.summary);
    setLiveRevocationReceipt(value.revocationReceipt);
    if (value.revocationReceipt) {
      eventRequestGenerationRef.current += 1;
      pairingRequestGenerationRef.current += 1;
      liveAccountPubIdRef.current = '';
      setLiveAccountPubId('');
      setActivePairingPubId('');
      setStage('revoked');
      resetAuthorization(authorizationDefaults);
      setIntegrationState('ready');
      return value.pubId;
    }
    if (liveAccountPubIdRef.current !== value.pubId) {
      eventRequestGenerationRef.current += 1;
      pairingRequestGenerationRef.current += 1;
      setActivePairingPubId('');
    }
    setValue('accountMask', value.summary.accountMask, { shouldDirty: false });
    setValue('custodyMode', value.summary.custodyMode, { shouldDirty: false });
    setValue(
      'expiresOn',
      /^\d{4}-\d{2}-\d{2}$/.test(value.summary.expiresLabel) ? value.summary.expiresLabel : '',
      { shouldDirty: false },
    );
    setValue('region', value.summary.regionLabel === '—' ? '' : value.summary.regionLabel, {
      shouldDirty: false,
    });
    setValue('scopes', [...value.summary.scopes], {
      shouldDirty: false,
      shouldValidate: true,
    });
    liveAccountPubIdRef.current = value.pubId;
    setLiveAccountPubId(value.pubId);
    setIntegrationState('ready');
    return value.pubId;
  };
  const applyLiveAccount = (value: CustomerAccountView): string => {
    const projected = projectCustomerAccountRecord(value);
    return projected ? applyProjectedLiveAccount(projected) : '';
  };
  const refreshEvents = async (accountPubId: string) => {
    const requestGeneration = ++eventRequestGenerationRef.current;
    if (accountPubId !== liveAccountPubIdRef.current) return;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setEventState('forbidden');
      return;
    }
    setEventState('loading');
    const result = await listCustomerAccountEvents(accountPubId, headers);
    if (
      requestGeneration !== eventRequestGenerationRef.current ||
      accountPubId !== liveAccountPubIdRef.current
    ) {
      return;
    }
    if (result.kind !== 'ready') {
      setSafeEvents([]);
      setEventState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      return;
    }
    const projection = mergeLifecycleProjection(
      projectCustomerEventResult(result.data.data),
      result.data.projection,
    );
    recordProjectionStatus('events', projection);
    setSafeEvents(projection.data);
    setEventState(projection.data.length ? 'ready' : projection.invalid ? 'failed' : 'empty');
  };
  useEffect(() => {
    setAccountMutationPending(false);
    setAuthorizationSaved(false);
    setLiveActionMessage('');
    setStage('registered');
    if (!live) {
      setIntegrationState('fixture');
      setEventState('fixture');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setIntegrationState('forbidden');
      return;
    }
    let active = true;
    accountRequestGenerationRef.current += 1;
    eventRequestGenerationRef.current += 1;
    pairingRequestGenerationRef.current += 1;
    revocationInFlightRef.current = false;
    liveAccountPubIdRef.current = '';
    livePairingPubIdRef.current = '';
    setLiveAccountPubId('');
    setLivePairingPubId('');
    setSafeEvents([]);
    setEventState('loading');
    setIntegrationState('loading');
    void Promise.all([listCustomerAccounts(headers), listResponsibleMembers(headers)]).then(
      ([result, memberResult]) => {
        if (!active) return;
        if (result.kind === 'forbidden' || memberResult.kind === 'forbidden') {
          setIntegrationState('forbidden');
          return;
        }
        if (result.kind === 'unavailable' || memberResult.kind === 'unavailable') {
          setIntegrationState('failed');
          return;
        }
        const memberProjection = mergeLifecycleProjection(
          projectResponsibleMemberResult(memberResult.data.data),
          memberResult.data.projection,
        );
        const accountProjection = mergeLifecycleProjection(
          projectCustomerAccountCollection(result.data.data),
          result.data.projection,
        );
        recordProjectionStatus('responsibleMembers', memberProjection);
        recordProjectionStatus('accounts', accountProjection);
        setResponsibleMembers(memberProjection.data);
        if (memberProjection.data[0]) {
          setValue('responsible', memberProjection.data[0].user_pub_id, {
            shouldDirty: false,
            shouldValidate: false,
          });
        }
        const first = accountProjection.data[0];
        if (!first) {
          setIntegrationState(accountProjection.invalid ? 'failed' : 'empty');
          setEventState('empty');
          return;
        }
        const pubId = applyProjectedLiveAccount(first);
        void refreshEvents(pubId);
      },
    );
    return () => {
      active = false;
      accountRequestGenerationRef.current += 1;
      eventRequestGenerationRef.current += 1;
      pairingRequestGenerationRef.current += 1;
    };
  }, [accountWriteContext, live, retryKey]);
  const saveAuthorization = async (fields: AuthorizationFields) => {
    setLiveActionMessage('');
    if (live) {
      const headers = getValidatedIdentityHeaders();
      if (!headers) {
        setIntegrationState('forbidden');
        return;
      }
      const writeTicket = accountWrite.begin(headers);
      if (!writeTicket) return;
      setAccountMutationPending(true);
      setAuthorizationSaved(false);
      const startingAccountPubId = liveAccountPubIdRef.current;
      try {
        let accountPubId = startingAccountPubId;
        if (!accountPubId) {
          const registration = await registerCustomerAccount(
            {
              platform_slug: fields.platformSlug,
              platform_name: fields.platformSlug === 'doubao' ? '豆包' : fields.platformSlug,
              account_mask: fields.accountMask,
              custody_mode:
                fields.custodyMode === 'customer-device' ? 'customer_device' : fields.custodyMode,
              region: fields.region,
              responsible_member_pub_id: fields.responsible,
            },
            headers,
          );
          if (!accountWrite.isCurrent(writeTicket)) return;
          if (registration.kind !== 'ready') {
            setIntegrationState(registration.kind === 'forbidden' ? 'forbidden' : 'failed');
            return;
          }
          accountPubId = applyLiveAccount(registration.data);
          if (!accountPubId) {
            setIntegrationState('failed');
            return;
          }
        }
        if (!accountWrite.isCurrent(writeTicket) || accountPubId !== liveAccountPubIdRef.current) {
          return;
        }
        const authorization = await authorizeCustomerAccount(
          accountPubId,
          {
            scopes: fields.scopes,
            forbidden_actions: ['delete', 'pay', 'direct_message', 'security_settings'],
            regions: [fields.region],
            valid_until: new Date(`${fields.expiresOn}T23:59:59+08:00`).toISOString(),
            responsible_member_pub_id: fields.responsible,
          },
          headers,
        );
        if (!accountWrite.isCurrent(writeTicket) || accountPubId !== liveAccountPubIdRef.current) {
          return;
        }
        if (authorization.kind !== 'ready') {
          setIntegrationState(authorization.kind === 'forbidden' ? 'forbidden' : 'failed');
          return;
        }
        if (!applyLiveAccount(authorization.data)) {
          setIntegrationState('failed');
          return;
        }
        pairingRequestGenerationRef.current += 1;
        setActivePairingPubId('');
        setStage('registered');
        await refreshEvents(accountPubId);
        if (!accountWrite.isCurrent(writeTicket)) return;
        setLiveActionMessage(
          '真实 API 已登记；owner 由当前认证主体在服务端绑定，责任人来自当前租户有效成员。',
        );
        setAuthorizationSaved(true);
      } finally {
        accountWrite.finish(writeTicket);
        setAccountMutationPending(accountWrite.isActive());
      }
    } else {
      setSafeAccount({
        ...safeAccount,
        accountMask: fields.accountMask,
        platformLabel: fields.platformSlug === 'doubao' ? '豆包' : fields.platformSlug,
        ownerLabel: `账号 owner · ${fields.owner} / 责任人 · ${fields.responsible}`,
        custodyMode: fields.custodyMode,
        scopes: fields.scopes,
        expiresLabel: fields.expiresOn,
        regionLabel: fields.region,
      });
      setAuthorizationSaved(true);
    }
  };
  const confirmPairing = async () => {
    if (!live) {
      setStage('paired');
      return;
    }
    const accountPubId = liveAccountPubId;
    const headers = getValidatedIdentityHeaders();
    if (!headers || !accountPubId) {
      setIntegrationState('forbidden');
      return;
    }
    const writeTicket = accountWrite.begin(headers);
    if (!writeTicket) return;
    setAccountMutationPending(true);
    const requestGeneration = ++pairingRequestGenerationRef.current;
    const action = safeAccount.scopes.includes('read') ? 'read' : safeAccount.scopes[0];
    if (!action) {
      setLiveActionMessage('当前没有可用于配对的授权动作。');
      accountWrite.finish(writeTicket);
      setAccountMutationPending(accountWrite.isActive());
      return;
    }
    try {
      const result = await createCustomerPairing(
        accountPubId,
        { allowed_domain: 'doubao.com', action, challenge_type: 'qr' },
        headers,
      );
      if (
        !accountWrite.isCurrent(writeTicket) ||
        requestGeneration !== pairingRequestGenerationRef.current ||
        accountPubId !== liveAccountPubIdRef.current ||
        revocationInFlightRef.current
      ) {
        return;
      }
      if (result.kind !== 'ready') {
        setIntegrationState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      const pairing = projectCustomerPairing(result.data, accountPubId);
      if (!pairing || pairing.stage !== 'waiting') {
        setIntegrationState('failed');
        return;
      }
      setActivePairingPubId(pairing.pubId);
      setLiveActionMessage('真实 API 已创建待处理配对；一次性 payload 仅由受控终端生成。');
      setStage('paired');
      await refreshEvents(accountPubId);
    } finally {
      accountWrite.finish(writeTicket);
      setAccountMutationPending(accountWrite.isActive());
    }
  };
  const refreshPairing = async () => {
    const accountPubId = liveAccountPubId;
    const pairingPubId = livePairingPubId;
    const requestGeneration = ++pairingRequestGenerationRef.current;
    const headers = getValidatedIdentityHeaders();
    if (!headers || !accountPubId || !pairingPubId || revocationInFlightRef.current) {
      setIntegrationState('forbidden');
      return;
    }
    const result = await listCustomerPairings(accountPubId, headers);
    if (
      requestGeneration !== pairingRequestGenerationRef.current ||
      accountPubId !== liveAccountPubIdRef.current ||
      pairingPubId !== livePairingPubIdRef.current ||
      revocationInFlightRef.current
    ) {
      return;
    }
    if (result.kind !== 'ready') {
      setIntegrationState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      return;
    }
    const projection = mergeLifecycleProjection(
      projectCustomerPairingResult(result.data.data, accountPubId, pairingPubId),
      result.data.projection,
    );
    recordProjectionStatus('pairings', projection);
    const next = projection.current?.stage ?? 'failed';
    setStage(next);
    setLiveActionMessage(
      !projection.current
        ? '真实配对响应未通过账号绑定、标识或 DLP 校验；未采用返回状态。'
        : next === 'waiting'
          ? '受控终端仍在处理；客户页面未接收任何挑战秘密。'
          : `真实配对状态已更新为 ${next}。`,
    );
    await refreshEvents(accountPubId);
  };
  const revokeAuthorization = async () => {
    if (live) {
      const accountPubId = liveAccountPubId;
      const headers = getValidatedIdentityHeaders();
      if (!headers || !accountPubId) {
        setIntegrationState('forbidden');
        return;
      }
      const writeTicket = accountWrite.begin(headers);
      if (!writeTicket) return;
      setAccountMutationPending(true);
      revocationInFlightRef.current = true;
      pairingRequestGenerationRef.current += 1;
      eventRequestGenerationRef.current += 1;
      setActivePairingPubId('');
      try {
        const result = await revokeCustomerAccount(accountPubId, headers);
        if (!accountWrite.isCurrent(writeTicket) || accountPubId !== liveAccountPubIdRef.current) {
          return;
        }
        if (result.kind !== 'ready') {
          revocationInFlightRef.current = false;
          setIntegrationState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
          return;
        }
        setLiveActionMessage('真实撤销工作流已受理；回执将在工作流完成后更新。');
        setStage('revoked');
      } finally {
        accountWrite.finish(writeTicket);
        setAccountMutationPending(accountWrite.isActive());
      }
      return;
    }
    setStage('revoked');
  };
  const refreshRevocation = async () => {
    const accountPubId = liveAccountPubId;
    const requestGeneration = ++accountRequestGenerationRef.current;
    const headers = getValidatedIdentityHeaders();
    if (!headers || !accountPubId) {
      setIntegrationState('forbidden');
      return;
    }
    const result = await listCustomerAccounts(headers);
    if (
      requestGeneration !== accountRequestGenerationRef.current ||
      accountPubId !== liveAccountPubIdRef.current
    ) {
      return;
    }
    if (result.kind !== 'ready') {
      setIntegrationState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      return;
    }
    const projection = mergeLifecycleProjection(
      projectCustomerAccountCollection(result.data.data),
      result.data.projection,
    );
    recordProjectionStatus('accounts', projection);
    const current = projection.data.find((item) => item.pubId === accountPubId);
    if (!current || !applyProjectedLiveAccount(current)) {
      setIntegrationState('failed');
      return;
    }
    setLiveActionMessage(
      current.revocationReceipt
        ? '真实撤销回执已由后端删除验证时间确认。'
        : '撤销工作流仍在处理；尚未收到删除验证时间。',
    );
    if (!current.revocationReceipt) {
      await refreshEvents(accountPubId);
    }
  };
  const custodyLabel = {
    server: '服务器托管',
    'customer-device': '客户终端托管',
    hybrid: '混合托管',
  }[safeAccount.custodyMode];
  const scopeLabel = safeAccount.scopes.join(' / ') || '无';
  const pairingReady =
    !live || (Boolean(liveAccountPubId) && safeAccount.scopes.length > 0 && stage !== 'revoked');
  const intervention =
    stage === 'pairing' || stage === 'waiting'
      ? 'waiting'
      : stage === 'paired'
        ? 'paired'
        : stage === 'completed'
          ? 'completed'
          : stage === 'refused'
            ? 'refused'
            : stage === 'timed_out'
              ? 'timed_out'
              : stage === 'failed'
                ? 'failed'
                : 'none';
  const lifecycleProjectionLabels: Record<
    keyof typeof customerAccountLifecycleProjectionLimits,
    string
  > = {
    accounts: '客户账号候选',
    responsibleMembers: '当前租户责任人',
    events: '账号安全事件',
    pairings: '配对状态候选',
  };
  const lifecycleProjectionNotices = (
    Object.keys(customerAccountLifecycleProjectionLimits) as Array<
      keyof typeof customerAccountLifecycleProjectionLimits
    >
  ).flatMap<ProjectionLimitNoticeItem>((key) =>
    lifecycleProjectionStatus[key].total > customerAccountLifecycleProjectionLimits[key]
      ? [
          {
            key: `customer-account-lifecycle-${key}`,
            label: lifecycleProjectionLabels[key],
            total: lifecycleProjectionStatus[key].total,
            shown: lifecycleProjectionStatus[key].shown,
          },
        ]
      : [],
  );
  const invalidLifecycleProjectionLabels = (
    Object.keys(customerAccountLifecycleProjectionLimits) as Array<
      keyof typeof customerAccountLifecycleProjectionLimits
    >
  )
    .filter((key) => lifecycleProjectionStatus[key].invalid)
    .map((key) => lifecycleProjectionLabels[key]);
  if (live && integrationState === 'loading') {
    return (
      <section className="panel">
        <h2>平台账号与授权</h2>
        <StatePanel state="loading" />
      </section>
    );
  }
  if (live && integrationState === 'forbidden') {
    return (
      <section className="panel">
        <h2>平台账号与授权</h2>
        <StatePanel state="forbidden" />
      </section>
    );
  }
  return (
    <>
      <section className="panel">
        <h2>平台账号与授权</h2>
        <p className="panel-subtitle">
          账号所有权、最小动作范围、期限、地域、责任人与撤销权均可审计。
        </p>
        <div className="button-row">
          <Badge tone={live ? 'positive' : 'warning'}>
            {live ? '客户安全投影 · 真实 API' : 'Contract fixture'}
          </Badge>
        </div>
        {live && lifecycleProjectionNotices.length ? (
          <ProjectionLimitNotice
            items={lifecycleProjectionNotices}
            detail="账号生命周期集合仅在既定上限内投影；当前页面不会静默采用越界行。"
          />
        ) : null}
        {live && invalidLifecycleProjectionLabels.length ? (
          <div className="confirmation projection-limit-notice" role="alert">
            <Badge tone="warning">安全投影不完整</Badge>
            <span>
              {invalidLifecycleProjectionLabels.join('、')}
              包含重复标识、跨账号、乱序、未知状态或未通过 DLP 校验的记录。
            </span>
          </div>
        ) : null}
        {live && integrationState === 'failed' ? (
          <StatePanel state="failed" onRetry={retry} />
        ) : null}
        {live && integrationState === 'empty' ? (
          <StatePanel state="empty" />
        ) : (
          <AccountSummary account={safeAccount} />
        )}
        <form className="form-grid" onSubmit={handleSubmit(saveAuthorization)} noValidate>
          <Field id="account-platform" label="目标平台" error={errors.platformSlug}>
            <select id="account-platform" {...register('platformSlug')}>
              <option value="doubao">豆包</option>
            </select>
          </Field>
          <Field id="account-mask" label="账号掩码" error={errors.accountMask}>
            <input
              id="account-mask"
              autoComplete="off"
              placeholder={live ? '例如：尾号 · 4821' : undefined}
              {...register('accountMask')}
            />
          </Field>
          <Field id="account-owner" label="账号 owner" error={errors.owner}>
            <input
              id="account-owner"
              autoComplete="off"
              readOnly={live}
              aria-describedby={live ? 'account-owner-help' : undefined}
              {...register('owner')}
            />
            {live ? (
              <span className="field-hint" id="account-owner-help">
                由当前认证主体的安全投影在服务端绑定，不接受自由文本覆盖。
              </span>
            ) : null}
          </Field>
          <Field id="account-responsible" label="运营责任人" error={errors.responsible}>
            {live ? (
              <select id="account-responsible" {...register('responsible')}>
                <option value="">请选择当前租户成员</option>
                {responsibleMembers.map((member) => (
                  <option value={member.user_pub_id} key={member.user_pub_id}>
                    {member.label} · {member.role}
                  </option>
                ))}
              </select>
            ) : (
              <input id="account-responsible" autoComplete="off" {...register('responsible')} />
            )}
          </Field>
          <Field id="account-custody" label="托管模式" error={errors.custodyMode}>
            <select id="account-custody" {...register('custodyMode')}>
              <option value="customer-device">客户终端托管</option>
              <option value="hybrid">混合托管</option>
              <option value="server">服务器托管</option>
            </select>
          </Field>
          <Field id="account-expiry" label="授权到期日" error={errors.expiresOn}>
            <input id="account-expiry" type="date" {...register('expiresOn')} />
          </Field>
          <Field id="account-region" label="授权地域" error={errors.region}>
            <input id="account-region" autoComplete="off" {...register('region')} />
          </Field>
          <fieldset className="field">
            <legend>允许动作</legend>
            <div className="scope-row">
              {(['read', 'query', 'draft', 'publish'] as const).map((scope) => (
                <label className="checkbox-line" key={scope}>
                  <input type="checkbox" value={scope} {...register('scopes')} />
                  {scope}
                </label>
              ))}
            </div>
            {errors.scopes ? <span className="field-error">{errors.scopes.message}</span> : null}
          </fieldset>
          <div className="form-actions">
            <button
              className="button"
              type="submit"
              disabled={live && (!isValid || isSubmitting || accountMutationBusy)}
            >
              登记授权
            </button>
            {accountMutationBusy ? <span role="status">账号治理写入处理中…</span> : null}
            {authorizationSaved ? (
              <Toast>
                授权登记已更新；配对范围将采用当前安全投影。
                {liveActionMessage ? ` ${liveActionMessage}` : ''}
              </Toast>
            ) : null}
          </div>
        </form>
      </section>
      <section className="panel" aria-labelledby="pairing-title">
        <div className="account-head">
          <div>
            <span className="overline">Customer terminal</span>
            <h2 id="pairing-title">客户终端安全配对</h2>
          </div>
          <InterventionStatus value={intervention} />
        </div>
        <ol className="flow-steps" aria-label="配对进度" tabIndex={0}>
          {['登记授权', '选择托管', '安全配对', '原生验证', '健康确认'].map((label, index) => (
            <li
              key={label}
              aria-current={
                (stage === 'registered' && index === (pairingReady ? 1 : 0)) ||
                (['pairing', 'paired'].includes(stage) && index === 2) ||
                (stage === 'waiting' && index === 3) ||
                (stage === 'completed' && index === 4)
                  ? 'step'
                  : undefined
              }
            >
              {label}
            </li>
          ))}
        </ol>
        {stage === 'registered' && !pairingReady ? (
          <div className="pairing-confirm">
            <StatePanel state="empty" />
            <p className="panel-subtitle">
              请先登记账号并获得至少一个允许动作。客户终端不会从空账号列表或表单默认值推断托管模式、
              授权范围或配对资格。
            </p>
          </div>
        ) : null}
        {stage === 'registered' && pairingReady ? (
          <div className="pairing-body">
            <div>
              <h3>
                {custodyLabel} · {scopeLabel}
              </h3>
              <p>
                敏感验证留在客户终端，日常获授权查询可由隔离 Runner 执行。配对令牌单次使用，10
                分钟过期。
              </p>
            </div>
            <button
              className="button"
              disabled={accountMutationBusy}
              onClick={() => setStage('pairing')}
            >
              创建一次性配对
            </button>
          </div>
        ) : null}
        {stage === 'pairing' ? (
          <div className="pairing-confirm">
            <h3>请二次确认本次任务</h3>
            <dl className="definition-grid">
              <div>
                <dt>账号掩码</dt>
                <dd>{safeAccount.accountMask}</dd>
              </div>
              <div>
                <dt>目标平台</dt>
                <dd>{safeAccount.platformLabel}</dd>
              </div>
              <div>
                <dt>允许动作</dt>
                <dd>{scopeLabel}</dd>
              </div>
              <div>
                <dt>允许域名</dt>
                <dd>doubao.com</dd>
              </div>
              <div>
                <dt>到期时间</dt>
                <dd>10 分钟后</dd>
              </div>
              <div>
                <dt>目标地域</dt>
                <dd>{safeAccount.regionLabel}</dd>
              </div>
              <div>
                <dt>秘密传输</dt>
                <dd>禁止</dd>
              </div>
            </dl>
            <p className="security-note">
              请勿在聊天或普通表单粘贴验证码、Cookie 或
              token。后续操作只在目标平台原生页面或受控终端完成。
            </p>
            <div className="button-row">
              <button
                className="button button-secondary"
                disabled={accountMutationBusy}
                onClick={() => setStage('refused')}
              >
                拒绝
              </button>
              <button
                className="button"
                disabled={accountMutationBusy}
                onClick={() => void confirmPairing()}
              >
                {live ? '确认并创建配对请求' : '确认并进入配对演示'}
              </button>
            </div>
          </div>
        ) : null}
        {stage === 'paired' ? (
          <div className="pairing-body">
            <div className="pairing-code">
              <div
                className="safe-pairing-qr"
                role="img"
                data-visual-evidence="payload-free"
                aria-label="一次性安全配对二维码占位；不可扫描，实际 payload 仅在受控终端通道中提供"
              >
                <span aria-hidden="true" />
              </div>
              <div>
                <Badge tone="info">{live ? '真实配对待受控终端处理' : '契约演示 · 不可扫描'}</Badge>
                <h3>{live ? '在已登记受控终端等待任务' : '配对交接视觉演示'}</h3>
                {live ? (
                  <p>
                    当前 Customer API 不向 Web
                    页面提供可打开的一次性链接或二维码。请在已登记受控终端
                    等待限域任务并刷新状态；页面不会伪造已可扫描能力。
                  </p>
                ) : (
                  <p>
                    终端通道仅允许 {safeAccount.platformLabel}、doubao.com 和 {scopeLabel}
                    动作。二维码视觉不包含可提取的配对 payload；真实内容只进入受控终端通道。
                  </p>
                )}
              </div>
            </div>
            <div className="button-row">
              {live ? (
                <button
                  className="button button-secondary"
                  disabled={accountMutationBusy}
                  onClick={() => void refreshPairing()}
                >
                  刷新真实配对状态
                </button>
              ) : (
                <button className="button button-secondary" onClick={() => setStage('timed_out')}>
                  模拟超时
                </button>
              )}
              {!live ? (
                <button className="button" onClick={() => setStage('waiting')}>
                  终端已连接
                </button>
              ) : null}
            </div>
          </div>
        ) : null}
        {stage === 'waiting' ? (
          <div className="pairing-confirm">
            <Badge tone="warning">等待目标平台</Badge>
            <h3>请在豆包原生页面完成验证</h3>
            <p>
              支持 OTP、官方 App 扫码、Push MFA、passkey、人脸/活体跳转和图形
              challenge。平台页面完成后只返回成功、失败或过期状态，不上传验证码或生物材料。
            </p>
            <div className="button-row">
              {live ? (
                <span className="security-note">
                  拒绝和挑战输入只在受控终端完成；客户页面通过刷新读取真实结果。
                </span>
              ) : (
                <button className="button button-secondary" onClick={() => setStage('refused')}>
                  拒绝本次操作
                </button>
              )}
              <button
                className="button"
                disabled={accountMutationBusy}
                onClick={() => (live ? void refreshPairing() : setStage('completed'))}
              >
                {live ? '刷新真实配对状态' : '模拟平台确认完成'}
              </button>
            </div>
          </div>
        ) : null}
        {stage === 'completed' ? (
          <div className="pairing-body">
            <div>
              <Badge tone="positive">身份探针通过</Badge>
              <h3>配对与验证已完成</h3>
              <p>
                账号 opaque identity 匹配；本次仅验证登录与 read，准入保持 read_verified。授权范围为{' '}
                {scopeLabel}，draft/publish 不会因登记授权被描述为已完成 live 验证。
              </p>
            </div>
            <button
              className="button button-secondary"
              disabled={accountMutationBusy}
              onClick={() => void revokeAuthorization()}
            >
              撤销授权
            </button>
          </div>
        ) : null}
        {stage === 'refused' || stage === 'timed_out' || stage === 'failed' ? (
          <div className="pairing-body">
            <div>
              <InterventionStatus value={stage} />
              <h3>
                {stage === 'refused'
                  ? '本次配对已拒绝'
                  : stage === 'timed_out'
                    ? '一次性配对已超时'
                    : '本次原生验证失败'}
              </h3>
              <p>
                {stage === 'failed'
                  ? '未提升任何准入等级；可以在确认目标平台与授权范围后重新开始。'
                  : live && stage === 'refused' && !livePairingPubId
                    ? '已取消本次确认；未创建配对请求或一次性通道，现有授权与会话未改变。'
                    : '通道和一次性令牌已销毁，没有改变现有授权或会话。'}
              </p>
            </div>
            <button className="button" onClick={() => setStage('registered')}>
              重新开始
            </button>
          </div>
        ) : null}
        {stage === 'revoked' && !live ? (
          <RevocationReceipt
            receipt={{
              receiptId: 'rvr_01K0SAFE9Y',
              revokedAtLabel: '刚刚',
              actorLabel: '客户管理员 · 林澄',
              leasesStopped: true,
              sessionsClosed: true,
              secretCopiesPurged: true,
            }}
          />
        ) : null}
        {stage === 'revoked' && live && liveRevocationReceipt ? (
          <RevocationReceipt receipt={liveRevocationReceipt} />
        ) : null}
        {stage === 'revoked' && live && !liveRevocationReceipt ? (
          <div className="pairing-body">
            <div>
              <Badge tone="warning">撤销工作流处理中</Badge>
              <h3>等待真实撤销回执</h3>
              <p>新租约已停止受理；只有后端完成会话关闭和秘密副本删除验证后才展示回执。</p>
            </div>
            <button
              className="button button-secondary"
              disabled={accountMutationBusy}
              onClick={() => void refreshRevocation()}
            >
              刷新撤销状态
            </button>
          </div>
        ) : null}
      </section>
      <section className="panel">
        <h2>账号安全事件</h2>
        <p className="panel-subtitle">
          {live
            ? '来自客户安全事件投影；不包含账号秘密。'
            : 'Contract fixture：真实 API 会话可用后替换。'}
        </p>
        {live && eventState === 'loading' ? (
          <StatePanel state="loading" />
        ) : live && eventState === 'forbidden' ? (
          <StatePanel state="forbidden" />
        ) : live && eventState === 'failed' ? (
          <StatePanel
            state="failed"
            {...(liveAccountPubId ? { onRetry: () => void refreshEvents(liveAccountPubId) } : {})}
          />
        ) : safeEvents.length ? (
          <ol className="workflow-list" aria-label="账号安全事件">
            {safeEvents.map((event) => (
              <li key={event.id}>
                <strong>
                  {event.type.split(/(?<=[._-])/u).map((part, index) => (
                    <span key={`${part}-${index}`}>
                      {part}
                      <wbr />
                    </span>
                  ))}
                </strong>{' '}
                · {event.occurredAt}
              </li>
            ))}
          </ol>
        ) : (
          <StatePanel state="empty" />
        )}
      </section>
      <div className="card-grid">
        <article className="action-card">
          <span className="overline">安全配对</span>
          <h3>一次性、限域、限动作</h3>
          <p>
            核对账号掩码、目标平台、允许动作、允许域名和到期时间后，在目标平台原生页面完成验证。
          </p>
        </article>
        <article className="action-card">
          <span className="overline">禁止动作</span>
          <h3>默认拒绝高风险操作</h3>
          <p>支付、删除、私信、修改安全设置、绑定手机和未审批发布均不在授权范围。</p>
          <Badge tone="warning">最小权限</Badge>
        </article>
        <article className="action-card">
          <span className="overline">撤销权</span>
          <h3>随时终止托管</h3>
          <p>撤销会停止新租约、关闭活动会话并生成不含秘密的撤销回执。</p>
          <button className="button button-secondary" onClick={() => setShowRevocationGuide(true)}>
            查看撤销流程
          </button>
        </article>
      </div>
      {showRevocationGuide ? (
        <Dialog
          title="客户撤销权与执行顺序"
          eyebrow="Revocation guide"
          closeLabel="关闭撤销流程"
          onClose={() => setShowRevocationGuide(false)}
        >
          <ol className="workflow-list">
            <li>立即拒绝新租约与新动作</li>
            <li>关闭活动会话并终止待处理配对</li>
            <li>删除托管秘密副本；客户设备上的平台凭据仍由客户在原生页面管理</li>
            <li>生成不含 Cookie、token、profile 或生物材料的撤销回执</li>
          </ol>
          <p className="security-note">撤销不要求客户再次提交 OTP、Cookie 或 token。</p>
        </Dialog>
      ) : null}
    </>
  );
}

const profileSchema = z.object({
  companyName: z
    .string()
    .trim()
    .min(2, '请输入至少 2 个字的企业名称')
    .max(80)
    .refine(noClientSecret, noClientSecretMessage),
  contactRole: z
    .string()
    .trim()
    .min(2, '请填写联系人角色')
    .max(40)
    .refine(noClientSecret, noClientSecretMessage),
  audience: z
    .string()
    .trim()
    .min(10, '请用至少 10 个字描述目标客户')
    .max(500)
    .refine(noClientSecret, noClientSecretMessage),
  publicStatement: z
    .string()
    .trim()
    .min(10, '请填写可公开核验的企业说明')
    .max(800)
    .refine(noClientSecret, noClientSecretMessage),
  truthConfirmed: z.boolean().refine((value) => value, '提交前必须确认资料真实性'),
});
type ProfileFormValue = z.infer<typeof profileSchema>;

function ProfileWorkspace() {
  const experience = useOptionalExperienceContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const [retryKey, retry] = useLocalRetry();
  const [savedAt, setSavedAt] = useState('尚未保存');
  const [profileWritePending, setProfileWritePending] = useState(false);
  const profileWriteContext = createStructuredClientScopeKey([optionalExperienceScope(experience)]);
  const profileWrite = useCustomerMutationGuard(profileWriteContext);
  const [versions, setVersions] = useState<ClientProfileView[]>([]);
  const [historyProjection, setHistoryProjection] = useState({
    total: 0,
    shown: 0,
    invalid: false,
  });
  const [liveNextCursor, setLiveNextCursor] = useState('');
  const cursorByPage = useRef(new Map<number, string>());
  const [liveState, setLiveState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<ProfileFormValue>({
    resolver: zodResolver(profileSchema),
    defaultValues: {
      companyName: '云岫智能科技有限公司',
      contactRole: '品牌负责人',
      audience: '需要安全部署企业知识库与智能问答的制造业数字化团队',
      publicStatement: '云岫智能提供企业知识检索、问答与治理服务，支持私有化部署。',
      truthConfirmed: false,
    },
  });
  const rawPage = searchParams.get('declaration_page') ?? '';
  const parsedPage = /^[1-9]\d{0,2}$/.test(rawPage) ? Number(rawPage) : 1;
  const rawCursor = searchParams.get('declaration_cursor') ?? '';
  const cursorMatch = /^rev_([1-9]\d{0,8})$/.exec(rawCursor);
  const declarationCursor = cursorMatch && !containsClientSecret(rawCursor) ? rawCursor : '';
  const requestedPage =
    experience?.source === 'live' && parsedPage > 1 && !declarationCursor ? 1 : parsedPage;
  const profileReadScope = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    String(retryKey),
    String(requestedPage),
    declarationCursor,
  ]);
  const [liveResultScope, setLiveResultScope] = useState(
    experience?.source === 'live' ? '' : profileReadScope,
  );
  useEffect(() => {
    setProfileWritePending(false);
    setSavedAt('尚未保存');
  }, [profileWriteContext]);
  useEffect(() => {
    const canonicalPage =
      (requestedPage === 1 && rawPage === '') || rawPage === String(requestedPage);
    if (rawCursor === declarationCursor && canonicalPage) return;
    const next = new URLSearchParams(searchParams);
    if (declarationCursor) next.set('declaration_cursor', declarationCursor);
    else next.delete('declaration_cursor');
    if (requestedPage > 1) next.set('declaration_page', String(requestedPage));
    else next.delete('declaration_page');
    void setSearchParams(next, { replace: true });
  }, [declarationCursor, rawCursor, rawPage, requestedPage, searchParams, setSearchParams]);
  useEffect(() => {
    if (experience?.source !== 'live' || !experience.projectPubId) {
      setLiveResultScope(profileReadScope);
      setLiveState('fixture');
      setHistoryProjection({ total: 0, shown: 0, invalid: false });
      return;
    }
    let cancelled = false;
    const commitLiveState = (nextState: 'ready' | 'failed' | 'forbidden') => {
      if (cancelled) return;
      setLiveResultScope(profileReadScope);
      setLiveState(nextState);
    };
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setVersions([]);
      setHistoryProjection({ total: 0, shown: 0, invalid: false });
      commitLiveState('forbidden');
      return;
    }
    setVersions([]);
    setHistoryProjection({ total: 0, shown: 0, invalid: false });
    setLiveState('loading');
    const cursorRevision = declarationCursor ? Number(declarationCursor.slice(4)) : undefined;
    void Promise.all([
      listClientProfileVersions(experience.projectPubId, headers, {
        ...(cursorRevision ? { cursor: cursorRevision } : {}),
        limit: customerGovernanceHistoryLimit,
      }),
      listClientProfileVersions(experience.projectPubId, headers, { limit: 1 }),
    ]).then(([result, latestResult]) => {
      if (cancelled) return;
      if (result.kind !== 'ready' || latestResult.kind !== 'ready') {
        setVersions([]);
        commitLiveState(
          result.kind === 'forbidden' || latestResult.kind === 'forbidden' ? 'forbidden' : 'failed',
        );
        return;
      }
      const projectedPage = projectClientProfilePage(
        result.data,
        experience.projectPubId,
        cursorRevision,
      );
      const projectedLatest = projectClientProfilePage(
        latestResult.data,
        experience.projectPubId,
        undefined,
        1,
      );
      setVersions(projectedPage.data);
      setHistoryProjection({
        total: projectedPage.total,
        shown: projectedPage.data.length,
        invalid: projectedPage.invalid || projectedLatest.invalid || projectedLatest.total > 1,
      });
      const latest =
        !projectedLatest.invalid && projectedLatest.total <= 1
          ? projectedLatest.data[0]
          : undefined;
      if (latest) {
        reset({
          companyName: latest.company_name,
          contactRole: latest.contact_role,
          audience: latest.audience,
          publicStatement: latest.public_statement,
          truthConfirmed: false,
        });
      }
      const safeNextCursor = projectedPage.nextCursor ? `rev_${projectedPage.nextCursor}` : '';
      setLiveNextCursor(safeNextCursor);
      if (safeNextCursor) cursorByPage.current.set(requestedPage + 1, safeNextCursor);
      commitLiveState('ready');
    });
    return () => {
      cancelled = true;
    };
  }, [declarationCursor, experience, profileReadScope, requestedPage, reset, retryKey]);
  const submit = handleSubmit(async (value) => {
    if (experience?.source === 'live' && experience.projectPubId) {
      const headers = getValidatedIdentityHeaders();
      if (!headers) {
        setLiveState('forbidden');
        return;
      }
      const projectPubId = experience.projectPubId;
      const writeTicket = profileWrite.begin(headers);
      if (!writeTicket) return;
      setProfileWritePending(true);
      const result = await createClientProfileVersion(
        projectPubId,
        {
          company_name: value.companyName,
          contact_role: value.contactRole,
          audience: value.audience,
          public_statement: value.publicStatement,
          truth_confirmed: value.truthConfirmed,
        },
        headers,
        `customer-profile-${crypto.randomUUID()}`,
      );
      if (!profileWrite.finish(writeTicket)) return;
      setProfileWritePending(false);
      if (result.kind !== 'ready') {
        setLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      const [projected] = projectClientProfileViews([result.data], projectPubId);
      if (!projected) {
        setLiveState('failed');
        return;
      }
      setVersions([projected]);
      setHistoryProjection({ total: 1, shown: 1, invalid: false });
      const next = new URLSearchParams(searchParams);
      next.delete('declaration_page');
      next.delete('declaration_cursor');
      cursorByPage.current.clear();
      void setSearchParams(next);
      retry();
      reset({ ...value, truthConfirmed: false });
      setSavedAt(`客户声明 v${projected.revision} · 已保存`);
      return;
    }
    setSavedAt(
      `客户声明 v3 · ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`,
    );
  });
  const effectiveLiveState =
    experience?.source === 'live' && liveResultScope !== profileReadScope ? 'loading' : liveState;
  if (effectiveLiveState === 'loading') return <StatePanel state="loading" />;
  if (effectiveLiveState === 'failed') return <StatePanel state="failed" onRetry={retry} />;
  if (effectiveLiveState === 'forbidden') return <StatePanel state="forbidden" />;
  const pageCount = Math.max(1, requestedPage + (liveNextCursor ? 1 : 0));
  const changePage = (nextPage: number) => {
    const next = new URLSearchParams(searchParams);
    const cursor =
      nextPage === requestedPage + 1 ? liveNextCursor : (cursorByPage.current.get(nextPage) ?? '');
    if (nextPage > 1 && cursor) {
      next.set('declaration_page', String(nextPage));
      next.set('declaration_cursor', cursor);
    } else {
      next.delete('declaration_page');
      next.delete('declaration_cursor');
    }
    void setSearchParams(next);
  };
  return (
    <div className="workspace-grid">
      <form className="panel form-panel" onSubmit={(event) => void submit(event)} noValidate>
        <span className="overline">Client declaration</span>
        <h2>甲方资料</h2>
        <p className="panel-subtitle">
          客户声明、AI 草稿和 GEO 规范化结果分别保存，任何一方都不能静默覆盖另一方。
        </p>
        <div className="form-grid">
          <Field id="companyName" label="企业全称" error={errors.companyName}>
            <input
              id="companyName"
              aria-invalid={Boolean(errors.companyName)}
              aria-describedby={errors.companyName ? 'companyName-error' : undefined}
              {...register('companyName')}
            />
          </Field>
          <Field id="contactRole" label="责任人角色" error={errors.contactRole}>
            <input id="contactRole" {...register('contactRole')} />
          </Field>
          <Field id="audience" label="目标客户" error={errors.audience}>
            <textarea id="audience" rows={4} {...register('audience')} />
          </Field>
          <Field
            id="publicStatement"
            label="可公开核验说明"
            error={errors.publicStatement}
            hint="仅填写可由官网、资质或公开材料证明的事实。"
          >
            <textarea id="publicStatement" rows={4} {...register('publicStatement')} />
          </Field>
        </div>
        <label className="check-field">
          <input type="checkbox" {...register('truthConfirmed')} />
          我确认上述客户声明真实、可核验，并理解修改会生成新版本。
        </label>
        {errors.truthConfirmed ? (
          <span className="field-error" role="alert">
            {errors.truthConfirmed.message}
          </span>
        ) : null}
        <div className="form-actions">
          <span aria-live="polite">
            {savedAt !== '尚未保存' ? savedAt : isDirty ? '有未保存修改' : savedAt}
          </span>
          <button className="button" disabled={isSubmitting || profileWritePending}>
            {isSubmitting || profileWritePending ? '正在提交' : '保存并生成版本'}
          </button>
        </div>
      </form>
      <aside className="panel timeline-panel">
        <h2>字段历史</h2>
        {experience?.source === 'live' &&
        historyProjection.total > customerGovernanceHistoryLimit ? (
          <ProjectionLimitNotice
            items={[
              {
                key: 'customer-profile-history',
                label: '客户声明历史',
                total: historyProjection.total,
                shown: historyProjection.shown,
              },
            ]}
          />
        ) : null}
        {experience?.source === 'live' && historyProjection.invalid ? (
          <div className="confirmation projection-limit-notice" role="alert">
            <Badge tone="warning">安全投影不完整</Badge>
            <span>客户声明历史包含跨项目、乱序、游标不一致或未通过 DLP 校验的版本。</span>
          </div>
        ) : null}
        <ol className="timeline">
          {experience?.source === 'live' ? (
            versions.length ? (
              versions.map((version) => (
                <li key={version.pub_id}>
                  <strong>客户声明 v{version.revision}</strong>
                  <span>{new Date(version.created_at).toLocaleString('zh-CN')}</span>
                </li>
              ))
            ) : (
              <li>
                <strong>尚无客户声明</strong>
                <span>首次保存后生成 v1</span>
              </li>
            )
          ) : (
            <>
              <li>
                <strong>客户声明 v2</strong>
                <span>林澄 · 今天 09:18</span>
              </li>
              <li>
                <strong>AI 调研草稿</strong>
                <span>仅建议，未覆盖客户值</span>
              </li>
              <li>
                <strong>客户声明 v1</strong>
                <span>项目创建时</span>
              </li>
            </>
          )}
        </ol>
        {experience?.source === 'live' ? (
          <Pagination
            label="客户声明历史分页"
            page={requestedPage}
            pageCount={pageCount}
            onPageChange={changePage}
          />
        ) : null}
      </aside>
    </div>
  );
}

const assetSchema = z.object({
  brandName: z
    .string()
    .trim()
    .min(2, '请输入品牌名称')
    .max(60)
    .refine(noClientSecret, noClientSecretMessage),
  website: z
    .url('请输入完整 HTTPS 官网地址')
    .refine((value) => value.startsWith('https://'), '官网必须使用 HTTPS')
    .refine(noClientSecret, noClientSecretMessage),
  productName: z
    .string()
    .trim()
    .min(2, '请输入产品或服务名称')
    .max(80)
    .refine(noClientSecret, noClientSecretMessage),
  competitor: z
    .string()
    .trim()
    .min(2, '请输入客户确认的竞品')
    .max(80)
    .refine(noClientSecret, noClientSecretMessage),
  forbiddenClaim: z
    .string()
    .trim()
    .min(2, '请填写至少 2 个字的禁止表述')
    .max(300)
    .refine(noClientSecret, noClientSecretMessage),
  truthConfirmed: z.boolean().refine((value) => value, '提交前必须确认资产真实性'),
});
type AssetFormValue = z.infer<typeof assetSchema>;

function AssetsWorkspace() {
  const experience = useOptionalExperienceContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const [retryKey, retry] = useLocalRetry();
  const [assetWritePending, setAssetWritePending] = useState(false);
  const assetWriteContext = createStructuredClientScopeKey([optionalExperienceScope(experience)]);
  const assetWrite = useCustomerMutationGuard(assetWriteContext);
  const [brands, setBrands] = useState([
    { brand: '云岫 AI', product: '企业知识中枢', competitor: '星河智库' },
  ]);
  const [liveCatalog, setLiveCatalog] = useState<LiveCatalogItem[]>([]);
  const [catalogProjection, setCatalogProjection] = useState({
    total: 0,
    shown: 0,
    invalid: false,
  });
  const [confirmations, setConfirmations] = useState<AssetConfirmationView[]>([]);
  const [latestConfirmation, setLatestConfirmation] = useState<AssetConfirmationView | null>(null);
  const [historyProjection, setHistoryProjection] = useState({
    total: 0,
    shown: 0,
    invalid: false,
  });
  const [liveNextCursor, setLiveNextCursor] = useState('');
  const cursorByPage = useRef(new Map<number, string>());
  const [liveState, setLiveState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<AssetFormValue>({
    resolver: zodResolver(assetSchema),
    defaultValues: {
      brandName: '',
      website: 'https://',
      productName: '',
      competitor: '',
      forbiddenClaim: '',
      truthConfirmed: false,
    },
  });
  const rawPage = searchParams.get('asset_history_page') ?? '';
  const parsedPage = /^[1-9]\d{0,2}$/.test(rawPage) ? Number(rawPage) : 1;
  const rawCursor = searchParams.get('asset_history_cursor') ?? '';
  const cursorMatch = /^rev_([1-9]\d{0,8})$/.exec(rawCursor);
  const assetHistoryCursor = cursorMatch && !containsClientSecret(rawCursor) ? rawCursor : '';
  const requestedPage =
    experience?.source === 'live' && parsedPage > 1 && !assetHistoryCursor ? 1 : parsedPage;
  const assetHistoryReadScope = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    String(retryKey),
    String(requestedPage),
    assetHistoryCursor,
  ]);
  const [liveResultScope, setLiveResultScope] = useState(
    experience?.source === 'live' ? '' : assetHistoryReadScope,
  );
  useEffect(() => {
    setAssetWritePending(false);
  }, [assetWriteContext]);
  useEffect(() => {
    const canonicalPage =
      (requestedPage === 1 && rawPage === '') || rawPage === String(requestedPage);
    if (rawCursor === assetHistoryCursor && canonicalPage) return;
    const next = new URLSearchParams(searchParams);
    if (assetHistoryCursor) next.set('asset_history_cursor', assetHistoryCursor);
    else next.delete('asset_history_cursor');
    if (requestedPage > 1) next.set('asset_history_page', String(requestedPage));
    else next.delete('asset_history_page');
    void setSearchParams(next, { replace: true });
  }, [assetHistoryCursor, rawCursor, rawPage, requestedPage, searchParams, setSearchParams]);
  const submit = handleSubmit(async (value) => {
    if (experience?.source === 'live' && experience.projectPubId) {
      const headers = getValidatedIdentityHeaders();
      if (!headers) {
        setLiveState('forbidden');
        return;
      }
      const projectPubId = experience.projectPubId;
      const writeTicket = assetWrite.begin(headers);
      if (!writeTicket) return;
      setAssetWritePending(true);
      const result = await createAssetConfirmation(
        projectPubId,
        {
          brand_name: value.brandName,
          website: value.website,
          product_name: value.productName,
          competitor_name: value.competitor,
          prohibited_claim: value.forbiddenClaim,
          truth_confirmed: value.truthConfirmed,
        },
        headers,
        `customer-assets-${crypto.randomUUID()}`,
      );
      if (!assetWrite.finish(writeTicket)) return;
      setAssetWritePending(false);
      if (result.kind !== 'ready') {
        setLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      const [projected] = projectAssetConfirmationViews([result.data], projectPubId);
      if (!projected) {
        setLiveState('failed');
        return;
      }
      setLatestConfirmation(projected);
      setConfirmations([projected]);
      setHistoryProjection({ total: 1, shown: 1, invalid: false });
      const next = new URLSearchParams(searchParams);
      next.delete('asset_history_page');
      next.delete('asset_history_cursor');
      cursorByPage.current.clear();
      void setSearchParams(next);
      retry();
      reset({
        brandName: '',
        website: 'https://',
        productName: '',
        competitor: '',
        forbiddenClaim: '',
        truthConfirmed: false,
      });
      return;
    }
    setBrands((current) => [
      ...current,
      { brand: value.brandName, product: value.productName, competitor: value.competitor },
    ]);
    reset({
      brandName: '',
      website: 'https://',
      productName: '',
      competitor: '',
      forbiddenClaim: '',
      truthConfirmed: false,
    });
  });
  useEffect(() => {
    if (experience?.source !== 'live' || !experience.projectPubId) {
      setLiveResultScope(assetHistoryReadScope);
      setLiveState('fixture');
      setCatalogProjection({ total: 0, shown: 0, invalid: false });
      setHistoryProjection({ total: 0, shown: 0, invalid: false });
      return;
    }
    let cancelled = false;
    const commitLiveState = (nextState: 'ready' | 'failed' | 'forbidden') => {
      if (cancelled) return;
      setLiveResultScope(assetHistoryReadScope);
      setLiveState(nextState);
    };
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveCatalog([]);
      setCatalogProjection({ total: 0, shown: 0, invalid: false });
      setConfirmations([]);
      setLatestConfirmation(null);
      setHistoryProjection({ total: 0, shown: 0, invalid: false });
      commitLiveState('forbidden');
      return;
    }
    setLiveCatalog([]);
    setCatalogProjection({ total: 0, shown: 0, invalid: false });
    setConfirmations([]);
    setHistoryProjection({ total: 0, shown: 0, invalid: false });
    setLiveState('loading');
    const cursorRevision = assetHistoryCursor ? Number(assetHistoryCursor.slice(4)) : undefined;
    void Promise.all([
      listProjectResources(experience.projectPubId, 'brands', headers),
      listProjectResources(experience.projectPubId, 'competitors', headers),
      listAssetConfirmations(experience.projectPubId, headers, {
        ...(cursorRevision ? { cursor: cursorRevision } : {}),
        limit: customerGovernanceHistoryLimit,
      }),
      listAssetConfirmations(experience.projectPubId, headers, { limit: 1 }),
    ]).then(([brandResult, competitorResult, confirmationResult, latestResult]) => {
      if (cancelled) return;
      if (
        brandResult.kind !== 'ready' ||
        competitorResult.kind !== 'ready' ||
        confirmationResult.kind !== 'ready' ||
        latestResult.kind !== 'ready'
      ) {
        setLiveCatalog([]);
        setCatalogProjection({ total: 0, shown: 0, invalid: false });
        commitLiveState(
          brandResult.kind === 'forbidden' ||
            competitorResult.kind === 'forbidden' ||
            confirmationResult.kind === 'forbidden' ||
            latestResult.kind === 'forbidden'
            ? 'forbidden'
            : 'failed',
        );
        return;
      }
      const projectedPage = projectAssetConfirmationPage(
        confirmationResult.data,
        experience.projectPubId,
        cursorRevision,
      );
      const projectedLatest = projectAssetConfirmationPage(
        latestResult.data,
        experience.projectPubId,
        undefined,
        1,
      );
      setConfirmations(projectedPage.data);
      setLatestConfirmation(
        !projectedLatest.invalid && projectedLatest.total <= 1
          ? (projectedLatest.data[0] ?? null)
          : null,
      );
      setHistoryProjection({
        total: projectedPage.total,
        shown: projectedPage.data.length,
        invalid: projectedPage.invalid || projectedLatest.invalid || projectedLatest.total > 1,
      });
      const safeNextCursor = projectedPage.nextCursor ? `rev_${projectedPage.nextCursor}` : '';
      setLiveNextCursor(safeNextCursor);
      if (safeNextCursor) cursorByPage.current.set(requestedPage + 1, safeNextCursor);
      const catalog = [
        ...projectCatalogItems(brandResult.data.data, 'brands'),
        ...projectCatalogItems(competitorResult.data.data, 'competitors'),
      ];
      const upstreamShown =
        brandResult.data.projection.shown + competitorResult.data.projection.shown;
      setLiveCatalog(catalog);
      setCatalogProjection({
        total: brandResult.data.projection.total + competitorResult.data.projection.total,
        shown: catalog.length,
        invalid:
          brandResult.data.projection.invalid ||
          competitorResult.data.projection.invalid ||
          catalog.length !== upstreamShown,
      });
      commitLiveState('ready');
    });
    return () => {
      cancelled = true;
    };
  }, [assetHistoryCursor, assetHistoryReadScope, experience, requestedPage, retryKey]);
  const effectiveLiveState =
    experience?.source === 'live' && liveResultScope !== assetHistoryReadScope
      ? 'loading'
      : liveState;
  if (effectiveLiveState === 'loading') return <StatePanel state="loading" />;
  if (effectiveLiveState === 'failed') return <StatePanel state="failed" onRetry={retry} />;
  if (effectiveLiveState === 'forbidden') return <StatePanel state="forbidden" />;
  const pageCount = Math.max(1, requestedPage + (liveNextCursor ? 1 : 0));
  const changePage = (nextPage: number) => {
    const next = new URLSearchParams(searchParams);
    const cursor =
      nextPage === requestedPage + 1 ? liveNextCursor : (cursorByPage.current.get(nextPage) ?? '');
    if (nextPage > 1 && cursor) {
      next.set('asset_history_page', String(nextPage));
      next.set('asset_history_cursor', cursor);
    } else {
      next.delete('asset_history_page');
      next.delete('asset_history_cursor');
    }
    void setSearchParams(next);
  };
  return (
    <>
      <section className="panel">
        <span className="overline">Brand registry</span>
        <h2>品牌、产品与竞品</h2>
        <p className="panel-subtitle">
          仅展示客户确认的资产；潜在别名、隐性竞品和内部消歧置信度不会暴露。
        </p>
        {experience?.source === 'live' && catalogProjection.total > catalogProjection.shown ? (
          <ProjectionLimitNotice
            items={[
              {
                key: 'customer-project-catalog',
                label: '品牌与竞品目录',
                total: catalogProjection.total,
                shown: catalogProjection.shown,
              },
            ]}
          />
        ) : null}
        {experience?.source === 'live' && catalogProjection.invalid ? (
          <div className="confirmation projection-limit-notice" role="alert">
            <Badge tone="warning">安全投影不完整</Badge>
            <span>品牌与竞品目录包含跨项目、种类错配或未通过 DLP 校验的条目。</span>
          </div>
        ) : null}
        <div className="asset-list">
          {experience?.source === 'live' ? (
            liveCatalog.length ? (
              liveCatalog.map((item) => (
                <article className="asset-row" key={item.id}>
                  <div>
                    <strong>{item.name}</strong>
                    <span>{item.website || '未提供可公开核验的 HTTPS 网站'}</span>
                  </div>
                  <div>
                    <small>{item.kind === 'brands' ? '真实品牌目录' : '客户指定竞品'}</small>
                    <b>{item.kind === 'brands' ? '品牌' : '竞品'}</b>
                  </div>
                  <Badge tone="positive">已确认</Badge>
                </article>
              ))
            ) : (
              <StatePanel state="empty" />
            )
          ) : (
            brands.map((item) => (
              <article className="asset-row" key={`${item.brand}-${item.product}`}>
                <div>
                  <strong>{item.brand}</strong>
                  <span>{item.product}</span>
                </div>
                <div>
                  <small>客户指定竞品</small>
                  <b>{item.competitor}</b>
                </div>
                <Badge tone="positive">已确认</Badge>
              </article>
            ))
          )}
        </div>
      </section>
      <form className="panel form-panel" onSubmit={(event) => void submit(event)} noValidate>
        <h2>登记品牌资产</h2>
        <div className="form-grid form-grid-three">
          <Field id="brandName" label="品牌名称" error={errors.brandName}>
            <input id="brandName" {...register('brandName')} />
          </Field>
          <Field id="website" label="官方 HTTPS 网站" error={errors.website}>
            <input id="website" inputMode="url" {...register('website')} />
          </Field>
          <Field id="productName" label="产品或服务" error={errors.productName}>
            <input id="productName" {...register('productName')} />
          </Field>
          <Field id="competitor" label="客户指定竞品" error={errors.competitor}>
            <input id="competitor" {...register('competitor')} />
          </Field>
          <Field id="forbiddenClaim" label="禁止使用的表述" error={errors.forbiddenClaim}>
            <input
              id="forbiddenClaim"
              placeholder="例如：未经证明的“行业第一”"
              {...register('forbiddenClaim')}
            />
          </Field>
        </div>
        <label className="check-field">
          <input type="checkbox" {...register('truthConfirmed')} />
          我确认品牌、产品、竞品与禁止表述真实，并同意生成不可静默覆盖的新版本。
        </label>
        {errors.truthConfirmed ? (
          <span className="field-error" role="alert">
            {errors.truthConfirmed.message}
          </span>
        ) : null}
        <div className="form-actions">
          <span>
            {experience?.source === 'live' && latestConfirmation
              ? `最新客户确认 v${latestConfirmation.revision}`
              : '提交后进入客户确认版本，不自动改变监测配置。'}
          </span>
          <button className="button" disabled={isSubmitting || assetWritePending}>
            {isSubmitting || assetWritePending ? '正在登记…' : '登记资产'}
          </button>
        </div>
      </form>
      {experience?.source === 'live' ? (
        <section className="panel">
          <h2>客户资产确认历史</h2>
          {historyProjection.total > customerGovernanceHistoryLimit ? (
            <ProjectionLimitNotice
              items={[
                {
                  key: 'customer-asset-history',
                  label: '客户资产确认历史',
                  total: historyProjection.total,
                  shown: historyProjection.shown,
                },
              ]}
            />
          ) : null}
          {historyProjection.invalid ? (
            <div className="confirmation projection-limit-notice" role="alert">
              <Badge tone="warning">安全投影不完整</Badge>
              <span>资产确认历史包含跨项目、乱序、游标不一致或未通过 DLP 校验的版本。</span>
            </div>
          ) : null}
          {confirmations.length ? (
            <ol className="timeline">
              {confirmations.map((confirmation) => (
                <li key={confirmation.pub_id}>
                  <strong>
                    v{confirmation.revision} · {confirmation.brand_name}
                  </strong>
                  <span>
                    {confirmation.product_name} · 竞品 {confirmation.competitor_name} · 禁止：
                    {confirmation.prohibited_claim}
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <StatePanel state="empty" />
          )}
          <Pagination
            label="客户资产确认历史分页"
            page={requestedPage}
            pageCount={pageCount}
            onPageChange={changePage}
          />
        </section>
      ) : null}
    </>
  );
}

const requestSchema = z.object({
  question: z
    .string()
    .trim()
    .min(8, '问题至少需要 8 个字')
    .max(200)
    .refine(noClientSecret, noClientSecretMessage),
  priority: z.enum(['high', 'medium', 'low']),
  goalMetric: z.enum(['mention_rate', 'top3_rate', 'citation_coverage']),
  target: z.number().min(0, '目标不能小于 0').max(100, '百分比目标不能超过 100'),
  changeType: z.enum(['add_query', 'pause', 'resume', 'backfill']),
  reason: z
    .string()
    .trim()
    .min(10, '请说明至少 10 个字的业务原因')
    .max(500)
    .refine(noClientSecret, noClientSecretMessage),
});
type RequestFormValue = z.infer<typeof requestSchema>;

function QuestionsWorkspace() {
  const experience = useOptionalExperienceContext();
  const [retryKey, retry] = useLocalRetry();
  const questionWriteContext = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
  ]);
  const questionWrite = useCustomerMutationGuard(questionWriteContext);
  const [submitted, setSubmitted] = useState<RequestFormValue[]>([]);
  const [liveCatalog, setLiveCatalog] = useState<LiveQuestionGoal[]>([]);
  const [catalogProjection, setCatalogProjection] = useState({
    total: 0,
    shown: 0,
    invalid: false,
  });
  const [catalogState, setCatalogState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const [submissionState, setSubmissionState] = useState<
    'idle' | 'submitting' | 'saved' | 'failed' | 'forbidden'
  >('idle');
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<RequestFormValue>({
    resolver: zodResolver(requestSchema),
    defaultValues: {
      question: '',
      priority: 'medium',
      goalMetric: 'mention_rate',
      target: 70,
      changeType: 'add_query',
      reason: '',
    },
  });
  useEffect(() => {
    setSubmissionState('idle');
    setSubmitted([]);
  }, [questionWriteContext]);
  useEffect(() => {
    if (experience?.source !== 'live' || !experience.projectPubId) {
      setCatalogState('fixture');
      setCatalogProjection({ total: 0, shown: 0, invalid: false });
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveCatalog([]);
      setCatalogProjection({ total: 0, shown: 0, invalid: false });
      setCatalogState('forbidden');
      return;
    }
    setLiveCatalog([]);
    setCatalogProjection({ total: 0, shown: 0, invalid: false });
    setCatalogState('loading');
    let cancelled = false;
    void Promise.all([
      listProjectResources(experience.projectPubId, 'query-items', headers),
      listProjectResources(experience.projectPubId, 'goals', headers),
    ]).then(([questionResult, goalResult]) => {
      if (cancelled) return;
      if (questionResult.kind !== 'ready' || goalResult.kind !== 'ready') {
        setLiveCatalog([]);
        setCatalogProjection({ total: 0, shown: 0, invalid: false });
        setCatalogState(
          questionResult.kind === 'forbidden' || goalResult.kind === 'forbidden'
            ? 'forbidden'
            : 'failed',
        );
        return;
      }
      const catalog = [
        ...projectQuestionGoals(questionResult.data.data, 'query-items'),
        ...projectQuestionGoals(goalResult.data.data, 'goals'),
      ];
      const upstreamShown = questionResult.data.projection.shown + goalResult.data.projection.shown;
      setLiveCatalog(catalog);
      setCatalogProjection({
        total: questionResult.data.projection.total + goalResult.data.projection.total,
        shown: catalog.length,
        invalid:
          questionResult.data.projection.invalid ||
          goalResult.data.projection.invalid ||
          catalog.length !== upstreamShown,
      });
      setCatalogState('ready');
    });
    return () => {
      cancelled = true;
    };
  }, [experience, retryKey]);
  const submit = handleSubmit(async (value) => {
    if (experience?.source === 'live') {
      const headers = getValidatedIdentityHeaders();
      if (!headers || !experience.projectPubId) {
        setSubmissionState('forbidden');
        return;
      }
      const projectPubId = experience.projectPubId;
      const writeTicket = questionWrite.begin(headers);
      if (!writeTicket) return;
      setSubmissionState('submitting');
      const result = await createProjectResource(
        projectPubId,
        'change-requests',
        {
          kind: value.changeType,
          state: 'pending',
          payload: {
            question: value.question,
            priority: value.priority,
            goal_metric: value.goalMetric,
            target_percent: value.target,
            reason: value.reason,
          },
        },
        headers,
        `customer-change-${crypto.randomUUID()}`,
      );
      if (!questionWrite.finish(writeTicket)) return;
      if (result.kind !== 'ready') {
        setSubmissionState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
    } else {
      setSubmissionState('submitting');
    }
    setSubmitted((current) => [value, ...current]);
    setSubmissionState('saved');
    reset({
      question: '',
      priority: 'medium',
      goalMetric: 'mention_rate',
      target: 70,
      changeType: 'add_query',
      reason: '',
    });
  });
  if (catalogState === 'loading') return <StatePanel state="loading" />;
  if (catalogState === 'failed') return <StatePanel state="failed" onRetry={retry} />;
  if (catalogState === 'forbidden') return <StatePanel state="forbidden" />;
  return (
    <div className="workspace-grid">
      <form className="panel form-panel" onSubmit={(event) => void submit(event)} noValidate>
        <span className="overline">Change request</span>
        <h2>问题、目标与配置申请</h2>
        <p className="panel-subtitle">
          客户提交的是待审核申请，不直接修改调度真源。运营审批、生效版本和审计事件分别记录。
        </p>
        <Field id="question" label="关注问题" error={errors.question}>
          <textarea
            id="question"
            rows={3}
            placeholder="例如：制造企业如何选择可私有化部署的知识库？"
            {...register('question')}
          />
        </Field>
        <div className="form-grid form-grid-three">
          <Field id="priority" label="优先级" error={errors.priority}>
            <select id="priority" {...register('priority')}>
              <option value="high">高</option>
              <option value="medium">中</option>
              <option value="low">低</option>
            </select>
          </Field>
          <Field id="goalMetric" label="目标指标" error={errors.goalMetric}>
            <select id="goalMetric" {...register('goalMetric')}>
              <option value="mention_rate">品牌提及率</option>
              <option value="top3_rate">Top 3 占比</option>
              <option value="citation_coverage">引用覆盖</option>
            </select>
          </Field>
          <Field id="target" label="目标值（%）" error={errors.target}>
            <input
              id="target"
              type="number"
              min="0"
              max="100"
              {...register('target', { valueAsNumber: true })}
            />
          </Field>
          <Field id="changeType" label="申请动作" error={errors.changeType}>
            <select id="changeType" {...register('changeType')}>
              <option value="add_query">新增问题</option>
              <option value="pause">申请暂停</option>
              <option value="resume">申请恢复</option>
              <option value="backfill">申请补采</option>
            </select>
          </Field>
        </div>
        <Field id="reason" label="业务原因" error={errors.reason}>
          <textarea id="reason" rows={3} {...register('reason')} />
        </Field>
        <div className="form-actions">
          <span>
            {experience?.source === 'live'
              ? '提交将通过生成的 OpenAPI client 写入幂等申请与审计记录。'
              : 'Contract fixture：提交仅保存在当前演示会话。'}
          </span>
          <button className="button" disabled={submissionState === 'submitting'}>
            {submissionState === 'submitting' ? '正在提交…' : '提交审核'}
          </button>
        </div>
        {submissionState === 'saved' ? <Toast>申请已进入待运营审核队列</Toast> : null}
        {submissionState === 'failed' ? (
          <Toast tone="negative">申请服务暂不可用；内容仍保留在表单中，请稍后重试。</Toast>
        ) : null}
        {submissionState === 'forbidden' ? (
          <Toast tone="negative">无权提交此项目申请，且不会探测或显示项目是否存在。</Toast>
        ) : null}
      </form>
      <aside className="panel">
        <h2>{experience?.source === 'live' ? '当前问题与目标' : '申请队列'}</h2>
        {experience?.source === 'live' && catalogProjection.total > catalogProjection.shown ? (
          <ProjectionLimitNotice
            items={[
              {
                key: 'customer-question-goal-catalog',
                label: '问题与目标目录',
                total: catalogProjection.total,
                shown: catalogProjection.shown,
              },
            ]}
          />
        ) : null}
        {experience?.source === 'live' && catalogProjection.invalid ? (
          <div className="confirmation projection-limit-notice" role="alert">
            <Badge tone="warning">安全投影不完整</Badge>
            <span>问题与目标目录包含跨项目、种类错配或未通过 DLP 校验的条目。</span>
          </div>
        ) : null}
        {experience?.source === 'live' && liveCatalog.length ? (
          <div className="request-list">
            {liveCatalog.map((item) => (
              <article key={item.id}>
                <Badge tone={item.kind === 'query' ? 'info' : 'positive'}>
                  {item.kind === 'query' ? '已配置问题' : '真实目标'}
                </Badge>
                <strong>
                  {item.kind === 'query'
                    ? item.text
                    : item.metric === 'mention_rate'
                      ? '品牌提及率'
                      : item.metric === 'top3_rate'
                        ? 'Top 3 占比'
                        : '引用覆盖'}
                </strong>
                <span>
                  {item.kind === 'query'
                    ? `优先级 ${item.priority}`
                    : `目标 ${item.targetPercent.toFixed(1)}% · ${item.state}`}
                </span>
              </article>
            ))}
          </div>
        ) : submitted.length ? (
          <div className="request-list">
            {submitted.map((request, index) => (
              <article key={`${request.question}-${index}`}>
                <Badge tone="warning">待运营审核</Badge>
                <strong>{request.question}</strong>
                <span>
                  目标 {request.target}% · {request.changeType}
                </span>
              </article>
            ))}
          </div>
        ) : (
          <StatePanel state="empty" />
        )}
      </aside>
    </div>
  );
}

function HomeWorkspace() {
  const experience = useOptionalExperienceContext();
  const live = experience?.source === 'live';
  const [retryKey, retry] = useLocalRetry();
  const [metrics, setMetrics] = useState<AnalyticsOverviewSafeResponse>([]);
  const [liveState, setLiveState] = useState<
    'fixture' | 'loading' | 'ready' | 'empty' | 'failed' | 'forbidden'
  >(live ? 'loading' : 'fixture');
  useEffect(() => {
    if (!live || !experience?.projectPubId) {
      setLiveState('fixture');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveState('forbidden');
      return;
    }
    const end = new Date();
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - 29);
    let cancelled = false;
    setLiveState('loading');
    void getAnalyticsOverview(
      experience.projectPubId,
      start.toISOString().slice(0, 10),
      end.toISOString().slice(0, 10),
      {},
      headers,
    ).then((result) => {
      if (cancelled) return;
      if (result.kind !== 'ready') {
        setMetrics([]);
        setLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      const projected = projectAnalyticsOverview(result.data.data);
      setMetrics(projected);
      setLiveState(projected.length ? 'ready' : 'empty');
    });
    return () => {
      cancelled = true;
    };
  }, [experience, live, retryKey]);
  const metricLabel: Record<string, string> = {
    mention_rate: '品牌提及率',
    citation_rate: '引用率',
    eligible_rate: '有效回答率',
    average_rank: '平均排名',
  };
  const liveCards = metrics.slice(0, 4).map((metric) => ({
    label: metricLabel[metric.metric] ?? '合同指标',
    value:
      metric.value === null
        ? '—'
        : metric.metric.endsWith('_rate')
          ? `${(metric.value * 100).toFixed(1)}%`
          : metric.value.toFixed(2),
    detail: `${metric.numerator ?? '—'} / ${metric.denominator} · ${analyticsMetricStateLabel(metric)}`,
    state: analyticsMetricDataState(metric),
  }));
  return (
    <>
      <section className="hero-panel">
        <div>
          <span className="overline">{live ? 'Analytics overview' : 'Project stage'}</span>
          <h2>{live ? '项目监测概览' : '监测运行中'}</h2>
          <p>
            {live
              ? '展示当前项目最近 30 天的真实分析合同结果；当前合同未提供项目阶段或采集计划，不展示进度比例。'
              : '最近一次数据窗口已于今天 10:20 冻结，下一次采集预计今晚 22:00 开始。'}
          </p>
        </div>
        {!live ? (
          <>
            <div
              className="stage-progress"
              role="progressbar"
              aria-label="项目进度"
              aria-valuemin={0}
              aria-valuemax={6}
              aria-valuenow={4}
            >
              <span style={{ width: '67%' }} />
            </div>
            <ol className="stage-list">
              <li data-done="true">资料确认</li>
              <li data-done="true">品牌档案</li>
              <li data-done="true">问题确认</li>
              <li data-current="true">监测运行</li>
              <li>报告审核</li>
              <li>优化复测</li>
            </ol>
          </>
        ) : null}
      </section>
      {liveState === 'loading' ? <StatePanel state="loading" /> : null}
      {liveState === 'failed' ? <StatePanel state="failed" onRetry={retry} /> : null}
      {liveState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
      {liveState === 'empty' ? <StatePanel state="empty" /> : null}
      {liveState === 'ready' ? <MetricGrid metrics={liveCards} /> : null}
      {liveState === 'fixture' ? <StatePanel state="insufficient" /> : null}
      <div className="two-column">
        <section className="panel">
          <h2>下一步</h2>
          <p className="panel-subtitle">
            {live
              ? '当前安全投影未提供建议动作，不根据指标推断客户待办。'
              : '系统只推荐一个最需要完成的客户动作。'}
          </p>
          {live ? (
            <StatePanel state="insufficient" />
          ) : (
            <article className="next-action">
              <Badge tone="warning">今天到期</Badge>
              <h3>确认 Q3 报告中的目标口径</h3>
              <p>报告审核人对“Top 3 目标值”提出一个澄清问题，确认后才能发布。</p>
              <button className="button" onClick={() => navigateCustomerSection('reports')}>
                前往报告
              </button>
            </article>
          )}
        </section>
        <section className="panel">
          <h2>数据新鲜度</h2>
          <p className="panel-subtitle">真实状态与最后可用版本分开显示。</p>
          {live &&
          liveState === 'ready' &&
          !metrics.some((metric) => metric.state === 'delayed') ? (
            <p>
              <Badge tone="positive">数据可用</Badge> 当前合同指标未标记为延迟。
            </p>
          ) : (
            <StatePanel
              state={
                live && metrics.some((metric) => metric.state === 'delayed')
                  ? 'delayed'
                  : live
                    ? 'insufficient'
                    : 'delayed'
              }
            />
          )}
        </section>
      </div>
    </>
  );
}

type AnswerFixture = {
  id: string;
  runId?: string;
  configVersionId?: string;
  question: string;
  model: string;
  mode: string;
  region: string;
  answer: string;
  cited: string[];
  mention: boolean | null;
  capturedAt: string;
};
export type CustomerAnswerPageProjection = {
  answers: AnswerFixture[];
  total: number;
  invalid: boolean;
};

export function projectCustomerAnswerPage(
  value: unknown,
  expectedProjectPubId: string,
): CustomerAnswerPageProjection {
  if (
    !isRecord(value) ||
    !Array.isArray(value.data) ||
    safeOpaqueId(expectedProjectPubId, 'prj_') !== expectedProjectPubId
  ) {
    return { answers: [], total: 0, invalid: true };
  }
  let invalid = false;
  const answers = value.data
    .slice(0, customerEvidenceProjectionLimits.answers)
    .flatMap<AnswerFixture>((candidate) => {
      if (!isRecord(candidate)) {
        invalid = true;
        return [];
      }
      const id = safeOpaqueId(candidate.pub_id, 'ans_');
      const projectPubId = safeOpaqueId(candidate.project_pub_id, 'prj_');
      const capturedAt = projectIsoTimestamp(candidate.capture_time);
      const runId =
        candidate.run_pub_id === null || candidate.run_pub_id === undefined
          ? null
          : safeOpaqueId(candidate.run_pub_id, 'run_');
      const configVersionId =
        candidate.config_version_pub_id === null || candidate.config_version_pub_id === undefined
          ? null
          : safeOpaqueId(candidate.config_version_pub_id, 'cfv_');
      const queryPubId =
        candidate.query_pub_id === null || candidate.query_pub_id === undefined
          ? null
          : safeOpaqueId(candidate.query_pub_id, 'qry_');
      const queryText =
        candidate.query_text === null || candidate.query_text === undefined
          ? null
          : typeof candidate.query_text === 'string' &&
              candidate.query_text.length > 0 &&
              candidate.query_text.length <= 500 &&
              !containsClientSecret(candidate.query_text)
            ? candidate.query_text
            : undefined;
      const responseText =
        typeof candidate.response_text === 'string' &&
        candidate.response_text.length > 0 &&
        candidate.response_text.length <= 4_000 &&
        !containsClientSecret(candidate.response_text)
          ? candidate.response_text
          : '';
      const safeText = (text: unknown, max: number): string =>
        typeof text === 'string' &&
        text.length > 0 &&
        text.length <= max &&
        !containsClientSecret(text)
          ? text
          : '';
      const model = safeText(candidate.model, 120);
      const mode = safeText(candidate.mode, 80);
      const region = safeText(candidate.region, 120);
      const mention =
        candidate.mentioned === null || typeof candidate.mentioned === 'boolean'
          ? candidate.mentioned
          : undefined;
      const citationCount =
        typeof candidate.citation_count === 'number' &&
        Number.isSafeInteger(candidate.citation_count) &&
        candidate.citation_count >= 0
          ? candidate.citation_count
          : null;
      if (
        !id ||
        projectPubId !== expectedProjectPubId ||
        !capturedAt ||
        (candidate.run_pub_id !== null && candidate.run_pub_id !== undefined && !runId) ||
        (candidate.config_version_pub_id !== null &&
          candidate.config_version_pub_id !== undefined &&
          !configVersionId) ||
        (candidate.query_pub_id !== null && candidate.query_pub_id !== undefined && !queryPubId) ||
        queryText === undefined ||
        (!queryText && !queryPubId) ||
        !responseText ||
        !model ||
        !mode ||
        !region ||
        mention === undefined ||
        citationCount === null
      ) {
        invalid = true;
        return [];
      }
      return [
        {
          id,
          ...(runId ? { runId } : {}),
          ...(configVersionId ? { configVersionId } : {}),
          question: queryText ?? queryPubId!,
          model,
          mode,
          region,
          answer: responseText,
          cited: citationCount > 0 ? [`${citationCount} 条规范化引用`] : [],
          mention,
          capturedAt: capturedAt.slice(0, 16).replace('T', ' '),
        },
      ];
    });
  return { answers, total: value.data.length, invalid };
}

type LiveAnswerRelations = {
  citations: {
    id: string;
    label: string;
    host: string;
    sourceUrl: string;
    citedText: string | null;
    contentHash: string;
  }[];
  evidence: {
    id: string;
    relation: string;
    kind: string;
    mimeType: string;
    capturedAt: string;
    integrity: string;
    sha256: string;
    byteSize: number;
    sourceHost: string;
    sourceUrl: string | null;
    anchorCount: number;
    anchors: EvidenceAnchor[];
    aiOpenedVerified: boolean;
    brandMentionVerified: boolean;
  }[];
  history: {
    id: string;
    beforeId: string;
    afterId: string;
    similarity: number | null;
    visualAvailable: boolean;
  }[];
  projectionNotices: ProjectionLimitNoticeItem[];
  invalidProjection: CustomerEvidenceProjectionCollection[];
};

export const customerEvidenceProjectionLimits = {
  answers: 2,
  assets: 2,
  citations: customerEvidenceReadProjectionLimits.citations,
  evidence: customerEvidenceReadProjectionLimits.evidence,
  anchors: customerEvidenceReadProjectionLimits.anchors,
  history: customerEvidenceReadProjectionLimits.history,
} as const;
type CustomerEvidenceProjectionCollection = keyof typeof customerEvidenceProjectionLimits;

const safeRelationUrl = (value: unknown): string | null => {
  if (typeof value !== 'string' || value.length > 2_000 || containsClientSecret(value)) return null;
  try {
    const parsed = new URL(value);
    return ['http:', 'https:'].includes(parsed.protocol) &&
      !parsed.username &&
      !parsed.password &&
      parsed.hostname.length > 0 &&
      parsed.hostname.length <= 253 &&
      !containsClientSecret(parsed.hostname)
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
};

const safeRelationHost = (value: unknown): string | null => {
  const url = safeRelationUrl(value);
  return url ? new URL(url).hostname : null;
};

const safeEvidenceBoundingBox = (value: unknown): [number, number, number, number] | null => {
  if (!isRecord(value)) return null;
  const coordinates = [value.x, value.y, value.width, value.height];
  if (
    !coordinates.every(
      (coordinate) =>
        typeof coordinate === 'number' &&
        Number.isFinite(coordinate) &&
        coordinate >= 0 &&
        coordinate <= 1_000_000,
    ) ||
    coordinates[2] === 0 ||
    coordinates[3] === 0
  ) {
    return null;
  }
  return coordinates as [number, number, number, number];
};

export const safeOfficialShareUrl = (value: string | null, platform: string): string | null => {
  if (!value) return null;
  const safe = safeRelationUrl(value);
  if (!safe) return null;
  const parsed = new URL(safe);
  if (parsed.protocol !== 'https:') return null;
  const normalizedPlatform = platform.trim().toLowerCase();
  if (normalizedPlatform === 'doubao' || normalizedPlatform === '豆包') {
    return ['doubao.com', 'www.doubao.com'].includes(parsed.hostname) &&
      parsed.pathname.startsWith('/thread/')
      ? parsed.toString()
      : null;
  }
  if (normalizedPlatform === 'deepseek') {
    return parsed.hostname === 'chat.deepseek.com' && parsed.pathname.startsWith('/share/')
      ? parsed.toString()
      : null;
  }
  if (['yiyan', '文心一言', '文心'].includes(normalizedPlatform)) {
    return ['mr.baidu.com', 'wenxin.baidu.com'].includes(parsed.hostname)
      ? parsed.toString()
      : null;
  }
  return null;
};

type LiveEvidencePurposeGroups = {
  officialShareImages: LiveAnswerRelations['evidence'];
  officialShareLinks: LiveAnswerRelations['evidence'];
  runtimeAnswerScreenshots: LiveAnswerRelations['evidence'];
  aiOpenedPagePreviews: LiveAnswerRelations['evidence'];
  brandMentionScreenshots: LiveAnswerRelations['evidence'];
  sourceReviewScreenshots: LiveAnswerRelations['evidence'];
  otherImages: LiveAnswerRelations['evidence'];
};

export function groupLiveEvidenceByPurpose(
  evidence: LiveAnswerRelations['evidence'],
): LiveEvidencePurposeGroups {
  const groups: LiveEvidencePurposeGroups = {
    officialShareImages: [],
    officialShareLinks: [],
    runtimeAnswerScreenshots: [],
    aiOpenedPagePreviews: [],
    brandMentionScreenshots: [],
    sourceReviewScreenshots: [],
    otherImages: [],
  };
  for (const asset of evidence) {
    if (asset.kind === 'share_image' && asset.relation === 'official_share_image') {
      groups.officialShareImages.push(asset);
    } else if (asset.kind === 'share_link' && asset.relation === 'official_share_link') {
      groups.officialShareLinks.push(asset);
    } else if (asset.kind === 'answer_screenshot') {
      groups.runtimeAnswerScreenshots.push(asset);
    } else if (
      asset.kind === 'source_screenshot' &&
      asset.relation === 'ai_opened_source_preview' &&
      asset.aiOpenedVerified
    ) {
      groups.aiOpenedPagePreviews.push(asset);
    } else if (
      asset.kind === 'source_screenshot' &&
      asset.relation === 'brand_mention_source_snapshot' &&
      asset.brandMentionVerified &&
      asset.anchors.some((anchor) => anchor.bbox)
    ) {
      groups.brandMentionScreenshots.push(asset);
    } else if (asset.kind === 'source_screenshot') {
      groups.sourceReviewScreenshots.push(asset);
    } else if (asset.mimeType.startsWith('image/')) {
      groups.otherImages.push(asset);
    }
  }
  return groups;
}

export function projectAnswerRelations(
  value: unknown,
  expectedAnswerPubId: string,
): LiveAnswerRelations | null {
  if (
    !isRecord(value) ||
    safeOpaqueId(expectedAnswerPubId, 'ans_') !== expectedAnswerPubId ||
    safeOpaqueId(value.answer_pub_id, 'ans_') !== expectedAnswerPubId ||
    !Array.isArray(value.citations) ||
    !Array.isArray(value.evidence) ||
    !Array.isArray(value.history)
  ) {
    return null;
  }
  const answerCitationRows = Array.isArray(value.answer_citations)
    ? value.answer_citations
    : value.citations;
  const strictEvidenceIds = (candidate: unknown): Set<string> =>
    new Set(
      (Array.isArray(candidate) ? candidate : []).flatMap((item) => {
        if (!isRecord(item)) return [];
        const id = safeOpaqueId(item.pub_id, 'evd_');
        return id ? [id] : [];
      }),
    );
  // The API only emits these semantic collections after strict relation, MIME,
  // byte-size and decoded-image bbox validation. Compatibility evidence rows are
  // never promoted merely because their relation text looks authoritative.
  const brandMentionEvidenceIds = strictEvidenceIds(value.brand_mention_evidence);
  const aiOpenedPreviewIds = strictEvidenceIds(value.opened_source_previews);
  const invalidProjection = new Set<CustomerEvidenceProjectionCollection>();
  const projectionNotices: ProjectionLimitNoticeItem[] = [];
  const addLimitNotice = (
    collection: CustomerEvidenceProjectionCollection,
    label: string,
    total: number,
    shown: number,
  ) => {
    if (total > customerEvidenceProjectionLimits[collection]) {
      projectionNotices.push({
        key: `customer-answer-${collection}`,
        label,
        total,
        shown,
      });
    }
  };

  const citations = answerCitationRows
    .slice(0, customerEvidenceProjectionLimits.citations)
    .flatMap<LiveAnswerRelations['citations'][number]>((candidate) => {
      if (!isRecord(candidate)) {
        invalidProjection.add('citations');
        return [];
      }
      const id = safeOpaqueId(candidate.pub_id, 'cit_');
      const sourceUrl = safeRelationUrl(candidate.canonical_url);
      const host = sourceUrl ? safeRelationHost(sourceUrl) : null;
      const ordinal =
        typeof candidate.ordinal === 'number' &&
        Number.isSafeInteger(candidate.ordinal) &&
        candidate.ordinal > 0
          ? candidate.ordinal
          : null;
      const title =
        candidate.title === null
          ? null
          : typeof candidate.title === 'string' &&
              candidate.title.length > 0 &&
              candidate.title.length <= 300 &&
              !containsClientSecret(candidate.title)
            ? candidate.title
            : undefined;
      const contentHash =
        candidate.content_hash === null
          ? null
          : typeof candidate.content_hash === 'string' &&
              /^[0-9a-f]{64}$/.test(candidate.content_hash)
            ? candidate.content_hash
            : undefined;
      const citedText =
        candidate.cited_text === null
          ? null
          : typeof candidate.cited_text === 'string' &&
              candidate.cited_text.length > 0 &&
              candidate.cited_text.length <= 2_000 &&
              !containsClientSecret(candidate.cited_text)
            ? candidate.cited_text
            : undefined;
      if (
        !id ||
        !host ||
        !sourceUrl ||
        ordinal === null ||
        title === undefined ||
        citedText === undefined ||
        contentHash === undefined
      ) {
        invalidProjection.add('citations');
        return [];
      }
      return [
        {
          id,
          label: title ?? host,
          host,
          sourceUrl,
          citedText,
          contentHash: contentHash ?? '未提供',
        },
      ];
    });
  addLimitNotice('citations', '答案组织引用', answerCitationRows.length, citations.length);

  let truncatedAnchorTotal = 0;
  let truncatedAnchorShown = 0;
  const evidence = value.evidence
    .slice(0, customerEvidenceProjectionLimits.evidence)
    .flatMap<LiveAnswerRelations['evidence'][number]>((candidate) => {
      if (!isRecord(candidate)) {
        invalidProjection.add('evidence');
        return [];
      }
      const id = safeOpaqueId(candidate.pub_id, 'evd_');
      const capturedAt = projectIsoTimestamp(candidate.capture_time);
      const relation =
        typeof candidate.relation_type === 'string' &&
        candidate.relation_type.length > 0 &&
        candidate.relation_type.length <= 80 &&
        !containsClientSecret(candidate.relation_type)
          ? candidate.relation_type
          : '';
      const kind =
        typeof candidate.kind === 'string' &&
        candidate.kind.length > 0 &&
        candidate.kind.length <= 80 &&
        !containsClientSecret(candidate.kind)
          ? candidate.kind
          : '';
      const mimeType =
        typeof candidate.mime_type === 'string' &&
        candidate.mime_type.length > 0 &&
        candidate.mime_type.length <= 120 &&
        !containsClientSecret(candidate.mime_type)
          ? candidate.mime_type
          : '';
      const sha256 =
        typeof candidate.sha256 === 'string' && /^[0-9a-f]{64}$/.test(candidate.sha256)
          ? candidate.sha256
          : '';
      const byteSize =
        typeof candidate.byte_size === 'number' &&
        Number.isSafeInteger(candidate.byte_size) &&
        candidate.byte_size > 0 &&
        candidate.byte_size <= 30 * 1024 * 1024
          ? candidate.byte_size
          : 0;
      const sourceUrl =
        candidate.source_url === null ? null : safeRelationUrl(candidate.source_url);
      const sourceHost = sourceUrl ? safeRelationHost(sourceUrl) : '来源已隐藏';
      if (!Array.isArray(candidate.anchors)) invalidProjection.add('anchors');
      const anchors = Array.isArray(candidate.anchors) ? candidate.anchors : [];
      const projectedAnchors: EvidenceAnchor[] = [];
      for (const anchor of anchors.slice(0, customerEvidenceProjectionLimits.anchors)) {
        if (!isRecord(anchor)) {
          invalidProjection.add('anchors');
          continue;
        }
        const anchorId = safeOpaqueId(anchor.pub_id, 'anch_');
        const textStart =
          anchor.text_start === null ||
          (typeof anchor.text_start === 'number' &&
            Number.isSafeInteger(anchor.text_start) &&
            anchor.text_start >= 0);
        const textEnd =
          anchor.text_end === null ||
          (typeof anchor.text_end === 'number' &&
            Number.isSafeInteger(anchor.text_end) &&
            anchor.text_end >= 0);
        const textRange =
          anchor.text_start === null ||
          anchor.text_end === null ||
          (typeof anchor.text_start === 'number' &&
            typeof anchor.text_end === 'number' &&
            anchor.text_end >= anchor.text_start);
        const pageNumber =
          anchor.page_number === null ||
          (typeof anchor.page_number === 'number' &&
            Number.isSafeInteger(anchor.page_number) &&
            anchor.page_number > 0);
        const quoteHash =
          anchor.quote_hash === null ||
          (typeof anchor.quote_hash === 'string' && /^[0-9a-f]{64}$/.test(anchor.quote_hash));
        const bbox = safeEvidenceBoundingBox(anchor.bbox);
        if (!anchorId || !textStart || !textEnd || !textRange || !pageNumber || !quoteHash) {
          invalidProjection.add('anchors');
          continue;
        }
        projectedAnchors.push({
          assetId: id,
          ...(typeof anchor.text_start === 'number' ? { textStart: anchor.text_start } : {}),
          ...(typeof anchor.text_end === 'number' ? { textEnd: anchor.text_end } : {}),
          ...(bbox ? { bbox } : {}),
        });
      }
      if (anchors.length > customerEvidenceProjectionLimits.anchors) {
        truncatedAnchorTotal += anchors.length;
        truncatedAnchorShown += projectedAnchors.length;
      }
      if (
        !id ||
        !capturedAt ||
        !relation ||
        !kind ||
        !mimeType ||
        !sha256 ||
        !byteSize ||
        !sourceHost ||
        (candidate.source_url !== null && !sourceUrl)
      ) {
        invalidProjection.add('evidence');
        return [];
      }
      return [
        {
          id,
          relation,
          kind,
          mimeType,
          capturedAt: capturedAt.slice(0, 16).replace('T', ' '),
          integrity: 'SHA-256 已校验',
          sha256,
          byteSize,
          sourceHost,
          sourceUrl,
          anchorCount: projectedAnchors.length,
          anchors: projectedAnchors,
          aiOpenedVerified: aiOpenedPreviewIds.has(id),
          brandMentionVerified: brandMentionEvidenceIds.has(id),
        },
      ];
    });
  addLimitNotice('evidence', '回答关联证据', value.evidence.length, evidence.length);
  if (truncatedAnchorTotal > 0) {
    projectionNotices.push({
      key: 'customer-answer-anchors',
      label: '单项证据锚点',
      total: truncatedAnchorTotal,
      shown: truncatedAnchorShown,
    });
  }

  const history = value.history
    .slice(-customerEvidenceProjectionLimits.history)
    .flatMap<LiveAnswerRelations['history'][number]>((candidate) => {
      if (!isRecord(candidate)) {
        invalidProjection.add('history');
        return [];
      }
      const id = safeOpaqueId(candidate.pub_id, 'diff_');
      const beforeId = safeOpaqueId(candidate.before_evidence_pub_id, 'evd_');
      const afterId = safeOpaqueId(candidate.after_evidence_pub_id, 'evd_');
      const createdAt = projectIsoTimestamp(candidate.created_at);
      const similarity =
        candidate.similarity === null
          ? null
          : typeof candidate.similarity === 'number' &&
              Number.isFinite(candidate.similarity) &&
              candidate.similarity >= 0 &&
              candidate.similarity <= 1
            ? candidate.similarity
            : undefined;
      if (
        !id ||
        !beforeId ||
        !afterId ||
        !createdAt ||
        similarity === undefined ||
        typeof candidate.visual_diff_available !== 'boolean'
      ) {
        invalidProjection.add('history');
        return [];
      }
      return [
        {
          id,
          beforeId,
          afterId,
          similarity,
          visualAvailable: candidate.visual_diff_available,
        },
      ];
    });
  addLimitNotice('history', '证据历史差异', value.history.length, history.length);

  return {
    citations,
    evidence,
    history,
    projectionNotices,
    invalidProjection: [...invalidProjection],
  };
}

export function mergeAnswerRelationProjection(
  value: LiveAnswerRelations,
  boundary: AnalyticsAnswerRelationsProjection['projection'],
): LiveAnswerRelations {
  const labels: Record<AnalyticsRelationCollection, string> = {
    citations: '回答引用',
    evidence: '回答关联证据',
    anchors: '单项证据锚点',
    history: '证据历史差异',
  };
  const shown: Record<AnalyticsRelationCollection, number> = {
    citations: value.citations.length,
    evidence: value.evidence.length,
    anchors: boundary.anchors.shown,
    history: value.history.length,
  };
  const notices = new Map(value.projectionNotices.map((notice) => [notice.key, notice]));
  const invalidProjection = new Set(value.invalidProjection);
  for (const collection of [
    'citations',
    'evidence',
    'anchors',
    'history',
  ] as const satisfies readonly AnalyticsRelationCollection[]) {
    const projection = boundary[collection];
    if (projection.invalid) invalidProjection.add(collection);
    if (projection.total > shown[collection]) {
      notices.set(`customer-answer-${collection}`, {
        key: `customer-answer-${collection}`,
        label: labels[collection],
        total: projection.total,
        shown: shown[collection],
      });
    }
  }
  return {
    ...value,
    projectionNotices: [...notices.values()],
    invalidProjection: [...invalidProjection],
  };
}

function LiveEvidenceImage({ asset }: { asset: LiveAnswerRelations['evidence'][number] }) {
  const headers = getValidatedIdentityHeaders();
  return (
    <VerifiedBlobImage
      className="live-evidence-image"
      alt={`${asset.kind} 证据 ${asset.id}`}
      resourceKey={`${asset.id}:${asset.sha256}`}
      load={async () => {
        if (!headers) return { kind: 'unavailable' };
        const result = await getEvidenceAssetContent(
          asset.id,
          { byteSize: asset.byteSize, mimeType: asset.mimeType, sha256: asset.sha256 },
          headers,
        );
        return result.kind === 'ready'
          ? { kind: 'ready', blob: result.data.blob }
          : { kind: result.kind };
      }}
    />
  );
}

function LiveEvidenceCard({
  asset,
  purpose,
}: {
  asset: LiveAnswerRelations['evidence'][number];
  purpose: 'plain' | 'ai-opened-preview' | 'brand-mention';
}) {
  const image = <LiveEvidenceImage asset={asset} />;
  return (
    <figure className="live-evidence-card">
      <figcaption>
        <strong>
          {purpose === 'brand-mention'
            ? '品牌提及原文证据'
            : purpose === 'ai-opened-preview'
              ? 'AI 打开 URL 的采集后页面概览'
              : asset.kind === 'share_image'
                ? '官方分享图片'
                : '运行时回答截图'}
        </strong>
        <span>
          {asset.sourceHost} · {asset.capturedAt} · {asset.integrity}
        </span>
      </figcaption>
      {purpose === 'plain' ? (
        image
      ) : (
        <EvidenceImageFrame
          label={`${asset.id} ${purpose === 'brand-mention' ? '品牌提及证据' : '页面概览'}`}
          {...(purpose === 'brand-mention' && asset.anchors[0] ? { anchor: asset.anchors[0] } : {})}
          overlayLabel="目标品牌原文位置"
        >
          {image}
        </EvidenceImageFrame>
      )}
      {purpose === 'ai-opened-preview' ? (
        <p className="evidence-image-frame-status">
          该图是采集器依据平台 TOOL_OPEN URL 事后重开的页面概览，不声称还原 AI 当时看到的像素。
        </p>
      ) : null}
    </figure>
  );
}

function LiveEvidencePurposeGallery({
  evidence,
  platform,
}: {
  evidence: LiveAnswerRelations['evidence'];
  platform: string;
}) {
  const groups = groupLiveEvidenceByPurpose(evidence);
  const validShareLinks = groups.officialShareLinks.flatMap((asset) => {
    const url = safeOfficialShareUrl(asset.sourceUrl, platform);
    return url ? [{ asset, url }] : [];
  });
  const missingShareParts = [
    ...(groups.officialShareImages.length ? [] : ['官方分享图片']),
    ...(validShareLinks.length ? [] : ['官方分享链接']),
  ];
  return (
    <div className="live-evidence-purpose-sections">
      <section className="live-evidence-gallery" aria-label="官方分享交付">
        <h3>官方分享交付</h3>
        {missingShareParts.length ? (
          <p className="evidence-purpose-warning" role="status">
            分享交付不完整：缺少{missingShareParts.join('、')}。运行时回答截图不会被当作官方分享图。
          </p>
        ) : (
          <p className="evidence-purpose-ok">已同时保存官方分享图片与官方分享链接。</p>
        )}
        {validShareLinks.map(({ asset, url }) => (
          <p key={asset.id}>
            <a href={url} target="_blank" rel="noreferrer noopener">
              打开{platform} 官方分享链接
            </a>
          </p>
        ))}
        {groups.officialShareImages.map((asset) => (
          <LiveEvidenceCard key={asset.id} asset={asset} purpose="plain" />
        ))}
      </section>

      <section className="live-evidence-gallery" aria-label="运行时回答截图">
        <h3>运行时回答截图</h3>
        <p className="panel-subtitle">
          只证明采集时的问答界面状态，不是官方分享制品，也不是信源品牌原文证据。
        </p>
        {groups.runtimeAnswerScreenshots.length ? (
          groups.runtimeAnswerScreenshots.map((asset) => (
            <LiveEvidenceCard key={asset.id} asset={asset} purpose="plain" />
          ))
        ) : (
          <p className="answer-detail-neutral">本次未登记运行时回答截图。</p>
        )}
      </section>

      <section className="live-evidence-gallery" aria-label="AI 打开页面概览">
        <h3>AI 打开页面概览</h3>
        {groups.aiOpenedPagePreviews.length ? (
          groups.aiOpenedPagePreviews.map((asset) => (
            <LiveEvidenceCard key={asset.id} asset={asset} purpose="ai-opened-preview" />
          ))
        ) : (
          <p className="answer-detail-neutral">
            本次未登记 <code>ai_opened_source_preview</code>{' '}
            图像资产；不会用检索结果或引用截图冒充页面概览。
          </p>
        )}
      </section>

      <section className="live-evidence-gallery" aria-label="品牌提及原文证据">
        <h3>品牌提及原文证据</h3>
        <p className="panel-subtitle">
          只展示同时具备真实信源页截图、品牌原文命中和可核验 bbox 的资产。
        </p>
        {groups.brandMentionScreenshots.length ? (
          groups.brandMentionScreenshots.map((asset) => (
            <LiveEvidenceCard key={asset.id} asset={asset} purpose="brand-mention" />
          ))
        ) : (
          <p className="answer-detail-neutral">
            本次没有通过“真实网页 + 品牌原文 + bbox”校验的证据。
          </p>
        )}
      </section>

      {groups.sourceReviewScreenshots.length ? (
        <section
          className="live-evidence-gallery evidence-purpose-quarantine"
          aria-label="采集后信源复核资产"
        >
          <h3>采集后信源复核资产（已隔离）</h3>
          <p className="evidence-purpose-warning">
            下列旧资产是答案生成后重开页面的复核截图，不是 AI 实际浏览证明；缺少真实品牌
            bbox，因此不渲染图片，也不计入品牌证据。
          </p>
          <ul>
            {groups.sourceReviewScreenshots.map((asset) => (
              <li key={asset.id}>
                {asset.id} · {asset.sourceHost} · {asset.relation}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

const answerFixtures: AnswerFixture[] = [
  {
    id: 'ans_01',
    question: '企业知识库如何选择？',
    model: '豆包',
    mode: 'deep',
    region: '上海',
    answer:
      '选择企业知识库时，需要同时评估数据权限、检索质量、更新机制与部署边界。云岫 AI 提供私有化知识治理与可追溯问答能力。[1]',
    cited: ['云岫智能产品白皮书', '工信部数据安全指南'],
    mention: true,
    capturedAt: '今天 09:42',
  },
  {
    id: 'ans_02',
    question: '适合制造业的 AI 平台有哪些？',
    model: 'DeepSeek',
    mode: 'quick',
    region: '江苏',
    answer:
      '制造业通常需要兼顾现场网络条件、知识更新频率和权限隔离，可优先评估具备本地部署与审计能力的平台。[1][2]',
    cited: ['制造业数字化转型指南', '企业知识工程实践'],
    mention: false,
    capturedAt: '今天 09:36',
  },
  {
    id: 'ans_03',
    question: '私有化大模型方案对比',
    model: '元宝',
    mode: 'quick',
    region: '北京',
    answer:
      '方案比较应覆盖基础模型、知识检索、应用编排、运维与安全治理五个层次。当前证据中没有足够信息形成品牌推荐。',
    cited: [],
    mention: false,
    capturedAt: '今天 09:28',
  },
  {
    id: 'ans_04',
    question: '知识库如何验证回答来源？',
    model: '豆包',
    mode: 'deep',
    region: '广东',
    answer:
      '可追溯系统应保存答案、引用规范化地址、页面快照、文本锚点和采集时间，并允许回看历史差异。[1]',
    cited: ['可信 AI 系统工程规范'],
    mention: true,
    capturedAt: '昨天 22:18',
  },
];

function EvidenceWorkspace() {
  const experience = useOptionalExperienceContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const [retryKey, retry] = useLocalRetry();
  const [assetRetryKey, retryAssets] = useLocalRetry();
  const [liveAnswers, setLiveAnswers] = useState<AnswerFixture[] | null>(null);
  const [liveAnswerProjection, setLiveAnswerProjection] = useState({
    total: 0,
    shown: 0,
    invalid: false,
  });
  const [liveAssets, setLiveAssets] = useState<LiveEvidenceAsset[]>([]);
  const [liveAssetProjection, setLiveAssetProjection] = useState({
    total: 0,
    shown: 0,
    invalid: false,
  });
  const [liveNextCursor, setLiveNextCursor] = useState('');
  const [liveAssetNextCursor, setLiveAssetNextCursor] = useState('');
  const cursorByPage = useRef(new Map<number, string>());
  const assetCursorByPage = useRef(new Map<number, string>());
  const [packageState, setPackageState] = useState<
    'idle' | 'saving' | 'saved' | 'failed' | 'forbidden'
  >('idle');
  const packageRequest = useRef(0);
  const [liveAnswerState, setLiveAnswerState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const [liveAssetState, setLiveAssetState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const model = ['all', 'doubao', 'deepseek', 'yuanbao'].includes(
    searchParams.get('answer_model') ?? '',
  )
    ? searchParams.get('answer_model')!
    : 'all';
  const mode = ['all', 'quick', 'deep'].includes(searchParams.get('answer_mode') ?? '')
    ? searchParams.get('answer_mode')!
    : 'all';
  const region = ['all', '上海', '江苏', '北京', '广东'].includes(
    searchParams.get('answer_region') ?? '',
  )
    ? searchParams.get('answer_region')!
    : 'all';
  const rawQuery = searchParams.get('answer_query') ?? '';
  const containsSecret = containsClientSecret(rawQuery);
  const query = containsSecret ? '' : rawQuery.slice(0, 80);
  const answerIdQuery = /^ans_[A-Za-z0-9_-]{1,116}$/.test(query) ? query : '';
  const rawPage = searchParams.get('answer_page') ?? '';
  const parsedPage = /^[1-9]\d{0,2}$/.test(rawPage) ? Number(rawPage) : 1;
  const rawCursor = searchParams.get('answer_cursor') ?? '';
  const answerCursor =
    /^ans_[A-Za-z0-9_-]{1,116}$/.test(rawCursor) && !containsClientSecret(rawCursor)
      ? rawCursor
      : '';
  const requestedPage =
    experience?.source === 'live' && parsedPage > 1 && !answerCursor ? 1 : parsedPage;
  const rawAssetPage = searchParams.get('evidence_page') ?? '';
  const parsedAssetPage = /^[1-9]\d{0,2}$/.test(rawAssetPage) ? Number(rawAssetPage) : 1;
  const rawAssetCursor = searchParams.get('evidence_cursor') ?? '';
  const assetCursor =
    /^evd_[A-Za-z0-9_-]{1,116}$/.test(rawAssetCursor) && !containsClientSecret(rawAssetCursor)
      ? rawAssetCursor
      : '';
  const assetPage =
    experience?.source === 'live' && parsedAssetPage > 1 && !assetCursor ? 1 : parsedAssetPage;
  const answerReadScope = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    String(retryKey),
    String(requestedPage),
    answerCursor,
    answerIdQuery,
    model,
    mode,
    region,
  ]);
  const assetReadScope = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    String(assetRetryKey),
    String(assetPage),
    assetCursor,
  ]);
  const answerPresentationScope = createStructuredClientScopeKey([answerReadScope, query]);
  const [answerResultScope, setAnswerResultScope] = useState(
    experience?.source === 'live' ? '' : answerReadScope,
  );
  const [assetResultScope, setAssetResultScope] = useState(
    experience?.source === 'live' ? '' : assetReadScope,
  );
  const effectiveLiveAnswerState =
    experience?.source === 'live' && answerResultScope !== answerReadScope
      ? 'loading'
      : liveAnswerState;
  const effectiveLiveAssetState =
    experience?.source === 'live' && assetResultScope !== assetReadScope
      ? 'loading'
      : liveAssetState;
  const packageWriteContext = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    String(assetRetryKey),
    String(assetPage),
    assetCursor,
    JSON.stringify(liveAssets.map((asset) => asset.id)),
  ]);
  const packageWrite = useCustomerMutationGuard(packageWriteContext);
  const [selected, setSelected] = useState<AnswerFixture | null>(null);
  const [selectedScope, setSelectedScope] = useState('');
  const [liveRelations, setLiveRelations] = useState<LiveAnswerRelations | null>(null);
  const relationRequest = useRef(0);
  useEffect(
    () => () => {
      relationRequest.current += 1;
    },
    [experience?.projectPubId, experience?.source],
  );
  const [liveRelationState, setLiveRelationState] = useState<
    'idle' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >('idle');
  const openEvidence = (answer: AnswerFixture) => {
    const requestId = relationRequest.current + 1;
    relationRequest.current = requestId;
    setSelected(answer);
    setSelectedScope(answerPresentationScope);
    setLiveRelations(null);
    if (experience?.source !== 'live') return;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveRelationState('failed');
      return;
    }
    setLiveRelationState('loading');
    void getAnalyticsAnswerRelations(answer.id, headers).then((result) => {
      if (relationRequest.current !== requestId) return;
      if (result.kind !== 'ready') {
        setLiveRelationState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      const projected = projectAnswerRelations(result.data, answer.id);
      if (!projected) {
        setLiveRelations(null);
        setLiveRelationState('failed');
        return;
      }
      setLiveRelations(mergeAnswerRelationProjection(projected, result.data.projection));
      setLiveRelationState('ready');
    });
  };
  const closeEvidence = () => {
    relationRequest.current += 1;
    setSelected(null);
    setSelectedScope('');
    setLiveRelations(null);
    setLiveRelationState('idle');
  };
  useEffect(() => {
    relationRequest.current += 1;
    setSelected(null);
    setSelectedScope('');
    setLiveRelations(null);
    setLiveRelationState('idle');
  }, [
    answerCursor,
    experience?.projectPubId,
    experience?.source,
    model,
    mode,
    query,
    region,
    requestedPage,
    retryKey,
  ]);
  const effectiveSelected = selected && selectedScope === answerPresentationScope ? selected : null;
  useEffect(() => {
    packageRequest.current += 1;
    setPackageState('idle');
  }, [packageWriteContext]);
  useEffect(() => {
    if (rawQuery === query) return;
    const next = new URLSearchParams(searchParams);
    if (query) next.set('answer_query', query);
    else next.delete('answer_query');
    void setSearchParams(next, { replace: true });
  }, [query, rawQuery, searchParams, setSearchParams]);
  useEffect(() => {
    const canonicalPage =
      (requestedPage === 1 && rawPage === '') || rawPage === String(requestedPage);
    if (rawCursor === answerCursor && canonicalPage) {
      return;
    }
    const next = new URLSearchParams(searchParams);
    if (answerCursor) next.set('answer_cursor', answerCursor);
    else next.delete('answer_cursor');
    if (requestedPage > 1) next.set('answer_page', String(requestedPage));
    else next.delete('answer_page');
    void setSearchParams(next, { replace: true });
  }, [answerCursor, rawCursor, rawPage, requestedPage, searchParams, setSearchParams]);
  useEffect(() => {
    const canonicalPage =
      (assetPage === 1 && rawAssetPage === '') || rawAssetPage === String(assetPage);
    if (rawAssetCursor === assetCursor && canonicalPage) return;
    const next = new URLSearchParams(searchParams);
    if (assetCursor) next.set('evidence_cursor', assetCursor);
    else next.delete('evidence_cursor');
    if (assetPage > 1) next.set('evidence_page', String(assetPage));
    else next.delete('evidence_page');
    void setSearchParams(next, { replace: true });
  }, [assetCursor, assetPage, rawAssetCursor, rawAssetPage, searchParams, setSearchParams]);
  const update = (key: string, value: string, fallback: string) => {
    const next = new URLSearchParams(searchParams);
    const safeValue =
      key === 'answer_query' && containsClientSecret(value) ? '' : value.slice(0, 80);
    if (safeValue === fallback || !safeValue) next.delete(key);
    else next.set(key, safeValue);
    if (
      ['answer_query', 'answer_model', 'answer_mode', 'answer_region'].includes(key) &&
      next.get(key) !== searchParams.get(key)
    ) {
      next.delete('answer_page');
      next.delete('answer_cursor');
      cursorByPage.current.clear();
    }
    void setSearchParams(next);
  };
  const createPackage = async () => {
    if (experience?.source !== 'live') {
      const writeTicket = packageWrite.beginFixture();
      if (!writeTicket) return;
      if (
        !downloadSafeGeneratedFile({
          kind: 'json',
          fileName: 'evidence-package-manifest.json',
          value: {
            version: '1.0',
            answers: filtered.map(({ id, question, model: answerModel, capturedAt }) => ({
              id,
              question,
              model: answerModel,
              capturedAt,
            })),
          },
        })
      ) {
        setPackageState('failed');
      }
      packageWrite.finish(writeTicket);
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setPackageState('forbidden');
      return;
    }
    if (
      effectiveLiveAssetState !== 'ready' ||
      !liveAssets.length ||
      liveAssetProjection.invalid ||
      liveAssetProjection.total > liveAssetProjection.shown
    ) {
      setPackageState('failed');
      return;
    }
    const writeTicket = packageWrite.begin(headers);
    if (!writeTicket) return;
    const requestId = ++packageRequest.current;
    const evidencePubIds = liveAssets.map((asset) => asset.id);
    setPackageState('saving');
    try {
      const result = await createEvidencePackage(
        {
          package_pub_id: createEvidencePackagePubId(),
          evidence_pub_ids: evidencePubIds,
          public: false,
          expires_at: null,
        },
        headers,
      );
      if (!packageWrite.isCurrent(writeTicket) || packageRequest.current !== requestId) return;
      setPackageState(
        result.kind === 'ready' ? 'saved' : result.kind === 'forbidden' ? 'forbidden' : 'failed',
      );
    } finally {
      packageWrite.finish(writeTicket);
    }
  };
  useEffect(() => {
    if (experience?.source !== 'live' || !experience.projectPubId) {
      setAnswerResultScope(answerReadScope);
      setLiveAnswers(null);
      setLiveAnswerProjection({ total: 0, shown: 0, invalid: false });
      setLiveAnswerState('fixture');
      return;
    }
    let cancelled = false;
    const commitAnswerState = (nextState: 'ready' | 'failed' | 'forbidden') => {
      if (cancelled) return;
      setAnswerResultScope(answerReadScope);
      setLiveAnswerState(nextState);
    };
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveAnswers(null);
      setLiveAnswerProjection({ total: 0, shown: 0, invalid: false });
      commitAnswerState('failed');
      return;
    }
    setLiveAnswerState('loading');
    void listAnalyticsAnswers(
      experience.projectPubId,
      {
        ...(answerIdQuery ? { answerPubId: answerIdQuery } : {}),
        ...(model !== 'all' ? { model } : {}),
        ...(mode !== 'all' ? { mode } : {}),
        ...(region !== 'all' ? { region } : {}),
        ...(!answerIdQuery && answerCursor ? { cursor: answerCursor } : {}),
        limit: 2,
      },
      headers,
    ).then((result) => {
      if (cancelled) return;
      if (result.kind !== 'ready') {
        setLiveAnswers(null);
        setLiveAnswerProjection({ total: 0, shown: 0, invalid: false });
        commitAnswerState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      const projectedAnswers = projectCustomerAnswerPage(result.data, experience.projectPubId);
      const pageProjection = isRecord(result.data.page) ? result.data.page : null;
      const nextCursor = pageProjection?.next_cursor;
      const safeNextCursor =
        pageProjection?.has_more === true &&
        typeof nextCursor === 'string' &&
        /^ans_[A-Za-z0-9_-]{1,116}$/.test(nextCursor) &&
        !containsClientSecret(nextCursor)
          ? nextCursor
          : '';
      const pageProjectionIsValid =
        pageProjection !== null &&
        typeof pageProjection.has_more === 'boolean' &&
        ((pageProjection.has_more === true && Boolean(safeNextCursor)) ||
          (pageProjection.has_more === false && nextCursor === null));
      setLiveAnswers(projectedAnswers.answers);
      setLiveAnswerProjection({
        total: result.data.projection.total,
        shown: projectedAnswers.answers.length,
        invalid:
          result.data.projection.invalid || projectedAnswers.invalid || !pageProjectionIsValid,
      });
      setLiveNextCursor(safeNextCursor);
      if (safeNextCursor) cursorByPage.current.set(requestedPage + 1, safeNextCursor);
      commitAnswerState('ready');
    });
    return () => {
      cancelled = true;
    };
  }, [
    answerCursor,
    answerIdQuery,
    answerReadScope,
    experience,
    model,
    mode,
    region,
    requestedPage,
    retryKey,
  ]);
  useEffect(() => {
    if (experience?.source !== 'live') {
      setAssetResultScope(assetReadScope);
      setLiveAssets([]);
      setLiveAssetProjection({ total: 0, shown: 0, invalid: false });
      setLiveAssetNextCursor('');
      setLiveAssetState('fixture');
      return;
    }
    let cancelled = false;
    const commitAssetState = (nextState: 'ready' | 'failed' | 'forbidden') => {
      if (cancelled) return;
      setAssetResultScope(assetReadScope);
      setLiveAssetState(nextState);
    };
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveAssets([]);
      setLiveAssetProjection({ total: 0, shown: 0, invalid: false });
      setLiveAssetNextCursor('');
      commitAssetState('forbidden');
      return;
    }
    setLiveAssetState('loading');
    void listEvidenceAssets(headers, {
      ...(assetCursor ? { cursor: assetCursor } : {}),
      limit: 2,
    }).then((result) => {
      if (cancelled) return;
      if (result.kind !== 'ready') {
        setLiveAssets([]);
        setLiveAssetProjection({ total: 0, shown: 0, invalid: false });
        setLiveAssetNextCursor('');
        commitAssetState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      const projectedAssets = projectEvidenceAssetPage(result.data);
      const pageProjection = isRecord(result.data.page) ? result.data.page : null;
      const nextCursor = pageProjection?.next_cursor;
      const safeNextCursor =
        pageProjection?.has_more === true &&
        typeof nextCursor === 'string' &&
        /^evd_[A-Za-z0-9_-]{1,116}$/.test(nextCursor) &&
        !containsClientSecret(nextCursor)
          ? nextCursor
          : '';
      const pageProjectionIsValid =
        pageProjection !== null &&
        typeof pageProjection.has_more === 'boolean' &&
        ((pageProjection.has_more === true && Boolean(safeNextCursor)) ||
          (pageProjection.has_more === false && nextCursor === null));
      setLiveAssets(projectedAssets.assets);
      setLiveAssetProjection({
        total: result.data.projection.total,
        shown: projectedAssets.assets.length,
        invalid:
          result.data.projection.invalid || projectedAssets.invalid || !pageProjectionIsValid,
      });
      setLiveAssetNextCursor(safeNextCursor);
      if (safeNextCursor) assetCursorByPage.current.set(assetPage + 1, safeNextCursor);
      commitAssetState('ready');
    });
    return () => {
      cancelled = true;
    };
  }, [assetCursor, assetPage, assetReadScope, assetRetryKey, experience]);
  const sourceAnswers =
    experience?.source === 'live'
      ? effectiveLiveAnswerState === 'ready'
        ? (liveAnswers ?? [])
        : []
      : answerFixtures;
  const filtered = sourceAnswers.filter(
    (answer) =>
      (model === 'all' || answer.model.toLowerCase() === model) &&
      (mode === 'all' || answer.mode === mode) &&
      (region === 'all' || answer.region === region) &&
      (!query || answer.id === query || answer.question.includes(query)),
  );
  const pageCount =
    experience?.source === 'live'
      ? Math.max(
          1,
          requestedPage + (effectiveLiveAnswerState === 'ready' && liveNextCursor ? 1 : 0),
        )
      : Math.max(1, Math.ceil(filtered.length / 2));
  const page = experience?.source === 'live' ? requestedPage : Math.min(requestedPage, pageCount);
  useEffect(() => {
    if (page === requestedPage) return;
    const next = new URLSearchParams(searchParams);
    if (page === 1) next.delete('answer_page');
    else next.set('answer_page', String(page));
    void setSearchParams(next, { replace: true });
  }, [page, requestedPage, searchParams, setSearchParams]);
  const rows = experience?.source === 'live' ? filtered : filtered.slice((page - 1) * 2, page * 2);
  const changePage = (nextPage: number) => {
    if (experience?.source !== 'live') {
      update('answer_page', String(nextPage), '1');
      return;
    }
    const next = new URLSearchParams(searchParams);
    const cursor =
      nextPage === page + 1 ? liveNextCursor : (cursorByPage.current.get(nextPage) ?? '');
    if (nextPage > 1 && cursor) {
      next.set('answer_page', String(nextPage));
      next.set('answer_cursor', cursor);
    } else {
      next.delete('answer_page');
      next.delete('answer_cursor');
    }
    void setSearchParams(next);
  };
  const assetPageCount = Math.max(
    1,
    assetPage + (effectiveLiveAssetState === 'ready' && liveAssetNextCursor ? 1 : 0),
  );
  const changeAssetPage = (nextPage: number) => {
    const next = new URLSearchParams(searchParams);
    const cursor =
      nextPage === assetPage + 1
        ? liveAssetNextCursor
        : (assetCursorByPage.current.get(nextPage) ?? '');
    if (nextPage > 1 && cursor) {
      next.set('evidence_page', String(nextPage));
      next.set('evidence_cursor', cursor);
    } else {
      next.delete('evidence_page');
      next.delete('evidence_cursor');
    }
    void setSearchParams(next);
  };
  return (
    <>
      <FilterBar label="回答筛选" className="filter-wrap">
        <label>
          搜索问题或 Answer ID
          <input
            aria-label="搜索问题或 Answer ID"
            value={query}
            onChange={(event) => update('answer_query', event.target.value, '')}
          />
        </label>
        <label>
          模型
          <select
            aria-label="回答模型"
            value={model}
            onChange={(event) => update('answer_model', event.target.value, 'all')}
          >
            <option value="all">全部模型</option>
            <option value="doubao">豆包</option>
            <option value="deepseek">DeepSeek</option>
            <option value="yuanbao">元宝</option>
          </select>
        </label>
        <label>
          模式
          <select
            aria-label="回答模式筛选"
            value={mode}
            onChange={(event) => update('answer_mode', event.target.value, 'all')}
          >
            <option value="all">全部模式</option>
            <option value="quick">快速</option>
            <option value="deep">深度思考</option>
          </select>
        </label>
        <label>
          地域
          <select
            aria-label="回答地域"
            value={region}
            onChange={(event) => update('answer_region', event.target.value, 'all')}
          >
            <option value="all">全部地域</option>
            <option>上海</option>
            <option>江苏</option>
            <option>北京</option>
            <option>广东</option>
          </select>
        </label>
        <span className="filter-summary">共 {filtered.length} 条回答 · 每页 2 条</span>
        <button
          className="button button-secondary"
          disabled={
            packageState === 'saving' ||
            (experience?.source === 'live' &&
              (effectiveLiveAssetState !== 'ready' ||
                !liveAssets.length ||
                liveAssetProjection.invalid ||
                liveAssetProjection.total > liveAssetProjection.shown))
          }
          onClick={() => void createPackage()}
        >
          {packageState === 'saving' ? '正在生成…' : '生成证据包'}
        </button>
      </FilterBar>
      {packageState === 'saved' ? <Toast>真实证据包已生成并冻结清单</Toast> : null}
      {packageState === 'failed' ? (
        <Toast tone="negative">证据不足或服务不可用；未生成本地伪造证据包。</Toast>
      ) : null}
      {packageState === 'forbidden' ? (
        <Toast tone="negative">无权生成证据包，且不会显示资产是否存在。</Toast>
      ) : null}
      {effectiveLiveAnswerState === 'loading' ? <StatePanel state="loading" /> : null}
      {effectiveLiveAnswerState === 'failed' ? <StatePanel state="failed" onRetry={retry} /> : null}
      {effectiveLiveAnswerState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
      {effectiveLiveAnswerState === 'ready' &&
      liveAnswerProjection.total > liveAnswerProjection.shown ? (
        <ProjectionLimitNotice
          items={[
            {
              key: 'customer-answer-page',
              label: '本页回答',
              total: liveAnswerProjection.total,
              shown: liveAnswerProjection.shown,
            },
          ]}
        />
      ) : null}
      {effectiveLiveAnswerState === 'ready' && liveAnswerProjection.invalid ? (
        <div className="confirmation projection-limit-notice" role="alert">
          <Badge tone="warning">安全投影不完整</Badge>
          <span>
            部分回答未通过身份、时间、计数或 DLP 校验；当前页不会把可见子集声称为完整结果。
          </span>
        </div>
      ) : null}
      <div className="answer-list">
        {effectiveLiveAnswerState === 'loading' ||
        effectiveLiveAnswerState === 'failed' ||
        effectiveLiveAnswerState === 'forbidden' ? null : rows.length ? (
          rows.map((answer) => (
            <article className="answer-card" key={answer.id}>
              <div className="answer-meta">
                <Badge tone={answer.mention ? 'positive' : 'neutral'}>
                  {answer.mention === null ? '尚未判断' : answer.mention ? '品牌已出现' : '未出现'}
                </Badge>
                <span>
                  {answer.model} ·{' '}
                  {answer.mode === 'deep'
                    ? '深度思考'
                    : answer.mode === 'quick'
                      ? '快速'
                      : answer.mode}{' '}
                  · {answer.region}
                </span>
                <time>{answer.capturedAt}</time>
              </div>
              <h2>{answer.question}</h2>
              <p className="panel-subtitle">
                Answer {answer.id}
                {experience?.source === 'live'
                  ? ` · 运行 ${answer.runId ?? '未关联'} · 冻结配置 ${answer.configVersionId ?? '未关联'}`
                  : ''}
              </p>
              <p>{answer.answer}</p>
              <div className="source-chips">
                {answer.cited.length ? (
                  answer.cited.map((source, index) => (
                    <button key={source} onClick={() => openEvidence(answer)}>
                      [{index + 1}] {source}
                    </button>
                  ))
                ) : (
                  <Badge tone="warning">无引用来源</Badge>
                )}
              </div>
              <div className="answer-actions">
                <button className="button button-secondary" onClick={() => openEvidence(answer)}>
                  查看回答截图
                </button>
                <button className="button button-secondary" onClick={() => openEvidence(answer)}>
                  历史 diff
                </button>
                <button className="button" onClick={() => openEvidence(answer)}>
                  打开证据中心
                </button>
              </div>
            </article>
          ))
        ) : (
          <StatePanel state="empty" />
        )}
      </div>
      <Pagination label="回答分页" page={page} pageCount={pageCount} onPageChange={changePage} />
      {experience?.source === 'live' ? (
        <section className="panel">
          <span className="overline">Evidence center</span>
          <h2>证据中心</h2>
          <p className="panel-subtitle">
            仅展示严格投影后的公开标识、类型、采集时间和哈希摘要；对象路径与原始载荷不会进入浏览器状态。
          </p>
          {effectiveLiveAssetState === 'loading' ? <StatePanel state="loading" /> : null}
          {effectiveLiveAssetState === 'failed' ? (
            <StatePanel state="failed" onRetry={retryAssets} />
          ) : null}
          {effectiveLiveAssetState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
          {effectiveLiveAssetState === 'ready' &&
          liveAssetProjection.total > liveAssetProjection.shown ? (
            <ProjectionLimitNotice
              items={[
                {
                  key: 'customer-evidence-assets',
                  label: '本页证据资产',
                  total: liveAssetProjection.total,
                  shown: liveAssetProjection.shown,
                },
              ]}
            />
          ) : null}
          {effectiveLiveAssetState === 'ready' && liveAssetProjection.invalid ? (
            <div className="confirmation projection-limit-notice" role="alert">
              <Badge tone="warning">安全投影不完整</Badge>
              <span>
                部分证据资产未通过安全校验；当前页不可用于生成证据包，也不会把可见子集声称为完整记录。
              </span>
            </div>
          ) : null}
          {effectiveLiveAssetState === 'ready' && liveAssets.length ? (
            <>
              <TableRegion label="证据中心资产">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>证据</th>
                      <th>类型</th>
                      <th>采集时间</th>
                      <th>完整性摘要</th>
                    </tr>
                  </thead>
                  <tbody>
                    {liveAssets.map((asset) => (
                      <tr key={asset.id}>
                        <td>{asset.id}</td>
                        <td>
                          {asset.kind} · {asset.mimeType}
                        </td>
                        <td>{asset.capturedAt}</td>
                        <td>{asset.integrity}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableRegion>
              <Pagination
                label="证据中心分页"
                page={assetPage}
                pageCount={assetPageCount}
                onPageChange={changeAssetPage}
              />
            </>
          ) : null}
          {effectiveLiveAssetState === 'ready' && liveAssets.length === 0 ? (
            <StatePanel state="empty" />
          ) : null}
        </section>
      ) : null}
      {effectiveSelected ? (
        <Dialog
          title="证据与历史差异"
          eyebrow="Evidence viewer"
          size="wide"
          closeLabel="关闭证据弹窗"
          onClose={closeEvidence}
        >
          {experience?.source === 'live' ? (
            <>
              {liveRelationState === 'loading' ? <StatePanel state="loading" /> : null}
              {liveRelationState === 'failed' ? (
                <StatePanel state="failed" onRetry={() => openEvidence(effectiveSelected)} />
              ) : null}
              {liveRelationState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
              {liveRelationState === 'ready' && liveRelations?.projectionNotices.length ? (
                <ProjectionLimitNotice items={liveRelations.projectionNotices} />
              ) : null}
              {liveRelationState === 'ready' && liveRelations?.invalidProjection.length ? (
                <div className="confirmation projection-limit-notice" role="alert">
                  <Badge tone="warning">安全投影不完整</Badge>
                  <span>
                    部分引用、证据、锚点或历史差异未通过安全校验；当前对话框不会把可见子集声称为完整记录。
                  </span>
                </div>
              ) : null}
              {liveRelationState === 'ready' && liveRelations?.citations.length ? (
                <TableRegion label="答案组织引用">
                  <table className="data-table">
                    <caption>
                      仅表示 AI 答案返回或组织时关联的引用；不等于页面提及目标品牌。
                    </caption>
                    <thead>
                      <tr>
                        <th>引用</th>
                        <th>来源</th>
                        <th>AI 返回的引用片段</th>
                        <th>内容哈希</th>
                      </tr>
                    </thead>
                    <tbody>
                      {liveRelations.citations.map((citation) => (
                        <tr key={citation.id}>
                          <td>{citation.label}</td>
                          <td>
                            <a href={citation.sourceUrl} target="_blank" rel="noreferrer noopener">
                              {citation.host}
                            </a>
                          </td>
                          <td>{citation.citedText ?? '未提取到引用片段'}</td>
                          <td>{citation.contentHash}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableRegion>
              ) : null}
              {liveRelationState === 'ready' && liveRelations?.evidence.length ? (
                <TableRegion label="回答关联证据">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>资产</th>
                        <th>类型</th>
                        <th>来源与锚点</th>
                        <th>完整性</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {liveRelations.evidence.map((asset) => {
                        const shareUrl =
                          asset.kind === 'share_link'
                            ? safeOfficialShareUrl(asset.sourceUrl, effectiveSelected.model)
                            : null;
                        return (
                          <tr key={asset.id}>
                            <td>{asset.id}</td>
                            <td>
                              {asset.kind} · {asset.mimeType} · {asset.relation}
                            </td>
                            <td>
                              {asset.sourceHost} · {asset.anchorCount} 个锚点 · {asset.capturedAt}
                            </td>
                            <td>{asset.integrity}</td>
                            <td>
                              {shareUrl ? (
                                <a href={shareUrl} target="_blank" rel="noreferrer noopener">
                                  打开官方分享链接
                                </a>
                              ) : asset.sourceUrl ? (
                                <a href={asset.sourceUrl} target="_blank" rel="noreferrer noopener">
                                  打开来源
                                </a>
                              ) : (
                                '—'
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </TableRegion>
              ) : null}
              {liveRelationState === 'ready' && liveRelations?.evidence.length ? (
                <LiveEvidencePurposeGallery
                  evidence={liveRelations.evidence}
                  platform={effectiveSelected.model}
                />
              ) : null}
              {liveRelationState === 'ready' && liveRelations?.history.length ? (
                <TableRegion label="证据历史差异">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>版本</th>
                        <th>相似度</th>
                        <th>视觉差异</th>
                      </tr>
                    </thead>
                    <tbody>
                      {liveRelations.history.map((diff) => (
                        <tr key={diff.id}>
                          <td>
                            {diff.beforeId} → {diff.afterId}
                          </td>
                          <td>
                            {diff.similarity === null
                              ? '未计算'
                              : `${(diff.similarity * 100).toFixed(1)}%`}
                          </td>
                          <td>{diff.visualAvailable ? '可用' : '不可用'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableRegion>
              ) : null}
              {liveRelationState === 'ready' &&
              !liveRelations?.citations.length &&
              !liveRelations?.evidence.length &&
              !liveRelations?.history.length ? (
                <StatePanel state="empty" />
              ) : null}
            </>
          ) : (
            <EvidenceViewer
              label={`${effectiveSelected.model} 回答截图，锚点高亮品牌提及`}
              anchor={{
                assetId: 'evd_01K0…A17',
                textStart: 48,
                textEnd: 73,
                bbox: [312, 184, 220, 46],
              }}
              previousText="仅支持云端部署"
              currentText="支持私有化部署与审计"
            >
              <Badge tone="positive">SHA-256 已校验</Badge>
            </EvidenceViewer>
          )}
        </Dialog>
      ) : null}
    </>
  );
}

function CustomerReportPdfPreview({
  reportPubId,
  versionPubId,
  integrity,
}: {
  reportPubId: string;
  versionPubId: string;
  integrity: ReportArtifactIntegrity;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [retryKey, retry] = useLocalRetry();
  const [state, setState] = useState<'loading' | 'ready' | 'failed' | 'forbidden'>('loading');
  useEffect(() => {
    let cancelled = false;
    let destroy: (() => Promise<void>) | undefined;
    let renderTask: { cancel: () => void; promise: Promise<unknown> } | undefined;
    clearSafePdfCanvas(canvasRef.current);
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setState('failed');
      return;
    }
    setState('loading');
    void getReportArtifact(reportPubId, versionPubId, 'pdf', integrity, headers).then(
      async (result) => {
        if (cancelled) return;
        if (result.kind !== 'ready') {
          clearSafePdfCanvas(canvasRef.current);
          setState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
          return;
        }
        try {
          const data = new Uint8Array(await result.data.blob.arrayBuffer());
          if (cancelled) return;
          const { getDocument, GlobalWorkerOptions } = await import('pdfjs-dist');
          if (cancelled) return;
          GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
          const task = getDocument({
            ...safePdfDocumentOptions,
            data,
          });
          destroy = () => task.destroy();
          const document = await task.promise;
          if (cancelled) return;
          const page = await document.getPage(1);
          const viewport = page.getViewport({ scale: 1.15 });
          const projectedViewport = projectSafePdfPageViewport({
            totalPages: document.numPages,
            pageNumber: 1,
            width: viewport.width,
            height: viewport.height,
          });
          if (!projectedViewport) throw new Error('PDF page exceeds browser preview limits');
          const canvas = canvasRef.current;
          const context = canvas?.getContext('2d');
          if (!canvas || !context || cancelled) return;
          canvas.width = projectedViewport.canvasWidth;
          canvas.height = projectedViewport.canvasHeight;
          renderTask = page.render({ canvas, canvasContext: context, viewport });
          await renderTask.promise;
          if (!cancelled) setState('ready');
        } catch {
          if (!cancelled) {
            clearSafePdfCanvas(canvasRef.current);
            setState('failed');
          }
        }
      },
    );
    return () => {
      cancelled = true;
      renderTask?.cancel();
      void destroy?.();
      clearSafePdfCanvas(canvasRef.current);
    };
  }, [integrity, reportPubId, retryKey, versionPubId]);
  return (
    <div className="pdf-canvas-wrap">
      {state === 'loading' ? <StatePanel state="loading" /> : null}
      {state === 'failed' ? <StatePanel state="failed" onRetry={retry} /> : null}
      {state === 'forbidden' ? <StatePanel state="forbidden" /> : null}
      <canvas ref={canvasRef} aria-hidden={state !== 'ready'} />
      <span className="sr-only" role="status">
        {state === 'ready' ? 'PDF.js 已渲染客户报告第一页' : '客户报告 PDF 尚未完成渲染'}
      </span>
    </div>
  );
}

function CustomerReportHtmlPreview({
  reportPubId,
  versionPubId,
  integrity,
}: {
  reportPubId: string;
  versionPubId: string;
  integrity: ReportArtifactIntegrity;
}) {
  const [retryKey, retry] = useLocalRetry();
  const [state, setState] = useState<'loading' | 'ready' | 'failed' | 'forbidden'>('loading');
  const [content, setContent] = useState<SafeHtmlDocumentProjection | null>(null);
  useEffect(() => {
    let cancelled = false;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setState('failed');
      return;
    }
    setContent(null);
    setState('loading');
    void getReportArtifact(reportPubId, versionPubId, 'html', integrity, headers).then(
      async (result) => {
        if (cancelled) return;
        if (result.kind !== 'ready') {
          setState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
          return;
        }
        const html = await result.data.blob.text();
        if (cancelled) return;
        const projection = projectSafeHtmlDocument(html);
        if (!projection) {
          setState('failed');
          return;
        }
        setContent(projection);
        setState('ready');
      },
    );
    return () => {
      cancelled = true;
    };
  }, [integrity, reportPubId, retryKey, versionPubId]);
  return (
    <div className="pdf-canvas-wrap">
      {state === 'loading' ? <StatePanel state="loading" /> : null}
      {state === 'failed' ? <StatePanel state="failed" onRetry={retry} /> : null}
      {state === 'forbidden' ? <StatePanel state="forbidden" /> : null}
      {state === 'ready' && content ? (
        <SafeHtmlDocument projection={content} label="客户报告在线预览" />
      ) : null}
      <span className="sr-only" role="status">
        {state === 'ready' ? '客户在线报告完整性已校验' : '客户在线报告尚未完成校验'}
      </span>
    </div>
  );
}

function CustomerReportPdfDownload({
  reportPubId,
  versionPubId,
  integrity,
}: {
  reportPubId: string;
  versionPubId: string;
  integrity: ReportArtifactIntegrity;
}) {
  return (
    <VerifiedBlobDownload
      fileName={`${reportPubId}-${versionPubId}.pdf`}
      resourceKey={createStructuredClientScopeKey([
        reportPubId,
        versionPubId,
        integrity.sha256,
        integrity.mimeType,
      ])}
      label="下载 PDF"
      failureLabel="报告制品完整性校验失败"
      successLabel="报告制品完整性校验通过并已下载"
      load={async () => {
        const headers = getValidatedIdentityHeaders();
        if (!headers) return { kind: 'forbidden' };
        const result = await getReportArtifact(
          reportPubId,
          versionPubId,
          'pdf',
          integrity,
          headers,
        );
        return result.kind === 'ready'
          ? { kind: 'ready', blob: result.data.blob }
          : result.kind === 'forbidden'
            ? { kind: 'forbidden' }
            : { kind: 'unavailable' };
      }}
    />
  );
}

const reportQuestionSchema = z.object({
  question: z
    .string()
    .trim()
    .min(6, '问题至少需要 6 个字')
    .max(2000, '问题不能超过 2000 个字')
    .refine(noClientSecret, noClientSecretMessage),
});
type ReportQuestionFields = z.infer<typeof reportQuestionSchema>;
type ReportQuestionReconciliation = {
  reportId: string;
  projectId: string;
  versionId: string;
  commentId: string;
  authorId: string;
  body: string;
};

function ReportsWorkspace() {
  const experience = useOptionalExperienceContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const [retryKey, retry] = useLocalRetry();
  const reportRequestGeneration = useRef(0);
  const [questions, setQuestions] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [liveDelivery, setLiveDelivery] = useState<SafeReportDelivery | null>(null);
  const [liveDeliveryProjection, setLiveDeliveryProjection] =
    useState<SafeReportDeliveryProjection>({
      data: [],
      total: 0,
      shown: 0,
      invalid: false,
    });
  const [confirmationState, setConfirmationState] = useState<
    'idle' | 'saving' | 'saved' | 'delayed' | 'failed' | 'forbidden'
  >('idle');
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewFormat, setPreviewFormat] = useState<'html' | 'pdf'>('html');
  const [livePage, setLivePage] = useState<ReportPageProjection | null>(null);
  const [liveCatalogProjection, setLiveCatalogProjection] =
    useState<ProjectReportCatalogProjection>({
      total: 0,
      shown: 0,
      scanned: 0,
      invalid: false,
      incomplete: false,
    });
  const [liveProjectionIssue, setLiveProjectionIssue] = useState<'catalog' | 'detail' | null>(null);
  const [liveVersionId, setLiveVersionId] = useState('');
  const [liveArtifacts, setLiveArtifacts] = useState<SafeCustomerReportArtifact[]>([]);
  const liveArtifactFormats = liveArtifacts.map((artifact) => artifact.format);
  const livePdfArtifact = liveArtifacts.find((artifact) => artifact.format === 'pdf');
  const liveHtmlArtifact = liveArtifacts.find((artifact) => artifact.format === 'html');
  const [liveVersions, setLiveVersions] = useState<
    { id: string; versionNumber: number; status: string }[]
  >([]);
  const [liveVersionProjection, setLiveVersionProjection] = useState<{
    total: number;
    shown: number;
    invalid: boolean;
  }>({ total: 0, shown: 0, invalid: false });
  const [liveArtifactProjection, setLiveArtifactProjection] = useState<{
    total: number;
    shown: number;
    invalid: boolean;
  }>({ total: 0, shown: 0, invalid: false });
  const [liveNextCursor, setLiveNextCursor] = useState('');
  const cursorByPage = useRef(new Map<number, string>());
  const rawPage = searchParams.get('report_page') ?? '';
  const parsedPage = /^[1-9]\d{0,2}$/.test(rawPage) ? Number(rawPage) : 1;
  const rawCursor = searchParams.get('report_cursor') ?? '';
  const reportCursor =
    /^rpt_[A-Za-z0-9_-]{1,116}$/.test(rawCursor) && !containsClientSecret(rawCursor)
      ? rawCursor
      : '';
  const requestedPage =
    experience?.source === 'live' && parsedPage > 1 && !reportCursor ? 1 : parsedPage;
  const reportReadScope = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    String(retryKey),
    String(requestedPage),
    reportCursor,
  ]);
  const [questionState, setQuestionState] = useState<
    'idle' | 'saving' | 'saved' | 'failed' | 'forbidden'
  >('idle');
  const [pendingQuestionReconciliation, setPendingQuestionReconciliation] =
    useState<ReportQuestionReconciliation | null>(null);
  const [questionReconciliationState, setQuestionReconciliationState] = useState<
    'idle' | 'reading' | 'failed'
  >('idle');
  const {
    register: registerQuestion,
    handleSubmit: handleQuestionSubmit,
    reset: resetQuestion,
    formState: { errors: questionErrors, isValid: questionIsValid },
  } = useForm<ReportQuestionFields>({
    resolver: zodResolver(reportQuestionSchema),
    defaultValues: { question: '' },
    mode: 'onChange',
  });
  const [liveState, setLiveState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const [liveResultScope, setLiveResultScope] = useState(
    experience?.source === 'live' ? '' : reportReadScope,
  );
  const reportWriteContext = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    String(retryKey),
    String(requestedPage),
    reportCursor,
    livePage?.data[0]?.pub_id ?? '',
    liveVersionId,
    liveDelivery?.id ?? '',
  ]);
  const reportWrite = useCustomerMutationGuard(reportWriteContext);
  const [reportMutationPending, setReportMutationPending] = useState(false);
  const reportMutationBusy = reportMutationPending && reportWrite.isActive();
  const reportMutationLocked = reportMutationBusy || pendingQuestionReconciliation !== null;
  useEffect(() => {
    setReportMutationPending(false);
    setPendingQuestionReconciliation(null);
    setQuestionReconciliationState('idle');
  }, [reportWriteContext]);
  useEffect(() => {
    if (experience?.source !== 'live') return;
    const canonicalPage =
      (requestedPage === 1 && rawPage === '') || rawPage === String(requestedPage);
    if (rawCursor === reportCursor && canonicalPage) return;
    const next = new URLSearchParams(searchParams);
    if (reportCursor) next.set('report_cursor', reportCursor);
    else next.delete('report_cursor');
    if (requestedPage > 1) next.set('report_page', String(requestedPage));
    else next.delete('report_page');
    void setSearchParams(next, { replace: true });
  }, [experience, rawCursor, rawPage, reportCursor, requestedPage, searchParams, setSearchParams]);
  useEffect(() => {
    if (experience?.source !== 'live') {
      setLiveResultScope(reportReadScope);
      setLiveState('fixture');
      return;
    }
    const requestGeneration = ++reportRequestGeneration.current;
    let cancelled = false;
    const superseded = () => cancelled || reportRequestGeneration.current !== requestGeneration;
    const commitLiveState = (state: 'ready' | 'failed' | 'forbidden') => {
      if (superseded()) return;
      setLiveResultScope(reportReadScope);
      setLiveState(state);
    };
    const headers = getValidatedIdentityHeaders();
    const projectPubId = experience.projectPubId;
    if (!headers || !projectPubId) {
      commitLiveState('failed');
      return;
    }
    setLiveState('loading');
    setLivePage(null);
    setLiveCatalogProjection({
      total: 0,
      shown: 0,
      scanned: 0,
      invalid: false,
      incomplete: false,
    });
    setLiveProjectionIssue(null);
    setLiveVersionId('');
    setLiveArtifacts([]);
    setLiveVersions([]);
    setLiveVersionProjection({ total: 0, shown: 0, invalid: false });
    setLiveArtifactProjection({ total: 0, shown: 0, invalid: false });
    setLiveDelivery(null);
    setLiveDeliveryProjection({ data: [], total: 0, shown: 0, invalid: false });
    setPreviewOpen(false);
    setQuestionState('idle');
    setConfirmationState('idle');
    setQuestions([]);
    if (requestedPage === 1) cursorByPage.current.clear();
    void loadProjectReportCatalog(headers, projectPubId, reportCursor).then(async (result) => {
      if (superseded()) return;
      if (result.kind === 'ready') {
        setLiveCatalogProjection(result.projection);
        if (result.projection.invalid) {
          setLiveProjectionIssue('catalog');
          setLivePage(null);
          setLiveNextCursor('');
          commitLiveState('ready');
          return;
        }
        setLivePage(result.page);
        setLiveNextCursor(result.nextCursor);
        if (result.nextCursor) cursorByPage.current.set(requestedPage + 1, result.nextCursor);
        const reportPubId = result.page.data[0]?.pub_id;
        if (reportPubId) {
          const detail = await getReport(reportPubId, headers);
          if (superseded()) return;
          if (detail.kind !== 'ready') {
            if (detail.kind === 'invalid') {
              setLiveProjectionIssue('detail');
              setLiveVersionId('');
              setLiveArtifacts([]);
              setLiveVersions([]);
              setLiveVersionProjection({ total: 0, shown: 0, invalid: true });
              setLiveArtifactProjection({ total: 0, shown: 0, invalid: true });
              setLiveDelivery(null);
              commitLiveState('ready');
              return;
            }
            setLiveVersionId('');
            commitLiveState(detail.kind === 'forbidden' ? 'forbidden' : 'failed');
            return;
          }
          if (!projectReportDetailIdentity(detail.data, reportPubId, projectPubId)) {
            setLiveProjectionIssue('detail');
            setLiveVersionId('');
            setLiveArtifacts([]);
            setLiveVersions([]);
            setLiveVersionProjection({ total: 0, shown: 0, invalid: true });
            setLiveArtifactProjection({ total: 0, shown: 0, invalid: true });
            setLiveDelivery(null);
            commitLiveState('ready');
            return;
          }
          const versionProjection = projectCustomerReportVersions(
            detail.data.versions,
            detail.data.projection,
          );
          setLiveVersions(versionProjection.versions);
          setLiveVersionProjection({
            total: versionProjection.versionTotal,
            shown: versionProjection.versionShown,
            invalid: versionProjection.invalidVersions,
          });
          setLiveArtifactProjection({
            total: versionProjection.artifactTotal,
            shown: versionProjection.artifactShown,
            invalid: versionProjection.invalidArtifacts,
          });
          setLiveVersionId(versionProjection.currentVersionId);
          setLiveArtifacts(versionProjection.artifacts);
          if (versionProjection.invalidVersions || versionProjection.invalidArtifacts) {
            setLiveDelivery(null);
            setLiveDeliveryProjection({ data: [], total: 0, shown: 0, invalid: false });
            commitLiveState('ready');
            return;
          }
          const deliveries = await listReportDeliveries(reportPubId, headers);
          if (superseded()) return;
          if (deliveries.kind === 'ready') {
            const deliveryProjection = mergeReportDeliveryProjection(
              projectReportDeliveryViews(deliveries.data.data, reportPubId, experience.userPubId),
              deliveries.data,
            );
            setLiveDeliveryProjection(deliveryProjection);
            setLiveDelivery(
              deliveryProjection.invalid ? null : (deliveryProjection.data[0] ?? null),
            );
          } else if (deliveries.kind === 'forbidden') {
            commitLiveState('forbidden');
            return;
          } else {
            commitLiveState('failed');
            return;
          }
        }
        commitLiveState('ready');
      } else {
        setLivePage(null);
        setLiveCatalogProjection({
          total: 0,
          shown: 0,
          scanned: 0,
          invalid: false,
          incomplete: false,
        });
        setLiveProjectionIssue(null);
        setLiveNextCursor('');
        setLiveVersionId('');
        setLiveArtifacts([]);
        setLiveVersions([]);
        setLiveVersionProjection({ total: 0, shown: 0, invalid: false });
        setLiveArtifactProjection({ total: 0, shown: 0, invalid: false });
        setLiveDelivery(null);
        setLiveDeliveryProjection({ data: [], total: 0, shown: 0, invalid: false });
        commitLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [experience, reportCursor, reportReadScope, requestedPage, retryKey]);
  const retryReports = () => {
    reportRequestGeneration.current += 1;
    retry();
  };
  const effectiveLiveState =
    experience?.source === 'live' && liveResultScope !== reportReadScope ? 'loading' : liveState;
  if (effectiveLiveState === 'loading') return <StatePanel state="loading" />;
  if (effectiveLiveState === 'failed') return <StatePanel state="failed" onRetry={retryReports} />;
  if (effectiveLiveState === 'forbidden') return <StatePanel state="forbidden" />;
  if (effectiveLiveState === 'ready' && liveProjectionIssue) {
    return (
      <section className="panel" aria-labelledby="customer-report-projection-title">
        <span className="overline">Report safety boundary</span>
        <h2 id="customer-report-projection-title">
          {liveProjectionIssue === 'catalog' ? '报告目录投影已拒绝' : '报告详情投影已拒绝'}
        </h2>
        <div className="confirmation projection-limit-notice" role="alert">
          <Badge tone="warning">安全投影不完整</Badge>
          <span>
            {liveProjectionIssue === 'catalog'
              ? '目录包含无效、重复、越界或游标不一致记录；系统未探测任何报告详情。'
              : '详情与所请求报告或当前项目不一致；预览、提问、确认和制品访问均已锁定。'}
          </span>
        </div>
        {liveCatalogProjection.total > liveCatalogProjection.shown ? (
          <ProjectionLimitNotice
            items={[
              {
                key: 'customer-report-catalog-rejected',
                label: '报告目录',
                total: liveCatalogProjection.total,
                shown: liveCatalogProjection.shown,
              },
            ]}
          />
        ) : null}
        <StatePanel state="failed" onRetry={retryReports} />
      </section>
    );
  }
  const liveReport = effectiveLiveState === 'ready' ? livePage?.data[0] : undefined;
  const reportPageCount =
    experience?.source === 'live' ? Math.max(1, requestedPage + (liveNextCursor ? 1 : 0)) : 1;
  const changeReportPage = (nextPage: number) => {
    if (experience?.source !== 'live') return;
    const next = new URLSearchParams(searchParams);
    const cursor =
      nextPage === requestedPage + 1 ? liveNextCursor : (cursorByPage.current.get(nextPage) ?? '');
    if (nextPage > 1 && cursor) {
      next.set('report_page', String(nextPage));
      next.set('report_cursor', cursor);
    } else {
      next.delete('report_page');
      next.delete('report_cursor');
    }
    reportRequestGeneration.current += 1;
    void setSearchParams(next);
  };
  if (effectiveLiveState === 'ready' && livePage?.data.length === 0) {
    if (!liveCatalogProjection.incomplete) return <StatePanel state="empty" />;
    return (
      <section className="panel" aria-labelledby="customer-report-catalog-continuation-title">
        <span className="overline">Published report</span>
        <h2 id="customer-report-catalog-continuation-title">当前扫描窗口没有项目报告</h2>
        <div className="confirmation projection-limit-notice" role="status">
          <Badge tone="warning">项目目录仍在后续页</Badge>
          <span>
            已安全扫描 {liveCatalogProjection.scanned}{' '}
            条租户报告但尚未确认下一份当前项目报告；可用分页继续扫描，且不会据此推断项目报告总数。
          </span>
        </div>
        <Pagination
          label="报告分页"
          page={requestedPage}
          pageCount={reportPageCount}
          onPageChange={changeReportPage}
        />
      </section>
    );
  }
  const safeTitle =
    liveReport && liveReport.title.length <= 240 && !containsClientSecret(liveReport.title)
      ? liveReport.title
      : '未命名报告';
  const safeState =
    liveReport && liveReport.state.length <= 80 && !containsClientSecret(liveReport.state)
      ? liveReport.state
      : 'unknown';
  const safeReportId =
    liveReport && liveReport.pub_id.length <= 120 && !containsClientSecret(liveReport.pub_id)
      ? liveReport.pub_id
      : '报告标识已隐藏';
  const safeUpdatedAt =
    liveReport &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(liveReport.updated_at) &&
    !containsClientSecret(liveReport.updated_at)
      ? liveReport.updated_at.slice(0, 16).replace('T', ' ')
      : '更新时间已隐藏';
  const questionAuthorityConfirms = (
    detail: ReportDetailProjection,
    expected: ReportQuestionReconciliation,
  ): boolean => {
    if (
      !projectReportDetailIdentity(detail, expected.reportId, expected.projectId) ||
      detail.projection.versions.invalid ||
      detail.projection.versions.total !== detail.projection.versions.shown
    ) {
      return false;
    }
    const version = detail.versions.find((candidate) => candidate.pub_id === expected.versionId);
    const commentProjection =
      detail.projection.version_collections[expected.versionId]?.comments ?? null;
    if (
      !version ||
      !commentProjection ||
      commentProjection.invalid ||
      commentProjection.total !== commentProjection.shown
    ) {
      return false;
    }
    return version.comments.some(
      (comment) =>
        comment.pub_id === expected.commentId &&
        comment.report_version_pub_id === expected.versionId &&
        comment.parent_pub_id === null &&
        comment.author_pub_id === expected.authorId &&
        comment.body === expected.body,
    );
  };
  const reconcileQuestion = async (
    expected: ReportQuestionReconciliation,
    writeTicket: CustomerAccountMutationTicket,
    requestGeneration: number,
  ) => {
    const headers = getValidatedIdentityHeaders();
    const detail = headers ? await getReport(expected.reportId, headers) : null;
    if (
      !reportWrite.isCurrent(writeTicket) ||
      reportRequestGeneration.current !== requestGeneration
    ) {
      reportWrite.finish(writeTicket);
      return false;
    }
    if (!detail || detail.kind !== 'ready' || !questionAuthorityConfirms(detail.data, expected)) {
      reportWrite.finish(writeTicket);
      setPendingQuestionReconciliation(expected);
      setQuestionReconciliationState('failed');
      setQuestionState(detail?.kind === 'forbidden' ? 'forbidden' : 'failed');
      return false;
    }
    if (!reportWrite.finish(writeTicket)) return false;
    setPendingQuestionReconciliation(null);
    setQuestionReconciliationState('idle');
    setQuestionState('saved');
    setQuestions((current) => [expected.body, ...current]);
    resetQuestion();
    return true;
  };
  const retryQuestionReconciliation = async () => {
    if (!pendingQuestionReconciliation) return;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setQuestionState('forbidden');
      setQuestionReconciliationState('failed');
      return;
    }
    const writeTicket = reportWrite.begin(headers);
    if (!writeTicket) return;
    const requestGeneration = reportRequestGeneration.current;
    setQuestionState('saving');
    setQuestionReconciliationState('reading');
    setReportMutationPending(true);
    try {
      await reconcileQuestion(pendingQuestionReconciliation, writeTicket, requestGeneration);
    } finally {
      reportWrite.finish(writeTicket);
      setReportMutationPending(reportWrite.isActive());
    }
  };
  const submitQuestion = handleQuestionSubmit(async ({ question }) => {
    if (liveReport) {
      const requestGeneration = reportRequestGeneration.current;
      const reportPubId = liveReport.pub_id;
      const versionPubId = liveVersionId;
      const projectPubId = experience?.projectPubId;
      const authorPubId = experience?.userPubId;
      const headers = getValidatedIdentityHeaders();
      if (!headers || !versionPubId || !projectPubId || !authorPubId) {
        setQuestionState('failed');
        return;
      }
      const writeTicket = reportWrite.begin(headers);
      if (!writeTicket) return;
      setReportMutationPending(true);
      setQuestionState('saving');
      try {
        const result = await commentOnReport(
          reportPubId,
          versionPubId,
          { body: question, parent_pub_id: null },
          headers,
        );
        if (
          !reportWrite.isCurrent(writeTicket) ||
          reportRequestGeneration.current !== requestGeneration
        ) {
          return;
        }
        if (result.kind !== 'ready') {
          setQuestionState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
          return;
        }
        const expected: ReportQuestionReconciliation = {
          reportId: result.data.reportPubId,
          projectId: projectPubId,
          versionId: versionPubId,
          commentId: result.data.commentPubId,
          authorId: authorPubId,
          body: question,
        };
        setPendingQuestionReconciliation(expected);
        setQuestionReconciliationState('reading');
        await reconcileQuestion(expected, writeTicket, requestGeneration);
      } finally {
        reportWrite.finish(writeTicket);
        setReportMutationPending(reportWrite.isActive());
      }
      return;
    }
    const writeTicket = reportWrite.beginFixture();
    if (!writeTicket) return;
    setQuestions((current) => [question, ...current]);
    resetQuestion();
    reportWrite.finish(writeTicket);
  });
  const confirmReceipt = async () => {
    if (!liveReport || !liveDelivery || !experience?.userPubId) return;
    const requestGeneration = reportRequestGeneration.current;
    const reportPubId = liveReport.pub_id;
    const deliveryPubId = liveDelivery.id;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setConfirmationState('failed');
      return;
    }
    const writeTicket = reportWrite.begin(headers);
    if (!writeTicket) return;
    setReportMutationPending(true);
    setConfirmationState('saving');
    try {
      const result = await confirmReportDelivery(
        reportPubId,
        deliveryPubId,
        { confirmation_comment: '客户确认已收到此报告版本' },
        headers,
      );
      if (
        !reportWrite.isCurrent(writeTicket) ||
        reportRequestGeneration.current !== requestGeneration
      ) {
        return;
      }
      if (result.kind !== 'ready') {
        setConfirmationState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      const refreshed = await listReportDeliveries(reportPubId, headers);
      if (
        !reportWrite.isCurrent(writeTicket) ||
        reportRequestGeneration.current !== requestGeneration
      ) {
        return;
      }
      if (refreshed.kind !== 'ready') {
        setConfirmationState(refreshed.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      const projected = mergeReportDeliveryProjection(
        projectReportDeliveryViews(refreshed.data.data, reportPubId, experience.userPubId),
        refreshed.data,
      );
      setLiveDeliveryProjection(projected);
      const delivery = projected.invalid ? null : (projected.data[0] ?? null);
      setLiveDelivery(delivery);
      if (projected.invalid) {
        setConfirmationState('failed');
        return;
      }
      if (!delivery?.confirmedAt) {
        setConfirmationState('delayed');
        return;
      }
      setConfirmationState('saved');
    } finally {
      reportWrite.finish(writeTicket);
      setReportMutationPending(reportWrite.isActive());
    }
  };
  return (
    <>
      <section className="panel report-feature">
        <div>
          <span className="overline">Published report</span>
          <h2>{liveReport ? safeTitle : '2026 Q3 GEO 监测与优化建议'}</h2>
          <p>
            {liveReport
              ? `${safeReportId} · 更新于 ${safeUpdatedAt}`
              : '覆盖窗口 2026-07-01—2026-07-21 · 发布版本 v1.2 · 文件 hash 已记录'}
          </p>
          <div className="scope-row">
            <Badge tone={safeState === 'published' ? 'positive' : 'warning'}>
              {liveReport ? safeState : '已发布'}
            </Badge>
            {liveReport ? <Badge tone="positive">真实 reports API</Badge> : null}
            {!liveReport || liveArtifactFormats.includes('pdf') ? <Badge>PDF</Badge> : null}
            {!liveReport || liveArtifactFormats.includes('html') ? <Badge>在线版</Badge> : null}
          </div>
        </div>
        <div className="report-actions">
          <button
            className="button button-secondary"
            disabled={Boolean(liveReport) && !liveArtifactFormats.includes('html')}
            title={
              liveReport && !liveArtifactFormats.includes('html')
                ? '此版本没有可用在线制品'
                : undefined
            }
            onClick={() => {
              setPreviewFormat('html');
              setPreviewOpen(true);
            }}
          >
            在线预览
          </button>
          <button
            className="button button-secondary"
            disabled={!liveReport || !liveArtifactFormats.includes('pdf')}
            title={
              liveReport && !liveArtifactFormats.includes('pdf')
                ? '此版本没有可用 PDF 制品'
                : undefined
            }
            onClick={() => {
              setPreviewFormat('pdf');
              setPreviewOpen(true);
            }}
          >
            {liveReport && liveArtifactFormats.includes('pdf') ? '打开 PDF' : 'PDF 正在生成'}
          </button>
          {liveReport && liveVersionId && livePdfArtifact ? (
            <CustomerReportPdfDownload
              reportPubId={liveReport.pub_id}
              versionPubId={liveVersionId}
              integrity={livePdfArtifact}
            />
          ) : liveReport ? (
            <button className="button button-secondary" disabled>
              下载 PDF
            </button>
          ) : null}
          <button
            className="button"
            onClick={() =>
              liveReport
                ? navigateCustomerSection('monitoring')
                : downloadSafeGeneratedFile({
                    kind: 'csv',
                    fileName: 'geo-report-data.csv',
                    content:
                      'metric,value,numerator,denominator\nmention_rate,0.684,26,38\ntop3_rate,0.737,28,38\n',
                  })
            }
          >
            {liveReport ? '前往监测导出' : '导出筛选数据'}
          </button>
        </div>
      </section>
      {experience?.source === 'live' ? (
        <>
          {liveCatalogProjection.total > liveCatalogProjection.shown ? (
            <ProjectionLimitNotice
              items={[
                {
                  key: 'customer-report-catalog',
                  label: '报告目录',
                  total: liveCatalogProjection.total,
                  shown: liveCatalogProjection.shown,
                },
              ]}
            />
          ) : null}
          {liveCatalogProjection.incomplete ? (
            <div className="confirmation projection-limit-notice" role="status">
              <Badge tone="warning">项目目录仍在后续页</Badge>
              <span>
                已安全扫描 {liveCatalogProjection.scanned}{' '}
                条租户报告但尚未确认下一份当前项目报告；可用分页继续扫描，且不会据此推断项目报告总数。
              </span>
            </div>
          ) : null}
          <Pagination
            label="报告分页"
            page={requestedPage}
            pageCount={reportPageCount}
            onPageChange={changeReportPage}
          />
        </>
      ) : null}
      <div className="two-column">
        <section className="panel">
          <h2>向报告提问</h2>
          <p className="panel-subtitle">问题与报告版本绑定，回答不会静默改写已发布报告。</p>
          <form onSubmit={(event) => void submitQuestion(event)} noValidate>
            <label className="form-field" htmlFor="report-question">
              <span>问题</span>
              <textarea
                id="report-question"
                rows={4}
                {...registerQuestion('question')}
                aria-invalid={Boolean(questionErrors.question)}
                aria-describedby={questionErrors.question ? 'report-question-error' : undefined}
              />
              {questionErrors.question ? (
                <span id="report-question-error" className="field-error" role="alert">
                  {questionErrors.question.message}
                </span>
              ) : null}
            </label>
            <div className="form-actions">
              <span>{questions.length} 个问题</span>
              <button
                type="submit"
                className="button"
                disabled={
                  !questionIsValid ||
                  questionState === 'saving' ||
                  reportMutationLocked ||
                  Boolean(liveReport && !liveVersionId)
                }
                title={
                  liveReport && !liveVersionId ? '报告版本或制品投影不完整，提问已锁定' : undefined
                }
              >
                {questionState === 'saving' ? '正在提交…' : '提交问题'}
              </button>
            </div>
          </form>
          {questionReconciliationState === 'reading' && pendingQuestionReconciliation ? (
            <div className="confirmation" role="status">
              <Badge tone="warning">正在核对</Badge>
              <span>写入已接受，正在重新读取权威报告评论投影。</span>
            </div>
          ) : null}
          {questionState === 'saved' ? <Toast>问题已写入真实报告版本评论</Toast> : null}
          {questionState === 'failed' && pendingQuestionReconciliation ? (
            <StatePanel state="failed" onRetry={() => void retryQuestionReconciliation()} />
          ) : questionState === 'failed' ? (
            <StatePanel state="failed" />
          ) : null}
          {questionState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
          {questions.map((item) => (
            <article className="question-thread" key={item}>
              <Badge tone="warning">等待报告团队</Badge>
              <p>{item}</p>
            </article>
          ))}
        </section>
        <section className="panel">
          <h2>客户确认</h2>
          <p className="panel-subtitle">确认仅表示已接收此版本，不代表认可所有建议。</p>
          {liveReport && liveDeliveryProjection.total > liveDeliveryProjection.shown ? (
            <ProjectionLimitNotice
              items={[
                {
                  key: 'customer-report-deliveries',
                  label: '当前客户交付记录',
                  total: liveDeliveryProjection.total,
                  shown: liveDeliveryProjection.shown,
                },
              ]}
            />
          ) : null}
          {liveReport && liveDeliveryProjection.invalid ? (
            <>
              <div className="confirmation projection-limit-notice" role="alert">
                <Badge tone="warning">交付投影不完整</Badge>
                <span>
                  返回记录未同时绑定当前报告与当前客户，或存在重复/倒序事实；接收确认已锁定。
                </span>
              </div>
              <StatePanel state="failed" onRetry={retryReports} />
            </>
          ) : liveReport && liveDelivery?.confirmedAt ? (
            <div className="confirmation" role="status">
              <Badge tone="positive">已确认接收</Badge>
              <span>确认事件已由真实 delivery API 写入审计事实</span>
            </div>
          ) : liveReport && liveDelivery ? (
            <>
              <button
                className="button"
                disabled={confirmationState === 'saving' || reportMutationLocked}
                onClick={() => void confirmReceipt()}
              >
                {confirmationState === 'saving' ? '正在确认…' : '确认收到此报告'}
              </button>
              {confirmationState === 'failed' ? <StatePanel state="failed" /> : null}
              {confirmationState === 'delayed' ? <StatePanel state="delayed" /> : null}
              {confirmationState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
            </>
          ) : liveReport ? (
            <StatePanel state="insufficient" />
          ) : confirmed ? (
            <div className="confirmation" role="status">
              <Badge tone="positive">已确认接收 v1.2</Badge>
              <span>确认事件已写入审计</span>
            </div>
          ) : (
            <button className="button" onClick={() => setConfirmed(true)}>
              确认收到 v1.2
            </button>
          )}
          <h3>历史版本</h3>
          {liveReport && liveVersionProjection.total > 100 ? (
            <ProjectionLimitNotice
              items={[
                {
                  key: 'customer-report-versions',
                  label: '报告版本',
                  total: liveVersionProjection.total,
                  shown: liveVersionProjection.shown,
                },
              ]}
            />
          ) : null}
          {liveReport && liveArtifactProjection.total > liveArtifactProjection.shown ? (
            <ProjectionLimitNotice
              items={[
                {
                  key: 'customer-report-artifacts',
                  label: '当前版本制品',
                  total: liveArtifactProjection.total,
                  shown: liveArtifactProjection.shown,
                },
              ]}
            />
          ) : null}
          {liveReport && liveVersionProjection.invalid ? (
            <div className="confirmation projection-limit-notice" role="alert">
              <Badge tone="warning">安全投影不完整</Badge>
              <span>
                部分报告版本未通过安全校验；当前版本预览已锁定，且不会把可见历史声称为完整记录。
              </span>
            </div>
          ) : null}
          {liveReport && liveArtifactProjection.invalid ? (
            <div className="confirmation projection-limit-notice" role="alert">
              <Badge tone="warning">制品投影不完整</Badge>
              <span>
                制品未绑定当前版本、格式重复或完整性字段无效；预览、提问与下游交付读取已锁定。
              </span>
            </div>
          ) : null}
          <ul className="version-list">
            {liveReport ? (
              liveVersions.length ? (
                liveVersions.map((version, index) => (
                  <li key={version.id}>
                    v{version.versionNumber} · {version.status}
                    {index === 0 ? ' · 当前版本' : ''}
                  </li>
                ))
              ) : (
                <li>版本事实不足</li>
              )
            ) : (
              <>
                <li>v1.2 · 当前发布</li>
                <li>v1.1 · 已撤回并保留审计</li>
                <li>v1.0 · 首次发布</li>
              </>
            )}
          </ul>
        </section>
      </div>
      {previewOpen ? (
        <Dialog
          title={liveReport ? safeTitle : '2026 Q3 GEO 监测与优化建议'}
          eyebrow={
            liveReport ? `Published artifact · ${previewFormat}` : 'Published online report · v1.2'
          }
          closeLabel="关闭在线报告预览"
          onClose={() => setPreviewOpen(false)}
        >
          {liveReport && liveVersionId && previewFormat === 'pdf' ? (
            livePdfArtifact ? (
              <CustomerReportPdfPreview
                key={createStructuredClientScopeKey([
                  liveReport.pub_id,
                  liveVersionId,
                  'pdf',
                  livePdfArtifact.sha256,
                  livePdfArtifact.mimeType,
                ])}
                reportPubId={liveReport.pub_id}
                versionPubId={liveVersionId}
                integrity={livePdfArtifact}
              />
            ) : (
              <StatePanel state="failed" />
            )
          ) : liveReport && liveVersionId ? (
            liveHtmlArtifact ? (
              <CustomerReportHtmlPreview
                key={createStructuredClientScopeKey([
                  liveReport.pub_id,
                  liveVersionId,
                  'html',
                  liveHtmlArtifact.sha256,
                  liveHtmlArtifact.mimeType,
                ])}
                reportPubId={liveReport.pub_id}
                versionPubId={liveVersionId}
                integrity={liveHtmlArtifact}
              />
            ) : (
              <StatePanel state="failed" />
            )
          ) : (
            <article className="report-preview-copy">
              <Badge tone="positive">发布 hash 已核验</Badge>
              <h3>执行摘要</h3>
              <p>品牌提及率 68.4%，有效回答 38 条；所有数字绑定冻结窗口与贡献证据。</p>
              <h3>优化建议</h3>
              <p>优先补齐可公开核验的部署边界说明，并在下一冻结窗口进行复测。</p>
            </article>
          )}
        </Dialog>
      ) : null}
    </>
  );
}

const memberSchema = z.object({
  name: z.string().trim().min(2, '请输入成员姓名').refine(noClientSecret, noClientSecretMessage),
  email: z.email('请输入有效邮箱').refine(noClientSecret, noClientSecretMessage),
  role: z.enum(['member', 'admin']),
});
type MemberValue = z.infer<typeof memberSchema>;
type CustomerMember = {
  id: string;
  userId: string;
  name: string;
  contact: string;
  role: '客户管理员' | '客户成员' | '其他成员';
  state: 'active' | 'revoked';
};
type MemberGovernanceReconciliation =
  | { kind: 'invite'; memberId: string; userId: string; receipt: string }
  | { kind: 'remove'; memberId: string; receipt: string }
  | { kind: 'bind-oidc'; userId: string; receipt: string }
  | { kind: 'revoke-oidc'; userId: string; receipt: string };
type MemberAuthoritySnapshot = {
  allMembers: CustomerMember[];
  visibleMembers: CustomerMember[];
  oidcBoundUsers: Set<string>;
  memberProjection: { total: number; shown: number; invalid: boolean };
  oidcProjection: { total: number; shown: number; invalid: boolean };
};
const maskMemberSubject = (value: unknown): string => {
  if (typeof value !== 'string' || containsClientSecret(value) || value.length > 255) {
    return '联系标识已隐藏';
  }
  const email = value.match(/^([^@\s])[^@\s]*(@[^@\s]+)$/);
  return email ? `${email[1]}***${email[2]}` : '联系标识已隐藏';
};
const projectCustomerMember = (value: IdentityMemberView): CustomerMember | null => {
  if (
    value.service_account ||
    typeof value.pub_id !== 'string' ||
    typeof value.user_pub_id !== 'string' ||
    containsClientSecret(`${value.pub_id} ${value.user_pub_id}`)
  ) {
    return null;
  }
  const name =
    typeof value.display_name === 'string' &&
    value.display_name.length <= 120 &&
    !containsClientSecret(value.display_name)
      ? value.display_name
      : '未命名成员';
  return {
    id: value.pub_id,
    userId: value.user_pub_id,
    name,
    contact: maskMemberSubject(value.subject),
    role:
      value.role === 'admin' ? '客户管理员' : value.role === 'customer' ? '客户成员' : '其他成员',
    state: value.state === 'revoked' ? 'revoked' : 'active',
  };
};
function MembersWorkspace() {
  const experience = useOptionalExperienceContext();
  const live = experience?.source === 'live';
  const [retryKey, retry] = useLocalRetry();
  const [members, setMembers] = useState<CustomerMember[]>([
    {
      id: 'mbr_fixture_admin',
      userId: 'usr_fixture_admin',
      name: '林澄',
      contact: 'l***@yunxiu.example',
      role: '客户管理员',
      state: 'active',
    },
  ]);
  const [liveState, setLiveState] = useState<
    'fixture' | 'loading' | 'ready' | 'empty' | 'forbidden' | 'failed'
  >(live ? 'loading' : 'fixture');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [memberReceipt, setMemberReceipt] = useState('');
  const [oidcBoundUsers, setOidcBoundUsers] = useState<Set<string>>(new Set());
  const [oidcSubject, setOidcSubject] = useState('');
  const [oidcState, setOidcState] = useState<'idle' | 'saving' | 'failed'>('idle');
  const [memberMutation, setMemberMutation] = useState<
    'idle' | 'invite' | 'remove' | 'bind-oidc' | 'revoke-oidc'
  >('idle');
  const [pendingReconciliation, setPendingReconciliation] =
    useState<MemberGovernanceReconciliation | null>(null);
  const [reconciliationState, setReconciliationState] = useState<'idle' | 'reading' | 'failed'>(
    'idle',
  );
  const memberMutationContext = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    String(retryKey),
  ]);
  const memberWrite = useCustomerMutationGuard(memberMutationContext);
  const [memberProjection, setMemberProjection] = useState({
    total: 1,
    shown: 1,
    invalid: false,
  });
  const [oidcProjection, setOidcProjection] = useState({
    total: 0,
    shown: 0,
    invalid: false,
  });
  const selectedMember = members.find((member) => member.id === selectedId) ?? null;
  const adminCount = members.filter(
    (member) => member.role === '客户管理员' && member.state === 'active',
  ).length;
  const memberProjectionIncomplete =
    memberProjection.invalid || memberProjection.total !== memberProjection.shown;
  const oidcProjectionIncomplete =
    oidcProjection.invalid || oidcProjection.total !== oidcProjection.shown;
  const governanceWritesLocked = live && (memberProjectionIncomplete || oidcProjectionIncomplete);
  const memberMutationPending =
    memberMutation !== 'idle' && (memberWrite.isActive() || reconciliationState === 'reading');
  const memberWritesLocked =
    governanceWritesLocked || memberMutationPending || pendingReconciliation !== null;
  const projectionNotices: ProjectionLimitNoticeItem[] = [
    ...(memberProjectionIncomplete
      ? [
          {
            key: 'identity-members',
            label: '成员合同安全投影',
            total: memberProjection.total,
            shown: memberProjection.shown,
          },
        ]
      : []),
    ...(oidcProjectionIncomplete
      ? [
          {
            key: 'oidc-bindings',
            label: 'OIDC 绑定安全投影',
            total: oidcProjection.total,
            shown: oidcProjection.shown,
          },
        ]
      : []),
  ];
  const readMemberAuthority = async (
    headers: NonNullable<ReturnType<typeof getValidatedIdentityHeaders>>,
  ): Promise<MemberAuthoritySnapshot | null> => {
    const [result, bindingResult] = await Promise.all([
      listIdentityMembers(headers),
      listOidcBindings(headers),
    ]);
    if (
      result.kind !== 'ready' ||
      bindingResult.kind !== 'ready' ||
      result.data.projection.invalid ||
      bindingResult.data.projection.invalid ||
      result.data.projection.total !== result.data.projection.shown ||
      bindingResult.data.projection.total !== bindingResult.data.projection.shown
    ) {
      return null;
    }
    const allMembers = result.data.data
      .map(projectCustomerMember)
      .filter((member): member is CustomerMember => member !== null);
    const visibleMembers = allMembers.filter((member) => member.state === 'active');
    const visibleUserIds = new Set(visibleMembers.map((member) => member.userId));
    return {
      allMembers,
      visibleMembers,
      oidcBoundUsers: new Set(
        bindingResult.data.data.flatMap((binding) => {
          const userId = safeOpaqueId(binding.user_pub_id, 'usr_');
          return binding.active && userId && visibleUserIds.has(userId) ? [userId] : [];
        }),
      ),
      memberProjection: result.data.projection,
      oidcProjection: bindingResult.data.projection,
    };
  };
  const adoptMemberAuthority = (snapshot: MemberAuthoritySnapshot) => {
    setMemberProjection(snapshot.memberProjection);
    setOidcProjection(snapshot.oidcProjection);
    setMembers(snapshot.visibleMembers);
    setOidcBoundUsers(snapshot.oidcBoundUsers);
    setLiveState(snapshot.visibleMembers.length ? 'ready' : 'empty');
  };
  useEffect(() => {
    setMemberMutation('idle');
    setPendingReconciliation(null);
    setReconciliationState('idle');
    setOidcState('idle');
    setOidcSubject('');
    setSelectedId(null);
    setMemberReceipt('');
    if (!live) {
      setLiveState('fixture');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setMembers([]);
      setLiveState('forbidden');
      return;
    }
    if (!experience?.roles.includes('admin')) {
      setMembers([]);
      setLiveState('forbidden');
      return;
    }
    let cancelled = false;
    setLiveState('loading');
    setMemberProjection({ total: 0, shown: 0, invalid: false });
    setOidcProjection({ total: 0, shown: 0, invalid: false });
    void Promise.all([listIdentityMembers(headers), listOidcBindings(headers)]).then(
      ([result, bindingResult]) => {
        if (cancelled) return;
        if (result.kind !== 'ready' || bindingResult.kind !== 'ready') {
          setMembers([]);
          setLiveState(
            result.kind === 'forbidden' || bindingResult.kind === 'forbidden'
              ? 'forbidden'
              : 'failed',
          );
          return;
        }
        setMemberProjection(result.data.projection);
        setOidcProjection(bindingResult.data.projection);
        if (
          (result.data.projection.total > 0 && result.data.projection.shown === 0) ||
          (bindingResult.data.projection.total > 0 && bindingResult.data.projection.shown === 0)
        ) {
          setMembers([]);
          setOidcBoundUsers(new Set());
          setLiveState('failed');
          return;
        }
        const projected = result.data.data
          .map(projectCustomerMember)
          .filter((member): member is CustomerMember => member !== null);
        const visibleMembers = projected.filter((member) => member.state === 'active');
        const projectedUserIds = new Set(visibleMembers.map((member) => member.userId));
        setMembers(visibleMembers);
        setOidcBoundUsers(
          new Set(
            bindingResult.data.data.flatMap((binding) => {
              const userId = safeOpaqueId(binding.user_pub_id, 'usr_');
              return binding.active && userId && projectedUserIds.has(userId) ? [userId] : [];
            }),
          ),
        );
        setLiveState(visibleMembers.length ? 'ready' : 'empty');
      },
    );
    return () => {
      cancelled = true;
    };
  }, [experience, live, retryKey]);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<MemberValue>({
    resolver: zodResolver(memberSchema),
    defaultValues: { name: '', email: '', role: 'member' },
  });
  const memberAuthorityConfirms = (
    snapshot: MemberAuthoritySnapshot,
    expected: MemberGovernanceReconciliation,
  ): boolean => {
    if (expected.kind === 'invite') {
      return snapshot.allMembers.some(
        (member) =>
          member.id === expected.memberId &&
          member.userId === expected.userId &&
          member.state === 'active',
      );
    }
    if (expected.kind === 'remove') {
      return !snapshot.allMembers.some(
        (member) => member.id === expected.memberId && member.state === 'active',
      );
    }
    if (expected.kind === 'bind-oidc') {
      return snapshot.oidcBoundUsers.has(expected.userId);
    }
    return !snapshot.oidcBoundUsers.has(expected.userId);
  };
  const reconcileMemberGovernance = async (
    expected: MemberGovernanceReconciliation,
    writeTicket: CustomerAccountMutationTicket,
  ) => {
    const headers = getValidatedIdentityHeaders();
    const snapshot = headers ? await readMemberAuthority(headers) : null;
    if (!memberWrite.isCurrent(writeTicket)) {
      memberWrite.finish(writeTicket);
      return false;
    }
    if (!snapshot || !memberAuthorityConfirms(snapshot, expected)) {
      memberWrite.finish(writeTicket);
      setPendingReconciliation(expected);
      setReconciliationState('failed');
      if (expected.kind === 'bind-oidc' || expected.kind === 'revoke-oidc') {
        setOidcState('failed');
      }
      return false;
    }
    if (!memberWrite.finish(writeTicket)) return false;
    adoptMemberAuthority(snapshot);
    setPendingReconciliation(null);
    setReconciliationState('idle');
    setMemberMutation('idle');
    setOidcState('idle');
    setMemberReceipt(expected.receipt);
    if (expected.kind === 'invite') reset();
    if (expected.kind === 'remove') {
      setSelectedId((current) => (current === expected.memberId ? null : current));
    }
    return true;
  };
  const retryMemberGovernanceReconciliation = async () => {
    if (!pendingReconciliation) return;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setReconciliationState('failed');
      return;
    }
    const writeTicket = memberWrite.begin(headers);
    if (!writeTicket) return;
    setMemberMutation(pendingReconciliation.kind);
    setReconciliationState('reading');
    if (
      pendingReconciliation.kind === 'bind-oidc' ||
      pendingReconciliation.kind === 'revoke-oidc'
    ) {
      setOidcState('saving');
    }
    await reconcileMemberGovernance(pendingReconciliation, writeTicket);
  };
  const submit = handleSubmit(async (value) => {
    if (memberWritesLocked) return;
    if (live) {
      const headers = getValidatedIdentityHeaders();
      if (!headers) {
        setLiveState('forbidden');
        return;
      }
      const writeTicket = memberWrite.begin(headers);
      if (!writeTicket) return;
      setMemberMutation('invite');
      setMemberReceipt('');
      const result = await createIdentityMember(
        {
          subject: value.email,
          display_name: value.name,
          role: value.role === 'admin' ? 'admin' : 'customer',
        },
        headers,
      );
      if (result.kind !== 'ready') {
        if (!memberWrite.finish(writeTicket)) return;
        setMemberMutation('idle');
        setLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      const projected = projectCustomerMember(result.data);
      if (!projected) {
        if (!memberWrite.finish(writeTicket)) return;
        setMemberMutation('idle');
        setLiveState('failed');
        return;
      }
      if (!memberWrite.isCurrent(writeTicket)) {
        memberWrite.finish(writeTicket);
        return;
      }
      const expected: MemberGovernanceReconciliation = {
        kind: 'invite',
        memberId: projected.id,
        userId: projected.userId,
        receipt: `${projected.name} 已加入租户，联系标识只保留掩码`,
      };
      setPendingReconciliation(expected);
      setReconciliationState('reading');
      await reconcileMemberGovernance(expected, writeTicket);
      return;
    }
    setMembers((current) => [
      ...current,
      {
        id: `mbr_fixture_${current.length + 1}`,
        userId: `usr_fixture_${current.length + 1}`,
        name: value.name,
        contact: value.email.replace(/(^.).*(@.*$)/, '$1***$2'),
        role: value.role === 'admin' ? '客户管理员' : '客户成员',
        state: 'active',
      },
    ]);
    setMemberReceipt(`${value.name} 的邀请已发送，邮箱仅以掩码保存`);
    reset();
  });
  const changeSelectedRole = () => {
    if (!selectedMember || live || memberWritesLocked) return;
    const nextRole = selectedMember.role === '客户管理员' ? '客户成员' : '客户管理员';
    if (selectedMember.role === '客户管理员' && adminCount === 1) return;
    setMembers((current) =>
      current.map((member) =>
        member.id === selectedMember.id ? { ...member, role: nextRole } : member,
      ),
    );
    setMemberReceipt(`${selectedMember.name} 已变更为${nextRole}，审计事件已记录`);
  };
  const removeSelected = async () => {
    if (!selectedMember || memberWritesLocked) return;
    if (selectedMember.role === '客户管理员' && adminCount === 1) return;
    const target = selectedMember;
    if (live) {
      const headers = getValidatedIdentityHeaders();
      if (!headers) {
        setLiveState('forbidden');
        return;
      }
      const writeTicket = memberWrite.begin(headers);
      if (!writeTicket) return;
      setMemberMutation('remove');
      setMemberReceipt('');
      const result = await revokeIdentityMember(target.id, headers);
      if (result.kind !== 'ready') {
        if (!memberWrite.finish(writeTicket)) return;
        setMemberMutation('idle');
        setLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      if (!memberWrite.isCurrent(writeTicket)) {
        memberWrite.finish(writeTicket);
        return;
      }
      const expected: MemberGovernanceReconciliation = {
        kind: 'remove',
        memberId: target.id,
        receipt: `${target.name} 已移出项目，历史审计仍保留`,
      };
      setPendingReconciliation(expected);
      setReconciliationState('reading');
      await reconcileMemberGovernance(expected, writeTicket);
      return;
    }
    setMembers((current) => current.filter((member) => member.id !== target.id));
    setMemberReceipt(`${target.name} 已移出项目，历史审计仍保留`);
    setSelectedId((current) => (current === target.id ? null : current));
  };
  const bindSelectedOidc = async () => {
    if (!selectedMember || !live || memberWritesLocked) return;
    const target = selectedMember;
    const subject = oidcSubject;
    setOidcSubject('');
    if (
      subject !== subject.trim() ||
      subject.length === 0 ||
      subject.length > 512 ||
      containsClientSecret(subject)
    ) {
      setOidcState('failed');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveState('forbidden');
      return;
    }
    const writeTicket = memberWrite.begin(headers);
    if (!writeTicket) return;
    setMemberMutation('bind-oidc');
    setMemberReceipt('');
    setOidcState('saving');
    const result = await bindOidcIdentity(target.userId, { subject }, headers);
    if (result.kind !== 'ready') {
      if (!memberWrite.finish(writeTicket)) return;
      setMemberMutation('idle');
      if (result.kind === 'forbidden') setLiveState('forbidden');
      else setOidcState('failed');
      return;
    }
    if (!memberWrite.isCurrent(writeTicket)) {
      memberWrite.finish(writeTicket);
      return;
    }
    const expected: MemberGovernanceReconciliation = {
      kind: 'bind-oidc',
      userId: target.userId,
      receipt: `${target.name} 的 OIDC 标识已哈希绑定；原始 subject 未保留`,
    };
    setPendingReconciliation(expected);
    setReconciliationState('reading');
    await reconcileMemberGovernance(expected, writeTicket);
  };
  const revokeSelectedOidc = async () => {
    if (!selectedMember || !live || memberWritesLocked) return;
    const target = selectedMember;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveState('forbidden');
      return;
    }
    const writeTicket = memberWrite.begin(headers);
    if (!writeTicket) return;
    setMemberMutation('revoke-oidc');
    setMemberReceipt('');
    setOidcState('saving');
    const result = await revokeOidcIdentity(target.userId, headers);
    if (result.kind !== 'ready') {
      if (!memberWrite.finish(writeTicket)) return;
      setMemberMutation('idle');
      if (result.kind === 'forbidden') setLiveState('forbidden');
      else setOidcState('failed');
      return;
    }
    if (!memberWrite.isCurrent(writeTicket)) {
      memberWrite.finish(writeTicket);
      return;
    }
    const expected: MemberGovernanceReconciliation = {
      kind: 'revoke-oidc',
      userId: target.userId,
      receipt: `${target.name} 的 OIDC 绑定已撤销并记录审计`,
    };
    setPendingReconciliation(expected);
    setReconciliationState('reading');
    await reconcileMemberGovernance(expected, writeTicket);
  };
  if (liveState === 'loading') return <StatePanel state="loading" />;
  if (liveState === 'forbidden') return <StatePanel state="forbidden" />;
  if (liveState === 'failed') return <StatePanel state="failed" onRetry={retry} />;
  return (
    <div className="workspace-grid" aria-busy={memberMutationPending}>
      <section className="panel">
        <h2>项目成员</h2>
        <p className="panel-subtitle">客户管理员可以管理本租户成员；邮箱在列表和审计中保持掩码。</p>
        <ProjectionLimitNotice
          items={projectionNotices}
          detail="成员或绑定集合不完整时，邀请、角色、移除和 OIDC 写操作全部锁定；请先局部重试获取完整安全投影。"
        />
        {governanceWritesLocked ? (
          <div className="confirmation projection-limit-notice" role="alert">
            <Badge tone="warning">成员安全投影不完整</Badge>
            <span>治理写操作已锁定，当前安全子集不会被当作完整成员或绑定清单。</span>
          </div>
        ) : null}
        <div className="member-list">
          {members.map((member) => (
            <article key={member.id}>
              <div className="avatar">{member.name.slice(0, 1)}</div>
              <div>
                <strong>{member.name}</strong>
                <span>{member.contact}</span>
              </div>
              <Badge tone={member.role === '客户管理员' ? 'info' : 'neutral'}>{member.role}</Badge>
              <button
                className="button button-secondary"
                aria-label={`管理 ${member.name}`}
                disabled={memberMutationPending}
                onClick={() => {
                  setOidcSubject('');
                  setOidcState('idle');
                  setSelectedId(member.id);
                }}
              >
                管理
              </button>
            </article>
          ))}
          {liveState === 'empty' ? <StatePanel state="empty" /> : null}
        </div>
      </section>
      <form className="panel form-panel" onSubmit={(event) => void submit(event)} noValidate>
        <h2>邀请成员</h2>
        <Field id="memberName" label="姓名" error={errors.name}>
          <input id="memberName" disabled={memberWritesLocked} {...register('name')} />
        </Field>
        <Field id="memberEmail" label="工作邮箱" error={errors.email}>
          <input
            id="memberEmail"
            type="email"
            disabled={memberWritesLocked}
            {...register('email')}
          />
        </Field>
        <Field id="memberRole" label="项目角色" error={errors.role}>
          <select id="memberRole" disabled={memberWritesLocked} {...register('role')}>
            <option value="member">客户成员</option>
            <option value="admin">客户管理员</option>
          </select>
        </Field>
        <button className="button" disabled={memberWritesLocked}>
          {memberMutation === 'invite' ? '正在发送…' : '发送邀请'}
        </button>
      </form>
      {memberMutationPending ? (
        <span className="sr-only" role="status">
          成员治理写入处理中；完成前其他成员写操作已锁定。
        </span>
      ) : null}
      {reconciliationState === 'reading' && pendingReconciliation ? (
        <div className="confirmation" role="status">
          <Badge tone="warning">正在确认</Badge>
          <span>写入已接受，正在重新读取权威成员与 OIDC 绑定投影。</span>
        </div>
      ) : null}
      {reconciliationState === 'failed' && pendingReconciliation ? (
        <StatePanel state="failed" onRetry={() => void retryMemberGovernanceReconciliation()} />
      ) : null}
      {memberReceipt ? <Toast>{memberReceipt}</Toast> : null}
      {selectedMember ? (
        <Dialog
          title={`管理 ${selectedMember.name}`}
          eyebrow="Project membership"
          closeLabel="关闭成员管理"
          onClose={() => {
            setOidcSubject('');
            setOidcState('idle');
            setSelectedId(null);
          }}
        >
          <dl className="definition-grid">
            <div>
              <dt>联系标识</dt>
              <dd>{selectedMember.contact}</dd>
            </div>
            <div>
              <dt>当前角色</dt>
              <dd>{selectedMember.role}</dd>
            </div>
          </dl>
          {selectedMember.role === '客户管理员' && adminCount === 1 ? (
            <p className="security-note">必须至少保留一名客户管理员，当前成员不可降级或移除。</p>
          ) : null}
          {live ? (
            <>
              <p className="security-note">
                当前合同不支持角色更新；角色变更保持禁用，不会伪造成功。
              </p>
              <Field id="memberOidcSubject" label="IdP opaque subject">
                <input
                  id="memberOidcSubject"
                  value={oidcSubject}
                  autoComplete="off"
                  disabled={memberWritesLocked || oidcBoundUsers.has(selectedMember.userId)}
                  onChange={(event) => setOidcSubject(event.target.value)}
                />
              </Field>
              <p className="security-note">
                {oidcBoundUsers.has(selectedMember.userId)
                  ? '已绑定；数据库和审计仅保存哈希，不返回原始 subject。'
                  : '输入只用于一次哈希绑定；请勿粘贴 token、Cookie 或验证码。'}
              </p>
              {oidcState === 'failed' && reconciliationState !== 'failed' ? (
                <Toast tone="negative">OIDC 绑定操作失败；未保存输入值。</Toast>
              ) : null}
            </>
          ) : null}
          <div className="button-row">
            <button
              className="button button-secondary"
              disabled={
                live ||
                memberWritesLocked ||
                (selectedMember.role === '客户管理员' && adminCount === 1)
              }
              onClick={changeSelectedRole}
            >
              {selectedMember.role === '客户管理员' ? '改为客户成员' : '提升为客户管理员'}
            </button>
            <button
              className="button button-danger"
              disabled={
                memberWritesLocked || (selectedMember.role === '客户管理员' && adminCount === 1)
              }
              onClick={() => void removeSelected()}
            >
              移出项目
            </button>
            {live ? (
              oidcBoundUsers.has(selectedMember.userId) ? (
                <button
                  className="button button-danger"
                  disabled={memberWritesLocked}
                  onClick={() => void revokeSelectedOidc()}
                >
                  撤销 OIDC 绑定
                </button>
              ) : (
                <button
                  className="button"
                  disabled={memberWritesLocked || !oidcSubject}
                  onClick={() => void bindSelectedOidc()}
                >
                  建立 OIDC 绑定
                </button>
              )
            ) : null}
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}

// ── 信息表（客户信息收集表 intake）工作区 ─────────────────────────────────
// 词表与 api/geo_platform/intake/models.py 单一真源对齐；live 模式优先用公开
// form-schema 的选项，失败/未配置时回退到这里的嵌入式副本（渲染用，写校验以后端为准）。
const INTAKE_GOALS = [
  '提升AI搜索曝光',
  '增加品牌被推荐频次',
  '获取销售线索',
  '建立行业权威形象',
  '纠正错误信息',
  '超越竞品曝光',
];
const INTAKE_AUDIENCE_TYPES = ['B2B企业客户', 'B2C个人消费者', '政府/机构', '经销商/渠道'];
const INTAKE_PLATFORMS = [
  'DeepSeek',
  '豆包',
  '文心一言',
  '通义千问',
  'Kimi',
  '腾讯元宝',
  'ChatGPT',
  'Claude',
  'Gemini',
];
const INTAKE_PRODUCT_FEATURES = [
  '价格优势',
  '品质领先',
  '技术领先',
  '服务好',
  '交付快',
  '定制化',
  '安全可靠',
  '口碑好',
  '一站式',
];
const INTAKE_COMPANY_STRENGTHS = [
  '团队规模领先',
  '多地服务网点',
  '资质认证齐全',
  '拥有专利',
  '行业协会成员',
  '知名合作伙伴',
  '获得融资',
  '上市公司',
];
const INTAKE_AD_REVIEW_DOC_TYPES = [
  '医疗广告审查证明',
  '药品广告审查批准文号',
  '医疗器械广告审查批准文号',
  '保健食品广告审查批准文号',
  '特医食品广告审查批准文号',
  '农药广告审查批准文件',
  '兽药广告审查批准文件',
  '不适用（非A类行业）',
];
const INTAKE_REVIEW_CATEGORY_OPTIONS = [
  { value: 'A', label: 'A类·法定前置审查（医疗/药品/医疗器械/保健食品/特医食品/农药/兽药）' },
  {
    value: 'B',
    label: 'B类·资质准入审查（金融/互联网金融/房地产/教育/电信/招商加盟/人力资源）',
  },
  { value: 'C', label: 'C类·内容合规审查（化妆品/医美/食品/酒类/旅游/养老等）' },
  { value: 'D', label: 'D类·禁止发布（烟草/处方药/特殊药品/婴儿乳制品替代母乳）' },
  { value: 'none', label: '不属于上述分类' },
];
export const INTAKE_TRUTH_CONFIRM_ITEMS = [
  '本表所填信息及所附材料真实、准确、合法，且有相应文件支撑',
  '拟推广产品/服务属于可依法面向公众宣传的范围',
  '如属于法定前置审查行业（A类），已依法取得广告审查批准文件，且保证投放内容与审查批准内容一致，不擅自剪辑、拼接或修改已审查内容',
  '确认所属行业不属于法律禁止发布广告的行业（D类）',
  '已知悉：推广内容发布前将逐篇提交我司书面确认',
];

type IntakeVocab = {
  goals: string[];
  audienceTypes: string[];
  platforms: string[];
  adReviewDocTypes: string[];
  reviewCategoryOptions: { value: string; label: string }[];
  truthItems: string[];
};
const intakeFallbackVocab: IntakeVocab = {
  goals: INTAKE_GOALS,
  audienceTypes: INTAKE_AUDIENCE_TYPES,
  platforms: INTAKE_PLATFORMS,
  adReviewDocTypes: INTAKE_AD_REVIEW_DOC_TYPES,
  reviewCategoryOptions: INTAKE_REVIEW_CATEGORY_OPTIONS,
  truthItems: INTAKE_TRUTH_CONFIRM_ITEMS,
};
const intakeVocabFromSchema = (schema: IntakeFormSchema): IntakeVocab => {
  const fields = schema.sections.flatMap((section) => section.fields);
  const optionsOf = (key: string) => fields.find((field) => field.key === key)?.options ?? [];
  const valuesOf = (key: string, fallback: string[]) => {
    const options = optionsOf(key);
    return options.length ? options.map((option) => option.value) : fallback;
  };
  const reviewOptions = optionsOf('review_category');
  const confirmField = fields.find((field) => field.key === 'truth_confirmed');
  return {
    goals: valuesOf('goals', INTAKE_GOALS),
    audienceTypes: valuesOf('audience_type', INTAKE_AUDIENCE_TYPES),
    platforms: valuesOf('platforms', INTAKE_PLATFORMS),
    adReviewDocTypes: valuesOf('ad_review_doc_types', INTAKE_AD_REVIEW_DOC_TYPES),
    reviewCategoryOptions: reviewOptions.length ? reviewOptions : INTAKE_REVIEW_CATEGORY_OPTIONS,
    truthItems:
      confirmField && confirmField.items.length ? confirmField.items : INTAKE_TRUTH_CONFIRM_ITEMS,
  };
};

const intakeText = (max: number) =>
  z.string().trim().max(max, `不能超过 ${max} 个字`).refine(noClientSecret, noClientSecretMessage);
const intakeVocabArray = (vocab: readonly string[], label: string) =>
  z
    .array(z.string())
    .max(customerIntakeProjectionLimits.listItems, `${label}最多 100 项`)
    .superRefine((items, ctx) => {
      for (const item of items) {
        if (!vocab.includes(item)) {
          ctx.addIssue({ code: 'custom', message: `${label}包含词表外取值：${item}` });
        }
        if (containsClientSecret(item)) {
          ctx.addIssue({ code: 'custom', message: noClientSecretMessage });
        }
      }
    });

export const intakeProfileSchema = z.object({
  contactPerson: intakeText(200),
  contactInfo: intakeText(500),
  website: intakeText(500),
  wechat: intakeText(200),
  douyin: intakeText(200),
  socialMedia: intakeText(2000),
  audienceDesc: intakeText(2000),
  sellingPoints: intakeText(2000),
  fillerName: intakeText(200),
  businessLicenseCode: z
    .string()
    .trim()
    .refine(
      (value) => value === '' || /^[0-9A-Z]{18}$/.test(value),
      '统一社会信用代码须为 18 位数字或大写字母',
    )
    .refine(noClientSecret, noClientSecretMessage),
  reviewCategory: z.enum(['', 'A', 'B', 'C', 'D', 'none']),
  preReviewRequired: z.boolean(),
  adReviewNo: intakeText(200),
  adReviewAuthority: intakeText(200),
  adReviewExpiry: intakeText(40),
  goals: intakeVocabArray(INTAKE_GOALS, '推广目标'),
  audienceType: intakeVocabArray(INTAKE_AUDIENCE_TYPES, '客群类型'),
  platforms: intakeVocabArray(INTAKE_PLATFORMS, '目标 AI 平台'),
  adReviewDocTypes: intakeVocabArray(INTAKE_AD_REVIEW_DOC_TYPES, '广告审查批准文件'),
  regionsText: intakeText(2000),
  trademarksText: intakeText(2000),
  evidenceText: intakeText(2000),
  licenses: z
    .array(
      z.object({
        name: intakeText(200),
        number: intakeText(200),
        expiry: intakeText(200),
      }),
    )
    .max(customerIntakeProjectionLimits.licenses, '行业许可证最多 20 行'),
  truthItems: z.array(z.string()).max(customerIntakeProjectionLimits.truthItems),
});
export type IntakeProfileFormValue = z.infer<typeof intakeProfileSchema>;

const intakeResearchSchema = z.object({
  brand: z
    .string()
    .trim()
    .min(1, '请填写品牌名称')
    .max(200, '品牌名称不能超过 200 个字')
    .refine(noClientSecret, noClientSecretMessage),
  website: intakeText(500),
});
type IntakeResearchFormValue = z.infer<typeof intakeResearchSchema>;

const intakePromoSchema = z
  .object({
    kind: z.enum(['product', 'company']),
    name: intakeText(2000),
    category: intakeText(2000),
    desc: intakeText(2000),
    price: intakeText(2000),
    advantage: intakeText(2000),
    cases: intakeText(2000),
    data: intakeText(2000),
    features: intakeVocabArray(INTAKE_PRODUCT_FEATURES, '产品特点'),
    strength: intakeVocabArray(INTAKE_COMPANY_STRENGTHS, '公司实力'),
  })
  .superRefine((value, ctx) => {
    if (!value.name) {
      ctx.addIssue({ code: 'custom', path: ['name'], message: '请填写名称' });
    }
  });
type IntakePromoFormValue = z.infer<typeof intakePromoSchema>;

const intakeTriggerBatchSchema = z.object({
  text: z
    .string()
    .trim()
    .min(1, '请填写期望问法（每行一条）')
    .max(8000, '批量问法不能超过 8000 个字')
    .refine(noClientSecret, noClientSecretMessage)
    .refine(
      (text) => text.split(/[\r\n]+/).every((line) => line.trim().length <= 500),
      '单条问法不能超过 500 字',
    ),
});
type IntakeTriggerBatchFormValue = z.infer<typeof intakeTriggerBatchSchema>;

const intakeSplitLines = (text: string): string[] =>
  text
    .split(/[\r\n]+/)
    .map((line) => line.trim())
    .filter(Boolean);
const intakeSplitTags = (text: string): string[] =>
  text
    .split(/[\r\n,，、;；]+/)
    .map((line) => line.trim())
    .filter(Boolean);

const intakeProfileToForm = (
  view: IntakeProfileView,
  truthItems: readonly string[],
): IntakeProfileFormValue => ({
  contactPerson: view.contact_person ?? '',
  contactInfo: view.contact_info ?? '',
  website: view.website ?? '',
  wechat: view.wechat ?? '',
  douyin: view.douyin ?? '',
  socialMedia: view.social_media ?? '',
  audienceDesc: view.audience_desc ?? '',
  sellingPoints: view.selling_points ?? '',
  fillerName: view.filler_name ?? '',
  businessLicenseCode: view.business_license_code ?? '',
  reviewCategory: (view.review_category ?? '') as IntakeProfileFormValue['reviewCategory'],
  preReviewRequired: view.pre_review_required === true,
  adReviewNo: view.ad_review_no ?? '',
  adReviewAuthority: view.ad_review_authority ?? '',
  adReviewExpiry: view.ad_review_expiry ?? '',
  goals: [...view.goals],
  audienceType: [...view.audience_type],
  platforms: [...view.platforms],
  adReviewDocTypes: [...view.ad_review_doc_types],
  regionsText: view.regions.join('\n'),
  trademarksText: view.trademarks.join('\n'),
  evidenceText: view.evidence_links.join('\n'),
  licenses: view.licenses.length
    ? view.licenses.map((row) => ({ name: row.name, number: row.number, expiry: row.expiry }))
    : [{ name: '', number: '', expiry: '' }],
  truthItems: view.truth_confirmed === true ? [...truthItems] : [],
});

const intakeFormToWrite = (value: IntakeProfileFormValue): IntakeProfileWrite => ({
  contact_person: value.contactPerson || null,
  contact_info: value.contactInfo || null,
  website: value.website || null,
  wechat: value.wechat || null,
  douyin: value.douyin || null,
  social_media: value.socialMedia || null,
  audience_desc: value.audienceDesc || null,
  business_license_code: value.businessLicenseCode || null,
  selling_points: value.sellingPoints || null,
  filler_name: value.fillerName || null,
  ad_review_no: value.adReviewNo || null,
  ad_review_authority: value.adReviewAuthority || null,
  ad_review_expiry: value.adReviewExpiry || null,
  review_category: value.reviewCategory || null,
  pre_review_required: value.preReviewRequired,
  truth_confirmed: true,
  goals: value.goals,
  audience_type: value.audienceType,
  platforms: value.platforms,
  regions: intakeSplitTags(value.regionsText),
  trademarks: intakeSplitTags(value.trademarksText),
  ad_review_doc_types: value.adReviewDocTypes,
  evidence_links: intakeSplitLines(value.evidenceText),
  licenses: value.licenses
    .map((row) => ({ name: row.name, number: row.number, expiry: row.expiry }))
    .filter((row) => row.name || row.number || row.expiry),
});

const intakeFixtureDefaults: IntakeProfileFormValue = {
  contactPerson: '林澄',
  contactInfo: 'lin.cheng@example.test',
  website: 'https://www.yunxiu.example',
  wechat: '',
  douyin: '',
  socialMedia: '',
  audienceDesc: '需要安全部署企业知识库与智能问答的制造业数字化团队',
  sellingPoints: '支持私有化部署；通过等保三级认证；制造业知识库落地案例 40+。',
  fillerName: '林澄',
  businessLicenseCode: '91110000MA01C8XU3T',
  reviewCategory: 'none',
  preReviewRequired: false,
  adReviewNo: '',
  adReviewAuthority: '',
  adReviewExpiry: '',
  goals: ['提升AI搜索曝光'],
  audienceType: ['B2B企业客户'],
  platforms: ['豆包', 'DeepSeek'],
  adReviewDocTypes: [],
  regionsText: '全国',
  trademarksText: '',
  evidenceText: 'https://www.yunxiu.example/cases',
  licenses: [{ name: '', number: '', expiry: '' }],
  truthItems: [],
};
const intakeFixturePromos: IntakePromoView[] = [
  {
    pub_id: 'prm_fixture_01',
    kind: 'product',
    payload: {
      name: '云岫知识库私有化部署',
      category: '企业软件',
      features: ['安全可靠', '定制化'],
      desc: '面向制造业的企业知识检索、问答与治理平台。',
      price: '',
    },
    created_at: '2026-07-20T08:00:00Z',
    updated_at: '2026-07-20T08:00:00Z',
  },
  {
    pub_id: 'prm_fixture_02',
    kind: 'company',
    payload: {
      name: '云岫智能科技有限公司',
      strength: ['资质认证齐全', '拥有专利'],
      advantage: '制造业知识库落地案例 40+，支持私有化部署。',
      cases: '',
      data: '',
    },
    created_at: '2026-07-20T08:00:00Z',
    updated_at: '2026-07-20T08:00:00Z',
  },
];
const intakeFixtureTriggers: IntakeTriggerView[] = [
  {
    pub_id: 'tq_fixture_01',
    text: '预算 50 万的制造企业知识库怎么选',
    status: 'draft',
    created_at: '2026-07-20T08:00:00Z',
  },
  {
    pub_id: 'tq_fixture_02',
    text: '企业知识库私有化部署厂商对比',
    status: 'claim_created',
    created_at: '2026-07-20T08:00:00Z',
  },
];

const intakePromoPayloadFromForm = (value: IntakePromoFormValue): IntakePromoPayload =>
  value.kind === 'product'
    ? {
        name: value.name,
        category: value.category,
        features: value.features,
        desc: value.desc,
        price: value.price,
      }
    : {
        name: value.name,
        strength: value.strength,
        advantage: value.advantage,
        cases: value.cases,
        data: value.data,
      };

const intakePromoFormFromView = (promo: IntakePromoView): IntakePromoFormValue => {
  const text = (key: string) => {
    const raw = promo.payload[key];
    return typeof raw === 'string' ? raw : '';
  };
  const list = (key: string) => {
    const raw = promo.payload[key];
    return Array.isArray(raw) ? raw.filter((item): item is string => typeof item === 'string') : [];
  };
  return {
    kind: promo.kind,
    name: text('name'),
    category: text('category'),
    desc: text('desc'),
    price: text('price'),
    advantage: text('advantage'),
    cases: text('cases'),
    data: text('data'),
    features: list('features'),
    strength: list('strength'),
  };
};
const intakeEmptyPromoForm: IntakePromoFormValue = {
  kind: 'product',
  name: '',
  category: '',
  desc: '',
  price: '',
  advantage: '',
  cases: '',
  data: '',
  features: [],
  strength: [],
};

const intakePromoSummaryRows = (promo: IntakePromoView): [string, string][] => {
  const rows: [string, string][] = [];
  const push = (label: string, key: string) => {
    const raw = promo.payload[key];
    const text = Array.isArray(raw) ? raw.join('、') : typeof raw === 'string' ? raw : '';
    if (text) rows.push([label, text]);
  };
  if (promo.kind === 'product') {
    push('品类', 'category');
    push('特点', 'features');
    push('介绍', 'desc');
    push('价格', 'price');
  } else {
    push('实力', 'strength');
    push('优势', 'advantage');
    push('案例', 'cases');
    push('数据', 'data');
  }
  return rows;
};

function IntakePromoSection({
  live,
  projectPubId,
  dataVersion,
  prefilledMark,
}: {
  live: boolean;
  projectPubId: string;
  dataVersion: number;
  prefilledMark: boolean;
}) {
  const [retryKey, retry] = useLocalRetry();
  const [promos, setPromos] = useState<IntakePromoView[]>(live ? [] : intakeFixturePromos);
  const [listState, setListState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(live ? 'loading' : 'fixture');
  const [writeState, setWriteState] = useState<
    'idle' | 'saving' | 'saved' | 'failed' | 'forbidden'
  >('idle');
  const [editingPubId, setEditingPubId] = useState('');
  const promoWrite = useCustomerMutationGuard(
    createStructuredClientScopeKey(['intake-promo', projectPubId || 'fixture']),
  );
  const {
    register,
    handleSubmit,
    reset,
    watch,
    formState: { errors },
  } = useForm<IntakePromoFormValue>({
    resolver: zodResolver(intakePromoSchema),
    defaultValues: intakeEmptyPromoForm,
  });
  const kind = watch('kind');
  useEffect(() => {
    if (!live || !projectPubId) {
      setListState('fixture');
      setPromos(intakeFixturePromos);
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setListState('forbidden');
      return;
    }
    let cancelled = false;
    setListState('loading');
    void listIntakePromos(projectPubId, headers).then((result) => {
      if (cancelled) return;
      if (result.kind !== 'ready' || result.data.projection.invalid) {
        setListState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      setPromos(result.data.data);
      setListState('ready');
    });
    return () => {
      cancelled = true;
    };
  }, [dataVersion, live, projectPubId, retryKey]);
  const submit = handleSubmit(async (value) => {
    const payload = intakePromoPayloadFromForm(value);
    if (!live || !projectPubId) {
      const local: IntakePromoView = {
        pub_id: editingPubId || `prm_local_${crypto.randomUUID()}`,
        kind: value.kind,
        payload,
        created_at: '2026-07-20T08:00:00Z',
        updated_at: '2026-07-20T08:00:00Z',
      };
      setPromos((current) =>
        editingPubId
          ? current.map((promo) => (promo.pub_id === editingPubId ? local : promo))
          : [...current, local],
      );
      setEditingPubId('');
      reset(intakeEmptyPromoForm);
      setWriteState('saved');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setWriteState('forbidden');
      return;
    }
    const ticket = promoWrite.begin(headers);
    if (!ticket) return;
    setWriteState('saving');
    const result = editingPubId
      ? await updateIntakePromo(projectPubId, editingPubId, { payload }, headers)
      : await createIntakePromo(
          projectPubId,
          { kind: value.kind, payload },
          headers,
          `intake-promo-${crypto.randomUUID()}`,
        );
    if (!promoWrite.finish(ticket)) return;
    if (result.kind !== 'ready') {
      setWriteState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      return;
    }
    setPromos((current) =>
      editingPubId
        ? current.map((promo) => (promo.pub_id === editingPubId ? result.data : promo))
        : [...current, result.data],
    );
    setEditingPubId('');
    reset(intakeEmptyPromoForm);
    setWriteState('saved');
  });
  const removePromo = async (promo: IntakePromoView) => {
    if (!live || !projectPubId) {
      setPromos((current) => current.filter((item) => item.pub_id !== promo.pub_id));
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setWriteState('forbidden');
      return;
    }
    const ticket = promoWrite.begin(headers);
    if (!ticket) return;
    const result = await deleteIntakePromo(projectPubId, promo.pub_id, headers);
    if (!promoWrite.finish(ticket)) return;
    if (result.kind !== 'ready') {
      setWriteState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      return;
    }
    setPromos((current) => current.filter((item) => item.pub_id !== promo.pub_id));
    if (editingPubId === promo.pub_id) {
      setEditingPubId('');
      reset(intakeEmptyPromoForm);
    }
    setWriteState('saved');
  };
  return (
    <section className="panel form-panel">
      <h2>拟推广产品 / 服务 {prefilledMark ? <Badge tone="info">AI 预填</Badge> : null}</h2>
      <p className="panel-subtitle">名称 + 一句话介绍，每次合作聚焦 1-3 个；公司实力单独一条。</p>
      {listState === 'loading' ? <StatePanel state="loading" /> : null}
      {listState === 'failed' ? <StatePanel state="failed" onRetry={retry} /> : null}
      {listState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
      {listState !== 'loading' && listState !== 'failed' && listState !== 'forbidden' ? (
        <>
          {promos.length ? (
            <div className="request-list">
              {promos.map((promo) => (
                <article key={promo.pub_id}>
                  <Badge tone={promo.kind === 'product' ? 'info' : 'positive'}>
                    {promo.kind === 'product' ? '产品 / 服务' : '公司实力'}
                  </Badge>
                  <strong>
                    {typeof promo.payload.name === 'string' && promo.payload.name
                      ? promo.payload.name
                      : '（未命名）'}
                  </strong>
                  {intakePromoSummaryRows(promo).map(([label, text]) => (
                    <span key={label}>
                      {label}：{text}
                    </span>
                  ))}
                  <span>
                    <button
                      type="button"
                      className="button button-ghost"
                      onClick={() => {
                        setEditingPubId(promo.pub_id);
                        reset(intakePromoFormFromView(promo));
                      }}
                    >
                      编辑
                    </button>{' '}
                    <button
                      type="button"
                      className="button button-ghost"
                      onClick={() => void removePromo(promo)}
                    >
                      删除
                    </button>
                  </span>
                </article>
              ))}
            </div>
          ) : (
            <StatePanel state="empty" />
          )}
          <form onSubmit={(event) => void submit(event)} noValidate>
            <Field id="intake-promo-kind" label={editingPubId ? '编辑推广内容' : '新增推广内容'}>
              <select id="intake-promo-kind" {...register('kind')} disabled={Boolean(editingPubId)}>
                <option value="product">产品 / 服务</option>
                <option value="company">公司实力</option>
              </select>
            </Field>
            <div className="form-grid">
              <Field id="intake-promo-name" label="名称" error={errors.name}>
                <input id="intake-promo-name" {...register('name')} />
              </Field>
              {kind === 'product' ? (
                <>
                  <Field id="intake-promo-category" label="品类" error={errors.category}>
                    <input id="intake-promo-category" {...register('category')} />
                  </Field>
                  <Field id="intake-promo-price" label="价格" error={errors.price}>
                    <input id="intake-promo-price" {...register('price')} />
                  </Field>
                </>
              ) : null}
            </div>
            {kind === 'product' ? (
              <Field id="intake-promo-desc" label="一句话介绍" error={errors.desc}>
                <textarea id="intake-promo-desc" rows={2} {...register('desc')} />
              </Field>
            ) : (
              <>
                <Field id="intake-promo-advantage" label="核心优势" error={errors.advantage}>
                  <textarea id="intake-promo-advantage" rows={2} {...register('advantage')} />
                </Field>
                <div className="form-grid">
                  <Field id="intake-promo-cases" label="代表案例" error={errors.cases}>
                    <textarea id="intake-promo-cases" rows={2} {...register('cases')} />
                  </Field>
                  <Field id="intake-promo-data" label="关键数据" error={errors.data}>
                    <textarea id="intake-promo-data" rows={2} {...register('data')} />
                  </Field>
                </div>
              </>
            )}
            <Field
              id="intake-promo-vocab"
              label={kind === 'product' ? '产品特点' : '公司实力'}
              error={kind === 'product' ? errors.features : errors.strength}
            >
              <div className="form-grid form-grid-three">
                {(kind === 'product' ? INTAKE_PRODUCT_FEATURES : INTAKE_COMPANY_STRENGTHS).map(
                  (option) => (
                    <label key={option} className="check-field">
                      <input
                        type="checkbox"
                        value={option}
                        {...register(kind === 'product' ? 'features' : 'strength')}
                      />
                      {option}
                    </label>
                  ),
                )}
              </div>
            </Field>
            <div className="form-actions">
              <span>{editingPubId ? '保存后覆盖该条内容。' : '新增后立即写入项目草稿。'}</span>
              {editingPubId ? (
                <button
                  type="button"
                  className="button button-ghost"
                  onClick={() => {
                    setEditingPubId('');
                    reset(intakeEmptyPromoForm);
                  }}
                >
                  取消编辑
                </button>
              ) : null}
              <button className="button" disabled={writeState === 'saving'}>
                {writeState === 'saving' ? '正在保存…' : editingPubId ? '保存修改' : '添加'}
              </button>
            </div>
          </form>
        </>
      ) : null}
      {writeState === 'saved' ? <Toast>推广内容已保存</Toast> : null}
      {writeState === 'failed' ? <Toast tone="negative">保存失败，请稍后重试。</Toast> : null}
      {writeState === 'forbidden' ? <Toast tone="negative">无权修改此项目的信息表。</Toast> : null}
    </section>
  );
}

function IntakeTriggerSection({
  live,
  projectPubId,
  dataVersion,
  prefilledMark,
}: {
  live: boolean;
  projectPubId: string;
  dataVersion: number;
  prefilledMark: boolean;
}) {
  const [retryKey, retry] = useLocalRetry();
  const [triggers, setTriggers] = useState<IntakeTriggerView[]>(live ? [] : intakeFixtureTriggers);
  const [listState, setListState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(live ? 'loading' : 'fixture');
  const [writeState, setWriteState] = useState<
    'idle' | 'saving' | 'saved' | 'failed' | 'forbidden'
  >('idle');
  const [skipped, setSkipped] = useState<string[]>([]);
  const [editing, setEditing] = useState<{ pubId: string; text: string; error: string } | null>(
    null,
  );
  const triggerWrite = useCustomerMutationGuard(
    createStructuredClientScopeKey(['intake-trigger', projectPubId || 'fixture']),
  );
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<IntakeTriggerBatchFormValue>({
    resolver: zodResolver(intakeTriggerBatchSchema),
    defaultValues: { text: '' },
  });
  useEffect(() => {
    if (!live || !projectPubId) {
      setListState('fixture');
      setTriggers(intakeFixtureTriggers);
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setListState('forbidden');
      return;
    }
    let cancelled = false;
    setListState('loading');
    void listIntakeTriggers(projectPubId, headers).then((result) => {
      if (cancelled) return;
      if (result.kind !== 'ready' || result.data.projection.invalid) {
        setListState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      setTriggers(result.data.data);
      setListState('ready');
    });
    return () => {
      cancelled = true;
    };
  }, [dataVersion, live, projectPubId, retryKey]);
  const submit = handleSubmit(async (value) => {
    const lines = intakeSplitLines(value.text);
    if (!live || !projectPubId) {
      setTriggers((current) => [
        ...current,
        ...lines.map((text) => ({
          pub_id: `tq_local_${crypto.randomUUID()}`,
          text,
          status: 'draft' as const,
          created_at: '2026-07-20T08:00:00Z',
        })),
      ]);
      reset({ text: '' });
      setWriteState('saved');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setWriteState('forbidden');
      return;
    }
    const ticket = triggerWrite.begin(headers);
    if (!ticket) return;
    setWriteState('saving');
    const result = await createIntakeTriggers(
      projectPubId,
      value.text,
      headers,
      `intake-trigger-${crypto.randomUUID()}`,
    );
    if (!triggerWrite.finish(ticket)) return;
    if (result.kind !== 'ready') {
      setWriteState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      return;
    }
    setTriggers((current) => [...current, ...result.data.items]);
    setSkipped(result.data.skipped_duplicates);
    reset({ text: '' });
    setWriteState('saved');
  });
  const removeTrigger = async (trigger: IntakeTriggerView) => {
    if (!live || !projectPubId) {
      setTriggers((current) => current.filter((item) => item.pub_id !== trigger.pub_id));
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setWriteState('forbidden');
      return;
    }
    const ticket = triggerWrite.begin(headers);
    if (!ticket) return;
    const result = await deleteIntakeTrigger(projectPubId, trigger.pub_id, headers);
    if (!triggerWrite.finish(ticket)) return;
    if (result.kind !== 'ready') {
      setWriteState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      return;
    }
    setTriggers((current) => current.filter((item) => item.pub_id !== trigger.pub_id));
    setWriteState('saved');
  };
  const saveEditing = async () => {
    if (!editing) return;
    const text = editing.text.trim();
    if (!text || text.length > 500 || containsClientSecret(text)) {
      setEditing({
        ...editing,
        error: !text
          ? '问法不能为空'
          : text.length > 500
            ? '单条问法不能超过 500 字'
            : noClientSecretMessage,
      });
      return;
    }
    if (!live || !projectPubId) {
      setTriggers((current) =>
        current.map((item) => (item.pub_id === editing.pubId ? { ...item, text } : item)),
      );
      setEditing(null);
      setWriteState('saved');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setWriteState('forbidden');
      return;
    }
    const ticket = triggerWrite.begin(headers);
    if (!ticket) return;
    const result = await updateIntakeTrigger(projectPubId, editing.pubId, text, headers);
    if (!triggerWrite.finish(ticket)) return;
    if (result.kind !== 'ready') {
      setWriteState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      return;
    }
    setTriggers((current) =>
      current.map((item) => (item.pub_id === editing.pubId ? result.data : item)),
    );
    setEditing(null);
    setWriteState('saved');
  };
  return (
    <section className="panel form-panel">
      <h2>期望的用户提问场景 {prefilledMark ? <Badge tone="info">AI 预填</Badge> : null}</h2>
      <p className="panel-subtitle">
        这是方案设计的最重要输入。用户会怎么问 AI？请写 3-5
        条，每行一条；已生成监测问法的条目文本冻结。
      </p>
      {listState === 'loading' ? <StatePanel state="loading" /> : null}
      {listState === 'failed' ? <StatePanel state="failed" onRetry={retry} /> : null}
      {listState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
      {listState !== 'loading' && listState !== 'failed' && listState !== 'forbidden' ? (
        <>
          {triggers.length ? (
            <div className="request-list">
              {triggers.map((trigger) => (
                <article key={trigger.pub_id}>
                  <Badge tone={trigger.status === 'draft' ? 'warning' : 'neutral'}>
                    {trigger.status === 'draft' ? '草稿' : '已生成问法'}
                  </Badge>
                  {editing?.pubId === trigger.pub_id ? (
                    <span>
                      <input
                        aria-label="编辑问法"
                        value={editing.text}
                        onChange={(event) =>
                          setEditing({ ...editing, text: event.target.value, error: '' })
                        }
                      />{' '}
                      <button
                        type="button"
                        className="button button-ghost"
                        onClick={() => void saveEditing()}
                      >
                        保存
                      </button>{' '}
                      <button
                        type="button"
                        className="button button-ghost"
                        onClick={() => setEditing(null)}
                      >
                        取消
                      </button>
                      {editing.error ? (
                        <span className="field-error" role="alert">
                          {editing.error}
                        </span>
                      ) : null}
                    </span>
                  ) : (
                    <strong>{trigger.text}</strong>
                  )}
                  {trigger.status === 'draft' && editing?.pubId !== trigger.pub_id ? (
                    <span>
                      <button
                        type="button"
                        className="button button-ghost"
                        onClick={() =>
                          setEditing({ pubId: trigger.pub_id, text: trigger.text, error: '' })
                        }
                      >
                        编辑
                      </button>{' '}
                      <button
                        type="button"
                        className="button button-ghost"
                        onClick={() => void removeTrigger(trigger)}
                      >
                        删除
                      </button>
                    </span>
                  ) : null}
                </article>
              ))}
            </div>
          ) : (
            <StatePanel state="empty" />
          )}
          <form onSubmit={(event) => void submit(event)} noValidate>
            <Field id="intake-trigger-text" label="批量收录（每行一条）" error={errors.text}>
              <textarea
                id="intake-trigger-text"
                rows={3}
                placeholder="例：预算 3000 的扫地机器人怎么选"
                {...register('text')}
              />
            </Field>
            <div className="form-actions">
              <span>重复问法会自动跳过。</span>
              <button className="button" disabled={writeState === 'saving'}>
                {writeState === 'saving' ? '正在收录…' : '收录问法'}
              </button>
            </div>
          </form>
        </>
      ) : null}
      {skipped.length ? <Toast tone="warning">已跳过重复问法 {skipped.length} 条</Toast> : null}
      {writeState === 'saved' ? <Toast>问法已保存</Toast> : null}
      {writeState === 'failed' ? <Toast tone="negative">保存失败，请稍后重试。</Toast> : null}
      {writeState === 'forbidden' ? <Toast tone="negative">无权修改此项目的信息表。</Toast> : null}
    </section>
  );
}

function IntakeWorkspace() {
  const experience = useOptionalExperienceContext();
  const live = experience?.source === 'live' && Boolean(experience?.projectPubId);
  const [retryKey, retry] = useLocalRetry();
  const writeContext = createStructuredClientScopeKey([
    optionalExperienceScope(experience),
    'intake-profile',
  ]);
  const profileWrite = useCustomerMutationGuard(writeContext);
  const researchWrite = useCustomerMutationGuard(
    createStructuredClientScopeKey([optionalExperienceScope(experience), 'intake-research']),
  );
  const [vocab, setVocab] = useState<IntakeVocab>(intakeFallbackVocab);
  const [prefilled, setPrefilled] = useState<Record<string, string>>({});
  const [updatedAtLabel, setUpdatedAtLabel] = useState('');
  const [liveState, setLiveState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(live ? 'loading' : 'fixture');
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'failed' | 'forbidden'>(
    'idle',
  );
  const [researchState, setResearchState] = useState<
    'idle' | 'running' | 'done' | 'disabled' | 'failed' | 'forbidden'
  >('idle');
  const [researchResult, setResearchResult] = useState<IntakeAiResearchSummary | null>(null);
  const [dataVersion, setDataVersion] = useState(0);
  const {
    register,
    control,
    handleSubmit,
    reset,
    setError,
    clearErrors,
    formState: { errors },
  } = useForm<IntakeProfileFormValue>({
    resolver: zodResolver(intakeProfileSchema),
    defaultValues: intakeFixtureDefaults,
  });
  const {
    fields: licenseFields,
    append: appendLicense,
    remove: removeLicense,
  } = useFieldArray({ control, name: 'licenses' });
  const researchForm = useForm<IntakeResearchFormValue>({
    resolver: zodResolver(intakeResearchSchema),
    defaultValues: { brand: '', website: '' },
  });
  useEffect(() => {
    setSaveState('idle');
    setResearchState('idle');
    setResearchResult(null);
  }, [writeContext]);
  useEffect(() => {
    if (!live || !experience?.projectPubId) {
      setLiveState('fixture');
      setVocab(intakeFallbackVocab);
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveState('forbidden');
      return;
    }
    const projectPubId = experience.projectPubId;
    let cancelled = false;
    setLiveState('loading');
    void Promise.all([getIntakeProfile(projectPubId, headers), getIntakeFormSchema()]).then(
      ([profileResult, schemaResult]) => {
        if (cancelled) return;
        const nextVocab =
          schemaResult.kind === 'ready'
            ? intakeVocabFromSchema(schemaResult.data)
            : intakeFallbackVocab;
        setVocab(nextVocab);
        if (profileResult.kind !== 'ready') {
          setLiveState(profileResult.kind === 'forbidden' ? 'forbidden' : 'failed');
          return;
        }
        reset(intakeProfileToForm(profileResult.data, nextVocab.truthItems));
        setPrefilled(profileResult.data.prefilled);
        setUpdatedAtLabel(profileResult.data.updated_at ?? '');
        setLiveState('ready');
      },
    );
    return () => {
      cancelled = true;
    };
  }, [experience, live, retryKey, reset]);
  const submit = handleSubmit(async (value) => {
    if (value.truthItems.length < vocab.truthItems.length) {
      setError('truthItems', {
        type: 'manual',
        message: '请逐条勾选信息真实性确认（全部确认后才能保存）',
      });
      return;
    }
    clearErrors('truthItems');
    if (live && experience?.projectPubId) {
      const headers = getValidatedIdentityHeaders();
      if (!headers) {
        setSaveState('forbidden');
        return;
      }
      const projectPubId = experience.projectPubId;
      const ticket = profileWrite.begin(headers);
      if (!ticket) return;
      setSaveState('saving');
      const result = await putIntakeProfile(projectPubId, intakeFormToWrite(value), headers);
      if (!profileWrite.finish(ticket)) return;
      if (result.kind !== 'ready') {
        setSaveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      reset(intakeProfileToForm(result.data, vocab.truthItems));
      setPrefilled(result.data.prefilled);
      setUpdatedAtLabel(result.data.updated_at ?? '');
    }
    setSaveState('saved');
  });
  const runResearch = researchForm.handleSubmit(async (value) => {
    if (!live || !experience?.projectPubId) return;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setResearchState('forbidden');
      return;
    }
    const projectPubId = experience.projectPubId;
    const ticket = researchWrite.begin(headers);
    if (!ticket) return;
    setResearchState('running');
    setResearchResult(null);
    const selectedModel = readAiOperationModel('intake-research');
    const result = await runIntakeAiResearch(
      projectPubId,
      {
        brand: value.brand,
        ...(value.website ? { website: value.website } : {}),
        ...(selectedModel ? { model: selectedModel } : {}),
      },
      headers,
    );
    if (!researchWrite.finish(ticket)) return;
    if (result.kind !== 'ready') {
      setResearchState(
        result.kind === 'disabled'
          ? 'disabled'
          : result.kind === 'forbidden'
            ? 'forbidden'
            : 'failed',
      );
      return;
    }
    setResearchResult(result.data);
    setResearchState('done');
    const profileResult = await getIntakeProfile(projectPubId, headers);
    if (profileResult.kind === 'ready') {
      reset(intakeProfileToForm(profileResult.data, vocab.truthItems));
      setPrefilled(profileResult.data.prefilled);
      setUpdatedAtLabel(profileResult.data.updated_at ?? '');
    }
    setDataVersion((current) => current + 1);
  });
  if (liveState === 'loading') return <StatePanel state="loading" />;
  if (liveState === 'failed') return <StatePanel state="failed" onRetry={retry} />;
  if (liveState === 'forbidden') return <StatePanel state="forbidden" />;
  const prefillBadge = (key: string) =>
    prefilled[key] ? <Badge tone="info">AI 预填</Badge> : null;
  const droppedCount = researchResult
    ? Object.values(researchResult.dropped).reduce((total, count) => total + count, 0)
    : 0;
  return (
    <>
      <section className="panel form-panel">
        <span className="overline">Intake</span>
        <h2>客户信息收集表</h2>
        <p className="panel-subtitle">
          对齐合同附件二的客户信息表：填写约需 10 分钟，带 AI 预填标的字段请人工核对后再保存。
        </p>
        <form onSubmit={(event) => void runResearch(event)} noValidate>
          <div className="form-grid">
            <Field
              id="intake-research-brand"
              label="品牌名称"
              error={researchForm.formState.errors.brand}
            >
              <input
                id="intake-research-brand"
                placeholder="用于 AI 联网调研预填"
                {...researchForm.register('brand')}
              />
            </Field>
            <Field
              id="intake-research-website"
              label="官网（选填）"
              error={researchForm.formState.errors.website}
            >
              <input
                id="intake-research-website"
                placeholder="可作为调研起点"
                {...researchForm.register('website')}
              />
            </Field>
          </div>
          <div className="form-actions">
            <span>
              {live
                ? 'AI 只预填当前为空的字段，绝不覆盖已确认内容。'
                : 'Contract fixture：AI 联网调研仅在真实项目环境执行。'}
            </span>
            <button className="button" disabled={!live || researchState === 'running'}>
              {researchState === 'running' ? 'AI 调研中…' : 'AI 一键调研预填'}
            </button>
          </div>
          {researchState === 'running' ? (
            <p role="status">AI 调研可能需要 1-3 分钟，请保持页面打开并稍候…</p>
          ) : null}
          {researchState === 'disabled' ? (
            <Toast tone="warning">AI 调研未配置（服务端未启用调研模型），请手工填写。</Toast>
          ) : null}
          {researchState === 'failed' ? (
            <Toast tone="negative">AI 调研失败，请稍后重试或手工填写。</Toast>
          ) : null}
          {researchState === 'forbidden' ? (
            <Toast tone="negative">无权在此项目执行 AI 调研。</Toast>
          ) : null}
          {researchState === 'done' && researchResult ? (
            <div className="confirmation" role="status">
              <Badge tone="positive">调研完成</Badge>
              <span>
                共 {researchResult.rounds} 轮 · 预填 {researchResult.prefilled.length} 个字段 ·
                新建推广内容 {researchResult.promosCreated} 条 · 收录问法{' '}
                {researchResult.triggersCreated} 条
                {researchResult.triggersSkipped
                  ? `（跳过重复 ${researchResult.triggersSkipped} 条）`
                  : ''}
                {droppedCount ? ` · 词表外丢弃 ${droppedCount} 项` : ''}
                {researchResult.unavailable.length
                  ? ` · 公开渠道查不到 ${researchResult.unavailable.length} 项`
                  : ''}
              </span>
              {researchResult.summary ? <p>{researchResult.summary}</p> : null}
              {researchResult.sources.length ? (
                <ul>
                  {researchResult.sources.map((source) => (
                    <li key={source.url}>
                      {source.title || source.url}（{source.url}）
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </form>
        {live && experience?.projectPubId ? (
          <VerifiedBlobDownload
            fileName={`intake-profile-${experience.projectPubId}.docx`}
            resourceKey={createStructuredClientScopeKey([
              experience.projectPubId,
              'intake-profile-docx',
              updatedAtLabel || 'draft',
            ])}
            label="导出 Word 信息表"
            failureLabel="信息表 docx 校验失败"
            successLabel="信息表 docx 已下载"
            load={async () => {
              const headers = getValidatedIdentityHeaders();
              if (!headers || !experience?.projectPubId) return { kind: 'forbidden' };
              const result = await getIntakeProfileDocx(experience.projectPubId, headers);
              return result.kind === 'ready'
                ? { kind: 'ready', blob: result.data.blob }
                : result.kind === 'forbidden'
                  ? { kind: 'forbidden' }
                  : { kind: 'unavailable' };
            }}
          />
        ) : null}
      </section>
      <form onSubmit={(event) => void submit(event)} noValidate>
        <section className="panel form-panel">
          <h2>联系与品牌触点</h2>
          <div className="form-grid">
            <Field id="intake-contact-person" label="联系人 ★" error={errors.contactPerson}>
              {prefillBadge('contact_person')}
              <input id="intake-contact-person" {...register('contactPerson')} />
            </Field>
            <Field
              id="intake-contact-info"
              label="联系方式（手机 / 微信 / 邮箱）★"
              error={errors.contactInfo}
            >
              {prefillBadge('contact_info')}
              <input id="intake-contact-info" {...register('contactInfo')} />
            </Field>
            <Field id="intake-website" label="官网" error={errors.website}>
              {prefillBadge('website')}
              <input id="intake-website" {...register('website')} />
            </Field>
            <Field id="intake-wechat" label="微信公众号" error={errors.wechat}>
              {prefillBadge('wechat')}
              <input id="intake-wechat" {...register('wechat')} />
            </Field>
            <Field id="intake-douyin" label="抖音号" error={errors.douyin}>
              {prefillBadge('douyin')}
              <input id="intake-douyin" {...register('douyin')} />
            </Field>
            <Field id="intake-filler-name" label="填表人" error={errors.fillerName}>
              {prefillBadge('filler_name')}
              <input id="intake-filler-name" {...register('fillerName')} />
            </Field>
          </div>
          <Field id="intake-social-media" label="其他社媒账号" error={errors.socialMedia}>
            {prefillBadge('social_media')}
            <textarea id="intake-social-media" rows={2} {...register('socialMedia')} />
          </Field>
          <Field id="intake-audience-desc" label="目标客群描述" error={errors.audienceDesc}>
            {prefillBadge('audience_desc')}
            <textarea id="intake-audience-desc" rows={2} {...register('audienceDesc')} />
          </Field>
        </section>
        <section className="panel form-panel">
          <h2>宣传内容与目标</h2>
          <Field
            id="intake-review-category"
            label="行业广告审查分类 ★"
            error={errors.reviewCategory}
          >
            {[{ value: '', label: '暂不填写' }, ...vocab.reviewCategoryOptions].map((option) => (
              <label key={option.value || 'unset'} className="check-field">
                <input type="radio" value={option.value} {...register('reviewCategory')} />
                {option.label}
              </label>
            ))}
          </Field>
          <label className="check-field">
            <input type="checkbox" {...register('preReviewRequired')} />
            属于法定前置审查行业（选是须提供广告审查批准文件）
          </label>
          <Field id="intake-goals" label="推广目标 ★" error={errors.goals}>
            {prefillBadge('goals')}
            <div className="form-grid form-grid-three">
              {vocab.goals.map((goal) => (
                <label key={goal} className="check-field">
                  <input type="checkbox" value={goal} {...register('goals')} />
                  {goal}
                </label>
              ))}
            </div>
          </Field>
          <Field id="intake-audience-type" label="客群类型" error={errors.audienceType}>
            {prefillBadge('audience_type')}
            <div className="form-grid form-grid-three">
              {vocab.audienceTypes.map((option) => (
                <label key={option} className="check-field">
                  <input type="checkbox" value={option} {...register('audienceType')} />
                  {option}
                </label>
              ))}
            </div>
          </Field>
          <Field
            id="intake-platforms"
            label="目标 AI 平台"
            hint="也可交由我方建议"
            error={errors.platforms}
          >
            {prefillBadge('platforms')}
            <div className="form-grid form-grid-three">
              {vocab.platforms.map((option) => (
                <label key={option} className="check-field">
                  <input type="checkbox" value={option} {...register('platforms')} />
                  {option}
                </label>
              ))}
            </div>
          </Field>
          <Field
            id="intake-regions"
            label="重点地域"
            hint="全国 或 重点区域（如 华东, 上海），每行一条"
            error={errors.regionsText}
          >
            {prefillBadge('regions')}
            <textarea id="intake-regions" rows={2} {...register('regionsText')} />
          </Field>
          <Field
            id="intake-selling-points"
            label="核心卖点 ★"
            hint="与同类相比，为什么应该推荐您？200 字以内，每条卖点需有出处（认证、数据、案例等）。"
            error={errors.sellingPoints}
          >
            {prefillBadge('selling_points')}
            <textarea id="intake-selling-points" rows={3} {...register('sellingPoints')} />
          </Field>
          <Field
            id="intake-evidence"
            label="可公开引用的佐证材料"
            hint="官网、检测报告、权威媒体报道、行业奖项等，每行一条链接或说明"
            error={errors.evidenceText}
          >
            {prefillBadge('evidence_links')}
            <textarea id="intake-evidence" rows={3} {...register('evidenceText')} />
          </Field>
        </section>
        <section className="panel form-panel">
          <h2>资质与合规</h2>
          <div className="form-grid">
            <Field
              id="intake-license-code"
              label="营业执照 · 统一社会信用代码 ★"
              hint="18 位（0-9/A-Z），扫描件线下交运营方归档"
              error={errors.businessLicenseCode}
            >
              {prefillBadge('business_license_code')}
              <input id="intake-license-code" {...register('businessLicenseCode')} />
            </Field>
            <Field
              id="intake-ad-review-no"
              label="广告审查批准文号"
              hint="仅 A 类行业必填"
              error={errors.adReviewNo}
            >
              {prefillBadge('ad_review_no')}
              <input id="intake-ad-review-no" {...register('adReviewNo')} />
            </Field>
            <Field
              id="intake-ad-review-authority"
              label="审查机关"
              error={errors.adReviewAuthority}
            >
              {prefillBadge('ad_review_authority')}
              <input id="intake-ad-review-authority" {...register('adReviewAuthority')} />
            </Field>
            <Field id="intake-ad-review-expiry" label="有效期至" error={errors.adReviewExpiry}>
              {prefillBadge('ad_review_expiry')}
              <input id="intake-ad-review-expiry" type="date" {...register('adReviewExpiry')} />
            </Field>
          </div>
          <Field
            id="intake-ad-review-doc-types"
            label="广告审查批准文件（A 类行业必填）"
            error={errors.adReviewDocTypes}
          >
            {prefillBadge('ad_review_doc_types')}
            <div className="form-grid form-grid-three">
              {vocab.adReviewDocTypes.map((option) => (
                <label key={option} className="check-field">
                  <input type="checkbox" value={option} {...register('adReviewDocTypes')} />
                  {option}
                </label>
              ))}
            </div>
          </Field>
          <Field
            id="intake-trademarks"
            label="商标 / 品牌权属证明"
            hint="商标注册证或授权文件（选填），每行一条"
            error={errors.trademarksText}
          >
            {prefillBadge('trademarks')}
            <textarea id="intake-trademarks" rows={2} {...register('trademarksText')} />
          </Field>
          <Field
            id="intake-licenses"
            label="行业许可证（须持证经营行业必填）"
            error={errors.licenses}
          >
            {prefillBadge('licenses')}
            {licenseFields.map((field, index) => (
              <div className="form-grid form-grid-three" key={field.id}>
                <input
                  aria-label={`许可证 ${index + 1} 名称`}
                  placeholder="证照名称"
                  {...register(`licenses.${index}.name`)}
                />
                <input
                  aria-label={`许可证 ${index + 1} 编号`}
                  placeholder="编号"
                  {...register(`licenses.${index}.number`)}
                />
                <span>
                  <input
                    aria-label={`许可证 ${index + 1} 有效期至`}
                    type="date"
                    {...register(`licenses.${index}.expiry`)}
                  />{' '}
                  <button
                    type="button"
                    className="button button-ghost"
                    onClick={() => removeLicense(index)}
                  >
                    删除
                  </button>
                </span>
              </div>
            ))}
            <button
              type="button"
              className="button button-ghost"
              onClick={() => appendLicense({ name: '', number: '', expiry: '' })}
            >
              添加许可证
            </button>
          </Field>
        </section>
        <section className="panel form-panel">
          <h2>信息真实性确认 ★</h2>
          <p className="panel-subtitle">请逐条确认（合同附件二原文），全部勾选后才能保存。</p>
          {vocab.truthItems.map((item) => (
            <label key={item} className="check-field">
              <input type="checkbox" value={item} {...register('truthItems')} />
              {item}
            </label>
          ))}
          {errors.truthItems ? (
            <span className="field-error" role="alert">
              {errors.truthItems.message}
            </span>
          ) : null}
          <div className="form-actions">
            <span>
              {live
                ? updatedAtLabel
                  ? `上次保存 ${updatedAtLabel}`
                  : '尚未保存'
                : 'Contract fixture：保存仅作用于当前演示会话。'}
            </span>
            <button className="button" disabled={saveState === 'saving'}>
              {saveState === 'saving' ? '正在保存…' : '保存信息表'}
            </button>
          </div>
          {saveState === 'saved' ? <Toast>信息表已保存</Toast> : null}
          {saveState === 'failed' ? (
            <Toast tone="negative">保存失败，表单内容已保留，请稍后重试。</Toast>
          ) : null}
          {saveState === 'forbidden' ? (
            <Toast tone="negative">无权保存此项目的信息表。</Toast>
          ) : null}
        </section>
      </form>
      <IntakePromoSection
        live={live}
        projectPubId={experience?.projectPubId ?? ''}
        dataVersion={dataVersion}
        prefilledMark={Boolean(prefilled.promos)}
      />
      <IntakeTriggerSection
        live={live}
        projectPubId={experience?.projectPubId ?? ''}
        dataVersion={dataVersion}
        prefilledMark={Boolean(prefilled.trigger_questions)}
      />
    </>
  );
}

function Placeholder({ active }: { active: string }) {
  const labels: Record<string, string> = {
    home: '项目总览',
    profile: '甲方资料',
    assets: '品牌、产品与竞品',
    questions: '问题、目标与配置申请',
    evidence: 'AI 回答与证据中心',
    reports: '报告、确认与导出',
  };
  return (
    <section className="panel">
      <h2>{labels[active] ?? '工作区'}</h2>
      <p className="panel-subtitle">
        此纵向页面已接入统一权限、状态和响应式外壳，详细交互正在持续补齐。
      </p>
      <div className="card-grid">
        <article className="action-card">
          <Badge tone="positive">已同步</Badge>
          <h3>项目数据</h3>
          <p>来自 OpenAPI contract fixture；真实接口可用后保持同一投影替换。</p>
        </article>
        <article className="action-card">
          <Badge tone="info">可追溯</Badge>
          <h3>证据与版本</h3>
          <p>所有结论保留冻结窗口、版本和贡献证据入口。</p>
        </article>
        <article className="action-card">
          <Badge tone="warning">待处理 2</Badge>
          <h3>客户待办</h3>
          <p>资料确认与报告问题将在这里完成闭环。</p>
        </article>
      </div>
    </section>
  );
}

// ══ AI 操作右栏（20260807 起）：可展开/折叠，列出所有使用 AI 的操作；══════════════
// 每个操作带下拉抽屉（<details>）选择模型。模型清单由服务端下发
// （GEO_RESEARCH_LLM_MODELS 为唯一真源），选择记忆在 localStorage（geo.ai.model.<opId>），
// 提交时由对应工作区读取。
const aiDockExpandedKey = 'geo.ai.dock.expanded';
const aiOperationModelKey = (opId: string) => `geo.ai.model.${opId}`;

type AiOperation = Readonly<{ id: string; label: string; description: string }>;
const aiOperations: readonly AiOperation[] = [
  {
    id: 'intake-research',
    label: 'AI 一键调研预填',
    description: '联网调研品牌公开信息，预填客户信息表的空字段（绝不覆盖已确认内容）。',
  },
];

const readAiDockStorage = (key: string): string => {
  try {
    return localStorage.getItem(key)?.trim() ?? '';
  } catch {
    return '';
  }
};
const writeAiDockStorage = (key: string, value: string): void => {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    // 隐私模式等写失败仅影响记忆，不影响功能
  }
};

/** 工作区提交 AI 操作时读取面板选中的模型；空 = 服务端缺省模型。 */
export const readAiOperationModel = (opId: string): string =>
  readAiDockStorage(aiOperationModelKey(opId));

function AiOpsDock() {
  const experience = useOptionalExperienceContext();
  const live = experience?.source === 'live' && Boolean(experience?.projectPubId);
  const [expanded, setExpanded] = useState(() => {
    const stored = readAiDockStorage(aiDockExpandedKey);
    if (stored) return stored !== '0';
    // 首访默认收起：操作入口常驻，但不遮挡客户仪表盘、筛选器和回答原文。
    return false;
  });
  const [catalog, setCatalog] = useState<ResearchModelCatalog | null>(null);
  const [pinned, setPinned] = useState(() => readAiOperationModel('intake-research'));
  useEffect(() => {
    if (!live || !experience?.projectPubId) {
      // 幂等重置：catalog 初值为 null，重复 set 同值时 React 短路、不触发重渲染。
      setCatalog((current) => (current === null ? current : null));
      return;
    }
    // 面板收起时不拉模型清单：收起态不展示清单，且首访/窄屏默认收起，
    // 挂载即拉取会给每个 live 会话增加一笔与当前操作无关的后台请求。
    if (!expanded || catalog) return;
    const headers = getValidatedIdentityHeaders();
    if (!headers) return;
    const projectPubId = experience.projectPubId;
    let cancelled = false;
    void getIntakeResearchModels(projectPubId, headers).then((result) => {
      if (!cancelled && result.kind === 'ready') setCatalog(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [experience, live, expanded, catalog]);
  const models = catalog?.models ?? [];
  const groups = catalog?.groups ?? [];
  const toggle = () => {
    setExpanded((current) => {
      writeAiDockStorage(aiDockExpandedKey, current ? '0' : '1');
      return !current;
    });
  };
  const effective = pinned && models.includes(pinned) ? pinned : (models[0] ?? '');
  const choose = (opId: string, model: string) => {
    // 选中缺省模型（清单首位）时清除记忆，跟随服务端缺省漂移
    const next = model === models[0] ? '' : model;
    writeAiDockStorage(aiOperationModelKey(opId), next);
    setPinned(next);
  };
  return (
    <aside className={expanded ? 'ai-dock ai-dock-open' : 'ai-dock'} aria-label="AI 操作面板">
      <button
        type="button"
        className="ai-dock-toggle"
        onClick={toggle}
        aria-expanded={expanded}
        aria-label={expanded ? '收起 AI 面板' : '展开 AI 面板'}
      >
        {expanded ? '收起 AI 面板 ›' : '‹ AI'}
      </button>
      {expanded ? (
        <div className="ai-dock-body">
          <h2 className="ai-dock-title">AI 操作</h2>
          <p className="ai-dock-subtitle">系统内所有使用 AI 的操作；点开可为每个操作单独选模型。</p>
          {aiOperations.map((op) => (
            <details key={op.id} className="ai-op" open={aiOperations.length === 1}>
              <summary>
                <span>{op.label}</span>
                <span className="ai-op-model">{pinned || '默认模型'}</span>
              </summary>
              <div className="ai-op-body">
                <p>{op.description}</p>
                <label>
                  调研模型
                  <select
                    aria-label={`${op.label}模型选择`}
                    value={effective}
                    disabled={models.length === 0}
                    onChange={(event) => choose(op.id, event.target.value)}
                  >
                    {models.length ? (
                      groups.length ? (
                        // 同 provider 的模型归为一组（级联选项）
                        groups.map((group) => (
                          <optgroup key={group.provider} label={group.provider}>
                            {group.models.map((model) => (
                              <option key={model} value={model}>
                                {model === models[0] ? `${model}（默认）` : model}
                              </option>
                            ))}
                          </optgroup>
                        ))
                      ) : (
                        models.map((model, index) => (
                          <option key={model} value={model}>
                            {index === 0 ? `${model}（默认）` : model}
                          </option>
                        ))
                      )
                    ) : (
                      <option value="">
                        {live ? '模型清单加载中…' : '登录真实项目后可选模型'}
                      </option>
                    )}
                  </select>
                </label>
              </div>
            </details>
          ))}
        </div>
      ) : null}
    </aside>
  );
}

export default function Shell() {
  const experience = useOptionalExperienceContext();
  return (
    <>
      <ProductShell
        product="Customer Web"
        title="客户工作台"
        description="围绕已授权五项服务、报告交付和项目资料的客户安全入口。"
        nav={experience?.source === 'live' ? liveNav : nav}
        probe={getHealth}
      >
        {(active) => (
          <>
            {experience?.source === 'live' &&
            ![
              'home',
              'services',
              'service-1',
              'service-2',
              'service-3',
              'service-4',
              'service-5',
              'answers',
              'profile',
              'intake',
              'assets',
              'monitoring',
              'competition',
              'sources',
              'reputation',
              'opportunities',
              'accounts',
              'questions',
              'evidence',
              'reports',
              'members',
            ].includes(active) ? (
              <StatePanel state="insufficient" />
            ) : active === 'home' ? (
              <CustomerAnalyticsWorkspace focus="overview" />
            ) : active === 'services' ? (
              <CustomerServicesWorkspace />
            ) : active === 'service-1' ? (
              <CustomerServicesWorkspace focus={1} />
            ) : active === 'service-2' ? (
              <CustomerServicesWorkspace focus={2} />
            ) : active === 'service-3' ? (
              <CustomerServicesWorkspace focus={3} />
            ) : active === 'service-4' ? (
              <CustomerServicesWorkspace focus={4} />
            ) : active === 'service-5' ? (
              <CustomerServicesWorkspace focus={5} />
            ) : active === 'monitoring' ? (
              <CustomerAnalyticsWorkspace focus="visibility" />
            ) : active === 'answers' ? (
              <CustomerAnalyticsWorkspace focus="answers" />
            ) : active === 'competition' ? (
              <CustomerAnalyticsWorkspace focus="competition" />
            ) : active === 'sources' ? (
              <CustomerAnalyticsWorkspace focus="sources" />
            ) : active === 'reputation' ? (
              <CustomerAnalyticsWorkspace focus="reputation" />
            ) : active === 'opportunities' ? (
              <CustomerAnalyticsWorkspace focus="opportunities" />
            ) : active === 'accounts' ? (
              <Accounts />
            ) : active === 'profile' ? (
              <ProfileWorkspace />
            ) : active === 'intake' ? (
              <IntakeWorkspace />
            ) : active === 'assets' ? (
              <AssetsWorkspace />
            ) : active === 'questions' ? (
              <QuestionsWorkspace />
            ) : active === 'evidence' ? (
              <EvidenceWorkspace />
            ) : active === 'reports' ? (
              <ReportsWorkspace />
            ) : active === 'members' ? (
              <MembersWorkspace />
            ) : (
              <Placeholder active={active} />
            )}
            {active === 'intake' ? <AiOpsDock /> : null}
          </>
        )}
      </ProductShell>
    </>
  );
}
