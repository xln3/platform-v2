import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  admitEvaluatedModel,
  allowsFixtureIdentityHeaders,
  approveEvaluationDataset,
  authorizeCustomerAccount,
  bindOidcIdentity,
  commentOnReport,
  confirmReportDelivery,
  createCustomerPairing,
  createAssetConfirmation,
  createClientProfileVersion,
  createEvidencePackage,
  createEvidencePackagePubId,
  createGeoApiClient,
  createIdentityMember,
  createInvestigationAppeal,
  createInvestigationVerdict,
  createMetricExport,
  createPostingBatch,
  createPostAnalysisTask,
  createProjectResource,
  createReportAction,
  createReportDelivery,
  createReportEffectRetest,
  createReportRevision,
  getAnalyticsCompetitors,
  getAnalyticsAnswerRelations,
  getAnalyticsBreakdown,
  getAnalyticsDelta,
  getAnalyticsOverview,
  getCustomerAnswerPage,
  getCustomerDashboard,
  getCustomerMetricCatalog,
  getEvidenceAssetContent,
  getHealth,
  geoApiJsonResponseMaxBytes,
  mediaWemediaDatasetMaxBytes,
  getIdentitySession,
  getInvestigation,
  getInvestigationPageHistory,
  getInvestigationVisualDiffs,
  getMediaPricesDataset,
  getMediaWemediaDataset,
  getMediaPricesRefreshStatus,
  getOperationsLifecycle,
  getPostAnalysisItem,
  getPostAnalysisItemAsset,
  getPostAnalysisTask,
  getReport,
  getReportArtifact,
  generateQuotation,
  listAnalyticsAnswers,
  listCustomerAccountEvents,
  listCustomerAccounts,
  listCustomerPairings,
  listAssetConfirmations,
  listClientProfileVersions,
  listEvidenceAssets,
  listEvaluationDatasets,
  listEvaluationRuns,
  listInvestigations,
  listModelAdmissions,
  resolveInvestigationAppeal,
  updateReportAction,
  listIdentityMembers,
  listOidcBindings,
  listPostAnalysisItems,
  listPostAnalysisTasks,
  listProjectResources,
  listReportDeliveries,
  listReports,
  loadProjectReportCatalog,
  listResponsibleMembers,
  listSopProjects,
  logoutIdentitySession,
  publishReport,
  projectCustomerAccountView,
  projectCustomerAnswerPageBoundary,
  projectCustomerDashboardBoundary,
  projectCustomerMetricCatalogBoundary,
  projectCustomerEventView,
  projectCustomerPairingView,
  projectEvaluationDatasetView,
  projectEvaluationRunView,
  projectHealthBoundary,
  projectIdentityMemberView,
  projectIdentitySessionBoundary,
  projectInvestigationPage,
  projectEvaluationDatasetPage,
  projectEvaluationRunPage,
  projectIdentityProjectPageBoundary,
  projectModelAdmissionPage,
  projectModelAdmissionView,
  projectOidcBindingView,
  projectOperationsLifecycleSnapshot,
  projectMediaPricesDataset,
  projectMediaWemediaDataset,
  projectMediaPricesRefreshStatus,
  projectReportDetailIdentity,
  projectReportPage,
  projectResponsibleMemberView,
  projectSafeAccountMask,
  projectSafeIsoTimestamp,
  registerEvaluationDataset,
  registerCustomerAccount,
  reviewReport,
  requestMediaPricesRefresh,
  revokeCustomerAccount,
  revokeIdentityMember,
  revokeOidcIdentity,
  runEvaluationDataset,
  type AnalyticsAnchorSafeView,
  type AnalyticsAnswerSafeView,
  type AnalyticsCitationSafeView,
  type AnalyticsCompetitorSafeResponse,
  type AnalyticsDeltaSafeResponse,
  type AnalyticsEvidenceSafeView,
  type AnalyticsHistorySafeView,
  type AnalyticsAnswerRelationsProjection,
  type EffectRetestSafeView,
  type EvaluationDatasetSafeView,
  type EvaluationRunSafeView,
  type IdentitySessionHeaders,
  type InvestigationDetailProjection,
  type InvestigationPageHistorySafeView,
  type InvestigationSummarySafeView,
  type InvestigationVisualDiffSafeView,
  type ModelAdmissionSafeView,
  type OptimizationActionSafeView,
  type ProjectResourceView,
  type ProjectPageResponse,
  type ProjectSummary,
  type ReportArtifactSafeView,
  type ReportCommentSafeView,
  type ReportComponentSafeView,
  type ReportDeliverySafeView,
  type ReportDetailProjection,
  type ReportEvidenceBindingSafeView,
  type ReportEventSafeView,
  type ReportFrozenFactSafeView,
  type ReportReviewSafeView,
  type ReportSummarySafeView,
  type ReportVersionSafeView,
  type SafeStructuredRecord,
} from './index';

afterEach(() => vi.unstubAllGlobals());

type SecretIdentityHeaderKeys = Extract<
  keyof IdentitySessionHeaders,
  'Authorization' | 'X-Service-Token' | 'session' | '__Host-geo_oidc'
>;
const identityHeadersExcludeSecretNames: SecretIdentityHeaderKeys extends never ? true : false =
  true;
type ProjectedClientOverrideMethodKeys = Extract<
  keyof NonNullable<Parameters<typeof getHealth>[0]>,
  'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
>;
const projectedWrappersExcludeRawClientMethods: ProjectedClientOverrideMethodKeys extends never
  ? true
  : false = true;
type AnalyticsProjectedAnchor =
  AnalyticsAnswerRelationsProjection['evidence'][number]['anchors'][number];
type AnalyticsProjectedAnchorAllowsArbitraryKeys = string extends keyof AnalyticsProjectedAnchor
  ? true
  : false;
type AnalyticsProjectedAnchorBboxAllowsRecord =
  Record<string, unknown> extends AnalyticsProjectedAnchor['bbox'] ? true : false;
const analyticsProjectedAnchorsExcludeArbitraryKeys: AnalyticsProjectedAnchorAllowsArbitraryKeys = false;
const analyticsProjectedAnchorBboxIsBounded: AnalyticsProjectedAnchorBboxAllowsRecord = false;
type InvestigationProjectedRecord =
  | InvestigationDetailProjection['scores'][number]
  | InvestigationDetailProjection['claims'][number]
  | InvestigationDetailProjection['evidence_matrix'][number]
  | InvestigationDetailProjection['source_independence'][number]
  | InvestigationDetailProjection['graph'][number]
  | InvestigationDetailProjection['appeals'][number]
  | InvestigationDetailProjection['verdicts'][number];
type InvestigationProjectionAllowsArbitraryKeys = string extends keyof InvestigationProjectedRecord
  ? true
  : false;
const investigationProjectionExcludesArbitraryKeys: InvestigationProjectionAllowsArbitraryKeys = false;
type AnalyticsDeltaProjectionAllowsArbitraryKeys = string extends keyof AnalyticsDeltaSafeResponse
  ? true
  : false;
type AnalyticsCompetitorProjectionAllowsArbitraryKeys =
  string extends keyof AnalyticsCompetitorSafeResponse[number] ? true : false;
type InvestigationVisualDiffProjectionAllowsArbitraryKeys =
  string extends keyof InvestigationVisualDiffSafeView ? true : false;
type InvestigationTextDiffAllowsArbitraryKeys = string extends keyof NonNullable<
  InvestigationVisualDiffSafeView['text_diff']
>
  ? true
  : false;
const analyticsDeltaProjectionExcludesArbitraryKeys: AnalyticsDeltaProjectionAllowsArbitraryKeys = false;
const analyticsCompetitorProjectionExcludesArbitraryKeys: AnalyticsCompetitorProjectionAllowsArbitraryKeys = false;
const investigationVisualDiffProjectionExcludesArbitraryKeys: InvestigationVisualDiffProjectionAllowsArbitraryKeys = false;
const investigationTextDiffExcludesArbitraryKeys: InvestigationTextDiffAllowsArbitraryKeys = false;
type ProjectResourceDataAllowsArbitraryKeys = string extends keyof ProjectResourceView['data']
  ? true
  : false;
type ReportDetailProjectionAllowsArbitraryKeys = string extends keyof ReportDetailProjection
  ? true
  : false;
type RawRecordCanMasqueradeAsSafeStructuredRecord =
  Record<string, unknown> extends SafeStructuredRecord ? true : false;
const projectResourceDataExcludesArbitraryKeys: ProjectResourceDataAllowsArbitraryKeys = false;
const reportDetailProjectionExcludesArbitraryKeys: ReportDetailProjectionAllowsArbitraryKeys = false;
const safeStructuredRecordRequiresProjection: RawRecordCanMasqueradeAsSafeStructuredRecord = false;

// Round170: public browser read types are fixed-field boundaries. keyof must equal the
// projected field literal set exactly, and arbitrary records must not be assignable.
type Expect<T extends true> = T;
type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
const projectSummaryHasFixedKeys: Expect<
  Equal<
    keyof ProjectSummary,
    'pub_id' | 'tenant_pub_id' | 'name' | 'state' | 'created_at' | 'updated_at'
  >
> = true;
const projectPageResponseHasFixedKeys: Expect<Equal<keyof ProjectPageResponse, 'data' | 'page'>> =
  true;
const projectPageMetadataHasFixedKeys: Expect<
  Equal<keyof ProjectPageResponse['page'], 'next_cursor' | 'has_more'>
> = true;
const reportSummarySafeViewHasFixedKeys: Expect<
  Equal<
    keyof ReportSummarySafeView,
    'pub_id' | 'project_pub_id' | 'title' | 'state' | 'created_at' | 'updated_at'
  >
> = true;
const reportDeliverySafeViewHasFixedKeys: Expect<
  Equal<
    keyof ReportDeliverySafeView,
    | 'pub_id'
    | 'report_pub_id'
    | 'recipient_pub_id'
    | 'delivered_at'
    | 'confirmed_at'
    | 'confirmation_comment'
  >
> = true;
const investigationSummarySafeViewHasFixedKeys: Expect<
  Equal<
    keyof InvestigationSummarySafeView,
    | 'pub_id'
    | 'title'
    | 'state'
    | 'access_class'
    | 'created_at'
    | 'updated_at'
    | 'claim_count'
    | 'source_cluster_count'
    | 'probability'
    | 'latest_verdict'
  >
> = true;
const investigationPageHistorySafeViewHasFixedKeys: Expect<
  Equal<
    keyof InvestigationPageHistorySafeView,
    | 'content_pub_id'
    | 'version_pub_id'
    | 'canonical_url'
    | 'title'
    | 'version_number'
    | 'body_hash'
    | 'evidence_pub_id'
    | 'captured_at'
    | 'published_at'
    | 'snapshot_pub_id'
    | 'snapshot_number'
    | 'normalized_text_hash'
    | 'perceptual_hash'
  >
> = true;
const evaluationDatasetSafeViewHasFixedKeys: Expect<
  Equal<
    keyof EvaluationDatasetSafeView,
    | 'pub_id'
    | 'version'
    | 'dataset_sha256'
    | 'state'
    | 'case_count'
    | 'positive_count'
    | 'labeler_count'
    | 'submitted_at'
    | 'approved_at'
  >
> = true;
const evaluationRunSafeViewHasFixedKeys: Expect<
  Equal<
    keyof EvaluationRunSafeView,
    | 'pub_id'
    | 'dataset_pub_id'
    | 'scorer_version'
    | 'decision_threshold'
    | 'calibration_bins'
    | 'training_cluster_manifest_sha256'
    | 'training_cluster_count'
    | 'sample_count'
    | 'admission_policy_version'
    | 'admission_checks'
    | 'admission_passed'
    | 'model_admission_state'
    | 'metrics'
    | 'required_explanation_fields'
    | 'created_at'
  >
> = true;
const evaluationRunSafeViewMetricsHaveFixedKeys: Expect<
  Equal<
    keyof EvaluationRunSafeView['metrics'],
    | 'precision'
    | 'recall'
    | 'false_positive_rate'
    | 'brier_score'
    | 'expected_calibration_error'
    | 'explanation_completeness_rate'
    | 'sample_count'
    | 'positive_count'
    | 'negative_count'
    | 'dataset_version'
    | 'scorer_version'
    | 'evaluation_sha256'
  >
> = true;
const evaluationRunSafeViewChecksHaveFixedKeys: Expect<
  Equal<
    keyof EvaluationRunSafeView['admission_checks'],
    | 'precision'
    | 'recall'
    | 'false_positive_rate'
    | 'brier_score'
    | 'expected_calibration_error'
    | 'explanation_completeness'
  >
> = true;
const modelAdmissionSafeViewHasFixedKeys: Expect<
  Equal<
    keyof ModelAdmissionSafeView,
    | 'pub_id'
    | 'evaluation_run_pub_id'
    | 'scorer_version'
    | 'state'
    | 'rationale'
    | 'admitted_at'
    | 'revoked_at'
  >
> = true;
const analyticsAnswerSafeViewHasFixedKeys: Expect<
  Equal<
    keyof AnalyticsAnswerSafeView,
    | 'pub_id'
    | 'project_pub_id'
    | 'run_pub_id'
    | 'config_version_pub_id'
    | 'query_pub_id'
    | 'query_text'
    | 'response_text'
    | 'model'
    | 'region'
    | 'mode'
    | 'eligible'
    | 'degraded'
    | 'capture_time'
    | 'mentioned'
    | 'rank'
    | 'sentiment'
    | 'recommendation_state'
    | 'citation_count'
  >
> = true;
const analyticsCitationSafeViewHasFixedKeys: Expect<
  Equal<
    keyof AnalyticsCitationSafeView,
    | 'pub_id'
    | 'ordinal'
    | 'canonical_url'
    | 'host'
    | 'title'
    | 'cited_text'
    | 'own_source'
    | 'content_hash'
  >
> = true;
const analyticsAnchorSafeViewHasFixedKeys: Expect<
  Equal<
    keyof AnalyticsAnchorSafeView,
    'pub_id' | 'text_start' | 'text_end' | 'bbox' | 'page_number' | 'quote_hash'
  >
> = true;
const analyticsEvidenceSafeViewHasFixedKeys: Expect<
  Equal<
    keyof AnalyticsEvidenceSafeView,
    | 'pub_id'
    | 'relation_type'
    | 'kind'
    | 'access_class'
    | 'sha256'
    | 'mime_type'
    | 'byte_size'
    | 'source_url'
    | 'capture_time'
    | 'anchors'
  >
> = true;
const analyticsHistorySafeViewHasFixedKeys: Expect<
  Equal<
    keyof AnalyticsHistorySafeView,
    | 'pub_id'
    | 'before_evidence_pub_id'
    | 'after_evidence_pub_id'
    | 'similarity'
    | 'visual_diff_available'
    | 'created_at'
  >
> = true;
const reportComponentSafeViewHasFixedKeys: Expect<
  Equal<
    keyof ReportComponentSafeView,
    | 'pub_id'
    | 'report_version_pub_id'
    | 'component_type'
    | 'ordinal'
    | 'payload'
    | 'source'
    | 'created_at'
  >
> = true;
const reportFrozenFactSafeViewHasFixedKeys: Expect<
  Equal<
    keyof ReportFrozenFactSafeView,
    'pub_id' | 'report_version_pub_id' | 'ordinal' | 'payload' | 'payload_hash' | 'created_at'
  >
> = true;
const reportArtifactSafeViewHasFixedKeys: Expect<
  Equal<
    keyof ReportArtifactSafeView,
    | 'pub_id'
    | 'report_version_pub_id'
    | 'format'
    | 'evidence_pub_id'
    | 'mime_type'
    | 'byte_size'
    | 'sha256'
    | 'created_at'
  >
> = true;
const reportEvidenceBindingSafeViewHasFixedKeys: Expect<
  Equal<
    keyof ReportEvidenceBindingSafeView,
    | 'pub_id'
    | 'report_version_pub_id'
    | 'evidence_pub_id'
    | 'purpose'
    | 'kind'
    | 'access_class'
    | 'mime_type'
    | 'byte_size'
    | 'sha256'
    | 'anchor_count'
    | 'capture_time'
    | 'created_at'
  >
> = true;
const reportReviewSafeViewHasFixedKeys: Expect<
  Equal<
    keyof ReportReviewSafeView,
    'pub_id' | 'report_version_pub_id' | 'reviewer_pub_id' | 'decision' | 'rationale' | 'created_at'
  >
> = true;
const reportCommentSafeViewHasFixedKeys: Expect<
  Equal<
    keyof ReportCommentSafeView,
    | 'pub_id'
    | 'report_version_pub_id'
    | 'parent_pub_id'
    | 'author_pub_id'
    | 'body'
    | 'resolved_at'
    | 'created_at'
  >
> = true;
const reportEventSafeViewHasFixedKeys: Expect<
  Equal<
    keyof ReportEventSafeView,
    'pub_id' | 'report_version_pub_id' | 'event_type' | 'actor_pub_id' | 'data' | 'created_at'
  >
> = true;
const reportVersionSafeViewHasFixedKeys: Expect<
  Equal<
    keyof ReportVersionSafeView,
    | 'pub_id'
    | 'version_number'
    | 'window_start'
    | 'window_end'
    | 'filters'
    | 'metric_version'
    | 'scorer_version'
    | 'fact_snapshot_hash'
    | 'status'
    | 'components'
    | 'frozen_facts'
    | 'artifacts'
    | 'evidence_bindings'
    | 'reviews'
    | 'comments'
    | 'events'
  >
> = true;
const effectRetestSafeViewHasFixedKeys: Expect<
  Equal<
    keyof EffectRetestSafeView,
    'pub_id' | 'action_pub_id' | 'measured_at' | 'result' | 'recorded_by_pub_id' | 'created_at'
  >
> = true;
const optimizationActionSafeViewHasFixedKeys: Expect<
  Equal<
    keyof OptimizationActionSafeView,
    | 'pub_id'
    | 'description'
    | 'owner_pub_id'
    | 'state'
    | 'baseline'
    | 'outcome'
    | 'created_at'
    | 'updated_at'
    | 'effect_retests'
  >
> = true;
const reportDetailProjectionHasFixedKeys: Expect<
  Equal<
    keyof ReportDetailProjection,
    | 'pub_id'
    | 'project_pub_id'
    | 'title'
    | 'state'
    | 'created_at'
    | 'updated_at'
    | 'versions'
    | 'optimization_actions'
    | 'projection'
  >
> = true;
const investigationVisualDiffSafeViewHasFixedKeys: Expect<
  Equal<
    keyof InvestigationVisualDiffSafeView,
    | 'pub_id'
    | 'content_pub_id'
    | 'before_version_pub_id'
    | 'after_version_pub_id'
    | 'before_evidence_pub_id'
    | 'after_evidence_pub_id'
    | 'text_diff'
    | 'similarity'
    | 'visual_diff_available'
    | 'created_at'
  >
> = true;
type ArbitraryRecordMasqueradesAsFixedBoundary =
  | (Record<string, unknown> extends ProjectPageResponse ? true : false)
  | (Record<string, unknown> extends ProjectSummary ? true : false)
  | (Record<string, unknown> extends ReportSummarySafeView ? true : false)
  | (Record<string, unknown> extends EvaluationRunSafeView ? true : false)
  | (Record<string, unknown> extends AnalyticsAnswerSafeView ? true : false)
  | (Record<string, unknown> extends AnalyticsHistorySafeView ? true : false)
  | (Record<string, unknown> extends InvestigationSummarySafeView ? true : false)
  | (Record<string, unknown> extends ReportArtifactSafeView ? true : false)
  | (Record<string, unknown> extends ReportEvidenceBindingSafeView ? true : false)
  | (Record<string, unknown> extends ReportReviewSafeView ? true : false)
  | (Record<string, unknown> extends ReportCommentSafeView ? true : false)
  | (Record<string, unknown> extends ReportVersionSafeView ? true : false)
  | (Record<string, unknown> extends InvestigationVisualDiffSafeView ? true : false);
const fixedBoundariesRejectArbitraryRecords: ArbitraryRecordMasqueradesAsFixedBoundary = false;
type FixedBoundariesAllowArbitraryKeys =
  | (string extends keyof ProjectPageResponse ? true : false)
  | (string extends keyof ProjectSummary ? true : false)
  | (string extends keyof ReportSummarySafeView ? true : false)
  | (string extends keyof EvaluationRunSafeView ? true : false)
  | (string extends keyof AnalyticsAnswerSafeView ? true : false)
  | (string extends keyof AnalyticsHistorySafeView ? true : false)
  | (string extends keyof ReportDeliverySafeView ? true : false)
  | (string extends keyof ReportArtifactSafeView ? true : false)
  | (string extends keyof ReportEvidenceBindingSafeView ? true : false)
  | (string extends keyof ReportReviewSafeView ? true : false)
  | (string extends keyof ReportCommentSafeView ? true : false)
  | (string extends keyof ModelAdmissionSafeView ? true : false)
  | (string extends keyof EvaluationDatasetSafeView ? true : false);
const fixedBoundariesExcludeArbitraryKeys: FixedBoundariesAllowArbitraryKeys = false;

describe('generated client', () => {
  it('exports only generated browser-safe identity header names', () => {
    expect(identityHeadersExcludeSecretNames).toBe(true);
  });

  it('does not expose raw generated methods through projected wrapper overrides', () => {
    expect(projectedWrappersExcludeRawClientMethods).toBe(true);
  });

  it('does not expose arbitrary analytics anchor maps through the browser projection', () => {
    expect(analyticsProjectedAnchorsExcludeArbitraryKeys).toBe(false);
    expect(analyticsProjectedAnchorBboxIsBounded).toBe(false);
  });

  it('does not expose arbitrary investigation records through the browser projection', () => {
    expect(investigationProjectionExcludesArbitraryKeys).toBe(false);
  });

  it('does not expose arbitrary analytics or visual-diff maps through projected reads', () => {
    expect(analyticsDeltaProjectionExcludesArbitraryKeys).toBe(false);
    expect(analyticsCompetitorProjectionExcludesArbitraryKeys).toBe(false);
    expect(investigationVisualDiffProjectionExcludesArbitraryKeys).toBe(false);
    expect(investigationTextDiffExcludesArbitraryKeys).toBe(false);
  });

  it('requires projected report records and explicit project-resource fields', () => {
    expect(projectResourceDataExcludesArbitraryKeys).toBe(false);
    expect(reportDetailProjectionExcludesArbitraryKeys).toBe(false);
    expect(safeStructuredRecordRequiresProjection).toBe(false);
  });

  it('exports the health contract', () => {
    const value = {
      status: 'ok',
      service: 'geo-platform-v2',
      version: 'contract-v1',
    };
    expect(value.status).toBe('ok');
  });

  it('creates evidence package IDs that cannot resemble an OTP or complete phone', () => {
    const packagePubId = createEvidencePackagePubId('13800138-0000-4000-8000-138001380000');
    expect(packagePubId).toBe('pkg_hjogghjoggggkgggoggghjogghjogggg');
    expect(packagePubId).toMatch(/^pkg_[a-p]{32}$/);
    expect(packagePubId).not.toMatch(/\d/);
  });

  it('accepts only real timezone-qualified ISO timestamps', () => {
    expect(projectSafeIsoTimestamp('2026-07-25T22:10:00Z')).toBe('2026-07-25T22:10:00Z');
    expect(projectSafeIsoTimestamp('2026-07-25T22:10:00.123456789+08:00')).toBe(
      '2026-07-25T22:10:00.123456789+08:00',
    );
    expect(projectSafeIsoTimestamp('2026-08-05T17:27:57.411449+00:00')).toBe(
      '2026-08-05T17:27:57.411449+00:00',
    );
    expect(projectSafeIsoTimestamp('2026-08-05T17:27:57.824911Z')).toBe(
      '2026-08-05T17:27:57.824911Z',
    );
    expect(projectSafeIsoTimestamp('1')).toBeNull();
    expect(projectSafeIsoTimestamp('2026-07-25T22:10:00')).toBeNull();
    expect(projectSafeIsoTimestamp('2026-02-30T22:10:00Z')).toBeNull();
    expect(projectSafeIsoTimestamp('2026-07-25T24:10:00Z')).toBeNull();
    expect(projectSafeIsoTimestamp('2026-07-25T22:10:00+24:00')).toBeNull();
  });

  it('loads one bounded real Operations lifecycle contract and rejects projection drift', async () => {
    const payload = {
      metrics: {
        running_runs: 0,
        project_count: 5,
        pending_interventions: 0,
        healthy_sessions: 0,
        total_sessions: 0,
        delayed_runs: 0,
        p95_delay_seconds: null,
      },
      activity: [],
      accounts: [],
      interventions: [],
      events: [],
      projection: {
        activity: { total: 0, shown: 0, truncated: false },
        accounts: { total: 0, shown: 0, truncated: false },
        interventions: { total: 0, shown: 0, truncated: false },
        events: { total: 0, shown: 0, truncated: false },
      },
    };
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const url = new URL(outbound.url);
      expect(url.pathname).toBe('/api/v2/operations/lifecycle');
      expect(url.searchParams.get('limit')).toBe('25');
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const result = await getOperationsLifecycle(
      {
        'X-Tenant-Id': 'tnt_safe',
        'X-Actor-Id': 'operator-safe',
        'X-Actor-Role': 'operator',
      },
      25,
      createGeoApiClient('https://geo.example'),
    );
    expect(result).toEqual({
      kind: 'ready',
      data: {
        metrics: {
          runningRuns: 0,
          projectCount: 5,
          pendingInterventions: 0,
          healthySessions: 0,
          totalSessions: 0,
          delayedRuns: 0,
          p95DelayLabel: '—',
        },
        activity: [],
        accounts: [],
        interventions: [],
        events: [],
        revocationReceipt: null,
        projectionTruncated: false,
      },
    });
    expect(request).toHaveBeenCalledTimes(1);

    expect(
      projectOperationsLifecycleSnapshot({
        ...payload,
        projection: {
          ...payload.projection,
          events: { total: 1, shown: 0, truncated: false },
        },
      }),
    ).toBeNull();
  });

  it('uses generated Anti-GEO governance paths and keeps admission idempotency in headers', async () => {
    let mismatchedWriteReceipts = false;
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const path = new URL(outbound.url).pathname;
      const commonDataset = {
        pub_id: 'dset_safe',
        version: 'external-approved-v1',
        dataset_sha256: 'a'.repeat(64),
        state: outbound.method === 'POST' ? 'approved' : 'draft',
        case_count: 20,
        positive_count: 10,
        labeler_count: 2,
        submitted_at: '2026-07-25T00:00:00Z',
        approved_at: outbound.method === 'POST' ? '2026-07-25T00:01:00Z' : null,
        cookie: 'SESSION=dataset-response-canary',
      };
      const commonRun = {
        pub_id: 'eval_safe',
        dataset_pub_id: mismatchedWriteReceipts ? 'dset_wrong_target' : 'dset_safe',
        scorer_version: 'anti-geo-v1',
        decision_threshold: '0.5',
        calibration_bins: 10,
        training_cluster_manifest_sha256: 'c'.repeat(64),
        training_cluster_count: 1,
        sample_count: 20,
        admission_policy_version: 'anti-geo-admission-v1',
        admission_checks: {
          precision: true,
          recall: true,
          false_positive_rate: true,
          brier_score: true,
          expected_calibration_error: true,
          explanation_completeness: true,
        },
        admission_passed: true,
        model_admission_state: null,
        metrics: {
          precision: '1',
          recall: '1',
          false_positive_rate: '0',
          brier_score: '0.01',
          expected_calibration_error: '0.1',
          explanation_completeness_rate: '1',
          sample_count: 20,
          positive_count: 10,
          negative_count: 10,
          dataset_version: 'external-approved-v1',
          scorer_version: 'anti-geo-v1',
          evaluation_sha256: 'b'.repeat(64),
        },
        required_explanation_fields: [
          'evidence_sufficiency',
          'independent_source_count',
          'uncertainty',
          'rule_version',
          'model_version',
          'human_verdict_state',
        ],
        created_at: '2026-07-25T00:02:00Z',
        token: 'Bearer evaluation-response-canary',
      };
      const admission = {
        pub_id: 'madm_safe',
        evaluation_run_pub_id: mismatchedWriteReceipts ? 'eval_wrong_target' : 'eval_safe',
        scorer_version: 'anti-geo-v1',
        state: 'admitted',
        rationale: 'independent review complete',
        admitted_at: '2026-07-25T00:03:00Z',
        revoked_at: null,
        profile_path: '/secret/profile/admission-response-canary',
      };
      const body = path.endsWith('/approve')
        ? {
            ...commonDataset,
            pub_id: mismatchedWriteReceipts ? 'dset_wrong_target' : 'dset_safe',
          }
        : path.endsWith('/admit')
          ? admission
          : outbound.method === 'POST' && path.endsWith('/evaluation-datasets')
            ? {
                ...commonDataset,
                version: mismatchedWriteReceipts
                  ? 'wrong-registration-version'
                  : 'external-candidate-v2',
                state: 'draft',
                approved_at: null,
              }
            : outbound.method === 'POST' && path.endsWith('/runs')
              ? commonRun
              : path.endsWith('/evaluation-datasets')
                ? {
                    data: [commonDataset],
                    page: {
                      next_cursor: null,
                      has_more: false,
                      token: 'Bearer dataset-page-canary',
                    },
                  }
                : path.endsWith('/evaluation-runs')
                  ? {
                      data: [commonRun],
                      page: {
                        next_cursor: null,
                        has_more: false,
                        cookie: 'SESSION=run-page-canary',
                      },
                    }
                  : {
                      data: [admission],
                      page: {
                        next_cursor: null,
                        has_more: false,
                        otp: '824911',
                      },
                    };
      return new Response(JSON.stringify(body), {
        status: outbound.method === 'POST' ? 201 : 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'reviewer-safe',
      'X-Actor-Role': 'reviewer' as const,
    };

    const datasetList = await listEvaluationDatasets(headers, {}, client);
    expect(datasetList.kind).toBe('ready');
    if (datasetList.kind === 'ready') {
      expect(JSON.stringify(datasetList.data)).not.toMatch(/SESSION=|Bearer|cookie|token/i);
    }
    const cases = Array.from({ length: 20 }, (_, index) => ({
      case_digest: index.toString(16).padStart(64, '0'),
      propagation_cluster_digest: (index + 100).toString(16).padStart(64, '0'),
      actual_positive: index < 10,
    }));
    expect(
      (
        await registerEvaluationDataset(
          {
            version: 'external-candidate-v2',
            source_artifact_pub_id: 'evd_safe',
            source_artifact_sha256: 'd'.repeat(64),
            label_policy_version: 'anti-geo-human-label-v1',
            labeler_count: 2,
            cases,
          },
          'dataset-registration-idempotency-safe',
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    expect(
      (
        await approveEvaluationDataset(
          'dset_safe',
          { rationale: 'independent review complete' },
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    expect(
      (
        await runEvaluationDataset(
          'dset_safe',
          {
            scorer_version: 'anti-geo-v1',
            decision_threshold: 0.5,
            calibration_bins: 10,
            training_propagation_cluster_digests: ['e'.repeat(64)],
            predictions: cases.map((item) => ({
              case_digest: item.case_digest,
              probability: item.actual_positive ? 0.9 : 0.1,
              predicted_positive: item.actual_positive,
              explanation_fields: [
                'evidence_sufficiency',
                'independent_source_count',
                'uncertainty',
                'rule_version',
                'model_version',
                'human_verdict_state',
              ],
            })),
          },
          'evaluation-run-idempotency-safe',
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    const runList = await listEvaluationRuns(headers, {}, client);
    expect(runList.kind).toBe('ready');
    if (runList.kind === 'ready') {
      expect(runList.data.data).toHaveLength(1);
      expect(JSON.stringify(runList.data)).not.toMatch(/SESSION=|Bearer|cookie|token/i);
    }
    expect(
      (
        await admitEvaluatedModel(
          'eval_safe',
          { rationale: 'independent review complete' },
          'model-admission-idempotency-safe',
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    const admissionList = await listModelAdmissions(headers, {}, client);
    expect(admissionList.kind).toBe('ready');
    if (admissionList.kind === 'ready') {
      expect(JSON.stringify(admissionList.data)).not.toMatch(
        /profile|admission-response-canary|824911/i,
      );
    }

    const requests = request.mock.calls.map(([input]) => input as Request);
    expect(requests.map((item) => new URL(item.url).pathname)).toEqual([
      '/api/v2/intelligence/evaluation-datasets',
      '/api/v2/intelligence/evaluation-datasets',
      '/api/v2/intelligence/evaluation-datasets/dset_safe/approve',
      '/api/v2/intelligence/evaluation-datasets/dset_safe/runs',
      '/api/v2/intelligence/evaluation-runs',
      '/api/v2/intelligence/evaluation-runs/eval_safe/admit',
      '/api/v2/intelligence/model-admissions',
    ]);
    expect(requests[1]!.headers.get('Idempotency-Key')).toBe(
      'dataset-registration-idempotency-safe',
    );
    expect(requests[3]!.headers.get('Idempotency-Key')).toBe('evaluation-run-idempotency-safe');
    expect(requests[5]!.headers.get('Idempotency-Key')).toBe('model-admission-idempotency-safe');
    for (const outbound of requests) {
      expect(outbound.headers.get('X-Service-Token')).toBeNull();
    }

    mismatchedWriteReceipts = true;
    const registrationBody = {
      version: 'external-candidate-v2',
      source_artifact_pub_id: 'evd_safe',
      source_artifact_sha256: 'd'.repeat(64),
      label_policy_version: 'anti-geo-human-label-v1',
      labeler_count: 2,
      cases,
    };
    const runBody = {
      scorer_version: 'anti-geo-v1',
      decision_threshold: 0.5,
      calibration_bins: 10,
      training_propagation_cluster_digests: ['e'.repeat(64)],
      predictions: cases.map((item) => ({
        case_digest: item.case_digest,
        probability: item.actual_positive ? 0.9 : 0.1,
        predicted_positive: item.actual_positive,
        explanation_fields: [
          'evidence_sufficiency',
          'independent_source_count',
          'uncertainty',
          'rule_version',
          'model_version',
          'human_verdict_state',
        ],
      })),
    };
    expect(
      await registerEvaluationDataset(
        registrationBody,
        'mismatch-registration-key',
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await approveEvaluationDataset(
        'dset_safe',
        { rationale: 'independent review complete' },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await runEvaluationDataset('dset_safe', runBody, 'mismatch-evaluation-key', headers, client),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await admitEvaluatedModel(
        'eval_safe',
        { rationale: 'independent review complete' },
        'mismatch-admission-key',
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
  });

  it('fails closed when Anti-GEO summary projections contain secret-shaped or invalid fields', () => {
    const dataset = projectEvaluationDatasetPage({
      data: [
        {
          pub_id: 'dset_safe',
          version: 'external-approved-v1',
          dataset_sha256: 'a'.repeat(64),
          state: 'draft',
          case_count: 20,
          positive_count: 10,
          labeler_count: 2,
          submitted_at: '1',
          approved_at: null,
        },
      ],
      page: { next_cursor: 'token=hidden-cursor', has_more: true },
    } as never)!;
    const run = projectEvaluationRunPage({
      data: [
        {
          pub_id: 'eval_safe',
          dataset_pub_id: 'dset_safe',
          scorer_version: 'anti-geo-v1',
          decision_threshold: '0.5',
          calibration_bins: 10,
          training_cluster_manifest_sha256: 'c'.repeat(64),
          training_cluster_count: 0,
          sample_count: 20,
          admission_policy_version: 'anti-geo-admission-v1',
          admission_checks: {
            precision: true,
            recall: true,
            false_positive_rate: true,
            brier_score: true,
            expected_calibration_error: true,
            explanation_completeness: true,
          },
          admission_passed: true,
          model_admission_state: null,
          metrics: {
            precision: '1',
            recall: '1',
            false_positive_rate: '0',
            brier_score: '0.01',
            expected_calibration_error: '0.1',
            explanation_completeness_rate: '1',
            sample_count: 20,
            positive_count: 10,
            negative_count: 10,
            dataset_version: 'external-approved-v1',
            scorer_version: 'anti-geo-v1',
            evaluation_sha256: 'b'.repeat(64),
          },
          required_explanation_fields: [
            'evidence_sufficiency',
            'independent_source_count',
            'uncertainty',
            'rule_version',
            'model_version',
            'human_verdict_state',
          ],
          created_at: '1',
          cookie: 'SESSION=hidden-run',
        },
      ],
      page: { next_cursor: null, has_more: false },
    } as never)!;
    const admission = projectModelAdmissionPage({
      data: [
        {
          pub_id: 'madm_safe',
          evaluation_run_pub_id: 'eval_safe',
          scorer_version: 'anti-geo-v1',
          state: 'admitted',
          rationale: 'independent review complete',
          admitted_at: '1',
          revoked_at: null,
        },
      ],
      page: { next_cursor: null, has_more: false },
    } as never)!;

    expect(dataset).toEqual({
      data: [],
      page: { next_cursor: null, has_more: false },
      projection: { total: 1, shown: 0, invalid: true },
    });
    expect(run.data).toEqual([]);
    expect(admission.data).toEqual([]);
    expect(JSON.stringify({ dataset, run, admission })).not.toMatch(/SESSION=|token=/i);
  });

  it('bounds Intelligence list pages and preserves invalid-row and cursor facts', async () => {
    const dataset = (index: number) => ({
      pub_id: `dset_boundary_${index}`,
      version: index === 1 ? 'Bearer dataset-list-secret' : `external-v${index}`,
      dataset_sha256: 'a'.repeat(64),
      state: 'draft',
      case_count: 20,
      positive_count: 10,
      labeler_count: 2,
      submitted_at: '2026-07-25T08:00:00Z',
      approved_at: null,
      cookie: 'SESSION=dataset-list-extension-secret',
    });
    const run = (index: number) => ({
      pub_id: `eval_boundary_${index}`,
      dataset_pub_id: `dset_boundary_${index}`,
      scorer_version: index === 1 ? 'Cookie=run-list-secret' : `anti-geo-v${index}`,
      decision_threshold: '0.5',
      calibration_bins: 10,
      training_cluster_manifest_sha256: 'b'.repeat(64),
      training_cluster_count: 0,
      sample_count: 20,
      admission_policy_version: 'anti-geo-admission-v1',
      admission_checks: {
        precision: true,
        recall: true,
        false_positive_rate: true,
        brier_score: true,
        expected_calibration_error: true,
        explanation_completeness: true,
      },
      admission_passed: true,
      model_admission_state: null,
      metrics: {
        precision: '1',
        recall: '1',
        false_positive_rate: '0',
        brier_score: '0.01',
        expected_calibration_error: '0.1',
        explanation_completeness_rate: '1',
        sample_count: 20,
        positive_count: 10,
        negative_count: 10,
        dataset_version: `external-v${index}`,
        scorer_version: index === 1 ? 'Cookie=run-list-secret' : `anti-geo-v${index}`,
        evaluation_sha256: 'c'.repeat(64),
      },
      required_explanation_fields: [
        'evidence_sufficiency',
        'independent_source_count',
        'uncertainty',
        'rule_version',
        'model_version',
        'human_verdict_state',
      ],
      created_at: '2026-07-25T09:00:00Z',
      profile_path: '/secret/profile/run-list-extension-secret',
    });
    const admission = (index: number) => ({
      pub_id: `madm_boundary_${index}`,
      evaluation_run_pub_id: `eval_boundary_${index}`,
      scorer_version: `anti-geo-v${index}`,
      state: 'admitted',
      rationale: index === 1 ? 'Bearer admission-list-secret' : `独立复核 ${index}`,
      admitted_at: '2026-07-25T10:00:00Z',
      revoked_at: null,
      otp: 824911,
    });
    const investigation = (index: number) => ({
      pub_id: `inv_boundary_${index}`,
      title: index === 1 ? 'Bearer investigation-list-secret' : `安全案件 ${index}`,
      state: 'review',
      access_class: 'customer_private',
      created_at: '2026-07-25T08:00:00Z',
      updated_at: '2026-07-25T09:00:00Z',
      claim_count: 1,
      source_cluster_count: 1,
      probability: '0.5',
      latest_verdict: null,
      proxy_password: 'investigation-list-extension-secret',
    });
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL((input as Request).url);
      expect(url.searchParams.get('limit')).toBe('2');
      const body = url.pathname.endsWith('/evaluation-datasets')
        ? {
            data: [dataset(0), dataset(1), dataset(2)],
            page: { next_cursor: 'dset_cursor_boundary_02', has_more: true },
          }
        : url.pathname.endsWith('/evaluation-runs')
          ? {
              data: [run(0), run(1), run(2)],
              page: { next_cursor: 'eval_cursor_boundary_02', has_more: true },
            }
          : url.pathname.endsWith('/model-admissions')
            ? {
                data: [admission(0), admission(1), admission(2)],
                page: { next_cursor: 'madm_cursor_boundary_02', has_more: true },
              }
            : {
                data: [investigation(0), investigation(1), investigation(2)],
                page: { next_cursor: 'inv_cursor_boundary_02', has_more: true },
              };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'reviewer-safe',
      'X-Actor-Role': 'reviewer' as const,
    };
    const pages = [
      await listEvaluationDatasets(headers, { limit: 2 }, client),
      await listEvaluationRuns(headers, { limit: 2 }, client),
      await listModelAdmissions(headers, { limit: 2 }, client),
      await listInvestigations(headers, { limit: 2 }, client),
    ];

    for (const page of pages) {
      expect(page).toMatchObject({
        kind: 'ready',
        data: {
          data: [expect.any(Object)],
          projection: { total: 3, shown: 1, invalid: true },
          page: { has_more: true },
        },
      });
    }
    expect(request).toHaveBeenCalledTimes(4);
    expect(JSON.stringify(pages)).not.toMatch(
      /Bearer|Cookie|SESSION=|profile_path|proxy_password|824911|list-secret/i,
    );
  });

  it('uses generated customer confirmation paths with bounded cursor queries and idempotency', async () => {
    const request = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      const isAsset = url.pathname.endsWith('/asset-confirmations');
      const isWrite = (input instanceof Request ? input.method : init?.method) === 'POST';
      const body = isAsset
        ? {
            pub_id: 'acv_safe',
            project_pub_id: 'prj_safe',
            revision: 2,
            brand_name: '安全品牌',
            website: 'https://example.test',
            product_name: '安全产品',
            competitor_name: '安全竞品',
            prohibited_claim: '未经验证的第一',
            created_at: '2026-07-25T00:00:00Z',
            cookie: 'SESSION=asset-confirmation-response-canary',
          }
        : {
            pub_id: 'cpv_safe',
            project_pub_id: 'prj_safe',
            revision: 2,
            company_name: '安全企业',
            contact_role: '品牌负责人',
            audience: '企业采购团队',
            public_statement: '可公开核验的安全声明。',
            created_at: '2026-07-25T00:00:00Z',
            token: 'Bearer profile-version-response-canary',
          };
      return new Response(
        JSON.stringify(
          isWrite
            ? body
            : {
                data: [body],
                next_cursor: null,
                profile_path: '/secret/profile/governance-page-canary',
              },
        ),
        {
          status: isWrite ? 201 : 200,
          headers: { 'content-type': 'application/json' },
        },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer-safe',
      'X-Actor-Role': 'customer' as const,
    };

    const profiles = await listClientProfileVersions(
      'prj_safe',
      headers,
      { cursor: 7, limit: 2 },
      client,
    );
    const confirmations = await listAssetConfirmations(
      'prj_safe',
      headers,
      { cursor: 5, limit: 2 },
      client,
    );
    expect(profiles.kind).toBe('ready');
    expect(confirmations.kind).toBe('ready');
    expect(JSON.stringify({ profiles, confirmations })).not.toMatch(
      /SESSION=|Bearer |profile_path|response-canary/i,
    );
    const createdProfile = await createClientProfileVersion(
      'prj_safe',
      {
        company_name: '安全企业',
        contact_role: '品牌负责人',
        audience: '企业采购团队',
        public_statement: '可公开核验的安全声明。',
        truth_confirmed: true,
      },
      headers,
      'profile-idempotency-safe',
      client,
    );
    const createdConfirmation = await createAssetConfirmation(
      'prj_safe',
      {
        brand_name: '安全品牌',
        website: 'https://example.test',
        product_name: '安全产品',
        competitor_name: '安全竞品',
        prohibited_claim: '未经验证的第一',
        truth_confirmed: true,
      },
      headers,
      'asset-idempotency-safe',
      client,
    );
    expect(createdProfile.kind).toBe('ready');
    expect(createdConfirmation.kind).toBe('ready');
    expect(JSON.stringify({ createdProfile, createdConfirmation })).not.toMatch(
      /SESSION=|Bearer |response-canary/i,
    );

    const requests = request.mock.calls.map(([input]) =>
      input instanceof Request ? input : new Request(String(input)),
    );
    expect(requests[0]!.url).toContain('cursor=7');
    expect(requests[0]!.url).toContain('limit=2');
    expect(requests[1]!.url).toContain('cursor=5');
    expect(requests[2]!.headers.get('Idempotency-Key')).toBe('profile-idempotency-safe');
    expect(requests[3]!.headers.get('Idempotency-Key')).toBe('asset-idempotency-safe');
    for (const outbound of requests) expect(outbound.headers.get('X-Service-Token')).toBeNull();
  });

  it('executes requests through generated OpenAPI paths', async () => {
    const request = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(
          JSON.stringify({
            status: 'ok',
            service: 'geo-platform-v2',
            version: 'contract-v1',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
    );
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const { data: result } = await client.GET('/api/v2/health');
    expect(result).toBeTruthy();
    if (!result) throw new Error('missing health response');
    expect(result.status).toBe('ok');
    expect(request.mock.calls[0]?.[0]).toBeInstanceOf(Request);
    const outbound = request.mock.calls[0]?.[0] as Request;
    expect(outbound.url).toMatch(/\/api\/v2\/health$/);
    expect(outbound.headers.get('Accept')).toBe('application/json');
    expect(outbound.cache).toBe('no-store');
    expect(outbound.redirect).toBe('error');
    expect(outbound.referrerPolicy).toBe('no-referrer');
    expect(client).toBeTruthy();
  });

  it('rejects non-contract response media types before JSON parsing', async () => {
    const mediaTypes = [null, 'text/html', 'application/problem+json'];
    let requestCount = 0;
    const request = vi.fn(async (_input: RequestInfo | URL) => {
      const mediaType = mediaTypes[requestCount++];
      return new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: mediaType ? { 'content-type': mediaType } : {},
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');

    for (const _mediaType of mediaTypes) {
      await expect(getHealth(client)).rejects.toThrow(
        'GEO Platform response media type is unavailable',
      );
    }
    expect(request).toHaveBeenCalledTimes(mediaTypes.length);
  });

  it('bounds decoded JSON bytes before generated parsing or projection', async () => {
    let requestCount = 0;
    const request = vi.fn(async () => {
      requestCount += 1;
      if (requestCount === 1) {
        return new Response('{}', {
          status: 200,
          headers: {
            'content-type': 'application/json',
            'content-length': String(geoApiJsonResponseMaxBytes + 1),
          },
        });
      }
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new Uint8Array(16 * 1024 * 1024));
          controller.enqueue(new Uint8Array(9 * 1024 * 1024));
          controller.enqueue(new Uint8Array(1));
          controller.close();
        },
      });
      return new Response(stream, {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');

    await expect(getHealth(client)).rejects.toThrow('response body is unavailable');
    await expect(getHealth(client)).rejects.toThrow('response body is unavailable');
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('scopes the larger JSON boundary to the lazy self-media artifact', async () => {
    let requestCount = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        requestCount += 1;
        return new Response('{}', {
          status: 200,
          headers: {
            'content-type': 'application/json',
            'content-length': String(
              requestCount === 1 ? geoApiJsonResponseMaxBytes + 1 : mediaWemediaDatasetMaxBytes + 1,
            ),
          },
        });
      }),
    );
    const client = createGeoApiClient('http://127.0.0.1:45200');

    await expect(
      client.GET('/api/v2/datasets/media-wemedia', { parseAs: 'arrayBuffer' }),
    ).resolves.toMatchObject({ response: { status: 200 } });
    await expect(
      client.GET('/api/v2/datasets/media-wemedia', { parseAs: 'arrayBuffer' }),
    ).rejects.toThrow('response body is unavailable');
  });

  it('reconstructs the health probe before its status reaches the shared shell', async () => {
    let requestCount = 0;
    const request = vi.fn(async () => {
      requestCount += 1;
      return new Response(
        JSON.stringify(
          requestCount === 1
            ? {
                status: 'ok',
                service: 'geo-platform-v2',
                version: 'Bearer health-version-canary',
                cookie: 'SESSION=health-root-canary',
              }
            : {
                status: 'token=health-status-canary',
                service: 'geo-platform-v2',
                version: 'contract-v2',
              },
        ),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');

    await expect(getHealth(client)).resolves.toEqual({ status: 'ok' });
    await expect(getHealth(client)).rejects.toThrow('health endpoint is unavailable');
  });

  it('validates identity and project context through generated contracts', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const url = (input as Request).url;
      const body = url.endsWith('/identity/session')
        ? {
            tenant_pub_id: 'tnt_safe',
            user_pub_id: 'usr_safe',
            role: 'customer',
            permissions: ['project:read', 'Bearer bootstrap-permission-canary'],
            cookie: 'SESSION=bootstrap-session-canary',
          }
        : {
            data: [
              {
                pub_id: 'prj_safe',
                tenant_pub_id: 'tnt_safe',
                name: '安全项目',
                state: 'active',
                created_at: '2026-07-24T00:00:00Z',
                updated_at: '2026-07-24T00:00:00Z',
                profile_path: '/secret/profile/bootstrap-project-canary',
              },
            ],
            page: { next_cursor: null, has_more: false },
          };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const result = await getIdentitySession(
      {
        'X-Tenant-Id': 'tnt_safe',
        'X-Actor-Id': 'customer@example.test',
        'X-Actor-Role': 'customer',
      },
      client,
    );
    expect(result.kind).toBe('ready');
    if (result.kind !== 'ready') throw new Error('missing validated session');
    expect(result.session.role).toBe('customer');
    expect(result.session.permissions).toEqual([]);
    expect(result.projects.data[0]?.pub_id).toBe('prj_safe');
    expect(result.projects.projection).toEqual({ total: 1, shown: 1, invalid: false });
    expect(JSON.stringify(result)).not.toMatch(
      /bootstrap-permission-canary|bootstrap-session-canary|bootstrap-project-canary/,
    );
    expect((request.mock.calls[0]?.[0] as Request).headers.get('X-Service-Token')).toBeNull();
  });

  it('normalizes obfuscated secrets before identity bootstrap enters application state', () => {
    const hostileNames = [
      '联系人为 138 0013 8000',
      '联系人为 138\u200b0013\u200b8000',
      '联系人为 １３８００１３８０００',
      '请使用验证码 824-911 完成操作',
      'Bearer%2520encoded-session-canary',
      String.raw`profile_dir=C:\Users\runner\AppData\Local\Chromium\User Data\Profile 1`,
      String.raw`\\server\browser-profiles\tenant-a`,
    ];
    const result = projectIdentityProjectPageBoundary(
      {
        data: hostileNames.map((name, index) => ({
          pub_id: `prj_normalized_dlp_${index}`,
          tenant_pub_id: 'tnt_normalized_dlp',
          name,
          state: 'active',
          created_at: '2026-07-24T00:00:00Z',
          updated_at: '2026-07-24T00:00:00Z',
        })),
        page: { next_cursor: null, has_more: false },
      },
      'tnt_normalized_dlp',
    );

    expect(result?.data.map((project) => project.name)).toEqual(
      hostileNames.map(() => '未命名项目'),
    );
    expect(JSON.stringify(result)).not.toMatch(
      /138 0013|１３８|824-911|encoded-session-canary|profile_dir|User Data|browser-profiles/i,
    );
  });

  it('fails closed on cross-tenant or duplicate project bootstrap rows', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const url = (input as Request).url;
      const project = {
        pub_id: 'prj_bootstrap_safe',
        tenant_pub_id: 'tnt_bootstrap_safe',
        name: '安全项目',
        state: 'active',
        created_at: '2026-07-24T00:00:00Z',
        updated_at: '2026-07-24T00:00:00Z',
      };
      return new Response(
        JSON.stringify(
          url.endsWith('/identity/session')
            ? {
                tenant_pub_id: 'tnt_bootstrap_safe',
                user_pub_id: 'usr_bootstrap_safe',
                role: 'customer',
                permissions: [],
              }
            : {
                data: [
                  {
                    ...project,
                    pub_id: 'prj_cross_tenant',
                    tenant_pub_id: 'tnt_other',
                    token: 'Bearer cross-tenant-bootstrap-canary',
                  },
                  project,
                  {
                    ...project,
                    profile_path: '/secret/profile/duplicate-bootstrap-canary',
                  },
                ],
                page: { next_cursor: null, has_more: false },
              },
        ),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);

    await expect(
      getIdentitySession(
        {
          'X-Tenant-Id': 'tnt_bootstrap_safe',
          'X-Actor-Id': 'customer-safe',
          'X-Actor-Role': 'customer',
        },
        createGeoApiClient('http://127.0.0.1:45200'),
      ),
    ).resolves.toEqual({ kind: 'unavailable' });
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('fails closed on an unauthorized identity without probing projects', async () => {
    const request = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: { code: 'membership_invalid' } }), {
          status: 401,
          headers: { 'content-type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', request);
    const result = await getIdentitySession(
      {
        'X-Tenant-Id': 'tnt_safe',
        'X-Actor-Id': 'unknown@example.test',
        'X-Actor-Role': 'customer',
      },
      createGeoApiClient('http://127.0.0.1:45200'),
    );
    expect(result).toEqual({ kind: 'forbidden' });
    expect(request).toHaveBeenCalledTimes(1);
  });

  it('reads and writes project catalog resources through generated paths and headers', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const resource = {
        pub_id: 'ent_brand_safe',
        project_pub_id: 'prj_safe',
        resource_kind: 'brands',
        version: 1,
        data: { name: '澄明云', website: 'https://example.test' },
      };
      return new Response(JSON.stringify(outbound.method === 'GET' ? [resource] : resource), {
        status: outbound.method === 'GET' ? 200 : 201,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer@example.test',
      'X-Actor-Role': 'customer' as const,
    };
    const listed = await listProjectResources('prj_safe', 'brands', headers, client);
    expect(listed.kind).toBe('ready');
    if (listed.kind !== 'ready') throw new Error('missing catalog response');
    expect(listed.data.data[0]?.data.name).toBe('澄明云');
    expect(listed.data.projection).toEqual({ total: 1, shown: 1, invalid: false });

    const created = await createProjectResource(
      'prj_safe',
      'brands',
      { name: '澄明云', website: 'https://example.test' },
      headers,
      'customer-brand-00000001',
      client,
    );
    expect(created.kind).toBe('ready');
    expect(request).toHaveBeenCalledTimes(2);
    const writeRequest = request.mock.calls[1]?.[0] as Request;
    expect(writeRequest.url).toContain('/api/v2/projects/prj_safe/resources/brands');
    expect(writeRequest.headers.get('Idempotency-Key')).toBe('customer-brand-00000001');
    expect(writeRequest.headers.get('X-Service-Token')).toBeNull();
    expect(await writeRequest.clone().json()).toEqual({
      name: '澄明云',
      website: 'https://example.test',
    });
  });

  it('projects catalog rows by project and kind and rejects mismatched write receipts', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const safe = {
        pub_id: 'ent_brand_safe',
        project_pub_id: 'prj_safe',
        resource_kind: 'brands',
        version: 1,
        data: {
          name: '安全品牌',
          website: 'https://safe.example.test',
          token: 'Bearer nested-catalog-response-canary',
        },
        cookie: 'SESSION=catalog-row-extension-canary',
      };
      const body =
        outbound.method === 'GET'
          ? [
              safe,
              {
                ...safe,
                pub_id: 'ent_cross_project',
                project_pub_id: 'prj_other',
                data: { name: '跨项目品牌', profile_path: '/secret/profile/catalog-canary' },
              },
              {
                ...safe,
                pub_id: 'ent_wrong_kind',
                resource_kind: 'competitors',
                data: { name: '错配目录', otp: '824911' },
              },
            ]
          : {
              ...safe,
              data: { name: '服务端替换品牌', website: 'https://safe.example.test' },
            };
      return new Response(JSON.stringify(body), {
        status: outbound.method === 'GET' ? 200 : 201,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer-safe',
      'X-Actor-Role': 'customer' as const,
    };

    const listed = await listProjectResources('prj_safe', 'brands', headers, client);
    expect(listed).toMatchObject({
      kind: 'ready',
      data: {
        projection: { total: 3, shown: 1, invalid: true },
      },
    });
    if (listed.kind !== 'ready') throw new Error('missing projected catalog');
    expect(listed.data.data).toEqual([
      {
        pub_id: 'ent_brand_safe',
        project_pub_id: 'prj_safe',
        resource_kind: 'brands',
        version: 1,
        data: { name: '安全品牌', website: 'https://safe.example.test/' },
      },
    ]);
    expect(JSON.stringify(listed)).not.toMatch(
      /nested-catalog-response-canary|catalog-row-extension-canary|catalog-canary|824911|Bearer |SESSION=|profile_path|otp/i,
    );

    const created = await createProjectResource(
      'prj_safe',
      'brands',
      { name: '客户提交品牌', website: 'https://safe.example.test' },
      headers,
      'customer-brand-integrity-0001',
      client,
    );
    expect(created).toEqual({ kind: 'unavailable' });
  });

  it('classifies catalog authorization failures without returning response details', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              detail: {
                code: 'project_forbidden',
                token: 'Bearer should-never-cross-the-boundary',
              },
            }),
            { status: 403, headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
    const result = await listProjectResources(
      'prj_hidden',
      'brands',
      {
        'X-Tenant-Id': 'tnt_safe',
        'X-Actor-Id': 'customer@example.test',
        'X-Actor-Role': 'customer',
      },
      createGeoApiClient('http://127.0.0.1:45200'),
    );
    expect(result).toEqual({ kind: 'forbidden' });
    expect(JSON.stringify(result)).not.toContain('Bearer');
  });

  it('reads analytics delta and competitor comparisons through generated query contracts', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      return new Response(
        JSON.stringify(
          outbound.url.includes('/delta')
            ? { mention_rate: { current: 0.75, previous: 0.5, delta: 0.25 } }
            : [
                {
                  competitor: '安全竞品',
                  mention_rate: 0.625,
                  mention_count: 5,
                  answer_count: 8,
                },
              ],
        ),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer@example.test',
      'X-Actor-Role': 'customer' as const,
    };
    expect(
      (await getAnalyticsDelta('prj_safe', '2026-07-01', '2026-07-25', headers, client)).kind,
    ).toBe('ready');
    expect(
      (
        await getAnalyticsCompetitors(
          'prj_safe',
          '2026-07-01',
          '2026-07-25',
          { model: 'doubao', region: 'east', mode: 'deep' },
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    const deltaRequest = request.mock.calls[0]?.[0] as Request;
    const competitorRequest = request.mock.calls[1]?.[0] as Request;
    expect(deltaRequest.url).toContain(
      '/api/v2/analytics/delta?project_pub_id=prj_safe&start=2026-07-01&end=2026-07-25',
    );
    expect(competitorRequest.url).toContain('/api/v2/analytics/competitors?');
    expect(competitorRequest.url).toContain('model=doubao');
    expect(competitorRequest.url).toContain('region=east');
    expect(competitorRequest.url).toContain('mode=deep');
    expect(competitorRequest.headers.get('X-Service-Token')).toBeNull();
  });

  it('reconstructs bounded analytics monitoring responses before they cross the browser boundary', async () => {
    const metric = (name: string, extension: Record<string, unknown> = {}) => ({
      metric: name,
      value: name === 'average_rank' ? 2 : 0.5,
      numerator: 2,
      denominator: 4,
      state: 'ready',
      metric_version: 'metric-v1',
      scorer_version: 'scorer-v1',
      filter_hash: 'a'.repeat(64),
      trace_tokens: ['Bearer analytics-trace-canary'],
      ...extension,
    });
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const pathname = new URL((input as Request).url).pathname;
      const body = pathname.endsWith('/overview')
        ? [
            metric('mention_rate', { profile_path: '/secret/profile/overview-canary' }),
            metric('average_rank'),
            metric('top3_rate'),
            metric('citation_coverage', { state: 'Cookie=overview-state-canary' }),
            metric('mention_rate', { token: 'Bearer overview-limit-canary' }),
          ]
        : pathname.endsWith('/breakdown')
          ? [
              {
                group_by: 'question',
                question_pub_id: 'qry_safe',
                question_text: '安全问题',
                answer_count: 4,
                mentioned_count: 2,
                mention_rate: 0.5,
                average_rank: 2,
                citation_coverage: 0.5,
                profile_path: '/secret/profile/breakdown-canary',
              },
              {
                group_by: 'model',
                model: 'wrong-group',
                answer_count: 4,
                mentioned_count: 2,
                mention_rate: 0.5,
                average_rank: 2,
                citation_coverage: 0.5,
              },
              {
                group_by: 'question',
                question_pub_id: 'Cookie=breakdown-question-canary',
                question_text: '不安全问题',
                answer_count: 4,
                mentioned_count: 2,
                mention_rate: 0.5,
                average_rank: 2,
                citation_coverage: 0.5,
              },
            ]
          : pathname.endsWith('/delta')
            ? {
                mention_rate: {
                  current: 0.5,
                  previous: 0.4,
                  delta: 0.1,
                  token: 'Bearer delta-canary',
                },
                average_rank: {
                  current: 2,
                  previous: 'Cookie=delta-previous-canary',
                  delta: 0.25,
                },
                profile_path: '/secret/profile/delta-root-canary',
              }
            : [
                {
                  competitor: '安全竞品',
                  mention_rate: 0.5,
                  mention_count: 2,
                  answer_count: 4,
                  proxy_password: 'competitor-proxy-password-canary',
                },
                {
                  competitor: 'Bearer competitor-name-canary',
                  mention_rate: 0.5,
                  mention_count: 2,
                  answer_count: 4,
                },
              ];
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer@example.test',
      'X-Actor-Role': 'customer' as const,
    };

    const overview = await getAnalyticsOverview(
      'prj_safe',
      '2026-07-01',
      '2026-07-25',
      {},
      headers,
      client,
    );
    const breakdown = await getAnalyticsBreakdown(
      'prj_safe',
      '2026-07-01',
      '2026-07-25',
      'question',
      {},
      headers,
      client,
    );
    const delta = await getAnalyticsDelta('prj_safe', '2026-07-01', '2026-07-25', headers, client);
    const competitors = await getAnalyticsCompetitors(
      'prj_safe',
      '2026-07-01',
      '2026-07-25',
      {},
      headers,
      client,
    );

    expect(overview).toMatchObject({
      kind: 'ready',
      data: { projection: { total: 5, shown: 3, invalid: true } },
    });
    expect(overview.kind === 'ready' ? overview.data.data[0] : null).not.toHaveProperty(
      'trace_tokens',
    );
    expect(breakdown).toMatchObject({
      kind: 'ready',
      data: { projection: { total: 3, shown: 1, invalid: true } },
    });
    expect(delta).toEqual({
      kind: 'ready',
      data: {
        data: { mention_rate: { current: 0.5, previous: 0.4, delta: 0.1 } },
        projection: { total: 2, shown: 1, invalid: true },
      },
    });
    expect(competitors).toMatchObject({
      kind: 'ready',
      data: { projection: { total: 2, shown: 1, invalid: true } },
    });
    expect(JSON.stringify({ overview, breakdown, delta, competitors })).not.toMatch(
      /Bearer|Cookie|proxy_password|profile_path|canary/i,
    );
  });

  it('projects answer pages and nested relations before application state', async () => {
    const answer = {
      pub_id: 'ans_boundary_safe',
      project_pub_id: 'prj_safe',
      run_pub_id: 'run_boundary_safe',
      config_version_pub_id: 'cfv_boundary_safe',
      query_pub_id: 'qry_boundary_safe',
      query_text: '安全问题',
      response_text: '安全回答',
      model: 'doubao',
      region: '上海',
      mode: 'deep',
      eligible: true,
      degraded: false,
      capture_time: '2026-07-25T08:00:00Z',
      mentioned: null,
      rank: null,
      sentiment: null,
      recommendation_state: null,
      citation_count: 201,
    };
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const pathname = new URL((input as Request).url).pathname;
      if (pathname.endsWith('/relations')) {
        return new Response(
          JSON.stringify({
            answer_pub_id: 'ans_boundary_safe',
            citations: Array.from({ length: 201 }, (_, index) => ({
              pub_id: `cit_boundary_${index}`,
              ordinal: index + 1,
              canonical_url:
                index === 199
                  ? 'https://user:proxy-password@source.example/article'
                  : `https://source${index}.example/article`,
              host: `source${index}.example`,
              title: `安全来源 ${index}`,
              cited_text: index === 0 ? 'Bearer citation-prose-canary' : null,
              own_source: false,
              content_hash: 'c'.repeat(64),
            })),
            evidence: Array.from({ length: 201 }, (_, index) => ({
              pub_id: `evd_boundary_${index}`,
              relation_type: 'visualizes',
              kind: 'answer_screenshot',
              access_class: 'customer_private',
              sha256: 'a'.repeat(64),
              mime_type: 'image/png',
              byte_size: 512,
              source_url: 'https://capture.example/answer',
              capture_time: '2026-07-25T08:00:00Z',
              anchors:
                index === 0
                  ? Array.from({ length: 201 }, (__, anchorIndex) => ({
                      pub_id: `anch_boundary_${anchorIndex}`,
                      text_start: anchorIndex,
                      text_end: anchorIndex + 1,
                      bbox: anchorIndex === 0 ? { cookie: 'SESSION=anchor-bbox-canary' } : null,
                      page_number: null,
                      quote_hash: 'd'.repeat(64),
                    }))
                  : [],
              object_key: index === 200 ? 'Cookie=relation-evidence-canary' : undefined,
            })),
            history: Array.from({ length: 201 }, (_, index) => ({
              pub_id: `diff_boundary_${index}`,
              before_evidence_pub_id: `evd_before_${index}`,
              after_evidence_pub_id: `evd_after_${index}`,
              similarity: 0.75,
              visual_diff_available: true,
              created_at: index === 199 ? '1' : '2026-07-25T08:00:00Z',
              profile_path: index === 199 ? '/secret/profile/relation-history-canary' : undefined,
            })),
            cookie: 'SESSION=relation-root-canary',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      return new Response(
        JSON.stringify({
          data: [
            {
              ...answer,
              trace_token: 'Bearer answer-visible-extension-canary',
              profile_path: '/secret/profile/answer-visible-canary',
            },
            {
              ...answer,
              pub_id: 'ans_boundary_invalid',
              response_text: 'Cookie=answer-response-canary',
            },
            { ...answer, pub_id: 'ans_boundary_over_limit' },
          ],
          page: { next_cursor: null, has_more: false, token: 'Bearer page-canary' },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer@example.test',
      'X-Actor-Role': 'customer' as const,
    };

    const page = await listAnalyticsAnswers('prj_safe', { limit: 2 }, headers, client);
    const relations = await getAnalyticsAnswerRelations('ans_boundary_safe', headers, client);

    expect(page).toMatchObject({
      kind: 'ready',
      data: {
        data: [{ pub_id: 'ans_boundary_safe', response_text: '安全回答' }],
        page: { next_cursor: null, has_more: false },
        projection: { total: 3, shown: 1, invalid: true },
      },
    });
    expect(relations).toMatchObject({
      kind: 'ready',
      data: {
        answer_pub_id: 'ans_boundary_safe',
        projection: {
          citations: { total: 201, shown: 198, invalid: true },
          evidence: { total: 201, shown: 200, invalid: false },
          anchors: { total: 201, shown: 200, invalid: false },
          history: { total: 201, shown: 199, invalid: true },
        },
      },
    });
    if (relations.kind === 'ready') {
      expect(relations.data.citations).toHaveLength(198);
      expect(relations.data.evidence).toHaveLength(200);
      expect(relations.data.evidence[0]?.anchors).toHaveLength(200);
      expect(relations.data.history).toHaveLength(199);
    }
    expect(JSON.stringify({ page, relations })).not.toMatch(
      /proxy-password|Bearer|Cookie|canary|profile_path|SESSION=/i,
    );
  });

  it('passes an exact Answer ID through the generated analytics query boundary', async () => {
    const request = vi.fn(
      async (_input: RequestInfo | URL) =>
        new Response(
          JSON.stringify({
            data: [
              {
                pub_id: 'ans_lookup_safe',
                project_pub_id: 'prj_safe',
                run_pub_id: 'run_lookup_safe',
                config_version_pub_id: 'cfv_lookup_safe',
                query_pub_id: 'qry_lookup_safe',
                query_text: '按 Answer ID 定位',
                response_text: '定位成功',
                model: 'doubao',
                region: 'Beijing',
                mode: 'normal',
                eligible: true,
                degraded: false,
                capture_time: '2026-08-03T05:00:00Z',
                mentioned: true,
                rank: 1,
                sentiment: 'positive',
                recommendation_state: 'experimental',
                citation_count: 1,
              },
            ],
            page: { next_cursor: null, has_more: false },
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
    );
    vi.stubGlobal('fetch', request);
    const result = await listAnalyticsAnswers(
      'prj_safe',
      { answerPubId: 'ans_lookup_safe', limit: 2 },
      {
        'X-Tenant-Id': 'tnt_safe',
        'X-Actor-Id': 'customer@example.test',
        'X-Actor-Role': 'customer',
      },
      createGeoApiClient('http://127.0.0.1:45200'),
    );

    expect(result).toMatchObject({
      kind: 'ready',
      data: { data: [{ pub_id: 'ans_lookup_safe' }] },
    });
    const outbound = request.mock.calls[0]![0] as Request;
    expect(new URL(outbound.url).searchParams.get('answer_pub_id')).toBe('ans_lookup_safe');
  });

  it('projects report deliveries and evidence asset pages before application state', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const pathname = new URL((input as Request).url).pathname;
      const body = pathname.endsWith('/deliveries')
        ? [
            {
              pub_id: 'dlv_boundary_safe',
              report_pub_id: 'rpt_safe',
              recipient_pub_id: 'usr_customer_safe',
              delivered_at: '2026-07-25T08:00:00Z',
              confirmed_at: '2026-07-25T08:01:00Z',
              confirmation_comment: 'Bearer delivery-comment-canary',
              cookie: 'SESSION=delivery-extension-canary',
            },
            {
              pub_id: 'dlv_boundary_cross_report',
              report_pub_id: 'rpt_other',
              recipient_pub_id: 'usr_customer_safe',
              delivered_at: '2026-07-25T08:00:00Z',
              confirmed_at: null,
              confirmation_comment: null,
              profile_path: '/secret/profile/delivery-cross-report-canary',
            },
          ]
        : {
            data: [
              {
                pub_id: 'evd_boundary_safe',
                kind: 'answer_screenshot',
                mime_type: 'image/png',
                capture_time: '2026-07-25T08:00:00Z',
                sha256: 'a'.repeat(64),
                object_key: 'Cookie=evidence-object-key-canary',
                otp: '318294',
              },
              {
                pub_id: 'evd_boundary_invalid',
                kind: 'answer_screenshot',
                mime_type: 'image/png',
                capture_time: '1',
                sha256: 'b'.repeat(64),
                token: 'Bearer evidence-invalid-canary',
              },
              {
                pub_id: 'evd_boundary_over_limit',
                kind: 'answer_screenshot',
                mime_type: 'image/png',
                capture_time: '2026-07-25T08:00:00Z',
                sha256: 'c'.repeat(64),
              },
            ],
            page: {
              next_cursor: 'evd_cursor_safe_02',
              has_more: true,
              cookie: 'SESSION=evidence-page-canary',
            },
            proxy_password: 'evidence-root-proxy-password-canary',
          };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer@example.test',
      'X-Actor-Role': 'customer' as const,
    };

    const deliveries = await listReportDeliveries('rpt_safe', headers, client);
    const evidence = await listEvidenceAssets(headers, { limit: 2 }, client);

    expect(deliveries).toEqual({
      kind: 'ready',
      data: {
        data: [
          {
            pub_id: 'dlv_boundary_safe',
            report_pub_id: 'rpt_safe',
            recipient_pub_id: 'usr_customer_safe',
            delivered_at: '2026-07-25T08:00:00Z',
            confirmed_at: '2026-07-25T08:01:00Z',
            confirmation_comment: null,
          },
        ],
        projection: { total: 2, shown: 1, invalid: true },
      },
    });
    expect(evidence).toEqual({
      kind: 'ready',
      data: {
        data: [
          {
            pub_id: 'evd_boundary_safe',
            kind: 'answer_screenshot',
            mime_type: 'image/png',
            capture_time: '2026-07-25T08:00:00Z',
            sha256: 'a'.repeat(64),
          },
        ],
        page: { next_cursor: 'evd_cursor_safe_02', has_more: true },
        projection: { total: 3, shown: 1, invalid: true },
      },
    });
    expect(JSON.stringify({ deliveries, evidence })).not.toMatch(
      /Bearer|Cookie|SESSION=|proxy-password|profile_path|318294|canary/i,
    );
  });

  it('reconstructs nested report details and preserves per-collection projection facts', async () => {
    const versionPubId = 'rptv_boundary_safe';
    const request = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            pub_id: 'rpt_boundary_safe',
            project_pub_id: 'prj_safe',
            title: '安全报告',
            state: 'review',
            created_at: '2026-07-25T08:00:00Z',
            updated_at: '2026-07-25T08:01:00Z',
            versions: [
              {
                pub_id: versionPubId,
                version_number: 1,
                window_start: '2026-07-01T00:00:00Z',
                window_end: '2026-07-25T00:00:00Z',
                filters: { region: 'east' },
                metric_version: 'metric-v1',
                scorer_version: 'scorer-v1',
                fact_snapshot_hash: 'a'.repeat(64),
                status: 'review',
                components: [
                  {
                    pub_id: 'rptc_boundary_safe',
                    report_version_pub_id: versionPubId,
                    component_type: 'section',
                    ordinal: 0,
                    payload: {
                      title: '安全章节',
                      body: '安全正文',
                      evidence_pub_ids: ['evd_boundary_safe'],
                    },
                    source: 'human',
                    created_at: '2026-07-25T08:00:00Z',
                    profile_path: '/secret/profile/component-extension-canary',
                  },
                  {
                    pub_id: 'rptc_boundary_invalid',
                    report_version_pub_id: versionPubId,
                    component_type: 'section',
                    ordinal: 1,
                    payload: {
                      title: '不安全章节',
                      body: 'report13800138000component-phone-canary',
                    },
                    source: 'ai',
                    created_at: '2026-07-25T08:00:00Z',
                  },
                  {
                    pub_id: 'rptc_boundary_otp_invalid',
                    report_version_pub_id: versionPubId,
                    component_type: 'section',
                    ordinal: 2,
                    payload: {
                      title: '不安全 OTP 章节',
                      body: '请使用验证码 824911 完成原生挑战',
                    },
                    source: 'ai',
                    created_at: '2026-07-25T08:00:00Z',
                  },
                  {
                    pub_id: 'rptc_boundary_numeric_otp_invalid',
                    report_version_pub_id: versionPubId,
                    component_type: 'section',
                    ordinal: 3,
                    payload: {
                      title: '不安全数值 OTP 章节',
                      body: '秘密作为结构化数值返回',
                      challenge: 824911,
                    },
                    source: 'ai',
                    created_at: '2026-07-25T08:00:00Z',
                  },
                  {
                    pub_id: 'rptc_boundary_numeric_phone_invalid',
                    report_version_pub_id: versionPubId,
                    component_type: 'section',
                    ordinal: 4,
                    payload: {
                      title: '不安全数值手机号章节',
                      body: '秘密作为结构化数值返回',
                      contact: 13800138000,
                    },
                    source: 'ai',
                    created_at: '2026-07-25T08:00:00Z',
                  },
                  ...[
                    ['rptc_boundary_fullwidth_key_invalid', 'ｔｏｋｅｎ', 'fullwidth-key-canary'],
                    [
                      'rptc_boundary_zero_width_key_invalid',
                      'to\u200bken',
                      'zero-width-key-canary',
                    ],
                    ['rptc_boundary_encoded_key_invalid', 'profile%255Fpath', 'encoded-key-canary'],
                  ].map(([pubId, secretKey, canary], index) => ({
                    pub_id: pubId,
                    report_version_pub_id: versionPubId,
                    component_type: 'section',
                    ordinal: 5 + index,
                    payload: {
                      title: '不安全键名章节',
                      body: '键名必须在结构化状态前规范化',
                      [secretKey!]: canary,
                    },
                    source: 'ai',
                    created_at: '2026-07-25T08:00:00Z',
                  })),
                ],
                frozen_facts: [
                  {
                    pub_id: 'rptf_boundary_safe',
                    report_version_pub_id: versionPubId,
                    ordinal: 0,
                    payload: { metric: 'mention_rate', value: 0.5 },
                    payload_hash: 'b'.repeat(64),
                    created_at: '2026-07-25T08:00:00Z',
                    cookie: 'SESSION=fact-extension-canary',
                  },
                ],
                artifacts: [
                  {
                    pub_id: 'rpta_boundary_safe',
                    report_version_pub_id: versionPubId,
                    format: 'pdf',
                    evidence_pub_id: 'evd_boundary_safe',
                    mime_type: 'application/pdf',
                    byte_size: 512,
                    sha256: 'c'.repeat(64),
                    created_at: '2026-07-25T08:00:00Z',
                    object_key: 'Cookie=artifact-object-key-canary',
                  },
                ],
                evidence_bindings: [],
                reviews: [],
                comments: [],
                events: [],
                token: 'Bearer version-extension-canary',
              },
            ],
            optimization_actions: [
              {
                pub_id: 'act_boundary_safe',
                description: '安全优化行动',
                owner_pub_id: 'usr_owner_safe',
                state: 'proposed',
                baseline: { mention_rate: 0.5 },
                outcome: null,
                created_at: '2026-07-25T08:00:00Z',
                updated_at: '2026-07-25T08:00:00Z',
                effect_retests: [
                  {
                    pub_id: 'rts_boundary_safe',
                    action_pub_id: 'act_boundary_safe',
                    measured_at: '2026-07-25T08:00:00Z',
                    result: { delta: 1.5 },
                    recorded_by_pub_id: 'usr_owner_safe',
                    created_at: '2026-07-25T08:00:00Z',
                    proxy_password: 'retest-proxy-password-canary',
                  },
                ],
                otp: '429155',
              },
            ],
            cookie: 'SESSION=report-root-canary',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
    );
    vi.stubGlobal('fetch', request);
    const result = await getReport(
      'rpt_boundary_safe',
      {
        'X-Tenant-Id': 'tnt_safe',
        'X-Actor-Id': 'reviewer@example.test',
        'X-Actor-Role': 'reviewer',
      },
      createGeoApiClient('http://127.0.0.1:45200'),
    );

    expect(result).toMatchObject({
      kind: 'ready',
      data: {
        pub_id: 'rpt_boundary_safe',
        versions: [
          {
            pub_id: versionPubId,
            components: [{ pub_id: 'rptc_boundary_safe' }],
            frozen_facts: [{ pub_id: 'rptf_boundary_safe' }],
            artifacts: [{ pub_id: 'rpta_boundary_safe' }],
          },
        ],
        optimization_actions: [
          {
            pub_id: 'act_boundary_safe',
            effect_retests: [{ pub_id: 'rts_boundary_safe' }],
          },
        ],
        projection: {
          versions: { total: 1, shown: 1, invalid: false },
          optimization_actions: { total: 1, shown: 1, invalid: false },
          version_collections: {
            [versionPubId]: {
              components: { total: 8, shown: 1, invalid: true },
              section_evidence_ids: { total: 1, shown: 1, invalid: false },
            },
          },
          action_retests: {
            act_boundary_safe: { total: 1, shown: 1, invalid: false },
          },
        },
      },
    });
    expect(JSON.stringify(result)).not.toMatch(
      /Bearer|Cookie|SESSION=|proxy-password|profile_path|13800138000|824911|429155|fullwidth-key-canary|zero-width-key-canary|encoded-key-canary|canary/i,
    );
  });

  it('binds report artifact bytes to the projected MIME, size and SHA-256', async () => {
    const pdf = '%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF';
    const request = vi.fn(
      async (_input: RequestInfo | URL) =>
        new Response(pdf, {
          status: 200,
          headers: { 'content-type': 'application/pdf' },
        }),
    );
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'reviewer@example.test',
      'X-Actor-Role': 'reviewer' as const,
    };
    const integrity = {
      byteSize: 44,
      mimeType: 'application/pdf',
      sha256: '5685e2d63d2a3b750e0850b8654c06f87fe9a1b138525deef264166e4152efbc',
    };

    const ready = await getReportArtifact(
      'rpt_boundary_safe',
      'rptv_boundary_safe',
      'pdf',
      integrity,
      headers,
      client,
    );
    expect(ready).toMatchObject({
      kind: 'ready',
      data: {
        byteSize: 44,
        mimeType: 'application/pdf',
        sha256: integrity.sha256,
      },
    });
    if (ready.kind === 'ready') {
      expect(await ready.data.blob.text()).toBe(pdf);
    }
    const artifactRequest = request.mock.calls[0]?.[0] as Request;
    expect(artifactRequest.headers.get('Accept')).toBe('application/pdf');
    expect(artifactRequest.cache).toBe('no-store');
    expect(artifactRequest.redirect).toBe('error');
    expect(artifactRequest.referrerPolicy).toBe('no-referrer');

    await expect(
      getReportArtifact(
        'rpt_boundary_safe',
        'rptv_boundary_safe',
        'pdf',
        { ...integrity, sha256: '0'.repeat(64) },
        headers,
        client,
      ),
    ).resolves.toEqual({ kind: 'unavailable' });
    await expect(
      getReportArtifact(
        'rpt_boundary_safe',
        'rptv_boundary_safe',
        'pdf',
        { ...integrity, mimeType: 'text/html' },
        headers,
        client,
      ),
    ).resolves.toEqual({ kind: 'unavailable' });
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('binds private evidence image bytes to MIME, size and SHA-256', async () => {
    const payload = 'PNG evidence';
    const request = vi.fn(
      async () =>
        new Response(payload, {
          status: 200,
          headers: { 'content-type': 'image/png' },
        }),
    );
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer@example.test',
      'X-Actor-Role': 'customer' as const,
    };
    const integrity = {
      byteSize: 12,
      mimeType: 'image/png',
      sha256: 'cd16052cdd2b4e25a5d19f1be13ad31eb6ec1af6dcdd45bdd6a94600a58c8c93',
    };
    const ready = await getEvidenceAssetContent('evd_boundary_safe', integrity, headers, client);
    expect(ready).toMatchObject({ kind: 'ready', data: integrity });
    if (ready.kind === 'ready') expect(await ready.data.blob.text()).toBe(payload);
    await expect(
      getEvidenceAssetContent(
        'evd_boundary_safe',
        { ...integrity, sha256: '0'.repeat(64) },
        headers,
        client,
      ),
    ).resolves.toEqual({ kind: 'unavailable' });
  });

  it('reconstructs investigation detail and history before browser state', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL((input as Request).url).pathname;
      const body = path.endsWith('/page-history')
        ? [
            {
              content_pub_id: 'cnt_boundary_safe',
              version_pub_id: 'cntv_boundary_safe_01',
              canonical_url: 'https://source.example/history',
              title: '安全历史页面',
              version_number: 1,
              body_hash: 'a'.repeat(64),
              evidence_pub_id: 'evd_boundary_safe_01',
              captured_at: '2026-07-25T08:00:00Z',
              published_at: null,
              snapshot_pub_id: 'snap_boundary_safe_01',
              snapshot_number: 1,
              normalized_text_hash: 'b'.repeat(64),
              perceptual_hash: null,
              cookie: 'SESSION=history-extension-secret',
            },
            {
              content_pub_id: 'cnt_boundary_unsafe',
              version_pub_id: 'cntv_boundary_unsafe_02',
              canonical_url: 'https://source.example/history',
              title: 'Bearer history-title-secret',
              version_number: 2,
              body_hash: 'c'.repeat(64),
              evidence_pub_id: null,
              captured_at: '2026-07-25T08:01:00Z',
              published_at: null,
              snapshot_pub_id: null,
              snapshot_number: null,
              normalized_text_hash: null,
              perceptual_hash: null,
            },
          ]
        : path.endsWith('/visual-diffs')
          ? [
              {
                pub_id: 'diff_boundary_safe',
                content_pub_id: 'cnt_boundary_safe',
                before_version_pub_id: 'cntv_boundary_safe_01',
                after_version_pub_id: 'cntv_boundary_safe_02',
                before_evidence_pub_id: 'evd_boundary_safe_01',
                after_evidence_pub_id: 'evd_boundary_safe_02',
                text_diff: {
                  before_hash: 'a'.repeat(64),
                  after_hash: 'b'.repeat(64),
                  unified: 'Bearer diff-extension-secret',
                },
                similarity: '0.75',
                visual_diff_available: false,
                created_at: '2026-07-25T08:02:00Z',
              },
              {
                pub_id: 'diff_boundary_unsafe',
                content_pub_id: 'cnt_boundary_safe',
                before_version_pub_id: 'cntv_boundary_safe_01',
                after_version_pub_id: 'cntv_boundary_safe_02',
                before_evidence_pub_id: 'evd_boundary_safe_01',
                after_evidence_pub_id: 'Bearer diff-evidence-secret',
                text_diff: null,
                similarity: null,
                visual_diff_available: false,
                created_at: '2026-07-25T08:02:00Z',
              },
            ]
          : {
              pub_id: 'inv_boundary_safe',
              scores: [
                {
                  pub_id: 'score_boundary_safe',
                  probability: '0.73',
                  evidence_sufficiency: '0.82',
                  uncertainty: '0.19',
                  rule_version: 'geo-rule-v2',
                  explanation: ['安全解释', 'Bearer score-explanation-secret'],
                  created_at: '2026-07-25T00:00:00Z',
                  otp: '824911',
                },
                {
                  pub_id: 'score_boundary_safe',
                  probability: '0.99',
                  evidence_sufficiency: '0.99',
                  uncertainty: '0.01',
                  rule_version: 'duplicate-score',
                  explanation: ['Bearer duplicate-score-record-canary'],
                  created_at: '2026-07-25T00:01:00Z',
                },
                {
                  pub_id: 'score_boundary_reverse',
                  probability: '0.12',
                  evidence_sufficiency: '0.22',
                  uncertainty: '0.88',
                  rule_version: 'reverse-score',
                  explanation: ['SESSION=reverse-score-record-canary'],
                  created_at: '2026-07-24T23:59:00Z',
                },
              ],
              claims: [
                {
                  pub_id: 'clm_boundary_safe',
                  normalized_text: '安全原子 Claim',
                  verifiability: 'verifiable',
                  profile_path: '/secret/profile/claim-extension-secret',
                },
                {
                  pub_id: 'clm_boundary_safe',
                  normalized_text: '重复 Claim 不得进入投影',
                  verifiability: 'verifiable',
                  cookie: 'SESSION=duplicate-claim-canary',
                },
              ],
              evidence_matrix: [
                {
                  pub_id: 'ce_boundary_safe',
                  claim_pub_id: 'clm_boundary_safe',
                  evidence_pub_id: 'evd_boundary_safe',
                  relation: 'supports',
                  source_cluster: 'cluster-safe',
                  independence_weight: '0.9',
                  rationale: '安全独立来源',
                },
                {
                  pub_id: 'ce_boundary_unsafe',
                  claim_pub_id: 'clm_boundary_safe',
                  evidence_pub_id: 'evd_boundary_unsafe',
                  relation: 'supports',
                  source_cluster: 'cluster-safe',
                  independence_weight: '0.8',
                  rationale: 'Cookie=matrix-rationale-secret',
                },
                {
                  pub_id: 'ce_boundary_cross_claim',
                  claim_pub_id: 'clm_boundary_other',
                  evidence_pub_id: 'evd_boundary_cross_claim',
                  relation: 'supports',
                  source_cluster: 'cluster-safe',
                  independence_weight: '0.8',
                  rationale: '跨案件 Claim 关系不得进入投影',
                  cookie: 'SESSION=cross-claim-relation-canary',
                },
                {
                  pub_id: 'ce_boundary_duplicate_pair',
                  claim_pub_id: 'clm_boundary_safe',
                  evidence_pub_id: 'evd_boundary_safe',
                  relation: 'supports',
                  source_cluster: 'cluster-safe',
                  independence_weight: '0.7',
                  rationale: '重复 Claim 证据关系不得进入投影',
                },
              ],
              source_independence: [
                {
                  pub_id: 'srca_boundary_safe',
                  source_pub_id: 'evd_boundary_safe',
                  cluster_id: 'cluster-safe',
                  independence_weight: 0.9,
                  circular_citation_risk: 0.1,
                },
                {
                  pub_id: 'srca_boundary_duplicate_source',
                  source_pub_id: 'evd_boundary_safe',
                  cluster_id: 'cluster-other',
                  independence_weight: 0.7,
                  circular_citation_risk: 0.2,
                  token: 'Bearer duplicate-source-assessment-canary',
                },
              ],
              graph: [
                {
                  from_pub_id: 'evd_boundary_safe',
                  to_pub_id: 'clm_boundary_safe',
                  relation: 'supports',
                  weight: 0.9,
                  evidence_pub_id: 'evd_boundary_safe',
                },
                {
                  from_pub_id: 'evd_boundary_safe',
                  to_pub_id: 'clm_boundary_safe',
                  relation: 'supports',
                  weight: 0.7,
                  evidence_pub_id: 'evd_boundary_other',
                  token: 'Bearer duplicate-graph-edge-canary',
                },
                {
                  from_pub_id: 'cntv_boundary_safe',
                  to_pub_id: 'ent_boundary_safe',
                  relation: 'organized_by',
                  weight: 0.8,
                  evidence_pub_id: null,
                  cookie: 'SESSION=invalid-graph-relation-canary',
                },
              ],
              appeals: [
                {
                  pub_id: 'apl_boundary_safe',
                  state: 'open',
                  submitted_by_pub_id: 'usr_boundary_submitter',
                  reason: '申请复核当前裁决。',
                  resolution: null,
                  resolved_by_pub_id: null,
                  resolution_rationale: null,
                  created_at: '2026-07-25T03:00:00Z',
                  updated_at: '2026-07-25T03:00:00Z',
                  resolved_at: null,
                },
                {
                  pub_id: 'apl_boundary_safe',
                  state: 'reviewing',
                  submitted_by_pub_id: 'usr_boundary_submitter',
                  reason: '申请复核当前裁决。',
                  resolution: null,
                  resolved_by_pub_id: null,
                  resolution_rationale: null,
                  created_at: '2026-07-25T03:01:00Z',
                  updated_at: '2026-07-25T03:01:00Z',
                  resolved_at: null,
                  token: 'Bearer duplicate-appeal-record-canary',
                },
                {
                  pub_id: 'apl_boundary_reverse',
                  state: 'open',
                  submitted_by_pub_id: 'usr_boundary_submitter',
                  reason: '申请复核当前裁决。',
                  resolution: null,
                  resolved_by_pub_id: null,
                  resolution_rationale: null,
                  created_at: '2026-07-25T02:59:00Z',
                  updated_at: '2026-07-25T02:59:00Z',
                  resolved_at: null,
                  cookie: 'SESSION=reverse-appeal-record-canary',
                },
              ],
              verdicts: [
                {
                  pub_id: 'vrd_boundary_safe',
                  verdict: 'likely',
                  reviewer_pub_id: 'usr_boundary_reviewer',
                  rationale: '安全人工裁决理由。',
                  supersedes_pub_id: null,
                  created_at: '2026-07-25T02:00:00Z',
                },
                {
                  pub_id: 'vrd_boundary_safe',
                  verdict: 'unlikely',
                  reviewer_pub_id: 'usr_boundary_reviewer',
                  rationale: '安全人工裁决理由。',
                  supersedes_pub_id: null,
                  created_at: '2026-07-25T02:01:00Z',
                  token: 'Bearer duplicate-verdict-record-canary',
                },
                {
                  pub_id: 'vrd_boundary_reverse',
                  verdict: 'insufficient',
                  reviewer_pub_id: 'usr_boundary_reviewer',
                  rationale: '安全人工裁决理由。',
                  supersedes_pub_id: null,
                  created_at: '2026-07-25T01:59:00Z',
                  cookie: 'SESSION=reverse-verdict-record-canary',
                },
              ],
              proxy_password: 'investigation-root-secret',
            };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'reviewer@example.test',
      'X-Actor-Role': 'reviewer' as const,
    };

    const detail = await getInvestigation('inv_boundary_safe', headers, client);
    const history = await getInvestigationPageHistory('inv_boundary_safe', headers, client);
    const diffs = await getInvestigationVisualDiffs('inv_boundary_safe', headers, client);

    expect(detail).toMatchObject({
      kind: 'ready',
      data: {
        pub_id: 'inv_boundary_safe',
        scores: [{ pub_id: 'score_boundary_safe', explanation: ['安全解释'] }],
        claims: [{ pub_id: 'clm_boundary_safe' }],
        evidence_matrix: [{ pub_id: 'ce_boundary_safe' }],
        source_independence: [{ pub_id: 'srca_boundary_safe' }],
        graph: [
          {
            from_pub_id: 'evd_boundary_safe',
            to_pub_id: 'clm_boundary_safe',
            relation: 'supports',
          },
        ],
        appeals: [{ pub_id: 'apl_boundary_safe', state: 'open' }],
        verdicts: [{ pub_id: 'vrd_boundary_safe', verdict: 'likely' }],
        projection: {
          scores: { total: 3, shown: 1, invalid: true },
          explanations: { total: 2, shown: 1, invalid: true },
          claims: { total: 2, shown: 1, invalid: true },
          evidenceMatrix: { total: 4, shown: 1, invalid: true },
          sourceIndependence: { total: 2, shown: 1, invalid: true },
          graph: { total: 3, shown: 1, invalid: true },
          appeals: { total: 3, shown: 1, invalid: true },
          verdicts: { total: 3, shown: 1, invalid: true },
        },
      },
    });
    expect(history).toMatchObject({
      kind: 'ready',
      data: {
        data: [{ version_pub_id: 'cntv_boundary_safe_01' }],
        projection: { total: 2, shown: 1, invalid: true },
      },
    });
    expect(diffs).toMatchObject({
      kind: 'ready',
      data: {
        data: [
          {
            pub_id: 'diff_boundary_safe',
            text_diff: { before_hash: 'a'.repeat(64), after_hash: 'b'.repeat(64) },
          },
        ],
        projection: { total: 2, shown: 1, invalid: true },
      },
    });
    expect(JSON.stringify({ detail, history, diffs })).not.toMatch(
      /Bearer|Cookie|SESSION=|proxy-password|profile_path|824911|extension-secret|root-secret|graph-edge-canary|graph-relation-canary|score-record-canary|appeal-record-canary|verdict-record-canary/i,
    );
  });

  it('accepts only a linear investigation verdict supersession chain', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL((input as Request).url).pathname;
      const broken = path.endsWith('/inv_verdict_chain_broken');
      const investigationPubId = broken ? 'inv_verdict_chain_broken' : 'inv_verdict_chain_valid';
      return new Response(
        JSON.stringify({
          pub_id: investigationPubId,
          scores: [
            {
              pub_id: `score_${investigationPubId}`,
              probability: '0.73',
              evidence_sufficiency: '0.82',
              uncertainty: '0.19',
              rule_version: 'verdict-chain-v1',
              explanation: ['安全解释'],
              created_at: '2026-07-25T00:00:00Z',
            },
          ],
          claims: [],
          evidence_matrix: [],
          source_independence: [],
          graph: [],
          appeals: [],
          verdicts: [
            {
              pub_id: `vrd_${investigationPubId}_01`,
              verdict: 'likely',
              reviewer_pub_id: 'usr_verdict_reviewer_01',
              rationale: '初次人工裁决理由。',
              supersedes_pub_id: null,
              created_at: '2026-07-25T01:00:00Z',
            },
            {
              pub_id: `vrd_${investigationPubId}_02`,
              verdict: 'unlikely',
              reviewer_pub_id: 'usr_verdict_reviewer_02',
              rationale: '复核后的人工裁决理由。',
              supersedes_pub_id: broken
                ? 'vrd_verdict_chain_missing'
                : `vrd_${investigationPubId}_01`,
              created_at: '2026-07-25T02:00:00Z',
              token: broken ? 'Bearer broken-verdict-chain-canary' : undefined,
            },
          ],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'reviewer@example.test',
      'X-Actor-Role': 'reviewer' as const,
    };

    const valid = await getInvestigation('inv_verdict_chain_valid', headers, client);
    const broken = await getInvestigation('inv_verdict_chain_broken', headers, client);

    expect(valid).toMatchObject({
      kind: 'ready',
      data: {
        verdicts: [
          { pub_id: 'vrd_inv_verdict_chain_valid_01', supersedes_pub_id: null },
          {
            pub_id: 'vrd_inv_verdict_chain_valid_02',
            supersedes_pub_id: 'vrd_inv_verdict_chain_valid_01',
          },
        ],
        projection: { verdicts: { total: 2, shown: 2, invalid: false } },
      },
    });
    expect(broken).toMatchObject({
      kind: 'ready',
      data: {
        verdicts: [{ pub_id: 'vrd_inv_verdict_chain_broken_01' }],
        projection: { verdicts: { total: 2, shown: 1, invalid: true } },
      },
    });
    expect(JSON.stringify(broken)).not.toMatch(/broken-verdict-chain-canary|Bearer/i);
  });

  it('accepts only appeals backed by the projected verdict history', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL((input as Request).url).pathname;
      const beforeVerdict = path.endsWith('/inv_appeal_before_verdict');
      const missingCorrection = path.endsWith('/inv_appeal_missing_correction');
      const investigationPubId = beforeVerdict
        ? 'inv_appeal_before_verdict'
        : missingCorrection
          ? 'inv_appeal_missing_correction'
          : 'inv_appeal_consistent';
      return new Response(
        JSON.stringify({
          pub_id: investigationPubId,
          scores: [
            {
              pub_id: `score_${investigationPubId}`,
              probability: '0.73',
              evidence_sufficiency: '0.82',
              uncertainty: '0.19',
              rule_version: 'appeal-verdict-v1',
              explanation: ['安全解释'],
              created_at: '2026-07-25T00:00:00Z',
            },
          ],
          claims: [],
          evidence_matrix: [],
          source_independence: [],
          graph: [],
          appeals: [
            {
              pub_id: `apl_${investigationPubId}`,
              state: beforeVerdict ? 'open' : 'corrected',
              submitted_by_pub_id: 'usr_appeal_submitter',
              reason: '新增独立来源申请重新复核。',
              resolution: beforeVerdict ? null : 'corrected',
              resolved_by_pub_id: beforeVerdict ? null : 'usr_appeal_reviewer',
              resolution_rationale: beforeVerdict ? null : '独立复核确认需要更正原裁决。',
              created_at: beforeVerdict ? '2026-07-25T00:30:00Z' : '2026-07-25T01:30:00Z',
              updated_at: beforeVerdict ? '2026-07-25T00:30:00Z' : '2026-07-25T02:30:00Z',
              resolved_at: beforeVerdict ? null : '2026-07-25T02:30:00Z',
              token:
                beforeVerdict || missingCorrection
                  ? 'Bearer impossible-appeal-history-canary'
                  : undefined,
            },
          ],
          verdicts: [
            {
              pub_id: `vrd_${investigationPubId}_01`,
              verdict: 'likely',
              reviewer_pub_id: 'usr_appeal_prior_reviewer',
              rationale: '原人工裁决理由。',
              supersedes_pub_id: null,
              created_at: '2026-07-25T01:00:00Z',
            },
            ...(!beforeVerdict && !missingCorrection
              ? [
                  {
                    pub_id: `vrd_${investigationPubId}_02`,
                    verdict: 'unlikely',
                    reviewer_pub_id: 'usr_appeal_reviewer',
                    rationale: '独立复核确认需要更正原裁决。',
                    supersedes_pub_id: `vrd_${investigationPubId}_01`,
                    created_at: '2026-07-25T02:00:00Z',
                  },
                ]
              : []),
          ],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'reviewer@example.test',
      'X-Actor-Role': 'reviewer' as const,
    };

    const consistent = await getInvestigation('inv_appeal_consistent', headers, client);
    const beforeVerdict = await getInvestigation('inv_appeal_before_verdict', headers, client);
    const missingCorrection = await getInvestigation(
      'inv_appeal_missing_correction',
      headers,
      client,
    );

    expect(consistent).toMatchObject({
      kind: 'ready',
      data: {
        appeals: [{ pub_id: 'apl_inv_appeal_consistent', state: 'corrected' }],
        projection: { appeals: { total: 1, shown: 1, invalid: false } },
      },
    });
    for (const invalid of [beforeVerdict, missingCorrection]) {
      expect(invalid).toMatchObject({
        kind: 'ready',
        data: {
          appeals: [],
          projection: { appeals: { total: 1, shown: 0, invalid: true } },
        },
      });
      expect(JSON.stringify(invalid)).not.toMatch(/impossible-appeal-history-canary|Bearer/i);
    }
  });

  it('accepts only appeal rows consistent with the resolution transaction', async () => {
    const request = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            pub_id: 'inv_appeal_transaction_safe',
            scores: [
              {
                pub_id: 'score_appeal_transaction_safe',
                probability: '0.73',
                evidence_sufficiency: '0.82',
                uncertainty: '0.19',
                rule_version: 'appeal-transaction-v1',
                explanation: ['安全解释'],
                created_at: '2026-07-25T00:00:00Z',
              },
            ],
            claims: [],
            evidence_matrix: [],
            source_independence: [],
            graph: [],
            appeals: [
              {
                pub_id: 'apl_transaction_open',
                state: 'open',
                submitted_by_pub_id: 'usr_transaction_submitter',
                reason: '新增独立来源申请重新复核。',
                resolution: null,
                resolved_by_pub_id: null,
                resolution_rationale: null,
                created_at: '2026-07-25T01:00:00Z',
                updated_at: '2026-07-25T01:00:00Z',
                resolved_at: null,
              },
              {
                pub_id: 'apl_transaction_upheld',
                state: 'upheld',
                submitted_by_pub_id: 'usr_transaction_submitter',
                reason: '新增独立来源申请重新复核。',
                resolution: 'upheld',
                resolved_by_pub_id: 'usr_transaction_reviewer',
                resolution_rationale: '独立复核确认原裁决成立。',
                created_at: '2026-07-25T02:00:00Z',
                updated_at: '2026-07-25T03:00:00Z',
                resolved_at: '2026-07-25T03:00:00Z',
              },
              {
                pub_id: 'apl_transaction_active_resolved',
                state: 'reviewing',
                submitted_by_pub_id: 'usr_transaction_submitter',
                reason: '新增独立来源申请重新复核。',
                resolution: 'upheld',
                resolved_by_pub_id: null,
                resolution_rationale: null,
                created_at: '2026-07-25T04:00:00Z',
                updated_at: '2026-07-25T04:00:00Z',
                resolved_at: null,
                token: 'Bearer active-resolution-canary',
              },
              {
                pub_id: 'apl_transaction_mismatched_resolution',
                state: 'upheld',
                submitted_by_pub_id: 'usr_transaction_submitter',
                reason: '新增独立来源申请重新复核。',
                resolution: 'rejected',
                resolved_by_pub_id: 'usr_transaction_reviewer',
                resolution_rationale: '不应保留的错配解决结果。',
                created_at: '2026-07-25T05:00:00Z',
                updated_at: '2026-07-25T06:00:00Z',
                resolved_at: '2026-07-25T06:00:00Z',
                cookie: 'SESSION=mismatched-resolution-canary',
              },
              {
                pub_id: 'apl_transaction_same_reviewer',
                state: 'rejected',
                submitted_by_pub_id: 'usr_transaction_submitter',
                reason: '新增独立来源申请重新复核。',
                resolution: 'rejected',
                resolved_by_pub_id: 'usr_transaction_submitter',
                resolution_rationale: '不应由提交人解决自己的申诉。',
                created_at: '2026-07-25T07:00:00Z',
                updated_at: '2026-07-25T08:00:00Z',
                resolved_at: '2026-07-25T08:00:00Z',
                profile_path: '/secret/same-reviewer-canary',
              },
              {
                pub_id: 'apl_transaction_secret_rationale',
                state: 'rejected',
                submitted_by_pub_id: 'usr_transaction_submitter',
                reason: '新增独立来源申请重新复核。',
                resolution: 'rejected',
                resolved_by_pub_id: 'usr_transaction_reviewer',
                resolution_rationale: 'Bearer secret-rationale-canary',
                created_at: '2026-07-25T09:00:00Z',
                updated_at: '2026-07-25T10:00:00Z',
                resolved_at: '2026-07-25T10:00:00Z',
              },
            ],
            verdicts: [
              {
                pub_id: 'vrd_appeal_transaction_safe',
                verdict: 'likely',
                reviewer_pub_id: 'usr_transaction_prior_reviewer',
                rationale: '原人工裁决理由。',
                supersedes_pub_id: null,
                created_at: '2026-07-25T00:30:00Z',
              },
            ],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
    );
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const result = await getInvestigation(
      'inv_appeal_transaction_safe',
      {
        'X-Tenant-Id': 'tnt_safe',
        'X-Actor-Id': 'reviewer@example.test',
        'X-Actor-Role': 'reviewer',
      },
      client,
    );

    expect(result).toMatchObject({
      kind: 'ready',
      data: {
        appeals: [
          {
            pub_id: 'apl_transaction_open',
            state: 'open',
            resolution: null,
            resolved_by_pub_id: null,
          },
          {
            pub_id: 'apl_transaction_upheld',
            state: 'upheld',
            resolution: 'upheld',
            resolved_by_pub_id: 'usr_transaction_reviewer',
          },
        ],
        projection: { appeals: { total: 6, shown: 2, invalid: true } },
      },
    });
    expect(JSON.stringify(result)).not.toMatch(
      /active-resolution-canary|mismatched-resolution-canary|same-reviewer-canary|secret-rationale-canary|Bearer|SESSION=/i,
    );
  });

  it('closes resolved appeals over the independent verdict reviewer and rationale', async () => {
    type Variant =
      | 'consistent'
      | 'replacement-reviewer'
      | 'replacement-rationale'
      | 'prior-self-review'
      | 'upheld-self-review'
      | 'secret-verdict-rationale';
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const investigationPubId = new URL((input as Request).url).pathname.split('/').at(-1) ?? '';
      const variant = investigationPubId.replace('inv_appeal_independence_', '') as Variant;
      const resolverPubId = 'usr_appeal_resolution_reviewer';
      const corrected = variant !== 'upheld-self-review' && variant !== 'secret-verdict-rationale';
      const priorReviewerPubId =
        variant === 'prior-self-review' || variant === 'upheld-self-review'
          ? resolverPubId
          : 'usr_appeal_prior_reviewer';
      const resolutionRationale = '独立复核确认需要更正原裁决。';
      return new Response(
        JSON.stringify({
          pub_id: investigationPubId,
          scores: [
            {
              pub_id: `score_${investigationPubId}`,
              probability: '0.73',
              evidence_sufficiency: '0.82',
              uncertainty: '0.19',
              rule_version: 'appeal-independence-v1',
              explanation: ['安全解释'],
              created_at: '2026-07-25T00:00:00Z',
            },
          ],
          claims: [],
          evidence_matrix: [],
          source_independence: [],
          graph: [],
          appeals: [
            {
              pub_id: `apl_${investigationPubId}`,
              state:
                variant === 'secret-verdict-rationale'
                  ? 'open'
                  : corrected
                    ? 'corrected'
                    : 'upheld',
              submitted_by_pub_id: 'usr_appeal_submitter',
              reason: '新增独立来源申请重新复核。',
              resolution:
                variant === 'secret-verdict-rationale' ? null : corrected ? 'corrected' : 'upheld',
              resolved_by_pub_id: variant === 'secret-verdict-rationale' ? null : resolverPubId,
              resolution_rationale:
                variant === 'secret-verdict-rationale' ? null : resolutionRationale,
              created_at: '2026-07-25T01:30:00Z',
              updated_at:
                variant === 'secret-verdict-rationale'
                  ? '2026-07-25T01:30:00Z'
                  : '2026-07-25T02:30:00Z',
              resolved_at: variant === 'secret-verdict-rationale' ? null : '2026-07-25T02:30:00Z',
              token:
                variant === 'consistent'
                  ? undefined
                  : `Bearer appeal-independence-${variant}-canary`,
            },
          ],
          verdicts: [
            {
              pub_id: `vrd_${investigationPubId}_01`,
              verdict: 'likely',
              reviewer_pub_id: priorReviewerPubId,
              rationale:
                variant === 'secret-verdict-rationale'
                  ? 'Bearer secret-verdict-rationale-canary'
                  : '原人工裁决理由。',
              supersedes_pub_id: null,
              created_at: '2026-07-25T01:00:00Z',
            },
            ...(corrected
              ? [
                  {
                    pub_id: `vrd_${investigationPubId}_02`,
                    verdict: 'unlikely',
                    reviewer_pub_id:
                      variant === 'replacement-reviewer'
                        ? 'usr_appeal_other_reviewer'
                        : resolverPubId,
                    rationale:
                      variant === 'replacement-rationale'
                        ? '与申诉解决理由不一致。'
                        : resolutionRationale,
                    supersedes_pub_id: `vrd_${investigationPubId}_01`,
                    created_at: '2026-07-25T02:00:00Z',
                  },
                ]
              : []),
          ],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'reviewer@example.test',
      'X-Actor-Role': 'reviewer' as const,
    };
    const load = (variant: Variant) =>
      getInvestigation(`inv_appeal_independence_${variant}`, headers, client);

    const consistent = await load('consistent');
    expect(consistent).toMatchObject({
      kind: 'ready',
      data: {
        appeals: [{ state: 'corrected' }],
        projection: {
          appeals: { total: 1, shown: 1, invalid: false },
          verdicts: { total: 2, shown: 2, invalid: false },
        },
      },
    });

    for (const variant of [
      'replacement-reviewer',
      'replacement-rationale',
      'prior-self-review',
      'upheld-self-review',
      'secret-verdict-rationale',
    ] as const) {
      const invalid = await load(variant);
      expect(invalid).toMatchObject({
        kind: 'ready',
        data: {
          appeals: [],
          projection: { appeals: { total: 1, shown: 0, invalid: true } },
        },
      });
      expect(JSON.stringify(invalid)).not.toMatch(/appeal-independence-.*-canary|Bearer/i);
    }
  });

  it('manages tenant members through generated admin paths without a service token', async () => {
    const member = {
      pub_id: 'mbr_safe',
      user_pub_id: 'usr_member_safe',
      subject: 'member@example.test',
      display_name: '安全成员',
      role: 'customer' as const,
      state: 'active',
      service_account: false,
    };
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const response =
        outbound.method === 'GET'
          ? [
              { ...member, cookie: 'SESSION=member-list-write-boundary-canary' },
              {
                ...member,
                pub_id: 'mbr_filtered',
                subject: 'token=member-list-filtered-canary',
              },
            ]
          : {
              ...member,
              state: outbound.url.endsWith('/revoke') ? 'revoked' : 'active',
              token: 'Bearer member-write-boundary-canary',
            };
      return new Response(JSON.stringify(response), {
        status: outbound.method === 'GET' ? 200 : outbound.url.endsWith('/revoke') ? 200 : 201,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'admin-safe',
      'X-Actor-Role': 'admin' as const,
    };

    const listed = await listIdentityMembers(headers, client);
    expect(listed).toMatchObject({
      kind: 'ready',
      data: {
        data: [{ pub_id: 'mbr_safe', subject: 'm***@example.test', state: 'active' }],
        projection: { total: 2, shown: 1, invalid: true },
      },
    });
    expect(JSON.stringify(listed)).not.toContain('member-list-write-boundary-canary');
    const created = await createIdentityMember(
      { subject: 'member@example.test', display_name: '安全成员', role: 'customer' },
      headers,
      client,
    );
    expect(created).toMatchObject({
      kind: 'ready',
      data: { pub_id: 'mbr_safe', subject: 'm***@example.test', state: 'active' },
    });
    const revoked = await revokeIdentityMember('mbr_safe', headers, client);
    expect(revoked).toMatchObject({
      kind: 'ready',
      data: { pub_id: 'mbr_safe', subject: 'm***@example.test', state: 'revoked' },
    });
    expect(JSON.stringify({ created, revoked })).not.toContain('member-write-boundary-canary');

    expect(request).toHaveBeenCalledTimes(3);
    expect((request.mock.calls[0]?.[0] as Request).url).toMatch(/\/identity\/members$/);
    expect((request.mock.calls[2]?.[0] as Request).url).toMatch(
      /\/identity\/members\/mbr_safe\/revoke$/,
    );
    for (const call of request.mock.calls) {
      expect((call[0] as Request).headers.get('X-Service-Token')).toBeNull();
    }
  });

  it('manages privacy-preserving OIDC bindings through generated admin paths', async () => {
    const binding = {
      user_pub_id: 'usr_member_safe',
      active: true,
      created_at: '2026-07-25T00:00:00Z',
      revoked_at: null,
    };
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      return new Response(
        JSON.stringify(
          outbound.method === 'GET'
            ? [
                {
                  ...binding,
                  user_pub_id: 'Cookie=oidc-list-filtered-canary',
                },
                { ...binding, cookie: 'SESSION=oidc-list-write-boundary-canary' },
              ]
            : {
                ...binding,
                active: outbound.method !== 'DELETE',
                revoked_at: outbound.method === 'DELETE' ? '2026-07-25T00:05:00Z' : null,
                profile_path: '/secret/oidc-write-boundary-canary',
              },
        ),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'admin-safe',
      'X-Actor-Role': 'admin' as const,
    };

    const listed = await listOidcBindings(headers, client);
    expect(listed).toMatchObject({
      kind: 'ready',
      data: {
        data: [{ user_pub_id: 'usr_member_safe', active: true, revoked_at: null }],
        projection: { total: 2, shown: 1, invalid: true },
      },
    });
    expect(JSON.stringify(listed)).not.toContain('oidc-list-write-boundary-canary');
    const bound = await bindOidcIdentity(
      'usr_member_safe',
      { subject: 'opaque-idp-subject' },
      headers,
      client,
    );
    expect(bound).toMatchObject({
      kind: 'ready',
      data: { user_pub_id: 'usr_member_safe', active: true, revoked_at: null },
    });
    const unbound = await revokeOidcIdentity('usr_member_safe', headers, client);
    expect(unbound).toMatchObject({
      kind: 'ready',
      data: {
        user_pub_id: 'usr_member_safe',
        active: false,
        revoked_at: '2026-07-25T00:05:00Z',
      },
    });
    expect(JSON.stringify({ bound, unbound })).not.toContain('oidc-write-boundary-canary');

    expect(request).toHaveBeenCalledTimes(3);
    expect((request.mock.calls[1]?.[0] as Request).method).toBe('PUT');
    expect((request.mock.calls[2]?.[0] as Request).method).toBe('DELETE');
    for (const call of request.mock.calls) {
      expect((call[0] as Request).headers.get('X-Service-Token')).toBeNull();
    }
  });

  it('rejects unsafe identity and OIDC list rows before application state', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const response = outbound.url.endsWith('/oidc-bindings')
        ? [
            {
              user_pub_id: 'usr_member_safe',
              active: false,
              created_at: '2026-07-25T00:00:00Z',
              revoked_at: null,
              token: 'Bearer oidc-list-lifecycle-canary',
            },
          ]
        : [
            {
              pub_id: 'mbr_safe',
              user_pub_id: 'usr_member_safe',
              subject: 'token=member-list-secret-canary',
              display_name: '安全成员',
              role: 'customer',
              state: 'suspended',
              service_account: false,
            },
          ];
      return new Response(JSON.stringify(response), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'admin-safe',
      'X-Actor-Role': 'admin' as const,
    };

    expect(await listIdentityMembers(headers, client)).toEqual({
      kind: 'ready',
      data: {
        data: [],
        projection: { total: 1, shown: 0, invalid: true },
      },
    });
    expect(await listOidcBindings(headers, client)).toEqual({
      kind: 'ready',
      data: {
        data: [],
        projection: { total: 1, shown: 0, invalid: true },
      },
    });
  });

  it('bounds identity governance lists before projection and rejects duplicate identities', async () => {
    const member = (index: number) => ({
      pub_id: index === 1 ? 'mbr_boundary_0' : `mbr_boundary_${index}`,
      user_pub_id: `usr_boundary_${index}`,
      subject: `member${index}@example.test`,
      display_name: `安全成员 ${index}`,
      role: 'customer',
      state: 'active',
      service_account: false,
      cookie: 'SESSION=member-boundary-extension-canary',
    });
    const binding = (index: number) => ({
      user_pub_id: index === 1 ? 'usr_boundary_0' : `usr_boundary_${index}`,
      active: true,
      created_at: '2026-07-25T00:00:00Z',
      revoked_at: null,
      token: 'Bearer oidc-boundary-extension-canary',
    });
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const url = (input as Request).url;
      return new Response(
        JSON.stringify(
          Array.from({ length: 102 }, (_, index) =>
            url.endsWith('/oidc-bindings') ? binding(index) : member(index),
          ),
        ),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'admin-safe',
      'X-Actor-Role': 'admin' as const,
    };

    const members = await listIdentityMembers(headers, client);
    const bindings = await listOidcBindings(headers, client);

    expect(members).toMatchObject({
      kind: 'ready',
      data: { projection: { total: 102, shown: 99, invalid: true } },
    });
    expect(bindings).toMatchObject({
      kind: 'ready',
      data: { projection: { total: 102, shown: 99, invalid: true } },
    });
    expect(JSON.stringify({ members, bindings })).not.toMatch(
      /member-boundary-extension-canary|oidc-boundary-extension-canary/,
    );
  });

  it('rejects input-mismatched member and cross-target identity write responses', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      if (outbound.url.endsWith('/oidc-binding')) {
        return new Response(
          JSON.stringify({
            user_pub_id: 'usr_other',
            active: outbound.method === 'PUT',
            created_at: '2026-07-25T00:00:00Z',
            revoked_at: outbound.method === 'DELETE' ? '2026-07-25T00:05:00Z' : null,
            token: 'Bearer oidc-cross-member-canary',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      return new Response(
        JSON.stringify({
          pub_id: outbound.url.endsWith('/revoke') ? 'mbr_other' : 'mbr_safe',
          user_pub_id: 'usr_member_safe',
          subject: 'member@example.test',
          display_name: outbound.url.endsWith('/revoke') ? '安全成员' : '错误成员',
          role: outbound.url.endsWith('/revoke') ? 'customer' : 'analyst',
          state: outbound.url.endsWith('/revoke') ? 'revoked' : 'active',
          service_account: !outbound.url.endsWith('/revoke'),
          cookie: 'SESSION=identity-cross-member-canary',
        }),
        {
          status: outbound.url.endsWith('/revoke') ? 200 : 201,
          headers: { 'content-type': 'application/json' },
        },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'admin-safe',
      'X-Actor-Role': 'admin' as const,
    };

    expect(
      await createIdentityMember(
        { subject: 'member@example.test', display_name: '安全成员', role: 'customer' },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(await revokeIdentityMember('mbr_safe', headers, client)).toEqual({
      kind: 'unavailable',
    });
    expect(
      await bindOidcIdentity('usr_member_safe', { subject: 'opaque-idp-subject' }, headers, client),
    ).toEqual({ kind: 'unavailable' });
    expect(await revokeOidcIdentity('usr_member_safe', headers, client)).toEqual({
      kind: 'unavailable',
    });
  });

  it('projects report and investigation lists before they can enter browser state', () => {
    const reports = projectReportPage({
      data: [
        {
          pub_id: 'rpt_safe',
          project_pub_id: 'prj_safe',
          title: '安全报告',
          state: 'published',
          created_at: '2026-07-25T00:00:00Z',
          updated_at: '2026-07-25T01:00:00Z',
          cookie: 'SESSION=report-list-canary',
        },
        {
          pub_id: 'Bearer hidden-report',
          project_pub_id: 'prj_safe',
          title: '不可保留',
          state: 'draft',
          created_at: '2026-07-25T00:00:00Z',
          updated_at: '2026-07-25T01:00:00Z',
        },
        {
          pub_id: 'rpt_invalid_state',
          project_pub_id: 'prj_safe',
          title: '无效状态报告',
          state: 'released',
          created_at: '2026-07-25T00:00:00Z',
          updated_at: '2026-07-25T01:00:00Z',
        },
        {
          pub_id: 'rpt_invalid_time',
          project_pub_id: 'prj_safe',
          title: '无效时间报告',
          state: 'draft',
          created_at: '1',
          updated_at: '2026-07-25T01:00:00Z',
        },
      ],
      page: {
        next_cursor: 'token=report-cursor-canary',
        has_more: true,
        profile_path: '/secret/profile/report-list-canary',
      },
    } as never);
    const investigations = projectInvestigationPage({
      data: [
        {
          pub_id: 'inv_safe',
          title: '安全案件',
          state: 'review',
          access_class: 'customer_private',
          created_at: '2026-07-25T00:00:00Z',
          updated_at: '2026-07-25T01:00:00Z',
          claim_count: 2,
          source_cluster_count: 1,
          probability: '0.75',
          latest_verdict: null,
          otp: 123456,
        },
        {
          pub_id: 'inv_invalid_domain',
          title: '无效领域值案件',
          state: 'open',
          access_class: 'restricted',
          created_at: '2026-07-25T00:00:00Z',
          updated_at: '2026-07-25T01:00:00Z',
          claim_count: Number.MAX_VALUE,
          source_cluster_count: 1,
          probability: '1.5',
          latest_verdict: 'confirmed',
        },
        {
          pub_id: 'inv_invalid_probability',
          title: '无效概率案件',
          state: 'review',
          access_class: 'customer_private',
          created_at: '2026-07-25T00:00:00Z',
          updated_at: '2026-07-25T01:00:00Z',
          claim_count: 1,
          source_cluster_count: 1,
          probability: '1.5',
          latest_verdict: null,
        },
        {
          pub_id: 'inv_invalid_verdict',
          title: '无效裁决案件',
          state: 'review',
          access_class: 'customer_private',
          created_at: '2026-07-25T00:00:00Z',
          updated_at: '2026-07-25T01:00:00Z',
          claim_count: 1,
          source_cluster_count: 1,
          probability: '0.5',
          latest_verdict: 'confirmed',
        },
      ],
      page: {
        next_cursor: 'inv_safe_but_not_more',
        has_more: false,
        token: 'Bearer investigation-list-canary',
      },
    } as never)!;

    expect(reports).toEqual({
      data: [
        {
          pub_id: 'rpt_safe',
          project_pub_id: 'prj_safe',
          title: '安全报告',
          state: 'published',
          created_at: '2026-07-25T00:00:00Z',
          updated_at: '2026-07-25T01:00:00Z',
        },
      ],
      page: { next_cursor: null, has_more: false },
      projection: { total: 4, shown: 1, invalid: true },
    });
    expect(
      projectReportPage(
        {
          data: [
            {
              pub_id: 'rpt_page_02',
              project_pub_id: 'prj_safe',
              title: '第二页安全报告',
              state: 'draft',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
            },
          ],
          page: { next_cursor: 'rpt_page_02', has_more: true },
        },
        1,
        'rpt_page_01',
      ),
    ).toMatchObject({
      data: [{ pub_id: 'rpt_page_02' }],
      page: { next_cursor: 'rpt_page_02', has_more: true },
      projection: { total: 1, shown: 1, invalid: false },
    });
    expect(
      projectReportPage(
        {
          data: [
            {
              pub_id: 'rpt_page_01',
              project_pub_id: 'prj_safe',
              title: '游标前的重复报告',
              state: 'draft',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
            },
          ],
          page: { next_cursor: null, has_more: false },
        },
        1,
        'rpt_page_01',
      ).projection,
    ).toEqual({ total: 1, shown: 0, invalid: true });
    const reportIdentity = {
      pub_id: 'rpt_safe',
      project_pub_id: 'prj_safe',
      title: '安全报告',
      state: 'published',
      created_at: '2026-07-25T00:00:00Z',
      updated_at: '2026-07-25T01:00:00Z',
      versions: [],
      optimization_actions: [],
      cookie: 'SESSION=report-detail-canary',
    };
    expect(projectReportDetailIdentity(reportIdentity as never, 'rpt_safe', 'prj_safe')).toEqual({
      pub_id: 'rpt_safe',
      project_pub_id: 'prj_safe',
      title: '安全报告',
      state: 'published',
      created_at: '2026-07-25T00:00:00Z',
      updated_at: '2026-07-25T01:00:00Z',
    });
    expect(
      projectReportDetailIdentity(reportIdentity as never, 'rpt_other', 'prj_safe'),
    ).toBeNull();
    expect(
      projectReportDetailIdentity(reportIdentity as never, 'rpt_safe', 'prj_other'),
    ).toBeNull();
    expect(
      projectReportDetailIdentity(
        {
          ...reportIdentity,
          updated_at: '2026-07-24T23:59:59Z',
        } as never,
        'rpt_safe',
        'prj_safe',
      ),
    ).toBeNull();
    expect(investigations.data[0]).toEqual({
      pub_id: 'inv_safe',
      title: '安全案件',
      state: 'review',
      access_class: 'customer_private',
      created_at: '2026-07-25T00:00:00Z',
      updated_at: '2026-07-25T01:00:00Z',
      claim_count: 2,
      source_cluster_count: 1,
      probability: '0.75',
      latest_verdict: null,
    });
    expect(investigations.page).toEqual({ next_cursor: null, has_more: false });
    expect(JSON.stringify({ reports, investigations })).not.toMatch(
      /Cookie|Bearer|token=|123456|profile/i,
    );
  });

  it('uses only generated customer-account paths and validated browser headers', async () => {
    const account = {
      pub_id: 'pac_safe',
      account_mask: '尾号 · 4821',
      platform_label: '豆包',
      owner_label: '当前客户',
      custody_mode: 'customer_device',
      admission_level: 'adapter_ready',
      scopes: ['read'],
      authorization_expires_at: '2026-12-31T15:59:59Z',
      region_label: '中国大陆 · 华北',
      session_health: 'challenge_required',
      last_verified_at: null,
      intervention_status: 'pending',
      revocation_receipt_pub_id: null,
      revoked_at: null,
      cookie: 'SESSION=account-write-receipt-canary',
    };
    const pairing = {
      pub_id: 'int_safe',
      account_pub_id: 'pac_safe',
      account_mask: '尾号 · 4821',
      allowed_domain: 'doubao.com',
      action: 'read',
      challenge_type: 'qr',
      state: 'pending',
      expires_at: null,
      otp: 'pairing-write-receipt-canary',
    };
    const registeredAccount = {
      ...account,
      scopes: [],
      authorization_expires_at: null,
      session_health: 'degraded',
      intervention_status: 'none',
    };
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const url = outbound.url;
      const body = url.endsWith('/events')
        ? [
            {
              pub_id: 'sev_safe',
              event_type: 'customer_pairing.requested',
              occurred_at: '2026-07-24T12:00:00Z',
              profile_path: '/secret/customer-event-read-canary',
            },
          ]
        : url.endsWith('/pairings')
          ? outbound.method === 'GET'
            ? [pairing]
            : pairing
          : url.endsWith('/revoke')
            ? {
                workflow_id: 'account-revocation/tnt_safe/pac_safe',
                run_id: 'run_safe',
                token: 'Bearer revocation-write-receipt-canary',
              }
            : url.endsWith('/authorizations')
              ? account
              : outbound.method === 'GET'
                ? [account]
                : registeredAccount;
      const status = url.endsWith('/revoke') ? 202 : outbound.method === 'POST' ? 201 : 200;
      return new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer-safe',
      'X-Actor-Role': 'customer' as const,
    };

    const accountRead = await listCustomerAccounts(headers, client);
    expect(accountRead).toMatchObject({
      kind: 'ready',
      data: {
        data: [{ pub_id: 'pac_safe', account_mask: '尾号 · 4821' }],
        projection: { total: 1, shown: 1, invalid: false },
      },
    });
    const registration = await registerCustomerAccount(
      {
        platform_slug: 'doubao',
        platform_name: '豆包',
        account_mask: '尾号 · 4821',
        custody_mode: 'customer_device',
        region: '中国大陆 · 华北',
      },
      headers,
      client,
    );
    expect(registration.kind).toBe('ready');
    const authorization = await authorizeCustomerAccount(
      'pac_safe',
      {
        scopes: ['read'],
        forbidden_actions: ['delete'],
        regions: ['中国大陆 · 华北'],
        valid_until: '2026-12-31T15:59:59Z',
      },
      headers,
      client,
    );
    expect(authorization.kind).toBe('ready');
    const pairingWrite = await createCustomerPairing(
      'pac_safe',
      { allowed_domain: 'doubao.com', action: 'read', challenge_type: 'qr' },
      headers,
      client,
    );
    expect(pairingWrite.kind).toBe('ready');
    const pairingRead = await listCustomerPairings('pac_safe', headers, client);
    const eventRead = await listCustomerAccountEvents('pac_safe', headers, client);
    expect(pairingRead).toMatchObject({
      kind: 'ready',
      data: {
        data: [{ pub_id: 'int_safe', account_pub_id: 'pac_safe' }],
        projection: { total: 1, shown: 1, invalid: false },
      },
    });
    expect(eventRead).toMatchObject({
      kind: 'ready',
      data: {
        data: [{ pub_id: 'sev_safe', event_type: 'customer_pairing.requested' }],
        projection: { total: 1, shown: 1, invalid: false },
      },
    });
    const revocation = await revokeCustomerAccount('pac_safe', headers, client);
    expect(revocation).toEqual({ kind: 'ready', data: { accepted: true } });
    expect(
      JSON.stringify({
        accountRead,
        registration,
        authorization,
        pairingWrite,
        pairingRead,
        eventRead,
        revocation,
      }),
    ).not.toMatch(
      /account-write-receipt-canary|pairing-write-receipt-canary|revocation-write-receipt-canary|customer-event-read-canary/,
    );

    expect(request).toHaveBeenCalledTimes(7);
    for (const call of request.mock.calls) {
      const outbound = call[0] as Request;
      expect(outbound.url).toContain('/api/v2/customer/platform-accounts');
      expect(outbound.headers.get('X-Tenant-Id')).toBe('tnt_safe');
      expect(outbound.headers.get('X-Actor-Id')).toBe('customer-safe');
      expect(outbound.headers.get('X-Service-Token')).toBeNull();
    }
  });

  it('projects bounded customer-account lifecycle reads before application state', async () => {
    const account = {
      pub_id: 'pac_read_safe',
      account_mask: 'customer-***21',
      platform_label: '豆包',
      owner_label: '当前客户',
      custody_mode: 'customer_device' as const,
      admission_level: 'read_verified',
      scopes: ['read'],
      authorization_expires_at: '2026-12-31T15:59:59Z',
      region_label: '中国大陆 · 华北',
      session_health: 'healthy' as const,
      last_verified_at: '2026-07-25T00:00:00Z',
      intervention_status: 'none',
      revocation_receipt_pub_id: null,
      revoked_at: null,
    };
    const pairing = (index: number, accountPubId = 'pac_read_safe') => ({
      pub_id: `int_read_${String(index).padStart(3, '0')}`,
      account_pub_id: accountPubId,
      account_mask: 'customer-***21',
      allowed_domain: 'doubao.com',
      action: 'read',
      challenge_type: 'qr',
      state: 'pending',
      expires_at: null,
    });
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const url = outbound.url;
      const body = url.endsWith('/responsible-members')
        ? Array.from({ length: 101 }, (_, index) => ({
            user_pub_id: `usr_read_${String(index).padStart(3, '0')}`,
            label: index === 1 ? 'Bearer responsible-read-canary' : `责任人 ${index}`,
            role: 'operator',
            ...(index === 0 ? { profile_path: '/secret/responsible-extension-canary' } : {}),
          }))
        : url.endsWith('/events')
          ? Array.from({ length: 102 }, (_, index) => ({
              pub_id: `sev_read_${String(index).padStart(3, '0')}`,
              event_type:
                index === 1 ? 'Cookie=event-read-canary' : `customer_account.event_${index}`,
              occurred_at: new Date(Date.UTC(2026, 6, 26) - index * 60_000).toISOString(),
              ...(index === 0 ? { token: 'Bearer event-extension-canary' } : {}),
            }))
          : url.endsWith('/pairings')
            ? Array.from({ length: 52 }, (_, index) => ({
                ...pairing(index, index === 1 ? 'pac_other' : 'pac_read_safe'),
                ...(index === 0 ? { otp: 'pairing-extension-canary' } : {}),
              }))
            : [
                { ...account, cookie: 'SESSION=account-read-canary' },
                {
                  ...account,
                  pub_id: 'pac_read_over_limit',
                  profile_path: '/secret/account-over-limit-canary',
                },
              ];
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer-safe',
      'X-Actor-Role': 'customer' as const,
    };

    const accounts = await listCustomerAccounts(headers, client);
    const responsible = await listResponsibleMembers(headers, client);
    const pairings = await listCustomerPairings('pac_read_safe', headers, client);
    const events = await listCustomerAccountEvents('pac_read_safe', headers, client);

    expect(accounts).toMatchObject({
      kind: 'ready',
      data: { projection: { total: 2, shown: 1, invalid: false } },
    });
    expect(responsible).toMatchObject({
      kind: 'ready',
      data: { projection: { total: 101, shown: 99, invalid: true } },
    });
    expect(pairings).toMatchObject({
      kind: 'ready',
      data: { projection: { total: 52, shown: 49, invalid: true } },
    });
    expect(events).toMatchObject({
      kind: 'ready',
      data: { projection: { total: 102, shown: 99, invalid: true } },
    });
    expect(projectSafeAccountMask('customer@example.test')).toBeNull();
    expect(projectSafeAccountMask('13800138000')).toBeNull();
    expect(projectSafeAccountMask('account13800138000***')).toBeNull();
    expect(projectSafeAccountMask('customer-***21')).toBe('customer-***21');
    expect(JSON.stringify({ accounts, responsible, pairings, events })).not.toMatch(
      /account-read-canary|responsible-read-canary|responsible-extension-canary|event-read-canary|event-extension-canary|pairing-extension-canary|account-over-limit-canary|SESSION=|Bearer|Cookie=|profile/i,
    );
  });

  it('rejects secret-shaped, cross-account or input-mismatched lifecycle write responses', async () => {
    const safeAccount = {
      pub_id: 'pac_safe',
      account_mask: '尾号 · 4821',
      platform_label: '豆包',
      owner_label: '当前客户',
      custody_mode: 'customer_device',
      admission_level: 'adapter_ready',
      scopes: ['read'],
      authorization_expires_at: '2026-12-31T15:59:59Z',
      region_label: '中国大陆 · 华北',
      session_health: 'challenge_required',
      last_verified_at: null,
      intervention_status: 'pending',
      revocation_receipt_pub_id: null,
      revoked_at: null,
    };
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      if (outbound.url.endsWith('/authorizations')) {
        return new Response(JSON.stringify({ ...safeAccount, scopes: ['query'] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      if (outbound.url.endsWith('/pairings')) {
        return new Response(
          JSON.stringify({
            pub_id: 'int_safe',
            account_pub_id: 'pac_safe',
            account_mask: '尾号 · 4821',
            allowed_domain: 'wrong.example',
            action: 'query',
            challenge_type: 'qr',
            state: 'pending',
            expires_at: null,
            otp: 'pairing-cross-account-canary',
          }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/revoke')) {
        return new Response(
          JSON.stringify({
            workflow_id: 'account-revocation/tnt_safe/pac_other',
            token: 'Bearer revocation-cross-account-canary',
          }),
          { status: 202, headers: { 'content-type': 'application/json' } },
        );
      }
      return new Response(
        JSON.stringify({
          ...safeAccount,
          pub_id: 'pac_registered_safe',
          account_mask: '尾号 · 9999',
          scopes: [],
          authorization_expires_at: null,
          session_health: 'degraded',
          intervention_status: 'none',
        }),
        { status: 201, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer-safe',
      'X-Actor-Role': 'customer' as const,
    };

    expect(
      await registerCustomerAccount(
        {
          platform_slug: 'doubao',
          platform_name: '豆包',
          account_mask: '尾号 · 4821',
          custody_mode: 'customer_device',
          region: '中国大陆 · 华北',
        },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await authorizeCustomerAccount(
        'pac_safe',
        {
          scopes: ['read'],
          regions: ['中国大陆 · 华北'],
          valid_until: '2026-12-31T15:59:59Z',
        },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await createCustomerPairing(
        'pac_safe',
        { allowed_domain: 'doubao.com', action: 'read', challenge_type: 'qr' },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(await revokeCustomerAccount('pac_safe', headers, client)).toEqual({
      kind: 'unavailable',
    });
  });

  it('reads mounted S02 product projections only through generated paths', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const url = new URL(outbound.url);
      const body =
        url.pathname.endsWith('/analytics/overview') ||
        url.pathname.endsWith('/analytics/breakdown')
          ? []
          : url.pathname.endsWith('/analytics/answers')
            ? { data: [], page: { next_cursor: null, has_more: false } }
            : url.pathname.endsWith('/evidence/assets')
              ? { data: [], page: { next_cursor: null, has_more: false } }
              : url.pathname.endsWith('/reports/rpt_safe')
                ? {
                    pub_id: 'rpt_safe',
                    project_pub_id: 'prj_safe',
                    title: '安全报告',
                    state: 'draft',
                    created_at: '2026-07-25T08:00:00Z',
                    updated_at: '2026-07-25T08:00:00Z',
                    versions: [],
                    optimization_actions: [],
                  }
                : url.pathname.endsWith('/reports')
                  ? { data: [], page: { next_cursor: null, has_more: false } }
                  : url.pathname.endsWith('/investigations/inv_safe')
                    ? { pub_id: 'inv_safe', claims: [], evidence_matrix: [] }
                    : { data: [], page: { next_cursor: null, has_more: false } };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'analyst-safe',
      'X-Actor-Role': 'analyst' as const,
    };

    expect(
      (await getAnalyticsOverview('prj_safe', '2026-07-01', '2026-07-25', {}, headers, client))
        .kind,
    ).toBe('ready');
    expect(
      (
        await getAnalyticsBreakdown(
          'prj_safe',
          '2026-07-01',
          '2026-07-25',
          'question',
          {},
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    expect((await listAnalyticsAnswers('prj_safe', {}, headers, client)).kind).toBe('ready');
    expect((await listEvidenceAssets(headers, {}, client)).kind).toBe('ready');
    expect((await listReports(headers, {}, client)).kind).toBe('ready');
    expect((await getReport('rpt_safe', headers, client)).kind).toBe('ready');
    expect((await listInvestigations(headers, {}, client)).kind).toBe('ready');
    expect((await getInvestigation('inv_safe', headers, client)).kind).toBe('ready');

    expect(request).toHaveBeenCalledTimes(8);
    for (const call of request.mock.calls) {
      const outbound = call[0] as Request;
      expect(outbound.url).toContain('/api/v2/');
      expect(outbound.headers.get('X-Tenant-Id')).toBe('tnt_safe');
      expect(outbound.headers.get('X-Service-Token')).toBeNull();
    }
  });

  it('scans the tenant report catalog and retains only the current project page', async () => {
    const summary = (pubId: string, projectPubId: string, title: string) => ({
      pub_id: pubId,
      project_pub_id: projectPubId,
      title,
      state: 'published' as const,
      created_at: '2026-07-25T08:00:00Z',
      updated_at: '2026-07-25T09:00:00Z',
    });
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      const cursor = new URL(outbound.url).searchParams.get('cursor');
      const body =
        cursor === null
          ? {
              data: [
                summary('rpt_001_other', 'prj_other', '其他项目报告一'),
                summary('rpt_002_other', 'prj_other', '其他项目报告二'),
              ],
              page: { next_cursor: 'rpt_002_other', has_more: true },
            }
          : {
              data: [
                summary('rpt_003_current', 'prj_current', '当前项目报告一'),
                summary('rpt_004_current', 'prj_current', '当前项目报告二'),
              ],
              page: { next_cursor: null, has_more: false },
            };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer-safe',
      'X-Actor-Role': 'customer' as const,
    };

    const result = await loadProjectReportCatalog(headers, 'prj_current', '', client);

    expect(result).toEqual({
      kind: 'ready',
      page: {
        data: [summary('rpt_003_current', 'prj_current', '当前项目报告一')],
        page: { next_cursor: 'rpt_003_current', has_more: true },
        projection: { total: 2, shown: 1, invalid: false },
      },
      nextCursor: 'rpt_003_current',
      projection: {
        total: 2,
        shown: 1,
        scanned: 4,
        invalid: false,
        incomplete: false,
      },
    });
    expect(request).toHaveBeenCalledTimes(2);
    expect(new URL((request.mock.calls[1]![0] as Request).url).searchParams.get('cursor')).toBe(
      'rpt_002_other',
    );
  });

  it('rejects unsafe project report catalog context without issuing a request', async () => {
    const request = vi.fn();
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer-safe',
      'X-Actor-Role': 'customer' as const,
    };

    const result = await loadProjectReportCatalog(
      headers,
      'prj_current',
      'Bearer report-cursor-canary',
      client,
    );

    expect(result.kind).toBe('ready');
    if (result.kind === 'ready') {
      expect(result.page.data).toEqual([]);
      expect(result.projection.invalid).toBe(true);
    }
    expect(request).not.toHaveBeenCalled();
  });

  it('writes report and intelligence decisions only through generated request bodies', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      if (outbound.url.endsWith('/evidence/packages')) {
        return new Response(
          JSON.stringify({
            package_pub_id: 'pkg_safe',
            manifest_sha256: 'c'.repeat(64),
            state: 'ready',
            cookie: 'SESSION=evidence-package-receipt-canary',
          }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/exports/metrics')) {
        return new Response(
          JSON.stringify({
            export_pub_id: 'exp_safe',
            evidence_pub_id: 'evd_export_safe',
            format: 'xlsx',
            row_count: 1,
            filter_hash: 'd'.repeat(64),
            fact_snapshot_hash: 'e'.repeat(64),
            metric_version: 'metrics-v2',
            scorer_version: 'scorer-v1',
            token: 'Bearer metric-export-receipt-canary',
          }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/verdicts')) {
        return new Response(
          JSON.stringify({
            verdict_pub_id: 'vrd_safe',
            profile_path: '/secret/verdict-receipt-canary',
          }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/reviews')) {
        return new Response(
          JSON.stringify({
            review_pub_id: 'rvw_safe',
            token: 'Bearer report-review-receipt-canary',
          }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/effect-retests')) {
        return new Response(
          JSON.stringify({
            effect_retest_pub_id: 'rts_safe',
            cookie: 'SESSION=effect-retest-receipt-canary',
          }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/actions')) {
        return new Response(
          JSON.stringify({
            action_pub_id: 'act_safe',
            profile_path: '/secret/report-action-receipt-canary',
          }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/appeals')) {
        return new Response(
          JSON.stringify({
            appeal_pub_id: 'apl_safe',
            otp: 'appeal-receipt-canary',
          }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/resolve')) {
        const resolution = (await outbound.clone().json()) as {
          corrected_verdict?: string | null;
        };
        return new Response(
          JSON.stringify({
            replacement_verdict_pub_id: resolution.corrected_verdict
              ? 'vrd_replacement_safe'
              : null,
            token: 'Bearer appeal-resolution-receipt-canary',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/reports/rpt_safe/versions')) {
        return new Response(
          JSON.stringify({
            report_pub_id: 'rpt_safe',
            report_version_pub_id: 'rptv_revision_safe',
            version_number: 2,
            fact_snapshot_hash: 'b'.repeat(64),
            cookie: 'SESSION=revision-response-canary',
          }),
          {
            status: 201,
            headers: { 'content-type': 'application/json' },
          },
        );
      }
      if (outbound.url.endsWith('/comments')) {
        return new Response(
          JSON.stringify({
            comment_pub_id: 'cmt_safe',
            report_pub_id: 'rpt_safe',
          }),
          {
            status: 201,
            headers: { 'content-type': 'application/json' },
          },
        );
      }
      if (outbound.url.endsWith('/deliveries')) {
        return new Response(
          JSON.stringify({
            delivery_pub_id: 'dlv_safe',
            report_pub_id: 'rpt_safe',
          }),
          {
            status: 201,
            headers: { 'content-type': 'application/json' },
          },
        );
      }
      return outbound.url.endsWith('/publish') || outbound.method === 'PATCH'
        ? new Response(null, { status: 204 })
        : new Response(JSON.stringify({ pub_id: 'receipt_safe' }), {
            status: 201,
            headers: { 'content-type': 'application/json' },
          });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'reviewer-safe',
      'X-Actor-Role': 'reviewer' as const,
    };

    const evidencePackage = await createEvidencePackage(
      {
        package_pub_id: 'pkg_safe',
        evidence_pub_ids: ['evd_safe'],
        public: false,
        expires_at: null,
      },
      headers,
      client,
    );
    expect(evidencePackage.kind).toBe('ready');
    expect(JSON.stringify(evidencePackage)).not.toContain('evidence-package-receipt-canary');
    const metricExport = await createMetricExport(
      {
        project_pub_id: 'prj_safe',
        start: '2026-07-01',
        end: '2026-07-25',
        dimensions: { model: 'doubao' },
      },
      headers,
      client,
    );
    expect(metricExport.kind).toBe('ready');
    expect(JSON.stringify(metricExport)).not.toContain('metric-export-receipt-canary');
    const revision = await createReportRevision(
      'rpt_safe',
      {
        components: [
          {
            component_type: 'section',
            source: 'human',
            title: '摘要',
            body: '真实修订',
            evidence_pub_ids: ['evd_safe'],
          },
        ],
      },
      'report-revision-idempotency-safe',
      headers,
      client,
    );
    expect(revision).toEqual({
      kind: 'ready',
      data: {
        reportPubId: 'rpt_safe',
        reportVersionPubId: 'rptv_revision_safe',
        versionNumber: 2,
        factSnapshotHash: 'b'.repeat(64),
      },
    });
    expect(JSON.stringify(revision)).not.toContain('revision-response-canary');
    const reportAction = await createReportAction(
      'rpt_safe',
      { description: '安全行动', owner_pub_id: null, baseline: { version: 1 } },
      headers,
      client,
    );
    expect(reportAction.kind).toBe('ready');
    expect(JSON.stringify(reportAction)).not.toContain('report-action-receipt-canary');
    expect(
      (
        await commentOnReport(
          'rpt_safe',
          'rptv_safe',
          { body: '安全评论', parent_pub_id: null },
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    const reportReview = await reviewReport(
      'rpt_safe',
      'rptv_safe',
      { decision: 'approved', rationale: '人工核验通过' },
      headers,
      client,
    );
    expect(reportReview.kind).toBe('ready');
    expect(JSON.stringify(reportReview)).not.toContain('report-review-receipt-canary');
    expect((await publishReport('rpt_safe', 'rptv_safe', headers, client)).kind).toBe('ready');
    const verdict = await createInvestigationVerdict(
      'inv_safe',
      { verdict: 'likely', rationale: '人工复核', workflow_operation_id: null },
      headers,
      client,
    );
    expect(verdict.kind).toBe('ready');
    expect(JSON.stringify(verdict)).not.toContain('verdict-receipt-canary');
    const appeal = await createInvestigationAppeal(
      'inv_safe',
      { reason: '补充独立来源' },
      headers,
      client,
    );
    expect(appeal.kind).toBe('ready');
    expect(JSON.stringify(appeal)).not.toContain('appeal-receipt-canary');
    const upheldResolution = await resolveInvestigationAppeal(
      'inv_safe',
      'apl_safe',
      {
        resolution: 'upheld',
        corrected_verdict: null,
        rationale: '二次复核完成',
      },
      headers,
      client,
    );
    expect(upheldResolution).toEqual({
      kind: 'ready',
      data: { replacementVerdictPubId: null },
    });
    const correctedResolution = await resolveInvestigationAppeal(
      'inv_safe',
      'apl_safe_corrected',
      {
        resolution: 'corrected',
        corrected_verdict: 'unlikely',
        rationale: '独立证据支持纠正',
      },
      headers,
      client,
    );
    expect(correctedResolution).toEqual({
      kind: 'ready',
      data: { replacementVerdictPubId: 'vrd_replacement_safe' },
    });
    expect(JSON.stringify({ upheldResolution, correctedResolution })).not.toContain(
      'appeal-resolution-receipt-canary',
    );
    expect(
      (
        await updateReportAction(
          'rpt_safe',
          'act_safe',
          { state: 'in_progress', outcome: null },
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    const effectRetest = await createReportEffectRetest(
      'rpt_safe',
      'act_safe',
      {
        measured_at: '2026-07-25T00:00:00Z',
        result: { delta: 4.8 },
      },
      headers,
      client,
    );
    expect(effectRetest.kind).toBe('ready');
    expect(JSON.stringify(effectRetest)).not.toContain('effect-retest-receipt-canary');

    expect(request).toHaveBeenCalledTimes(13);
    const revisionRequest = request.mock.calls
      .map((call) => call[0] as Request)
      .find((outbound) => outbound.url.endsWith('/reports/rpt_safe/versions'));
    expect(revisionRequest?.headers.get('Idempotency-Key')).toBe(
      'report-revision-idempotency-safe',
    );
    for (const call of request.mock.calls) {
      const outbound = call[0] as Request;
      expect(['POST', 'PATCH']).toContain(outbound.method);
      expect(outbound.headers.get('X-Service-Token')).toBeNull();
    }
  });

  it('keeps report delivery and recipient confirmation as separate generated operations', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      if (outbound.method === 'GET') {
        return new Response(
          JSON.stringify([
            {
              pub_id: 'dlv_safe',
              report_pub_id: 'rpt_safe',
              recipient_pub_id: 'customer-safe',
              delivered_at: '2026-07-25T00:00:00Z',
              confirmed_at: null,
              confirmation_comment: null,
            },
          ]),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      return new Response(
        JSON.stringify(
          outbound.url.endsWith('/confirm')
            ? { delivery_pub_id: 'dlv_safe', state: 'confirmed' }
            : { delivery_pub_id: 'dlv_safe', report_pub_id: 'rpt_safe' },
        ),
        {
          status: outbound.url.endsWith('/confirm') ? 200 : 201,
          headers: {
            'content-type': 'application/json',
          },
        },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer-safe',
      'X-Actor-Role': 'customer' as const,
    };

    expect((await listReportDeliveries('rpt_safe', headers, client)).kind).toBe('ready');
    expect(
      (
        await createReportDelivery(
          'rpt_safe',
          { recipient_pub_id: 'customer-safe' },
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');
    expect(
      (
        await confirmReportDelivery(
          'rpt_safe',
          'dlv_safe',
          { confirmation_comment: '已收到并完成核验' },
          headers,
          client,
        )
      ).kind,
    ).toBe('ready');

    const requests = request.mock.calls.map(([input]) => input as Request);
    expect(requests.map((item) => item.method)).toEqual(['GET', 'POST', 'POST']);
    expect(requests[0]!.url).toMatch(/\/api\/v2\/reports\/rpt_safe\/deliveries$/);
    expect(requests[2]!.url).toMatch(
      /\/api\/v2\/reports\/rpt_safe\/deliveries\/dlv_safe\/confirm$/,
    );
    expect(await requests[1]!.clone().json()).toEqual({ recipient_pub_id: 'customer-safe' });
    expect(await requests[2]!.clone().json()).toEqual({
      confirmation_comment: '已收到并完成核验',
    });
    for (const outbound of requests) {
      expect(outbound.headers.get('X-Tenant-Id')).toBe('tnt_safe');
      expect(outbound.headers.get('X-Service-Token')).toBeNull();
    }
  });

  it('rejects mismatched or secret-shaped write receipts before application state', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      if (outbound.url.endsWith('/exports/metrics')) {
        return new Response(
          JSON.stringify({
            export_pub_id: 'exp_zero_rows',
            evidence_pub_id: 'evd_safe',
            format: 'xlsx',
            row_count: 0,
            filter_hash: 'd'.repeat(64),
            fact_snapshot_hash: 'e'.repeat(64),
            metric_version: 'metrics-v2',
            scorer_version: 'scorer-v1',
            token: 'Bearer metric-export-receipt-canary',
          }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/evidence/packages')) {
        return new Response(
          JSON.stringify({
            package_pub_id: 'pkg_other',
            manifest_sha256: 'c'.repeat(64),
            state: 'ready',
            cookie: 'SESSION=evidence-package-receipt-canary',
          }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/verdicts')) {
        return new Response(JSON.stringify({ verdict_pub_id: 'Bearer verdict-receipt-canary' }), {
          status: 201,
          headers: { 'content-type': 'application/json' },
        });
      }
      if (outbound.url.endsWith('/reviews')) {
        return new Response(
          JSON.stringify({ review_pub_id: 'Bearer report-review-receipt-canary' }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/effect-retests')) {
        return new Response(
          JSON.stringify({ effect_retest_pub_id: 'SESSION=effect-retest-receipt-canary' }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/actions')) {
        return new Response(
          JSON.stringify({ action_pub_id: 'Bearer report-action-receipt-canary' }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/appeals')) {
        return new Response(JSON.stringify({ appeal_pub_id: 'otp=appeal-receipt-canary' }), {
          status: 201,
          headers: { 'content-type': 'application/json' },
        });
      }
      if (outbound.url.endsWith('/resolve')) {
        return new Response(
          JSON.stringify({
            replacement_verdict_pub_id: 'Bearer appeal-resolution-receipt-canary',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/comments')) {
        return new Response(
          JSON.stringify({
            comment_pub_id: 'Bearer comment-receipt-canary',
            report_pub_id: 'rpt_other',
          }),
          { status: 201, headers: { 'content-type': 'application/json' } },
        );
      }
      if (outbound.url.endsWith('/confirm')) {
        return new Response(
          JSON.stringify({
            delivery_pub_id: 'dlv_other',
            state: 'confirmed',
            token: 'Bearer confirmation-receipt-canary',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      return new Response(
        JSON.stringify({
          delivery_pub_id: 'dlv_safe',
          report_pub_id: 'rpt_other',
          cookie: 'SESSION=delivery-receipt-canary',
        }),
        { status: 201, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'usr_customer_safe',
      'X-Actor-Role': 'customer' as const,
    };

    expect(
      await commentOnReport(
        'rpt_safe',
        'rptv_safe',
        { body: '安全问题', parent_pub_id: null },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await createReportDelivery(
        'rpt_safe',
        { recipient_pub_id: 'usr_customer_safe' },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await confirmReportDelivery(
        'rpt_safe',
        'dlv_safe',
        { confirmation_comment: '确认收到' },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await createMetricExport(
        {
          project_pub_id: 'prj_safe',
          start: '2026-07-01',
          end: '2026-07-25',
          dimensions: {},
        },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await createEvidencePackage(
        {
          package_pub_id: 'pkg_safe',
          evidence_pub_ids: ['evd_safe'],
          public: false,
          expires_at: null,
        },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await createInvestigationVerdict(
        'inv_safe',
        { verdict: 'likely', rationale: '人工裁决', workflow_operation_id: null },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await reviewReport(
        'rpt_safe',
        'rptv_safe',
        { decision: 'approved', rationale: '人工核验通过' },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await createReportAction(
        'rpt_safe',
        { description: '安全行动', owner_pub_id: null, baseline: {} },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await createReportEffectRetest(
        'rpt_safe',
        'act_safe',
        { measured_at: '2026-07-25T00:00:00Z', result: {} },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await createInvestigationAppeal('inv_safe', { reason: '补充独立来源' }, headers, client),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await resolveInvestigationAppeal(
        'inv_safe',
        'apl_safe',
        {
          resolution: 'upheld',
          corrected_verdict: null,
          rationale: '独立复核完成',
        },
        headers,
        client,
      ),
    ).toEqual({ kind: 'unavailable' });
  });

  it('projects the complete customer dashboard and rejects any operational task field', async () => {
    const metric = (code: string, value: number | null = 0.5) => ({
      code,
      label: code === 'mention_rate' ? '品牌提及率' : 'GEO 可见度指数',
      group: code === 'mention_rate' ? 'visibility' : 'composite',
      format: code === 'mention_rate' ? 'percentage' : 'score',
      direction: 'higher',
      value,
      state: value === null ? 'not_ready' : 'ready',
      version: 'customer-metrics-v1',
    });
    const payload = {
      schema_version: 'customer-dashboard-v1',
      metric_version: 'customer-metrics-v1',
      project_pub_id: 'prj_safe',
      brand_name: '盛邦安全',
      state: 'ready',
      generated_at: '2026-08-17T08:00:00Z',
      as_of: '2026-08-16T08:00:00Z',
      window: { start: '2026-08-01', end: '2026-08-17', filters: {} },
      metrics: [metric('geo_visibility_index', 72), metric('mention_rate')],
      models: [{ key: 'doubao', label: '豆包', metrics: [metric('mention_rate')] }],
      competitors: [{ name: '竞品 A', metrics: [metric('mention_rate', 0.3)] }],
      questions: [
        {
          // Opaque hashes may naturally contain phone-shaped digit runs. The fixed
          // qry_ grammar is the security boundary; prose DLP must not reject the ID.
          query_pub_id: 'qry_hash_b5855173086854844b54',
          query_text: '安全厂商怎么选',
          query_group: '选型',
          metrics: [metric('mention_rate')],
        },
      ],
      sources: [
        {
          host: 'example.com',
          references: 2,
          share: 1,
          own_source: true,
          answers: 2,
        },
        {
          host: '101.132.138.0',
          references: 1,
          share: 0.25,
          own_source: false,
          answers: 1,
        },
        {
          host: 'www.962600.com',
          references: 1,
          share: 0.25,
          own_source: false,
          answers: 1,
        },
      ],
      regions: [{ key: '华东', label: '华东', metrics: [metric('mention_rate')] }],
      modes: [{ key: 'deep', label: 'deep', metrics: [metric('mention_rate')] }],
      trends: [{ date: '2026-08-16', metrics: [metric('mention_rate')] }],
      risk: { metrics: [], by_model: [] },
      source_audit: { metrics: [], verdicts: { accurate: 2 } },
      snapshot_hash: 'a'.repeat(64),
    };

    const projectedDashboard = projectCustomerDashboardBoundary(payload, 'prj_safe');
    expect(projectedDashboard?.brand_name).toBe('盛邦安全');
    expect(projectedDashboard?.metrics).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: 'geo_visibility_index', value: 72 }),
      ]),
    );
    expect(projectedDashboard?.questions[0]?.query_pub_id).toBe('qry_hash_b5855173086854844b54');
    expect(projectedDashboard?.sources.map((source) => source.host)).toEqual([
      'example.com',
      '101.132.138.0',
      'www.962600.com',
    ]);
    expect(
      projectCustomerDashboardBoundary(
        { ...payload, internal: { success_rate: 0.98 } },
        'prj_safe',
      ),
    ).toBeNull();
    expect(projectCustomerDashboardBoundary(payload, 'prj_other')).toBeNull();

    const catalog = {
      schema_version: 'customer-metric-catalog-v1',
      metrics: [
        {
          ...metric('mention_rate'),
          description: '有效回答中提到目标品牌的比例。',
        },
      ].map(({ value: _value, state: _state, ...item }) => item),
    };
    expect(projectCustomerMetricCatalogBoundary(catalog)?.metrics).toHaveLength(1);

    const request = vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL((input as Request).url).pathname;
      return new Response(JSON.stringify(path.endsWith('/metrics/catalog') ? catalog : payload), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer-safe',
      'X-Actor-Role': 'customer' as const,
    };
    await expect(
      getCustomerDashboard('prj_safe', '2026-08-01', '2026-08-17', {}, headers, client),
    ).resolves.toMatchObject({ kind: 'ready', data: { brand_name: '盛邦安全' } });
    await expect(getCustomerMetricCatalog(headers, client)).resolves.toMatchObject({
      kind: 'ready',
      data: { metrics: [{ code: 'mention_rate' }] },
    });
  });

  it('projects customer answer pages with complete public answer text and exact pagination', async () => {
    const payload = {
      schema_version: 'customer-answer-page-v1',
      project_pub_id: 'prj_safe',
      data: [
        {
          answer_pub_id: 'ans_safe_01',
          query_pub_id: 'qry_hash_b5855173086854844b54',
          query_text: '盛邦安全的安全能力与客服电话是什么？',
          response_text:
            '回答原文保留公开数字内容：访问 https://39.105.175.14:8443，或联系 400-123-4567。\n第二段也应完整显示。',
          model: 'deepseek',
          region: '中国',
          mode: 'deep',
          capture_time: '2026-08-17T08:00:00Z',
          mentioned: true,
          rank: 1,
          sentiment: 'positive',
          recommended: true,
          citation_count: 3,
        },
      ],
      page: { total: 21, offset: 20, limit: 20, has_more: false },
    };

    const projected = projectCustomerAnswerPageBoundary(payload, 'prj_safe', 20, 20);
    expect(projected?.data[0]?.response_text).toContain('39.105.175.14:8443');
    expect(projected?.data[0]?.response_text).toContain('400-123-4567');
    expect(projected?.page).toEqual({ total: 21, offset: 20, limit: 20, has_more: false });
    expect(
      projectCustomerAnswerPageBoundary(
        { ...payload, internal: { task_success_rate: 0.98 } },
        'prj_safe',
        20,
        20,
      ),
    ).toBeNull();
    expect(
      projectCustomerAnswerPageBoundary(
        { ...payload, page: { ...payload.page, has_more: true } },
        'prj_safe',
        20,
        20,
      ),
    ).toBeNull();

    const request = vi.fn(
      async (_input: RequestInfo | URL) =>
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const headers = {
      'X-Tenant-Id': 'tnt_safe',
      'X-Actor-Id': 'customer-safe',
      'X-Actor-Role': 'customer' as const,
    };
    await expect(
      getCustomerAnswerPage(
        'prj_safe',
        '2026-08-01',
        '2026-08-17',
        {
          search: '盛邦安全',
          mentioned: true,
          sentiment: 'positive',
          offset: 20,
          limit: 20,
        },
        headers,
        client,
      ),
    ).resolves.toMatchObject({
      kind: 'ready',
      data: { data: [{ answer_pub_id: 'ans_safe_01' }], page: { total: 21 } },
    });
    const outbound = request.mock.calls[0]?.[0] as Request;
    const url = new URL(outbound.url);
    expect(url.pathname).toBe('/api/v2/customer-dashboard/projects/prj_safe/answers');
    expect(Object.fromEntries(url.searchParams)).toMatchObject({
      start: '2026-08-01',
      end: '2026-08-17',
      search: '盛邦安全',
      mentioned: 'true',
      sentiment: 'positive',
      offset: '20',
      limit: '20',
    });
    expect(outbound.headers.get('X-Tenant-Id')).toBe('tnt_safe');
  });
});

describe('media prices dataset boundary', () => {
  const headers = {
    'X-Tenant-Id': 'tnt_safe',
    'X-Actor-Id': 'operator-safe',
    'X-Actor-Role': 'operator',
  } as const;
  const datasetPayload = {
    generated_at: '2026-07-27 15:53',
    sources: {
      prfabu: 'prfabu媒体管家',
      toumeiw: '投媒网',
      mtpfw: '媒体批发网',
      meititejia: '媒体特价网',
      meijiehezi: '媒介盒子',
      pinda: '品达发稿',
    },
    partial: { toumeiw: true },
    stats: {
      counts: { prfabu: 2, toumeiw: 1, mtpfw: 1, meititejia: 0, meijiehezi: 0, pinda: 0 },
      geo_counts: {
        prfabu: 1,
        toumeiw: 0,
        mtpfw: 0,
        meititejia: 0,
        meijiehezi: 0,
        pinda: 0,
      },
      unique_media: 2,
      matched_2plus: 1,
      matched_3: 0,
      geo_union: 2,
      geo_multi_src: 0,
    },
    rows: [
      {
        name: '示例媒体',
        prices: { prfabu: 100, toumeiw: 80 },
        best: 80,
        best_plat: 'toumeiw',
        spread: 1.3,
        n_src: 2,
        geo: ['b'],
        geo_n: 1,
        portal: '门户网站',
      },
      {
        name: 'GEO 交叉媒体',
        prices: { prfabu: 50 },
        best: 50,
        best_plat: 'prfabu',
        spread: null,
        n_src: 1,
        geo: ['a', 'f'],
        geo_n: 1,
      },
    ],
  };

  async function sha256Hex(bytes: Uint8Array): Promise<string> {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes.buffer as ArrayBuffer);
    return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('');
  }

  async function jsonArrayBufferResponse(
    payload: unknown,
    init: { status?: number; sha256?: string | null } = {},
  ) {
    const bytes = new TextEncoder().encode(JSON.stringify(payload));
    const sha256 = init.sha256 === undefined ? await sha256Hex(bytes) : init.sha256;
    return new Response(bytes, {
      status: init.status ?? 200,
      headers: {
        'content-type': 'application/json',
        ...(sha256 ? { 'x-dataset-sha256': sha256 } : {}),
      },
    });
  }

  it('loads the dataset artifact with the generated path and sha256 header', async () => {
    let expectedSha256 = '';
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const outbound = input as Request;
      expect(new URL(outbound.url).pathname).toBe('/api/v2/datasets/media-prices');
      const response = await jsonArrayBufferResponse(datasetPayload);
      expectedSha256 = response.headers.get('x-dataset-sha256') ?? '';
      return response;
    });
    vi.stubGlobal('fetch', request);
    const result = await getMediaPricesDataset(headers, createGeoApiClient('https://geo.example'));
    expect(request).toHaveBeenCalledTimes(1);
    if (result.kind !== 'ready') throw new Error('expected ready dataset');
    expect(result.data.generatedAt).toBe('2026-07-27 15:53');
    expect(result.data.rows).toHaveLength(2);
    expect(result.data.stats.unique_media).toBe(2);
    expect(result.data.sha256).toBe(expectedSha256);
  });

  it('classifies 403 as forbidden, 404 as missing and rejects malformed envelopes', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: { code: 'permission_denied' } }), {
            status: 403,
            headers: { 'content-type': 'application/json' },
          }),
      ),
    );
    expect(await getMediaPricesDataset(headers, createGeoApiClient('https://geo.example'))).toEqual(
      { kind: 'forbidden' },
    );

    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: { code: 'dataset_not_found' } }), {
            status: 404,
            headers: { 'content-type': 'application/json' },
          }),
      ),
    );
    expect(await getMediaPricesDataset(headers, createGeoApiClient('https://geo.example'))).toEqual(
      { kind: 'missing' },
    );

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonArrayBufferResponse({ generated_at: 'x', rows: 'not-an-array' })),
    );
    expect(await getMediaPricesDataset(headers, createGeoApiClient('https://geo.example'))).toEqual(
      { kind: 'unavailable' },
    );
    expect(projectMediaPricesDataset(null, null)).toBeNull();
    expect(projectMediaPricesDataset({ ...datasetPayload, stats: null }, null)).toBeNull();
    expect(
      projectMediaPricesDataset(
        { ...datasetPayload, rows: [{ name: '', prices: {}, geo: [] }] },
        null,
      ),
    ).toBeNull();
    const projected = projectMediaPricesDataset(datasetPayload, null);
    expect(projected?.rows[1]?.name).toBe('GEO 交叉媒体');
    expect(projected?.sha256).toBeNull();
  });

  it('projects a strict media dataset allow-list and fails closed on secret-shaped display data', () => {
    const projected = projectMediaPricesDataset(
      {
        ...datasetPayload,
        access_token: 'dataset-envelope-canary',
        sources: {
          ...datasetPayload.sources,
          access_token: 'dataset-source-canary',
        },
        stats: {
          ...datasetPayload.stats,
          counts: {
            ...datasetPayload.stats.counts,
            access_token: 1,
          },
        },
        rows: [
          {
            ...datasetPayload.rows[0],
            access_token: 'dataset-row-canary',
            ids: { Cookie: 'dataset-id-canary', prfabu: 12345 },
          },
          datasetPayload.rows[1],
        ],
      },
      null,
    );
    expect(projected).not.toBeNull();
    expect(JSON.stringify(projected)).not.toMatch(
      /access_token|dataset-envelope-canary|dataset-source-canary|dataset-row-canary|Cookie|dataset-id-canary/i,
    );
    expect(projected?.rows[0]?.ids).toEqual({ prfabu: '12345' });

    const secretOptional = projectMediaPricesDataset(
      {
        ...datasetPayload,
        rows: [{ ...datasetPayload.rows[0], remark: 'OTP 824911' }, datasetPayload.rows[1]],
      },
      null,
    );
    expect(secretOptional?.rows[0]).not.toHaveProperty('remark');
    expect(JSON.stringify(secretOptional)).not.toMatch(/OTP|824911/i);
    expect(
      projectMediaPricesDataset(
        {
          ...datasetPayload,
          rows: [{ ...datasetPayload.rows[0], name: '联系人 13800138000' }],
        },
        null,
      ),
    ).toBeNull();
  });

  it('accepts a completed real-zero media dataset and rejects malformed numeric or GEO rows', () => {
    const realZero = projectMediaPricesDataset(
      {
        ...datasetPayload,
        stats: {
          counts: {
            prfabu: 0,
            toumeiw: 0,
            mtpfw: 0,
            meititejia: 0,
            meijiehezi: 0,
            pinda: 0,
          },
          geo_counts: {
            prfabu: 0,
            toumeiw: 0,
            mtpfw: 0,
            meititejia: 0,
            meijiehezi: 0,
            pinda: 0,
          },
          unique_media: 0,
          matched_2plus: 0,
          matched_3: 0,
          geo_union: 0,
          geo_multi_src: 0,
        },
        rows: [],
      },
      null,
    );
    expect(realZero?.rows).toEqual([]);
    const withPinda = projectMediaPricesDataset(
      {
        ...datasetPayload,
        stats: {
          ...datasetPayload.stats,
          counts: { ...datasetPayload.stats.counts, pinda: 1 },
          matched_2plus: 2,
        },
        rows: [
          datasetPayload.rows[0],
          {
            ...datasetPayload.rows[1],
            prices: { prfabu: 50, pinda: 40 },
            best: 40,
            best_plat: 'pinda',
            spread: 1.3,
            n_src: 2,
          },
        ],
      },
      null,
    );
    expect(withPinda?.rows[1]?.best_plat).toBe('pinda');
    expect(
      projectMediaPricesDataset(
        {
          ...datasetPayload,
          rows: [{ ...datasetPayload.rows[0], prices: { prfabu: -1 } }],
        },
        null,
      ),
    ).toBeNull();
    expect(
      projectMediaPricesDataset(
        {
          ...datasetPayload,
          rows: [{ ...datasetPayload.rows[0], geo: ['a', 'unknown'] }],
        },
        null,
      ),
    ).toBeNull();
  });

  it('rejects inconsistent row derivations, duplicate names and aggregate statistics', () => {
    const rejects = [
      { ...datasetPayload.rows[0], best: 81 },
      { ...datasetPayload.rows[0], best_plat: 'prfabu' },
      { ...datasetPayload.rows[0], spread: 9.9 },
      { ...datasetPayload.rows[0], n_src: 1 },
      { ...datasetPayload.rows[0], geo_n: 0 },
    ];
    for (const row of rejects) {
      expect(
        projectMediaPricesDataset({ ...datasetPayload, rows: [row, datasetPayload.rows[1]] }, null),
      ).toBeNull();
    }
    expect(
      projectMediaPricesDataset(
        {
          ...datasetPayload,
          rows: [datasetPayload.rows[0], { ...datasetPayload.rows[1], name: '示例媒体' }],
        },
        null,
      ),
    ).toBeNull();
    expect(
      projectMediaPricesDataset(
        {
          ...datasetPayload,
          stats: { ...datasetPayload.stats, matched_2plus: 0 },
        },
        null,
      ),
    ).toBeNull();
    expect(
      projectMediaPricesDataset(
        {
          ...datasetPayload,
          stats: {
            ...datasetPayload.stats,
            counts: { ...datasetPayload.stats.counts, toumeiw: 0 },
          },
        },
        null,
      ),
    ).toBeNull();
  });

  it('rejects wrong content-type, invalid JSON, digest mismatch and oversized artifacts', async () => {
    const bytes = new TextEncoder().encode(JSON.stringify(datasetPayload));
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () => new Response(bytes, { status: 200, headers: { 'content-type': 'text/html' } }),
      ),
    );
    expect(await getMediaPricesDataset(headers, createGeoApiClient('https://geo.example'))).toEqual(
      { kind: 'unavailable' },
    );

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonArrayBufferResponse(datasetPayload, { sha256: 'a'.repeat(64) })),
    );
    expect(await getMediaPricesDataset(headers, createGeoApiClient('https://geo.example'))).toEqual(
      { kind: 'unavailable' },
    );

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonArrayBufferResponse(datasetPayload, { sha256: null })),
    );
    expect(await getMediaPricesDataset(headers, createGeoApiClient('https://geo.example'))).toEqual(
      { kind: 'unavailable' },
    );

    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(new TextEncoder().encode('{broken'), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }),
      ),
    );
    expect(await getMediaPricesDataset(headers, createGeoApiClient('https://geo.example'))).toEqual(
      { kind: 'unavailable' },
    );

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        const oversized = new ArrayBuffer(25 * 1024 * 1024 + 1);
        return new Response(oversized, {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
    expect(await getMediaPricesDataset(headers, createGeoApiClient('https://geo.example'))).toEqual(
      { kind: 'unavailable' },
    );
  });
});

describe('wemedia dataset boundary', () => {
  const headers = {
    'X-Tenant-Id': 'tnt_safe',
    'X-Actor-Id': 'operator-safe',
    'X-Actor-Role': 'operator',
  } as const;
  const payload = {
    generated_at: '2026-07-28 18:30',
    sources: {
      prfabu: 'prfabu媒体管家',
      toumeiw: '投媒网',
      mtpfw: '媒体批发网',
      meititejia: '媒体特价网',
      meijiehezi: '媒介盒子',
      pinda: '品达发稿',
    },
    partial: {
      prfabu: false,
      toumeiw: false,
      mtpfw: false,
      meititejia: false,
      meijiehezi: false,
      pinda: false,
    },
    stats: {
      counts: { prfabu: 1, toumeiw: 1, mtpfw: 0, meititejia: 0, meijiehezi: 0, pinda: 0 },
      geo_counts: {
        prfabu: 1,
        toumeiw: 0,
        mtpfw: 0,
        meititejia: 0,
        meijiehezi: 0,
        pinda: 0,
      },
      unique_media: 1,
      matched_2plus: 1,
      matched_3: 0,
      geo_union: 1,
      geo_multi_src: 0,
    },
    rows: [
      {
        name: '示例账号',
        platform: '百家号',
        prices: { prfabu: 100, toumeiw: 80 },
        best: 80,
        best_plat: 'toumeiw',
        spread: 1.3,
        n_src: 2,
        geo: ['e'],
        geo_n: 1,
        industry: '新闻',
        fans: '1万-5万',
        reads: '5001-1万',
        fans_level: 50,
        reads_level: 10,
      },
    ],
  };

  it('projects the self-media allow-list and enforces account-platform identity', () => {
    const projected = projectMediaWemediaDataset(payload, null);
    expect(projected?.rows[0]).toMatchObject({
      name: '示例账号',
      platform: '百家号',
      best: 80,
      fans: '1万-5万',
    });
    expect(
      projectMediaWemediaDataset(
        {
          ...payload,
          stats: { ...payload.stats, unique_media: 2 },
          rows: [...payload.rows, payload.rows[0]],
        },
        null,
      ),
    ).toBeNull();
    expect(
      projectMediaWemediaDataset({ ...payload, rows: [{ ...payload.rows[0], best: 81 }] }, null),
    ).toBeNull();
  });

  it('loads the lazy self-media artifact from its separate endpoint with sha256 verification', async () => {
    const bytes = new TextEncoder().encode(JSON.stringify(payload));
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    const sha256 = [...new Uint8Array(digest)]
      .map((value) => value.toString(16).padStart(2, '0'))
      .join('');
    const request = vi.fn(async (input: RequestInfo | URL) => {
      expect(new URL((input as Request).url).pathname).toBe('/api/v2/datasets/media-wemedia');
      return new Response(bytes, {
        status: 200,
        headers: { 'content-type': 'application/json', 'x-dataset-sha256': sha256 },
      });
    });
    vi.stubGlobal('fetch', request);
    const result = await getMediaWemediaDataset(headers, createGeoApiClient('https://geo.example'));
    expect(result.kind).toBe('ready');
    expect(request).toHaveBeenCalledTimes(1);
  });
});

describe('posting batch browser boundary', () => {
  it('sends the DOCX as multipart data and projects per-media posting status', async () => {
    const contract = {
      pub_id: 'pbt_test',
      tenant_pub_id: 'tnt_test',
      source_filename: 'article.docx',
      source_sha256: 'a'.repeat(64),
      catalog_sha256: 'b'.repeat(64),
      title: '自动发帖标题',
      content_text: '自动发帖标题\n\n正文',
      image_count: 1,
      customer_name: '测试品牌',
      release_time: null,
      auto_submit: true,
      spend_confirmed_at: '2026-07-29T08:00:00Z',
      max_total_amount: '88.00',
      quoted_total_amount: '88.00',
      status: 'queued',
      note: '',
      sop_project_pub_id: null,
      article_version_pub_id: null,
      approval_state: 'approved',
      approval_requested_by_pub_id: 'usr_test',
      approved_by_pub_id: 'usr_test',
      approved_at: '2026-07-29T08:00:00Z',
      created_by_pub_id: 'usr_test',
      created_at: '2026-07-29T08:00:00Z',
      updated_at: '2026-07-29T08:00:00Z',
      targets: [
        {
          pub_id: 'ptg_test',
          tenant_pub_id: 'tnt_test',
          batch_pub_id: 'pbt_test',
          catalog_type: 'news',
          provider: 'prfabu',
          media_name: '测试媒体',
          media_platform: '',
          provider_media_id: '123',
          quoted_price: '88.00',
          status: 'queued',
          external_order_id: '',
          public_url: '',
          provider_message: '',
          submitted_at: null,
          published_at: null,
          created_at: '2026-07-29T08:00:00Z',
          updated_at: '2026-07-29T08:00:00Z',
        },
      ],
      events: [
        {
          pub_id: 'pev_test',
          tenant_pub_id: 'tnt_test',
          batch_pub_id: 'pbt_test',
          target_pub_id: null,
          event_type: 'batch.created',
          from_status: '',
          to_status: 'queued',
          message: '已创建',
          payload: {},
          actor_pub_id: 'usr_test',
          created_at: '2026-07-29T08:00:00Z',
        },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request = input as Request;
      expect(request.headers.get('content-type')).toContain('multipart/form-data');
      expect(request.headers.get('idempotency-key')).toBe('posting-test-idempotency-0001');
      const form = await request.clone().formData();
      expect((form.get('document') as File).name).toBe('article.docx');
      expect(form.get('targets_json')).toContain('"provider":"prfabu"');
      expect(form.get('confirm_spend')).toBe('true');
      return new Response(JSON.stringify(contract), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    const result = await createPostingBatch(
      {
        document: new File(['docx'], 'article.docx', {
          type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        }),
        targets: [
          {
            catalogType: 'news',
            provider: 'prfabu',
            mediaName: '测试媒体',
          },
        ],
        autoSubmit: true,
        confirmSpend: true,
        maxTotalAmount: 88,
        idempotencyKey: 'posting-test-idempotency-0001',
      },
      {
        'X-Tenant-Id': 'tnt_test',
        'X-Actor-Id': 'usr_test',
        'X-Actor-Role': 'operator',
      },
      createGeoApiClient('https://geo.example'),
    );
    expect(result).toMatchObject({
      kind: 'ready',
      data: {
        pubId: 'pbt_test',
        title: '自动发帖标题',
        contentText: '自动发帖标题\n\n正文',
        quotedTotalAmount: 88,
        targets: [
          {
            provider: 'prfabu',
            mediaName: '测试媒体',
            status: 'queued',
          },
        ],
      },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('quotation generation browser boundary', () => {
  const quotationHeaders = {
    'X-Tenant-Id': 'tnt_test',
    'X-Actor-Id': 'usr_test',
    'X-Actor-Role': 'operator',
  } as const;
  const docxMime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

  it('uploads XLSX multipart and verifies the returned DOCX digest and metadata', async () => {
    const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x14, 0x00, 0x06, 0x00]);
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    const sha256 = [...new Uint8Array(digest)]
      .map((value) => value.toString(16).padStart(2, '0'))
      .join('');
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request = input as Request;
      expect(new URL(request.url).pathname).toBe('/api/v2/quotations/generate');
      expect(request.headers.get('accept')).toBe(docxMime);
      expect(request.headers.get('content-type')).toContain('multipart/form-data');
      const form = await request.clone().formData();
      expect(form.get('brand_name')).toBe('盛邦安全');
      expect(form.get('quote_date')).toBe('2026-08-12');
      expect((form.get('target_words') as File).name).toBe('盛邦目标词.xlsx');
      return new Response(bytes, {
        status: 200,
        headers: {
          'content-type': docxMime,
          'content-disposition':
            "attachment; filename*=UTF-8''%E6%8A%A5%E4%BB%B7%E5%8D%95-%E7%9B%9B%E9%82%A6%E5%AE%89%E5%85%A8-20260812.docx",
          'x-quotation-sha256': sha256,
          'x-quotation-target-query-count': '64',
          'x-quotation-selected-query-count': '18',
          'x-quotation-opportunity-count': '16',
        },
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    const result = await generateQuotation(
      {
        brandName: ' 盛邦安全 ',
        quoteDate: '2026-08-12',
        targetWords: new File(['xlsx'], '盛邦目标词.xlsx', {
          type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }),
      },
      quotationHeaders,
      createGeoApiClient('https://geo.example'),
    );
    expect(result).toMatchObject({
      kind: 'ready',
      data: {
        fileName: '报价单-盛邦安全-20260812.docx',
        sha256,
        targetQueryCount: 64,
        selectedQueryCount: 18,
        opportunityCount: 16,
      },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('rejects a digest mismatch and classifies an unavailable model configuration', async () => {
    const file = new File(['xlsx'], '目标词.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(new Uint8Array([0x50, 0x4b, 0x03, 0x04]), {
            status: 200,
            headers: {
              'content-type': docxMime,
              'content-disposition':
                "attachment; filename*=UTF-8''%E6%8A%A5%E4%BB%B7%E5%8D%95-%E6%B5%8B%E8%AF%95%E5%93%81%E7%89%8C-20260812.docx",
              'x-quotation-sha256': 'a'.repeat(64),
              'x-quotation-target-query-count': '10',
              'x-quotation-selected-query-count': '10',
              'x-quotation-opportunity-count': '16',
            },
          }),
      ),
    );
    expect(
      await generateQuotation(
        { brandName: '测试品牌', quoteDate: '2026-08-12', targetWords: file },
        quotationHeaders,
        createGeoApiClient('https://geo.example'),
      ),
    ).toEqual({ kind: 'unavailable' });

    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: { code: 'llm_disabled' } }), {
            status: 503,
            headers: { 'content-type': 'application/json' },
          }),
      ),
    );
    expect(
      await generateQuotation(
        { brandName: '测试品牌', quoteDate: '2026-08-12', targetWords: file },
        quotationHeaders,
        createGeoApiClient('https://geo.example'),
      ),
    ).toEqual({ kind: 'disabled' });
  });
});

describe('media prices refresh boundary', () => {
  const headers = {
    'X-Tenant-Id': 'tnt_safe',
    'X-Actor-Id': 'operator-safe',
    'X-Actor-Role': 'operator',
  } as const;

  it('starts a refresh and maps 202/409/403 honestly', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL((input as Request).url);
        calls.push(url.pathname);
        expect(url.pathname).toBe('/api/v2/datasets/media-prices/refresh');
        return new Response(
          JSON.stringify({ state: 'running', started_at: null, message: 'refresh_started' }),
          { status: 202, headers: { 'content-type': 'application/json' } },
        );
      }),
    );
    expect(
      await requestMediaPricesRefresh(headers, createGeoApiClient('https://geo.example')),
    ).toEqual({ kind: 'started' });
    expect(calls).toHaveLength(1);

    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: { code: 'refresh_already_running' } }), {
            status: 409,
            headers: { 'content-type': 'application/json' },
          }),
      ),
    );
    expect(
      await requestMediaPricesRefresh(headers, createGeoApiClient('https://geo.example')),
    ).toEqual({ kind: 'already_running' });

    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: { code: 'permission_denied' } }), {
            status: 403,
            headers: { 'content-type': 'application/json' },
          }),
      ),
    );
    expect(
      await requestMediaPricesRefresh(headers, createGeoApiClient('https://geo.example')),
    ).toEqual({ kind: 'forbidden' });
  });

  it('loads refresh status with per-source projections and rejects drift', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              state: 'done',
              started_at: '2026-07-27 16:00:00',
              updated_at: '2026-07-27 16:01:00',
              message: 'prfabu 19087 · 投媒网 10004(限流)',
              sources: {
                prfabu: { status: 'ok', rows: 19087, note: '' },
                toumeiw: { status: 'partial', rows: 10004, note: 'rate_limited' },
                mtpfw: { status: 'ok', rows: 8800, note: '' },
                meititejia: { status: 'ok', rows: 9000, note: '' },
                meijiehezi: { status: 'ok', rows: 8500, note: '' },
                pinda: { status: 'ok', rows: 33_471, note: '' },
              },
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
    const result = await getMediaPricesRefreshStatus(
      headers,
      createGeoApiClient('https://geo.example'),
    );
    if (result.kind !== 'ready') throw new Error('expected ready refresh status');
    expect(result.data.state).toBe('done');
    expect(result.data.sources.prfabu).toEqual({ status: 'ok', rows: 19087, note: '' });
    expect(result.data.sources.toumeiw?.status).toBe('partial');
    expect(projectMediaPricesRefreshStatus({ state: 'bogus' })).toBeNull();
    expect(
      projectMediaPricesRefreshStatus({
        state: 'done',
        sources: { prfabu: { status: 'ok', rows: -1, note: '' } },
      }),
    ).toBeNull();
    expect(projectMediaPricesRefreshStatus({ state: 'never' })).toEqual({
      state: 'never',
      startedAt: null,
      updatedAt: null,
      message: '',
      sources: {},
    });
  });

  it('fails closed on secret-bearing or unbounded media refresh projections', () => {
    const safeStatus = {
      state: 'done',
      started_at: '2026-07-27 16:00:00',
      updated_at: '2026-07-27 16:01:00',
      message: 'prfabu 19087 · 投媒网 10004(限流)',
      sources: {
        prfabu: { status: 'ok', rows: 19087, note: '' },
        toumeiw: { status: 'partial', rows: 10004, note: 'rate_limited' },
        mtpfw: { status: 'ok', rows: 8800, note: '' },
        meititejia: { status: 'ok', rows: 9000, note: '' },
        meijiehezi: { status: 'ok', rows: 8500, note: '' },
        pinda: { status: 'ok', rows: 33_471, note: '' },
      },
    };
    expect(projectMediaPricesRefreshStatus(safeStatus)?.state).toBe('done');
    for (const hostile of [
      { ...safeStatus, message: 'Bearer media-refresh-secret-canary' },
      { ...safeStatus, started_at: '/secret/browser/profile/refresh-canary' },
      {
        ...safeStatus,
        sources: {
          ...safeStatus.sources,
          mtpfw: { status: 'failed', rows: 0, note: 'OTP 824911' },
        },
      },
      {
        ...safeStatus,
        sources: {
          ...safeStatus.sources,
          access_token: { status: 'ok', rows: 1, note: '' },
        },
      },
      {
        ...safeStatus,
        sources: { prfabu: { status: 'ok', rows: 200_001, note: '' } },
      },
      { ...safeStatus, sources: 'not-a-source-map' },
    ]) {
      expect(projectMediaPricesRefreshStatus(hostile)).toBeNull();
    }
  });

  it('rejects semantically incomplete completed media refresh projections', () => {
    const completed = {
      state: 'done',
      started_at: '2026-07-27 20:55:00',
      updated_at: '2026-07-27 20:55:02',
      message: '刷新任务结束',
      sources: {
        prfabu: { status: 'ok', rows: 3, note: '' },
        toumeiw: { status: 'partial', rows: 2, note: 'rate_limited' },
        mtpfw: { status: 'stale', rows: 1, note: 'source_unavailable' },
        meititejia: { status: 'ok', rows: 2, note: '' },
        meijiehezi: { status: 'ok', rows: 2, note: '' },
        pinda: { status: 'ok', rows: 2, note: '' },
      },
    };
    expect(projectMediaPricesRefreshStatus(completed)?.state).toBe('done');
    for (const contradictory of [
      { ...completed, started_at: null },
      { ...completed, updated_at: null },
      { ...completed, message: '' },
      {
        ...completed,
        sources: {
          prfabu: completed.sources.prfabu,
          toumeiw: completed.sources.toumeiw,
        },
      },
      {
        ...completed,
        sources: {
          ...completed.sources,
          mtpfw: { status: 'pending', rows: 0, note: '' },
        },
      },
    ]) {
      expect(projectMediaPricesRefreshStatus(contradictory)).toBeNull();
    }
  });

  it('keeps accepted starts distinct from authoritative refresh status', async () => {
    const authoritativeRunning = {
      state: 'running',
      started_at: '2026-07-27 21:30:00',
      updated_at: '2026-07-27 21:30:02',
      message: '拉取 prfabu 第1页…',
      sources: {
        prfabu: { status: 'pending', rows: 0, note: '' },
        toumeiw: { status: 'pending', rows: 0, note: '' },
        mtpfw: { status: 'pending', rows: 0, note: '' },
        meititejia: { status: 'pending', rows: 0, note: '' },
        meijiehezi: { status: 'pending', rows: 0, note: '' },
        pinda: { status: 'pending', rows: 0, note: '' },
      },
    };
    expect(projectMediaPricesRefreshStatus(authoritativeRunning)?.state).toBe('running');
    for (const contradictory of [
      { ...authoritativeRunning, started_at: null },
      { ...authoritativeRunning, updated_at: null },
      { ...authoritativeRunning, message: '' },
      {
        ...authoritativeRunning,
        sources: {
          prfabu: authoritativeRunning.sources.prfabu,
          toumeiw: authoritativeRunning.sources.toumeiw,
        },
      },
      {
        ...authoritativeRunning,
        sources: {
          ...authoritativeRunning.sources,
          mtpfw: { status: 'pending', rows: 1, note: '' },
        },
      },
      { ...authoritativeRunning, started_at: '2026-02-31 21:30:00' },
      {
        ...authoritativeRunning,
        started_at: '2026-07-27 21:31:00',
        updated_at: '2026-07-27 21:30:02',
      },
    ]) {
      expect(projectMediaPricesRefreshStatus(contradictory)).toBeNull();
    }

    for (const contradictoryNever of [
      { state: 'never', message: '旧状态残留' },
      { state: 'never', started_at: '2026-07-27 21:30:00' },
      {
        state: 'never',
        sources: { prfabu: { status: 'pending', rows: 0, note: '' } },
      },
    ]) {
      expect(projectMediaPricesRefreshStatus(contradictoryNever)).toBeNull();
    }
    const authoritativeFailed = {
      ...authoritativeRunning,
      state: 'failed',
      message: '刷新流水线失败',
      sources: {
        ...authoritativeRunning.sources,
        prfabu: { status: 'failed', rows: 0, note: 'source_unavailable' },
      },
    };
    expect(projectMediaPricesRefreshStatus(authoritativeFailed)?.state).toBe('failed');
    expect(
      projectMediaPricesRefreshStatus({
        ...authoritativeFailed,
        sources: { prfabu: authoritativeFailed.sources.prfabu },
      }),
    ).toBeNull();

    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              state: 'running',
              started_at: null,
              updated_at: null,
              message: 'refresh_started',
              sources: {},
            }),
            { status: 202, headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
    expect(
      await requestMediaPricesRefresh(headers, createGeoApiClient('https://geo.example')),
    ).toEqual({ kind: 'started' });

    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify(authoritativeRunning), {
            status: 202,
            headers: { 'content-type': 'application/json' },
          }),
      ),
    );
    expect(
      await requestMediaPricesRefresh(headers, createGeoApiClient('https://geo.example')),
    ).toEqual({ kind: 'unavailable' });
  });
});

describe('fixed-field browser boundaries (Round170)', () => {
  const boundaryHeaders = {
    'X-Tenant-Id': 'tnt_safe',
    'X-Actor-Id': 'reviewer-safe',
    'X-Actor-Role': 'reviewer' as const,
  };
  const sortedKeys = (value: object): string[] => Object.keys(value).sort();
  const hostileExtensions = {
    trace_tokens: ['round170-trace-canary'],
    cookie: 'SESSION=round170-cookie-canary',
    otp_code: '318294',
    session_token: 'round170-session-canary',
    phone: '13800138000',
    profile_path: '/secret/profile/round170-canary',
  };
  const extensionPattern =
    /round170-trace-canary|SESSION=round170|318294|round170-session-canary|13800138000|round170-canary/i;

  it('freezes public safe view keys to the fixed field literals at the type level', () => {
    expect(projectSummaryHasFixedKeys).toBe(true);
    expect(projectPageResponseHasFixedKeys).toBe(true);
    expect(projectPageMetadataHasFixedKeys).toBe(true);
    expect(reportSummarySafeViewHasFixedKeys).toBe(true);
    expect(reportDeliverySafeViewHasFixedKeys).toBe(true);
    expect(investigationSummarySafeViewHasFixedKeys).toBe(true);
    expect(investigationPageHistorySafeViewHasFixedKeys).toBe(true);
    expect(evaluationDatasetSafeViewHasFixedKeys).toBe(true);
    expect(evaluationRunSafeViewHasFixedKeys).toBe(true);
    expect(evaluationRunSafeViewMetricsHaveFixedKeys).toBe(true);
    expect(evaluationRunSafeViewChecksHaveFixedKeys).toBe(true);
    expect(modelAdmissionSafeViewHasFixedKeys).toBe(true);
    expect(analyticsAnswerSafeViewHasFixedKeys).toBe(true);
    expect(analyticsCitationSafeViewHasFixedKeys).toBe(true);
    expect(analyticsAnchorSafeViewHasFixedKeys).toBe(true);
    expect(analyticsEvidenceSafeViewHasFixedKeys).toBe(true);
    expect(analyticsHistorySafeViewHasFixedKeys).toBe(true);
    expect(reportComponentSafeViewHasFixedKeys).toBe(true);
    expect(reportFrozenFactSafeViewHasFixedKeys).toBe(true);
    expect(reportArtifactSafeViewHasFixedKeys).toBe(true);
    expect(reportEvidenceBindingSafeViewHasFixedKeys).toBe(true);
    expect(reportReviewSafeViewHasFixedKeys).toBe(true);
    expect(reportCommentSafeViewHasFixedKeys).toBe(true);
    expect(reportEventSafeViewHasFixedKeys).toBe(true);
    expect(reportVersionSafeViewHasFixedKeys).toBe(true);
    expect(effectRetestSafeViewHasFixedKeys).toBe(true);
    expect(optimizationActionSafeViewHasFixedKeys).toBe(true);
    expect(reportDetailProjectionHasFixedKeys).toBe(true);
    expect(investigationVisualDiffSafeViewHasFixedKeys).toBe(true);
    expect(fixedBoundariesRejectArbitraryRecords).toBe(false);
    expect(fixedBoundariesExcludeArbitraryKeys).toBe(false);
  });

  it('projects identity boundaries with exact keys and rejects non-record input', () => {
    const health = projectHealthBoundary({ status: 'ok', ...hostileExtensions });
    expect(health).toEqual({ status: 'ok' });
    expect(sortedKeys(health!)).toEqual(['status']);
    const session = projectIdentitySessionBoundary({
      tenant_pub_id: 'tnt_safe',
      user_pub_id: 'usr_safe',
      role: 'customer',
      permissions: ['reports:read', 'admin:write'],
      ...hostileExtensions,
    });
    expect(session).toEqual({
      tenant_pub_id: 'tnt_safe',
      user_pub_id: 'usr_safe',
      role: 'customer',
      permissions: [],
    });
    expect(sortedKeys(session!)).toEqual(['permissions', 'role', 'tenant_pub_id', 'user_pub_id']);
    const member = projectIdentityMemberView({
      pub_id: 'mbr_safe',
      user_pub_id: 'usr_safe',
      subject: 'safe@example.com',
      display_name: '安全成员',
      role: 'analyst',
      state: 'active',
      service_account: false,
      ...hostileExtensions,
    });
    expect(sortedKeys(member!)).toEqual([
      'display_name',
      'pub_id',
      'role',
      'service_account',
      'state',
      'subject',
      'user_pub_id',
    ]);
    expect(member!.subject).toBe('s***@example.com');
    const binding = projectOidcBindingView({
      user_pub_id: 'usr_safe',
      active: true,
      created_at: '2026-07-25T08:00:00Z',
      revoked_at: null,
      ...hostileExtensions,
    });
    expect(sortedKeys(binding!)).toEqual(['active', 'created_at', 'revoked_at', 'user_pub_id']);
    for (const projector of [
      projectHealthBoundary,
      projectIdentitySessionBoundary,
      projectIdentityMemberView,
      projectOidcBindingView,
    ]) {
      expect(projector(null)).toBeNull();
      expect(projector('rpt_safe')).toBeNull();
      expect(projector([{ status: 'ok' }])).toBeNull();
      expect(projector(42)).toBeNull();
    }
    expect(JSON.stringify({ health, session, member, binding })).not.toMatch(extensionPattern);
  });

  it('projects identity project pages with exact summary keys and rejects non-record input', () => {
    const page = projectIdentityProjectPageBoundary(
      {
        data: [
          {
            pub_id: 'prj_safe',
            tenant_pub_id: 'tnt_safe',
            name: '安全项目',
            state: 'active',
            created_at: '2026-08-05T17:27:57.411449+00:00',
            updated_at: '2026-08-09T21:00:05.757883+00:00',
            ...hostileExtensions,
          },
        ],
        page: { next_cursor: null, has_more: false, ...hostileExtensions },
      },
      'tnt_safe',
    );
    expect(page).not.toBeNull();
    expect(sortedKeys(page!.data[0]!)).toEqual([
      'created_at',
      'name',
      'pub_id',
      'state',
      'tenant_pub_id',
      'updated_at',
    ]);
    expect(page!.page).toEqual({ next_cursor: null, has_more: false });
    expect(projectIdentityProjectPageBoundary(null, 'tnt_safe')).toBeNull();
    expect(projectIdentityProjectPageBoundary([], 'tnt_safe')).toBeNull();
    expect(projectIdentityProjectPageBoundary({ data: [], page: null }, 'tnt_safe')).toBeNull();
    expect(JSON.stringify(page)).not.toMatch(extensionPattern);
  });

  it('projects customer lifecycle boundaries with exact keys and rejects non-record input', () => {
    const account = projectCustomerAccountView({
      pub_id: 'pac_safe',
      account_mask: '尾号 · 4821',
      platform_label: 'doubao',
      owner_label: '运营账号',
      custody_mode: 'server',
      admission_level: 'read_verified',
      scopes: ['read', 'query'],
      authorization_expires_at: '2026-08-25T08:00:00Z',
      region_label: '天津',
      session_health: 'healthy',
      last_verified_at: '2026-07-25T08:00:00Z',
      intervention_status: 'none',
      revocation_receipt_pub_id: null,
      revoked_at: null,
      ...hostileExtensions,
    });
    expect(account).not.toBeNull();
    expect(sortedKeys(account!)).toEqual([
      'account_mask',
      'admission_level',
      'authorization_expires_at',
      'custody_mode',
      'intervention_status',
      'last_verified_at',
      'owner_label',
      'platform_label',
      'pub_id',
      'region_label',
      'revocation_receipt_pub_id',
      'revoked_at',
      'scopes',
      'session_health',
    ]);
    const pairing = projectCustomerPairingView(
      {
        pub_id: 'int_safe',
        account_pub_id: 'pac_safe',
        account_mask: '尾号 · 4821',
        allowed_domain: 'example.com',
        action: 'read',
        challenge_type: 'otp',
        state: 'pending',
        expires_at: null,
        ...hostileExtensions,
      },
      'pac_safe',
    );
    expect(pairing).not.toBeNull();
    expect(sortedKeys(pairing!)).toEqual([
      'account_mask',
      'account_pub_id',
      'action',
      'allowed_domain',
      'challenge_type',
      'expires_at',
      'pub_id',
      'state',
    ]);
    const member = projectResponsibleMemberView({
      user_pub_id: 'usr_safe',
      label: '值班运营',
      role: 'operator',
      ...hostileExtensions,
    });
    expect(member).toEqual({ user_pub_id: 'usr_safe', label: '值班运营', role: 'operator' });
    const event = projectCustomerEventView({
      pub_id: 'sev_safe',
      event_type: 'account.revoked',
      occurred_at: '2026-07-25T08:00:00Z',
      ...hostileExtensions,
    });
    expect(event).toEqual({
      pub_id: 'sev_safe',
      event_type: 'account.revoked',
      occurred_at: '2026-07-25T08:00:00Z',
    });
    for (const projector of [
      projectCustomerAccountView,
      projectResponsibleMemberView,
      projectCustomerEventView,
    ]) {
      expect(projector(null)).toBeNull();
      expect(projector('pac_safe')).toBeNull();
      expect(projector([])).toBeNull();
    }
    expect(projectCustomerPairingView(null, 'pac_safe')).toBeNull();
    expect(projectCustomerPairingView([], 'pac_safe')).toBeNull();
    expect(JSON.stringify({ account, pairing, member, event })).not.toMatch(extensionPattern);
  });

  it('projects the operations snapshot with exact keys and rejects extension-shaped input', () => {
    const lifecycle = {
      metrics: {
        running_runs: 1,
        project_count: 2,
        pending_interventions: 0,
        healthy_sessions: 1,
        total_sessions: 2,
        delayed_runs: 0,
        p95_delay_seconds: null,
        ...hostileExtensions,
      },
      activity: [],
      accounts: [],
      interventions: [],
      events: [],
      projection: {
        activity: { total: 0, shown: 0, truncated: false },
        accounts: { total: 0, shown: 0, truncated: false },
        interventions: { total: 0, shown: 0, truncated: false },
        events: { total: 0, shown: 0, truncated: false },
      },
      ...hostileExtensions,
    };
    const snapshot = projectOperationsLifecycleSnapshot(lifecycle);
    expect(snapshot).not.toBeNull();
    expect(sortedKeys(snapshot!.metrics)).toEqual([
      'delayedRuns',
      'healthySessions',
      'p95DelayLabel',
      'pendingInterventions',
      'projectCount',
      'runningRuns',
      'totalSessions',
    ]);
    expect(snapshot!.revocationReceipt).toBeNull();
    expect(snapshot!.projectionTruncated).toBe(false);
    expect(projectOperationsLifecycleSnapshot(null)).toBeNull();
    expect(projectOperationsLifecycleSnapshot([])).toBeNull();
    expect(projectOperationsLifecycleSnapshot({ ...lifecycle, metrics: null })).toBeNull();
    expect(projectOperationsLifecycleSnapshot({ ...lifecycle, projection: undefined })).toBeNull();
    expect(JSON.stringify(snapshot)).not.toMatch(extensionPattern);
  });

  it('projects report and investigation lists with exact keys and fails closed on non-record input', () => {
    const reportPage = projectReportPage({
      data: [
        {
          pub_id: 'rpt_safe',
          project_pub_id: 'prj_safe',
          title: '安全报告',
          state: 'published',
          created_at: '2026-07-25T00:00:00Z',
          updated_at: '2026-07-25T01:00:00Z',
          ...hostileExtensions,
        },
      ],
      page: { next_cursor: null, has_more: false },
    });
    expect(reportPage.projection.invalid).toBe(false);
    expect(sortedKeys(reportPage.data[0]!)).toEqual([
      'created_at',
      'project_pub_id',
      'pub_id',
      'state',
      'title',
      'updated_at',
    ]);
    expect(projectReportPage(null)).toEqual({
      data: [],
      page: { next_cursor: null, has_more: false },
      projection: { total: 0, shown: 0, invalid: true },
    });
    expect(projectReportPage([]).projection).toEqual({ total: 0, shown: 0, invalid: true });
    expect(projectReportPage({ data: null, page: {} }).projection.invalid).toBe(true);
    const identity = projectReportDetailIdentity(
      {
        pub_id: 'rpt_safe',
        project_pub_id: 'prj_safe',
        title: '安全报告',
        state: 'published',
        created_at: '2026-07-25T00:00:00Z',
        updated_at: '2026-07-25T01:00:00Z',
        ...hostileExtensions,
      },
      'rpt_safe',
      'prj_safe',
    );
    expect(identity).not.toBeNull();
    expect(sortedKeys(identity!)).toEqual([
      'created_at',
      'project_pub_id',
      'pub_id',
      'state',
      'title',
      'updated_at',
    ]);
    expect(projectReportDetailIdentity(null, 'rpt_safe', 'prj_safe')).toBeNull();
    expect(projectReportDetailIdentity([], 'rpt_safe', 'prj_safe')).toBeNull();
    const investigations = projectInvestigationPage({
      data: [
        {
          pub_id: 'inv_safe',
          title: '安全案件',
          state: 'review',
          access_class: 'customer_private',
          created_at: '2026-07-25T08:00:00Z',
          updated_at: '2026-07-25T09:00:00Z',
          claim_count: 2,
          source_cluster_count: 1,
          probability: '0.75',
          latest_verdict: 'likely',
          ...hostileExtensions,
        },
      ],
      page: { next_cursor: null, has_more: false },
    });
    expect(investigations).not.toBeNull();
    expect(sortedKeys(investigations!.data[0]!)).toEqual([
      'access_class',
      'claim_count',
      'created_at',
      'latest_verdict',
      'probability',
      'pub_id',
      'source_cluster_count',
      'state',
      'title',
      'updated_at',
    ]);
    expect(projectInvestigationPage(null)).toBeNull();
    expect(projectInvestigationPage([])).toBeNull();
    expect(JSON.stringify({ reportPage, identity, investigations })).not.toMatch(extensionPattern);
  });

  it('projects evaluation and admission boundaries with exact keys and rejects non-record input', () => {
    const dataset = projectEvaluationDatasetView({
      pub_id: 'dset_safe',
      version: 'external-v1',
      dataset_sha256: 'a'.repeat(64),
      state: 'approved',
      case_count: 40,
      positive_count: 12,
      labeler_count: 3,
      submitted_at: '2026-07-25T08:00:00Z',
      approved_at: '2026-07-25T09:00:00Z',
      ...hostileExtensions,
    });
    expect(dataset).not.toBeNull();
    expect(sortedKeys(dataset!)).toEqual([
      'approved_at',
      'case_count',
      'dataset_sha256',
      'labeler_count',
      'positive_count',
      'pub_id',
      'state',
      'submitted_at',
      'version',
    ]);
    const run = projectEvaluationRunView({
      pub_id: 'eval_safe',
      dataset_pub_id: 'dset_safe',
      scorer_version: 'anti-geo-v1',
      decision_threshold: '0.5',
      calibration_bins: 10,
      training_cluster_manifest_sha256: 'c'.repeat(64),
      training_cluster_count: 0,
      sample_count: 20,
      admission_policy_version: 'anti-geo-admission-v1',
      admission_checks: {
        precision: true,
        recall: true,
        false_positive_rate: true,
        brier_score: true,
        expected_calibration_error: true,
        explanation_completeness: true,
        trace_tokens: false,
      },
      admission_passed: true,
      model_admission_state: 'admitted',
      metrics: {
        precision: '1',
        recall: '1',
        false_positive_rate: '0',
        brier_score: '0.01',
        expected_calibration_error: '0.1',
        explanation_completeness_rate: '1',
        sample_count: 20,
        positive_count: 10,
        negative_count: 10,
        dataset_version: 'external-v1',
        scorer_version: 'anti-geo-v1',
        evaluation_sha256: 'b'.repeat(64),
        cookie: 'SESSION=round170-metric-canary',
      },
      required_explanation_fields: [
        'evidence_sufficiency',
        'independent_source_count',
        'uncertainty',
        'rule_version',
        'model_version',
        'human_verdict_state',
      ],
      created_at: '2026-07-25T10:00:00Z',
      cases: ['round170-raw-case-canary'],
      ...hostileExtensions,
    });
    expect(run).not.toBeNull();
    expect(sortedKeys(run!)).toEqual([
      'admission_checks',
      'admission_passed',
      'admission_policy_version',
      'calibration_bins',
      'created_at',
      'dataset_pub_id',
      'decision_threshold',
      'metrics',
      'model_admission_state',
      'pub_id',
      'required_explanation_fields',
      'sample_count',
      'scorer_version',
      'training_cluster_count',
      'training_cluster_manifest_sha256',
    ]);
    expect(sortedKeys(run!.metrics)).toEqual([
      'brier_score',
      'dataset_version',
      'evaluation_sha256',
      'expected_calibration_error',
      'explanation_completeness_rate',
      'false_positive_rate',
      'negative_count',
      'positive_count',
      'precision',
      'recall',
      'sample_count',
      'scorer_version',
    ]);
    expect(sortedKeys(run!.admission_checks)).toEqual([
      'brier_score',
      'expected_calibration_error',
      'explanation_completeness',
      'false_positive_rate',
      'precision',
      'recall',
    ]);
    const admission = projectModelAdmissionView({
      pub_id: 'madm_safe',
      evaluation_run_pub_id: 'eval_safe',
      scorer_version: 'anti-geo-v1',
      state: 'admitted',
      rationale: '独立复核通过',
      admitted_at: '2026-07-25T11:00:00Z',
      revoked_at: null,
      ...hostileExtensions,
    });
    expect(admission).not.toBeNull();
    expect(sortedKeys(admission!)).toEqual([
      'admitted_at',
      'evaluation_run_pub_id',
      'pub_id',
      'rationale',
      'revoked_at',
      'scorer_version',
      'state',
    ]);
    for (const projector of [
      projectEvaluationDatasetView,
      projectEvaluationRunView,
      projectModelAdmissionView,
      projectEvaluationDatasetPage,
      projectEvaluationRunPage,
      projectModelAdmissionPage,
    ]) {
      expect(projector(null)).toBeNull();
      expect(projector('eval_safe')).toBeNull();
      expect(projector([])).toBeNull();
    }
    expect(JSON.stringify({ dataset, run, admission })).not.toMatch(
      /round170|SESSION=|318294|13800138000/i,
    );
  });

  it('projects report detail families with exact keys and drops secret-bearing payloads', async () => {
    const version = {
      pub_id: 'rptv_safe',
      version_number: 1,
      window_start: '2026-07-01T00:00:00Z',
      window_end: '2026-07-22T00:00:00Z',
      filters: { mode: 'blind' },
      metric_version: 'geo-v4',
      scorer_version: 'scorer-v2',
      fact_snapshot_hash: 'd'.repeat(64),
      status: 'frozen',
      components: [
        {
          pub_id: 'rptc_safe',
          report_version_pub_id: 'rptv_safe',
          component_type: 'summary',
          ordinal: 1,
          payload: { text: '安全内容' },
          source: 'scorer',
          created_at: '2026-07-25T08:00:00Z',
          ...hostileExtensions,
        },
      ],
      frozen_facts: [
        {
          pub_id: 'rptf_safe',
          report_version_pub_id: 'rptv_safe',
          ordinal: 1,
          payload: { claim: '安全事实' },
          payload_hash: 'e'.repeat(64),
          created_at: '2026-07-25T08:00:00Z',
          ...hostileExtensions,
        },
        {
          pub_id: 'rptf_secret',
          report_version_pub_id: 'rptv_safe',
          ordinal: 2,
          payload: { cookie: 'SESSION=round170-payload-canary' },
          payload_hash: 'f'.repeat(64),
          created_at: '2026-07-25T08:00:00Z',
        },
      ],
      artifacts: [
        {
          pub_id: 'rpta_safe',
          report_version_pub_id: 'rptv_safe',
          format: 'pdf',
          evidence_pub_id: 'evd_artifact',
          mime_type: 'application/pdf',
          byte_size: 128,
          sha256: 'a'.repeat(64),
          created_at: '2026-07-25T08:00:00Z',
          ...hostileExtensions,
        },
      ],
      evidence_bindings: [
        {
          pub_id: 'rptev_safe',
          report_version_pub_id: 'rptv_safe',
          evidence_pub_id: 'evd_binding',
          purpose: 'section_source',
          kind: 'screenshot',
          access_class: 'customer_private',
          mime_type: 'image/png',
          byte_size: 256,
          sha256: 'b'.repeat(64),
          anchor_count: 1,
          capture_time: '2026-07-25T07:30:00Z',
          created_at: '2026-07-25T08:00:00Z',
          ...hostileExtensions,
        },
      ],
      reviews: [
        {
          pub_id: 'rvw_safe',
          report_version_pub_id: 'rptv_safe',
          reviewer_pub_id: 'usr_reviewer',
          decision: 'approved',
          rationale: '独立复核通过',
          created_at: '2026-07-25T08:30:00Z',
          ...hostileExtensions,
        },
      ],
      comments: [
        {
          pub_id: 'cmt_safe',
          report_version_pub_id: 'rptv_safe',
          parent_pub_id: null,
          author_pub_id: 'usr_reviewer',
          body: '安全评论',
          resolved_at: null,
          created_at: '2026-07-25T08:40:00Z',
          ...hostileExtensions,
        },
      ],
      events: [
        {
          pub_id: 'evt_safe',
          report_version_pub_id: 'rptv_safe',
          event_type: 'report.frozen',
          actor_pub_id: 'usr_safe',
          data: { detail: '安全事件' },
          created_at: '2026-07-25T08:00:00Z',
          ...hostileExtensions,
        },
      ],
      ...hostileExtensions,
    };
    const action = {
      pub_id: 'act_safe',
      description: '安全优化动作',
      owner_pub_id: null,
      state: 'open',
      baseline: { mention_rate: '0.4' },
      outcome: null,
      created_at: '2026-07-25T08:00:00Z',
      updated_at: '2026-07-25T09:00:00Z',
      effect_retests: [
        {
          pub_id: 'rts_safe',
          action_pub_id: 'act_safe',
          measured_at: '2026-07-25T10:00:00Z',
          result: { mention_rate: '0.5' },
          recorded_by_pub_id: 'usr_safe',
          created_at: '2026-07-25T10:00:00Z',
          ...hostileExtensions,
        },
      ],
      ...hostileExtensions,
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              pub_id: 'rpt_safe',
              project_pub_id: 'prj_safe',
              title: '安全报告',
              state: 'published',
              created_at: '2026-07-25T00:00:00Z',
              updated_at: '2026-07-25T01:00:00Z',
              versions: [version],
              optimization_actions: [action],
              ...hostileExtensions,
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
    const result = await getReport(
      'rpt_safe',
      boundaryHeaders,
      createGeoApiClient('http://127.0.0.1:45200'),
    );
    expect(result.kind).toBe('ready');
    if (result.kind !== 'ready') return;
    const detail = result.data;
    expect(sortedKeys(detail)).toEqual([
      'created_at',
      'optimization_actions',
      'project_pub_id',
      'projection',
      'pub_id',
      'state',
      'title',
      'updated_at',
      'versions',
    ]);
    const projectedVersion = detail.versions[0]!;
    expect(sortedKeys(projectedVersion)).toEqual([
      'artifacts',
      'comments',
      'components',
      'events',
      'evidence_bindings',
      'fact_snapshot_hash',
      'filters',
      'frozen_facts',
      'metric_version',
      'pub_id',
      'reviews',
      'scorer_version',
      'status',
      'version_number',
      'window_end',
      'window_start',
    ]);
    expect(sortedKeys(projectedVersion.components[0]!)).toEqual([
      'component_type',
      'created_at',
      'ordinal',
      'payload',
      'pub_id',
      'report_version_pub_id',
      'source',
    ]);
    expect(projectedVersion.frozen_facts).toHaveLength(1);
    expect(sortedKeys(projectedVersion.frozen_facts[0]!)).toEqual([
      'created_at',
      'ordinal',
      'payload',
      'payload_hash',
      'pub_id',
      'report_version_pub_id',
    ]);
    expect(detail.projection.version_collections['rptv_safe']!.frozen_facts.invalid).toBe(true);
    expect(sortedKeys(projectedVersion.artifacts[0]!)).toEqual([
      'byte_size',
      'created_at',
      'evidence_pub_id',
      'format',
      'mime_type',
      'pub_id',
      'report_version_pub_id',
      'sha256',
    ]);
    expect(sortedKeys(projectedVersion.evidence_bindings[0]!)).toEqual([
      'access_class',
      'anchor_count',
      'byte_size',
      'capture_time',
      'created_at',
      'evidence_pub_id',
      'kind',
      'mime_type',
      'pub_id',
      'purpose',
      'report_version_pub_id',
      'sha256',
    ]);
    expect(sortedKeys(projectedVersion.reviews[0]!)).toEqual([
      'created_at',
      'decision',
      'pub_id',
      'rationale',
      'report_version_pub_id',
      'reviewer_pub_id',
    ]);
    expect(sortedKeys(projectedVersion.comments[0]!)).toEqual([
      'author_pub_id',
      'body',
      'created_at',
      'parent_pub_id',
      'pub_id',
      'report_version_pub_id',
      'resolved_at',
    ]);
    expect(sortedKeys(projectedVersion.events[0]!)).toEqual([
      'actor_pub_id',
      'created_at',
      'data',
      'event_type',
      'pub_id',
      'report_version_pub_id',
    ]);
    const projectedAction = detail.optimization_actions[0]!;
    expect(sortedKeys(projectedAction)).toEqual([
      'baseline',
      'created_at',
      'description',
      'effect_retests',
      'outcome',
      'owner_pub_id',
      'pub_id',
      'state',
      'updated_at',
    ]);
    expect(sortedKeys(projectedAction.effect_retests[0]!)).toEqual([
      'action_pub_id',
      'created_at',
      'measured_at',
      'pub_id',
      'recorded_by_pub_id',
      'result',
    ]);
    expect(JSON.stringify(detail)).not.toMatch(extensionPattern);
  });

  it('projects analytics answers and relations with exact keys', async () => {
    const answer = {
      pub_id: 'ans_safe',
      project_pub_id: 'prj_safe',
      run_pub_id: 'run_safe',
      config_version_pub_id: 'cfv_safe',
      query_pub_id: 'qry_safe',
      query_text: '安全问题',
      response_text: '安全回答',
      model: 'doubao',
      region: 'cn',
      mode: 'blind',
      eligible: true,
      degraded: false,
      capture_time: '2026-07-25T08:00:00Z',
      mentioned: true,
      rank: 1,
      sentiment: 'neutral',
      recommendation_state: 'mentioned',
      citation_count: 1,
      ...hostileExtensions,
    };
    const citation = {
      pub_id: 'cit_safe',
      ordinal: 1,
      canonical_url: 'https://source.example/article',
      host: 'source.example',
      title: '来源',
      cited_text: 'round170-cited-prose-canary',
      own_source: false,
      content_hash: 'c'.repeat(64),
      ...hostileExtensions,
    };
    const anchor = {
      pub_id: 'anch_safe',
      text_start: 0,
      text_end: 10,
      bbox: {
        x: 10,
        y: 20,
        width: 30,
        height: 40,
        confidence: 0.9,
        note: 'round170-bbox-canary',
      },
      page_number: 1,
      quote_hash: 'd'.repeat(64),
      ...hostileExtensions,
    };
    const evidence = {
      pub_id: 'evd_safe',
      relation_type: 'citation',
      kind: 'screenshot',
      access_class: 'customer_private',
      sha256: 'a'.repeat(64),
      mime_type: 'image/png',
      byte_size: 128,
      source_url: 'https://source.example/article',
      capture_time: '2026-07-25T08:00:00Z',
      anchors: [anchor],
      ...hostileExtensions,
    };
    const history = {
      pub_id: 'diff_safe',
      before_evidence_pub_id: 'evd_before',
      after_evidence_pub_id: 'evd_after',
      similarity: 0.5,
      visual_diff_available: true,
      created_at: '2026-07-25T09:00:00Z',
      ...hostileExtensions,
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL((input as Request).url);
        const body = url.pathname.endsWith('/relations')
          ? {
              answer_pub_id: 'ans_safe',
              citations: [citation],
              evidence: [evidence],
              history: [history],
              ...hostileExtensions,
            }
          : {
              data: [answer],
              page: { next_cursor: null, has_more: false },
            };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const answers = await listAnalyticsAnswers('prj_safe', {}, boundaryHeaders, client);
    expect(answers.kind).toBe('ready');
    if (answers.kind !== 'ready') return;
    expect(sortedKeys(answers.data.data[0]!)).toEqual([
      'capture_time',
      'citation_count',
      'config_version_pub_id',
      'degraded',
      'eligible',
      'mentioned',
      'mode',
      'model',
      'project_pub_id',
      'pub_id',
      'query_pub_id',
      'query_text',
      'rank',
      'recommendation_state',
      'region',
      'response_text',
      'run_pub_id',
      'sentiment',
    ]);
    const relations = await getAnalyticsAnswerRelations('ans_safe', boundaryHeaders, client);
    expect(relations.kind).toBe('ready');
    if (relations.kind !== 'ready') return;
    const projectedCitation = relations.data.citations[0]!;
    expect(sortedKeys(projectedCitation)).toEqual([
      'canonical_url',
      'cited_text',
      'content_hash',
      'host',
      'ordinal',
      'own_source',
      'pub_id',
      'title',
    ]);
    expect(projectedCitation.cited_text).toBe('round170-cited-prose-canary');
    const projectedEvidence = relations.data.evidence[0]!;
    expect(sortedKeys(projectedEvidence)).toEqual([
      'access_class',
      'anchors',
      'byte_size',
      'capture_time',
      'kind',
      'mime_type',
      'pub_id',
      'relation_type',
      'sha256',
      'source_url',
    ]);
    const projectedAnchor = projectedEvidence.anchors[0]!;
    expect(sortedKeys(projectedAnchor)).toEqual([
      'bbox',
      'page_number',
      'pub_id',
      'quote_hash',
      'text_end',
      'text_start',
    ]);
    expect(projectedAnchor.bbox).toEqual({
      x: 10,
      y: 20,
      width: 30,
      height: 40,
      confidence: 0.9,
    });
    expect(sortedKeys(relations.data.history[0]!)).toEqual([
      'after_evidence_pub_id',
      'before_evidence_pub_id',
      'created_at',
      'pub_id',
      'similarity',
      'visual_diff_available',
    ]);
    expect(JSON.stringify({ answers, relations })).not.toMatch(extensionPattern);
  });

  it('projects deliveries, evidence assets, investigation history and visual diffs with exact keys', async () => {
    const delivery = {
      pub_id: 'dlv_safe',
      report_pub_id: 'rpt_safe',
      recipient_pub_id: 'usr_safe',
      delivered_at: '2026-07-25T08:00:00Z',
      confirmed_at: null,
      confirmation_comment: 'round170-delivery-comment-canary',
      ...hostileExtensions,
    };
    const asset = {
      pub_id: 'evd_safe',
      kind: 'answer_screenshot',
      mime_type: 'image/png',
      capture_time: '2026-07-25T08:00:00Z',
      sha256: 'a'.repeat(64),
      ...hostileExtensions,
    };
    const historyRow = {
      content_pub_id: 'cnt_safe',
      version_pub_id: 'cntv_safe',
      canonical_url: 'https://source.example/article',
      title: '页面',
      version_number: 1,
      body_hash: 'b'.repeat(64),
      evidence_pub_id: null,
      captured_at: '2026-07-25T08:00:00Z',
      published_at: null,
      snapshot_pub_id: null,
      snapshot_number: null,
      normalized_text_hash: null,
      perceptual_hash: null,
      ...hostileExtensions,
    };
    const visualDiff = {
      pub_id: 'diff_safe',
      content_pub_id: 'cnt_safe',
      before_version_pub_id: 'cntv_before',
      after_version_pub_id: 'cntv_after',
      before_evidence_pub_id: 'evd_before',
      after_evidence_pub_id: 'evd_after',
      text_diff: {
        before_hash: 'c'.repeat(64),
        after_hash: 'd'.repeat(64),
        extra: 'round170-text-diff-canary',
      },
      similarity: '0.5',
      visual_diff_available: true,
      created_at: '2026-07-25T09:00:00Z',
      ...hostileExtensions,
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL((input as Request).url);
        const body = url.pathname.endsWith('/deliveries')
          ? [delivery]
          : url.pathname.endsWith('/page-history')
            ? [historyRow]
            : url.pathname.endsWith('/visual-diffs')
              ? [visualDiff]
              : { data: [asset], page: { next_cursor: null, has_more: false } };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
    const client = createGeoApiClient('http://127.0.0.1:45200');
    const deliveries = await listReportDeliveries('rpt_safe', boundaryHeaders, client);
    expect(deliveries.kind).toBe('ready');
    if (deliveries.kind !== 'ready') return;
    expect(sortedKeys(deliveries.data.data[0]!)).toEqual([
      'confirmation_comment',
      'confirmed_at',
      'delivered_at',
      'pub_id',
      'recipient_pub_id',
      'report_pub_id',
    ]);
    expect(deliveries.data.data[0]!.confirmation_comment).toBeNull();
    const assets = await listEvidenceAssets(boundaryHeaders, {}, client);
    expect(assets.kind).toBe('ready');
    if (assets.kind !== 'ready') return;
    expect(sortedKeys(assets.data.data[0]!)).toEqual([
      'capture_time',
      'kind',
      'mime_type',
      'pub_id',
      'sha256',
    ]);
    const history = await getInvestigationPageHistory('inv_safe', boundaryHeaders, client);
    expect(history.kind).toBe('ready');
    if (history.kind !== 'ready') return;
    expect(sortedKeys(history.data.data[0]!)).toEqual([
      'body_hash',
      'canonical_url',
      'captured_at',
      'content_pub_id',
      'evidence_pub_id',
      'normalized_text_hash',
      'perceptual_hash',
      'published_at',
      'snapshot_number',
      'snapshot_pub_id',
      'title',
      'version_number',
      'version_pub_id',
    ]);
    const diffs = await getInvestigationVisualDiffs('inv_safe', boundaryHeaders, client);
    expect(diffs.kind).toBe('ready');
    if (diffs.kind !== 'ready') return;
    expect(sortedKeys(diffs.data.data[0]!)).toEqual([
      'after_evidence_pub_id',
      'after_version_pub_id',
      'before_evidence_pub_id',
      'before_version_pub_id',
      'content_pub_id',
      'created_at',
      'pub_id',
      'similarity',
      'text_diff',
      'visual_diff_available',
    ]);
    expect(sortedKeys(diffs.data.data[0]!.text_diff!)).toEqual(['after_hash', 'before_hash']);
    expect(JSON.stringify({ deliveries, assets, history, diffs })).not.toMatch(extensionPattern);
  });

  it('exposes and sends SOP project cursors so later pages remain reachable', async () => {
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL((input as Request).url);
      expect(url.searchParams.get('cursor')).toBe('spr_previous');
      expect(url.searchParams.get('limit')).toBe('100');
      return new Response(
        JSON.stringify({
          data: [
            {
              pub_id: 'spr_next',
              tenant_pub_id: boundaryHeaders['X-Tenant-Id'],
              name: '下一页项目',
              brand_standard_name: 'Acme',
              brand_profile: {},
              target_platforms: [],
              success_definition: [],
              status: 'active',
              created_by_pub_id: 'usr_operator',
              created_at: '2026-07-29T08:00:00Z',
              updated_at: '2026-07-29T09:00:00Z',
            },
          ],
          page: { next_cursor: 'spr_next', has_more: true },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', request);

    const result = await listSopProjects(
      boundaryHeaders,
      'spr_previous',
      createGeoApiClient('http://127.0.0.1:45200'),
    );

    expect(result).toEqual({
      kind: 'ready',
      data: {
        data: [
          {
            pubId: 'spr_next',
            name: '下一页项目',
            brandStandardName: 'Acme',
            status: 'active',
            updatedAt: '2026-07-29T09:00:00Z',
          },
        ],
        nextCursor: 'spr_next',
        hasMore: true,
      },
    });
    expect(request).toHaveBeenCalledTimes(1);
  });
});

describe('post analysis browser boundary', () => {
  const postAnalysisHeaders = {
    'X-Tenant-Id': 'tnt_pa_safe',
    'X-Actor-Id': 'operator@example.test',
    'X-Actor-Role': 'operator' as const,
  };
  const postAnalysisTaskRow = {
    pub_id: `pat_${'a'.repeat(26)}`,
    target_brand: '示例品牌',
    target_brand_aliases: ['示例别名'],
    status: 'running',
    url_count: 2,
    options: { verify_facts: true, annotate: true },
    workflow_id: 'wf-pa-safe',
    error: null,
    created_by: 'usr_operator',
    created_at: '2026-08-06T02:00:00Z',
    updated_at: '2026-08-06T02:05:00Z',
  };
  const postAnalysisTaskSummary = {
    pubId: `pat_${'a'.repeat(26)}`,
    targetBrand: '示例品牌',
    targetBrandAliases: ['示例别名'],
    status: 'running',
    urlCount: 2,
    error: null,
    createdAt: '2026-08-06T02:00:00Z',
    updatedAt: '2026-08-06T02:05:00Z',
  };
  const postAnalysisItemRow = {
    pub_id: `pai_${'b'.repeat(26)}`,
    ordinal: 1,
    url: 'https://example.com/post-1',
    host: 'example.com',
    status: 'completed',
    annotation_status: 'completed',
    category: 'review_ranking',
    category_label: '评测榜单',
    is_geo_post: true,
    is_target_brand_geo: false,
    disparagement_count: 1,
    misinformation_count: 0,
    error: null,
    created_at: '2026-08-06T02:00:10Z',
    updated_at: '2026-08-06T02:04:10Z',
  };
  const postAnalysisIntegrity = {
    sha256: '0c5455b7a259b7c3d53cfff0e3fcf4f85e2d6e8d93441915433d3a23066e06fa',
    byteSize: 17,
    mimeType: 'image/png',
  };
  const postAnalysisItemDetailRow = {
    pub_id: `pai_${'b'.repeat(26)}`,
    ordinal: 1,
    url: 'https://example.com/post-1',
    url_hash: 'd'.repeat(64),
    host: 'example.com',
    status: 'completed',
    annotation_status: 'completed',
    final_url: 'https://example.com/post-1-final',
    http_status: 200,
    extractor: 'innertext-v1',
    text_sha256: 'e'.repeat(64),
    analysis: {
      summary: '这是一篇评测文章。',
      is_geo_post: true,
      geo_confidence: 0.82,
      geo_signals: [{ signal: '榜单模板化', quote: '十大品牌排行榜' }],
      category: 'review_ranking',
      category_label: '评测榜单',
      category_rationale: '含榜单结构。',
      brand_mentions: [
        {
          brand: '示例品牌',
          is_target_brand: true,
          sentiment: 'positive',
          quote: '示例品牌表现优秀',
        },
      ],
      is_target_brand_geo: true,
      disparagement: [
        {
          direction: 'target_disparaged',
          subject_brand: '竞品A',
          object_brand: '示例品牌',
          quote: '示例品牌不如竞品A',
          severity: 'medium',
          confidence: 0.7,
        },
      ],
      claims: [
        {
          claim: '示例品牌市占率第一',
          quote: '市占率第一',
          about_target_brand: true,
          verification: {
            verdict: 'inaccurate',
            correction: '公开数据为第三。',
            confidence: 0.9,
            sources: [{ title: '统计年报', url: 'https://example.com/report' }],
          },
        },
      ],
      model: 'gpt-5.6-luna',
      prompt_version: 'pa-v1',
    },
    analysis_validation: {
      dropped: { geo_signals: 0, brand_mentions: 0, disparagement: 0, claims: 1 },
      details: [],
      verification_errors: 0,
      claims_verified: 1,
    },
    annotations: [
      {
        type: 'target_brand',
        quote: '示例品牌表现优秀',
        note: '目标品牌提及：示例品牌（positive）',
        rects: [{ x: 1, y: 2, width: 3, height: 4 }],
        matched: true,
      },
    ],
    error: null,
    has_screenshot: true,
    has_annotated: true,
    screenshot_asset: {
      sha256: postAnalysisIntegrity.sha256,
      byte_size: 17,
      mime_type: 'image/png',
    },
    annotated_asset: {
      sha256: postAnalysisIntegrity.sha256,
      byte_size: 17,
      mime_type: 'image/png',
    },
    created_at: '2026-08-06T02:00:10Z',
    updated_at: '2026-08-06T02:04:10Z',
  };
  const jsonResponse = (payload: unknown, status = 200) =>
    new Response(JSON.stringify(payload), {
      status,
      headers: { 'content-type': 'application/json' },
    });

  it('projects task pages and fails closed on hostile rows or cursor drift', async () => {
    const request = vi.fn(async (_input: RequestInfo | URL) =>
      jsonResponse({
        data: [postAnalysisTaskRow],
        page: { next_cursor: `pat_${'a'.repeat(26)}`, has_more: true },
      }),
    );
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');

    const ready = await listPostAnalysisTasks(postAnalysisHeaders, `pat_${'0'.repeat(26)}`, client);
    expect(ready).toEqual({
      kind: 'ready',
      data: { data: [postAnalysisTaskSummary], nextCursor: `pat_${'a'.repeat(26)}`, hasMore: true },
    });
    const outbound = request.mock.calls[0]?.[0] as Request;
    expect(outbound.url).toContain(`cursor=pat_${'0'.repeat(26)}`);

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          data: [{ ...postAnalysisTaskRow, status: 'bogus' }],
          page: { next_cursor: null, has_more: false },
        }),
      ),
    );
    await expect(listPostAnalysisTasks(postAnalysisHeaders, null, client)).resolves.toEqual({
      kind: 'unavailable',
    });

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          data: [postAnalysisTaskRow],
          page: { next_cursor: `pat_${'c'.repeat(26)}`, has_more: true },
        }),
      ),
    );
    await expect(listPostAnalysisTasks(postAnalysisHeaders, null, client)).resolves.toEqual({
      kind: 'unavailable',
    });
  });

  it('creates tasks with an idempotency key and projects the receipt', async () => {
    const request = vi.fn(async (_input: RequestInfo | URL) =>
      jsonResponse(postAnalysisTaskRow, 201),
    );
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');

    const ready = await createPostAnalysisTask(
      postAnalysisHeaders,
      {
        targetBrand: '示例品牌',
        targetBrandAliases: ['示例别名'],
        urls: ['https://example.com/post-1'],
        verifyFacts: true,
        annotate: false,
        openInvestigation: false,
      },
      'post-analysis-4f2d2ad3-0000-4000-8000-000000000000',
      client,
    );
    expect(ready).toEqual({ kind: 'ready', data: { pubId: `pat_${'a'.repeat(26)}` } });
    const outbound = request.mock.calls[0]?.[0] as Request;
    expect(outbound.method).toBe('POST');
    expect(outbound.headers.get('Idempotency-Key')).toBe(
      'post-analysis-4f2d2ad3-0000-4000-8000-000000000000',
    );
    await expect(outbound.clone().json()).resolves.toEqual({
      target_brand: '示例品牌',
      target_brand_aliases: ['示例别名'],
      urls: ['https://example.com/post-1'],
      options: { verify_facts: true, annotate: false, open_investigation: false },
    });

    await createPostAnalysisTask(
      postAnalysisHeaders,
      {
        targetBrand: '示例品牌',
        targetBrandAliases: [],
        urls: ['https://example.com/post-2'],
        verifyFacts: true,
        annotate: true,
      },
      'post-analysis-4f2d2ad3-0000-4000-8000-000000000002',
      client,
    );
    const defaulted = request.mock.calls[1]?.[0] as Request;
    await expect(defaulted.clone().json()).resolves.toMatchObject({
      options: { verify_facts: true, annotate: true, open_investigation: true },
    });

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: { code: 'forbidden' } }, 403)),
    );
    await expect(
      createPostAnalysisTask(
        postAnalysisHeaders,
        {
          targetBrand: '示例品牌',
          targetBrandAliases: [],
          urls: ['https://example.com/post-1'],
          verifyFacts: true,
          annotate: true,
        },
        'post-analysis-4f2d2ad3-0000-4000-8000-000000000001',
        client,
      ),
    ).resolves.toEqual({ kind: 'forbidden' });
  });

  it('projects task detail status counts through the item-status whitelist', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          ...postAnalysisTaskRow,
          status_counts: { completed: 2, bogus_status: 3, fetch_failed: 1 },
          investigation_pub_id: `inv_${'f'.repeat(26)}`,
        }),
      ),
    );
    const client = createGeoApiClient('http://127.0.0.1:45200');

    const ready = await getPostAnalysisTask(postAnalysisHeaders, `pat_${'a'.repeat(26)}`, client);
    expect(ready).toEqual({
      kind: 'ready',
      data: {
        task: postAnalysisTaskSummary,
        statusCounts: [
          { status: 'completed', count: 2 },
          { status: 'fetch_failed', count: 1 },
        ],
        investigationPubId: `inv_${'f'.repeat(26)}`,
      },
    });

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          ...postAnalysisTaskRow,
          status_counts: {},
          investigation_pub_id: 'bogus-investigation',
        }),
      ),
    );
    const garbage = await getPostAnalysisTask(postAnalysisHeaders, `pat_${'a'.repeat(26)}`, client);
    expect(garbage).toEqual({
      kind: 'ready',
      data: { task: postAnalysisTaskSummary, statusCounts: [], investigationPubId: null },
    });

    await expect(getPostAnalysisTask(postAnalysisHeaders, 'not-a-task', client)).resolves.toEqual({
      kind: 'unavailable',
    });
  });

  it('projects item list rows with badge fields and item details with assets', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          data: [postAnalysisItemRow],
          page: { next_cursor: null, has_more: false },
        }),
      ),
    );
    const client = createGeoApiClient('http://127.0.0.1:45200');

    const page = await listPostAnalysisItems(
      postAnalysisHeaders,
      `pat_${'a'.repeat(26)}`,
      null,
      client,
    );
    expect(page).toEqual({
      kind: 'ready',
      data: {
        data: [
          {
            pubId: `pai_${'b'.repeat(26)}`,
            ordinal: 1,
            url: 'https://example.com/post-1',
            host: 'example.com',
            status: 'completed',
            annotationStatus: 'completed',
            category: 'review_ranking',
            categoryLabel: '评测榜单',
            isGeoPost: true,
            isTargetBrandGeo: false,
            disparagementCount: 1,
            misinformationCount: 0,
            error: null,
            createdAt: '2026-08-06T02:00:10Z',
            updatedAt: '2026-08-06T02:04:10Z',
          },
        ],
        nextCursor: null,
        hasMore: false,
      },
    });

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(postAnalysisItemDetailRow)),
    );
    const detail = await getPostAnalysisItem(postAnalysisHeaders, `pai_${'b'.repeat(26)}`, client);
    expect(detail).toEqual({
      kind: 'ready',
      data: {
        pubId: `pai_${'b'.repeat(26)}`,
        ordinal: 1,
        url: 'https://example.com/post-1',
        host: 'example.com',
        status: 'completed',
        annotationStatus: 'completed',
        finalUrl: 'https://example.com/post-1-final',
        httpStatus: 200,
        extractor: 'innertext-v1',
        textSha256: 'e'.repeat(64),
        error: null,
        analysis: {
          summary: '这是一篇评测文章。',
          isGeoPost: true,
          geoConfidence: 0.82,
          geoSignals: [{ signal: '榜单模板化', quote: '十大品牌排行榜' }],
          category: 'review_ranking',
          categoryLabel: '评测榜单',
          categoryRationale: '含榜单结构。',
          brandMentions: [
            {
              brand: '示例品牌',
              isTargetBrand: true,
              sentiment: 'positive',
              quote: '示例品牌表现优秀',
            },
          ],
          isTargetBrandGeo: true,
          disparagement: [
            {
              direction: 'target_disparaged',
              subjectBrand: '竞品A',
              objectBrand: '示例品牌',
              quote: '示例品牌不如竞品A',
              severity: 'medium',
              confidence: 0.7,
            },
          ],
          claims: [
            {
              claim: '示例品牌市占率第一',
              quote: '市占率第一',
              aboutTargetBrand: true,
              verification: {
                verdict: 'inaccurate',
                correction: '公开数据为第三。',
                confidence: 0.9,
                sources: [{ title: '统计年报', url: 'https://example.com/report' }],
              },
            },
          ],
        },
        analysisValidation: { droppedTotal: 1, claimsVerified: 1, verificationErrors: 0 },
        annotations: [
          {
            type: 'target_brand',
            quote: '示例品牌表现优秀',
            note: '目标品牌提及：示例品牌（positive）',
            matched: true,
          },
        ],
        screenshotAsset: postAnalysisIntegrity,
        annotatedAsset: postAnalysisIntegrity,
        createdAt: '2026-08-06T02:00:10Z',
        updatedAt: '2026-08-06T02:04:10Z',
      },
    });
  });

  it('binds post analysis image bytes to MIME, size and SHA-256', async () => {
    const payload = 'PNG post analysis';
    const request = vi.fn(
      async (_input: RequestInfo | URL) =>
        new Response(payload, { status: 200, headers: { 'content-type': 'image/png' } }),
    );
    vi.stubGlobal('fetch', request);
    const client = createGeoApiClient('http://127.0.0.1:45200');

    const ready = await getPostAnalysisItemAsset(
      postAnalysisHeaders,
      `pai_${'b'.repeat(26)}`,
      'annotated',
      postAnalysisIntegrity,
      client,
    );
    expect(ready).toMatchObject({ kind: 'ready', data: postAnalysisIntegrity });
    if (ready.kind === 'ready') expect(await ready.data.blob.text()).toBe(payload);
    const outbound = request.mock.calls[0]?.[0] as Request;
    expect(outbound.url).toMatch(/\/api\/v2\/post-analysis\/items\/pai_.*\/assets\/annotated$/);
    expect(outbound.headers.get('Accept')).toBe('image/png');

    await expect(
      getPostAnalysisItemAsset(
        postAnalysisHeaders,
        `pai_${'b'.repeat(26)}`,
        'screenshot',
        { ...postAnalysisIntegrity, sha256: '0'.repeat(64) },
        client,
      ),
    ).resolves.toEqual({ kind: 'unavailable' });
    await expect(
      getPostAnalysisItemAsset(
        postAnalysisHeaders,
        'not-an-item',
        'screenshot',
        postAnalysisIntegrity,
        client,
      ),
    ).resolves.toEqual({ kind: 'unavailable' });
    expect(request).toHaveBeenCalledTimes(2);

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: { code: 'not_found' } }, 404)),
    );
    await expect(
      getPostAnalysisItemAsset(
        postAnalysisHeaders,
        `pai_${'b'.repeat(26)}`,
        'screenshot',
        postAnalysisIntegrity,
        client,
      ),
    ).resolves.toEqual({ kind: 'forbidden' });
  });
});

describe('identity logout boundary', () => {
  it('posts the native logout endpoint to the same-origin API base by default', async () => {
    const request = vi.fn(async () => new Response(null, { status: 204 }));

    await expect(logoutIdentitySession(request)).resolves.toBe(true);

    expect(request).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledWith('/api/v2/identity/logout', { method: 'POST' });
  });

  it('reports a non-acknowledging revocation response without throwing', async () => {
    const request = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: { code: 'session_expired' } }), {
          status: 401,
          headers: { 'content-type': 'application/json' },
        }),
    );

    await expect(logoutIdentitySession(request)).resolves.toBe(false);
  });

  it('never throws when the revocation transport fails', async () => {
    const request = vi.fn(async () => {
      throw new TypeError('network down');
    });

    await expect(logoutIdentitySession(request)).resolves.toBe(false);
  });
});

describe('production identity header boundary', () => {
  const identityHeaders: IdentitySessionHeaders = {
    'X-Tenant-Id': 'tnt_safe',
    'X-Actor-Id': 'usr_safe',
    'X-Actor-Role': 'customer',
  };
  const recordIdentityHeaderPresence = () => {
    const sent: boolean[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input as Request;
        sent.push(
          request.headers.has('X-Tenant-Id') ||
            request.headers.has('X-Actor-Id') ||
            request.headers.has('X-Actor-Role'),
        );
        const body = request.url.endsWith('/identity/session')
          ? {
              tenant_pub_id: 'tnt_safe',
              user_pub_id: 'usr_safe',
              role: 'customer',
              permissions: [],
            }
          : { data: [], page: { next_cursor: null, has_more: false } };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
    return sent;
  };

  it('keeps fixture identity headers on the wire in development and e2e builds', async () => {
    const sent = recordIdentityHeaderPresence();

    const result = await getIdentitySession(
      identityHeaders,
      createGeoApiClient('https://geo.example'),
    );

    expect(result.kind).toBe('ready');
    expect(sent.length).toBeGreaterThan(0);
    expect(sent.every((present) => present)).toBe(true);
  });

  it('strips browser identity headers when the build env is a production bundle', () => {
    expect(allowsFixtureIdentityHeaders({ DEV: false })).toBe(false);
    expect(allowsFixtureIdentityHeaders({ DEV: false, VITE_ALLOW_CONTRACT_FIXTURES: '' })).toBe(
      false,
    );
    expect(allowsFixtureIdentityHeaders(undefined)).toBe(false);
    expect(allowsFixtureIdentityHeaders({ DEV: true })).toBe(true);
    expect(allowsFixtureIdentityHeaders({ DEV: false, VITE_ALLOW_CONTRACT_FIXTURES: 'true' })).toBe(
      true,
    );
  });
});
