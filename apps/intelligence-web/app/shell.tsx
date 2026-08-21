import { useEffect, useMemo, useRef, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Background,
  BaseEdge,
  Controls,
  getBezierPath,
  ReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useForm } from 'react-hook-form';
import {
  Badge,
  containsClientSecret,
  createSafeExperienceScopeKey,
  createStructuredClientScopeKey,
  Dialog,
  downloadSafeGeneratedFile,
  MetricGrid,
  Pagination,
  ProjectionLimitNotice,
  ProductShell,
  StatePanel,
  useOptionalExperienceContext,
} from '@geo/design-system';
import {
  createEvidencePackage,
  createEvidencePackagePubId,
  createInvestigationAppeal,
  createInvestigationVerdict,
  getHealth,
  getInvestigation,
  getInvestigationPageHistory,
  getInvestigationVisualDiffs,
  intelligenceReadProjectionLimits,
  listInvestigations,
  projectSafeIsoTimestamp,
  resolveInvestigationAppeal,
  type EvidencePackageSafeReceipt,
  type InvestigationPageProjection,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { useSearchParams } from 'react-router';
import { z } from 'zod';
import { useIntelligenceMutationGuard } from './mutation-guard';
import { CalibrationWorkspace as GovernedCalibrationWorkspace } from './calibration-workspace';
import { SourceIntelligenceWorkspace } from './source-intelligence-workspace';

const nav = [
  { id: 'cases', label: '案件' },
  { id: 'claims', label: 'Claim 矩阵' },
  { id: 'sources', label: '多源证据' },
  { id: 'source-insight', label: '信源洞察' },
  { id: 'graph', label: '传播关系' },
  { id: 'history', label: '页面历史' },
  { id: 'calibration', label: '模型准入' },
  { id: 'verdict', label: '裁决与申诉', badge: '1' },
  { id: 'package', label: '证据包' },
];
const liveNav = nav.map((item) => ({ id: item.id, label: item.label }));

type Verdict = 'pending' | 'confirmed' | 'rejected' | 'appealed' | 'reviewed';
type IntelligenceCapabilities = {
  analyze: boolean;
  review: boolean;
};
type GovernanceReconciliation =
  | {
      kind: 'verdict';
      verdictPubId: string;
      verdict: Extract<Verdict, 'confirmed' | 'rejected'>;
      receipt: string;
    }
  | {
      kind: 'appeal';
      appealPubId: string;
      receipt: string;
    }
  | {
      kind: 'resolution';
      appealPubId: string;
      receipt: string;
    };

function useLocalRetry(): [number, () => void] {
  const [retryKey, setRetryKey] = useState(0);
  return [retryKey, () => setRetryKey((current) => current + 1)];
}

const appealReasonSchema = z.object({
  reason: z
    .string()
    .trim()
    .min(8, '申诉理由至少需要 8 个字')
    .max(2000, '申诉理由不能超过 2000 个字')
    .refine(
      (value) => !containsClientSecret(value),
      '请勿在申诉中粘贴验证码、Cookie、token、密码、完整手机号或 profile 路径',
    ),
});
type AppealReasonFields = z.infer<typeof appealReasonSchema>;

export const investigationProjectionLimits = {
  scores: intelligenceReadProjectionLimits.scores,
  explanations: intelligenceReadProjectionLimits.explanations,
  claims: intelligenceReadProjectionLimits.claims,
  evidenceMatrix: intelligenceReadProjectionLimits.evidenceMatrix,
  sourceIndependence: intelligenceReadProjectionLimits.sourceIndependence,
  graph: intelligenceReadProjectionLimits.graph,
  appeals: intelligenceReadProjectionLimits.appeals,
  verdicts: intelligenceReadProjectionLimits.verdicts,
  historyPages: intelligenceReadProjectionLimits.historyPages,
  historyDiffs: intelligenceReadProjectionLimits.historyDiffs,
} as const;
type InvestigationProjectionCollection = keyof typeof investigationProjectionLimits;
type ProjectionNotice = {
  total: number;
  shown: number;
};
type ProjectionNotices = Partial<Record<InvestigationProjectionCollection, ProjectionNotice>>;

type LiveInvestigationTarget = {
  investigationPubId: string;
  probability: number | null;
  evidenceSufficiency: number | null;
  uncertainty: number | null;
  ruleVersion: string;
  explanations: string[];
  claims: {
    id: string;
    text: string;
    verifiability: string;
  }[];
  evidenceMatrix: {
    id: string;
    claimId: string;
    evidenceId: string;
    relation: string;
    cluster: string;
    weight: number | null;
    rationale: string;
  }[];
  sourceIndependence: {
    id: string;
    sourceId: string;
    cluster: string;
    weight: number | null;
    circularRisk: number | null;
  }[];
  graph: {
    from: string;
    to: string;
    relation: string;
    weight: number | null;
    evidenceId: string;
  }[];
  appealStates: {
    id: string;
    state: string;
  }[];
  verdictPubIds: string[];
  openAppealPubId: string;
  verdictState: Verdict;
  projectionNotices: ProjectionNotices;
  invalidProjection: InvestigationProjectionCollection[];
};
type LiveHistoryTarget = {
  pages: {
    contentPubId: string;
    versionPubId: string;
    versionNumber: number;
    title: string;
    source: string;
    capturedAt: string;
    snapshotNumber: number | null;
    bodyHash: string;
  }[];
  diffs: {
    pubId: string;
    contentPubId: string;
    beforeVersionPubId: string;
    afterVersionPubId: string;
    similarity: number | null;
    visualDiffAvailable: boolean;
    beforeHash: string;
    afterHash: string;
  }[];
  projectionNotices: ProjectionNotices;
  invalidProjection: InvestigationProjectionCollection[];
};
type Evidence = {
  id: string;
  source: string;
  kind: string;
  cluster: string;
  stance: '支持' | '反驳' | '背景';
  independent: boolean;
};

const evidence: Evidence[] = [
  {
    id: 'E-019',
    source: '国家认证信息平台',
    kind: '一手登记',
    cluster: 'C-01',
    stance: '反驳',
    independent: true,
  },
  {
    id: 'E-027',
    source: '品牌官网 / awards',
    kind: '自有页面',
    cluster: 'C-02',
    stance: '支持',
    independent: true,
  },
  {
    id: 'E-031',
    source: '行业观察转载',
    kind: '媒体转载',
    cluster: 'C-07',
    stance: '支持',
    independent: false,
  },
  {
    id: 'E-044',
    source: '区域代理商文章',
    kind: '近重复',
    cluster: 'C-07',
    stance: '支持',
    independent: false,
  },
];

export function projectLiveSourceRows(
  liveTarget: Pick<LiveInvestigationTarget, 'evidenceMatrix' | 'sourceIndependence'>,
): Evidence[] {
  return liveTarget.evidenceMatrix.map((item) => {
    const source = liveTarget.sourceIndependence.find(
      (candidate) => candidate.sourceId === item.evidenceId,
    );
    return {
      id: item.id,
      source: item.evidenceId,
      kind: item.rationale,
      cluster: source?.cluster ?? item.cluster,
      stance:
        item.relation === 'supports' ? '支持' : item.relation === 'contradicts' ? '反驳' : '背景',
      independent: (source?.weight ?? item.weight ?? 0) >= 0.75,
    };
  });
}

export function liveGraphEdgeIdentity(
  edge: Pick<LiveInvestigationTarget['graph'][number], 'from' | 'relation' | 'to'>,
): string {
  return `live-edge:${edge.from}:${edge.relation}:${edge.to}`;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export function projectLiveInvestigation(
  value: unknown,
  expectedInvestigationPubId?: string,
): LiveInvestigationTarget | null {
  if (!isRecord(value)) return null;
  const projectionNotices: ProjectionNotices = {};
  const invalidProjection = new Set<InvestigationProjectionCollection>();
  const investigationPubId =
    typeof value.pub_id === 'string' &&
    /^inv_[A-Za-z0-9_-]{1,116}$/.test(value.pub_id) &&
    !containsClientSecret(value.pub_id)
      ? value.pub_id
      : '';
  if (
    !investigationPubId ||
    (expectedInvestigationPubId !== undefined && investigationPubId !== expectedInvestigationPubId)
  ) {
    return null;
  }
  const safeRatio = (candidate: unknown): number | null => {
    const normalized =
      typeof candidate === 'number'
        ? candidate
        : typeof candidate === 'string' &&
            candidate.length <= 24 &&
            /^(?:0(?:\.\d+)?|1(?:\.0+)?)$/.test(candidate)
          ? Number(candidate)
          : Number.NaN;
    return Number.isFinite(normalized) && normalized >= 0 && normalized <= 1 ? normalized : null;
  };
  const safeText = (candidate: unknown, fallback: string, max = 1000): string =>
    typeof candidate === 'string' && candidate.length <= max && !containsClientSecret(candidate)
      ? candidate
      : fallback;
  const safeId = (candidate: unknown): string => {
    const projected = safeText(candidate, '', 120);
    return /^[A-Za-z0-9_-]+$/.test(projected) ? projected : '';
  };
  const safePrefixedId = (candidate: unknown, prefix: string): string => {
    const projected = safeId(candidate);
    return projected.startsWith(prefix) ? projected : '';
  };
  const safeResourceId = (candidate: unknown): string => {
    const projected = safeId(candidate);
    return /^[A-Za-z][A-Za-z0-9_-]{2,119}$/.test(projected) ? projected : '';
  };
  const projectExplanation = (candidate: unknown): string[] => {
    const values = Array.isArray(candidate)
      ? candidate
      : isRecord(candidate)
        ? Object.values(candidate)
        : [];
    if (!Array.isArray(candidate) && !isRecord(candidate)) invalidProjection.add('explanations');
    const projected = values
      .slice(0, investigationProjectionLimits.explanations)
      .flatMap((item) => {
        const value = safeText(item, '', 500);
        if (!value) invalidProjection.add('explanations');
        return value ? [value] : [];
      });
    if (!values.length) invalidProjection.add('explanations');
    if (values.length > investigationProjectionLimits.explanations) {
      projectionNotices.explanations = {
        total: values.length,
        shown: projected.length,
      };
    }
    return projected;
  };
  const projectRecords = <T,>(
    candidate: unknown,
    collection: Exclude<
      InvestigationProjectionCollection,
      'explanations' | 'historyPages' | 'historyDiffs'
    >,
    project: (record: Record<string, unknown>) => T | null,
    direction: 'head' | 'tail' = 'head',
  ): T[] => {
    const values = Array.isArray(candidate) ? candidate : [];
    if (!Array.isArray(candidate)) invalidProjection.add(collection);
    const limit = investigationProjectionLimits[collection];
    const bounded = direction === 'tail' ? values.slice(-limit) : values.slice(0, limit);
    const projected = bounded.flatMap((item) => {
      if (!isRecord(item)) {
        invalidProjection.add(collection);
        return [];
      }
      const value = project(item);
      if (value === null) invalidProjection.add(collection);
      return value ? [value] : [];
    });
    if (values.length > limit) {
      projectionNotices[collection] = {
        total: values.length,
        shown: projected.length,
      };
    }
    return projected;
  };
  const seenScorePubIds = new Set<string>();
  let previousScoreCreatedAt = Number.NEGATIVE_INFINITY;
  const scores = projectRecords(
    value.scores,
    'scores',
    (score) => {
      const pubId = safePrefixedId(score.pub_id, 'score_');
      const probability = safeRatio(score.probability);
      const evidenceSufficiency = safeRatio(score.evidence_sufficiency);
      const uncertainty = safeRatio(score.uncertainty);
      const ruleVersion = safeText(score.rule_version, '', 120);
      const createdAt = projectSafeIsoTimestamp(score.created_at);
      const createdAtEpoch = createdAt ? new Date(createdAt).getTime() : Number.NaN;
      if (
        !pubId ||
        probability === null ||
        evidenceSufficiency === null ||
        uncertainty === null ||
        !ruleVersion ||
        !createdAt ||
        !Number.isFinite(createdAtEpoch) ||
        seenScorePubIds.has(pubId) ||
        createdAtEpoch < previousScoreCreatedAt
      ) {
        return null;
      }
      seenScorePubIds.add(pubId);
      previousScoreCreatedAt = createdAtEpoch;
      return {
        pubId,
        probability,
        evidenceSufficiency,
        uncertainty,
        ruleVersion,
        explanation: score.explanation,
        createdAt,
      };
    },
    'tail',
  );
  const latestScore = scores.at(-1);
  if (!latestScore) invalidProjection.add('scores');
  const seenAppealPubIds = new Set<string>();
  let previousAppealCreatedAt = Number.NEGATIVE_INFINITY;
  const projectedAppeals = projectRecords(
    value.appeals,
    'appeals',
    (appeal) => {
      const id = safePrefixedId(appeal.pub_id, 'apl_');
      const submittedByPubId = safePrefixedId(appeal.submitted_by_pub_id, 'usr_');
      const reason = safeText(appeal.reason, '', 10_000);
      const state = safeText(appeal.state, '', 40);
      const resolution =
        appeal.resolution === null ? null : safeText(appeal.resolution, '', 40) || undefined;
      const resolvedByPubId =
        appeal.resolved_by_pub_id === null
          ? null
          : safePrefixedId(appeal.resolved_by_pub_id, 'usr_') || undefined;
      const resolutionRationale =
        appeal.resolution_rationale === null
          ? null
          : safeText(appeal.resolution_rationale, '', 10_000) || undefined;
      const createdAt = projectSafeIsoTimestamp(appeal.created_at);
      const updatedAt = projectSafeIsoTimestamp(appeal.updated_at);
      const resolvedAt =
        appeal.resolved_at === null
          ? null
          : (projectSafeIsoTimestamp(appeal.resolved_at) ?? undefined);
      const createdAtEpoch = createdAt ? new Date(createdAt).getTime() : Number.NaN;
      const updatedAtEpoch = updatedAt ? new Date(updatedAt).getTime() : Number.NaN;
      const resolvedAtEpoch =
        typeof resolvedAt === 'string' ? new Date(resolvedAt).getTime() : Number.NaN;
      const active = state === 'open' || state === 'reviewing';
      const transactionIsConsistent =
        (active &&
          resolution === null &&
          resolvedByPubId === null &&
          resolutionRationale === null &&
          resolvedAt === null) ||
        (!active &&
          resolution === state &&
          typeof resolvedByPubId === 'string' &&
          resolvedByPubId !== submittedByPubId &&
          typeof resolutionRationale === 'string' &&
          resolutionRationale.trim().length > 0 &&
          typeof resolvedAt === 'string');
      if (
        !id ||
        !submittedByPubId ||
        !reason ||
        reason.trim().length === 0 ||
        !['open', 'reviewing', 'upheld', 'corrected', 'rejected'].includes(state) ||
        resolution === undefined ||
        resolvedByPubId === undefined ||
        resolutionRationale === undefined ||
        !createdAt ||
        !updatedAt ||
        !Number.isFinite(createdAtEpoch) ||
        !Number.isFinite(updatedAtEpoch) ||
        updatedAtEpoch < createdAtEpoch ||
        seenAppealPubIds.has(id) ||
        createdAtEpoch < previousAppealCreatedAt ||
        !transactionIsConsistent ||
        (!active &&
          (typeof resolvedAt !== 'string' ||
            !Number.isFinite(resolvedAtEpoch) ||
            resolvedAtEpoch < createdAtEpoch ||
            updatedAtEpoch < resolvedAtEpoch))
      ) {
        return null;
      }
      seenAppealPubIds.add(id);
      previousAppealCreatedAt = createdAtEpoch;
      return {
        id,
        state,
        resolvedByPubId,
        resolutionRationale,
        createdAt,
        updatedAt,
        resolvedAt,
      };
    },
    'tail',
  );
  const seenVerdictPubIds = new Set<string>();
  let previousVerdictCreatedAt = Number.NEGATIVE_INFINITY;
  let previousVerdictPubId = '';
  let verdictChainIsValid = true;
  const verdicts = projectRecords(
    value.verdicts,
    'verdicts',
    (verdict) => {
      const id = safePrefixedId(verdict.pub_id, 'vrd_');
      const reviewerPubId = safePrefixedId(verdict.reviewer_pub_id, 'usr_');
      const rationale = safeText(verdict.rationale, '', 10_000);
      const decision = safeText(verdict.verdict, '', 40);
      const supersedesPubId =
        verdict.supersedes_pub_id === null ? '' : safePrefixedId(verdict.supersedes_pub_id, 'vrd_');
      const createdAt = projectSafeIsoTimestamp(verdict.created_at);
      const createdAtEpoch = createdAt ? new Date(createdAt).getTime() : Number.NaN;
      if (
        !id ||
        !reviewerPubId ||
        !rationale ||
        rationale.trim().length === 0 ||
        !['likely', 'unlikely', 'uncertain', 'insufficient'].includes(decision) ||
        (verdict.supersedes_pub_id !== null && !supersedesPubId) ||
        !createdAt ||
        !Number.isFinite(createdAtEpoch) ||
        seenVerdictPubIds.has(id) ||
        createdAtEpoch < previousVerdictCreatedAt ||
        (Boolean(supersedesPubId) && supersedesPubId !== previousVerdictPubId) ||
        !verdictChainIsValid
      ) {
        verdictChainIsValid = false;
        return null;
      }
      seenVerdictPubIds.add(id);
      previousVerdictCreatedAt = createdAtEpoch;
      previousVerdictPubId = id;
      return { id, verdict: decision, reviewerPubId, rationale, supersedesPubId, createdAt };
    },
    'tail',
  );
  const matchedReplacementVerdicts = new Set<string>();
  const appeals = projectedAppeals.filter((appeal) => {
    const appealCreatedAt = new Date(appeal.createdAt).getTime();
    const priorVerdict = verdicts
      .filter((verdict) => new Date(verdict.createdAt).getTime() <= appealCreatedAt)
      .at(-1);
    if (!priorVerdict) return false;
    if (appeal.state === 'open' || appeal.state === 'reviewing') return true;
    const resolvedAt =
      typeof appeal.resolvedAt === 'string' ? new Date(appeal.resolvedAt).getTime() : Number.NaN;
    if (
      !Number.isFinite(resolvedAt) ||
      typeof appeal.resolvedByPubId !== 'string' ||
      typeof appeal.resolutionRationale !== 'string'
    ) {
      return false;
    }
    if (appeal.state !== 'corrected') {
      const verdictAtResolution = verdicts
        .filter((verdict) => new Date(verdict.createdAt).getTime() <= resolvedAt)
        .at(-1);
      return Boolean(
        verdictAtResolution && verdictAtResolution.reviewerPubId !== appeal.resolvedByPubId,
      );
    }
    const replacementIndex = verdicts.findIndex(
      (verdict) =>
        Boolean(verdict.supersedesPubId) &&
        !matchedReplacementVerdicts.has(verdict.id) &&
        new Date(verdict.createdAt).getTime() >= appealCreatedAt &&
        new Date(verdict.createdAt).getTime() <= resolvedAt &&
        verdict.reviewerPubId === appeal.resolvedByPubId &&
        verdict.rationale === appeal.resolutionRationale,
    );
    const replacement = replacementIndex >= 0 ? verdicts[replacementIndex] : undefined;
    const replacedVerdict = replacementIndex > 0 ? verdicts[replacementIndex - 1] : undefined;
    if (
      !replacement ||
      !replacedVerdict ||
      replacement.supersedesPubId !== replacedVerdict.id ||
      replacedVerdict.reviewerPubId === appeal.resolvedByPubId
    ) {
      return false;
    }
    matchedReplacementVerdicts.add(replacement.id);
    return true;
  });
  if (appeals.length !== projectedAppeals.length) invalidProjection.add('appeals');
  const seenClaimPubIds = new Set<string>();
  const claims = projectRecords(value.claims, 'claims', (claim) => {
    const id = safePrefixedId(claim.pub_id, 'clm_');
    const text = safeText(claim.normalized_text, '', 1000);
    const verifiability = safeText(claim.verifiability, '', 80);
    if (!text || !verifiability) invalidProjection.add('claims');
    if (!id || seenClaimPubIds.has(id)) return null;
    seenClaimPubIds.add(id);
    return {
      id,
      text: text || 'Claim 内容已隐藏',
      verifiability: verifiability || 'unknown',
    };
  });
  const claimPubIds = new Set(claims.map((claim) => claim.id));
  const seenClaimEvidencePubIds = new Set<string>();
  const seenClaimEvidencePairs = new Set<string>();
  const evidenceMatrix = projectRecords(value.evidence_matrix, 'evidenceMatrix', (item) => {
    const id = safePrefixedId(item.pub_id, 'ce_');
    const claimId = safePrefixedId(item.claim_pub_id, 'clm_');
    const evidenceId = safePrefixedId(item.evidence_pub_id, 'evd_');
    const relation = safeText(item.relation, '', 80);
    const cluster = safeText(item.source_cluster, '', 120);
    const rationale = safeText(item.rationale, '', 1000);
    const weight = safeRatio(item.independence_weight);
    const pair = claimId && evidenceId ? `${claimId}\u0000${evidenceId}` : '';
    if (
      !id ||
      !claimPubIds.has(claimId) ||
      !evidenceId ||
      !['supports', 'contradicts', 'insufficient'].includes(relation) ||
      !cluster ||
      !rationale ||
      weight === null ||
      seenClaimEvidencePubIds.has(id) ||
      seenClaimEvidencePairs.has(pair)
    ) {
      return null;
    }
    seenClaimEvidencePubIds.add(id);
    seenClaimEvidencePairs.add(pair);
    return {
      id,
      claimId,
      evidenceId,
      relation,
      cluster,
      weight,
      rationale,
    };
  });
  const seenSourceAssessmentPubIds = new Set<string>();
  const seenAssessedSourcePubIds = new Set<string>();
  const sourceIndependence = projectRecords(
    value.source_independence,
    'sourceIndependence',
    (source) => {
      const id = safePrefixedId(source.pub_id, 'srca_');
      const sourceId = safeId(source.source_pub_id);
      const cluster = safeText(source.cluster_id, '', 120);
      const weight = safeRatio(source.independence_weight);
      const circularRisk = safeRatio(source.circular_citation_risk);
      if (
        !id ||
        !sourceId ||
        !cluster ||
        weight === null ||
        circularRisk === null ||
        seenSourceAssessmentPubIds.has(id) ||
        seenAssessedSourcePubIds.has(sourceId)
      ) {
        return null;
      }
      seenSourceAssessmentPubIds.add(id);
      seenAssessedSourcePubIds.add(sourceId);
      return {
        id,
        sourceId,
        cluster,
        weight,
        circularRisk,
      };
    },
  );
  const seenGraphEdges = new Set<string>();
  const graph = projectRecords(value.graph, 'graph', (edge) => {
    const from = safeResourceId(edge.from_pub_id);
    const to = safeResourceId(edge.to_pub_id);
    const relation = safeText(edge.relation, '', 80);
    const allowedRelations = [
      'supports',
      'contradicts',
      'insufficient',
      'derived_from',
      'near_duplicate',
      'published_by',
      'cites',
      'mentions',
    ];
    const weight = edge.weight === null ? null : safeRatio(edge.weight);
    const evidenceId =
      edge.evidence_pub_id === null ? '' : safePrefixedId(edge.evidence_pub_id, 'evd_');
    const edgeKey = from && to && relation ? `${from}\u0000${to}\u0000${relation}` : '';
    if (
      !from ||
      !to ||
      !allowedRelations.includes(relation) ||
      (edge.weight !== null && weight === null) ||
      (edge.evidence_pub_id !== null && !evidenceId) ||
      !edgeKey ||
      seenGraphEdges.has(edgeKey)
    ) {
      return null;
    }
    seenGraphEdges.add(edgeKey);
    return {
      from,
      to,
      relation,
      weight,
      evidenceId,
    };
  });
  const probability = latestScore?.probability ?? null;
  const evidenceSufficiency = latestScore?.evidenceSufficiency ?? null;
  const uncertainty = latestScore?.uncertainty ?? null;
  const ruleVersion = latestScore?.ruleVersion ?? '';
  if (probability === null || evidenceSufficiency === null || uncertainty === null || !ruleVersion)
    invalidProjection.add('scores');
  const explanations = projectExplanation(latestScore?.explanation);
  const boundaryProjection = isRecord(value.projection) ? value.projection : null;
  const boundaryShown: Record<InvestigationProjectionCollection, number> = {
    scores: scores.length,
    explanations: explanations.length,
    claims: claims.length,
    evidenceMatrix: evidenceMatrix.length,
    sourceIndependence: sourceIndependence.length,
    graph: graph.length,
    appeals: appeals.length,
    verdicts: verdicts.length,
    historyPages: 0,
    historyDiffs: 0,
  };
  if (boundaryProjection) {
    (
      [
        'scores',
        'explanations',
        'claims',
        'evidenceMatrix',
        'sourceIndependence',
        'graph',
        'appeals',
        'verdicts',
      ] as const
    ).forEach((collection) => {
      const metadata = boundaryProjection[collection];
      if (!isRecord(metadata)) {
        invalidProjection.add(collection);
        return;
      }
      const total =
        typeof metadata.total === 'number' &&
        Number.isSafeInteger(metadata.total) &&
        metadata.total >= 0
          ? metadata.total
          : null;
      const shown =
        typeof metadata.shown === 'number' &&
        Number.isSafeInteger(metadata.shown) &&
        metadata.shown >= 0
          ? metadata.shown
          : null;
      if (
        total === null ||
        shown === null ||
        shown > total ||
        typeof metadata.invalid !== 'boolean'
      ) {
        invalidProjection.add(collection);
        return;
      }
      if (total !== boundaryShown[collection]) {
        projectionNotices[collection] = {
          total,
          shown: boundaryShown[collection],
        };
      }
      if (metadata.invalid || shown !== boundaryShown[collection]) {
        invalidProjection.add(collection);
      }
    });
  }
  const governanceIncomplete = Boolean(
    projectionNotices.appeals ||
      projectionNotices.verdicts ||
      invalidProjection.has('appeals') ||
      invalidProjection.has('verdicts'),
  );
  const openAppealPubId = governanceIncomplete
    ? ''
    : (appeals.find((appeal) => ['open', 'reviewing'].includes(appeal.state))?.id ?? '');
  const latestVerdict = governanceIncomplete ? undefined : verdicts.at(-1);
  const verdictState: Verdict = governanceIncomplete
    ? 'pending'
    : openAppealPubId
      ? 'appealed'
      : latestVerdict?.verdict === 'likely'
        ? 'confirmed'
        : latestVerdict?.verdict === 'unlikely' || latestVerdict?.verdict === 'insufficient'
          ? 'rejected'
          : 'pending';
  return investigationPubId
    ? {
        investigationPubId,
        probability,
        evidenceSufficiency,
        uncertainty,
        ruleVersion: ruleVersion || '规则版本未提供',
        explanations,
        claims,
        evidenceMatrix,
        sourceIndependence,
        graph,
        appealStates: appeals.map(({ id, state }) => ({ id, state })),
        verdictPubIds: verdicts.map(({ id }) => id),
        openAppealPubId,
        verdictState,
        projectionNotices,
        invalidProjection: [...invalidProjection],
      }
    : null;
}

export function projectLiveHistory(history: unknown, diffs: unknown): LiveHistoryTarget {
  const projectionNotices: ProjectionNotices = {};
  const invalidProjection = new Set<InvestigationProjectionCollection>();
  const safeId = (value: unknown): string =>
    typeof value === 'string' &&
    value.length <= 120 &&
    /^[A-Za-z0-9_-]+$/.test(value) &&
    !containsClientSecret(value)
      ? value
      : '';
  const safePrefixedId = (value: unknown, prefix: string): string => {
    const projected = safeId(value);
    return projected.startsWith(prefix) ? projected : '';
  };
  const safeHash = (value: unknown): string =>
    typeof value === 'string' && /^[0-9a-f]{64}$/.test(value) ? value : '';
  const safePositiveInteger = (value: unknown): number | null =>
    typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : null;
  const safeTimestamp = projectSafeIsoTimestamp;
  const historyValues = Array.isArray(history)
    ? history
    : isRecord(history) && Array.isArray(history.data)
      ? history.data
      : [];
  const diffValues = Array.isArray(diffs)
    ? diffs
    : isRecord(diffs) && Array.isArray(diffs.data)
      ? diffs.data
      : [];
  const seenVersionPubIds = new Set<string>();
  const seenContentVersions = new Set<string>();
  const projectedPageRows = historyValues
    .slice(-investigationProjectionLimits.historyPages)
    .flatMap((item) => {
      if (!isRecord(item)) {
        invalidProjection.add('historyPages');
        return [];
      }
      const contentPubId = safePrefixedId(item.content_pub_id, 'cnt_');
      const versionPubId = safePrefixedId(item.version_pub_id, 'cntv_');
      const evidencePubId =
        item.evidence_pub_id === null ? null : safePrefixedId(item.evidence_pub_id, 'evd_');
      const bodyHash = safeHash(item.body_hash);
      const versionNumber = safePositiveInteger(item.version_number);
      const capturedAt = safeTimestamp(item.captured_at);
      let source = '来源已隐藏';
      try {
        if (typeof item.canonical_url !== 'string' || containsClientSecret(item.canonical_url))
          invalidProjection.add('historyPages');
        const parsed = new URL(typeof item.canonical_url === 'string' ? item.canonical_url : '');
        if (
          ['https:', 'http:'].includes(parsed.protocol) &&
          parsed.hostname.length <= 253 &&
          !containsClientSecret(parsed.hostname)
        )
          source = parsed.hostname;
      } catch {
        source = '来源已隐藏';
      }
      const titleIsSafe =
        item.title === null ||
        (typeof item.title === 'string' &&
          item.title.length <= 300 &&
          !containsClientSecret(item.title));
      if (!titleIsSafe) invalidProjection.add('historyPages');
      const title = typeof item.title === 'string' && titleIsSafe ? item.title : '无标题页面';
      const contentVersionKey =
        contentPubId && versionNumber !== null ? `${contentPubId}\u0000${versionNumber}` : '';
      if (
        !contentPubId ||
        !versionPubId ||
        evidencePubId === '' ||
        !bodyHash ||
        versionNumber === null ||
        !capturedAt ||
        seenVersionPubIds.has(versionPubId) ||
        seenContentVersions.has(contentVersionKey)
      ) {
        invalidProjection.add('historyPages');
        return [];
      }
      seenVersionPubIds.add(versionPubId);
      seenContentVersions.add(contentVersionKey);
      return [
        {
          contentPubId,
          versionPubId,
          evidencePubId,
          versionNumber,
          title,
          source,
          capturedAt,
          snapshotNumber: safePositiveInteger(item.snapshot_number),
          bodyHash,
        },
      ];
    });
  const pages = projectedPageRows.map(
    ({
      contentPubId,
      versionPubId,
      versionNumber,
      title,
      source,
      capturedAt,
      snapshotNumber,
      bodyHash,
    }) => ({
      contentPubId,
      versionPubId,
      versionNumber,
      title,
      source,
      capturedAt,
      snapshotNumber,
      bodyHash,
    }),
  );
  if (historyValues.length > investigationProjectionLimits.historyPages) {
    projectionNotices.historyPages = {
      total: historyValues.length,
      shown: pages.length,
    };
  }
  const seenDiffPubIds = new Set<string>();
  const projectedDiffRows = diffValues
    .slice(-investigationProjectionLimits.historyDiffs)
    .flatMap((item) => {
      if (!isRecord(item)) {
        invalidProjection.add('historyDiffs');
        return [];
      }
      const pubId = safePrefixedId(item.pub_id, 'diff_');
      const contentPubId = safePrefixedId(item.content_pub_id, 'cnt_');
      const beforeVersionPubId = safePrefixedId(item.before_version_pub_id, 'cntv_');
      const afterVersionPubId = safePrefixedId(item.after_version_pub_id, 'cntv_');
      const beforeEvidencePubId = safePrefixedId(item.before_evidence_pub_id, 'evd_');
      const afterEvidencePubId = safePrefixedId(item.after_evidence_pub_id, 'evd_');
      const createdAt = safeTimestamp(item.created_at);
      const beforeHash = isRecord(item.text_diff) ? safeHash(item.text_diff.before_hash) : '';
      const afterHash = isRecord(item.text_diff) ? safeHash(item.text_diff.after_hash) : '';
      const textDiffIsValid =
        item.text_diff === null ||
        (isRecord(item.text_diff) && beforeHash !== '' && afterHash !== '');
      const similarity = (() => {
        if (item.similarity === null) return null;
        const normalized =
          typeof item.similarity === 'number'
            ? item.similarity
            : typeof item.similarity === 'string' &&
                item.similarity.length <= 24 &&
                /^(?:0(?:\.\d+)?|1(?:\.0+)?)$/.test(item.similarity)
              ? Number(item.similarity)
              : Number.NaN;
        if (!Number.isFinite(normalized) || normalized < 0 || normalized > 1) {
          invalidProjection.add('historyDiffs');
          return null;
        }
        return normalized;
      })();
      if (
        !pubId ||
        !contentPubId ||
        !beforeVersionPubId ||
        !afterVersionPubId ||
        !beforeEvidencePubId ||
        !afterEvidencePubId ||
        !createdAt ||
        !textDiffIsValid ||
        typeof item.visual_diff_available !== 'boolean' ||
        seenDiffPubIds.has(pubId)
      ) {
        invalidProjection.add('historyDiffs');
        return [];
      }
      seenDiffPubIds.add(pubId);
      return [
        {
          pubId,
          contentPubId,
          beforeVersionPubId,
          afterVersionPubId,
          beforeEvidencePubId,
          afterEvidencePubId,
          createdAt,
          similarity,
          visualDiffAvailable: item.visual_diff_available,
          beforeHash,
          afterHash,
          textDiffIsNull: item.text_diff === null,
        },
      ];
    });
  const pageByVersionPubId = new Map(
    projectedPageRows.map((page) => [page.versionPubId, page] as const),
  );
  const projectedDiffs = projectedDiffRows.flatMap((diff) => {
    const before = pageByVersionPubId.get(diff.beforeVersionPubId);
    const after = pageByVersionPubId.get(diff.afterVersionPubId);
    if (
      !before ||
      !after ||
      before.contentPubId !== diff.contentPubId ||
      after.contentPubId !== diff.contentPubId ||
      before.evidencePubId !== diff.beforeEvidencePubId ||
      after.evidencePubId !== diff.afterEvidencePubId ||
      before.versionNumber >= after.versionNumber ||
      new Date(before.capturedAt).getTime() > new Date(after.capturedAt).getTime() ||
      new Date(diff.createdAt).getTime() < new Date(after.capturedAt).getTime() ||
      (!diff.textDiffIsNull &&
        (diff.beforeHash !== before.bodyHash || diff.afterHash !== after.bodyHash))
    ) {
      invalidProjection.add('historyDiffs');
      return [];
    }
    return [
      {
        pubId: diff.pubId,
        contentPubId: diff.contentPubId,
        beforeVersionPubId: diff.beforeVersionPubId,
        afterVersionPubId: diff.afterVersionPubId,
        similarity: diff.similarity,
        visualDiffAvailable: diff.visualDiffAvailable,
        beforeHash: diff.beforeHash,
        afterHash: diff.afterHash,
      },
    ];
  });
  if (diffValues.length > investigationProjectionLimits.historyDiffs) {
    projectionNotices.historyDiffs = {
      total: diffValues.length,
      shown: projectedDiffs.length,
    };
  }
  if (!Array.isArray(history)) {
    const boundary = isRecord(history) && isRecord(history.projection) ? history.projection : null;
    const total =
      boundary && typeof boundary.total === 'number' && Number.isSafeInteger(boundary.total)
        ? boundary.total
        : null;
    const shown =
      boundary && typeof boundary.shown === 'number' && Number.isSafeInteger(boundary.shown)
        ? boundary.shown
        : null;
    if (total === null || shown === null || typeof boundary?.invalid !== 'boolean') {
      invalidProjection.add('historyPages');
    } else {
      if (total !== pages.length) {
        projectionNotices.historyPages = {
          total,
          shown: pages.length,
        };
      }
      if (boundary.invalid || shown !== pages.length) {
        invalidProjection.add('historyPages');
      }
    }
  }
  if (!Array.isArray(diffs)) {
    const boundary = isRecord(diffs) && isRecord(diffs.projection) ? diffs.projection : null;
    const total =
      boundary && typeof boundary.total === 'number' && Number.isSafeInteger(boundary.total)
        ? boundary.total
        : null;
    const shown =
      boundary && typeof boundary.shown === 'number' && Number.isSafeInteger(boundary.shown)
        ? boundary.shown
        : null;
    if (total === null || shown === null || typeof boundary?.invalid !== 'boolean') {
      invalidProjection.add('historyDiffs');
    } else {
      if (total !== projectedDiffs.length) {
        projectionNotices.historyDiffs = {
          total,
          shown: projectedDiffs.length,
        };
      }
      if (boundary.invalid || shown !== projectedDiffs.length) {
        invalidProjection.add('historyDiffs');
      }
    }
  }
  return {
    pages,
    diffs: projectedDiffs,
    projectionNotices,
    invalidProjection: [...invalidProjection],
  };
}

export function selectLiveHistoryView(liveHistory: LiveHistoryTarget, requestedContentPubId = '') {
  const latestPageByContent = new Map<string, LiveHistoryTarget['pages'][number]>();
  for (const page of liveHistory.pages) {
    const current = latestPageByContent.get(page.contentPubId);
    if (
      !current ||
      page.versionNumber > current.versionNumber ||
      (page.versionNumber === current.versionNumber &&
        new Date(page.capturedAt).getTime() > new Date(current.capturedAt).getTime())
    ) {
      latestPageByContent.set(page.contentPubId, page);
    }
  }
  const contentOptions = [...latestPageByContent.values()].sort(
    (left, right) =>
      new Date(right.capturedAt).getTime() - new Date(left.capturedAt).getTime() ||
      left.contentPubId.localeCompare(right.contentPubId),
  );
  const activeContentPubId = latestPageByContent.has(requestedContentPubId)
    ? requestedContentPubId
    : (contentOptions[0]?.contentPubId ?? '');
  const contentPages = liveHistory.pages
    .filter((page) => page.contentPubId === activeContentPubId)
    .sort(
      (left, right) =>
        left.versionNumber - right.versionNumber ||
        new Date(left.capturedAt).getTime() - new Date(right.capturedAt).getTime(),
    );
  const currentPage = contentPages.at(-1) ?? null;
  const previousPage = contentPages.at(-2) ?? null;
  const selectedDiff =
    currentPage && previousPage
      ? (liveHistory.diffs
          .filter(
            (diff) =>
              diff.contentPubId === activeContentPubId &&
              diff.beforeVersionPubId === previousPage.versionPubId &&
              diff.afterVersionPubId === currentPage.versionPubId,
          )
          .at(-1) ?? null)
      : null;
  return {
    contentOptions,
    activeContentPubId,
    currentPage,
    previousPage,
    selectedDiff,
  };
}

const projectionCollectionLabels: Record<InvestigationProjectionCollection, string> = {
  scores: '评分记录',
  explanations: '规则解释',
  claims: '原子 Claim',
  evidenceMatrix: '证据关系',
  sourceIndependence: '来源独立性',
  graph: '传播关系',
  appeals: '申诉记录',
  verdicts: '裁决记录',
  historyPages: '页面历史',
  historyDiffs: '视觉 Diff',
};

function InvestigationProjectionLimitNotice({
  notices,
  invalidProjection = [],
  collections,
}: {
  notices: ProjectionNotices;
  invalidProjection?: InvestigationProjectionCollection[];
  collections: InvestigationProjectionCollection[];
}) {
  const visible = collections.flatMap((collection) => {
    const notice = notices[collection];
    return notice ? [{ collection, ...notice }] : [];
  });
  const invalid = collections.filter((collection) => invalidProjection.includes(collection));
  return (
    <>
      {visible.length ? (
        <ProjectionLimitNotice
          items={visible.map(({ collection, total, shown }) => ({
            key: collection,
            label: projectionCollectionLabels[collection],
            total,
            shown,
          }))}
        />
      ) : null}
      {invalid.length ? (
        <div className="confirmation projection-limit-notice" role="alert">
          <Badge tone="warning">安全投影不完整</Badge>
          <span>
            {invalid.map((collection) => projectionCollectionLabels[collection]).join('、')}
            含未通过安全校验的数据；相关写操作已锁定，且不会把当前视图声称为完整记录。
          </span>
        </div>
      ) : null}
    </>
  );
}

const hasIncompleteInvestigationProjection = (
  target: LiveInvestigationTarget,
  collections: InvestigationProjectionCollection[],
) =>
  collections.some(
    (collection) =>
      Boolean(target.projectionNotices[collection]) ||
      target.invalidProjection.includes(collection),
  );

function CasesWorkspace({
  livePage,
  liveState,
  page,
  pageCount,
  onPageChange,
  onRetry,
}: {
  livePage: InvestigationPageProjection | null;
  liveState: 'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden';
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  onRetry: () => void;
}) {
  const [selected, setSelected] = useState('CASE-2407');
  const liveCases = livePage?.data ?? [];
  const pageProjection = livePage?.projection ?? null;
  const pageProjectionIncomplete = Boolean(
    pageProjection && (pageProjection.invalid || pageProjection.total !== pageProjection.shown),
  );
  if (liveState === 'loading') {
    return <StatePanel state="loading" />;
  }
  if (liveState === 'failed') {
    return <StatePanel state="failed" onRetry={onRetry} />;
  }
  if (liveState === 'forbidden') {
    return <StatePanel state="forbidden" />;
  }
  if (liveState === 'ready' && liveCases.length === 0 && !pageProjectionIncomplete) {
    return <StatePanel state="empty" />;
  }
  const selectedLiveCase =
    liveState === 'ready'
      ? (liveCases.find((item) => item.pub_id === selected) ?? liveCases[0] ?? null)
      : null;
  const effectiveSelected = selectedLiveCase?.pub_id ?? selected;
  const caseRows =
    liveState === 'ready'
      ? liveCases.map((item) => [
          item.pub_id,
          item.title,
          item.latest_verdict ?? item.state,
          item.probability ?? '—',
        ])
      : [
          ['CASE-2407', '认证表述跨页面传播', '人工复核', '高'],
          ['CASE-2406', '市场份额口径冲突', '证据补充中', '中'],
          ['CASE-2398', '产品发布日期历史变更', '已裁决', '低'],
        ];
  return (
    <>
      <MetricGrid
        metrics={[
          {
            label: liveState === 'ready' ? '本页案件' : '开放案件',
            value: liveState === 'ready' ? String(liveCases.length) : '4',
            detail: liveState === 'ready' ? '真实 investigations API' : '1 个需复核',
          },
          {
            label: '原子 Claim',
            value: liveState === 'ready' ? '—' : '24',
            detail: liveState === 'ready' ? '列表合同未提供聚合值' : '已验证 17',
          },
          {
            label: '独立来源',
            value: liveState === 'ready' ? '—' : '8',
            detail: liveState === 'ready' ? '需进入案件详情查看' : '3 个同源簇',
          },
          {
            label: '申诉时限',
            value: liveState === 'ready' ? '—' : '6 天',
            detail: liveState === 'ready' ? '列表合同未提供期限' : 'CASE-2407',
          },
        ]}
      />
      {pageProjection && pageProjection.total !== pageProjection.shown ? (
        <ProjectionLimitNotice
          items={[
            {
              key: 'investigation-catalog',
              label: '调查案件',
              total: pageProjection.total,
              shown: pageProjection.shown,
            },
          ]}
        />
      ) : null}
      {pageProjection?.invalid ? (
        <div className="confirmation projection-limit-notice" role="alert">
          <Badge tone="warning">安全投影不完整</Badge>
          <span>案件列表含未通过安全校验或分页契约的数据；当前页面不会被描述为完整队列。</span>
        </div>
      ) : null}
      {liveState === 'ready' && liveCases.length === 0 && pageProjectionIncomplete ? (
        <StatePanel state="failed" onRetry={onRetry} />
      ) : null}
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Investigation queue</span>
            <h2>调查案件</h2>
          </div>
          <Badge tone={liveState === 'ready' && !pageProjectionIncomplete ? 'positive' : 'warning'}>
            {liveState === 'ready'
              ? pageProjectionIncomplete
                ? 'live API · 安全子集'
                : 'live API'
              : 'contract fixture'}
          </Badge>
        </div>
        <div className="case-list" role="list">
          {caseRows.map(([id, title, status, risk]) => (
            <button
              key={id}
              role="listitem"
              className={effectiveSelected === id ? 'selected' : ''}
              onClick={() => setSelected(id!)}
            >
              <strong>{id}</strong>
              <span>{title}</span>
              <Badge tone={status === '已裁决' ? 'positive' : 'warning'}>{status}</Badge>
              <small>风险 {risk}</small>
            </button>
          ))}
        </div>
        <div className="case-summary">
          <div>
            <span className="overline">Selected case</span>
            <h3>
              {selectedLiveCase
                ? `${selectedLiveCase.pub_id} · ${selectedLiveCase.title}`
                : `${selected} · 认证表述跨页面传播`}
            </h3>
            <p>
              {selectedLiveCase
                ? '案件详情、Claim、多源证据和传播关系须从真实详情合同读取；概率只用于排序，不直接构成指控。'
                : '调查目标是判断“国家级认证”表述是否有可核验的一手依据，并追踪近重复内容的传播路径。概率只用于排序，不直接构成指控。'}
            </p>
          </div>
          {selectedLiveCase ? (
            <dl className="definition-grid">
              <div>
                <dt>Claim 数</dt>
                <dd>{selectedLiveCase.claim_count}</dd>
              </div>
              <div>
                <dt>独立来源簇</dt>
                <dd>{selectedLiveCase.source_cluster_count}</dd>
              </div>
              <div>
                <dt>概率</dt>
                <dd>
                  {selectedLiveCase.probability === null
                    ? '证据不足'
                    : selectedLiveCase.probability}
                </dd>
              </div>
            </dl>
          ) : (
            <dl className="definition-grid">
              <div>
                <dt>案件 owner</dt>
                <dd>调查员 · 林岚</dd>
              </div>
              <div>
                <dt>规则版本</dt>
                <dd>intelligence-v2.3</dd>
              </div>
              <div>
                <dt>证据窗口</dt>
                <dd>2026-05-01—07-21</dd>
              </div>
            </dl>
          )}
        </div>
      </section>
      {liveState === 'ready' ? (
        <Pagination
          label="案件目录分页"
          page={page}
          pageCount={pageCount}
          onPageChange={onPageChange}
        />
      ) : null}
    </>
  );
}

function ClaimsWorkspace({ liveTarget }: { liveTarget?: LiveInvestigationTarget | null }) {
  const [expanded, setExpanded] = useState('CL-01');
  const claims = liveTarget
    ? liveTarget.claims.map((claim) => {
        const relations = liveTarget.evidenceMatrix.filter((item) => item.claimId === claim.id);
        return {
          id: claim.id,
          text: claim.text,
          support: relations.filter((item) => item.relation === 'supports').length,
          oppose: relations.filter((item) => item.relation === 'contradicts').length,
          sufficiency:
            relations.length >= 2 && (liveTarget.evidenceSufficiency ?? 0) >= 0.6 ? '充分' : '不足',
          uncertainty: `可验证性 ${claim.verifiability}`,
        };
      })
    : [
        {
          id: 'CL-01',
          text: '产品已通过国家级认证',
          support: 3,
          oppose: 1,
          sufficiency: '不足',
          uncertainty: '登记主体名称可能存在别名',
        },
        {
          id: 'CL-02',
          text: '市场份额连续三年第一',
          support: 2,
          oppose: 1,
          sufficiency: '不足',
          uncertainty: '未披露市场范围与统计机构',
        },
        {
          id: 'CL-03',
          text: '支持私有化部署',
          support: 4,
          oppose: 0,
          sufficiency: '充分',
          uncertainty: '版本适用范围待确认',
        },
      ];
  return (
    <section className="panel">
      <span className="overline">Atomic claims</span>
      <h2>Claim × Evidence 矩阵</h2>
      <p className="panel-subtitle">
        复合表述已拆成可单独支持或反驳的原子 Claim；真实 0 与未采集严格区分。
      </p>
      {liveTarget ? <Badge tone="positive">真实 intelligence API</Badge> : null}
      {liveTarget ? (
        <InvestigationProjectionLimitNotice
          notices={liveTarget.projectionNotices}
          invalidProjection={liveTarget.invalidProjection}
          collections={['claims', 'evidenceMatrix']}
        />
      ) : null}
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Claim</th>
              <th>支持</th>
              <th>反驳</th>
              <th>充分度</th>
              <th>解释</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((claim) => (
              <tr key={claim.id}>
                <td>
                  <button
                    className="link-button"
                    aria-expanded={expanded === claim.id}
                    onClick={() => setExpanded(expanded === claim.id ? '' : claim.id)}
                  >
                    {claim.id} · {claim.text}
                  </button>
                </td>
                <td>{claim.support}</td>
                <td>{claim.oppose}</td>
                <td>
                  <Badge tone={claim.sufficiency === '充分' ? 'positive' : 'warning'}>
                    {claim.sufficiency}
                  </Badge>
                </td>
                <td>{claim.uncertainty}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {liveTarget && claims.length === 0 ? (
        <StatePanel
          state={
            hasIncompleteInvestigationProjection(liveTarget, ['claims', 'evidenceMatrix'])
              ? 'insufficient'
              : 'empty'
          }
        />
      ) : null}
      {expanded ? (
        <div className="rule-explanation">
          <Badge tone="info">规则解释</Badge>
          {liveTarget ? (
            <>
              <p>
                <strong>{expanded}</strong> · 规则版本 {liveTarget.ruleVersion} · 概率{' '}
                {liveTarget.probability?.toFixed(2) ?? '不可用'} · 证据充分度{' '}
                {liveTarget.evidenceSufficiency?.toFixed(2) ?? '不可用'}
              </p>
              {liveTarget.evidenceMatrix.some((item) => item.claimId === expanded) ? (
                <ul>
                  {liveTarget.evidenceMatrix
                    .filter((item) => item.claimId === expanded)
                    .map((item) => (
                      <li key={item.id}>
                        {item.evidenceId} · {item.relation} · {item.rationale}
                      </li>
                    ))}
                </ul>
              ) : (
                <StatePanel state="insufficient" />
              )}
            </>
          ) : (
            <p>
              <strong>{expanded}</strong> 的独立一手来源不足 2 个；同源转载只计一次，反证 E-019
              权重更高。当前概率 0.61，区间 0.43–0.76。
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}

function SourcesWorkspace({ liveTarget }: { liveTarget?: LiveInvestigationTarget | null }) {
  const [filter, setFilter] = useState('all');
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [verifiedEvidence, setVerifiedEvidence] = useState<Set<string>>(() => new Set());
  const sourceRows = liveTarget ? projectLiveSourceRows(liveTarget) : evidence;
  const rows = sourceRows.filter((item) => filter === 'all' || item.cluster === filter);
  const clusters = [...new Set(sourceRows.map((item) => item.cluster))];
  const verifySelected = () => {
    if (!selectedEvidence) return;
    setVerifiedEvidence((current) => new Set(current).add(selectedEvidence.id));
  };
  return (
    <>
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Source independence</span>
            <h2>多源证据与同源簇</h2>
          </div>
          <Badge tone="info">{rows.length} 条</Badge>
          {liveTarget ? <Badge tone="positive">真实 intelligence API</Badge> : null}
        </div>
        <div className="filter-bar">
          <label>
            同源簇
            <select
              aria-label="筛选同源簇"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            >
              <option value="all">全部</option>
              {clusters.map((cluster) => (
                <option key={cluster} value={cluster}>
                  {cluster}
                </option>
              ))}
            </select>
          </label>
        </div>
        {liveTarget ? (
          <InvestigationProjectionLimitNotice
            notices={liveTarget.projectionNotices}
            invalidProjection={liveTarget.invalidProjection}
            collections={['evidenceMatrix', 'sourceIndependence']}
          />
        ) : null}
        <div className="source-grid">
          {rows.map((item) => (
            <article key={item.id}>
              <div>
                <Badge
                  tone={
                    item.stance === '反驳'
                      ? 'danger'
                      : item.stance === '支持'
                        ? 'positive'
                        : 'neutral'
                  }
                >
                  {item.stance}
                </Badge>
                <Badge tone={item.independent ? 'info' : 'warning'}>
                  {item.independent ? '独立来源' : '同源传播'}
                </Badge>
                {verifiedEvidence.has(item.id) ? <Badge tone="positive">锚点已核验</Badge> : null}
              </div>
              <h3>{item.source}</h3>
              <p>
                {item.kind} · cluster {item.cluster}
              </p>
              <dl>
                <div>
                  <dt>采集</dt>
                  <dd>{liveTarget ? '详情合同未提供' : '2026-07-21 09:42'}</dd>
                </div>
                <div>
                  <dt>快照</dt>
                  <dd>{liveTarget ? '详情合同未提供' : 'sha256: 8bc…19f'}</dd>
                </div>
              </dl>
              <button
                className="button button-secondary"
                disabled={Boolean(liveTarget)}
                title={liveTarget ? '证据锚点与快照关系合同尚未提供' : undefined}
                onClick={() => setSelectedEvidence(item)}
              >
                检查证据锚点
              </button>
            </article>
          ))}
        </div>
        {rows.length === 0 ? <StatePanel state="empty" /> : null}
      </section>
      {selectedEvidence ? (
        <Dialog
          title={`${selectedEvidence.id} 证据锚点`}
          eyebrow="Frozen evidence"
          closeLabel="关闭对话框"
          onClose={() => setSelectedEvidence(null)}
        >
          <div
            className="screenshot-placeholder"
            role="img"
            aria-label={`${selectedEvidence.source}页面快照，锚点 bbox 84,176,310,42`}
          >
            <span>目标表述位于 bbox 84,176,310,42</span>
          </div>
          <dl className="definition-grid">
            <div>
              <dt>文本锚点</dt>
              <dd>字符 112–168</dd>
            </div>
            <div>
              <dt>页面版本</dt>
              <dd>sha256: 8bc…19f</dd>
            </div>
            <div>
              <dt>来源判定</dt>
              <dd>
                {selectedEvidence.independent ? '独立来源' : `同源簇 ${selectedEvidence.cluster}`}
              </dd>
            </div>
            <div>
              <dt>历史差异</dt>
              <dd>当前版本新增目标 claim；前版无此段落</dd>
            </div>
          </dl>
          {verifiedEvidence.has(selectedEvidence.id) ? (
            <div className="confirmation" role="status">
              <Badge tone="positive">锚点已核验</Badge>
              <span>核验事件绑定页面 hash，未改写原证据。</span>
            </div>
          ) : (
            <button className="button" onClick={verifySelected}>
              标记锚点已核验
            </button>
          )}
        </Dialog>
      ) : null}
    </>
  );
}

const graphNodes: Node[] = [
  { id: 'origin', position: { x: 20, y: 90 }, data: { label: '品牌页面 E-027' }, type: 'input' },
  { id: 'media', position: { x: 260, y: 20 }, data: { label: '行业观察 E-031' } },
  { id: 'agent', position: { x: 260, y: 165 }, data: { label: '代理商 E-044' } },
  { id: 'answer', position: { x: 520, y: 90 }, data: { label: 'AI 回答 A-108' }, type: 'output' },
];
const graphEdges: Edge[] = [
  { id: 'e1', source: 'origin', target: 'media', label: '近重复 0.91' },
  { id: 'e2', source: 'origin', target: 'agent', label: '近重复 0.87' },
  { id: 'e3', source: 'media', target: 'answer', label: '被引用' },
  { id: 'e4', source: 'agent', target: 'answer', label: '语义匹配' },
];

function LiveParallelGraphEdge({
  id,
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  label,
  data,
  pathOptions,
  style,
  markerStart,
  markerEnd,
}: EdgeProps) {
  const curvature = typeof pathOptions?.curvature === 'number' ? pathOptions.curvature : 0.25;
  const labelOffset = typeof data?.labelOffset === 'number' ? data.labelOffset : 0;
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature,
  });
  return (
    <BaseEdge
      id={id}
      path={path}
      label={label}
      labelX={labelX}
      labelY={labelY + labelOffset}
      labelShowBg
      labelBgPadding={[4, 2]}
      labelBgBorderRadius={4}
      labelBgStyle={{ fill: '#f8fbf9' }}
      labelStyle={{ fill: '#17322a', fontSize: 11 }}
      {...(style ? { style } : {})}
      {...(markerStart ? { markerStart } : {})}
      {...(markerEnd ? { markerEnd } : {})}
    />
  );
}

const liveGraphEdgeTypes = { liveParallel: LiveParallelGraphEdge };

export function projectLiveGraphEdges(graph: LiveInvestigationTarget['graph']): Edge[] {
  const pairTotals = new Map<string, number>();
  graph.forEach((edge) => {
    const pair = `${edge.from}\u0000${edge.to}`;
    pairTotals.set(pair, (pairTotals.get(pair) ?? 0) + 1);
  });
  const pairOrdinals = new Map<string, number>();
  return graph.map((edge) => {
    const pair = `${edge.from}\u0000${edge.to}`;
    const ordinal = pairOrdinals.get(pair) ?? 0;
    pairOrdinals.set(pair, ordinal + 1);
    const pairTotal = pairTotals.get(pair) ?? 1;
    const labelOffset = (ordinal - (pairTotal - 1) / 2) * 28;
    return {
      id: liveGraphEdgeIdentity(edge),
      type: 'liveParallel',
      source: edge.from,
      target: edge.to,
      label: `${edge.relation}${edge.weight === null ? '' : ` ${edge.weight.toFixed(2)}`}`,
      data: { labelOffset },
      ...(pairTotal > 1
        ? {
            pathOptions: {
              curvature: 0.2 + ordinal * 0.35,
            },
          }
        : {}),
    };
  });
}

function GraphWorkspace({ liveTarget }: { liveTarget?: LiveInvestigationTarget | null }) {
  const graphProjectionLimited = Boolean(
    liveTarget && hasIncompleteInvestigationProjection(liveTarget, ['graph']),
  );
  const compactGraph =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(max-width: 620px)').matches;
  const liveNodeIds = liveTarget
    ? [...new Set(liveTarget.graph.flatMap((edge) => [edge.from, edge.to]))]
    : [];
  const sparseCompactGraph = compactGraph && liveNodeIds.length <= 8;
  const nodes: Node[] = liveTarget
    ? liveNodeIds.map((id, index) => ({
        id,
        position: sparseCompactGraph
          ? { x: (index % 2) * 80 + 20, y: index * 115 + 20 }
          : compactGraph
            ? { x: (index % 2) * 160 + 20, y: Math.floor(index / 2) * 125 + 20 }
            : { x: (index % 3) * 240 + 20, y: Math.floor(index / 3) * 145 + 20 },
        data: { label: id },
      }))
    : graphNodes;
  const edges = liveTarget ? projectLiveGraphEdges(liveTarget.graph) : graphEdges;
  if (liveTarget && edges.length === 0) {
    return (
      <>
        <InvestigationProjectionLimitNotice
          notices={liveTarget.projectionNotices}
          invalidProjection={liveTarget.invalidProjection}
          collections={['graph']}
        />
        <StatePanel state={graphProjectionLimited ? 'insufficient' : 'empty'} />
      </>
    );
  }
  return (
    <div className="graph-layout">
      <section className="panel">
        <span className="overline">Propagation graph</span>
        <h2>内容传播关系</h2>
        <p className="panel-subtitle">边表示可解释的相似或引用关系，不代表主体之间存在组织关系。</p>
        {liveTarget ? <Badge tone="positive">真实 intelligence API</Badge> : null}
        {liveTarget ? (
          <InvestigationProjectionLimitNotice
            notices={liveTarget.projectionNotices}
            invalidProjection={liveTarget.invalidProjection}
            collections={['graph']}
          />
        ) : null}
        <div className="flow-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            edgeTypes={liveGraphEdgeTypes}
            fitView
            fitViewOptions={{
              padding: sparseCompactGraph ? 0.12 : compactGraph ? 0.24 : 0.16,
              maxZoom: sparseCompactGraph ? 1.1 : compactGraph ? 0.8 : 1.5,
            }}
            onlyRenderVisibleElements
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </section>
      <section className="panel">
        <h2>可访问表格替代</h2>
        <p className="panel-subtitle">与图中节点和边完全等价，可通过键盘和读屏访问。</p>
        <div
          className="table-scroll graph-table-scroll"
          tabIndex={0}
          aria-label="传播图关系表滚动区域"
        >
          <table className="data-table">
            <caption className="sr-only">传播图节点与关系</caption>
            <thead>
              <tr>
                <th>起点</th>
                <th>关系</th>
                <th>终点</th>
                <th>依据</th>
              </tr>
            </thead>
            <tbody>
              {liveTarget
                ? liveTarget.graph.map((edge) => (
                    <tr key={liveGraphEdgeIdentity(edge)}>
                      <td>{edge.from}</td>
                      <td>{edge.relation}</td>
                      <td>{edge.to}</td>
                      <td>
                        {edge.evidenceId || '无公开证据标识'}
                        {edge.weight === null ? '' : ` · ${edge.weight.toFixed(2)}`}
                      </td>
                    </tr>
                  ))
                : [
                    ['品牌页面 E-027', '近重复', '行业观察 E-031', '相似度 0.91'],
                    ['品牌页面 E-027', '近重复', '代理商 E-044', '相似度 0.87'],
                    ['行业观察 E-031', '被引用', 'AI 回答 A-108', 'citation #2'],
                    ['代理商 E-044', '语义匹配', 'AI 回答 A-108', '锚点 44–78'],
                  ].map(([from, relation, to, basis]) => (
                    <tr key={`${from}-${to}`}>
                      <td>{from}</td>
                      <td>{relation}</td>
                      <td>{to}</td>
                      <td>{basis}</td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function HistoryWorkspace({ liveHistory }: { liveHistory?: LiveHistoryTarget | null }) {
  const [version, setVersion] = useState<'current' | 'previous'>('current');
  const [contentPubId, setContentPubId] = useState('');
  if (liveHistory) {
    if (!liveHistory.pages.length)
      return (
        <>
          <InvestigationProjectionLimitNotice
            notices={liveHistory.projectionNotices}
            invalidProjection={liveHistory.invalidProjection}
            collections={['historyPages', 'historyDiffs']}
          />
          <StatePanel
            state={
              liveHistory.invalidProjection.length ||
              liveHistory.projectionNotices.historyPages ||
              liveHistory.projectionNotices.historyDiffs
                ? 'insufficient'
                : 'empty'
            }
          />
        </>
      );
    const historyView = selectLiveHistoryView(liveHistory, contentPubId);
    const selectedPage =
      version === 'previous' && historyView.previousPage
        ? historyView.previousPage
        : historyView.currentPage!;
    return (
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Immutable history · real API</span>
            <h2>页面历史与视觉 Diff</h2>
          </div>
          <div className="segmented">
            <button
              aria-pressed={version === 'previous' && Boolean(historyView.previousPage)}
              disabled={!historyView.previousPage}
              onClick={() => setVersion('previous')}
            >
              上一版本
            </button>
            <button aria-pressed={version === 'current'} onClick={() => setVersion('current')}>
              当前版本
            </button>
          </div>
        </div>
        {historyView.contentOptions.length > 1 ? (
          <label className="field">
            <span>历史页面</span>
            <select
              aria-label="选择历史页面"
              value={historyView.activeContentPubId}
              onChange={(event) => {
                setContentPubId(event.currentTarget.value);
                setVersion('current');
              }}
            >
              {historyView.contentOptions.map((page) => (
                <option key={page.contentPubId} value={page.contentPubId}>
                  {page.title}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <InvestigationProjectionLimitNotice
          notices={liveHistory.projectionNotices}
          invalidProjection={liveHistory.invalidProjection}
          collections={['historyPages', 'historyDiffs']}
        />
        <dl className="case-summary">
          <div>
            <dt>页面</dt>
            <dd>{selectedPage.title}</dd>
          </div>
          <div>
            <dt>来源</dt>
            <dd>{selectedPage.source}</dd>
          </div>
          <div>
            <dt>版本</dt>
            <dd>v{selectedPage.versionNumber}</dd>
          </div>
          <div>
            <dt>快照</dt>
            <dd>
              {selectedPage.snapshotNumber === null ? '未绑定' : `#${selectedPage.snapshotNumber}`}
            </dd>
          </div>
        </dl>
        <div className="page-mock">
          <h3>{selectedPage.title}</h3>
          <p>采集时间：{new Date(selectedPage.capturedAt).toLocaleString('zh-CN')}</p>
          <p>正文哈希：{selectedPage.bodyHash.slice(0, 16)}…</p>
        </div>
        {historyView.selectedDiff ? (
          <div className="diff-summary">
            <Badge tone={historyView.selectedDiff.visualDiffAvailable ? 'warning' : 'info'}>
              {historyView.selectedDiff.visualDiffAvailable
                ? '视觉 Diff 已冻结'
                : '文本 Diff 已冻结'}
            </Badge>
            <span>
              v{historyView.previousPage?.versionNumber ?? 1} → v
              {historyView.currentPage?.versionNumber ?? 1} · 相似度{' '}
              {historyView.selectedDiff.similarity === null
                ? '不可用'
                : `${(historyView.selectedDiff.similarity * 100).toFixed(1)}%`}
            </span>
          </div>
        ) : (
          <StatePanel state="insufficient" />
        )}
      </section>
    );
  }
  return (
    <section className="panel">
      <div className="account-head">
        <div>
          <span className="overline">Immutable history</span>
          <h2>页面历史与视觉 Diff</h2>
        </div>
        <div className="segmented">
          <button aria-pressed={version === 'previous'} onClick={() => setVersion('previous')}>
            07/02
          </button>
          <button aria-pressed={version === 'current'} onClick={() => setVersion('current')}>
            07/21
          </button>
        </div>
      </div>
      <div className="visual-diff">
        <article>
          <header>历史快照 · 2026-07-02</header>
          <div className="page-mock">
            <h3>资质与荣誉</h3>
            <p>荣获行业创新产品奖。</p>
          </div>
        </article>
        <article>
          <header>当前快照 · 2026-07-21</header>
          <div className="page-mock">
            <h3>资质与荣誉</h3>
            <p>荣获行业创新产品奖。</p>
            <mark>已通过国家级认证</mark>
          </div>
        </article>
      </div>
      <div className="diff-summary">
        <Badge tone="warning">新增区域</Badge>
        <span>bbox 84,176,310,42 · OCR “已通过国家级认证” · 感知哈希变化 18%</span>
      </div>
    </section>
  );
}

function VerdictWorkspace({
  verdict,
  setVerdict,
  liveTarget,
  capabilities,
  onReconcile,
}: {
  verdict: Verdict;
  setVerdict: (value: Verdict) => void;
  liveTarget?: LiveInvestigationTarget | null;
  capabilities: IntelligenceCapabilities;
  onReconcile?: (investigationPubId: string) => Promise<LiveInvestigationTarget | null>;
}) {
  const [writeState, setWriteState] = useState<'idle' | 'saving' | 'failed'>('idle');
  const [receipt, setReceipt] = useState('');
  const [appealPubId, setAppealPubId] = useState(liveTarget?.openAppealPubId ?? '');
  const [pendingReconciliation, setPendingReconciliation] =
    useState<GovernanceReconciliation | null>(null);
  const writeScope = liveTarget?.investigationPubId ?? 'fixture';
  const governanceWrite = useIntelligenceMutationGuard(`verdict:${writeScope}`);
  const {
    register,
    handleSubmit,
    formState: { errors, isValid },
  } = useForm<AppealReasonFields>({
    resolver: zodResolver(appealReasonSchema),
    defaultValues: { reason: '' },
    mode: 'onChange',
  });
  const governanceCollections: InvestigationProjectionCollection[] = [
    'scores',
    'explanations',
    'claims',
    'evidenceMatrix',
    'sourceIndependence',
    'graph',
    'appeals',
    'verdicts',
  ];
  const governanceIncomplete = liveTarget
    ? hasIncompleteInvestigationProjection(liveTarget, governanceCollections)
    : false;
  const reconciliationLocked = writeState === 'saving' || pendingReconciliation !== null;
  const projectionConfirms = (
    target: LiveInvestigationTarget,
    expected: GovernanceReconciliation,
  ): boolean => {
    if (expected.kind === 'verdict') {
      return (
        target.verdictPubIds.includes(expected.verdictPubId) &&
        target.verdictState === expected.verdict
      );
    }
    if (expected.kind === 'appeal') {
      return target.openAppealPubId === expected.appealPubId && target.verdictState === 'appealed';
    }
    return target.appealStates.some(
      (appeal) =>
        appeal.id === expected.appealPubId &&
        ['upheld', 'corrected', 'rejected'].includes(appeal.state),
    );
  };
  const reconcileGovernance = async (
    expected: GovernanceReconciliation,
    ticket: NonNullable<ReturnType<typeof governanceWrite.begin>>,
  ) => {
    if (!liveTarget || !onReconcile) return false;
    const target = await onReconcile(liveTarget.investigationPubId);
    if (!governanceWrite.isCurrent(ticket)) {
      governanceWrite.finish(ticket);
      return false;
    }
    if (!target || !projectionConfirms(target, expected)) {
      governanceWrite.finish(ticket);
      setPendingReconciliation(expected);
      setWriteState('failed');
      return false;
    }
    if (!governanceWrite.finish(ticket)) return false;
    setPendingReconciliation(null);
    setReceipt(expected.receipt);
    setWriteState('idle');
    if (expected.kind === 'appeal') setAppealPubId(expected.appealPubId);
    setVerdict(
      expected.kind === 'verdict'
        ? expected.verdict
        : expected.kind === 'appeal'
          ? 'appealed'
          : 'reviewed',
    );
    return true;
  };
  const retryReconciliation = async () => {
    if (!pendingReconciliation || !liveTarget || !onReconcile) return;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setWriteState('failed');
      return;
    }
    const ticket = governanceWrite.begin(headers);
    if (!ticket) return;
    setWriteState('saving');
    await reconcileGovernance(pendingReconciliation, ticket);
  };
  const decide = async (next: 'confirmed' | 'rejected') => {
    if (!capabilities.review || governanceIncomplete || pendingReconciliation) return;
    if (liveTarget) {
      const headers = getValidatedIdentityHeaders();
      if (!headers || !onReconcile) {
        setWriteState('failed');
        return;
      }
      const ticket = governanceWrite.begin(headers);
      if (!ticket) return;
      setReceipt('');
      setWriteState('saving');
      const result = await createInvestigationVerdict(
        liveTarget.investigationPubId,
        {
          verdict: next === 'confirmed' ? 'likely' : 'insufficient',
          rationale:
            next === 'confirmed'
              ? '人工复核确认当前证据支持高风险表述。'
              : '人工复核认为当前证据不足以支持该结论。',
          workflow_operation_id: null,
        },
        headers,
      );
      if (result.kind !== 'ready') {
        if (governanceWrite.finish(ticket)) setWriteState('failed');
        return;
      }
      if (!governanceWrite.isCurrent(ticket)) {
        governanceWrite.finish(ticket);
        return;
      }
      const expected: GovernanceReconciliation = {
        kind: 'verdict',
        verdictPubId: result.data.verdictPubId,
        verdict: next,
        receipt: '真实人工裁决已记录',
      };
      setPendingReconciliation(expected);
      await reconcileGovernance(expected, ticket);
      return;
    }
    setVerdict(next);
  };
  const appeal = handleSubmit(async ({ reason }) => {
    if (!capabilities.analyze || governanceIncomplete || pendingReconciliation) return;
    if (liveTarget) {
      const headers = getValidatedIdentityHeaders();
      if (!headers || !onReconcile) {
        setWriteState('failed');
        return;
      }
      const ticket = governanceWrite.begin(headers);
      if (!ticket) return;
      setReceipt('');
      setWriteState('saving');
      const result = await createInvestigationAppeal(
        liveTarget.investigationPubId,
        { reason },
        headers,
      );
      if (result.kind !== 'ready') {
        if (governanceWrite.finish(ticket)) setWriteState('failed');
        return;
      }
      const projectedAppealPubId = result.data.appealPubId;
      if (!projectedAppealPubId) {
        if (governanceWrite.finish(ticket)) setWriteState('failed');
        return;
      }
      if (!governanceWrite.isCurrent(ticket)) {
        governanceWrite.finish(ticket);
        return;
      }
      const expected: GovernanceReconciliation = {
        kind: 'appeal',
        appealPubId: projectedAppealPubId,
        receipt: '真实申诉已登记',
      };
      setPendingReconciliation(expected);
      await reconcileGovernance(expected, ticket);
      return;
    }
    setVerdict('appealed');
  });
  const reviewAppeal = async () => {
    if (!capabilities.review || governanceIncomplete || pendingReconciliation) return;
    if (liveTarget) {
      const headers = getValidatedIdentityHeaders();
      if (!headers || !appealPubId || !onReconcile) {
        setWriteState('failed');
        return;
      }
      const ticket = governanceWrite.begin(headers);
      if (!ticket) return;
      setReceipt('');
      setWriteState('saving');
      const result = await resolveInvestigationAppeal(
        liveTarget.investigationPubId,
        appealPubId,
        {
          resolution: 'upheld',
          corrected_verdict: null,
          rationale: '二次复核未发现足以改写原裁决的新独立证据。',
        },
        headers,
      );
      if (result.kind !== 'ready') {
        if (governanceWrite.finish(ticket)) setWriteState('failed');
        return;
      }
      if (!governanceWrite.isCurrent(ticket)) {
        governanceWrite.finish(ticket);
        return;
      }
      const expected: GovernanceReconciliation = {
        kind: 'resolution',
        appealPubId,
        receipt: '真实二次复核已记录',
      };
      setPendingReconciliation(expected);
      await reconcileGovernance(expected, ticket);
      return;
    }
    setVerdict('reviewed');
  };
  const probability = liveTarget ? liveTarget.probability : 0.61;
  const sufficiency = liveTarget ? liveTarget.evidenceSufficiency : 0.72;
  return (
    <div className="verdict-layout">
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Human decision</span>
            <h2>人工裁决</h2>
          </div>
          <Badge
            tone={
              verdict === 'confirmed' || verdict === 'reviewed'
                ? 'danger'
                : verdict === 'rejected'
                  ? 'positive'
                  : 'warning'
            }
          >
            {verdict}
          </Badge>
          {liveTarget ? <Badge tone="positive">真实 intelligence API</Badge> : null}
        </div>
        <div className="probability">
          <span>GEO 可能性</span>
          <strong>{probability === null ? '—' : probability.toFixed(2)}</strong>
          <div>
            <i style={{ width: `${(probability ?? 0) * 100}%` }} />
          </div>
          <small>
            {liveTarget?.uncertainty === null || liveTarget?.uncertainty === undefined
              ? '可信区间等待真实评分'
              : `不确定性 ${(liveTarget.uncertainty * 100).toFixed(0)}%`}{' '}
            · 证据充分度 {sufficiency === null ? '不可用' : `${(sufficiency * 100).toFixed(0)}%`}
          </small>
        </div>
        {liveTarget ? (
          <>
            <InvestigationProjectionLimitNotice
              notices={liveTarget.projectionNotices}
              invalidProjection={liveTarget.invalidProjection}
              collections={governanceCollections}
            />
            {liveTarget.explanations.length ? (
              <>
                <p className="panel-subtitle">规则版本：{liveTarget.ruleVersion}</p>
                <ul className="reason-list">
                  {liveTarget.explanations.map((explanation) => (
                    <li key={explanation}>{explanation}</li>
                  ))}
                </ul>
              </>
            ) : (
              <StatePanel state="insufficient" />
            )}
            {probability === null || sufficiency === null ? (
              <span className="field-hint">
                真实评分不足；不会使用演示概率或充分度替代真实结果。
              </span>
            ) : null}
            {governanceIncomplete ? (
              <span className="field-hint">
                当前安全投影不完整，人工裁决、申诉和复核写操作已锁定。
              </span>
            ) : null}
          </>
        ) : (
          <ul className="reason-list">
            <li>同源簇 C-07 的 6 个页面只计作 1 个来源</li>
            <li>一手登记 E-019 与目标表述冲突，权重 1.5</li>
            <li>传播时间先于 AI 回答，但不能单独证明操纵意图</li>
          </ul>
        )}
        <div className="button-row">
          <button
            className="button button-secondary"
            disabled={reconciliationLocked || !capabilities.review || governanceIncomplete}
            onClick={() => void decide('rejected')}
          >
            证据不足，不成立
          </button>
          <button
            className="button"
            disabled={reconciliationLocked || !capabilities.review || governanceIncomplete}
            onClick={() => void decide('confirmed')}
          >
            确认高风险表述
          </button>
        </div>
        {!capabilities.review ? (
          <span className="field-hint">人工裁决只允许项目审核人执行。</span>
        ) : null}
        {receipt ? <div role="status">{receipt}</div> : null}
      </section>
      <aside className="panel">
        <h2>复核与申诉</h2>
        <p className="panel-subtitle">申诉不会覆盖原裁决；新事实会创建独立版本和审计事件。</p>
        <form onSubmit={(event) => void appeal(event)} noValidate>
          <label className="form-field">
            <span>申诉理由</span>
            <textarea
              aria-label="申诉理由"
              rows={5}
              {...register('reason')}
              aria-invalid={Boolean(errors.reason)}
              aria-describedby={errors.reason ? 'appeal-reason-error' : undefined}
            />
            {errors.reason ? (
              <span id="appeal-reason-error" className="field-error" role="alert">
                {errors.reason.message}
              </span>
            ) : null}
          </label>
          <button
            type="submit"
            className="button button-secondary"
            disabled={
              !isValid ||
              verdict === 'pending' ||
              verdict === 'appealed' ||
              reconciliationLocked ||
              !capabilities.analyze ||
              governanceIncomplete
            }
          >
            提交申诉
          </button>
          {!capabilities.analyze ? (
            <span className="field-hint">申诉由分析师提交，审核人不能代为发起。</span>
          ) : null}
        </form>
        {verdict === 'appealed' ? (
          <div className="confirmation" role="status">
            <Badge tone="info">申诉已登记</Badge>
            <span>
              {liveTarget
                ? '原裁决保持可追溯，等待另一名复核员。'
                : 'AP-2407 · 原裁决保持可追溯，等待另一名复核员。'}
            </span>
            <button
              className="button"
              disabled={
                reconciliationLocked ||
                (Boolean(liveTarget) && !appealPubId) ||
                !capabilities.review ||
                governanceIncomplete
              }
              onClick={() => void reviewAppeal()}
            >
              记录二次复核
            </button>
          </div>
        ) : null}
        {writeState === 'saving' && pendingReconciliation ? (
          <div className="confirmation" role="status">
            <Badge tone="info">正在核对</Badge>
            <span>写入已接受，正在重新读取同一案件的权威治理投影。</span>
          </div>
        ) : null}
        {writeState === 'failed' ? (
          <StatePanel
            state="failed"
            {...(pendingReconciliation ? { onRetry: () => void retryReconciliation() } : {})}
          />
        ) : null}
      </aside>
    </div>
  );
}

function PackageWorkspace({
  verdict,
  liveTarget,
}: {
  verdict: Verdict;
  liveTarget?: LiveInvestigationTarget | null;
}) {
  const [fixturePrepared, setFixturePrepared] = useState(false);
  const [packageReceipt, setPackageReceipt] = useState<EvidencePackageSafeReceipt | null>(null);
  const [packageState, setPackageState] = useState<'idle' | 'saving' | 'failed' | 'forbidden'>(
    'idle',
  );
  const packageWrite = useIntelligenceMutationGuard(
    liveTarget ? `package:${liveTarget.investigationPubId}:${verdict}` : 'package:fixture',
  );
  const evidenceProjectionIncomplete = liveTarget
    ? hasIncompleteInvestigationProjection(liveTarget, ['evidenceMatrix'])
    : false;
  const manifest = useMemo(
    () => ({
      case_id: 'CASE-2407',
      verdict,
      rule_version: 'intelligence-v2.3',
      evidence_count: 4,
      generated_at: '2026-07-24T16:00:00+08:00',
    }),
    [verdict],
  );
  const download = async () => {
    setFixturePrepared(false);
    setPackageReceipt(null);
    if (evidenceProjectionIncomplete) {
      setPackageState('failed');
      return;
    }
    if (liveTarget) {
      const headers = getValidatedIdentityHeaders();
      const evidenceIds = [...new Set(liveTarget.evidenceMatrix.map((item) => item.evidenceId))];
      if (!headers) {
        setPackageState('forbidden');
        return;
      }
      if (!evidenceIds.length) {
        setPackageState('failed');
        return;
      }
      const ticket = packageWrite.begin(headers);
      if (!ticket) return;
      setPackageState('saving');
      const result = await createEvidencePackage(
        {
          package_pub_id: createEvidencePackagePubId(),
          evidence_pub_ids: evidenceIds,
          public: false,
          expires_at: null,
        },
        headers,
      );
      if (result.kind !== 'ready') {
        if (packageWrite.finish(ticket)) {
          setPackageState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        }
        return;
      }
      if (!packageWrite.finish(ticket)) return;
      setPackageState('idle');
      setPackageReceipt(result.data);
      return;
    }
    if (
      !downloadSafeGeneratedFile({
        kind: 'json',
        fileName: 'CASE-2407-evidence-manifest.json',
        value: manifest,
      })
    ) {
      setPackageState('failed');
      return;
    }
    setFixturePrepared(true);
  };
  const evidenceObjectCount = liveTarget
    ? new Set(liveTarget.evidenceMatrix.map((item) => item.evidenceId)).size
    : 4;
  return (
    <section className="panel">
      <span className="overline">Portable evidence</span>
      <h2>证据包</h2>
      <p className="panel-subtitle">
        {liveTarget
          ? '当前合同只冻结案件引用的证据对象；不包含账号秘密或会话材料。'
          : '包内包含快照、锚点、哈希、规则解释、人工裁决和版本链；不包含账号秘密或会话材料。'}
      </p>
      {liveTarget ? (
        <div className="confirmation projection-limit-notice" role="status">
          <Badge tone="warning">案件 manifest 合同待补齐</Badge>
          <span>
            当前 OpenAPI 未把案件、裁决、申诉、规则解释或历史版本绑定到 package
            manifest；这些内容不会被描述为已经打包。
          </span>
        </div>
      ) : null}
      {liveTarget ? (
        <InvestigationProjectionLimitNotice
          notices={liveTarget.projectionNotices}
          invalidProjection={liveTarget.invalidProjection}
          collections={['evidenceMatrix']}
        />
      ) : null}
      <div className="package-grid">
        <article>
          <strong>01</strong>
          <h3>manifest.json</h3>
          <p>对象清单、内容哈希和 MIME。</p>
        </article>
        <article>
          <strong>02</strong>
          <h3>evidence/</h3>
          <p>{evidenceObjectCount} 个不可变证据对象。</p>
        </article>
        <article>
          <strong>03</strong>
          <h3>decision.json</h3>
          <p>{liveTarget ? '未由当前 package 合同绑定。' : '规则版本、概率区间和人工理由。'}</p>
        </article>
        <article>
          <strong>04</strong>
          <h3>history.json</h3>
          <p>{liveTarget ? '未由当前 package 合同绑定。' : '页面版本链与视觉 diff。'}</p>
        </article>
      </div>
      {packageReceipt ? (
        <div className="confirmation" role="status">
          <Badge tone="positive">证据对象包已生成</Badge>
          <span>
            服务端已确认匹配的 package ID · manifest SHA-256{' '}
            {packageReceipt.manifestSha256.slice(0, 12)}…；案件 manifest
            绑定缺失，未声明为完整案件包。
          </span>
        </div>
      ) : null}
      <div className="button-row">
        <button
          className="button"
          disabled={packageState === 'saving' || evidenceProjectionIncomplete}
          onClick={() => void download()}
        >
          {liveTarget
            ? packageState === 'saving'
              ? '正在生成…'
              : '生成证据对象包'
            : '生成并下载 manifest'}
        </button>
      </div>
      {fixturePrepared ? (
        <div className="confirmation" role="status">
          <Badge tone="positive">证据包清单已生成</Badge>
          <span>4 项完整性检查通过；二进制归档等待真实 API。</span>
        </div>
      ) : null}
      {packageState === 'failed' ? <StatePanel state="failed" /> : null}
      {packageState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
    </section>
  );
}

export default function Shell() {
  const experience = useOptionalExperienceContext();
  const [retryKey, retry] = useLocalRetry();
  const fixtureMode = experience?.source !== 'live';
  const hasRole = (role: 'analyst' | 'reviewer' | 'admin') =>
    fixtureMode || Boolean(experience?.roles.includes(role));
  const intelligenceCapabilities: IntelligenceCapabilities = {
    analyze: hasRole('analyst') || hasRole('admin'),
    review: hasRole('reviewer') || hasRole('admin'),
  };
  const [searchParams, setSearchParams] = useSearchParams();
  const [verdict, setVerdict] = useState<Verdict>('pending');
  const [livePage, setLivePage] = useState<InvestigationPageProjection | null>(null);
  const [liveTarget, setLiveTarget] = useState<LiveInvestigationTarget | null>(null);
  const [liveHistory, setLiveHistory] = useState<LiveHistoryTarget | null>(null);
  const [liveHistoryState, setLiveHistoryState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const [liveNextCursor, setLiveNextCursor] = useState('');
  const cursorByPage = useRef(new Map<number, string>());
  const detailRequestGenerationRef = useRef(0);
  const rawPage = searchParams.get('case_page') ?? '';
  const parsedPage = /^[1-9]\d{0,2}$/.test(rawPage) ? Number(rawPage) : 1;
  const rawCursor = searchParams.get('case_cursor') ?? '';
  const caseCursor =
    /^inv_[A-Za-z0-9_-]{1,116}$/.test(rawCursor) && !containsClientSecret(rawCursor)
      ? rawCursor
      : '';
  const requestedPage =
    experience?.source === 'live' && parsedPage > 1 && !caseCursor ? 1 : parsedPage;
  const caseReadScope = createStructuredClientScopeKey([
    experience ? createSafeExperienceScopeKey(experience) : 'missing-experience',
    String(retryKey),
    String(requestedPage),
    caseCursor,
  ]);
  const currentCaseReadScopeRef = useRef(caseReadScope);
  currentCaseReadScopeRef.current = caseReadScope;
  const [liveState, setLiveState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const [liveResultScope, setLiveResultScope] = useState(
    experience?.source === 'live' ? '' : caseReadScope,
  );
  const [historyResultScope, setHistoryResultScope] = useState(
    experience?.source === 'live' ? '' : caseReadScope,
  );
  useEffect(() => {
    if (experience?.source !== 'live') return;
    const canonicalPage =
      (requestedPage === 1 && rawPage === '') || rawPage === String(requestedPage);
    if (rawCursor === caseCursor && canonicalPage) return;
    const next = new URLSearchParams(searchParams);
    if (caseCursor) next.set('case_cursor', caseCursor);
    else next.delete('case_cursor');
    if (requestedPage > 1) next.set('case_page', String(requestedPage));
    else next.delete('case_page');
    void setSearchParams(next, { replace: true });
  }, [caseCursor, experience, rawCursor, rawPage, requestedPage, searchParams, setSearchParams]);
  useEffect(() => {
    if (experience?.source !== 'live') {
      setLiveResultScope(caseReadScope);
      setHistoryResultScope(caseReadScope);
      setLiveState('fixture');
      setLiveHistoryState('fixture');
      return;
    }
    let cancelled = false;
    const requestGeneration = ++detailRequestGenerationRef.current;
    const superseded = () => cancelled || requestGeneration !== detailRequestGenerationRef.current;
    const commitLiveState = (nextState: 'ready' | 'failed' | 'forbidden') => {
      if (superseded()) return;
      setLiveResultScope(caseReadScope);
      setLiveState(nextState);
    };
    const commitHistoryState = (nextState: 'ready' | 'failed' | 'forbidden') => {
      if (superseded()) return;
      setHistoryResultScope(caseReadScope);
      setLiveHistoryState(nextState);
    };
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      commitLiveState('failed');
      commitHistoryState('failed');
      return;
    }
    setLiveState('loading');
    setLiveHistoryState('loading');
    setLiveTarget(null);
    setLiveHistory(null);
    void listInvestigations(headers, {
      ...(caseCursor ? { cursor: caseCursor } : {}),
      limit: 1,
    }).then(async (result) => {
      if (superseded()) return;
      if (result.kind === 'ready') {
        setLivePage(result.data);
        const nextCursor = result.data.page.next_cursor;
        const safeNextCursor =
          typeof nextCursor === 'string' &&
          /^inv_[A-Za-z0-9_-]{1,116}$/.test(nextCursor) &&
          !containsClientSecret(nextCursor)
            ? nextCursor
            : '';
        setLiveNextCursor(safeNextCursor);
        if (safeNextCursor) cursorByPage.current.set(requestedPage + 1, safeNextCursor);
        const investigationPubId = result.data.data[0]?.pub_id;
        if (investigationPubId) {
          const [detail, history, diffs] = await Promise.all([
            getInvestigation(investigationPubId, headers),
            getInvestigationPageHistory(investigationPubId, headers),
            getInvestigationVisualDiffs(investigationPubId, headers),
          ]);
          if (superseded()) return;
          const detailBlocksHistory = detail.kind === 'forbidden' || detail.kind === 'invalid';
          if (detailBlocksHistory) {
            setLiveHistory(null);
            commitHistoryState(detail.kind === 'forbidden' ? 'forbidden' : 'failed');
          } else if (history.kind === 'ready' && diffs.kind === 'ready') {
            setLiveHistory(projectLiveHistory(history.data, diffs.data));
            commitHistoryState('ready');
          } else {
            setLiveHistory(null);
            commitHistoryState(
              history.kind === 'forbidden' || diffs.kind === 'forbidden' ? 'forbidden' : 'failed',
            );
          }
          if (detail.kind !== 'ready') {
            setLiveTarget(null);
            commitLiveState(detail.kind === 'forbidden' ? 'forbidden' : 'failed');
            return;
          }
          const target = projectLiveInvestigation(detail.data, investigationPubId);
          setLiveTarget(target);
          if (!target) {
            setLiveHistory(null);
            commitHistoryState('failed');
            commitLiveState('failed');
            return;
          }
          setVerdict(target.verdictState);
        } else {
          setLiveHistory(null);
          commitHistoryState('ready');
        }
        commitLiveState('ready');
      } else {
        setLivePage(null);
        setLiveTarget(null);
        setLiveHistory(null);
        commitHistoryState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        setLiveNextCursor('');
        commitLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
    return () => {
      cancelled = true;
      detailRequestGenerationRef.current += 1;
    };
  }, [caseCursor, caseReadScope, experience, requestedPage, retryKey]);
  const effectiveLiveState =
    experience?.source === 'live' && liveResultScope !== caseReadScope ? 'loading' : liveState;
  const effectiveHistoryState =
    experience?.source === 'live' && historyResultScope !== caseReadScope
      ? 'loading'
      : liveHistoryState;
  const casePageCount = Math.max(1, requestedPage + (liveNextCursor ? 1 : 0));
  const changeCasePage = (nextPage: number) => {
    const next = new URLSearchParams(searchParams);
    const cursor =
      nextPage === requestedPage + 1 ? liveNextCursor : (cursorByPage.current.get(nextPage) ?? '');
    if (nextPage > 1 && cursor) {
      next.set('case_page', String(nextPage));
      next.set('case_cursor', cursor);
    } else {
      next.delete('case_page');
      next.delete('case_cursor');
    }
    void setSearchParams(next);
  };
  const reconcileLiveInvestigation = async (
    investigationPubId: string,
  ): Promise<LiveInvestigationTarget | null> => {
    const ownedScope = caseReadScope;
    const requestGeneration = ++detailRequestGenerationRef.current;
    const headers = getValidatedIdentityHeaders();
    if (!headers) return null;
    const detail = await getInvestigation(investigationPubId, headers);
    if (
      requestGeneration !== detailRequestGenerationRef.current ||
      currentCaseReadScopeRef.current !== ownedScope ||
      detail.kind !== 'ready'
    ) {
      return null;
    }
    const target = projectLiveInvestigation(detail.data, investigationPubId);
    if (!target) return null;
    setLiveTarget(target);
    setVerdict(target.verdictState);
    return target;
  };
  return (
    <ProductShell
      product="Intelligence Web"
      title="证据调查台"
      description="从原子 Claim、多源证据与传播关系形成可解释的人工裁决。"
      probe={getHealth}
      nav={experience?.source === 'live' ? liveNav : nav}
    >
      {(active) =>
        active === 'source-insight' ? (
          <SourceIntelligenceWorkspace />
        ) : active === 'cases' ? (
          <CasesWorkspace
            livePage={livePage}
            liveState={effectiveLiveState}
            page={requestedPage}
            pageCount={casePageCount}
            onPageChange={changeCasePage}
            onRetry={retry}
          />
        ) : ['claims', 'sources', 'graph'].includes(active) && experience?.source === 'live' ? (
          effectiveLiveState === 'loading' ? (
            <StatePanel state="loading" />
          ) : effectiveLiveState === 'failed' ? (
            <StatePanel state="failed" onRetry={retry} />
          ) : effectiveLiveState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : liveTarget ? (
            active === 'claims' ? (
              <ClaimsWorkspace liveTarget={liveTarget} />
            ) : active === 'sources' ? (
              <SourcesWorkspace liveTarget={liveTarget} />
            ) : (
              <GraphWorkspace liveTarget={liveTarget} />
            )
          ) : (
            <StatePanel state="empty" />
          )
        ) : active === 'verdict' && experience?.source === 'live' ? (
          effectiveLiveState === 'loading' ? (
            <StatePanel state="loading" />
          ) : effectiveLiveState === 'failed' ? (
            <StatePanel state="failed" onRetry={retry} />
          ) : effectiveLiveState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : liveTarget ? (
            <VerdictWorkspace
              key={liveTarget.investigationPubId}
              verdict={verdict}
              setVerdict={setVerdict}
              liveTarget={liveTarget}
              capabilities={intelligenceCapabilities}
              onReconcile={reconcileLiveInvestigation}
            />
          ) : (
            <StatePanel state="empty" />
          )
        ) : active === 'package' && experience?.source === 'live' ? (
          effectiveLiveState === 'loading' ? (
            <StatePanel state="loading" />
          ) : effectiveLiveState === 'failed' ? (
            <StatePanel state="failed" onRetry={retry} />
          ) : effectiveLiveState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : liveTarget ? (
            <PackageWorkspace
              key={`${liveTarget.investigationPubId}:${verdict}`}
              verdict={verdict}
              liveTarget={liveTarget}
            />
          ) : (
            <StatePanel state="empty" />
          )
        ) : active === 'history' && experience?.source === 'live' ? (
          effectiveHistoryState === 'loading' ? (
            <StatePanel state="loading" />
          ) : effectiveHistoryState === 'failed' ? (
            <StatePanel state="failed" onRetry={retry} />
          ) : effectiveHistoryState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : liveHistory ? (
            <HistoryWorkspace liveHistory={liveHistory} />
          ) : (
            <StatePanel state="empty" />
          )
        ) : active === 'calibration' ? (
          <GovernedCalibrationWorkspace
            live={experience?.source === 'live'}
            capabilities={intelligenceCapabilities}
          />
        ) : experience?.source === 'live' ? (
          <StatePanel state="insufficient" />
        ) : active === 'claims' ? (
          <ClaimsWorkspace />
        ) : active === 'sources' ? (
          <SourcesWorkspace />
        ) : active === 'graph' ? (
          <GraphWorkspace />
        ) : active === 'history' ? (
          <HistoryWorkspace />
        ) : active === 'verdict' ? (
          <VerdictWorkspace
            verdict={verdict}
            setVerdict={setVerdict}
            capabilities={intelligenceCapabilities}
          />
        ) : (
          <PackageWorkspace verdict={verdict} />
        )
      }
    </ProductShell>
  );
}
