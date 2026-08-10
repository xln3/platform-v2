import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { Layer, Rect, Stage, Text } from 'react-konva';
import { useForm } from 'react-hook-form';
import {
  Badge,
  clearSafePdfCanvas,
  containsClientSecret,
  createSafeExperienceScopeKey,
  createStructuredClientScopeKey,
  MetricGrid,
  Pagination,
  ProjectionLimitNotice,
  ProductShell,
  projectSafePdfPageViewport,
  safePdfDocumentOptions,
  StatePanel,
  TableRegion,
  Toast,
  VerifiedBlobDownload,
  useOptionalExperienceContext,
} from '@geo/design-system';
import {
  commentOnReport,
  createReport,
  createReportDelivery,
  createReportEffectRetest,
  createReportAction,
  createReportRevision,
  draftReportSection,
  getReportArtifact,
  getHealth,
  getReport,
  getReportAiDraftModels,
  isReportVersionPubId,
  listReportDeliveries,
  loadProjectReportCatalog,
  projectSafeIsoTimestamp,
  publishReport,
  reportDetailReadProjectionLimits,
  reviewReport,
  updateReportAction,
  type ReportArtifactFormat,
  type ReportDetailProjection,
  type ReportPageProjection,
  type ProjectReportCatalogProjection,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { WorkflowTimeline } from '@geo/workflow-ui';
import { useSearchParams } from 'react-router';
import { z } from 'zod';
import './ai-dock.css';
import { FactSuggestionsPanel } from './fact-suggestions';

const nav = [
  { id: 'window', label: '数据窗口' },
  { id: 'trace', label: 'KPI Trace' },
  { id: 'editor', label: '章节编辑' },
  { id: 'diff', label: '版本对比' },
  { id: 'evidence', label: '证据编排' },
  { id: 'preview', label: 'PDF 预览' },
  { id: 'review', label: '审核发布', badge: '3' },
  { id: 'outcomes', label: '效果复盘' },
];
const liveNav = nav.map((item) => ({ id: item.id, label: item.label }));

type ReportState = 'draft' | 'frozen' | 'review' | 'approved' | 'published';
type ReportCapabilities = {
  author: boolean;
  review: boolean;
  publish: boolean;
  deliver: boolean;
};
type ReportReleaseReconciliation =
  | { kind: 'comment'; commentPubId: string; body: string; receipt: string }
  | { kind: 'review'; reviewPubId: string; receipt: string }
  | { kind: 'publish'; receipt: string }
  | { kind: 'delivery'; deliveryPubId: string; recipientPubId: string; receipt: string };
type ReportOutcomeReconciliation =
  | { kind: 'action'; actionPubId: string; receipt: string }
  | {
      kind: 'retest';
      actionPubId: string;
      effectRetestPubId: string;
      receipt: string;
    };
type ReportRevisionReconciliation = {
  reportVersionPubId: string;
  versionNumber: number;
  receipt: string;
};

function useLocalRetry(): [number, () => void] {
  const [retryKey, setRetryKey] = useState(0);
  return [retryKey, () => setRetryKey((current) => current + 1)];
}

type ReportMutationTicket = Readonly<{
  context: string;
  generation: number;
  identity: string;
}>;

const reportIdentityContext = (headers: ReturnType<typeof getValidatedIdentityHeaders>): string => {
  const tenant = headers?.['X-Tenant-Id'];
  const actor = headers?.['X-Actor-Id'];
  const role = headers?.['X-Actor-Role'];
  return tenant && actor && role ? createStructuredClientScopeKey([tenant, actor, role]) : '';
};

function useReportMutationGuard(context: string) {
  const active = useRef(false);
  const generation = useRef(0);
  const contextRef = useRef(context);
  if (contextRef.current !== context) {
    contextRef.current = context;
    generation.current += 1;
    active.current = false;
  }
  useEffect(
    () => () => {
      generation.current += 1;
      active.current = false;
    },
    [],
  );
  return {
    begin(
      headers: NonNullable<ReturnType<typeof getValidatedIdentityHeaders>>,
    ): ReportMutationTicket | null {
      if (active.current) return null;
      const identity = reportIdentityContext(headers);
      if (!identity) return null;
      active.current = true;
      generation.current += 1;
      return { context: contextRef.current, generation: generation.current, identity };
    },
    isCurrent(ticket: ReportMutationTicket): boolean {
      return (
        active.current &&
        ticket.context === contextRef.current &&
        ticket.generation === generation.current &&
        ticket.identity === reportIdentityContext(getValidatedIdentityHeaders())
      );
    },
    finish(ticket: ReportMutationTicket): boolean {
      if (
        ticket.context !== contextRef.current ||
        ticket.generation !== generation.current ||
        !active.current
      ) {
        return false;
      }
      active.current = false;
      return ticket.identity === reportIdentityContext(getValidatedIdentityHeaders());
    },
  };
}

const reportCommentSchema = z.object({
  comment: z
    .string()
    .trim()
    .min(4, '评论至少需要 4 个字')
    .max(2000, '评论不能超过 2000 个字')
    .refine(
      (value) => !containsClientSecret(value),
      '请勿在评论中粘贴验证码、Cookie、token、密码、完整手机号或 profile 路径',
    ),
});
type ReportCommentFields = z.infer<typeof reportCommentSchema>;

const reportDeliverySchema = z.object({
  recipientPubId: z
    .string()
    .trim()
    .regex(/^usr_[A-Za-z0-9_-]{1,116}$/, '只接受不含秘密的 usr_ 客户公开标识')
    .refine((value) => !containsClientSecret(value), '只接受不含秘密的 usr_ 客户公开标识'),
});
type ReportDeliveryFields = z.infer<typeof reportDeliverySchema>;

const reportSectionSchema = z.object({
  body: z
    .string()
    .min(1, '章节正文不能为空')
    .max(100_000, '章节正文不能超过 100000 个字')
    .refine(
      (value) => !containsClientSecret(value),
      '请移除验证码、Cookie、token、密码、完整手机号或 profile 路径后再保存',
    ),
});
type ReportSectionFields = z.infer<typeof reportSectionSchema>;

const reportRetestSchema = z.object({
  delta: z
    .number({ error: '请输入有效的效果变化' })
    .min(-100, '效果变化必须在 -100 到 100 之间')
    .max(100, '效果变化必须在 -100 到 100 之间'),
});
type ReportRetestFields = z.infer<typeof reportRetestSchema>;

const splitReportEvidencePubIds = (value: string) =>
  value
    .split(/[\s,]+/)
    .map((candidate) => candidate.trim())
    .filter(Boolean);

const reportRevisionSchema = z.object({
  sections: z
    .array(
      z.object({
        title: z
          .string()
          .trim()
          .min(1, '章节标题不能为空')
          .max(200, '章节标题不能超过 200 个字')
          .refine(
            (value) => !containsClientSecret(value),
            '章节标题不能包含验证码、Cookie、token、密码、完整手机号或 profile 路径',
          ),
        body: reportSectionSchema.shape.body,
        source: z.enum(['system', 'ai', 'human']),
        evidenceText: z
          .string()
          .max(12_999, '组件证据 ID 列表过长')
          .refine((value) => !containsClientSecret(value), '证据绑定只接受不含秘密的 evd_ 公开标识')
          .refine((value) => {
            const evidencePubIds = splitReportEvidencePubIds(value);
            return (
              evidencePubIds.length <= 100 &&
              evidencePubIds.every((candidate) => /^evd_[A-Za-z0-9_-]{1,116}$/.test(candidate))
            );
          }, '证据绑定只接受不含秘密的 evd_ 公开标识，且每个组件最多 100 条'),
      }),
    )
    .min(1, '至少需要一个报告章节')
    .max(100, '每个报告版本最多包含 100 个章节'),
});
type ReportRevisionFields = z.infer<typeof reportRevisionSchema>;

export const reportProjectionLimits = {
  versions: 2,
  facts: reportDetailReadProjectionLimits.frozenFacts,
  sections: reportDetailReadProjectionLimits.components,
  sectionEvidenceIds: reportDetailReadProjectionLimits.sectionEvidenceIds,
  artifacts: reportDetailReadProjectionLimits.artifacts,
  evidenceBindings: reportDetailReadProjectionLimits.evidenceBindings,
  comments: reportDetailReadProjectionLimits.comments,
  reviews: reportDetailReadProjectionLimits.reviews,
  events: reportDetailReadProjectionLimits.events,
  actions: reportDetailReadProjectionLimits.optimizationActions,
  effectRetests: reportDetailReadProjectionLimits.effectRetests,
} as const;
type ReportProjectionCollection = keyof typeof reportProjectionLimits;
type ReportProjectionNotices = Partial<
  Record<ReportProjectionCollection, { total: number; shown: number }>
>;

type LiveReportTarget = {
  reportPubId: string;
  versionPubId: string;
  versionNumber: number;
  status: string;
  facts: { label: string; value: string; hash: string }[];
  sections: { title: string; body: string; source: string; evidencePubIds: string[] }[];
  artifacts: {
    format: ReportArtifactFormat;
    byteSize: number;
    mimeType: string;
    sha256: string;
  }[];
  evidenceBindings: {
    id: string;
    kind: string;
    purpose: string;
    mimeType: string;
    byteSize: number;
    hash: string;
    anchorCount: number;
  }[];
  comments: { id: string; body: string; resolved: boolean; createdAt: string }[];
  reviews: { id: string; decision: string; rationale: string; createdAt: string }[];
  events: { id: string; type: string; createdAt: string }[];
  actions: {
    pubId: string;
    description: string;
    state: string;
    createdAt: string;
    retests: { pubId: string; measuredAt: string; result: string }[];
  }[];
  versions: {
    versionNumber: number;
    sections: { title: string; body: string; source: string; evidencePubIds: string[] }[];
  }[];
  projectionNotices: ReportProjectionNotices;
  invalidProjection: ReportProjectionCollection[];
};

type Section = {
  id: string;
  title: string;
  body: string;
  provenance: 'ai' | 'human';
  modified: string;
};
type SectionVersion = {
  sectionId: string;
  version: number;
  title: string;
  body: string;
  savedBy: string;
  savedAt: string;
};
const initialSections: Section[] = [
  {
    id: 'summary',
    title: '执行摘要',
    body: '本窗口共获得 38 个有效回答，品牌提及率为 68.4%，较上一窗口提升 6.2 个百分点。',
    provenance: 'human',
    modified: '分析师 · 10:42',
  },
  {
    id: 'model',
    title: '模型差异分析',
    body: '豆包渠道的品牌提及率领先，但 DeepSeek 的独立来源覆盖更均衡。建议优先补齐制造业决策类内容。',
    provenance: 'ai',
    modified: 'AI 草稿 · 待确认',
  },
  {
    id: 'action',
    title: '优化建议',
    body: '补充私有化部署、权限审计与知识更新机制的权威材料，并在 30 天后复测。',
    provenance: 'human',
    modified: '项目经理 · 昨天',
  },
];
const initialVersions: SectionVersion[] = [
  {
    sectionId: 'published-summary',
    version: 1,
    title: '执行摘要',
    body: '本窗口共获得 36 个有效回答，品牌提及率为 62.2%，较上一窗口提升 3.1 个百分点。',
    savedBy: '分析师 · 林澈',
    savedAt: '2026-07-23 16:20',
  },
  {
    sectionId: 'published-summary',
    version: 2,
    title: '执行摘要',
    body: '本窗口共获得 38 个有效回答，品牌提及率为 68.4%，较上一窗口提升 6.2 个百分点。',
    savedBy: '分析师 · 林澈',
    savedAt: '2026-07-24 10:42',
  },
];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export function projectLiveReportTarget(
  value: unknown,
  expectedReportPubId?: string,
  expectedProjectPubId?: string,
): LiveReportTarget | null {
  if (!isRecord(value) || !Array.isArray(value.versions)) return null;
  const reportPubId =
    typeof value.pub_id === 'string' &&
    /^rpt_[A-Za-z0-9_-]{1,116}$/.test(value.pub_id) &&
    !containsClientSecret(value.pub_id)
      ? value.pub_id
      : '';
  const projectPubId =
    typeof value.project_pub_id === 'string' &&
    /^prj_[A-Za-z0-9_-]{1,116}$/.test(value.project_pub_id) &&
    !containsClientSecret(value.project_pub_id)
      ? value.project_pub_id
      : '';
  const strictRoot = expectedReportPubId !== undefined || expectedProjectPubId !== undefined;
  const rootTitle =
    typeof value.title === 'string' &&
    value.title.length > 0 &&
    value.title.length <= 240 &&
    !containsClientSecret(value.title)
      ? value.title
      : '';
  const rootCreatedAt = projectSafeIsoTimestamp(value.created_at);
  const rootUpdatedAt = projectSafeIsoTimestamp(value.updated_at);
  if (
    !reportPubId ||
    (expectedReportPubId !== undefined && reportPubId !== expectedReportPubId) ||
    (expectedProjectPubId !== undefined && projectPubId !== expectedProjectPubId) ||
    (strictRoot &&
      (!projectPubId ||
        !rootTitle ||
        !rootCreatedAt ||
        !rootUpdatedAt ||
        new Date(rootUpdatedAt).getTime() < new Date(rootCreatedAt).getTime()))
  ) {
    return null;
  }

  const projectionNotices: ReportProjectionNotices = {};
  const invalidProjection = new Set<ReportProjectionCollection>();
  const addNotice = (collection: ReportProjectionCollection, total: number, shown: number) => {
    const current = projectionNotices[collection];
    projectionNotices[collection] = current
      ? { total: current.total + total, shown: current.shown + shown }
      : { total, shown };
  };
  const safeText = (candidate: unknown, fallback: string, maxLength = 120) =>
    typeof candidate === 'string' &&
    candidate.length <= maxLength &&
    !containsClientSecret(candidate)
      ? candidate
      : fallback;
  const safeTimestamp = (candidate: unknown) => projectSafeIsoTimestamp(candidate) ?? '';
  const safeStructuredRecord = (
    candidate: unknown,
    maxLength = 20_000,
  ): candidate is Record<string, unknown> => {
    if (!isRecord(candidate)) return false;
    const serialized = JSON.stringify(candidate);
    return serialized.length <= maxLength && !containsClientSecret(serialized);
  };
  const boundedRecords = <T,>(
    candidate: unknown,
    collection: Exclude<
      ReportProjectionCollection,
      'versions' | 'sections' | 'sectionEvidenceIds' | 'artifacts' | 'effectRetests'
    >,
    project: (record: Record<string, unknown>) => T | null,
    direction: 'head' | 'tail' = 'head',
  ): T[] => {
    const values = Array.isArray(candidate) ? candidate : [];
    const limit = reportProjectionLimits[collection];
    const bounded = direction === 'tail' ? values.slice(-limit) : values.slice(0, limit);
    const projected = bounded.flatMap((item) => {
      if (!isRecord(item)) {
        invalidProjection.add(collection);
        return [];
      }
      const result = project(item);
      if (result === null) invalidProjection.add(collection);
      return result === null ? [] : [result];
    });
    if (values.length > limit) addNotice(collection, values.length, projected.length);
    return projected;
  };
  const projectSections = (candidate: unknown, expectedVersionPubId: string) => {
    const values = Array.isArray(candidate) ? candidate : [];
    const bounded = values.slice(0, reportProjectionLimits.sections);
    const seenComponentPubIds = new Set<string>();
    const seenOrdinals = new Set<number>();
    let previousOrdinal = -1;
    const sections = bounded.flatMap((component) => {
      if (!isRecord(component) || !isRecord(component.payload)) {
        invalidProjection.add('sections');
        return [];
      }
      const componentPubId =
        typeof component.pub_id === 'string' &&
        /^rptc_[A-Za-z0-9_-]{1,115}$/.test(component.pub_id) &&
        !containsClientSecret(component.pub_id)
          ? component.pub_id
          : '';
      const ordinal =
        typeof component.ordinal === 'number' &&
        Number.isSafeInteger(component.ordinal) &&
        component.ordinal >= 0
          ? component.ordinal
          : null;
      if (
        !componentPubId ||
        seenComponentPubIds.has(componentPubId) ||
        component.report_version_pub_id !== expectedVersionPubId ||
        component.component_type !== 'section' ||
        ordinal === null ||
        seenOrdinals.has(ordinal) ||
        ordinal <= previousOrdinal ||
        !safeTimestamp(component.created_at)
      ) {
        invalidProjection.add('sections');
        return [];
      }
      const rawTitle = component.payload.title;
      const title = safeText(rawTitle, '报告组件', 200);
      if (rawTitle !== undefined && title === '报告组件' && rawTitle !== '报告组件')
        invalidProjection.add('sections');
      const body = safeText(component.payload.body, '', 100_000);
      const source =
        typeof component.source === 'string' && ['system', 'ai', 'human'].includes(component.source)
          ? component.source
          : '';
      if (!body || !source) {
        invalidProjection.add('sections');
        return [];
      }
      seenComponentPubIds.add(componentPubId);
      seenOrdinals.add(ordinal);
      previousOrdinal = ordinal;
      const rawEvidencePubIds = Array.isArray(component.payload.evidence_pub_ids)
        ? component.payload.evidence_pub_ids
        : [];
      const boundedEvidencePubIds = rawEvidencePubIds.slice(
        0,
        reportProjectionLimits.sectionEvidenceIds,
      );
      const evidencePubIds = [
        ...new Set(
          boundedEvidencePubIds.flatMap((candidate) =>
            typeof candidate === 'string' &&
            /^evd_[A-Za-z0-9_-]{1,116}$/.test(candidate) &&
            !containsClientSecret(candidate)
              ? [candidate]
              : [],
          ),
        ),
      ];
      if (evidencePubIds.length !== boundedEvidencePubIds.length)
        invalidProjection.add('sectionEvidenceIds');
      if (rawEvidencePubIds.length > reportProjectionLimits.sectionEvidenceIds) {
        addNotice('sectionEvidenceIds', rawEvidencePubIds.length, evidencePubIds.length);
      }
      return [{ title, body, source, evidencePubIds }];
    });
    if (values.length > reportProjectionLimits.sections)
      addNotice('sections', values.length, sections.length);
    return sections;
  };

  const retainedVersions: {
    version: Record<string, unknown>;
    versionPubId: string;
    versionNumber: number;
  }[] = [];
  const versionWindow = value.versions.slice(-100);
  const seenVersionPubIds = new Set<string>();
  const seenVersionNumbers = new Set<number>();
  let previousVersionNumber = 0;
  for (const candidate of versionWindow) {
    if (
      !isRecord(candidate) ||
      !isReportVersionPubId(candidate.pub_id) ||
      typeof candidate.version_number !== 'number' ||
      !Number.isSafeInteger(candidate.version_number) ||
      candidate.version_number < 1 ||
      seenVersionPubIds.has(candidate.pub_id) ||
      seenVersionNumbers.has(candidate.version_number) ||
      candidate.version_number <= previousVersionNumber ||
      typeof candidate.status !== 'string' ||
      !['frozen', 'review', 'approved', 'published'].includes(candidate.status)
    ) {
      invalidProjection.add('versions');
      continue;
    }
    seenVersionPubIds.add(candidate.pub_id);
    seenVersionNumbers.add(candidate.version_number);
    previousVersionNumber = candidate.version_number;
    retainedVersions.push({
      version: candidate,
      versionPubId: candidate.pub_id,
      versionNumber: candidate.version_number,
    });
    if (retainedVersions.length > reportProjectionLimits.versions) retainedVersions.shift();
  }
  if (!retainedVersions.length) return null;
  if (value.versions.length > reportProjectionLimits.versions) {
    addNotice('versions', value.versions.length, retainedVersions.length);
  }
  const projectedVersions = retainedVersions
    .map(({ version, versionNumber, versionPubId }) => ({
      versionNumber,
      sections: projectSections(version.components, versionPubId),
    }))
    .sort((left, right) => left.versionNumber - right.versionNumber);
  const selectedVersion = retainedVersions.at(-1)!;
  const selectedSections =
    projectedVersions.find((candidate) => candidate.versionNumber === selectedVersion.versionNumber)
      ?.sections ?? [];

  const seenFactPubIds = new Set<string>();
  const seenFactOrdinals = new Set<number>();
  let previousFactOrdinal = -1;
  const facts = boundedRecords(selectedVersion.version.frozen_facts, 'facts', (fact) => {
    const factPubId =
      typeof fact.pub_id === 'string' &&
      /^rptf_[A-Za-z0-9_-]{1,115}$/.test(fact.pub_id) &&
      !containsClientSecret(fact.pub_id)
        ? fact.pub_id
        : '';
    const ordinal =
      typeof fact.ordinal === 'number' && Number.isSafeInteger(fact.ordinal) && fact.ordinal >= 0
        ? fact.ordinal
        : null;
    if (
      !factPubId ||
      seenFactPubIds.has(factPubId) ||
      fact.report_version_pub_id !== selectedVersion.versionPubId ||
      ordinal === null ||
      seenFactOrdinals.has(ordinal) ||
      ordinal <= previousFactOrdinal ||
      !safeStructuredRecord(fact.payload) ||
      !safeTimestamp(fact.created_at)
    ) {
      return null;
    }
    const label = safeText(fact.payload.metric, `冻结事实 ${ordinal + 1}`, 120);
    const rawValue = fact.payload.value;
    const displayValue =
      typeof rawValue === 'number' && Number.isFinite(rawValue)
        ? String(rawValue)
        : typeof rawValue === 'boolean'
          ? String(rawValue)
          : safeText(rawValue, '', 200);
    const hash =
      typeof fact.payload_hash === 'string' && /^[0-9a-f]{64}$/.test(fact.payload_hash)
        ? fact.payload_hash
        : '';
    if (!displayValue || !hash) return null;
    seenFactPubIds.add(factPubId);
    seenFactOrdinals.add(ordinal);
    previousFactOrdinal = ordinal;
    return { label, value: displayValue, hash };
  });
  const artifacts = (() => {
    const values = Array.isArray(selectedVersion.version.artifacts)
      ? selectedVersion.version.artifacts
      : [];
    const projected: LiveReportTarget['artifacts'] = [];
    const seenFormats = new Set<string>();
    const seenArtifactPubIds = new Set<string>();
    for (const artifact of values) {
      if (projected.length >= reportProjectionLimits.artifacts) break;
      const pubId =
        isRecord(artifact) &&
        typeof artifact.pub_id === 'string' &&
        /^rpta_[A-Za-z0-9_-]{1,115}$/.test(artifact.pub_id) &&
        !containsClientSecret(artifact.pub_id)
          ? artifact.pub_id
          : '';
      if (
        !isRecord(artifact) ||
        !pubId ||
        seenArtifactPubIds.has(pubId) ||
        artifact.report_version_pub_id !== selectedVersion.versionPubId ||
        typeof artifact.format !== 'string' ||
        !['html', 'pdf', 'docx', 'xlsx'].includes(artifact.format) ||
        seenFormats.has(artifact.format) ||
        typeof artifact.evidence_pub_id !== 'string' ||
        !/^evd_[A-Za-z0-9_-]{1,116}$/.test(artifact.evidence_pub_id) ||
        containsClientSecret(artifact.evidence_pub_id) ||
        !safeText(artifact.mime_type, '', 120) ||
        typeof artifact.byte_size !== 'number' ||
        !Number.isSafeInteger(artifact.byte_size) ||
        artifact.byte_size <= 0 ||
        artifact.byte_size > 50 * 1024 * 1024 ||
        typeof artifact.sha256 !== 'string' ||
        !/^[0-9a-f]{64}$/.test(artifact.sha256) ||
        !safeTimestamp(artifact.created_at)
      ) {
        invalidProjection.add('artifacts');
        continue;
      }
      seenFormats.add(artifact.format);
      seenArtifactPubIds.add(pubId);
      projected.push({
        format: artifact.format as ReportArtifactFormat,
        byteSize: artifact.byte_size,
        mimeType: safeText(artifact.mime_type, '', 120),
        sha256: artifact.sha256,
      });
    }
    if (values.length > reportProjectionLimits.artifacts)
      addNotice('artifacts', values.length, projected.length);
    return projected;
  })();
  const seenBindingPubIds = new Set<string>();
  const seenBindingEvidencePubIds = new Set<string>();
  let previousBindingOrder = '';
  const evidenceBindings = boundedRecords(
    selectedVersion.version.evidence_bindings,
    'evidenceBindings',
    (binding) => {
      const bindingPubId =
        typeof binding.pub_id === 'string' &&
        /^rptev_[A-Za-z0-9_-]{1,114}$/.test(binding.pub_id) &&
        !containsClientSecret(binding.pub_id)
          ? binding.pub_id
          : '';
      const evidencePubId =
        typeof binding.evidence_pub_id === 'string' &&
        /^evd_[A-Za-z0-9_-]{1,116}$/.test(binding.evidence_pub_id) &&
        !containsClientSecret(binding.evidence_pub_id)
          ? binding.evidence_pub_id
          : '';
      const byteSize =
        typeof binding.byte_size === 'number' &&
        Number.isSafeInteger(binding.byte_size) &&
        binding.byte_size >= 0
          ? binding.byte_size
          : null;
      const anchorCount =
        typeof binding.anchor_count === 'number' &&
        Number.isSafeInteger(binding.anchor_count) &&
        binding.anchor_count >= 0
          ? binding.anchor_count
          : null;
      const kind = safeText(binding.kind, '', 120);
      const purpose = safeText(binding.purpose, '', 120);
      const accessClass =
        typeof binding.access_class === 'string' &&
        ['public', 'customer_private', 'paid_or_organization'].includes(binding.access_class)
          ? binding.access_class
          : '';
      const mimeType = safeText(binding.mime_type, '', 120);
      const hash =
        typeof binding.sha256 === 'string' && /^[0-9a-f]{64}$/.test(binding.sha256)
          ? binding.sha256
          : '';
      const captureTime = safeTimestamp(binding.capture_time);
      const createdAt = safeTimestamp(binding.created_at);
      const orderKey = createdAt && bindingPubId ? `${createdAt}\u0000${bindingPubId}` : '';
      if (
        !bindingPubId ||
        seenBindingPubIds.has(bindingPubId) ||
        binding.report_version_pub_id !== selectedVersion.versionPubId ||
        !evidencePubId ||
        seenBindingEvidencePubIds.has(evidencePubId) ||
        byteSize === null ||
        anchorCount === null ||
        !kind ||
        purpose !== 'frozen_fact_or_component' ||
        !accessClass ||
        !mimeType ||
        !hash ||
        !captureTime ||
        !createdAt ||
        new Date(createdAt).getTime() < new Date(captureTime).getTime() ||
        (previousBindingOrder !== '' && orderKey <= previousBindingOrder)
      ) {
        return null;
      }
      seenBindingPubIds.add(bindingPubId);
      seenBindingEvidencePubIds.add(evidencePubId);
      previousBindingOrder = orderKey;
      return {
        id: evidencePubId,
        kind: kind || '类型已隐藏',
        purpose: purpose || '用途已隐藏',
        mimeType: mimeType || '类型已隐藏',
        byteSize,
        hash,
        anchorCount,
      };
    },
  );
  const evidenceBindingIds = new Set(evidenceBindings.map((binding) => binding.id));
  const linkedSelectedSections = selectedSections.map((section) => {
    const evidencePubIds = section.evidencePubIds.filter((evidencePubId) =>
      evidenceBindingIds.has(evidencePubId),
    );
    if (evidencePubIds.length !== section.evidencePubIds.length) {
      invalidProjection.add('sectionEvidenceIds');
    }
    return { ...section, evidencePubIds };
  });
  const linkedProjectedVersions = projectedVersions.map((version) =>
    version.versionNumber === selectedVersion.versionNumber
      ? { ...version, sections: linkedSelectedSections }
      : version,
  );
  const seenCommentPubIds = new Set<string>();
  let previousCommentOrder = '';
  const comments = boundedRecords(
    selectedVersion.version.comments,
    'comments',
    (comment) => {
      const id =
        typeof comment.pub_id === 'string' &&
        /^cmt_[A-Za-z0-9_-]{1,116}$/.test(comment.pub_id) &&
        !containsClientSecret(comment.pub_id)
          ? comment.pub_id
          : '';
      const body = safeText(comment.body, '', 2_000);
      const createdAt = safeTimestamp(comment.created_at);
      const parentPubId =
        comment.parent_pub_id === null
          ? null
          : typeof comment.parent_pub_id === 'string' &&
              /^cmt_[A-Za-z0-9_-]{1,116}$/.test(comment.parent_pub_id) &&
              !containsClientSecret(comment.parent_pub_id)
            ? comment.parent_pub_id
            : '';
      const authorPubId =
        typeof comment.author_pub_id === 'string' &&
        /^usr_[A-Za-z0-9_-]{1,116}$/.test(comment.author_pub_id) &&
        !containsClientSecret(comment.author_pub_id)
          ? comment.author_pub_id
          : '';
      const resolvedAt =
        comment.resolved_at === null || comment.resolved_at === undefined
          ? ''
          : safeTimestamp(comment.resolved_at);
      const orderKey = createdAt && id ? `${createdAt}\u0000${id}` : '';
      if (
        !id ||
        seenCommentPubIds.has(id) ||
        comment.report_version_pub_id !== selectedVersion.versionPubId ||
        parentPubId === '' ||
        !authorPubId ||
        !body ||
        !createdAt ||
        (comment.resolved_at !== null && comment.resolved_at !== undefined && !resolvedAt) ||
        (resolvedAt && new Date(resolvedAt).getTime() < new Date(createdAt).getTime()) ||
        (previousCommentOrder !== '' && orderKey <= previousCommentOrder)
      ) {
        return null;
      }
      seenCommentPubIds.add(id);
      previousCommentOrder = orderKey;
      return { id, body, resolved: Boolean(resolvedAt), createdAt };
    },
    'tail',
  );
  const seenReviewPubIds = new Set<string>();
  let previousReviewOrder = '';
  const reviews = boundedRecords(
    selectedVersion.version.reviews,
    'reviews',
    (review) => {
      const id =
        typeof review.pub_id === 'string' &&
        /^rvw_[A-Za-z0-9_-]{1,116}$/.test(review.pub_id) &&
        !containsClientSecret(review.pub_id)
          ? review.pub_id
          : '';
      const reviewerPubId =
        typeof review.reviewer_pub_id === 'string' &&
        /^usr_[A-Za-z0-9_-]{1,116}$/.test(review.reviewer_pub_id) &&
        !containsClientSecret(review.reviewer_pub_id)
          ? review.reviewer_pub_id
          : '';
      const decision =
        typeof review.decision === 'string' &&
        ['approved', 'changes_requested', 'rejected'].includes(review.decision)
          ? review.decision
          : '';
      const rationale = safeText(review.rationale, '', 2_000);
      const createdAt = safeTimestamp(review.created_at);
      const orderKey = createdAt && id ? `${createdAt}\u0000${id}` : '';
      if (
        !id ||
        seenReviewPubIds.has(id) ||
        review.report_version_pub_id !== selectedVersion.versionPubId ||
        !reviewerPubId ||
        !decision ||
        !rationale ||
        !createdAt ||
        (previousReviewOrder !== '' && orderKey <= previousReviewOrder)
      ) {
        return null;
      }
      seenReviewPubIds.add(id);
      previousReviewOrder = orderKey;
      return { id, decision, rationale, createdAt };
    },
    'tail',
  );
  const seenEventPubIds = new Set<string>();
  let previousEventOrder = '';
  const events = boundedRecords(
    selectedVersion.version.events,
    'events',
    (event) => {
      const id =
        typeof event.pub_id === 'string' &&
        /^evt_[A-Za-z0-9_-]{1,116}$/.test(event.pub_id) &&
        !containsClientSecret(event.pub_id)
          ? event.pub_id
          : '';
      const type =
        typeof event.event_type === 'string' &&
        [
          'revision_created',
          'human_edited',
          'published',
          'delivered',
          'delivery_confirmed',
        ].includes(event.event_type)
          ? event.event_type
          : '';
      const actorPubId =
        typeof event.actor_pub_id === 'string' &&
        /^(?:usr|svc)_[A-Za-z0-9_-]{1,116}$/.test(event.actor_pub_id) &&
        !containsClientSecret(event.actor_pub_id)
          ? event.actor_pub_id
          : '';
      const createdAt = safeTimestamp(event.created_at);
      const orderKey = createdAt && id ? `${createdAt}\u0000${id}` : '';
      if (
        !id ||
        seenEventPubIds.has(id) ||
        (event.report_version_pub_id !== null &&
          event.report_version_pub_id !== selectedVersion.versionPubId) ||
        !type ||
        !actorPubId ||
        !safeStructuredRecord(event.data) ||
        !createdAt ||
        (previousEventOrder !== '' && orderKey <= previousEventOrder)
      ) {
        return null;
      }
      seenEventPubIds.add(id);
      previousEventOrder = orderKey;
      return { id, type, createdAt };
    },
    'tail',
  );
  const seenActionPubIds = new Set<string>();
  let previousActionOrder = '';
  const actions = boundedRecords(
    value.optimization_actions,
    'actions',
    (action) => {
      const pubId =
        typeof action.pub_id === 'string' &&
        /^act_[A-Za-z0-9_-]{1,116}$/.test(action.pub_id) &&
        !containsClientSecret(action.pub_id)
          ? action.pub_id
          : '';
      const description = safeText(action.description, '', 1_000);
      const state =
        typeof action.state === 'string' &&
        ['proposed', 'accepted', 'in_progress', 'done', 'rejected'].includes(action.state)
          ? action.state
          : '';
      const ownerPubId =
        action.owner_pub_id === null
          ? null
          : typeof action.owner_pub_id === 'string' &&
              /^usr_[A-Za-z0-9_-]{1,116}$/.test(action.owner_pub_id) &&
              !containsClientSecret(action.owner_pub_id)
            ? action.owner_pub_id
            : '';
      const createdAt = safeTimestamp(action.created_at);
      const updatedAt = safeTimestamp(action.updated_at);
      const orderKey = createdAt && pubId ? `${createdAt}\u0000${pubId}` : '';
      if (
        !pubId ||
        seenActionPubIds.has(pubId) ||
        !description ||
        !state ||
        ownerPubId === '' ||
        (action.baseline !== null && !safeStructuredRecord(action.baseline)) ||
        (action.outcome !== null && !safeStructuredRecord(action.outcome)) ||
        !createdAt ||
        !updatedAt ||
        new Date(updatedAt).getTime() < new Date(createdAt).getTime() ||
        (previousActionOrder !== '' && orderKey <= previousActionOrder)
      ) {
        return null;
      }
      seenActionPubIds.add(pubId);
      previousActionOrder = orderKey;
      const rawRetests = Array.isArray(action.effect_retests) ? action.effect_retests : [];
      const boundedRetests = rawRetests.slice(-reportProjectionLimits.effectRetests);
      const seenRetestPubIds = new Set<string>();
      let previousRetestOrder = '';
      const retests = boundedRetests
        .flatMap((retest) => {
          if (!isRecord(retest)) {
            invalidProjection.add('effectRetests');
            return [];
          }
          const retestPubId =
            typeof retest.pub_id === 'string' &&
            /^rts_[A-Za-z0-9_-]{1,116}$/.test(retest.pub_id) &&
            !containsClientSecret(retest.pub_id)
              ? retest.pub_id
              : '';
          const recordedByPubId =
            typeof retest.recorded_by_pub_id === 'string' &&
            /^usr_[A-Za-z0-9_-]{1,116}$/.test(retest.recorded_by_pub_id) &&
            !containsClientSecret(retest.recorded_by_pub_id)
              ? retest.recorded_by_pub_id
              : '';
          const measuredAt = safeTimestamp(retest.measured_at);
          const retestCreatedAt = safeTimestamp(retest.created_at);
          const retestOrder = measuredAt && retestPubId ? `${measuredAt}\u0000${retestPubId}` : '';
          if (
            !retestPubId ||
            seenRetestPubIds.has(retestPubId) ||
            retest.action_pub_id !== pubId ||
            !recordedByPubId ||
            !safeStructuredRecord(retest.result) ||
            !measuredAt ||
            !retestCreatedAt ||
            new Date(retestCreatedAt).getTime() < new Date(measuredAt).getTime() ||
            (previousRetestOrder !== '' && retestOrder <= previousRetestOrder)
          ) {
            invalidProjection.add('effectRetests');
            return [];
          }
          seenRetestPubIds.add(retestPubId);
          previousRetestOrder = retestOrder;
          const delta = retest.result.delta;
          const result =
            typeof delta === 'number' && Number.isFinite(delta)
              ? `${delta >= 0 ? '+' : ''}${delta}pp`
              : '已记录结构化结果';
          return [{ pubId: retestPubId, measuredAt, result }];
        })
        .sort((left, right) => left.measuredAt.localeCompare(right.measuredAt));
      if (rawRetests.length > reportProjectionLimits.effectRetests)
        addNotice('effectRetests', rawRetests.length, retests.length);
      return { pubId, description, state, createdAt, retests };
    },
    'tail',
  ).sort((left, right) => left.createdAt.localeCompare(right.createdAt));
  const reportState =
    typeof value.state === 'string' &&
    ['draft', 'review', 'approved', 'published'].includes(value.state)
      ? value.state
      : 'unknown';
  if (reportState === 'unknown') invalidProjection.add('versions');

  if ('projection' in value) {
    const boundary = isRecord(value.projection) ? value.projection : null;
    const mergeBoundary = (
      collection: ReportProjectionCollection,
      projection: unknown,
      shown: number,
    ) => {
      if (
        !isRecord(projection) ||
        typeof projection.total !== 'number' ||
        !Number.isSafeInteger(projection.total) ||
        projection.total < 0 ||
        typeof projection.shown !== 'number' ||
        !Number.isSafeInteger(projection.shown) ||
        projection.shown < 0 ||
        typeof projection.invalid !== 'boolean'
      ) {
        invalidProjection.add(collection);
        return;
      }
      if (projection.invalid || projection.shown !== shown) invalidProjection.add(collection);
      if (projection.total > shown) {
        projectionNotices[collection] = { total: projection.total, shown };
      }
    };
    mergeBoundary('versions', boundary?.versions, projectedVersions.length);
    mergeBoundary('actions', boundary?.optimization_actions, actions.length);
    const versionCollections =
      boundary && isRecord(boundary.version_collections) ? boundary.version_collections : null;
    const versionBoundary = versionCollections?.[selectedVersion.versionPubId];
    if (isRecord(versionBoundary)) {
      mergeBoundary('sections', versionBoundary.components, selectedSections.length);
      mergeBoundary(
        'sectionEvidenceIds',
        versionBoundary.section_evidence_ids,
        linkedSelectedSections.reduce((total, section) => total + section.evidencePubIds.length, 0),
      );
      mergeBoundary('facts', versionBoundary.frozen_facts, facts.length);
      mergeBoundary('artifacts', versionBoundary.artifacts, artifacts.length);
      mergeBoundary('evidenceBindings', versionBoundary.evidence_bindings, evidenceBindings.length);
      mergeBoundary('comments', versionBoundary.comments, comments.length);
      mergeBoundary('reviews', versionBoundary.reviews, reviews.length);
      mergeBoundary('events', versionBoundary.events, events.length);
    } else {
      invalidProjection.add('versions');
    }
    const actionRetests =
      boundary && isRecord(boundary.action_retests) ? boundary.action_retests : null;
    const retestBoundary = actions.reduce(
      (combined, action) => {
        const projection = actionRetests?.[action.pubId];
        if (
          !isRecord(projection) ||
          typeof projection.total !== 'number' ||
          !Number.isSafeInteger(projection.total) ||
          projection.total < 0 ||
          typeof projection.shown !== 'number' ||
          !Number.isSafeInteger(projection.shown) ||
          projection.shown < 0 ||
          typeof projection.invalid !== 'boolean'
        ) {
          invalidProjection.add('effectRetests');
          return combined;
        }
        return {
          total: combined.total + projection.total,
          shown: combined.shown + projection.shown,
          invalid: combined.invalid || projection.invalid,
        };
      },
      { total: 0, shown: 0, invalid: false },
    );
    mergeBoundary(
      'effectRetests',
      retestBoundary,
      actions.reduce((total, action) => total + action.retests.length, 0),
    );
  }

  return {
    reportPubId,
    versionPubId: selectedVersion.versionPubId,
    versionNumber: selectedVersion.versionNumber,
    status: reportState,
    facts,
    sections: linkedSelectedSections,
    artifacts,
    evidenceBindings,
    comments,
    reviews,
    events,
    actions,
    versions: linkedProjectedVersions,
    projectionNotices,
    invalidProjection: [...invalidProjection],
  };
}

const reportProjectionLabels: Record<ReportProjectionCollection, string> = {
  versions: '报告版本',
  facts: '冻结事实',
  sections: '版本章节',
  sectionEvidenceIds: '章节证据标识',
  artifacts: '版本产物',
  evidenceBindings: '证据绑定',
  comments: '审核评论',
  reviews: '审核决定',
  events: '报告事件',
  actions: '优化行动',
  effectRetests: '效果复测',
};

function ReportProjectionNotice({
  target,
  collections,
}: {
  target: LiveReportTarget;
  collections: ReportProjectionCollection[];
}) {
  const items = collections.flatMap((collection) => {
    const notice = target.projectionNotices[collection];
    return notice
      ? [
          {
            key: collection,
            label: reportProjectionLabels[collection],
            total: notice.total,
            shown: notice.shown,
          },
        ]
      : [];
  });
  const invalid = collections.filter((collection) => target.invalidProjection.includes(collection));
  return (
    <>
      <ProjectionLimitNotice items={items} />
      {invalid.length ? (
        <div className="confirmation projection-limit-notice" role="alert">
          <Badge tone="warning">安全投影不完整</Badge>
          <span>
            {invalid.map((collection) => reportProjectionLabels[collection]).join('、')}
            含未通过安全校验的数据；相关写操作已锁定，且不会把当前视图声称为完整记录。
          </span>
        </div>
      ) : null}
    </>
  );
}

const hasIncompleteReportProjection = (
  target: LiveReportTarget,
  collections: ReportProjectionCollection[],
) =>
  collections.some(
    (collection) =>
      Boolean(target.projectionNotices[collection]) ||
      target.invalidProjection.includes(collection),
  );

const toReportState = (status: string): ReportState =>
  status === 'published'
    ? 'published'
    : status === 'approved'
      ? 'approved'
      : status === 'review'
        ? 'review'
        : 'frozen';

function createPreviewPdf() {
  const streams = [
    'BT /F1 22 Tf 64 750 Td (GEO Monitoring Report) Tj /F1 12 Tf 0 -38 Td (Frozen window: 2026-07-01 to 2026-07-21) Tj 0 -26 Td (Brand mention rate: 68.4 percent) Tj 0 -22 Td (Evidence objects: 92) Tj ET',
    'BT /F1 22 Tf 64 750 Td (Optimization and Retest) Tj /F1 12 Tf 0 -38 Td (Priority: private deployment evidence) Tj 0 -26 Td (Retest window: 30 days) Tj 0 -22 Td (Human review required before release) Tj ET',
  ];
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${streams[0]!.length} >>\nstream\n${streams[0]}\nendstream`,
    `<< /Length ${streams[1]!.length} >>\nstream\n${streams[1]}\nendstream`,
  ];
  let pdf = '%PDF-1.4\n';
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(pdf.length);
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets
    .slice(1)
    .map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`)
    .join('');
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return new TextEncoder().encode(pdf);
}

function PdfCanvas({
  page,
  zoom,
  liveArtifact,
}: {
  page: number;
  zoom: 'fit' | '100';
  liveArtifact?: {
    reportPubId: string;
    versionPubId: string;
    byteSize: number;
    mimeType: string;
    sha256: string;
  };
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [retryKey, retry] = useLocalRetry();
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>('loading');
  useEffect(() => {
    let cancelled = false;
    let destroy: (() => Promise<void>) | undefined;
    let renderTask: { cancel: () => void; promise: Promise<unknown> } | undefined;
    clearSafePdfCanvas(canvasRef.current);
    setStatus('loading');
    void (async () => {
      let data = createPreviewPdf();
      if (liveArtifact) {
        const headers = getValidatedIdentityHeaders();
        if (!headers) throw new Error('Validated identity required');
        const result = await getReportArtifact(
          liveArtifact.reportPubId,
          liveArtifact.versionPubId,
          'pdf',
          {
            byteSize: liveArtifact.byteSize,
            mimeType: liveArtifact.mimeType,
            sha256: liveArtifact.sha256,
          },
          headers,
        );
        if (result.kind !== 'ready') throw new Error('Report artifact unavailable');
        data = new Uint8Array(await result.data.blob.arrayBuffer());
      }
      const { getDocument, GlobalWorkerOptions } = await import('pdfjs-dist');
      if (cancelled) return;
      GlobalWorkerOptions.workerSrc = new URL(
        'pdfjs-dist/build/pdf.worker.min.mjs',
        import.meta.url,
      ).toString();
      const task = getDocument({ ...safePdfDocumentOptions, data });
      destroy = () => task.destroy();
      const document = await task.promise;
      if (cancelled) return;
      const pdfPage = await document.getPage(page);
      const viewport = pdfPage.getViewport({ scale: 1.25 });
      const projectedViewport = projectSafePdfPageViewport({
        totalPages: document.numPages,
        pageNumber: page,
        width: viewport.width,
        height: viewport.height,
      });
      if (!projectedViewport) throw new Error('PDF page exceeds browser preview limits');
      const canvas = canvasRef.current;
      if (!canvas || cancelled) return;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('PDF canvas unavailable');
      canvas.width = projectedViewport.canvasWidth;
      canvas.height = projectedViewport.canvasHeight;
      renderTask = pdfPage.render({ canvas, canvasContext: context, viewport });
      await renderTask.promise;
      if (!cancelled) setStatus('ready');
    })().catch(() => {
      if (!cancelled) {
        clearSafePdfCanvas(canvasRef.current);
        setStatus('failed');
      }
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
      void destroy?.();
      clearSafePdfCanvas(canvasRef.current);
    };
  }, [liveArtifact, page, retryKey]);
  return (
    <div className="pdf-canvas-wrap" data-zoom={zoom}>
      {status === 'failed' ? <StatePanel state="failed" onRetry={retry} /> : null}
      <canvas ref={canvasRef} aria-hidden="true" />
      <span className="sr-only" role="status">
        {status === 'loading'
          ? 'PDF 页面加载中'
          : status === 'ready'
            ? `PDF.js 已渲染第 ${page} 页`
            : 'PDF 页面渲染失败'}
      </span>
    </div>
  );
}

function VerifiedArtifactDownload({
  reportPubId,
  versionPubId,
  artifact,
}: {
  reportPubId: string;
  versionPubId: string;
  artifact: LiveReportTarget['artifacts'][number];
}) {
  return (
    <VerifiedBlobDownload
      fileName={`${reportPubId}-${versionPubId}.${artifact.format}`}
      resourceKey={createStructuredClientScopeKey([
        reportPubId,
        versionPubId,
        artifact.format,
        artifact.sha256,
        artifact.mimeType,
      ])}
      load={async () => {
        const headers = getValidatedIdentityHeaders();
        if (!headers) return { kind: 'forbidden' };
        const result = await getReportArtifact(
          reportPubId,
          versionPubId,
          artifact.format,
          {
            byteSize: artifact.byteSize,
            mimeType: artifact.mimeType,
            sha256: artifact.sha256,
          },
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

function WindowWorkspace({
  state,
  onFreeze,
  livePage,
  liveState,
  page,
  pageCount,
  onPageChange,
  onRetry,
  emptyContent,
}: {
  state: ReportState;
  onFreeze: () => void;
  livePage: ReportPageProjection | null;
  liveState: 'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden';
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  onRetry: () => void;
  emptyContent?: React.ReactNode;
}) {
  if (liveState === 'loading') return <StatePanel state="loading" />;
  if (liveState === 'failed') return <StatePanel state="failed" onRetry={onRetry} />;
  if (liveState === 'forbidden') return <StatePanel state="forbidden" />;
  const liveReports = livePage?.data ?? [];
  if (liveState === 'ready' && liveReports.length === 0) {
    return emptyContent ?? <StatePanel state="empty" />;
  }
  if (liveState === 'ready') {
    const report = liveReports[0]!;
    return (
      <>
        <section className="panel">
          <span className="overline">Report catalog</span>
          <h2>{report.title}</h2>
          <p className="panel-subtitle">
            {report.pub_id} · {report.state} · 更新于{' '}
            {report.updated_at.slice(0, 16).replace('T', ' ')}
          </p>
          <Badge tone={report.state === 'published' ? 'positive' : 'warning'}>{report.state}</Badge>
          <StatePanel state="insufficient" />
          <p className="panel-subtitle">
            列表合同未提供冻结窗口、事实行、指标或证据绑定；这些字段不会由浏览器推断。
          </p>
        </section>
        <Pagination
          label="报告目录分页"
          page={page}
          pageCount={pageCount}
          onPageChange={onPageChange}
        />
      </>
    );
  }
  return (
    <>
      <MetricGrid
        metrics={[
          {
            label: '冻结样本',
            value: state === 'draft' ? '—' : '1,284',
            detail: '38 个 eligible 回答 · contract fixture',
          },
          { label: '数据窗口', value: '21 天', detail: '07/01–07/21' },
          { label: '口径版本', value: 'v2.4', detail: 'scorer geo-v4' },
          {
            label: '漂移检查',
            value: state === 'draft' ? '待冻结' : '通过',
            detail: 'input hash 已锁定',
          },
        ]}
      />
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Immutable snapshot</span>
            <h2>数据窗口与事实冻结</h2>
          </div>
          <Badge tone={state === 'draft' ? 'warning' : 'positive'}>
            {state === 'draft' ? '尚未冻结' : '事实已冻结'}
          </Badge>
        </div>
        <div className="freeze-grid">
          <label>
            开始日期
            <input type="date" value="2026-07-01" readOnly />
          </label>
          <label>
            结束日期
            <input type="date" value="2026-07-21" readOnly />
          </label>
          <label>
            Metric version
            <input value="client-metrics-v2.4" readOnly />
          </label>
          <label>
            Scorer version
            <input value="geo-scoring-v4" readOnly />
          </label>
        </div>
        <div className="freeze-summary">
          <div>
            <strong>1,284</strong>
            <span>分析事实</span>
          </div>
          <div>
            <strong>38</strong>
            <span>有效回答</span>
          </div>
          <div>
            <strong>92</strong>
            <span>证据对象</span>
          </div>
          <div>
            <strong>sha256: 7a3f…c91e</strong>
            <span>输入哈希</span>
          </div>
        </div>
        <div className="form-actions">
          <span>冻结后窗口和版本不可原地修改；需创建新报告版本。</span>
          <button className="button" disabled={state !== 'draft'} onClick={onFreeze}>
            {state === 'draft' ? '冻结事实并创建 v0.8' : '已冻结'}
          </button>
        </div>
      </section>
    </>
  );
}

export function CreateReportWorkspace({
  projectPubId,
  canAuthor,
  onCreated,
}: {
  projectPubId: string;
  canAuthor: boolean;
  onCreated: () => void;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const weekAgo = new Date(Date.now() - 6 * 86_400_000).toISOString().slice(0, 10);
  const [title, setTitle] = useState('GEO 自动化监测报告');
  const [windowStart, setWindowStart] = useState(weekAgo);
  const [windowEnd, setWindowEnd] = useState(today);
  const [factLabel, setFactLabel] = useState('监测结论');
  const [factValue, setFactValue] = useState('待审核');
  const [summary, setSummary] = useState(
    '本报告由已冻结的 GEO 监测窗口创建，请审核事实、证据与建议后发布。',
  );
  const [writeState, setWriteState] = useState<'idle' | 'saving' | 'failed'>('idle');
  const [receipt, setReceipt] = useState('');
  const [suggestions, setSuggestions] = useState<{
    payloads: Record<string, unknown>[];
    invalidCount: number;
  }>({ payloads: [], invalidCount: 0 });
  const handleSuggestionsChange = useCallback(
    (payloads: Record<string, unknown>[], invalidCount: number) => {
      setSuggestions((current) =>
        current.payloads === payloads && current.invalidCount === invalidCount
          ? current
          : { payloads, invalidCount },
      );
    },
    [],
  );
  const operationRef = useRef(`report-studio/${projectPubId}/${crypto.randomUUID()}`);
  const invalid =
    !title.trim() ||
    !factLabel.trim() ||
    !factValue.trim() ||
    !summary.trim() ||
    windowStart > windowEnd ||
    suggestions.invalidCount > 0 ||
    [title, factLabel, factValue, summary].some(containsClientSecret);

  async function submit() {
    const headers = getValidatedIdentityHeaders();
    if (!headers || invalid || !canAuthor || writeState === 'saving') return;
    setWriteState('saving');
    const result = await createReport(
      {
        project_pub_id: projectPubId,
        title: title.trim(),
        window_start: `${windowStart}T00:00:00.000Z`,
        window_end: `${windowEnd}T23:59:59.999Z`,
        filters: { source: 'report_studio', project_pub_id: projectPubId },
        metric_version: 'geo-automation-v1',
        scorer_version: 'geo-scoring-v1',
        fact_rows: [
          {
            metric: factLabel.trim(),
            value: factValue.trim(),
            source: 'manual_confirmed',
            measured_at: `${windowEnd}T23:59:59.999Z`,
          },
          ...suggestions.payloads,
        ],
        components: [
          {
            component_type: 'section',
            source: 'human',
            title: '执行摘要',
            body: summary.trim(),
          },
        ],
        workflow_operation_id: operationRef.current,
      },
      headers,
    );
    if (result.kind !== 'ready') {
      setWriteState('failed');
      return;
    }
    setReceipt(
      `报告已创建：${result.data.reportPubId} · 事实快照 ${result.data.factSnapshotHash.slice(0, 12)}`,
    );
    setWriteState('idle');
    onCreated();
  }

  return (
    <section className="panel" aria-labelledby="create-first-report-title">
      <span className="overline">First report</span>
      <h2 id="create-first-report-title">创建首份报告</h2>
      <p className="panel-subtitle">
        创建时会冻结时间窗口、事实行和首个章节，并生成 HTML、DOCX、PDF、XLSX 四种可校验产物。
      </p>
      {!canAuthor ? <StatePanel state="forbidden" /> : null}
      <div className="freeze-grid">
        <label>
          报告标题
          <input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label>
          开始日期
          <input
            type="date"
            value={windowStart}
            onChange={(event) => setWindowStart(event.target.value)}
          />
        </label>
        <label>
          结束日期
          <input
            type="date"
            value={windowEnd}
            onChange={(event) => setWindowEnd(event.target.value)}
          />
        </label>
        <label>
          首条事实名称
          <input
            value={factLabel}
            maxLength={200}
            onChange={(event) => setFactLabel(event.target.value)}
          />
        </label>
        <label>
          首条事实值
          <input
            value={factValue}
            maxLength={500}
            onChange={(event) => setFactValue(event.target.value)}
          />
        </label>
      </div>
      <FactSuggestionsPanel
        projectPubId={projectPubId}
        windowStart={windowStart}
        windowEnd={windowEnd}
        disabled={!canAuthor || writeState === 'saving'}
        onAcceptedChange={handleSuggestionsChange}
      />
      <label className="form-field">
        <span>执行摘要</span>
        <textarea
          rows={7}
          value={summary}
          maxLength={10_000}
          onChange={(event) => setSummary(event.target.value)}
        />
      </label>
      {invalid ? (
        <span className="field-hint">
          请填写完整字段、保证起止日期有效，并移除凭据或其他敏感内容。
        </span>
      ) : null}
      <div className="form-actions">
        <span>项目：{projectPubId}</span>
        <button
          className="button"
          type="button"
          disabled={!canAuthor || invalid || writeState === 'saving'}
          onClick={() => void submit()}
        >
          {writeState === 'saving' ? '正在冻结并生成…' : '创建首份报告'}
        </button>
      </div>
      {receipt ? <Toast>{receipt}</Toast> : null}
      {writeState === 'failed' ? <StatePanel state="failed" onRetry={() => void submit()} /> : null}
    </section>
  );
}

function TraceWorkspace() {
  const [expanded, setExpanded] = useState<string | null>('mention');
  const [openedEvidence, setOpenedEvidence] = useState<string | null>(null);
  const metrics = [
    {
      id: 'mention',
      label: '品牌提及率',
      value: '68.4%',
      numerator: 26,
      denominator: 38,
      contributions: ['ans_01 · 豆包 · +1', 'ans_04 · 豆包 · +1', 'ans_08 · DeepSeek · +1'],
    },
    {
      id: 'top3',
      label: 'Top 3 占比',
      value: '73.7%',
      numerator: 28,
      denominator: 38,
      contributions: ['ans_01 · rank 2', 'ans_03 · rank 1', 'ans_11 · rank 3'],
    },
    {
      id: 'citation',
      label: '引用覆盖',
      value: '55.3%',
      numerator: 21,
      denominator: 38,
      contributions: ['evd_019 · 官网白皮书', 'evd_027 · 工信部指南'],
    },
  ];
  return (
    <section className="panel">
      <span className="overline">Reproducible metrics</span>
      <h2>KPI Trace</h2>
      <p className="panel-subtitle">
        任意数字可下钻到贡献回答、证据和版本；分子、分母及真实 0 分开表达。
      </p>
      <div className="trace-list">
        {metrics.map((metric) => (
          <article key={metric.id}>
            <button
              aria-expanded={expanded === metric.id}
              onClick={() => setExpanded(expanded === metric.id ? null : metric.id)}
            >
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
              <small>
                {metric.numerator} / {metric.denominator}
              </small>
            </button>
            {expanded === metric.id ? (
              <div className="trace-detail">
                <Badge tone="info">metric v2.4</Badge>
                <ul>
                  {metric.contributions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                <button
                  className="button button-secondary"
                  aria-expanded={openedEvidence === metric.id}
                  onClick={() => setOpenedEvidence(openedEvidence === metric.id ? null : metric.id)}
                >
                  {openedEvidence === metric.id ? '关闭贡献证据' : '打开贡献证据'}
                </button>
                {openedEvidence === metric.id ? (
                  <section className="confirmation" aria-label={`${metric.label}贡献证据`}>
                    <Badge tone="positive">证据版本已冻结</Badge>
                    <strong>{metric.contributions[0]}</strong>
                    <span>回答截图、文本锚点与窗口 hash 已绑定；当前只展示安全证据摘要。</span>
                  </section>
                ) : null}
              </div>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}

function EditorWorkspace({
  sections,
  onChange,
  savedVersions,
  onSaveVersion,
}: {
  sections: Section[];
  onChange: (sections: Section[]) => void;
  savedVersions: SectionVersion[];
  onSaveVersion: (version: SectionVersion) => void;
}) {
  const [selectedId, setSelectedId] = useState(sections[0]!.id);
  const selected = sections.find((section) => section.id === selectedId)!;
  const selectedVersions = savedVersions.filter((version) => version.sectionId === selectedId);
  const {
    register: registerSection,
    handleSubmit: handleSectionSubmit,
    reset: resetSection,
    watch: watchSection,
    formState: { errors: sectionErrors },
  } = useForm<ReportSectionFields>({
    resolver: zodResolver(reportSectionSchema),
    defaultValues: { body: selected.body },
    mode: 'onChange',
  });
  const sectionBody = watchSection('body');
  const selectSection = (nextSelectedId: string) => {
    const nextSection = sections.find((section) => section.id === nextSelectedId);
    if (!nextSection) return;
    resetSection({ body: nextSection.body });
    setSelectedId(nextSelectedId);
  };
  const updateBody = (body: string) =>
    onChange(
      sections.map((section) =>
        section.id === selectedId
          ? { ...section, body, provenance: 'human', modified: '当前分析师 · 刚刚' }
          : section,
      ),
    );
  const acceptDraft = handleSectionSubmit(({ body }) => updateBody(body));
  const saveVersion = handleSectionSubmit(({ body }) => {
    onSaveVersion({
      sectionId: selected.id,
      version: Math.max(0, ...selectedVersions.map((version) => version.version)) + 1,
      title: selected.title,
      body,
      savedBy: '当前分析师',
      savedAt: '刚刚',
    });
  });
  return (
    <div className="editor-layout">
      <aside className="panel section-nav">
        <h2>报告章节</h2>
        {sections.map((section) => (
          <button
            className={section.id === selectedId ? 'selected' : ''}
            key={section.id}
            onClick={() => selectSection(section.id)}
          >
            <span>{section.title}</span>
            <Badge tone={section.provenance === 'ai' ? 'info' : 'positive'}>
              {section.provenance === 'ai' ? 'AI 草稿' : '人工修改'}
            </Badge>
          </button>
        ))}
      </aside>
      <section className="panel document-editor">
        <div className="account-head">
          <div>
            <span className="overline">Section editor</span>
            <h2>{selected.title}</h2>
          </div>
          <Badge tone={selected.provenance === 'ai' ? 'info' : 'positive'}>
            {selected.provenance === 'ai' ? 'AI 生成 · 未确认' : '人工内容'}
          </Badge>
        </div>
        <form onSubmit={(event) => void saveVersion(event)} noValidate>
          <textarea
            aria-label="章节正文"
            {...registerSection('body', {
              onChange: (event) => updateBody(event.target.value),
            })}
            aria-invalid={Boolean(sectionErrors.body)}
            aria-describedby={sectionErrors.body ? 'report-section-body-error' : undefined}
          />
          {sectionErrors.body ? (
            <span id="report-section-body-error" className="field-error" role="alert">
              {sectionErrors.body.message}
            </span>
          ) : null}
          <div className="editor-meta">
            <span>{selected.modified}</span>
            <span>{sectionBody.length} 字</span>
            <span>已绑定 3 条证据</span>
          </div>
          <div className="button-row">
            {selected.provenance === 'ai' ? (
              <button
                type="button"
                className="button button-secondary"
                disabled={Boolean(sectionErrors.body)}
                onClick={() => void acceptDraft()}
              >
                接受草稿并标记人工确认
              </button>
            ) : null}
            <button type="submit" className="button" disabled={Boolean(sectionErrors.body)}>
              保存章节版本
            </button>
          </div>
        </form>
        {selectedVersions[0] ? (
          <>
            <Toast>
              {selectedVersions[0].title} v{selectedVersions[0].version} 已保存，正文快照不可变
            </Toast>
            <ol className="version-list" aria-label={`${selected.title}章节版本历史`}>
              {selectedVersions.map((version) => (
                <li key={`${version.sectionId}-${version.version}`}>
                  v{version.version} · {version.body.length} 字 · 人工版本
                </li>
              ))}
            </ol>
          </>
        ) : null}
      </section>
    </div>
  );
}

type DiffChunk = { kind: 'equal' | 'removed' | 'added'; text: string };
function createVersionDiff(before: string, after: string) {
  const rows = Array.from({ length: before.length + 1 }, () => new Uint16Array(after.length + 1));
  for (let left = before.length - 1; left >= 0; left -= 1) {
    for (let right = after.length - 1; right >= 0; right -= 1) {
      rows[left]![right] =
        before[left] === after[right]
          ? rows[left + 1]![right + 1]! + 1
          : Math.max(rows[left + 1]![right]!, rows[left]![right + 1]!);
    }
  }
  const chunks: DiffChunk[] = [];
  const append = (kind: DiffChunk['kind'], text: string) => {
    const last = chunks.at(-1);
    if (last?.kind === kind) last.text += text;
    else chunks.push({ kind, text });
  };
  let left = 0;
  let right = 0;
  while (left < before.length || right < after.length) {
    if (left < before.length && right < after.length && before[left] === after[right]) {
      append('equal', before[left]!);
      left += 1;
      right += 1;
    } else if (
      left < before.length &&
      (right === after.length || rows[left + 1]![right]! >= rows[left]![right + 1]!)
    ) {
      append('removed', before[left]!);
      left += 1;
    } else {
      append('added', after[right]!);
      right += 1;
    }
  }
  return {
    chunks,
    removed: chunks
      .filter((chunk) => chunk.kind === 'removed')
      .reduce((total, chunk) => total + chunk.text.length, 0),
    added: chunks
      .filter((chunk) => chunk.kind === 'added')
      .reduce((total, chunk) => total + chunk.text.length, 0),
  };
}

function VersionDiffWorkspace({ versions }: { versions: SectionVersion[] }) {
  const sectionIds = [...new Set(versions.map((version) => version.sectionId))];
  const [sectionId, setSectionId] = useState(sectionIds[0] ?? '');
  const sectionVersions = versions
    .filter((version) => version.sectionId === sectionId)
    .sort((left, right) => left.version - right.version);
  const [beforeNumber, setBeforeNumber] = useState(sectionVersions[0]?.version ?? 0);
  const [afterNumber, setAfterNumber] = useState(sectionVersions.at(-1)?.version ?? 0);
  const before = sectionVersions.find((version) => version.version === beforeNumber);
  const after = sectionVersions.find((version) => version.version === afterNumber);
  const diff = before && after ? createVersionDiff(before.body, after.body) : null;
  const selectSection = (nextSectionId: string) => {
    const nextVersions = versions
      .filter((version) => version.sectionId === nextSectionId)
      .sort((left, right) => left.version - right.version);
    setSectionId(nextSectionId);
    setBeforeNumber(nextVersions[0]?.version ?? 0);
    setAfterNumber(nextVersions.at(-1)?.version ?? 0);
  };
  return (
    <section className="panel">
      <span className="overline">Immutable comparison</span>
      <h2>章节版本对比</h2>
      <p className="panel-subtitle">
        对比两个不可变正文快照；差异不会改写原版本，也不包含评论、账号或会话材料。
      </p>
      <div className="filter-bar" aria-label="版本对比筛选">
        <label>
          章节
          <select
            aria-label="对比章节"
            value={sectionId}
            onChange={(event) => selectSection(event.target.value)}
          >
            {sectionIds.map((id) => (
              <option key={id} value={id}>
                {versions.find((version) => version.sectionId === id)?.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          基准版本
          <select
            value={beforeNumber}
            onChange={(event) => setBeforeNumber(Number(event.target.value))}
          >
            {sectionVersions.map((version) => (
              <option key={version.version} value={version.version}>
                v{version.version}
              </option>
            ))}
          </select>
        </label>
        <label>
          对比版本
          <select
            value={afterNumber}
            onChange={(event) => setAfterNumber(Number(event.target.value))}
          >
            {sectionVersions.map((version) => (
              <option key={version.version} value={version.version}>
                v{version.version}
              </option>
            ))}
          </select>
        </label>
      </div>
      {before && after && diff ? (
        <>
          <div className="version-compare-meta">
            <article>
              <Badge tone="neutral">基准 v{before.version}</Badge>
              <strong>{before.savedBy}</strong>
              <span>{before.savedAt}</span>
            </article>
            <article>
              <Badge tone="info">对比 v{after.version}</Badge>
              <strong>{after.savedBy}</strong>
              <span>{after.savedAt}</span>
            </article>
          </div>
          <article
            className="version-diff"
            aria-label={`${before.title} v${before.version} 与 v${after.version} 正文差异`}
          >
            <p>
              {diff.chunks.map((chunk, index) =>
                chunk.kind === 'removed' ? (
                  <del key={`${chunk.kind}-${index}`}>{chunk.text}</del>
                ) : chunk.kind === 'added' ? (
                  <ins key={`${chunk.kind}-${index}`}>{chunk.text}</ins>
                ) : (
                  <span key={`${chunk.kind}-${index}`}>{chunk.text}</span>
                ),
              )}
            </p>
          </article>
          <p className="confirmation" role="status">
            {before.version === after.version
              ? '所选版本相同，正文无差异。'
              : `已对比 v${before.version} → v${after.version}；删除 ${diff.removed} 字，新增 ${diff.added} 字。`}
          </p>
        </>
      ) : (
        <StatePanel state="empty" />
      )}
    </section>
  );
}

type AnchorRect = { x: number; y: number; width: number; height: number };
function EvidenceCanvas({ anchor }: { anchor: AnchorRect }) {
  return (
    <div
      className="konva-wrap"
      role="img"
      tabIndex={0}
      aria-label={`回答截图证据，品牌提及锚点位于坐标 ${anchor.x},${anchor.y}，尺寸 ${anchor.width}×${anchor.height}`}
    >
      <Stage width={620} height={330}>
        <Layer>
          <Rect x={0} y={0} width={620} height={330} fill="#f4f7f5" />
          <Text
            x={34}
            y={34}
            width={540}
            text="企业知识库选型建议\n\n需要评估数据权限、检索质量和部署边界。\n云岫 AI 支持私有化部署与审计能力。"
            fontSize={18}
            lineHeight={1.7}
            fill="#24322d"
          />
          <Rect
            x={anchor.x}
            y={anchor.y}
            width={anchor.width}
            height={anchor.height}
            stroke="#d4573f"
            strokeWidth={3}
            fill="#fff4"
          />
          <Text
            x={anchor.x + 5}
            y={anchor.y + anchor.height + 6}
            text={`Anchor #A17 · bbox ${anchor.x},${anchor.y},${anchor.width},${anchor.height}`}
            fontSize={12}
            fill="#9b3929"
          />
        </Layer>
      </Stage>
    </div>
  );
}

function EvidenceWorkspace() {
  const [attached, setAttached] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [anchor, setAnchor] = useState<AnchorRect>({ x: 245, y: 118, width: 238, height: 52 });
  const moveAnchor = (dx: number, dy: number) =>
    setAnchor((current) => ({
      ...current,
      x: Math.max(0, Math.min(620 - current.width, current.x + dx)),
      y: Math.max(0, Math.min(330 - current.height, current.y + dy)),
    }));
  return (
    <div className="evidence-layout">
      <section className="panel">
        <span className="overline">Konva annotation</span>
        <h2>图表与证据编辑</h2>
        <p className="panel-subtitle">
          截图坐标、正文范围和内容哈希同时保存；画布不替代可访问文本说明。
        </p>
        <EvidenceCanvas anchor={anchor} />
        <div className="button-row">
          <button
            className="button button-secondary"
            aria-expanded={adjusting}
            onClick={() => setAdjusting((value) => !value)}
          >
            {adjusting ? '完成锚点调整' : '调整锚点'}
          </button>
          <button className="button" onClick={() => setAttached(true)}>
            绑定到“执行摘要”
          </button>
        </div>
        {adjusting ? (
          <div className="button-row" role="group" aria-label="锚点位置微调">
            <button onClick={() => moveAnchor(0, -8)}>上移</button>
            <button onClick={() => moveAnchor(-8, 0)}>左移</button>
            <button onClick={() => moveAnchor(8, 0)}>右移</button>
            <button onClick={() => moveAnchor(0, 8)}>下移</button>
            <span role="status">
              bbox {anchor.x},{anchor.y},{anchor.width},{anchor.height}
            </span>
          </div>
        ) : null}
      </section>
      <aside className="panel">
        <h2>证据属性</h2>
        <dl className="definition-grid evidence-dl">
          <div>
            <dt>资产</dt>
            <dd>evd_01K0…A17</dd>
          </div>
          <div>
            <dt>类型</dt>
            <dd>回答截图</dd>
          </div>
          <div>
            <dt>文本范围</dt>
            <dd>48–73</dd>
          </div>
          <div>
            <dt>采集时间</dt>
            <dd>2026-07-21 09:42</dd>
          </div>
          <div>
            <dt>完整性</dt>
            <dd>SHA-256 已校验</dd>
          </div>
          <div>
            <dt>章节绑定</dt>
            <dd>{attached ? '执行摘要' : '尚未绑定'}</dd>
          </div>
        </dl>
        {attached ? (
          <div className="confirmation" role="status">
            <Badge tone="positive">绑定成功</Badge>
            <span>章节引用已保存到当前草稿版本。</span>
          </div>
        ) : (
          <StatePanel state="empty" />
        )}
      </aside>
    </div>
  );
}

function PreviewWorkspace({ sections }: { sections: Section[] }) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState<'fit' | '100'>('fit');
  const pages = useMemo(() => [sections.slice(0, 2), sections.slice(2)], [sections]);
  return (
    <section className="preview-layout">
      <div className="preview-toolbar">
        <Badge tone="positive">PDF.js canvas</Badge>
        <button disabled={page === 1} onClick={() => setPage(page - 1)}>
          上一页
        </button>
        <span>
          {page} / {pages.length}
        </span>
        <button disabled={page === pages.length} onClick={() => setPage(page + 1)}>
          下一页
        </button>
        <button aria-pressed={zoom === 'fit'} onClick={() => setZoom('fit')}>
          适合页面
        </button>
        <button aria-pressed={zoom === '100'} onClick={() => setZoom('100')}>
          100%
        </button>
      </div>
      <PdfCanvas page={page} zoom={zoom} />
      <article className="pdf-page pdf-accessible-copy" aria-label={`报告预览第 ${page} 页`}>
        <header>
          <span>GEO Platform</span>
          <small>2026 Q3 监测报告 · v0.8</small>
        </header>
        <h1>{page === 1 ? 'GEO 监测与优化建议' : '优化行动与复测'}</h1>
        {pages[page - 1]!.map((section) => (
          <section key={section.id}>
            <h2>{section.title}</h2>
            <p>{section.body}</p>
            <div className="mini-chart">
              <span style={{ width: section.id === 'summary' ? '68%' : '55%' }} />
            </div>
          </section>
        ))}
        <footer>冻结窗口 2026-07-01—2026-07-21 · 第 {page} 页</footer>
      </article>
    </section>
  );
}

const createLiveRevisionDefaults = (target: LiveReportTarget): ReportRevisionFields => ({
  sections: target.sections.map((section) => ({
    title: section.title,
    body: section.body,
    source:
      section.source === 'ai' || section.source === 'human' ? section.source : ('system' as const),
    evidenceText: section.evidencePubIds.join(', '),
  })),
});

function LiveEditorWorkspace({
  target,
  canAuthor,
  onVerify,
  onAdopt,
}: {
  target: LiveReportTarget;
  canAuthor: boolean;
  onVerify: (
    reportPubId: string,
    reportVersionPubId: string,
    versionNumber: number,
  ) => Promise<LiveReportTarget | null>;
  onAdopt: (target: LiveReportTarget) => void;
}) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [writeState, setWriteState] = useState<'idle' | 'saving' | 'failed'>('idle');
  const [draftState, setDraftState] = useState<'idle' | 'running' | 'failed'>('idle');
  const [receipt, setReceipt] = useState('');
  const [pendingReconciliation, setPendingReconciliation] =
    useState<ReportRevisionReconciliation | null>(null);
  const retainedReceiptVersionRef = useRef('');
  const renderedVersionRef = useRef(target.versionPubId);
  const revisionWrite = useReportMutationGuard(
    createStructuredClientScopeKey(['revision', target.reportPubId, target.versionPubId]),
  );
  const {
    register: registerRevision,
    handleSubmit: handleRevisionSubmit,
    reset: resetRevision,
    setValue: setRevisionValue,
    watch: watchRevision,
    formState: {
      errors: revisionErrors,
      isSubmitting: isRevisionSubmitting,
      isValid: isRevisionValid,
    },
  } = useForm<ReportRevisionFields>({
    resolver: zodResolver(reportRevisionSchema),
    defaultValues: createLiveRevisionDefaults(target),
    mode: 'onChange',
  });
  useEffect(() => {
    if (renderedVersionRef.current !== target.versionPubId) {
      if (retainedReceiptVersionRef.current !== target.versionPubId) setReceipt('');
      retainedReceiptVersionRef.current = '';
      renderedVersionRef.current = target.versionPubId;
      setPendingReconciliation(null);
      setWriteState('idle');
    }
    resetRevision(createLiveRevisionDefaults(target));
    setSelectedIndex(0);
  }, [resetRevision, target.versionPubId, target.sections]);
  const sections = watchRevision('sections');
  const selected = sections[selectedIndex];
  if (!selected) return <StatePanel state="empty" />;
  const selectedErrors = revisionErrors.sections?.[selectedIndex];
  const bodyError = selectedErrors?.body?.message;
  const evidenceError = selectedErrors?.evidenceText?.message;
  const editable = canAuthor && target.status !== 'published';
  const reconciliationLocked = writeState === 'saving' || pendingReconciliation !== null;
  const reconcileRevision = async (
    expected: ReportRevisionReconciliation,
    ticket: ReportMutationTicket,
  ) => {
    const projected = await onVerify(
      target.reportPubId,
      expected.reportVersionPubId,
      expected.versionNumber,
    );
    if (!revisionWrite.isCurrent(ticket)) {
      revisionWrite.finish(ticket);
      return false;
    }
    if (
      !projected ||
      projected.versionPubId !== expected.reportVersionPubId ||
      projected.versionNumber !== expected.versionNumber
    ) {
      revisionWrite.finish(ticket);
      setPendingReconciliation(expected);
      setWriteState('failed');
      return false;
    }
    if (!revisionWrite.finish(ticket)) return false;
    setPendingReconciliation(null);
    setReceipt(expected.receipt);
    setWriteState('idle');
    retainedReceiptVersionRef.current = projected.versionPubId;
    onAdopt(projected);
    return true;
  };
  const retryRevisionReconciliation = async () => {
    if (!pendingReconciliation) return;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setWriteState('failed');
      return;
    }
    const ticket = revisionWrite.begin(headers);
    if (!ticket) return;
    setWriteState('saving');
    await reconcileRevision(pendingReconciliation, ticket);
  };
  const saveRevision = handleRevisionSubmit(async ({ sections: submittedSections }) => {
    if (!editable || pendingReconciliation) return;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setWriteState('failed');
      return;
    }
    const ticket = revisionWrite.begin(headers);
    if (!ticket) return;
    setWriteState('saving');
    setReceipt('');
    const result = await createReportRevision(
      target.reportPubId,
      {
        components: submittedSections.map((section) => {
          const evidencePubIds = [...new Set(splitReportEvidencePubIds(section.evidenceText))];
          return {
            component_type: 'section',
            source: section.source,
            title: section.title,
            body: section.body,
            ...(evidencePubIds.length ? { evidence_pub_ids: evidencePubIds } : {}),
          };
        }),
      },
      `report-revision-${globalThis.crypto.randomUUID()}`,
      headers,
    );
    if (result.kind !== 'ready') {
      if (revisionWrite.finish(ticket)) setWriteState('failed');
      return;
    }
    if (!revisionWrite.isCurrent(ticket)) {
      revisionWrite.finish(ticket);
      return;
    }
    const expected: ReportRevisionReconciliation = {
      reportVersionPubId: result.data.reportVersionPubId,
      versionNumber: result.data.versionNumber,
      receipt: `真实报告版本 ${result.data.versionNumber} 已冻结`,
    };
    setPendingReconciliation(expected);
    await reconcileRevision(expected, ticket);
  });
  return (
    <div className="editor-layout">
      <aside className="panel section-nav">
        <h2>版本 {target.versionNumber} 章节</h2>
        {sections.map((section, index) => (
          <button
            type="button"
            className={index === selectedIndex ? 'selected' : ''}
            key={`${section.title}-${index}`}
            onClick={() => setSelectedIndex(index)}
          >
            <span>{section.title}</span>
            <Badge tone={section.source === 'human' ? 'positive' : 'info'}>
              {section.source === 'human' ? '人工内容' : '待人工确认'}
            </Badge>
          </button>
        ))}
      </aside>
      <form
        className="panel document-editor"
        onSubmit={(event) => void saveRevision(event)}
        noValidate
      >
        <span className="overline">Immutable revision</span>
        <h2>{selected.title}</h2>
        <label>
          章节正文
          <textarea
            aria-label="真实章节正文"
            {...registerRevision(`sections.${selectedIndex}.body`, {
              onChange: () =>
                setRevisionValue(`sections.${selectedIndex}.source`, 'human', {
                  shouldDirty: true,
                  shouldValidate: true,
                }),
            })}
            readOnly={!editable}
            aria-invalid={Boolean(bodyError)}
            aria-describedby={bodyError ? 'report-revision-body-error' : undefined}
          />
        </label>
        {bodyError ? (
          <span id="report-revision-body-error" className="field-error" role="alert">
            {bodyError}
          </span>
        ) : null}
        <label>
          组件证据 ID
          <input
            aria-label="组件证据 ID"
            {...registerRevision(`sections.${selectedIndex}.evidenceText`, {
              onChange: () =>
                setRevisionValue(`sections.${selectedIndex}.source`, 'human', {
                  shouldDirty: true,
                  shouldValidate: true,
                }),
            })}
            readOnly={!editable}
            placeholder="evd_…，多个用逗号分隔"
            aria-invalid={Boolean(evidenceError)}
            aria-describedby={evidenceError ? 'report-revision-evidence-error' : undefined}
          />
        </label>
        {evidenceError ? (
          <span id="report-revision-evidence-error" className="field-error" role="alert">
            {evidenceError}
          </span>
        ) : null}
        <p className="panel-subtitle">
          保存会复制服务端冻结事实并创建不可变新版本；证据 ID 绑定到当前组件，原版本不会被改写。
        </p>
        {editable ? (
          <div className="button-row">
            <button
              type="button"
              className="button button-secondary"
              disabled={draftState === 'running' || reconciliationLocked}
              onClick={() => {
                const headers = getValidatedIdentityHeaders();
                if (!headers) {
                  setDraftState('failed');
                  return;
                }
                setDraftState('running');
                const model = readAiOperationModel('report-draft');
                void draftReportSection(
                  target.reportPubId,
                  { title: selected.title, ...(model ? { model } : {}) },
                  headers,
                ).then((result) => {
                  if (result.kind !== 'ready') {
                    setDraftState('failed');
                    return;
                  }
                  // 草稿落编辑器并标 source='ai'：既有「AI 草稿需人工确认」发布门接管
                  setRevisionValue(`sections.${selectedIndex}.body`, result.data.body, {
                    shouldDirty: true,
                    shouldValidate: true,
                  });
                  setRevisionValue(`sections.${selectedIndex}.source`, 'ai', {
                    shouldDirty: true,
                    shouldValidate: true,
                  });
                  setDraftState('idle');
                });
              }}
            >
              {draftState === 'running' ? 'AI 起草中…' : 'AI 起草本章节'}
            </button>
            {draftState === 'failed' ? (
              <Toast tone="negative">AI 起草失败（未配置模型或上游不可用），请重试或手工撰写。</Toast>
            ) : null}
          </div>
        ) : null}
        <button
          type="submit"
          className="button"
          disabled={!editable || !isRevisionValid || isRevisionSubmitting || reconciliationLocked}
          aria-busy={writeState === 'saving'}
        >
          {writeState === 'saving' ? '正在保存不可变版本…' : '保存不可变报告版本'}
        </button>
        {!canAuthor ? <p className="panel-subtitle">报告修订仅由分析师维护。</p> : null}
        {target.status === 'published' ? (
          <p className="panel-subtitle">已发布报告不可原地修订；请创建新的报告周期。</p>
        ) : null}
        {writeState === 'saving' && pendingReconciliation ? (
          <div className="confirmation" role="status">
            <Badge tone="warning">正在确认</Badge>
            <span>写入已接受，正在重新读取同一报告的权威版本投影。</span>
          </div>
        ) : null}
        {receipt ? <Toast>{receipt}</Toast> : null}
        {writeState === 'failed' ? (
          <StatePanel
            state="failed"
            {...(pendingReconciliation
              ? { onRetry: () => void retryRevisionReconciliation() }
              : {})}
          />
        ) : null}
      </form>
    </div>
  );
}

function LiveDetailWorkspace({
  active,
  target,
  canAuthor,
  onVerifyRevision,
  onAdoptRevision,
}: {
  active: 'trace' | 'editor' | 'diff' | 'evidence' | 'preview';
  target: LiveReportTarget;
  canAuthor: boolean;
  onVerifyRevision: (
    reportPubId: string,
    reportVersionPubId: string,
    versionNumber: number,
  ) => Promise<LiveReportTarget | null>;
  onAdoptRevision: (target: LiveReportTarget) => void;
}) {
  if (active === 'preview') {
    const artifactProjectionIncomplete = hasIncompleteReportProjection(target, [
      'versions',
      'artifacts',
    ]);
    const pdf = artifactProjectionIncomplete
      ? undefined
      : target.artifacts.find((artifact) => artifact.format === 'pdf');
    return (
      <section className="panel">
        {pdf ? (
          <>
            <div className="account-head">
              <div>
                <span className="overline">Verified artifact</span>
                <h2>已冻结 PDF 预览</h2>
              </div>
              <Badge tone="positive">真实 reports API</Badge>
            </div>
            <ReportProjectionNotice target={target} collections={['artifacts']} />
            <PdfCanvas
              key={createStructuredClientScopeKey([
                target.reportPubId,
                target.versionPubId,
                'pdf',
                pdf.sha256,
                pdf.mimeType,
              ])}
              page={1}
              zoom="fit"
              liveArtifact={{
                reportPubId: target.reportPubId,
                versionPubId: target.versionPubId,
                byteSize: pdf.byteSize,
                mimeType: pdf.mimeType,
                sha256: pdf.sha256,
              }}
            />
          </>
        ) : (
          <>
            <ReportProjectionNotice target={target} collections={['versions', 'artifacts']} />
            <StatePanel state={artifactProjectionIncomplete ? 'insufficient' : 'empty'} />
          </>
        )}
      </section>
    );
  }
  if (active === 'trace') {
    return (
      <section className="panel">
        <h2>冻结事实与哈希</h2>
        <ReportProjectionNotice target={target} collections={['facts']} />
        {target.facts.length ? (
          <TableRegion label="报告冻结事实">
            <table className="data-table">
              <thead>
                <tr>
                  <th>事实</th>
                  <th>值</th>
                  <th>行哈希</th>
                </tr>
              </thead>
              <tbody>
                {target.facts.map((fact, index) => (
                  <tr key={`${fact.hash}-${index}`}>
                    <td>{fact.label}</td>
                    <td>{fact.value}</td>
                    <td>{fact.hash ? `${fact.hash.slice(0, 12)}…` : '不可用'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableRegion>
        ) : (
          <StatePanel state="empty" />
        )}
      </section>
    );
  }
  if (active === 'editor') {
    const collections: ReportProjectionCollection[] = [
      'versions',
      'sections',
      'sectionEvidenceIds',
    ];
    const incomplete = hasIncompleteReportProjection(target, collections);
    return (
      <>
        <ReportProjectionNotice target={target} collections={collections} />
        {target.sections.length ? (
          <LiveEditorWorkspace
            target={target}
            canAuthor={canAuthor && !incomplete}
            onVerify={onVerifyRevision}
            onAdopt={onAdoptRevision}
          />
        ) : (
          <StatePanel state="empty" />
        )}
        {canAuthor && incomplete ? (
          <span className="field-hint">
            当前章节投影不完整，禁止用浏览器中的部分章节覆盖不可变报告版本。
          </span>
        ) : null}
      </>
    );
  }
  if (active === 'diff') {
    const current = target.versions.at(-1);
    const previous = target.versions.at(-2);
    if (!current || !previous)
      return (
        <>
          <ReportProjectionNotice
            target={target}
            collections={['versions', 'sections', 'sectionEvidenceIds']}
          />
          <StatePanel state="insufficient" />
        </>
      );
    const sectionTitles = [
      ...new Set([...previous.sections, ...current.sections].map((section) => section.title)),
    ];
    return (
      <section className="panel">
        <span className="overline">Immutable comparison</span>
        <h2>
          版本 {previous.versionNumber} → {current.versionNumber}
        </h2>
        <p className="panel-subtitle">仅比较合同返回的不可变组件正文，不用事件摘要冒充正文差异。</p>
        <ReportProjectionNotice
          target={target}
          collections={['versions', 'sections', 'sectionEvidenceIds']}
        />
        <div className="diff-document" aria-label="真实报告版本正文差异">
          {sectionTitles.map((title) => {
            const before = previous.sections.find((section) => section.title === title)?.body ?? '';
            const after = current.sections.find((section) => section.title === title)?.body ?? '';
            const diff = createVersionDiff(before, after);
            return (
              <article key={title}>
                <h3>{title}</h3>
                <p>
                  {diff.chunks.map((chunk, index) =>
                    chunk.kind === 'removed' ? (
                      <del key={`${chunk.kind}-${index}`}>{chunk.text}</del>
                    ) : chunk.kind === 'added' ? (
                      <mark key={`${chunk.kind}-${index}`}>{chunk.text}</mark>
                    ) : (
                      <span key={`${chunk.kind}-${index}`}>{chunk.text}</span>
                    ),
                  )}
                </p>
                <small>
                  删除 {diff.removed} 字 · 新增 {diff.added} 字
                </small>
              </article>
            );
          })}
        </div>
      </section>
    );
  }
  if (active === 'evidence') {
    const artifactsIncomplete = hasIncompleteReportProjection(target, ['versions', 'artifacts']);
    const safeArtifacts = artifactsIncomplete ? [] : target.artifacts;
    return (
      <section className="panel">
        <h2>版本产物与证据绑定</h2>
        <ReportProjectionNotice
          target={target}
          collections={['versions', 'artifacts', 'evidenceBindings']}
        />
        {safeArtifacts.length ? (
          <TableRegion label="报告产物">
            <table className="data-table">
              <thead>
                <tr>
                  <th>格式</th>
                  <th>字节</th>
                  <th>访问</th>
                </tr>
              </thead>
              <tbody>
                {safeArtifacts.map((artifact) => (
                  <tr key={artifact.format}>
                    <td>{artifact.format.toUpperCase()}</td>
                    <td>{artifact.byteSize.toLocaleString()}</td>
                    <td>
                      <VerifiedArtifactDownload
                        reportPubId={target.reportPubId}
                        versionPubId={target.versionPubId}
                        artifact={artifact}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableRegion>
        ) : null}
        {target.evidenceBindings.length ? (
          <TableRegion label="冻结事实证据绑定">
            <table className="data-table">
              <thead>
                <tr>
                  <th>证据</th>
                  <th>类型与用途</th>
                  <th>锚点</th>
                  <th>完整性</th>
                </tr>
              </thead>
              <tbody>
                {target.evidenceBindings.map((binding) => (
                  <tr key={binding.id}>
                    <td>{binding.id}</td>
                    <td>
                      {binding.kind} · {binding.mimeType} · {binding.purpose}
                    </td>
                    <td>{binding.anchorCount}</td>
                    <td>{binding.hash ? 'SHA-256 已校验' : '校验值无效'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableRegion>
        ) : null}
        {!safeArtifacts.length && !target.evidenceBindings.length ? (
          <StatePanel state={artifactsIncomplete ? 'insufficient' : 'empty'} />
        ) : null}
      </section>
    );
  }
  return <StatePanel state="insufficient" />;
}

function ReviewWorkspace({
  state,
  onState,
  liveTarget,
  capabilities,
  onReconcile,
  onReconcileDelivery,
}: {
  state: ReportState;
  onState: (state: ReportState) => void;
  liveTarget?: LiveReportTarget | null;
  capabilities: ReportCapabilities;
  onReconcile?: (reportPubId: string) => Promise<LiveReportTarget | null>;
  onReconcileDelivery?: (
    reportPubId: string,
    deliveryPubId: string,
    recipientPubId: string,
  ) => Promise<boolean>;
}) {
  const [comments, setComments] = useState(
    liveTarget
      ? liveTarget.comments.filter((item) => !item.resolved)
      : [
          {
            id: 'fixture_comment',
            body: '请确认 Top 3 的分母是否排除了 degraded 样本。',
            resolved: false,
            createdAt: 'fixture',
          },
        ],
  );
  const [aiReviewed, setAiReviewed] = useState(
    !liveTarget ||
      !liveTarget.sections.some((section) => section.source === 'ai') ||
      ['approved', 'published'].includes(liveTarget.status),
  );
  const [writeState, setWriteState] = useState<'idle' | 'saving' | 'failed'>('idle');
  const [receipt, setReceipt] = useState('');
  const [pendingReconciliation, setPendingReconciliation] =
    useState<ReportReleaseReconciliation | null>(null);
  const reviewWrite = useReportMutationGuard(
    liveTarget
      ? createStructuredClientScopeKey(['review', liveTarget.reportPubId, liveTarget.versionPubId])
      : createStructuredClientScopeKey(['review', 'fixture']),
  );
  const {
    register: registerComment,
    handleSubmit: handleCommentSubmit,
    reset: resetComment,
    formState: { errors: commentErrors, isValid: commentIsValid },
  } = useForm<ReportCommentFields>({
    resolver: zodResolver(reportCommentSchema),
    defaultValues: { comment: '' },
    mode: 'onChange',
  });
  const {
    register: registerDelivery,
    handleSubmit: handleDeliverySubmit,
    reset: resetDelivery,
    formState: { errors: deliveryErrors, isValid: deliveryIsValid },
  } = useForm<ReportDeliveryFields>({
    resolver: zodResolver(reportDeliverySchema),
    defaultValues: { recipientPubId: '' },
    mode: 'onChange',
  });
  const reviewProjectionCollections: ReportProjectionCollection[] = [
    'versions',
    'facts',
    'sections',
    'sectionEvidenceIds',
    'artifacts',
    'evidenceBindings',
    'comments',
    'reviews',
    'events',
  ];
  const projectionIncomplete = liveTarget
    ? hasIncompleteReportProjection(liveTarget, reviewProjectionCollections)
    : false;
  const gates = {
    facts:
      !liveTarget ||
      (liveTarget.facts.length > 0 &&
        !hasIncompleteReportProjection(liveTarget, ['versions', 'facts'])),
    evidence:
      !liveTarget ||
      (liveTarget.sections.length > 0 &&
        liveTarget.evidenceBindings.length > 0 &&
        !hasIncompleteReportProjection(liveTarget, [
          'versions',
          'sections',
          'sectionEvidenceIds',
          'evidenceBindings',
        ])),
    aiReviewed,
    commentsResolved:
      comments.length === 0 &&
      (!liveTarget ||
        (!liveTarget.projectionNotices.comments &&
          !liveTarget.invalidProjection.includes('comments'))),
  };
  const allPassed = Object.values(gates).every(Boolean);
  const reconciliationLocked = writeState === 'saving' || pendingReconciliation !== null;
  const releaseProjectionConfirms = (
    target: LiveReportTarget,
    expected: Exclude<ReportReleaseReconciliation, { kind: 'delivery' }>,
  ): boolean => {
    if (expected.kind === 'comment') {
      return target.comments.some((comment) => comment.id === expected.commentPubId);
    }
    if (expected.kind === 'review') {
      return (
        target.status === 'approved' &&
        target.reviews.some(
          (review) => review.id === expected.reviewPubId && review.decision === 'approved',
        )
      );
    }
    return (
      target.status === 'published' && target.events.some((event) => event.type === 'published')
    );
  };
  const reconcileRelease = async (
    expected: ReportReleaseReconciliation,
    ticket: ReportMutationTicket,
  ) => {
    if (!liveTarget || !onReconcile || !onReconcileDelivery) return false;
    const confirmed =
      expected.kind === 'delivery'
        ? await onReconcileDelivery(
            liveTarget.reportPubId,
            expected.deliveryPubId,
            expected.recipientPubId,
          )
        : await onReconcile(liveTarget.reportPubId).then(
            (target) => target !== null && releaseProjectionConfirms(target, expected),
          );
    if (!reviewWrite.isCurrent(ticket)) {
      reviewWrite.finish(ticket);
      return false;
    }
    if (!confirmed) {
      reviewWrite.finish(ticket);
      setPendingReconciliation(expected);
      setWriteState('failed');
      return false;
    }
    if (!reviewWrite.finish(ticket)) return false;
    setPendingReconciliation(null);
    setReceipt(expected.receipt);
    setWriteState('idle');
    if (expected.kind === 'comment') {
      setComments((current) => [
        ...current,
        {
          id: expected.commentPubId,
          body: expected.body,
          resolved: false,
          createdAt: '已由权威投影确认',
        },
      ]);
      resetComment();
    } else if (expected.kind === 'review') {
      onState('approved');
    } else if (expected.kind === 'publish') {
      onState('published');
    } else {
      resetDelivery();
    }
    return true;
  };
  const retryReleaseReconciliation = async () => {
    if (!pendingReconciliation || !liveTarget || !onReconcile || !onReconcileDelivery) {
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setWriteState('failed');
      return;
    }
    const ticket = reviewWrite.begin(headers);
    if (!ticket) return;
    setWriteState('saving');
    await reconcileRelease(pendingReconciliation, ticket);
  };
  const saveReview = async () => {
    if (!capabilities.review || !allPassed || projectionIncomplete || pendingReconciliation) {
      return;
    }
    if (!liveTarget) {
      onState('approved');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers || !onReconcile || !onReconcileDelivery) {
      setWriteState('failed');
      return;
    }
    const ticket = reviewWrite.begin(headers);
    if (!ticket) return;
    setReceipt('');
    setWriteState('saving');
    const result = await reviewReport(
      liveTarget.reportPubId,
      liveTarget.versionPubId,
      { decision: 'approved', rationale: '事实、证据、AI 草稿与评论门均已人工核验。' },
      headers,
    );
    if (result.kind !== 'ready') {
      if (reviewWrite.finish(ticket)) setWriteState('failed');
      return;
    }
    const reviewPubId = result.data.reviewPubId;
    if (!reviewPubId) {
      if (reviewWrite.finish(ticket)) setWriteState('failed');
      return;
    }
    if (!reviewWrite.isCurrent(ticket)) {
      reviewWrite.finish(ticket);
      return;
    }
    const expected: ReportReleaseReconciliation = {
      kind: 'review',
      reviewPubId,
      receipt: '真实审核决定已记录',
    };
    setPendingReconciliation(expected);
    await reconcileRelease(expected, ticket);
  };
  const publish = async () => {
    if (!capabilities.publish || projectionIncomplete || pendingReconciliation) return;
    if (!liveTarget) {
      onState('published');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers || !onReconcile || !onReconcileDelivery) {
      setWriteState('failed');
      return;
    }
    const ticket = reviewWrite.begin(headers);
    if (!ticket) return;
    setReceipt('');
    setWriteState('saving');
    const result = await publishReport(liveTarget.reportPubId, liveTarget.versionPubId, headers);
    if (result.kind !== 'ready') {
      if (reviewWrite.finish(ticket)) setWriteState('failed');
      return;
    }
    if (!reviewWrite.isCurrent(ticket)) {
      reviewWrite.finish(ticket);
      return;
    }
    const expected: ReportReleaseReconciliation = {
      kind: 'publish',
      receipt: '真实发布操作已完成；尚未创建客户交付',
    };
    setPendingReconciliation(expected);
    await reconcileRelease(expected, ticket);
  };
  const deliver = handleDeliverySubmit(async ({ recipientPubId }) => {
    if (!capabilities.deliver || !liveTarget || projectionIncomplete || pendingReconciliation) {
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers || !onReconcile || !onReconcileDelivery) {
      setWriteState('failed');
      return;
    }
    const ticket = reviewWrite.begin(headers);
    if (!ticket) return;
    setReceipt('');
    setWriteState('saving');
    const result = await createReportDelivery(
      liveTarget.reportPubId,
      { recipient_pub_id: recipientPubId },
      headers,
    );
    if (result.kind !== 'ready') {
      if (reviewWrite.finish(ticket)) setWriteState('failed');
      return;
    }
    if (!reviewWrite.isCurrent(ticket)) {
      reviewWrite.finish(ticket);
      return;
    }
    const expected: ReportReleaseReconciliation = {
      kind: 'delivery',
      deliveryPubId: result.data.deliveryPubId,
      recipientPubId,
      receipt: '真实 delivery 已创建，指定客户可确认接收',
    };
    setPendingReconciliation(expected);
    await reconcileRelease(expected, ticket);
  });
  const addComment = handleCommentSubmit(async ({ comment }) => {
    if (projectionIncomplete || pendingReconciliation) return;
    if (liveTarget) {
      const headers = getValidatedIdentityHeaders();
      if (!headers || !onReconcile || !onReconcileDelivery) {
        setWriteState('failed');
        return;
      }
      const ticket = reviewWrite.begin(headers);
      if (!ticket) return;
      setReceipt('');
      setWriteState('saving');
      const result = await commentOnReport(
        liveTarget.reportPubId,
        liveTarget.versionPubId,
        { body: comment, parent_pub_id: null },
        headers,
      );
      if (result.kind !== 'ready') {
        if (reviewWrite.finish(ticket)) setWriteState('failed');
        return;
      }
      if (!reviewWrite.isCurrent(ticket)) {
        reviewWrite.finish(ticket);
        return;
      }
      const expected: ReportReleaseReconciliation = {
        kind: 'comment',
        commentPubId: result.data.commentPubId,
        body: comment,
        receipt: '真实审核评论已记录',
      };
      setPendingReconciliation(expected);
      await reconcileRelease(expected, ticket);
      return;
    }
    setComments((current) => [
      ...current,
      {
        id: `local_comment_${Date.now()}`,
        body: comment,
        resolved: false,
        createdAt: new Date().toISOString(),
      },
    ]);
    resetComment();
  });
  return (
    <div className="review-layout">
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Release gates</span>
            <h2>审核与发布门</h2>
          </div>
          <Badge tone={state === 'published' ? 'positive' : 'warning'}>{state}</Badge>
          {liveTarget ? <Badge tone="positive">真实 reports API</Badge> : null}
        </div>
        <ul className="gate-list">
          <li data-pass={gates.facts}>事实窗口已冻结</li>
          <li data-pass={gates.evidence}>KPI 与章节证据齐全</li>
          <li data-pass={gates.aiReviewed}>AI 草稿已人工确认</li>
          <li data-pass={gates.commentsResolved}>未解决评论已逐条纳入本次审核</li>
        </ul>
        {liveTarget ? (
          <ReportProjectionNotice target={liveTarget} collections={reviewProjectionCollections} />
        ) : null}
        {liveTarget &&
        liveTarget.sections.some((section) => section.source === 'ai') &&
        !aiReviewed ? (
          <button
            className="button button-secondary"
            disabled={!capabilities.review || projectionIncomplete || reconciliationLocked}
            onClick={() => setAiReviewed(true)}
          >
            确认 AI 草稿已人工复核
          </button>
        ) : null}
        <div className="button-row">
          <button
            className="button button-secondary"
            disabled={state !== 'frozen' || projectionIncomplete || reconciliationLocked}
            onClick={() => onState('review')}
          >
            提交审核
          </button>
          <button
            className="button button-secondary"
            disabled={
              state !== 'review' ||
              !allPassed ||
              !capabilities.review ||
              projectionIncomplete ||
              reconciliationLocked
            }
            onClick={() => void saveReview()}
          >
            批准发布
          </button>
          <button
            className="button"
            disabled={
              state !== 'approved' ||
              reconciliationLocked ||
              !capabilities.publish ||
              projectionIncomplete
            }
            onClick={() => void publish()}
          >
            发布 v1.0
          </button>
        </div>
        {!capabilities.review || !capabilities.publish ? (
          <span className="field-hint">
            分析师可提交报告与评论；审核决定和发布由项目审核人执行。
          </span>
        ) : null}
        {state === 'published' ? (
          <>
            <div className="confirmation" role="status">
              <Badge tone="positive">已发布</Badge>
              <span>在线版已生成；客户可见性以独立 delivery 记录为准。</span>
            </div>
            {liveTarget ? (
              <form className="form-field" onSubmit={(event) => void deliver(event)} noValidate>
                <label htmlFor="report-delivery-recipient">客户收件人 ID</label>
                <input
                  id="report-delivery-recipient"
                  {...registerDelivery('recipientPubId')}
                  placeholder="usr_..."
                  aria-invalid={Boolean(deliveryErrors.recipientPubId)}
                  aria-describedby={
                    deliveryErrors.recipientPubId ? 'report-delivery-recipient-error' : undefined
                  }
                />
                {deliveryErrors.recipientPubId ? (
                  <span id="report-delivery-recipient-error" className="field-error" role="alert">
                    {deliveryErrors.recipientPubId.message}
                  </span>
                ) : null}
                <button
                  type="submit"
                  className="button"
                  disabled={
                    !deliveryIsValid ||
                    reconciliationLocked ||
                    !capabilities.deliver ||
                    projectionIncomplete
                  }
                >
                  创建客户交付
                </button>
                {!capabilities.deliver ? (
                  <span className="field-hint">客户交付仅允许项目审核人执行。</span>
                ) : null}
              </form>
            ) : null}
          </>
        ) : null}
        {writeState === 'saving' && pendingReconciliation ? (
          <div className="confirmation" role="status">
            <Badge tone="warning">正在确认</Badge>
            <span>写入已接受，正在重新读取同一报告的权威发布投影。</span>
          </div>
        ) : null}
        {receipt ? <Toast>{receipt}</Toast> : null}
        {writeState === 'failed' ? (
          <StatePanel
            state="failed"
            {...(pendingReconciliation ? { onRetry: () => void retryReleaseReconciliation() } : {})}
          />
        ) : null}
      </section>
      <aside className="panel">
        <h2>审核评论</h2>
        <div className="comment-list">
          {comments.map((item) => (
            <article key={item.id}>
              <p>{item.body}</p>
              <button
                disabled={reconciliationLocked}
                onClick={() =>
                  setComments((current) => current.filter((value) => value.id !== item.id))
                }
              >
                纳入本次审核
              </button>
            </article>
          ))}
        </div>
        <form onSubmit={(event) => void addComment(event)} noValidate>
          <label className="form-field" htmlFor="review-comment">
            <span>新增评论</span>
            <textarea
              id="review-comment"
              rows={3}
              {...registerComment('comment')}
              aria-invalid={Boolean(commentErrors.comment)}
              aria-describedby={commentErrors.comment ? 'review-comment-error' : undefined}
            />
            {commentErrors.comment ? (
              <span id="review-comment-error" className="field-error" role="alert">
                {commentErrors.comment.message}
              </span>
            ) : null}
          </label>
          <button
            type="submit"
            className="button button-secondary"
            disabled={!commentIsValid || reconciliationLocked || projectionIncomplete}
          >
            添加评论
          </button>
        </form>
      </aside>
    </div>
  );
}

function OutcomesWorkspace({
  liveTarget,
  canAuthor,
  onReconcile,
}: {
  liveTarget?: LiveReportTarget | null;
  canAuthor: boolean;
  onReconcile?: (reportPubId: string) => Promise<LiveReportTarget | null>;
}) {
  const projectionIncomplete = Boolean(
    liveTarget && hasIncompleteReportProjection(liveTarget, ['actions', 'effectRetests']),
  );
  const initialAction = liveTarget?.actions.at(-1);
  const [status, setStatus] = useState<'planned' | 'running' | 'reviewed'>(
    initialAction?.retests.length
      ? 'reviewed'
      : initialAction?.state === 'in_progress'
        ? 'running'
        : 'planned',
  );
  const [writeState, setWriteState] = useState<'idle' | 'saving' | 'failed'>('idle');
  const [receipt, setReceipt] = useState('');
  const [pendingReconciliation, setPendingReconciliation] =
    useState<ReportOutcomeReconciliation | null>(null);
  const [actionPubId, setActionPubId] = useState(
    liveTarget ? (initialAction?.pubId ?? '') : 'fixture_action',
  );
  const outcomeWrite = useReportMutationGuard(
    liveTarget
      ? createStructuredClientScopeKey([
          'outcomes',
          liveTarget.reportPubId,
          liveTarget.versionPubId,
        ])
      : createStructuredClientScopeKey(['outcomes', 'fixture']),
  );
  const {
    register: registerRetest,
    handleSubmit: handleRetestSubmit,
    formState: { errors: retestErrors, isValid: retestIsValid },
  } = useForm<ReportRetestFields>({
    resolver: zodResolver(reportRetestSchema),
    defaultValues: { delta: Number.NaN },
    mode: 'onChange',
  });
  const reconciliationLocked = writeState === 'saving' || pendingReconciliation !== null;
  const reconcileOutcome = async (
    expected: ReportOutcomeReconciliation,
    ticket: ReportMutationTicket,
  ) => {
    if (!liveTarget || !onReconcile) return false;
    const target = await onReconcile(liveTarget.reportPubId);
    if (!outcomeWrite.isCurrent(ticket)) {
      outcomeWrite.finish(ticket);
      return false;
    }
    const action = target?.actions.find((candidate) => candidate.pubId === expected.actionPubId);
    const confirmed =
      expected.kind === 'action'
        ? action?.state === 'in_progress'
        : action?.state === 'done' &&
          action.retests.some((retest) => retest.pubId === expected.effectRetestPubId);
    if (!confirmed) {
      outcomeWrite.finish(ticket);
      setPendingReconciliation(expected);
      setWriteState('failed');
      return false;
    }
    if (!outcomeWrite.finish(ticket)) return false;
    setPendingReconciliation(null);
    setActionPubId(expected.actionPubId);
    setReceipt(expected.receipt);
    setWriteState('idle');
    setStatus(expected.kind === 'action' ? 'running' : 'reviewed');
    return true;
  };
  const retryOutcomeReconciliation = async () => {
    if (!pendingReconciliation || !liveTarget || !onReconcile) return;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setWriteState('failed');
      return;
    }
    const ticket = outcomeWrite.begin(headers);
    if (!ticket) return;
    setWriteState('saving');
    await reconcileOutcome(pendingReconciliation, ticket);
  };
  const startAction = async () => {
    if (!canAuthor || projectionIncomplete || pendingReconciliation) return;
    if (liveTarget) {
      const headers = getValidatedIdentityHeaders();
      if (!headers || !onReconcile) {
        setWriteState('failed');
        return;
      }
      const ticket = outcomeWrite.begin(headers);
      if (!ticket) return;
      setWriteState('saving');
      let nextActionPubId = actionPubId;
      if (!nextActionPubId) {
        const result = await createReportAction(
          liveTarget.reportPubId,
          {
            description: '补齐私有化部署权威材料',
            owner_pub_id: null,
            baseline: { source: 'report_review', version: liveTarget.versionNumber },
          },
          headers,
        );
        nextActionPubId = result.kind === 'ready' ? result.data.actionPubId : '';
      }
      if (!nextActionPubId) {
        if (outcomeWrite.finish(ticket)) setWriteState('failed');
        return;
      }
      if (!outcomeWrite.isCurrent(ticket)) {
        outcomeWrite.finish(ticket);
        return;
      }
      const updateResult = await updateReportAction(
        liveTarget.reportPubId,
        nextActionPubId,
        { state: 'in_progress', outcome: null },
        headers,
      );
      if (updateResult.kind !== 'ready') {
        if (outcomeWrite.finish(ticket)) setWriteState('failed');
        return;
      }
      if (!outcomeWrite.isCurrent(ticket)) {
        outcomeWrite.finish(ticket);
        return;
      }
      const expected: ReportOutcomeReconciliation = {
        kind: 'action',
        actionPubId: nextActionPubId,
        receipt: '真实优化行动已登记',
      };
      setPendingReconciliation(expected);
      await reconcileOutcome(expected, ticket);
      return;
    }
    setStatus('running');
  };
  const recordRetest = async (delta?: number) => {
    if (!canAuthor || projectionIncomplete || pendingReconciliation) return;
    if (liveTarget) {
      const headers = getValidatedIdentityHeaders();
      if (!headers || !actionPubId || delta === undefined || !onReconcile) {
        setWriteState('failed');
        return;
      }
      const ticket = outcomeWrite.begin(headers);
      if (!ticket) return;
      setWriteState('saving');
      const requestedActionPubId = actionPubId;
      const result = await createReportEffectRetest(
        liveTarget.reportPubId,
        requestedActionPubId,
        {
          measured_at: new Date().toISOString(),
          result: {
            metric: 'mention_rate',
            baseline_version: liveTarget.versionNumber,
            delta,
          },
        },
        headers,
      );
      if (result.kind !== 'ready') {
        if (outcomeWrite.finish(ticket)) setWriteState('failed');
        return;
      }
      const effectRetestPubId = result.data.effectRetestPubId;
      if (!effectRetestPubId) {
        if (outcomeWrite.finish(ticket)) setWriteState('failed');
        return;
      }
      if (!outcomeWrite.isCurrent(ticket)) {
        outcomeWrite.finish(ticket);
        return;
      }
      const updateResult = await updateReportAction(
        liveTarget.reportPubId,
        requestedActionPubId,
        { state: 'done', outcome: { delta } },
        headers,
      );
      if (updateResult.kind !== 'ready') {
        if (outcomeWrite.finish(ticket)) setWriteState('failed');
        return;
      }
      if (!outcomeWrite.isCurrent(ticket)) {
        outcomeWrite.finish(ticket);
        return;
      }
      const expected: ReportOutcomeReconciliation = {
        kind: 'retest',
        actionPubId: requestedActionPubId,
        effectRetestPubId,
        receipt: '真实效果复测已追加记录',
      };
      setPendingReconciliation(expected);
      await reconcileOutcome(expected, ticket);
      return;
    }
    setStatus('reviewed');
  };
  const visibleAction = liveTarget?.actions.at(-1);
  const liveRetests = (liveTarget?.actions.flatMap((action) => action.retests) ?? []).sort(
    (left, right) => left.measuredAt.localeCompare(right.measuredAt),
  );
  const latestLiveRetest = liveRetests.at(-1);
  const actionTotal =
    liveTarget?.projectionNotices.actions?.total ?? liveTarget?.actions.length ?? 0;
  const retestTotal = liveTarget?.projectionNotices.effectRetests?.total ?? liveRetests.length;
  return (
    <>
      <MetricGrid
        metrics={
          liveTarget
            ? [
                {
                  label: '建议',
                  value: String(actionTotal),
                  detail: projectionIncomplete ? '当前为受控部分投影' : '真实优化行动',
                  ...(projectionIncomplete ? { state: 'insufficient' as const } : {}),
                },
                {
                  label: '执行中',
                  value: String(
                    liveTarget.actions.filter((action) => action.state === 'in_progress').length,
                  ),
                  detail: projectionIncomplete ? '部分投影不可推断总数' : '合同状态',
                  ...(projectionIncomplete ? { state: 'insufficient' as const } : {}),
                },
                {
                  label: '复测记录',
                  value: String(retestTotal),
                  detail: projectionIncomplete ? '当前为受控部分投影' : '不可变记录',
                  ...(projectionIncomplete ? { state: 'insufficient' as const } : {}),
                },
                {
                  label: '最近效果',
                  value: projectionIncomplete ? '—' : (latestLiveRetest?.result ?? '—'),
                  detail: projectionIncomplete
                    ? '部分投影不推断最新值'
                    : latestLiveRetest
                      ? latestLiveRetest.measuredAt
                      : '复测后可用',
                  ...(projectionIncomplete ? { state: 'insufficient' as const } : {}),
                },
              ]
            : [
                { label: '建议', value: '6', detail: '高优先级 2' },
                {
                  label: '执行中',
                  value: status === 'running' ? '1' : '0',
                  detail: '负责人已分配',
                },
                { label: '复测窗口', value: '30 天', detail: '最小到日' },
                {
                  label: '效果 Delta',
                  value: status === 'reviewed' ? '+6.2pp' : '—',
                  detail: '复测后可用',
                },
              ]
        }
      />
      <section className="panel">
        <h2>优化建议与效果复盘</h2>
        {liveTarget ? <Badge tone="positive">真实 reports API</Badge> : null}
        {liveTarget ? (
          <ReportProjectionNotice target={liveTarget} collections={['actions', 'effectRetests']} />
        ) : null}
        <TableRegion label="优化建议与复测表">
          <table className="data-table">
            <thead>
              <tr>
                <th>建议</th>
                <th>负责人</th>
                <th>状态</th>
                <th>复测</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{visibleAction?.description ?? '补齐私有化部署权威材料'}</td>
                <td>{liveTarget ? '安全投影未提供' : '内容负责人 · 周岚'}</td>
                <td>
                  <Badge tone={status === 'planned' ? 'warning' : 'positive'}>
                    {visibleAction?.state ?? status}
                  </Badge>
                </td>
                <td>
                  {projectionIncomplete
                    ? '部分投影，不推断'
                    : (visibleAction?.retests.at(-1)?.result ?? '待复测')}
                </td>
              </tr>
            </tbody>
          </table>
        </TableRegion>
        <WorkflowTimeline
          label="建议执行与复测进度"
          steps={[
            {
              id: 'plan',
              label: '建议已确认',
              state: 'completed',
              detail: liveTarget ? '真实 action 记录' : '2026-07-24',
            },
            {
              id: 'execute',
              label: '内容优化',
              state: status === 'planned' ? 'scheduled' : 'completed',
              detail: liveTarget ? '按 action 状态推进' : '负责人 周岚',
            },
            {
              id: 'retest',
              label: '30 天复测',
              state: status === 'reviewed' ? 'completed' : 'scheduled',
              detail: liveTarget ? '等待人工录入真实复测' : '2026-08-21',
            },
          ]}
        />
        <div className="button-row">
          <button
            className="button button-secondary"
            disabled={reconciliationLocked || !canAuthor || projectionIncomplete}
            onClick={() => void startAction()}
          >
            开始执行
          </button>
          <button
            type={liveTarget ? 'submit' : 'button'}
            form={liveTarget ? 'report-retest-form' : undefined}
            className="button"
            disabled={
              status !== 'running' ||
              reconciliationLocked ||
              !canAuthor ||
              !actionPubId ||
              projectionIncomplete ||
              (Boolean(liveTarget) && !retestIsValid)
            }
            onClick={liveTarget ? undefined : () => void recordRetest()}
          >
            记录复测效果
          </button>
        </div>
        {!canAuthor ? <span className="field-hint">优化行动与复测由分析师维护。</span> : null}
        {liveTarget && status === 'running' ? (
          <form
            id="report-retest-form"
            onSubmit={(event) => void handleRetestSubmit(({ delta }) => recordRetest(delta))(event)}
            noValidate
          >
            <label className="form-field">
              <span>效果变化（百分点，-100 至 100）</span>
              <input
                aria-label="效果变化"
                type="number"
                min="-100"
                max="100"
                step="0.1"
                {...registerRetest('delta', { valueAsNumber: true })}
                aria-invalid={Boolean(retestErrors.delta)}
                aria-describedby={retestErrors.delta ? 'report-retest-delta-error' : undefined}
              />
              {retestErrors.delta ? (
                <span id="report-retest-delta-error" className="field-error" role="alert">
                  {retestErrors.delta.message}
                </span>
              ) : null}
            </label>
          </form>
        ) : null}
        {writeState === 'saving' && pendingReconciliation ? (
          <div className="confirmation" role="status">
            <Badge tone="warning">正在确认</Badge>
            <span>写入已接受，正在重新读取同一报告的权威效果投影。</span>
          </div>
        ) : null}
        {receipt ? <Toast>{receipt}</Toast> : null}
        {writeState === 'failed' ? (
          <StatePanel
            state="failed"
            {...(pendingReconciliation ? { onRetry: () => void retryOutcomeReconciliation() } : {})}
          />
        ) : null}
      </section>
    </>
  );
}

// ══ AI 操作右栏（20260807 起）：可展开/折叠，列出所有使用 AI 的操作；══════════════
// 每个操作带下拉抽屉（<details>）选择模型。模型清单由服务端下发
// （GEO_RESEARCH_LLM_MODELS 为唯一真源），选择记忆在 localStorage（geo.ai.model.<opId>），
// 提交时由对应工作区读取。报告起草与信息表调研各自独立选模型。
const aiDockExpandedKey = 'geo.ai.dock.expanded';
const aiOperationModelKey = (opId: string) => `geo.ai.model.${opId}`;

type AiOperation = Readonly<{ id: string; label: string; description: string }>;
const aiOperations: readonly AiOperation[] = [
  {
    id: 'report-draft',
    label: 'AI 起草报告章节',
    description:
      '基于报告已冻结事实撰写章节散文草稿；未溯源数字整句洗刷。草稿以「待人工确认」标记，复核后才可随版本发布。',
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
const readAiOperationModel = (opId: string): string =>
  readAiDockStorage(aiOperationModelKey(opId));

function AiOpsDock() {
  const experience = useOptionalExperienceContext();
  const live = experience?.source === 'live';
  const [expanded, setExpanded] = useState(() => readAiDockStorage(aiDockExpandedKey) !== '0');
  const [models, setModels] = useState<readonly string[]>([]);
  const [pinned, setPinned] = useState(() => readAiOperationModel('report-draft'));
  useEffect(() => {
    if (!live) {
      setModels([]);
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) return;
    let cancelled = false;
    void getReportAiDraftModels(headers).then((result) => {
      if (!cancelled && result.kind === 'ready') setModels(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [experience, live]);
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
                  起草模型
                  <select
                    aria-label={`${op.label}模型选择`}
                    value={effective}
                    disabled={models.length === 0}
                    onChange={(event) => choose(op.id, event.target.value)}
                  >
                    {models.length ? (
                      models.map((model, index) => (
                        <option key={model} value={model}>
                          {index === 0 ? `${model}（默认）` : model}
                        </option>
                      ))
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
  const [retryKey, retry] = useLocalRetry();
  const fixtureMode = experience?.source !== 'live';
  const hasRole = (role: 'analyst' | 'reviewer' | 'admin') =>
    fixtureMode || Boolean(experience?.roles.includes(role));
  const reportCapabilities: ReportCapabilities = {
    author: hasRole('analyst') || hasRole('admin'),
    review: hasRole('reviewer') || hasRole('admin'),
    publish: hasRole('reviewer') || hasRole('admin'),
    deliver: hasRole('reviewer') || hasRole('admin'),
  };
  const [searchParams, setSearchParams] = useSearchParams();
  const [state, setState] = useState<ReportState>('draft');
  const [sections, setSections] = useState(initialSections);
  const [savedVersions, setSavedVersions] = useState(initialVersions);
  const [livePage, setLivePage] = useState<ReportPageProjection | null>(null);
  const [liveTarget, setLiveTarget] = useState<LiveReportTarget | null>(null);
  const [liveNextCursor, setLiveNextCursor] = useState('');
  const [catalogProjection, setCatalogProjection] = useState<ProjectReportCatalogProjection>({
    total: 0,
    shown: 0,
    scanned: 0,
    invalid: false,
    incomplete: false,
  });
  const [detailProjectionInvalid, setDetailProjectionInvalid] = useState(false);
  const cursorByPage = useRef(new Map<number, string>());
  const detailRequestGenerationRef = useRef(0);
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
    experience ? createSafeExperienceScopeKey(experience) : 'missing-experience',
    String(retryKey),
    String(requestedPage),
    reportCursor,
  ]);
  const currentReportReadScopeRef = useRef(reportReadScope);
  currentReportReadScopeRef.current = reportReadScope;
  const currentLiveTargetRef = useRef(liveTarget);
  currentLiveTargetRef.current = liveTarget;
  const [liveState, setLiveState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const [liveResultScope, setLiveResultScope] = useState(
    experience?.source === 'live' ? '' : reportReadScope,
  );
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
    let cancelled = false;
    const requestGeneration = ++detailRequestGenerationRef.current;
    const superseded = () => cancelled || requestGeneration !== detailRequestGenerationRef.current;
    const commitLiveState = (nextState: 'ready' | 'failed' | 'forbidden') => {
      if (superseded()) return;
      setLiveResultScope(reportReadScope);
      setLiveState(nextState);
    };
    const headers = getValidatedIdentityHeaders();
    const expectedProjectPubId = experience.projectPubId;
    if (!headers) {
      commitLiveState('failed');
      return;
    }
    setLiveState('loading');
    setLiveTarget(null);
    setLivePage(null);
    setLiveNextCursor('');
    setCatalogProjection({
      total: 0,
      shown: 0,
      scanned: 0,
      invalid: false,
      incomplete: false,
    });
    setDetailProjectionInvalid(false);
    if (requestedPage === 1) cursorByPage.current.clear();
    void loadProjectReportCatalog(headers, expectedProjectPubId, reportCursor).then(
      async (result) => {
        if (superseded()) return;
        if (result.kind === 'ready') {
          setCatalogProjection(result.projection);
          if (result.projection.invalid) {
            setLivePage(null);
            setLiveTarget(null);
            commitLiveState('failed');
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
              setLiveTarget(null);
              setDetailProjectionInvalid(detail.kind === 'invalid');
              commitLiveState(detail.kind === 'forbidden' ? 'forbidden' : 'failed');
              return;
            }
            const target = projectLiveReportTarget(detail.data, reportPubId, expectedProjectPubId);
            setLiveTarget(target);
            if (target) {
              setState(toReportState(target.status));
            } else {
              setDetailProjectionInvalid(true);
              commitLiveState('failed');
              return;
            }
          }
          commitLiveState('ready');
        } else {
          setLivePage(null);
          setLiveTarget(null);
          setLiveNextCursor('');
          setCatalogProjection({
            total: 0,
            shown: 0,
            scanned: 0,
            invalid: false,
            incomplete: false,
          });
          setDetailProjectionInvalid(false);
          commitLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        }
      },
    );
    return () => {
      cancelled = true;
      detailRequestGenerationRef.current += 1;
    };
  }, [experience, reportCursor, reportReadScope, requestedPage, retryKey]);
  const ownsCurrentReportResult =
    experience?.source === 'live' && liveResultScope === reportReadScope;
  const effectiveLiveState =
    experience?.source === 'live' && !ownsCurrentReportResult ? 'loading' : liveState;
  const reportPageCount = Math.max(1, requestedPage + (liveNextCursor ? 1 : 0));
  const changeReportPage = (nextPage: number) => {
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
    void setSearchParams(next);
  };
  const readLiveReportProjection = async (
    reportPubId: string,
    adopt: boolean,
  ): Promise<LiveReportTarget | null> => {
    const ownedScope = reportReadScope;
    const requestGeneration = ++detailRequestGenerationRef.current;
    const projectPubId = experience?.projectPubId;
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId || currentLiveTargetRef.current?.reportPubId !== reportPubId)
      return null;
    const detail = await getReport(reportPubId, headers);
    if (
      requestGeneration !== detailRequestGenerationRef.current ||
      currentReportReadScopeRef.current !== ownedScope ||
      currentLiveTargetRef.current?.reportPubId !== reportPubId ||
      detail.kind !== 'ready'
    )
      return null;
    const target = projectLiveReportTarget(detail.data, reportPubId, projectPubId);
    if (!target) return null;
    if (adopt) {
      setLiveTarget(target);
      setDetailProjectionInvalid(false);
      setState(toReportState(target.status));
    }
    return target;
  };
  const reconcileLiveReport = (reportPubId: string) => readLiveReportProjection(reportPubId, true);
  const adoptVerifiedLiveReport = (target: LiveReportTarget) => {
    if (currentLiveTargetRef.current?.reportPubId !== target.reportPubId) return;
    setLiveTarget(target);
    setDetailProjectionInvalid(false);
    setState(toReportState(target.status));
  };
  const reconcileReportDelivery = async (
    reportPubId: string,
    deliveryPubId: string,
    recipientPubId: string,
  ): Promise<boolean> => {
    const ownedScope = reportReadScope;
    const headers = getValidatedIdentityHeaders();
    if (!headers || currentLiveTargetRef.current?.reportPubId !== reportPubId) return false;
    const result = await listReportDeliveries(reportPubId, headers);
    if (
      currentReportReadScopeRef.current !== ownedScope ||
      currentLiveTargetRef.current?.reportPubId !== reportPubId ||
      result.kind !== 'ready' ||
      result.data.projection.invalid
    )
      return false;
    return result.data.data.some(
      (delivery) =>
        delivery.pub_id === deliveryPubId &&
        delivery.report_pub_id === reportPubId &&
        delivery.recipient_pub_id === recipientPubId,
    );
  };
  return (
    <>
      <ProductShell
        product="Report Studio"
        title="报告工作室"
        description="冻结事实窗口，编辑可追溯章节，并通过审核门发布。"
        probe={getHealth}
        nav={experience?.source === 'live' ? liveNav : nav}
      >
      {(active) =>
        active === 'window' ? (
          <>
            {ownsCurrentReportResult && catalogProjection.total > 1 ? (
              <ProjectionLimitNotice
                items={[
                  {
                    key: 'report-catalog',
                    label: '当前检索窗口内的项目报告',
                    total: catalogProjection.total,
                    shown: catalogProjection.shown,
                  },
                ]}
              />
            ) : null}
            {ownsCurrentReportResult && catalogProjection.incomplete ? (
              <div className="confirmation projection-limit-notice" role="status">
                <Badge tone="warning">项目目录仍在后续页</Badge>
                <span>
                  已安全扫描 {catalogProjection.scanned}{' '}
                  条租户报告但尚未确认下一份当前项目报告；可用分页继续扫描，且不会据此推断项目报告总数。
                </span>
              </div>
            ) : null}
            {ownsCurrentReportResult && catalogProjection.invalid ? (
              <div className="confirmation projection-limit-notice" role="alert">
                <Badge tone="warning">安全投影不完整</Badge>
                <span>
                  报告目录包含跨项目、重复标识、乱序时间、游标不一致或未通过 DLP
                  校验的记录；未请求其详情。
                </span>
              </div>
            ) : null}
            {ownsCurrentReportResult && detailProjectionInvalid ? (
              <div className="confirmation projection-limit-notice" role="alert">
                <Badge tone="warning">详情投影已拒绝</Badge>
                <span>
                  报告详情未与请求报告和当前项目严格绑定，或根级标题、状态、时间未通过安全校验；未采用任何版本事实。
                </span>
              </div>
            ) : null}
            <WindowWorkspace
              state={state}
              onFreeze={() => setState('frozen')}
              livePage={livePage}
              liveState={effectiveLiveState}
              page={requestedPage}
              pageCount={reportPageCount}
              onPageChange={changeReportPage}
              onRetry={retry}
              emptyContent={
                experience?.source === 'live' ? (
                  <CreateReportWorkspace
                    projectPubId={experience.projectPubId}
                    canAuthor={reportCapabilities.author}
                    onCreated={retry}
                  />
                ) : undefined
              }
            />
          </>
        ) : active === 'review' && experience?.source === 'live' ? (
          effectiveLiveState === 'loading' ? (
            <StatePanel state="loading" />
          ) : effectiveLiveState === 'failed' ? (
            <StatePanel state="failed" onRetry={retry} />
          ) : effectiveLiveState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : liveTarget ? (
            <ReviewWorkspace
              key={`review:${liveTarget.reportPubId}:${liveTarget.versionPubId}`}
              state={state}
              onState={setState}
              liveTarget={liveTarget}
              capabilities={reportCapabilities}
              onReconcile={reconcileLiveReport}
              onReconcileDelivery={reconcileReportDelivery}
            />
          ) : (
            <StatePanel state="empty" />
          )
        ) : active === 'outcomes' && experience?.source === 'live' ? (
          effectiveLiveState === 'loading' ? (
            <StatePanel state="loading" />
          ) : effectiveLiveState === 'failed' ? (
            <StatePanel state="failed" onRetry={retry} />
          ) : effectiveLiveState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : liveTarget ? (
            <OutcomesWorkspace
              key={`outcomes:${liveTarget.reportPubId}:${liveTarget.versionPubId}`}
              liveTarget={liveTarget}
              canAuthor={reportCapabilities.author}
              onReconcile={reconcileLiveReport}
            />
          ) : (
            <StatePanel state="empty" />
          )
        ) : experience?.source === 'live' ? (
          effectiveLiveState === 'loading' ? (
            <StatePanel state="loading" />
          ) : effectiveLiveState === 'failed' ? (
            <StatePanel state="failed" onRetry={retry} />
          ) : effectiveLiveState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : liveTarget && ['trace', 'editor', 'diff', 'evidence', 'preview'].includes(active) ? (
            <LiveDetailWorkspace
              key={`${active}:${liveTarget.reportPubId}`}
              active={active as 'trace' | 'editor' | 'diff' | 'evidence' | 'preview'}
              target={liveTarget}
              canAuthor={reportCapabilities.author}
              onVerifyRevision={(reportPubId) => readLiveReportProjection(reportPubId, false)}
              onAdoptRevision={adoptVerifiedLiveReport}
            />
          ) : (
            <StatePanel state="empty" />
          )
        ) : active === 'trace' ? (
          <TraceWorkspace />
        ) : active === 'editor' ? (
          <EditorWorkspace
            sections={sections}
            onChange={setSections}
            savedVersions={savedVersions}
            onSaveVersion={(version) =>
              setSavedVersions((current) => [
                version,
                ...current.filter(
                  (item) =>
                    item.sectionId !== version.sectionId || item.version !== version.version,
                ),
              ])
            }
          />
        ) : active === 'diff' ? (
          <VersionDiffWorkspace versions={savedVersions} />
        ) : active === 'evidence' ? (
          <EvidenceWorkspace />
        ) : active === 'preview' ? (
          <PreviewWorkspace sections={sections} />
        ) : active === 'review' ? (
          <ReviewWorkspace state={state} onState={setState} capabilities={reportCapabilities} />
        ) : (
          <OutcomesWorkspace canAuthor={reportCapabilities.author} />
        )
      }
      </ProductShell>
      <AiOpsDock />
    </>
  );
}
