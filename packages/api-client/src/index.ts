import createClient from 'openapi-fetch';
import type { paths } from './schema.generated';

type HealthResponse =
  paths['/api/v2/health']['get']['responses']['200']['content']['application/json'];
export type HealthProjection = Pick<HealthResponse, 'status'>;
type IdentitySessionResponse =
  paths['/api/v2/identity/session']['get']['responses']['200']['content']['application/json'];
export type IdentitySessionProjection = Pick<
  IdentitySessionResponse,
  'tenant_pub_id' | 'user_pub_id' | 'role' | 'permissions'
>;
type ProjectPageContractResponse =
  paths['/api/v2/projects']['get']['responses']['200']['content']['application/json'];
type ProjectSummaryContractView = ProjectPageContractResponse['data'][number];
export type ProjectSummary = Pick<
  ProjectSummaryContractView,
  'pub_id' | 'tenant_pub_id' | 'name' | 'state' | 'created_at' | 'updated_at'
>;
/**
 * Fixed-field compatibility view for the separately owned S01 execution surface.
 * The raw generated project page remains private to this package.
 */
export type ProjectPageResponse = {
  data: ProjectSummary[];
  page: {
    next_cursor: string | null;
    has_more: boolean;
  };
};
export type IdentityProjectPageProjection = ProjectedContractPage<ProjectSummary>;
type GeneratedIdentitySessionHeaders = NonNullable<
  paths['/api/v2/identity/session']['get']['parameters']['header']
>;
export type IdentitySessionHeaders = Pick<
  GeneratedIdentitySessionHeaders,
  'X-Tenant-Id' | 'X-Actor-Id' | 'X-Actor-Role'
>;
type IdentityMemberContractView =
  paths['/api/v2/identity/members']['get']['responses']['200']['content']['application/json'][number];
export type IdentityMemberView = Pick<
  IdentityMemberContractView,
  'pub_id' | 'user_pub_id' | 'subject' | 'display_name' | 'role' | 'state' | 'service_account'
>;
export type IdentityMemberProjection = ProjectedCollection<IdentityMemberView>;
export type IdentityMemberCreate =
  paths['/api/v2/identity/members']['post']['requestBody']['content']['application/json'];
type OidcBindingContractView =
  paths['/api/v2/identity/oidc-bindings']['get']['responses']['200']['content']['application/json'][number];
export type OidcBindingView = Pick<
  OidcBindingContractView,
  'user_pub_id' | 'active' | 'created_at' | 'revoked_at'
>;
export type OidcBindingProjection = ProjectedCollection<OidcBindingView>;
export type OidcBindingCreate =
  paths['/api/v2/identity/members/{user_pub_id}/oidc-binding']['put']['requestBody']['content']['application/json'];
export type ProjectResourceKind =
  paths['/api/v2/projects/{project_pub_id}/resources/{kind}']['get']['parameters']['path']['kind'];
type ProjectResourceContractView =
  paths['/api/v2/projects/{project_pub_id}/resources/{kind}']['get']['responses']['200']['content']['application/json'][number];
export type ProjectResourceSafePayload = {
  target?: number;
  question?: string;
  reason?: string;
  goal_metric?: string;
  priority?: string;
  target_percent?: number;
};
export type ProjectResourceSafeData = {
  name?: string;
  website?: string | null;
  parent_pub_id?: string;
  value?: string;
  kind?: string;
  uri?: string;
  sha256?: string | null;
  text?: string;
  priority?: number;
  metric?: string;
  payload?: ProjectResourceSafePayload;
  state?: string;
};
export type ProjectResourceView = Pick<
  ProjectResourceContractView,
  'pub_id' | 'project_pub_id' | 'resource_kind' | 'version'
> & {
  data: ProjectResourceSafeData;
};
export type ProjectResourceWrite =
  paths['/api/v2/projects/{project_pub_id}/resources/{kind}']['post']['requestBody']['content']['application/json'];
export type ClientProfileWrite =
  paths['/api/v2/projects/{project_pub_id}/client-profile/versions']['post']['requestBody']['content']['application/json'];
type ClientProfileContractView =
  paths['/api/v2/projects/{project_pub_id}/client-profile/versions']['post']['responses']['201']['content']['application/json'];
export type ClientProfileView = Pick<
  ClientProfileContractView,
  | 'pub_id'
  | 'project_pub_id'
  | 'revision'
  | 'company_name'
  | 'contact_role'
  | 'audience'
  | 'public_statement'
  | 'created_at'
>;
export type ClientProfilePage = {
  data: ClientProfileView[];
  next_cursor: string | null;
};
export type AssetConfirmationWrite =
  paths['/api/v2/projects/{project_pub_id}/asset-confirmations']['post']['requestBody']['content']['application/json'];
type AssetConfirmationContractView =
  paths['/api/v2/projects/{project_pub_id}/asset-confirmations']['post']['responses']['201']['content']['application/json'];
export type AssetConfirmationView = Pick<
  AssetConfirmationContractView,
  | 'pub_id'
  | 'project_pub_id'
  | 'revision'
  | 'brand_name'
  | 'website'
  | 'product_name'
  | 'competitor_name'
  | 'prohibited_claim'
  | 'created_at'
>;
export type AssetConfirmationPage = {
  data: AssetConfirmationView[];
  next_cursor: string | null;
};
type CustomerAccountContractView =
  paths['/api/v2/customer/platform-accounts']['get']['responses']['200']['content']['application/json'][number];
export type CustomerAccountView = Pick<
  CustomerAccountContractView,
  | 'pub_id'
  | 'account_mask'
  | 'platform_label'
  | 'owner_label'
  | 'custody_mode'
  | 'admission_level'
  | 'scopes'
  | 'authorization_expires_at'
  | 'region_label'
  | 'session_health'
  | 'last_verified_at'
  | 'intervention_status'
  | 'revocation_receipt_pub_id'
  | 'revoked_at'
>;
export type CustomerAccountCreate =
  paths['/api/v2/customer/platform-accounts']['post']['requestBody']['content']['application/json'];
type ResponsibleMemberContractView =
  paths['/api/v2/customer/platform-accounts/responsible-members']['get']['responses']['200']['content']['application/json'][number];
export type ResponsibleMemberView = Pick<
  ResponsibleMemberContractView,
  'user_pub_id' | 'label' | 'role'
>;
export type CustomerAuthorizationCreate =
  paths['/api/v2/customer/platform-accounts/{account_pub_id}/authorizations']['post']['requestBody']['content']['application/json'];
export type CustomerPairingCreate =
  paths['/api/v2/customer/platform-accounts/{account_pub_id}/pairings']['post']['requestBody']['content']['application/json'];
type CustomerPairingContractView =
  paths['/api/v2/customer/platform-accounts/{account_pub_id}/pairings']['post']['responses']['201']['content']['application/json'];
export type CustomerPairingView = Pick<
  CustomerPairingContractView,
  | 'pub_id'
  | 'account_pub_id'
  | 'account_mask'
  | 'allowed_domain'
  | 'action'
  | 'challenge_type'
  | 'state'
  | 'expires_at'
>;
type CustomerEventContractView =
  paths['/api/v2/customer/platform-accounts/{account_pub_id}/events']['get']['responses']['200']['content']['application/json'][number];
export type CustomerEventView = Pick<
  CustomerEventContractView,
  'pub_id' | 'event_type' | 'occurred_at'
>;
export type OperationsLifecycleSnapshotProjection = {
  metrics: {
    runningRuns: number;
    projectCount: number;
    pendingInterventions: number;
    healthySessions: number;
    totalSessions: number;
    delayedRuns: number;
    p95DelayLabel: string;
  };
  activity: {
    pubId: string;
    occurredAtLabel: string;
    eventLabel: string;
    objectLabel: string;
    resultLabel: string;
    tone: 'positive' | 'warning' | 'danger' | 'neutral';
  }[];
  accounts: {
    accountMask: string;
    platformLabel: string;
    ownerLabel: string;
    custodyMode: 'server' | 'customer-device' | 'hybrid';
    admissionLevel:
      | 'catalogued'
      | 'adapter_ready'
      | 'login_verified'
      | 'read_verified'
      | 'draft_verified'
      | 'publish_verified'
      | 'suspended';
    scopes: readonly ('read' | 'query' | 'draft' | 'publish')[];
    expiresLabel: string;
    regionLabel: string;
    sessionHealth: 'healthy' | 'degraded' | 'challenge_required' | 'revoked';
    lastVerifiedLabel: string;
    interventionStatus:
      | 'none'
      | 'waiting'
      | 'paired'
      | 'refused'
      | 'timed_out'
      | 'failed'
      | 'completed';
  }[];
  interventions: {
    pubId: string;
    accountMask: string;
    challengeType: 'otp' | 'qr' | 'push' | 'passkey' | 'face' | 'graphical';
    state: 'none' | 'waiting' | 'paired' | 'refused' | 'timed_out' | 'failed' | 'completed';
    leaseLabel: string;
    expiresLabel: string;
  }[];
  events: {
    pubId: string;
    eventLabel: string;
    detailLabel: string;
    occurredAtLabel: string;
  }[];
  revocationReceipt: null;
  projectionTruncated: boolean;
};
type WorkflowAccepted =
  paths['/api/v2/customer/platform-accounts/{account_pub_id}/revoke']['post']['responses']['202']['content']['application/json'];
export type CustomerRevocationSafeReceipt = { accepted: true };
type AnalyticsOverviewResponse =
  paths['/api/v2/analytics/overview']['get']['responses']['200']['content']['application/json'];
type AnalyticsOverviewContractMetric = AnalyticsOverviewResponse[number];
export type AnalyticsOverviewMetric = Pick<
  AnalyticsOverviewContractMetric,
  | 'metric'
  | 'value'
  | 'numerator'
  | 'denominator'
  | 'state'
  | 'metric_version'
  | 'scorer_version'
  | 'filter_hash'
>;
export type AnalyticsOverviewSafeResponse = AnalyticsOverviewMetric[];
type CustomerDashboardContractResponse =
  paths['/api/v2/customer-dashboard/projects/{project_pub_id}']['get']['responses']['200']['content']['application/json'];
type CustomerMetricContractView = CustomerDashboardContractResponse['metrics'][number];
export type CustomerMetricProjection = Pick<
  CustomerMetricContractView,
  'code' | 'label' | 'group' | 'format' | 'direction' | 'value' | 'state' | 'version'
>;
export type CustomerDimensionProjection = {
  key: string;
  label: string;
  metrics: CustomerMetricProjection[];
};
export type CustomerCompetitorProjection = {
  name: string;
  metrics: CustomerMetricProjection[];
};
export type CustomerQuestionProjection = {
  query_pub_id: string;
  query_text: string;
  query_group: string | null;
  metrics: CustomerMetricProjection[];
};
export type CustomerSourceProjection = {
  host: string;
  references: number;
  share: number | null;
  own_source: boolean;
  answers: number;
};
export type CustomerTrendProjection = {
  date: string;
  metrics: CustomerMetricProjection[];
};
export type CustomerDashboardProjection = Pick<
  CustomerDashboardContractResponse,
  | 'schema_version'
  | 'metric_version'
  | 'project_pub_id'
  | 'brand_name'
  | 'state'
  | 'generated_at'
  | 'as_of'
  | 'snapshot_hash'
> & {
  window: {
    start: string | null;
    end: string | null;
    filters: Partial<Record<'model' | 'region' | 'mode', string>>;
  };
  metrics: CustomerMetricProjection[];
  models: CustomerDimensionProjection[];
  competitors: CustomerCompetitorProjection[];
  questions: CustomerQuestionProjection[];
  sources: CustomerSourceProjection[];
  regions: CustomerDimensionProjection[];
  modes: CustomerDimensionProjection[];
  trends: CustomerTrendProjection[];
  risk: {
    metrics: CustomerMetricProjection[];
    by_model: CustomerDimensionProjection[];
  };
  source_audit: {
    metrics: CustomerMetricProjection[];
    verdicts: Record<string, number>;
  };
};
type CustomerMetricCatalogContractResponse =
  paths['/api/v2/customer-dashboard/metrics/catalog']['get']['responses']['200']['content']['application/json'];
type CustomerMetricSpecContractView = CustomerMetricCatalogContractResponse['metrics'][number];
export type CustomerMetricSpecProjection = Pick<
  CustomerMetricSpecContractView,
  'code' | 'label' | 'group' | 'format' | 'direction' | 'description' | 'version'
>;
export type CustomerMetricCatalogProjection = {
  schema_version: 'customer-metric-catalog-v1';
  metrics: CustomerMetricSpecProjection[];
};
type AnalyticsBreakdownContractResponse =
  paths['/api/v2/analytics/breakdown']['get']['responses']['200']['content']['application/json'];
type AnalyticsBreakdownContractView = AnalyticsBreakdownContractResponse[number];
export type AnalyticsBreakdownView = Pick<
  AnalyticsBreakdownContractView,
  | 'group_by'
  | 'day'
  | 'model'
  | 'region'
  | 'mode'
  | 'question_pub_id'
  | 'question_text'
  | 'answer_count'
  | 'mentioned_count'
  | 'mention_rate'
  | 'average_rank'
  | 'citation_coverage'
>;
export type AnalyticsBreakdownResponse = AnalyticsBreakdownView[];
export type AnalyticsBreakdownGroup =
  paths['/api/v2/analytics/breakdown']['get']['parameters']['query']['group_by'];
type AnalyticsAnswerPage =
  paths['/api/v2/analytics/answers']['get']['responses']['200']['content']['application/json'];
type AnalyticsAnswerRelations =
  paths['/api/v2/analytics/answers/{answer_pub_id}/relations']['get']['responses']['200']['content']['application/json'];
type AnalyticsDeltaResponse =
  paths['/api/v2/analytics/delta']['get']['responses']['200']['content']['application/json'];
type AnalyticsCompetitorResponse =
  paths['/api/v2/analytics/competitors']['get']['responses']['200']['content']['application/json'];
export type MetricExportCreate =
  paths['/api/v2/exports/metrics']['post']['requestBody']['content']['application/json'];
type MetricExportCreateResponse =
  paths['/api/v2/exports/metrics']['post']['responses']['201']['content']['application/json'];
export type MetricExportSafeReceipt = {
  exportPubId: string;
  evidencePubId: string;
  format: 'xlsx';
  rowCount: number;
  filterHash: string;
  factSnapshotHash: string;
  metricVersion: string;
  scorerVersion: string;
};
type ReportDetail =
  paths['/api/v2/reports/{report_pub_id}']['get']['responses']['200']['content']['application/json'];
export type ReportCreateInput =
  paths['/api/v2/reports']['post']['requestBody']['content']['application/json'];
type ReportCreateResponse =
  paths['/api/v2/reports']['post']['responses']['201']['content']['application/json'];
export type ReportCreateSafeReceipt = {
  reportPubId: string;
  reportVersionPubId: string;
  state: string;
  factSnapshotHash: string;
};
declare const safeStructuredRecordBrand: unique symbol;
export type SafeStructuredValue =
  | null
  | boolean
  | number
  | string
  | SafeStructuredValue[]
  | SafeStructuredRecord;
export type SafeStructuredRecord = {
  readonly [safeStructuredRecordBrand]: true;
  [key: string]: SafeStructuredValue;
};
type ReportVersionContractView = ReportDetail['versions'][number];
type ReportComponentContractView = ReportVersionContractView['components'][number];
type ReportFrozenFactContractView = ReportVersionContractView['frozen_facts'][number];
type ReportArtifactContractView = ReportVersionContractView['artifacts'][number];
type ReportEvidenceBindingContractView = ReportVersionContractView['evidence_bindings'][number];
type ReportReviewContractView = ReportVersionContractView['reviews'][number];
type ReportCommentContractView = ReportVersionContractView['comments'][number];
type ReportEventContractView = ReportVersionContractView['events'][number];
type OptimizationActionContractView = ReportDetail['optimization_actions'][number];
type EffectRetestContractView = OptimizationActionContractView['effect_retests'][number];
export type ReportComponentSafeView = Pick<
  ReportComponentContractView,
  'pub_id' | 'report_version_pub_id' | 'component_type' | 'ordinal' | 'source' | 'created_at'
> & {
  payload: SafeStructuredRecord;
};
export type ReportFrozenFactSafeView = Pick<
  ReportFrozenFactContractView,
  'pub_id' | 'report_version_pub_id' | 'ordinal' | 'payload_hash' | 'created_at'
> & {
  payload: SafeStructuredRecord;
};
export type ReportArtifactSafeView = Pick<
  ReportArtifactContractView,
  | 'pub_id'
  | 'report_version_pub_id'
  | 'format'
  | 'evidence_pub_id'
  | 'mime_type'
  | 'byte_size'
  | 'sha256'
  | 'created_at'
>;
export type ReportEvidenceBindingSafeView = Pick<
  ReportEvidenceBindingContractView,
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
>;
export type ReportReviewSafeView = Pick<
  ReportReviewContractView,
  'pub_id' | 'report_version_pub_id' | 'reviewer_pub_id' | 'decision' | 'rationale' | 'created_at'
>;
export type ReportCommentSafeView = Pick<
  ReportCommentContractView,
  | 'pub_id'
  | 'report_version_pub_id'
  | 'parent_pub_id'
  | 'author_pub_id'
  | 'body'
  | 'resolved_at'
  | 'created_at'
>;
export type ReportEventSafeView = Pick<
  ReportEventContractView,
  'pub_id' | 'report_version_pub_id' | 'event_type' | 'actor_pub_id' | 'created_at'
> & {
  data: SafeStructuredRecord;
};
export type ReportVersionSafeView = Pick<
  ReportVersionContractView,
  | 'pub_id'
  | 'version_number'
  | 'window_start'
  | 'window_end'
  | 'metric_version'
  | 'scorer_version'
  | 'fact_snapshot_hash'
  | 'status'
> & {
  filters: SafeStructuredRecord;
  components: ReportComponentSafeView[];
  frozen_facts: ReportFrozenFactSafeView[];
  artifacts: ReportArtifactSafeView[];
  evidence_bindings: ReportEvidenceBindingSafeView[];
  reviews: ReportReviewSafeView[];
  comments: ReportCommentSafeView[];
  events: ReportEventSafeView[];
};
export type EffectRetestSafeView = Pick<
  EffectRetestContractView,
  'pub_id' | 'action_pub_id' | 'measured_at' | 'recorded_by_pub_id' | 'created_at'
> & {
  result: SafeStructuredRecord;
};
export type OptimizationActionSafeView = Pick<
  OptimizationActionContractView,
  'pub_id' | 'description' | 'owner_pub_id' | 'state' | 'created_at' | 'updated_at'
> & {
  baseline: SafeStructuredRecord | null;
  outcome: SafeStructuredRecord | null;
  effect_retests: EffectRetestSafeView[];
};
export type ReportVersionReadCollection =
  | 'components'
  | 'section_evidence_ids'
  | 'frozen_facts'
  | 'artifacts'
  | 'evidence_bindings'
  | 'reviews'
  | 'comments'
  | 'events';
export type ReportDetailProjection = Pick<
  ReportDetail,
  'pub_id' | 'project_pub_id' | 'title' | 'state' | 'created_at' | 'updated_at'
> & {
  versions: ReportVersionSafeView[];
  optimization_actions: OptimizationActionSafeView[];
  projection: {
    versions: ProjectedCollection<never>['projection'];
    optimization_actions: ProjectedCollection<never>['projection'];
    version_collections: Record<
      string,
      Record<ReportVersionReadCollection, ProjectedCollection<never>['projection']>
    >;
    action_retests: Record<string, ProjectedCollection<never>['projection']>;
  };
};
export type ReportDetailResult =
  | ProjectResourceResult<ReportDetailProjection>
  | { kind: 'invalid' };
export type ReportRevisionCreate =
  paths['/api/v2/reports/{report_pub_id}/versions']['post']['requestBody']['content']['application/json'];
export type ReportRevisionSafeReceipt = {
  reportPubId: string;
  reportVersionPubId: string;
  versionNumber: number;
  factSnapshotHash: string;
};
export type ReportReviewCreate =
  paths['/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/reviews']['post']['requestBody']['content']['application/json'];
type ReportReviewCreateResponse =
  paths['/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/reviews']['post']['responses']['201']['content']['application/json'];
export type ReportReviewSafeReceipt = { reviewPubId: string };
export type ReportCommentCreate =
  paths['/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/comments']['post']['requestBody']['content']['application/json'];
type ReportCommentCreateResponse =
  paths['/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/comments']['post']['responses']['201']['content']['application/json'];
type ReportDeliveryContractView =
  paths['/api/v2/reports/{report_pub_id}/deliveries']['get']['responses']['200']['content']['application/json'][number];
export type ReportDeliverySafeView = Pick<
  ReportDeliveryContractView,
  'pub_id' | 'report_pub_id' | 'recipient_pub_id' | 'delivered_at' | 'confirmed_at'
> & {
  confirmation_comment: null;
};
export type ReportDeliveryProjection = ProjectedCollection<ReportDeliverySafeView>;
export type ReportDeliveryCreate =
  paths['/api/v2/reports/{report_pub_id}/deliveries']['post']['requestBody']['content']['application/json'];
type ReportDeliveryCreateResponse =
  paths['/api/v2/reports/{report_pub_id}/deliveries']['post']['responses']['201']['content']['application/json'];
export type ReportDeliveryConfirm =
  paths['/api/v2/reports/{report_pub_id}/deliveries/{delivery_pub_id}/confirm']['post']['requestBody']['content']['application/json'];
type ReportDeliveryConfirmResponse =
  paths['/api/v2/reports/{report_pub_id}/deliveries/{delivery_pub_id}/confirm']['post']['responses']['200']['content']['application/json'];
export type ReportCommentSafeReceipt = { reportPubId: string; commentPubId: string };
export type ReportDeliverySafeReceipt = { reportPubId: string; deliveryPubId: string };
export type ReportDeliveryConfirmationSafeReceipt = {
  deliveryPubId: string;
  state: 'confirmed';
};
export type ReportActionCreate =
  paths['/api/v2/reports/{report_pub_id}/actions']['post']['requestBody']['content']['application/json'];
type ReportActionCreateResponse =
  paths['/api/v2/reports/{report_pub_id}/actions']['post']['responses']['201']['content']['application/json'];
export type ReportActionSafeReceipt = { actionPubId: string };
export type ReportActionUpdate =
  paths['/api/v2/reports/{report_pub_id}/actions/{action_pub_id}']['patch']['requestBody']['content']['application/json'];
export type ReportEffectRetestCreate =
  paths['/api/v2/reports/{report_pub_id}/actions/{action_pub_id}/effect-retests']['post']['requestBody']['content']['application/json'];
type ReportEffectRetestCreateResponse =
  paths['/api/v2/reports/{report_pub_id}/actions/{action_pub_id}/effect-retests']['post']['responses']['201']['content']['application/json'];
export type ReportEffectRetestSafeReceipt = { effectRetestPubId: string };
export type ReportArtifactFormat =
  paths['/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/artifacts/{format_name}']['get']['parameters']['path']['format_name'];
export type ReportArtifactIntegrity = {
  byteSize: number;
  mimeType: string;
  sha256: string;
};
export type VerifiedReportArtifact = ReportArtifactIntegrity & { blob: Blob };
type EvidenceAssetPage =
  paths['/api/v2/evidence/assets']['get']['responses']['200']['content']['application/json'];
export type EvidenceAssetSafeView = {
  pub_id: string;
  kind: string;
  mime_type: string;
  capture_time: string;
  sha256: string;
};
export type EvidenceAssetProjection = ProjectedContractPage<EvidenceAssetSafeView>;
export type EvidenceAssetIntegrity = {
  byteSize: number;
  mimeType: string;
  sha256: string;
};
export type VerifiedEvidenceAsset = EvidenceAssetIntegrity & { blob: Blob };
export type EvidencePackageCreate =
  paths['/api/v2/evidence/packages']['post']['requestBody']['content']['application/json'];
type EvidencePackageCreateResponse =
  paths['/api/v2/evidence/packages']['post']['responses']['201']['content']['application/json'];
export type EvidencePackageSafeReceipt = {
  packagePubId: string;
  manifestSha256: string;
  state: 'ready';
};
export type InvestigationSummarySafeView = {
  pub_id: string;
  title: string;
  state: 'draft' | 'collecting' | 'review' | 'decided' | 'appealed' | 'corrected';
  access_class: 'public' | 'customer_private';
  created_at: string;
  updated_at: string;
  claim_count: number;
  source_cluster_count: number;
  probability: string | null;
  latest_verdict: 'likely' | 'unlikely' | 'uncertain' | 'insufficient' | null;
};
export type InvestigationPageProjection = ProjectedContractPage<InvestigationSummarySafeView>;
type InvestigationDetail =
  paths['/api/v2/intelligence/investigations/{investigation_pub_id}']['get']['responses']['200']['content']['application/json'];
type InvestigationVisualDiffs =
  paths['/api/v2/intelligence/investigations/{investigation_pub_id}/visual-diffs']['get']['responses']['200']['content']['application/json'];
export type InvestigationDetailCollection =
  | 'scores'
  | 'explanations'
  | 'claims'
  | 'evidenceMatrix'
  | 'sourceIndependence'
  | 'graph'
  | 'appeals'
  | 'verdicts';
export type InvestigationScoreSafeView = {
  pub_id: string;
  probability: number;
  evidence_sufficiency: number;
  uncertainty: number;
  rule_version: string;
  explanation: string[];
  created_at: string;
};
export type InvestigationClaimSafeView = {
  pub_id: string;
  normalized_text: string;
  verifiability: string;
};
export type InvestigationEvidenceRelation = 'supports' | 'contradicts' | 'insufficient';
export type InvestigationEvidenceSafeView = {
  pub_id: string;
  claim_pub_id: string;
  evidence_pub_id: string;
  relation: InvestigationEvidenceRelation;
  source_cluster: string;
  independence_weight: number;
  rationale: string;
};
export type InvestigationSourceSafeView = {
  pub_id: string;
  source_pub_id: string;
  cluster_id: string;
  independence_weight: number;
  circular_citation_risk: number;
};
export type InvestigationGraphRelation =
  | InvestigationEvidenceRelation
  | 'derived_from'
  | 'near_duplicate'
  | 'published_by'
  | 'cites'
  | 'mentions';
export type InvestigationGraphSafeView = {
  from_pub_id: string;
  to_pub_id: string;
  relation: InvestigationGraphRelation;
  weight: number | null;
  evidence_pub_id: string | null;
};
export type InvestigationAppealState = 'open' | 'reviewing' | 'upheld' | 'corrected' | 'rejected';
export type InvestigationAppealResolution = Exclude<InvestigationAppealState, 'open' | 'reviewing'>;
export type InvestigationAppealSafeView = {
  pub_id: string;
  state: InvestigationAppealState;
  submitted_by_pub_id: string;
  reason: string;
  resolution: InvestigationAppealResolution | null;
  resolved_by_pub_id: string | null;
  resolution_rationale: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
};
export type InvestigationVerdict = 'likely' | 'unlikely' | 'uncertain' | 'insufficient';
export type InvestigationVerdictSafeView = {
  pub_id: string;
  verdict: InvestigationVerdict;
  reviewer_pub_id: string;
  rationale: string;
  supersedes_pub_id: string | null;
  created_at: string;
};
export type InvestigationDetailProjection = {
  pub_id: string;
  scores: InvestigationScoreSafeView[];
  claims: InvestigationClaimSafeView[];
  evidence_matrix: InvestigationEvidenceSafeView[];
  source_independence: InvestigationSourceSafeView[];
  graph: InvestigationGraphSafeView[];
  appeals: InvestigationAppealSafeView[];
  verdicts: InvestigationVerdictSafeView[];
  projection: Record<InvestigationDetailCollection, ProjectedCollection<never>['projection']>;
};
export type InvestigationDetailResult =
  | ProjectResourceResult<InvestigationDetailProjection>
  | { kind: 'invalid' };
export type InvestigationPageHistorySafeView = {
  content_pub_id: string;
  version_pub_id: string;
  canonical_url: string;
  title: string | null;
  version_number: number;
  body_hash: string;
  evidence_pub_id: string | null;
  captured_at: string;
  published_at: string | null;
  snapshot_pub_id: string | null;
  snapshot_number: number | null;
  normalized_text_hash: string | null;
  perceptual_hash: string | null;
};
export type InvestigationPageHistoryProjection =
  ProjectedCollection<InvestigationPageHistorySafeView>;
type InvestigationVisualDiffContractView = InvestigationVisualDiffs[number];
export type InvestigationVisualDiffSafeView = Pick<
  InvestigationVisualDiffContractView,
  | 'pub_id'
  | 'content_pub_id'
  | 'before_version_pub_id'
  | 'after_version_pub_id'
  | 'before_evidence_pub_id'
  | 'after_evidence_pub_id'
  | 'similarity'
  | 'visual_diff_available'
  | 'created_at'
> & {
  text_diff: { before_hash: string; after_hash: string } | null;
};
export type InvestigationVisualDiffSafeResponse = InvestigationVisualDiffSafeView[];
export type InvestigationVisualDiffsProjection =
  ProjectedCollection<InvestigationVisualDiffSafeView>;
export type VerdictCreate =
  paths['/api/v2/intelligence/investigations/{investigation_pub_id}/verdicts']['post']['requestBody']['content']['application/json'];
type VerdictCreateResponse =
  paths['/api/v2/intelligence/investigations/{investigation_pub_id}/verdicts']['post']['responses']['201']['content']['application/json'];
export type VerdictSafeReceipt = { verdictPubId: string };
export type AppealCreate =
  paths['/api/v2/intelligence/investigations/{investigation_pub_id}/appeals']['post']['requestBody']['content']['application/json'];
type AppealCreateResponse =
  paths['/api/v2/intelligence/investigations/{investigation_pub_id}/appeals']['post']['responses']['201']['content']['application/json'];
export type AppealSafeReceipt = { appealPubId: string };
export type AppealResolution =
  paths['/api/v2/intelligence/investigations/{investigation_pub_id}/appeals/{appeal_pub_id}/resolve']['post']['requestBody']['content']['application/json'];
type AppealResolutionResponse =
  paths['/api/v2/intelligence/investigations/{investigation_pub_id}/appeals/{appeal_pub_id}/resolve']['post']['responses']['200']['content']['application/json'];
export type AppealResolutionSafeReceipt = { replacementVerdictPubId: string | null };
export type EvaluationDatasetCreate =
  paths['/api/v2/intelligence/evaluation-datasets']['post']['requestBody']['content']['application/json'];
export type EvaluationDatasetSafeView = {
  pub_id: string;
  version: string;
  dataset_sha256: string;
  state: 'draft' | 'approved' | 'revoked';
  case_count: number;
  positive_count: number;
  labeler_count: number;
  submitted_at: string;
  approved_at: string | null;
};
export type EvaluationDatasetPageProjection = ProjectedContractPage<EvaluationDatasetSafeView>;
export type EvaluationDatasetApprove =
  paths['/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/approve']['post']['requestBody']['content']['application/json'];
export type EvaluationRunCreate =
  paths['/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/runs']['post']['requestBody']['content']['application/json'];
export type EvaluationRunSafeView = {
  pub_id: string;
  dataset_pub_id: string;
  scorer_version: string;
  decision_threshold: string;
  calibration_bins: number;
  training_cluster_manifest_sha256: string;
  training_cluster_count: number;
  sample_count: number;
  admission_policy_version: string;
  admission_checks: {
    precision: boolean;
    recall: boolean;
    false_positive_rate: boolean;
    brier_score: boolean;
    expected_calibration_error: boolean;
    explanation_completeness: boolean;
  };
  admission_passed: boolean;
  model_admission_state: 'admitted' | 'revoked' | null;
  metrics: {
    precision: string | null;
    recall: string | null;
    false_positive_rate: string | null;
    brier_score: string;
    expected_calibration_error: string;
    explanation_completeness_rate: string;
    sample_count: number;
    positive_count: number;
    negative_count: number;
    dataset_version: string;
    scorer_version: string;
    evaluation_sha256: string;
  };
  required_explanation_fields: string[];
  created_at: string;
};
export type EvaluationRunPageProjection = ProjectedContractPage<EvaluationRunSafeView>;
export type ModelAdmissionCreate =
  paths['/api/v2/intelligence/evaluation-runs/{evaluation_run_pub_id}/admit']['post']['requestBody']['content']['application/json'];
export type ModelAdmissionSafeView = {
  pub_id: string;
  evaluation_run_pub_id: string;
  scorer_version: string;
  state: 'admitted' | 'revoked';
  rationale: string;
  admitted_at: string;
  revoked_at: string | null;
};
export type ModelAdmissionPageProjection = ProjectedContractPage<ModelAdmissionSafeView>;

export type GeoApiClient = ReturnType<typeof createClient<paths>>;
type ProjectedApiClientOverride = object;

const configuredApiBase =
  (import.meta as ImportMeta & { env?: { VITE_GEO_API_BASE?: string } }).env?.VITE_GEO_API_BASE ??
  '';

const reportArtifactMediaTypes: Readonly<Record<ReportArtifactFormat, string>> = {
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  html: 'text/html',
  pdf: 'application/pdf',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
};
const quotationGeneratePath = '/api/v2/quotations/generate';
const quotationDocxMimeType =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

/**
 * 帖子分析截图/标注图字节流路径（evidence 内容下载同级的 image/png 边界；kind 词表收窄，
 * 其他路径一律回退 application/json 预期）。
 */
const postAnalysisImageAssetPath =
  /^\/api\/v2\/post-analysis\/items\/[^/]+\/assets\/(?:screenshot|annotated)$/;

export const geoApiJsonResponseMaxBytes = 25 * 1024 * 1024;
// Self-media is a separately requested lazy artifact and can grow beyond the shared JSON
// boundary as more suppliers are added. Keep the exception path-scoped.
export const mediaWemediaDatasetMaxBytes = 64 * 1024 * 1024;

function expectedApiMediaType(request: Request, response: Response): string | null {
  if (response.status === 204 || response.status === 205 || request.method === 'HEAD') return null;
  if (!response.ok) return 'application/json';
  const artifactMatch =
    /^\/api\/v2\/reports\/[^/]+\/versions\/[^/]+\/artifacts\/(docx|html|pdf|xlsx)$/.exec(
      new URL(request.url).pathname,
    );
  const evidenceImageMatch = /^\/api\/v2\/evidence\/assets\/[^/]+\/content$/.test(
    new URL(request.url).pathname,
  );
  const quotationMatch = new URL(request.url).pathname === quotationGeneratePath;
  const postAnalysisImageMatch = postAnalysisImageAssetPath.test(new URL(request.url).pathname);
  return artifactMatch
    ? (reportArtifactMediaTypes[artifactMatch[1] as ReportArtifactFormat] ?? 'application/json')
    : quotationMatch
      ? quotationDocxMimeType
      : evidenceImageMatch || postAnalysisImageMatch
        ? 'image/png'
        : 'application/json';
}

async function boundGeoApiJsonResponse(
  response: Response,
  maxBytes = geoApiJsonResponseMaxBytes,
): Promise<Response> {
  const declaredLength = response.headers.get('content-length');
  if (
    declaredLength !== null &&
    (!/^(?:0|[1-9]\d*)$/u.test(declaredLength) || Number(declaredLength) > maxBytes)
  ) {
    await response.body?.cancel().catch(() => undefined);
    throw new Error('GEO Platform response body is unavailable');
  }
  if (!response.body) return response;

  const probe = response.clone();
  if (!probe.body) return response;
  const reader = probe.body.getReader();
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await Promise.all([
          reader.cancel().catch(() => undefined),
          response.body.cancel().catch(() => undefined),
        ]);
        throw new Error('GEO Platform response body is unavailable');
      }
    }
  } catch {
    await Promise.all([
      reader.cancel().catch(() => undefined),
      response.body.cancel().catch(() => undefined),
    ]);
    throw new Error('GEO Platform response body is unavailable');
  } finally {
    reader.releaseLock();
  }
  return response;
}

/**
 * Production bundles never transmit browser identity headers: native_session authenticates by
 * cookie, and every other production identity mode rejects these headers outright, so sending
 * them only leaks actor claims into logs and violates the zero-actor-header browser invariant.
 * The validated triple stays in @geo/auth memory as a local mutation-guard fingerprint. Fixture
 * builds (vite dev / e2e) keep transmitting them for the contract-fixture identity flow.
 */
export type BrowserBuildIdentityEnv = {
  DEV?: boolean;
  VITE_ALLOW_CONTRACT_FIXTURES?: string;
};

export const allowsFixtureIdentityHeaders = (env: BrowserBuildIdentityEnv | undefined): boolean =>
  env?.DEV === true || env?.VITE_ALLOW_CONTRACT_FIXTURES === 'true';

async function secureGeoApiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const source = new Request(input, init);
  const headers = new Headers(source.headers);
  if (
    !allowsFixtureIdentityHeaders(
      (import.meta as ImportMeta & { env?: BrowserBuildIdentityEnv }).env,
    )
  ) {
    headers.delete('X-Tenant-Id');
    headers.delete('X-Actor-Id');
    headers.delete('X-Actor-Role');
  }
  const artifactMatch =
    /^\/api\/v2\/reports\/[^/]+\/versions\/[^/]+\/artifacts\/(docx|html|pdf|xlsx)$/.exec(
      new URL(source.url).pathname,
    );
  const evidenceImageMatch = /^\/api\/v2\/evidence\/assets\/[^/]+\/content$/.test(
    new URL(source.url).pathname,
  );
  const quotationMatch = new URL(source.url).pathname === quotationGeneratePath;
  const postAnalysisImageMatch = postAnalysisImageAssetPath.test(new URL(source.url).pathname);
  headers.set(
    'Accept',
    artifactMatch
      ? (reportArtifactMediaTypes[artifactMatch[1] as ReportArtifactFormat] ?? 'application/json')
      : quotationMatch
        ? quotationDocxMimeType
        : evidenceImageMatch || postAnalysisImageMatch
          ? 'image/png'
          : 'application/json',
  );
  const request = new Request(source, {
    headers,
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  });
  const response = await globalThis.fetch(request);
  const expectedMediaType = expectedApiMediaType(request, response);
  const mediaType = response.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase();
  if (expectedMediaType !== null && mediaType !== expectedMediaType) {
    await response.body?.cancel().catch(() => undefined);
    throw new Error('GEO Platform response media type is unavailable');
  }
  if (expectedMediaType !== 'application/json') return response;
  const maxBytes =
    new URL(request.url).pathname === '/api/v2/datasets/media-wemedia'
      ? mediaWemediaDatasetMaxBytes
      : geoApiJsonResponseMaxBytes;
  return boundGeoApiJsonResponse(response, maxBytes);
}

export function createGeoApiClient(baseUrl = configuredApiBase): GeoApiClient {
  return createClient<paths>({
    baseUrl,
    // Resolve at request time so browser/test runtimes can install an audited fetch boundary
    // after module evaluation without the client retaining a stale implementation.
    fetch: secureGeoApiFetch,
  });
}

/**
 * Generated-path client for same-origin browser calls. Application code must use this boundary
 * instead of duplicating OpenAPI request or response shapes.
 */
export const apiClient = createGeoApiClient();

/**
 * Projected wrappers intentionally expose no raw OpenAPI method type through their optional
 * test override. S01's separately owned execution feature still consumes the raw client above;
 * every S03 application is architecture-guarded to consume only projected wrappers.
 */
const projectedApiClient = (client: ProjectedApiClientOverride): GeoApiClient =>
  client as GeoApiClient;

export async function getHealth(
  client: ProjectedApiClientOverride = apiClient,
): Promise<HealthProjection> {
  const { data, error } = await projectedApiClient(client).GET('/api/v2/health');
  if (error || !data) {
    throw new Error('GEO Platform health endpoint is unavailable');
  }
  const projected = projectHealthBoundary(data);
  if (!projected) throw new Error('GEO Platform health endpoint is unavailable');
  return projected;
}

export type IdentitySessionResult =
  | {
      kind: 'ready';
      session: IdentitySessionProjection;
      projects: IdentityProjectPageProjection;
    }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

export type ProjectResourceResult<T> =
  | { kind: 'ready'; data: T }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

export type ProjectedCollection<T> = {
  data: T[];
  projection: {
    total: number;
    shown: number;
    invalid: boolean;
  };
};

export type ProjectedCursorPage<T> = {
  data: T[];
  next_cursor: string | null;
  projection: {
    total: number;
    shown: number;
    invalid: boolean;
  };
};

export type AnalyticsOverviewProjection = ProjectedCollection<AnalyticsOverviewMetric>;
export type AnalyticsBreakdownProjection = ProjectedCollection<AnalyticsBreakdownResponse[number]>;
export type AnalyticsCompetitorSafeView = {
  competitor: string;
  mention_rate: number;
  mention_count: number;
  answer_count: number;
};
export type AnalyticsCompetitorSafeResponse = AnalyticsCompetitorSafeView[];
export type AnalyticsCompetitorProjection = ProjectedCollection<AnalyticsCompetitorSafeView>;
export type AnalyticsDeltaMetric = {
  current: number | null;
  previous: number | null;
  delta: number | null;
};
export type AnalyticsDeltaSafeResponse = Partial<
  Record<'mention_rate' | 'average_rank' | 'top3_rate' | 'citation_coverage', AnalyticsDeltaMetric>
>;
export type AnalyticsDeltaProjection = {
  data: AnalyticsDeltaSafeResponse;
  projection: ProjectedCollection<never>['projection'];
};
export type ProjectedContractPage<T> = ProjectedCollection<T> & {
  page: {
    next_cursor: string | null;
    has_more: boolean;
  };
};
export type AnalyticsAnswerSafeView = {
  pub_id: string;
  project_pub_id: string;
  run_pub_id: string | null;
  config_version_pub_id: string | null;
  query_pub_id: string | null;
  query_text: string | null;
  response_text: string;
  model: string;
  region: string;
  mode: string;
  eligible: boolean;
  degraded: boolean;
  capture_time: string;
  mentioned: boolean | null;
  rank: number | null;
  sentiment: string | null;
  recommendation_state: string | null;
  citation_count: number;
};
export type AnalyticsAnswerProjection = ProjectedContractPage<AnalyticsAnswerSafeView>;
export type AnalyticsRelationCollection = 'citations' | 'evidence' | 'anchors' | 'history';
type AnalyticsCitationContractView = AnalyticsAnswerRelations['citations'][number];
type AnalyticsEvidenceContractView = AnalyticsAnswerRelations['evidence'][number];
type AnalyticsAnchorContractView = AnalyticsEvidenceContractView['anchors'][number];
export type AnalyticsCitationSafeView = Pick<
  AnalyticsCitationContractView,
  | 'pub_id'
  | 'ordinal'
  | 'canonical_url'
  | 'host'
  | 'title'
  | 'cited_text'
  | 'own_source'
  | 'content_hash'
>;
export type AnalyticsBoundingBoxSafeView = {
  x: number;
  y: number;
  width: number;
  height: number;
  confidence?: number;
};
export type AnalyticsAnchorSafeView = Pick<
  AnalyticsAnchorContractView,
  'pub_id' | 'text_start' | 'text_end' | 'page_number' | 'quote_hash'
> & {
  bbox: AnalyticsBoundingBoxSafeView | null;
};
export type AnalyticsEvidenceSafeView = Pick<
  AnalyticsEvidenceContractView,
  | 'pub_id'
  | 'relation_type'
  | 'kind'
  | 'access_class'
  | 'sha256'
  | 'mime_type'
  | 'byte_size'
  | 'source_url'
  | 'capture_time'
> & {
  anchors: AnalyticsAnchorSafeView[];
};
export type AnalyticsHistorySafeView = {
  pub_id: string;
  before_evidence_pub_id: string;
  after_evidence_pub_id: string;
  similarity: number | null;
  visual_diff_available: boolean;
  created_at: string;
};
export type AnalyticsAnswerRelationsProjection = {
  answer_pub_id: string;
  answer_citations: AnalyticsCitationSafeView[];
  brand_mention_evidence: AnalyticsEvidenceSafeView[];
  opened_source_previews: AnalyticsEvidenceSafeView[];
  citations: AnalyticsCitationSafeView[];
  evidence: AnalyticsEvidenceSafeView[];
  history: AnalyticsHistorySafeView[];
  projection: Record<AnalyticsRelationCollection, ProjectedCollection<never>['projection']>;
};

export const customerAccountLifecycleProjectionLimits = {
  accounts: 1,
  responsibleMembers: 100,
  events: 100,
  pairings: 50,
} as const;

export const identityReadProjectionLimits = {
  projects: 50,
  members: 100,
  oidcBindings: 100,
} as const;

export const customerGovernanceProjectionLimits = {
  projectResources: 100,
  historyVersions: 100,
} as const;

export const customerAnalyticsProjectionLimits = {
  overview: 4,
  delta: 4,
  competitors: 50,
  day: 90,
  model: 20,
  regionMode: 50,
  question: 100,
} as const;

export const customerDashboardProjectionLimits = {
  metrics: 100,
  models: 100,
  competitors: 200,
  questions: 1_000,
  sources: 5_000,
  regions: 200,
  modes: 100,
  trends: 367,
  riskModels: 100,
  metricCatalog: 200,
} as const;

export const customerEvidenceReadProjectionLimits = {
  answers: 200,
  assets: 200,
  citations: 200,
  evidence: 200,
  anchors: 200,
  history: 200,
} as const;

export const customerReportReadProjectionLimits = {
  deliveries: 50,
} as const;

export const reportDetailReadProjectionLimits = {
  versions: 100,
  components: 100,
  sectionEvidenceIds: 100,
  frozenFacts: 500,
  artifacts: 4,
  evidenceBindings: 500,
  reviews: 200,
  comments: 500,
  events: 500,
  optimizationActions: 200,
  effectRetests: 200,
} as const;

export const intelligenceReadProjectionLimits = {
  investigations: 50,
  evaluationDatasets: 50,
  evaluationRuns: 50,
  modelAdmissions: 50,
  scores: 200,
  explanations: 40,
  claims: 200,
  evidenceMatrix: 500,
  sourceIndependence: 500,
  graph: 120,
  appeals: 200,
  verdicts: 200,
  historyPages: 200,
  historyDiffs: 200,
} as const;

const projectBoundedCollection = <Source, Projected>(
  values: Source[],
  limit: number,
  projector: (value: Source) => Projected | null,
): ProjectedCollection<Projected> => {
  const bounded = values.slice(0, limit);
  const data = bounded.flatMap((value) => {
    const projected = projector(value);
    return projected ? [projected] : [];
  });
  return {
    data,
    projection: {
      total: values.length,
      shown: data.length,
      invalid: data.length !== bounded.length,
    },
  };
};

const projectBoundedUniqueCollection = <Source, Projected>(
  values: Source[],
  limit: number,
  projector: (value: Source) => Projected | null,
  identity: (value: Projected) => string,
): ProjectedCollection<Projected> => {
  const bounded = values.slice(0, limit);
  const seen = new Set<string>();
  let invalid = false;
  const data = bounded.flatMap((value) => {
    const projected = projector(value);
    if (!projected) {
      invalid = true;
      return [];
    }
    const key = identity(projected);
    if (seen.has(key)) {
      invalid = true;
      return [];
    }
    seen.add(key);
    return [projected];
  });
  return {
    data,
    projection: {
      total: values.length,
      shown: data.length,
      invalid,
    },
  };
};

const classifyResourceFailure = (status: number): { kind: 'forbidden' | 'unavailable' } =>
  status === 401 || status === 403 || status === 404
    ? { kind: 'forbidden' }
    : { kind: 'unavailable' };

/** Resolves the provider-owned browser session before any role-gated projection is rendered. */
export async function getIdentitySession(
  headers: IdentitySessionHeaders = {},
  client: ProjectedApiClientOverride = apiClient,
): Promise<IdentitySessionResult> {
  try {
    const sessionResult = await projectedApiClient(client).GET('/api/v2/identity/session', {
      params: { header: headers },
    });
    if (!sessionResult.data) {
      return sessionResult.response.status === 401 || sessionResult.response.status === 403
        ? { kind: 'forbidden' }
        : { kind: 'unavailable' };
    }
    const session = projectIdentitySessionBoundary(sessionResult.data);
    if (!session) return { kind: 'unavailable' };
    const projectsResult = await projectedApiClient(client).GET('/api/v2/projects', {
      params: { header: headers, query: { limit: 50 } },
    });
    if (!projectsResult.data) {
      return projectsResult.response.status === 401 || projectsResult.response.status === 403
        ? { kind: 'forbidden' }
        : { kind: 'unavailable' };
    }
    const projects = projectIdentityProjectPageBoundary(projectsResult.data, session.tenant_pub_id);
    return projects && !projects.projection.invalid
      ? { kind: 'ready', session, projects }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

/**
 * Revokes the current native session. The logout endpoint is intentionally excluded from the
 * generated OpenAPI surface, so this wrapper drives the audited secure fetch boundary directly
 * instead of the typed client. Best-effort by contract: returns whether the server acknowledged
 * the revocation (204), and never throws — callers must always continue local cleanup.
 */
export async function logoutIdentitySession(
  request: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> = secureGeoApiFetch,
): Promise<boolean> {
  try {
    const response = await request(`${configuredApiBase}/api/v2/identity/logout`, {
      method: 'POST',
    });
    return response.status === 204;
  } catch {
    return false;
  }
}

export async function listIdentityMembers(
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<IdentityMemberProjection>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/identity/members', {
      params: { header: headers },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    if (!Array.isArray(result.data)) return { kind: 'unavailable' };
    return {
      kind: 'ready',
      data: projectBoundedUniqueCollection(
        result.data,
        identityReadProjectionLimits.members,
        projectIdentityMemberView,
        (member) => member.pub_id,
      ),
    };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createIdentityMember(
  body: IdentityMemberCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<IdentityMemberView>> {
  try {
    const result = await projectedApiClient(client).POST('/api/v2/identity/members', {
      params: { header: headers },
      body,
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectIdentityMemberWriteView(result.data, {
      expectedSubject: body.subject,
      expectedDisplayName: body.display_name,
      expectedRole: body.role,
      expectedServiceAccount: false,
      expectedState: 'active',
    });
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function revokeIdentityMember(
  membershipPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<IdentityMemberView>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/identity/members/{membership_pub_id}/revoke',
      {
        params: { path: { membership_pub_id: membershipPubId }, header: headers },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectIdentityMemberWriteView(result.data, {
      expectedMembershipPubId: membershipPubId,
      expectedState: 'revoked',
    });
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listOidcBindings(
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<OidcBindingProjection>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/identity/oidc-bindings', {
      params: { header: headers },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    if (!Array.isArray(result.data)) return { kind: 'unavailable' };
    return {
      kind: 'ready',
      data: projectBoundedUniqueCollection(
        result.data,
        identityReadProjectionLimits.oidcBindings,
        projectOidcBindingView,
        (binding) => binding.user_pub_id,
      ),
    };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function bindOidcIdentity(
  userPubId: string,
  body: OidcBindingCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<OidcBindingView>> {
  try {
    const result = await projectedApiClient(client).PUT(
      '/api/v2/identity/members/{user_pub_id}/oidc-binding',
      {
        params: { path: { user_pub_id: userPubId }, header: headers },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectOidcBindingWriteView(result.data, userPubId, true);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function revokeOidcIdentity(
  userPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<OidcBindingView>> {
  try {
    const result = await projectedApiClient(client).DELETE(
      '/api/v2/identity/members/{user_pub_id}/oidc-binding',
      {
        params: { path: { user_pub_id: userPubId }, header: headers },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectOidcBindingWriteView(result.data, userPubId, false);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

/** Generated-contract read boundary for the currently mounted Customer project catalog. */
export async function listProjectResources(
  projectPubId: string,
  kind: ProjectResourceKind,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ProjectedCollection<ProjectResourceView>>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/projects/{project_pub_id}/resources/{kind}',
      {
        params: {
          path: { project_pub_id: projectPubId, kind },
          query: { limit: 100 },
          header: headers,
        },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    if (!Array.isArray(result.data)) return { kind: 'unavailable' };
    return {
      kind: 'ready',
      data: projectBoundedCollection(
        result.data,
        customerGovernanceProjectionLimits.projectResources,
        (value) => projectProjectResourceView(value, projectPubId, kind),
      ),
    };
  } catch {
    return { kind: 'unavailable' };
  }
}

/** Generated-contract write boundary; callers must supply a fresh, non-secret idempotency key. */
export async function createProjectResource(
  projectPubId: string,
  kind: ProjectResourceKind,
  body: ProjectResourceWrite,
  headers: IdentitySessionHeaders,
  idempotencyKey: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ProjectResourceView>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/projects/{project_pub_id}/resources/{kind}',
      {
        params: {
          path: { project_pub_id: projectPubId, kind },
          header: { ...headers, 'Idempotency-Key': idempotencyKey },
        },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectProjectResourceView(result.data, projectPubId, kind);
    return projected && projectResourceWriteMatches(projected, kind, body)
      ? { kind: 'ready', data: projected }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listClientProfileVersions(
  projectPubId: string,
  headers: IdentitySessionHeaders,
  filters: { cursor?: number; limit?: number } = {},
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ProjectedCursorPage<ClientProfileView>>> {
  try {
    const limit = normalizeGovernanceHistoryLimit(filters.limit);
    const result = await projectedApiClient(client).GET(
      '/api/v2/projects/{project_pub_id}/client-profile/versions',
      {
        params: {
          path: { project_pub_id: projectPubId },
          query: {
            ...(filters.cursor ? { cursor: filters.cursor } : {}),
            limit,
          },
          header: headers,
        },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectGovernanceCursorPage(result.data, limit, (value) =>
      projectClientProfileBoundaryView(value, projectPubId),
    );
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createClientProfileVersion(
  projectPubId: string,
  body: ClientProfileWrite,
  headers: IdentitySessionHeaders,
  idempotencyKey: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ClientProfileView>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/projects/{project_pub_id}/client-profile/versions',
      {
        params: {
          path: { project_pub_id: projectPubId },
          header: { ...headers, 'Idempotency-Key': idempotencyKey },
        },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectClientProfileBoundaryView(result.data, projectPubId);
    return projected && clientProfileWriteMatches(projected, body)
      ? { kind: 'ready', data: projected }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listAssetConfirmations(
  projectPubId: string,
  headers: IdentitySessionHeaders,
  filters: { cursor?: number; limit?: number } = {},
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ProjectedCursorPage<AssetConfirmationView>>> {
  try {
    const limit = normalizeGovernanceHistoryLimit(filters.limit);
    const result = await projectedApiClient(client).GET(
      '/api/v2/projects/{project_pub_id}/asset-confirmations',
      {
        params: {
          path: { project_pub_id: projectPubId },
          query: {
            ...(filters.cursor ? { cursor: filters.cursor } : {}),
            limit,
          },
          header: headers,
        },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectGovernanceCursorPage(result.data, limit, (value) =>
      projectAssetConfirmationBoundaryView(value, projectPubId),
    );
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createAssetConfirmation(
  projectPubId: string,
  body: AssetConfirmationWrite,
  headers: IdentitySessionHeaders,
  idempotencyKey: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<AssetConfirmationView>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/projects/{project_pub_id}/asset-confirmations',
      {
        params: {
          path: { project_pub_id: projectPubId },
          header: { ...headers, 'Idempotency-Key': idempotencyKey },
        },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectAssetConfirmationBoundaryView(result.data, projectPubId);
    return projected && assetConfirmationWriteMatches(projected, body)
      ? { kind: 'ready', data: projected }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listCustomerAccounts(
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ProjectedCollection<CustomerAccountView>>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/customer/platform-accounts', {
      params: { header: headers },
    });
    return result.data
      ? {
          kind: 'ready',
          data: projectBoundedCollection(
            result.data,
            customerAccountLifecycleProjectionLimits.accounts,
            projectCustomerAccountView,
          ),
        }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getOperationsLifecycle(
  headers: IdentitySessionHeaders,
  limit = 100,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<OperationsLifecycleSnapshotProjection>> {
  try {
    const normalizedLimit = Number.isSafeInteger(limit) && limit >= 1 && limit <= 100 ? limit : 100;
    const result = await projectedApiClient(client).GET('/api/v2/operations/lifecycle', {
      params: { query: { limit: normalizedLimit }, header: headers },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectOperationsLifecycleSnapshot(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listResponsibleMembers(
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ProjectedCollection<ResponsibleMemberView>>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/customer/platform-accounts/responsible-members',
      {
        params: { header: headers },
      },
    );
    return result.data
      ? {
          kind: 'ready',
          data: projectBoundedCollection(
            result.data,
            customerAccountLifecycleProjectionLimits.responsibleMembers,
            projectResponsibleMemberView,
          ),
        }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getAnalyticsOverview(
  projectPubId: string,
  start: string,
  end: string,
  filters: { model?: string; region?: string; mode?: string },
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<AnalyticsOverviewProjection>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/analytics/overview', {
      params: {
        query: {
          project_pub_id: projectPubId,
          start,
          end,
          ...(filters.model ? { model: filters.model } : {}),
          ...(filters.region ? { region: filters.region } : {}),
          ...(filters.mode ? { mode: filters.mode } : {}),
        },
        header: headers,
      },
    });
    return result.data
      ? {
          kind: 'ready',
          data: projectBoundedCollection(
            result.data,
            customerAnalyticsProjectionLimits.overview,
            projectAnalyticsOverviewBoundary,
          ),
        }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getCustomerDashboard(
  projectPubId: string,
  start: string,
  end: string,
  filters: { model?: string; region?: string; mode?: string },
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<CustomerDashboardProjection>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/customer-dashboard/projects/{project_pub_id}',
      {
        params: {
          path: { project_pub_id: projectPubId },
          query: { start, end, ...filters },
          header: headers,
        },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectCustomerDashboardBoundary(result.data, projectPubId);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getCustomerMetricCatalog(
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<CustomerMetricCatalogProjection>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/customer-dashboard/metrics/catalog',
      { params: { header: headers } },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectCustomerMetricCatalogBoundary(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getAnalyticsBreakdown(
  projectPubId: string,
  start: string,
  end: string,
  groupBy: AnalyticsBreakdownGroup,
  dimensions: { model?: string; region?: string; mode?: string },
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<AnalyticsBreakdownProjection>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/analytics/breakdown', {
      params: {
        query: {
          project_pub_id: projectPubId,
          start,
          end,
          group_by: groupBy,
          ...dimensions,
        },
        header: headers,
      },
    });
    const limit =
      groupBy === 'region_mode'
        ? customerAnalyticsProjectionLimits.regionMode
        : customerAnalyticsProjectionLimits[groupBy];
    return result.data
      ? {
          kind: 'ready',
          data: projectBoundedCollection(result.data, limit, (value) =>
            projectAnalyticsBreakdownBoundary(value, groupBy),
          ),
        }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listAnalyticsAnswers(
  projectPubId: string,
  filters: {
    answerPubId?: string;
    model?: string;
    region?: string;
    mode?: string;
    cursor?: string;
    limit?: number;
  },
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<AnalyticsAnswerProjection>> {
  try {
    const limit =
      typeof filters.limit === 'number' &&
      Number.isSafeInteger(filters.limit) &&
      filters.limit >= 1 &&
      filters.limit <= customerEvidenceReadProjectionLimits.answers
        ? filters.limit
        : 50;
    const result = await projectedApiClient(client).GET('/api/v2/analytics/answers', {
      params: {
        query: {
          project_pub_id: projectPubId,
          ...(filters.answerPubId ? { answer_pub_id: filters.answerPubId } : {}),
          ...(filters.model ? { model: filters.model } : {}),
          ...(filters.region ? { region: filters.region } : {}),
          ...(filters.mode ? { mode: filters.mode } : {}),
          ...(filters.cursor ? { cursor: filters.cursor } : {}),
          limit,
        },
        header: headers,
      },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectAnalyticsAnswerPageBoundary(result.data, projectPubId, limit);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getAnalyticsAnswerRelations(
  answerPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<AnalyticsAnswerRelationsProjection>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/analytics/answers/{answer_pub_id}/relations',
      {
        params: {
          path: { answer_pub_id: answerPubId },
          header: headers,
        },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectAnalyticsAnswerRelationsBoundary(result.data, answerPubId);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getAnalyticsDelta(
  projectPubId: string,
  start: string,
  end: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<AnalyticsDeltaProjection>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/analytics/delta', {
      params: {
        query: { project_pub_id: projectPubId, start, end },
        header: headers,
      },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectAnalyticsDeltaBoundary(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getAnalyticsCompetitors(
  projectPubId: string,
  start: string,
  end: string,
  filters: { model?: string; region?: string; mode?: string },
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<AnalyticsCompetitorProjection>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/analytics/competitors', {
      params: {
        query: {
          project_pub_id: projectPubId,
          start,
          end,
          ...(filters.model ? { model: filters.model } : {}),
          ...(filters.region ? { region: filters.region } : {}),
          ...(filters.mode ? { mode: filters.mode } : {}),
        },
        header: headers,
      },
    });
    return result.data
      ? {
          kind: 'ready',
          data: projectBoundedCollection(
            result.data,
            customerAnalyticsProjectionLimits.competitors,
            projectAnalyticsCompetitorBoundary,
          ),
        }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createMetricExport(
  body: MetricExportCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<MetricExportSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST('/api/v2/exports/metrics', {
      params: { header: headers },
      body,
    });
    if (!result.data) {
      return classifyResourceFailure(result.response.status);
    }
    const response = result.data as MetricExportCreateResponse;
    const exportPubId = safeBrowserString(response.export_pub_id, 120);
    const evidencePubId = safeBrowserString(response.evidence_pub_id, 120);
    const rowCount = safeCount(response.row_count);
    const filterHash = safeHash(response.filter_hash);
    const factSnapshotHash = safeHash(response.fact_snapshot_hash);
    const metricVersion = safeBrowserString(response.metric_version, 120);
    const scorerVersion = safeBrowserString(response.scorer_version, 120);
    return exportPubId &&
      /^exp_[A-Za-z0-9_-]{1,116}$/.test(exportPubId) &&
      evidencePubId &&
      /^evd_[A-Za-z0-9_-]{1,116}$/.test(evidencePubId) &&
      response.format === 'xlsx' &&
      rowCount !== null &&
      rowCount > 0 &&
      filterHash &&
      factSnapshotHash &&
      metricVersion &&
      scorerVersion
      ? {
          kind: 'ready',
          data: {
            exportPubId,
            evidencePubId,
            format: 'xlsx',
            rowCount,
            filterHash,
            factSnapshotHash,
            metricVersion,
            scorerVersion,
          },
        }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

const browserSecretValue =
  /(?:bearer\s+|session\s*=|cookie(?:\s|=|:)|token(?:\s|=|:)|otp(?:\s|=|:)|password(?:\s|=|:)|proxy(?:[_ -]?password)?(?:\s|=|:)|profile(?:s|[_ /-]?(?:path|dir|directory))?(?:\s|=|:|\\|\/)|biometric|dlp-canary|(?:^|[^\w])\d{6}(?:[^\w]|$)|(?:^|[^\w])\d{3}[\s.-]\d{3}(?:[^\w]|$)|1[3-9]\d{9}|1[3-9](?:[\s().-]?\d){9}|(?:[A-Za-z]:\\|\\\\)[^\r\n]{0,1024}(?:profiles?|user[ _-]?data)(?:\\[^\r\n]{0,1024})?|\/[^\s]*profile(?:s?\/[^\s]*)?)/i;
const browserSecretKey =
  /cookie|authorization|token|otp|password|phone|profile|biometric|storage.?state|qr/i;
const clientSecretInvisiblePattern = /[\u200b-\u200d\u2060\ufeff]/g;
const normalizeClientSecretCandidate = (value: string): string => {
  let normalized = value.normalize('NFKC').replace(clientSecretInvisiblePattern, '');
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const decoded = decodeURIComponent(normalized);
      if (decoded === normalized) break;
      normalized = decoded.normalize('NFKC').replace(clientSecretInvisiblePattern, '');
    } catch {
      break;
    }
  }
  return normalized;
};
const containsBrowserSecretValue = (value: string): boolean =>
  browserSecretValue.test(normalizeClientSecretCandidate(value));
const containsBrowserSecretKey = (value: string): boolean =>
  browserSecretKey.test(normalizeClientSecretCandidate(value));
const safeBrowserString = (value: unknown, maxLength: number): string | null =>
  typeof value === 'string' &&
  value.length > 0 &&
  value.length <= maxLength &&
  !containsBrowserSecretValue(value)
    ? value
    : null;
export const isReportVersionPubId = (value: unknown): value is string => {
  const projected = safeBrowserString(value, 120);
  return (
    projected !== null && /^(?:rptv_[A-Za-z0-9_-]{1,115}|rpv_[A-Za-z0-9_-]{1,116})$/.test(projected)
  );
};
const safePage = (value: Record<string, unknown>) => {
  const nextCursor = safeBrowserString(value.next_cursor, 512);
  const hasMore = value.has_more === true && nextCursor !== null;
  return {
    next_cursor: hasMore ? nextCursor : null,
    has_more: hasMore,
  };
};
const safeHash = (value: unknown): string | null => {
  const projected = safeBrowserString(value, 64);
  return projected && /^[0-9a-f]{64}$/.test(projected) ? projected : null;
};
const safeCount = (value: unknown): number | null =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;
const safeUnitDecimal = (value: unknown): string | null => {
  const projected = safeBrowserString(value, 80);
  if (!projected || !/^(?:0(?:\.\d+)?|1(?:\.0+)?)$/.test(projected)) return null;
  const parsed = Number(projected);
  return Number.isFinite(parsed) && parsed >= 0 && parsed <= 1 ? projected : null;
};
const strictIsoTimestampPattern =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-](\d{2}):(\d{2}))$/;

/**
 * Projects only a complete, timezone-qualified ISO timestamp.
 *
 * Date.parse accepts ambiguous values such as "1" as a date in 2001 and normalizes
 * impossible calendar dates. Browser-facing projections must reject both behaviours.
 */
export const projectSafeIsoTimestamp = (value: unknown): string | null => {
  const projected = safeBrowserString(value, 80);
  if (!projected) return null;
  const match = strictIsoTimestampPattern.exec(projected);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = match[8] === undefined ? 0 : Number(match[8]);
  const offsetMinute = match[9] === undefined ? 0 : Number(match[9]);
  const isLeapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, isLeapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > daysInMonth[month - 1]! ||
    hour > 23 ||
    minute > 59 ||
    second > 59 ||
    offsetHour > 23 ||
    offsetMinute > 59 ||
    !Number.isFinite(Date.parse(projected))
  ) {
    return null;
  }
  return projected;
};
const safeTimestamp = projectSafeIsoTimestamp;

const isBrowserRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** Narrows an untrusted value to a known enum member without widening the projected type. */
const safeBrowserEnum = <const Values extends readonly string[]>(
  value: unknown,
  allowed: Values,
): Values[number] | null =>
  typeof value === 'string' && (allowed as readonly string[]).includes(value)
    ? (value as Values[number])
    : null;

const customerDashboardForbiddenOperationalFields = new Set([
  'totaltasks',
  'completedtasks',
  'failedtasks',
  'successrate',
  'tasksuccessrate',
  'collectionsuccessrate',
  'attemptcount',
  'workflowid',
  'temporalrunid',
  'browserinstance',
  'platformaccount',
  'accountpubid',
  'errorcode',
]);

const customerDashboardContainsOperationalField = (value: unknown): boolean => {
  const pending: unknown[] = [value];
  let visited = 0;
  while (pending.length) {
    const candidate = pending.pop();
    visited += 1;
    if (visited > 250_000) return true;
    if (Array.isArray(candidate)) {
      pending.push(...candidate);
      continue;
    }
    if (!isBrowserRecord(candidate)) continue;
    for (const [key, child] of Object.entries(candidate)) {
      const normalized = key.toLowerCase().replace(/[^a-z0-9]/gu, '');
      if (customerDashboardForbiddenOperationalFields.has(normalized)) return true;
      pending.push(child);
    }
  }
  return false;
};

const safeCustomerDashboardDate = (value: unknown): string | null => {
  const candidate = safeBrowserString(value, 10);
  return candidate && projectSafeIsoTimestamp(`${candidate}T00:00:00Z`) ? candidate : null;
};

const safeCustomerQueryPubId = (value: unknown): string | null =>
  typeof value === 'string' &&
  value.length <= 120 &&
  /^qry_(?:hash_)?[A-Za-z0-9_-]{1,116}$/u.test(value)
    ? value
    : null;

const safeCustomerSourceHost = (value: unknown): string | null => {
  if (typeof value !== 'string' || value.length === 0 || value.length > 255) return null;
  const normalized = value.toLowerCase().replace(/\.$/u, '');
  if (normalized !== value || /[\s/:?#]/u.test(normalized)) return null;
  const labels = normalized.split('.');
  if (
    labels.length < 2 ||
    labels.some(
      (label) =>
        label.length === 0 ||
        label.length > 63 ||
        !/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/u.test(label),
    )
  ) {
    return null;
  }
  if (labels.length === 4 && labels.every((label) => /^\d{1,3}$/u.test(label))) {
    return labels.every((label) => Number(label) <= 255) ? normalized : null;
  }
  return normalized;
};

const projectCustomerMetricBoundary = (value: unknown): CustomerMetricProjection | null => {
  if (!isBrowserRecord(value)) return null;
  const code = safeBrowserString(value.code, 80);
  const label = safeBrowserString(value.label, 120);
  const group = safeBrowserString(value.group, 40);
  const format = safeBrowserEnum(value.format, [
    'percentage',
    'score',
    'rank',
    'count',
    'decimal',
  ] as const);
  const direction = safeBrowserEnum(value.direction, ['higher', 'lower', 'neutral'] as const);
  const state = safeBrowserEnum(value.state, ['ready', 'not_ready'] as const);
  const version = safeBrowserString(value.version, 80);
  const metricValue =
    value.value === null
      ? null
      : typeof value.value === 'number' && Number.isFinite(value.value)
        ? value.value
        : undefined;
  if (
    !code ||
    !/^[a-z][a-z0-9_]{0,79}$/u.test(code) ||
    !label ||
    !group ||
    !format ||
    !direction ||
    !state ||
    !version ||
    metricValue === undefined ||
    (state === 'not_ready' && metricValue !== null) ||
    (state === 'ready' && metricValue === null)
  ) {
    return null;
  }
  if (metricValue !== null) {
    const inDomain =
      format === 'percentage'
        ? metricValue >= 0 && metricValue <= 1
        : format === 'score'
          ? metricValue >= 0 && metricValue <= 100
          : format === 'rank'
            ? metricValue >= 1 && metricValue <= 1_000_000
            : format === 'count'
              ? Number.isSafeInteger(metricValue) && metricValue >= 0
              : Math.abs(metricValue) <= 1_000_000_000;
    if (!inDomain) return null;
  }
  return { code, label, group, format, direction, value: metricValue, state, version };
};

const projectCustomerMetricList = (
  value: unknown,
  limit = customerDashboardProjectionLimits.metrics,
): CustomerMetricProjection[] | null => {
  if (!Array.isArray(value) || value.length > limit) return null;
  const projected: CustomerMetricProjection[] = [];
  const codes = new Set<string>();
  for (const candidate of value) {
    const metric = projectCustomerMetricBoundary(candidate);
    if (!metric || codes.has(metric.code)) return null;
    codes.add(metric.code);
    projected.push(metric);
  }
  return projected;
};

const projectCustomerDimensionList = (
  value: unknown,
  limit: number,
): CustomerDimensionProjection[] | null => {
  if (!Array.isArray(value) || value.length > limit) return null;
  const projected: CustomerDimensionProjection[] = [];
  const keys = new Set<string>();
  for (const candidate of value) {
    if (!isBrowserRecord(candidate)) return null;
    const key = safeBrowserString(candidate.key, 160);
    const label = safeBrowserString(candidate.label, 160);
    const metrics = projectCustomerMetricList(candidate.metrics);
    if (!key || !label || !metrics || keys.has(key)) return null;
    keys.add(key);
    projected.push({ key, label, metrics });
  }
  return projected;
};

const projectCustomerCompetitors = (value: unknown): CustomerCompetitorProjection[] | null => {
  if (!Array.isArray(value) || value.length > customerDashboardProjectionLimits.competitors) {
    return null;
  }
  const projected: CustomerCompetitorProjection[] = [];
  const names = new Set<string>();
  for (const candidate of value) {
    if (!isBrowserRecord(candidate)) return null;
    const name = safeBrowserString(candidate.name, 200);
    const metrics = projectCustomerMetricList(candidate.metrics);
    if (!name || !metrics || names.has(name)) return null;
    names.add(name);
    projected.push({ name, metrics });
  }
  return projected;
};

const projectCustomerQuestions = (value: unknown): CustomerQuestionProjection[] | null => {
  if (!Array.isArray(value) || value.length > customerDashboardProjectionLimits.questions) {
    return null;
  }
  const projected: CustomerQuestionProjection[] = [];
  const ids = new Set<string>();
  for (const candidate of value) {
    if (!isBrowserRecord(candidate)) return null;
    const queryPubId = safeCustomerQueryPubId(candidate.query_pub_id);
    const queryText = safeBrowserString(candidate.query_text, 2_000);
    const queryGroup =
      candidate.query_group === null || candidate.query_group === undefined
        ? null
        : safeBrowserString(candidate.query_group, 200);
    const metrics = projectCustomerMetricList(candidate.metrics);
    if (
      !queryPubId ||
      !queryText ||
      (queryGroup === null &&
        candidate.query_group !== null &&
        candidate.query_group !== undefined) ||
      !metrics ||
      ids.has(queryPubId)
    ) {
      return null;
    }
    ids.add(queryPubId);
    projected.push({
      query_pub_id: queryPubId,
      query_text: queryText,
      query_group: queryGroup,
      metrics,
    });
  }
  return projected;
};

const projectCustomerSources = (value: unknown): CustomerSourceProjection[] | null => {
  if (!Array.isArray(value) || value.length > customerDashboardProjectionLimits.sources) {
    return null;
  }
  const projected: CustomerSourceProjection[] = [];
  const hosts = new Set<string>();
  for (const candidate of value) {
    if (!isBrowserRecord(candidate)) return null;
    const host = safeCustomerSourceHost(candidate.host);
    const references = safeCount(candidate.references);
    const answers = safeCount(candidate.answers);
    const share =
      candidate.share === null || candidate.share === undefined
        ? null
        : typeof candidate.share === 'number' &&
            Number.isFinite(candidate.share) &&
            candidate.share >= 0 &&
            candidate.share <= 1
          ? candidate.share
          : undefined;
    if (
      !host ||
      references === null ||
      answers === null ||
      share === undefined ||
      typeof candidate.own_source !== 'boolean' ||
      hosts.has(host)
    ) {
      return null;
    }
    hosts.add(host);
    projected.push({ host, references, share, own_source: candidate.own_source, answers });
  }
  return projected;
};

const projectCustomerTrends = (value: unknown): CustomerTrendProjection[] | null => {
  if (!Array.isArray(value) || value.length > customerDashboardProjectionLimits.trends) return null;
  const projected: CustomerTrendProjection[] = [];
  const dates = new Set<string>();
  for (const candidate of value) {
    if (!isBrowserRecord(candidate)) return null;
    const date = safeCustomerDashboardDate(candidate.date);
    const metrics = projectCustomerMetricList(candidate.metrics);
    if (!date || !metrics || dates.has(date)) return null;
    dates.add(date);
    projected.push({ date, metrics });
  }
  return projected;
};

const projectCustomerWindow = (value: unknown): CustomerDashboardProjection['window'] | null => {
  if (!isBrowserRecord(value) || !isBrowserRecord(value.filters)) return null;
  const start = value.start === null ? null : safeCustomerDashboardDate(value.start);
  const end = value.end === null ? null : safeCustomerDashboardDate(value.end);
  if ((value.start !== null && !start) || (value.end !== null && !end)) return null;
  const filters: Partial<Record<'model' | 'region' | 'mode', string>> = {};
  const entries = Object.entries(value.filters);
  if (entries.length > 3) return null;
  for (const [key, raw] of entries) {
    if (!['model', 'region', 'mode'].includes(key)) return null;
    const projected = safeBrowserString(raw, key === 'mode' ? 80 : 120);
    if (!projected) return null;
    filters[key as 'model' | 'region' | 'mode'] = projected;
  }
  return { start, end, filters };
};

export function projectCustomerDashboardBoundary(
  value: unknown,
  expectedProjectPubId: string,
): CustomerDashboardProjection | null {
  if (
    !isBrowserRecord(value) ||
    customerDashboardContainsOperationalField(value) ||
    value.schema_version !== 'customer-dashboard-v1' ||
    value.metric_version !== 'customer-metrics-v1' ||
    value.project_pub_id !== expectedProjectPubId ||
    !/^prj_[A-Za-z0-9_-]{1,116}$/u.test(expectedProjectPubId)
  ) {
    return null;
  }
  const brandName = safeBrowserString(value.brand_name, 200);
  const state = safeBrowserEnum(value.state, ['ready', 'building'] as const);
  const generatedAt = projectSafeIsoTimestamp(value.generated_at);
  const asOf = value.as_of === null ? null : projectSafeIsoTimestamp(value.as_of);
  const window = projectCustomerWindow(value.window);
  const metrics = projectCustomerMetricList(value.metrics);
  const models = projectCustomerDimensionList(
    value.models,
    customerDashboardProjectionLimits.models,
  );
  const competitors = projectCustomerCompetitors(value.competitors);
  const questions = projectCustomerQuestions(value.questions);
  const sources = projectCustomerSources(value.sources);
  const regions = projectCustomerDimensionList(
    value.regions,
    customerDashboardProjectionLimits.regions,
  );
  const modes = projectCustomerDimensionList(value.modes, customerDashboardProjectionLimits.modes);
  const trends = projectCustomerTrends(value.trends);
  const snapshotHash = safeHash(value.snapshot_hash);
  if (
    !brandName ||
    !state ||
    !generatedAt ||
    (value.as_of !== null && !asOf) ||
    !window ||
    !metrics ||
    !models ||
    !competitors ||
    !questions ||
    !sources ||
    !regions ||
    !modes ||
    !trends ||
    !snapshotHash ||
    !isBrowserRecord(value.risk) ||
    !isBrowserRecord(value.source_audit)
  ) {
    return null;
  }
  const riskMetrics = projectCustomerMetricList(value.risk.metrics);
  const riskModels = projectCustomerDimensionList(
    value.risk.by_model,
    customerDashboardProjectionLimits.riskModels,
  );
  const sourceAuditMetrics = projectCustomerMetricList(value.source_audit.metrics);
  if (
    !riskMetrics ||
    !riskModels ||
    !sourceAuditMetrics ||
    !isBrowserRecord(value.source_audit.verdicts)
  ) {
    return null;
  }
  const verdicts: Record<string, number> = {};
  if (Object.keys(value.source_audit.verdicts).length > 20) return null;
  for (const [key, count] of Object.entries(value.source_audit.verdicts)) {
    const safeKey = safeBrowserString(key, 40);
    const safeValue = safeCount(count);
    if (!safeKey || safeValue === null) return null;
    verdicts[safeKey] = safeValue;
  }
  return {
    schema_version: 'customer-dashboard-v1',
    metric_version: 'customer-metrics-v1',
    project_pub_id: expectedProjectPubId,
    brand_name: brandName,
    state,
    generated_at: generatedAt,
    as_of: asOf,
    window,
    metrics,
    models,
    competitors,
    questions,
    sources,
    regions,
    modes,
    trends,
    risk: { metrics: riskMetrics, by_model: riskModels },
    source_audit: { metrics: sourceAuditMetrics, verdicts },
    snapshot_hash: snapshotHash,
  };
}

export function projectCustomerMetricCatalogBoundary(
  value: unknown,
): CustomerMetricCatalogProjection | null {
  if (
    !isBrowserRecord(value) ||
    value.schema_version !== 'customer-metric-catalog-v1' ||
    !Array.isArray(value.metrics) ||
    value.metrics.length > customerDashboardProjectionLimits.metricCatalog ||
    customerDashboardContainsOperationalField(value)
  ) {
    return null;
  }
  const metrics: CustomerMetricSpecProjection[] = [];
  const codes = new Set<string>();
  for (const candidate of value.metrics) {
    if (!isBrowserRecord(candidate)) return null;
    const code = safeBrowserString(candidate.code, 80);
    const label = safeBrowserString(candidate.label, 120);
    const group = safeBrowserString(candidate.group, 40);
    const format = safeBrowserEnum(candidate.format, [
      'percentage',
      'score',
      'rank',
      'count',
      'decimal',
    ] as const);
    const direction = safeBrowserEnum(candidate.direction, ['higher', 'lower', 'neutral'] as const);
    const description = safeBrowserString(candidate.description, 500);
    const version = safeBrowserString(candidate.version, 80);
    if (
      !code ||
      !/^[a-z][a-z0-9_]{0,79}$/u.test(code) ||
      !label ||
      !group ||
      !format ||
      !direction ||
      !description ||
      !version ||
      codes.has(code)
    ) {
      return null;
    }
    codes.add(code);
    metrics.push({ code, label, group, format, direction, description, version });
  }
  return { schema_version: 'customer-metric-catalog-v1', metrics };
}

export function projectHealthBoundary(value: unknown): HealthProjection | null {
  return isBrowserRecord(value) && value.status === 'ok' ? { status: 'ok' } : null;
}

export function projectIdentitySessionBoundary(value: unknown): IdentitySessionProjection | null {
  if (!isBrowserRecord(value)) return null;
  const tenantPubId = safeBrowserString(value.tenant_pub_id, 120);
  const userPubId = safeBrowserString(value.user_pub_id, 120);
  const role = safeBrowserEnum(value.role, [
    'customer',
    'operator',
    'analyst',
    'reviewer',
    'admin',
    'worker',
  ] as const);
  return tenantPubId &&
    /^tnt_[A-Za-z0-9_-]{1,116}$/.test(tenantPubId) &&
    userPubId &&
    /^usr_[A-Za-z0-9_-]{1,116}$/.test(userPubId) &&
    role
    ? {
        tenant_pub_id: tenantPubId,
        user_pub_id: userPubId,
        role,
        // Product role-gating uses the validated role. Fine-grained permission claims are
        // deliberately not retained in the browser bootstrap or Query cache.
        permissions: [],
      }
    : null;
}

const projectIdentityProjectSummaryBoundary = (
  value: unknown,
  expectedTenantPubId: string,
): ProjectSummary | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeBrowserString(value.pub_id, 120);
  const tenantPubId = safeBrowserString(value.tenant_pub_id, 120);
  const safeName = safeBrowserString(value.name, 120);
  const createdAt = safeTimestamp(value.created_at);
  const updatedAt = safeTimestamp(value.updated_at);
  const state = safeBrowserEnum(value.state, ['draft', 'active', 'paused', 'archived'] as const);
  return pubId &&
    /^prj_[A-Za-z0-9_-]{1,116}$/.test(pubId) &&
    tenantPubId === expectedTenantPubId &&
    createdAt &&
    updatedAt &&
    Date.parse(updatedAt) >= Date.parse(createdAt) &&
    state
    ? {
        pub_id: pubId,
        tenant_pub_id: tenantPubId,
        name: safeName ?? '未命名项目',
        state,
        created_at: createdAt,
        updated_at: updatedAt,
      }
    : null;
};

export function projectIdentityProjectPageBoundary(
  value: unknown,
  expectedTenantPubId: string,
): IdentityProjectPageProjection | null {
  if (
    !isBrowserRecord(value) ||
    !Array.isArray(value.data) ||
    !isBrowserRecord(value.page) ||
    typeof value.page.has_more !== 'boolean'
  ) {
    return null;
  }
  const rawCursor = value.page.next_cursor;
  const nextCursor = rawCursor === null ? null : safeBrowserString(rawCursor, 512);
  if (
    (value.page.has_more && nextCursor === null) ||
    (!value.page.has_more && rawCursor !== null)
  ) {
    return null;
  }
  const projected = projectBoundedUniqueCollection(
    value.data,
    identityReadProjectionLimits.projects,
    (project) => projectIdentityProjectSummaryBoundary(project, expectedTenantPubId),
    (project) => project.pub_id,
  );
  return {
    ...projected,
    page: {
      next_cursor: value.page.has_more ? nextCursor : null,
      has_more: value.page.has_more,
    },
  };
}

const projectAnalyticsOverviewBoundary = (value: unknown): AnalyticsOverviewMetric | null => {
  if (!isBrowserRecord(value)) return null;
  const metric = safeBrowserString(value.metric, 80);
  const metricValue =
    value.value === null
      ? null
      : typeof value.value === 'number' && Number.isFinite(value.value)
        ? value.value
        : undefined;
  const numerator =
    value.numerator === null
      ? null
      : typeof value.numerator === 'number' &&
          Number.isSafeInteger(value.numerator) &&
          value.numerator >= 0
        ? value.numerator
        : undefined;
  const denominator = safeCount(value.denominator);
  const state = safeBrowserString(value.state, 30);
  const metricVersion = safeBrowserString(value.metric_version, 120);
  const scorerVersion = safeBrowserString(value.scorer_version, 120);
  const filterHash = safeBrowserString(value.filter_hash, 160);
  return metric &&
    metricValue !== undefined &&
    numerator !== undefined &&
    denominator !== null &&
    state &&
    metricVersion &&
    scorerVersion &&
    filterHash
    ? {
        metric,
        value: metricValue,
        numerator,
        denominator,
        state,
        metric_version: metricVersion,
        scorer_version: scorerVersion,
        filter_hash: filterHash,
      }
    : null;
};

const projectNullableAnalyticsText = (
  value: unknown,
  maxLength: number,
): string | null | undefined =>
  value === null || value === undefined ? null : (safeBrowserString(value, maxLength) ?? undefined);

const projectNullableAnalyticsNumber = (value: unknown): number | null | undefined =>
  value === null ? null : typeof value === 'number' && Number.isFinite(value) ? value : undefined;

const projectAnalyticsBreakdownBoundary = (
  value: unknown,
  expectedGroupBy: AnalyticsBreakdownGroup,
): AnalyticsBreakdownResponse[number] | null => {
  if (!isBrowserRecord(value) || value.group_by !== expectedGroupBy) return null;
  const day = projectNullableAnalyticsText(value.day, 10);
  const model = projectNullableAnalyticsText(value.model, 120);
  const region = projectNullableAnalyticsText(value.region, 120);
  const mode = projectNullableAnalyticsText(value.mode, 80);
  const questionPubId = projectNullableAnalyticsText(value.question_pub_id, 120);
  const questionText = projectNullableAnalyticsText(value.question_text, 500);
  const answerCount = safeCount(value.answer_count);
  const mentionedCount = safeCount(value.mentioned_count);
  const mentionRate = projectNullableAnalyticsNumber(value.mention_rate);
  const averageRank = projectNullableAnalyticsNumber(value.average_rank);
  const citationCoverage = projectNullableAnalyticsNumber(value.citation_coverage);
  return day !== undefined &&
    model !== undefined &&
    region !== undefined &&
    mode !== undefined &&
    questionPubId !== undefined &&
    questionText !== undefined &&
    answerCount !== null &&
    mentionedCount !== null &&
    mentionRate !== undefined &&
    averageRank !== undefined &&
    citationCoverage !== undefined
    ? {
        group_by: expectedGroupBy,
        day,
        model,
        region,
        mode,
        question_pub_id: questionPubId,
        question_text: questionText,
        answer_count: answerCount,
        mentioned_count: mentionedCount,
        mention_rate: mentionRate,
        average_rank: averageRank,
        citation_coverage: citationCoverage,
      }
    : null;
};

const projectAnalyticsDeltaBoundary = (value: unknown): AnalyticsDeltaProjection | null => {
  if (!isBrowserRecord(value)) return null;
  const projected: AnalyticsDeltaSafeResponse = {};
  let total = 0;
  let invalid = false;
  for (const metric of [
    'mention_rate',
    'average_rank',
    'top3_rate',
    'citation_coverage',
  ] as const) {
    if (!(metric in value)) continue;
    total += 1;
    const candidate = value[metric];
    if (!isBrowserRecord(candidate)) {
      invalid = true;
      continue;
    }
    const safeMetricValue = (input: unknown): number | null | undefined =>
      input === null
        ? null
        : typeof input === 'number' && Number.isFinite(input) && Math.abs(input) <= 1_000_000
          ? input
          : undefined;
    const current = safeMetricValue(candidate.current);
    const previous = safeMetricValue(candidate.previous);
    const delta = safeMetricValue(candidate.delta);
    if (current === undefined || previous === undefined || delta === undefined) {
      invalid = true;
      continue;
    }
    projected[metric] = {
      current,
      previous,
      delta,
    };
  }
  const shown = Object.keys(projected).length;
  return {
    data: projected,
    projection: {
      total,
      shown,
      invalid: invalid || shown < total,
    },
  };
};

const projectAnalyticsCompetitorBoundary = (value: unknown): AnalyticsCompetitorSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const competitor = safeBrowserString(value.competitor, 160);
  const mentionRate =
    typeof value.mention_rate === 'number' && Number.isFinite(value.mention_rate)
      ? value.mention_rate
      : null;
  const mentionCount = safeCount(value.mention_count);
  const answerCount = safeCount(value.answer_count);
  return competitor && mentionRate !== null && mentionCount !== null && answerCount !== null
    ? {
        competitor,
        mention_rate: mentionRate,
        mention_count: mentionCount,
        answer_count: answerCount,
      }
    : null;
};

const projectAnalyticsPubId = (value: unknown, prefix: string): string | null => {
  const projected = safeBrowserString(value, 120);
  return projected &&
    projected.startsWith(prefix) &&
    projected.length > prefix.length &&
    /^[A-Za-z0-9_-]+$/.test(projected)
    ? projected
    : null;
};

const projectNullableAnalyticsPubId = (
  value: unknown,
  prefix: string,
): string | null | undefined =>
  value === null ? null : (projectAnalyticsPubId(value, prefix) ?? undefined);

const projectAnalyticsAnswerBoundary = (
  value: unknown,
  expectedProjectPubId: string,
): AnalyticsAnswerSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'ans_');
  const projectPubId = projectAnalyticsPubId(value.project_pub_id, 'prj_');
  const runPubId = projectNullableAnalyticsPubId(value.run_pub_id, 'run_');
  const configVersionPubId = projectNullableAnalyticsPubId(value.config_version_pub_id, 'cfv_');
  const queryPubId = projectNullableAnalyticsPubId(value.query_pub_id, 'qry_');
  const queryText =
    value.query_text === null ? null : (safeBrowserString(value.query_text, 500) ?? undefined);
  const responseText = safeBrowserString(value.response_text, 4_000);
  const model = safeBrowserString(value.model, 120);
  const region = safeBrowserString(value.region, 120);
  const mode = safeBrowserString(value.mode, 80);
  const captureTime = projectSafeIsoTimestamp(value.capture_time);
  const mentioned =
    value.mentioned === null || typeof value.mentioned === 'boolean' ? value.mentioned : undefined;
  const rank =
    value.rank === null
      ? null
      : typeof value.rank === 'number' && Number.isFinite(value.rank) && value.rank >= 1
        ? value.rank
        : undefined;
  const sentiment =
    value.sentiment === null ? null : (safeBrowserString(value.sentiment, 80) ?? undefined);
  const recommendationState =
    value.recommendation_state === null
      ? null
      : (safeBrowserString(value.recommendation_state, 80) ?? undefined);
  const citationCount = safeCount(value.citation_count);
  return pubId &&
    projectPubId === expectedProjectPubId &&
    runPubId !== undefined &&
    configVersionPubId !== undefined &&
    queryPubId !== undefined &&
    queryText !== undefined &&
    (queryPubId !== null || queryText !== null) &&
    responseText &&
    model &&
    region &&
    mode &&
    typeof value.eligible === 'boolean' &&
    typeof value.degraded === 'boolean' &&
    captureTime &&
    mentioned !== undefined &&
    rank !== undefined &&
    sentiment !== undefined &&
    recommendationState !== undefined &&
    citationCount !== null
    ? {
        pub_id: pubId,
        project_pub_id: projectPubId,
        run_pub_id: runPubId,
        config_version_pub_id: configVersionPubId,
        query_pub_id: queryPubId,
        query_text: queryText,
        response_text: responseText,
        model,
        region,
        mode,
        eligible: value.eligible,
        degraded: value.degraded,
        capture_time: captureTime,
        mentioned,
        rank,
        sentiment,
        recommendation_state: recommendationState,
        citation_count: citationCount,
      }
    : null;
};

const projectAnalyticsAnswerPageBoundary = (
  value: unknown,
  expectedProjectPubId: string,
  limit: number,
): AnalyticsAnswerProjection | null => {
  if (!isBrowserRecord(value) || !Array.isArray(value.data) || !isBrowserRecord(value.page)) {
    return null;
  }
  const collection = projectBoundedCollection(value.data, limit, (candidate) =>
    projectAnalyticsAnswerBoundary(candidate, expectedProjectPubId),
  );
  const nextCursor = projectAnalyticsPubId(value.page.next_cursor, 'ans_');
  const hasMore = value.page.has_more === true && nextCursor !== null;
  const pageIsValid =
    typeof value.page.has_more === 'boolean' &&
    ((value.page.has_more === true && nextCursor !== null) ||
      (value.page.has_more === false && value.page.next_cursor === null));
  return {
    data: collection.data,
    page: {
      next_cursor: hasMore ? nextCursor : null,
      has_more: hasMore,
    },
    projection: {
      ...collection.projection,
      invalid: collection.projection.invalid || !pageIsValid,
    },
  };
};

const projectSafeRelationUrl = (value: unknown): string | null => {
  const candidate = safeBrowserString(value, 2_000);
  if (!candidate) return null;
  try {
    const parsed = new URL(candidate);
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') &&
      !parsed.username &&
      !parsed.password &&
      parsed.hostname.length > 0 &&
      parsed.hostname.length <= 253
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
};

const projectAnalyticsCitationBoundary = (value: unknown): AnalyticsCitationSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'cit_');
  const canonicalUrl = projectSafeRelationUrl(value.canonical_url);
  const ordinal = safeCount(value.ordinal);
  const title = value.title === null ? null : (safeBrowserString(value.title, 300) ?? undefined);
  const citedText =
    value.cited_text === null ? null : (safeBrowserString(value.cited_text, 2_000) ?? undefined);
  const contentHash =
    value.content_hash === null ? null : (safeHash(value.content_hash) ?? undefined);
  if (
    !pubId ||
    !canonicalUrl ||
    ordinal === null ||
    ordinal <= 0 ||
    title === undefined ||
    citedText === undefined ||
    typeof value.own_source !== 'boolean' ||
    contentHash === undefined
  ) {
    return null;
  }
  return {
    pub_id: pubId,
    ordinal,
    canonical_url: canonicalUrl,
    host: new URL(canonicalUrl).hostname,
    title,
    cited_text: citedText,
    own_source: value.own_source,
    content_hash: contentHash,
  };
};

const projectAnalyticsAnchorBoundary = (value: unknown): AnalyticsAnchorSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'anch_');
  const textStart = value.text_start === null ? null : safeCount(value.text_start);
  const textEnd = value.text_end === null ? null : safeCount(value.text_end);
  const pageNumber =
    value.page_number === null
      ? null
      : typeof value.page_number === 'number' &&
          Number.isSafeInteger(value.page_number) &&
          value.page_number > 0
        ? value.page_number
        : undefined;
  const quoteHash = value.quote_hash === null ? null : (safeHash(value.quote_hash) ?? undefined);
  const bbox = (() => {
    if (value.bbox === null) return null;
    if (!isBrowserRecord(value.bbox)) return null;
    const { x, y, width, height, confidence } = value.bbox;
    if (
      typeof x !== 'number' ||
      !Number.isFinite(x) ||
      x < 0 ||
      x > 1_000_000 ||
      typeof y !== 'number' ||
      !Number.isFinite(y) ||
      y < 0 ||
      y > 1_000_000 ||
      typeof width !== 'number' ||
      !Number.isFinite(width) ||
      width <= 0 ||
      width > 1_000_000 ||
      typeof height !== 'number' ||
      !Number.isFinite(height) ||
      height <= 0 ||
      height > 1_000_000 ||
      (confidence !== undefined &&
        (typeof confidence !== 'number' ||
          !Number.isFinite(confidence) ||
          confidence < 0 ||
          confidence > 1))
    ) {
      return null;
    }
    return {
      x,
      y,
      width,
      height,
      ...(typeof confidence === 'number' ? { confidence } : {}),
    };
  })();
  const textStartIsValid = value.text_start === null || textStart !== null;
  const textEndIsValid = value.text_end === null || textEnd !== null;
  return pubId &&
    textStartIsValid &&
    textEndIsValid &&
    pageNumber !== undefined &&
    quoteHash !== undefined
    ? {
        pub_id: pubId,
        text_start: textStart,
        text_end: textEnd,
        bbox,
        page_number: pageNumber,
        quote_hash: quoteHash,
      }
    : null;
};

const projectAnalyticsEvidenceBoundary = (
  value: unknown,
  anchorProjection: ProjectedCollection<never>['projection'],
): AnalyticsEvidenceSafeView | null => {
  if (!isBrowserRecord(value) || !Array.isArray(value.anchors)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'evd_');
  const relationType = safeBrowserString(value.relation_type, 80);
  const kind = safeBrowserString(value.kind, 80);
  const accessClass = safeBrowserString(value.access_class, 80);
  const sha256 = safeHash(value.sha256);
  const mimeType = safeBrowserString(value.mime_type, 120);
  const byteSize = safeCount(value.byte_size);
  const sourceUrl =
    value.source_url === null ? null : (projectSafeRelationUrl(value.source_url) ?? undefined);
  const captureTime = projectSafeIsoTimestamp(value.capture_time);
  if (
    !pubId ||
    !relationType ||
    !kind ||
    !accessClass ||
    !sha256 ||
    !mimeType ||
    byteSize === null ||
    sourceUrl === undefined ||
    !captureTime
  ) {
    return null;
  }
  const anchors = projectBoundedCollection(
    value.anchors,
    customerEvidenceReadProjectionLimits.anchors,
    projectAnalyticsAnchorBoundary,
  );
  anchorProjection.total += anchors.projection.total;
  anchorProjection.shown += anchors.projection.shown;
  anchorProjection.invalid ||= anchors.projection.invalid;
  return {
    pub_id: pubId,
    relation_type: relationType,
    kind,
    access_class: accessClass,
    sha256,
    mime_type: mimeType,
    byte_size: byteSize,
    source_url: sourceUrl,
    capture_time: captureTime,
    anchors: anchors.data,
  };
};

const projectAnalyticsHistoryBoundary = (value: unknown): AnalyticsHistorySafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'diff_');
  const beforeEvidencePubId = projectAnalyticsPubId(value.before_evidence_pub_id, 'evd_');
  const afterEvidencePubId = projectAnalyticsPubId(value.after_evidence_pub_id, 'evd_');
  const similarity =
    value.similarity === null
      ? null
      : typeof value.similarity === 'number' && Number.isFinite(value.similarity)
        ? value.similarity
        : undefined;
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId &&
    beforeEvidencePubId &&
    afterEvidencePubId &&
    similarity !== undefined &&
    typeof value.visual_diff_available === 'boolean' &&
    createdAt
    ? {
        pub_id: pubId,
        before_evidence_pub_id: beforeEvidencePubId,
        after_evidence_pub_id: afterEvidencePubId,
        similarity,
        visual_diff_available: value.visual_diff_available,
        created_at: createdAt,
      }
    : null;
};

const projectBoundedTailCollection = <Source, Projected>(
  values: Source[],
  limit: number,
  projector: (value: Source) => Projected | null,
): ProjectedCollection<Projected> => {
  const bounded = values.slice(-limit);
  const data = bounded.flatMap((value) => {
    const projected = projector(value);
    return projected ? [projected] : [];
  });
  return {
    data,
    projection: {
      total: values.length,
      shown: data.length,
      invalid: data.length !== bounded.length,
    },
  };
};

const projectAnalyticsAnswerRelationsBoundary = (
  value: unknown,
  expectedAnswerPubId: string,
): AnalyticsAnswerRelationsProjection | null => {
  if (
    !isBrowserRecord(value) ||
    projectAnalyticsPubId(value.answer_pub_id, 'ans_') !== expectedAnswerPubId ||
    !Array.isArray(value.citations) ||
    !Array.isArray(value.evidence) ||
    !Array.isArray(value.history)
  ) {
    return null;
  }
  const citations = projectBoundedCollection(
    value.citations,
    customerEvidenceReadProjectionLimits.citations,
    projectAnalyticsCitationBoundary,
  );
  const anchorProjection = { total: 0, shown: 0, invalid: false };
  const evidence = projectBoundedCollection(
    value.evidence,
    customerEvidenceReadProjectionLimits.evidence,
    (candidate) => projectAnalyticsEvidenceBoundary(candidate, anchorProjection),
  );
  const history = projectBoundedTailCollection(
    value.history,
    customerEvidenceReadProjectionLimits.history,
    projectAnalyticsHistoryBoundary,
  );
  const answerCitations = citations.data;
  const brandMentionEvidence = evidence.data.filter(
    (item) =>
      item.relation_type === 'brand_mention_source_snapshot' &&
      item.kind === 'source_screenshot' &&
      item.anchors.some((anchor) => anchor.bbox !== null),
  );
  const openedSourcePreviews = evidence.data.filter(
    (item) =>
      item.relation_type === 'ai_opened_source_preview' && item.kind === 'source_screenshot',
  );
  const exactIds = (raw: unknown, expected: { pub_id: string }[], prefix: string): boolean => {
    if (raw === undefined) return true; // compatibility with a pre-taxonomy API during rolling deploy
    if (!Array.isArray(raw)) return false;
    const ids = raw.map((item) =>
      isBrowserRecord(item) ? projectAnalyticsPubId(item.pub_id, prefix) : null,
    );
    return (
      ids.every((id): id is string => id !== null) &&
      ids.length === expected.length &&
      ids.every((id, index) => id === expected[index]?.pub_id)
    );
  };
  if (
    !exactIds(value.answer_citations, answerCitations, 'cit_') ||
    !exactIds(value.brand_mention_evidence, brandMentionEvidence, 'evd_') ||
    !exactIds(value.opened_source_previews, openedSourcePreviews, 'evd_')
  ) {
    return null;
  }
  return {
    answer_pub_id: expectedAnswerPubId,
    answer_citations: answerCitations,
    brand_mention_evidence: brandMentionEvidence,
    opened_source_previews: openedSourcePreviews,
    citations: citations.data,
    evidence: evidence.data,
    history: history.data,
    projection: {
      citations: citations.projection,
      evidence: evidence.projection,
      anchors: anchorProjection,
      history: history.projection,
    },
  };
};

const projectSafeHttpsUrl = (value: unknown): string | null => {
  const candidate = safeBrowserString(value, 500);
  if (!candidate) return null;
  try {
    const parsed = new URL(candidate);
    return parsed.protocol === 'https:' && !parsed.username && !parsed.password
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
};

const normalizeGovernanceHistoryLimit = (value: number | undefined): number =>
  typeof value === 'number' &&
  Number.isSafeInteger(value) &&
  value >= 1 &&
  value <= customerGovernanceProjectionLimits.historyVersions
    ? value
    : 20;

const projectGovernanceCursorPage = <T>(
  value: unknown,
  limit: number,
  projector: (candidate: unknown) => T | null,
): ProjectedCursorPage<T> | null => {
  if (!isBrowserRecord(value) || !Array.isArray(value.data)) return null;
  const bounded = value.data.slice(0, limit);
  const data = bounded.flatMap((candidate) => {
    const projected = projector(candidate);
    return projected ? [projected] : [];
  });
  const nextCursor =
    value.next_cursor === null
      ? null
      : typeof value.next_cursor === 'string' && /^[1-9]\d{0,8}$/.test(value.next_cursor)
        ? value.next_cursor
        : null;
  const cursorInvalid = value.next_cursor !== null && nextCursor === null;
  return {
    data,
    next_cursor: cursorInvalid ? null : nextCursor,
    projection: {
      total: value.data.length,
      shown: data.length,
      invalid: cursorInvalid || value.data.length > limit || data.length !== bounded.length,
    },
  };
};

const projectClientProfileBoundaryView = (
  value: unknown,
  expectedProjectPubId: string,
): ClientProfileView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeBrowserString(value.pub_id, 120);
  const projectPubId = safeBrowserString(value.project_pub_id, 120);
  const companyName = safeBrowserString(value.company_name, 160);
  const contactRole = safeBrowserString(value.contact_role, 120);
  const audience = safeBrowserString(value.audience, 1000);
  const publicStatement = safeBrowserString(value.public_statement, 2000);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId &&
    /^cpv_[A-Za-z0-9_-]{1,116}$/.test(pubId) &&
    projectPubId === expectedProjectPubId &&
    /^prj_[A-Za-z0-9_-]{1,116}$/.test(projectPubId) &&
    typeof value.revision === 'number' &&
    Number.isSafeInteger(value.revision) &&
    value.revision >= 1 &&
    companyName &&
    contactRole &&
    audience &&
    publicStatement &&
    createdAt
    ? {
        pub_id: pubId,
        project_pub_id: projectPubId,
        revision: value.revision,
        company_name: companyName,
        contact_role: contactRole,
        audience,
        public_statement: publicStatement,
        created_at: createdAt,
      }
    : null;
};

const projectAssetConfirmationBoundaryView = (
  value: unknown,
  expectedProjectPubId: string,
): AssetConfirmationView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeBrowserString(value.pub_id, 120);
  const projectPubId = safeBrowserString(value.project_pub_id, 120);
  const brandName = safeBrowserString(value.brand_name, 160);
  const website = projectSafeHttpsUrl(value.website);
  const productName = safeBrowserString(value.product_name, 200);
  const competitorName = safeBrowserString(value.competitor_name, 200);
  const prohibitedClaim = safeBrowserString(value.prohibited_claim, 1000);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId &&
    /^acv_[A-Za-z0-9_-]{1,116}$/.test(pubId) &&
    projectPubId === expectedProjectPubId &&
    /^prj_[A-Za-z0-9_-]{1,116}$/.test(projectPubId) &&
    typeof value.revision === 'number' &&
    Number.isSafeInteger(value.revision) &&
    value.revision >= 1 &&
    brandName &&
    website &&
    productName &&
    competitorName &&
    prohibitedClaim &&
    createdAt
    ? {
        pub_id: pubId,
        project_pub_id: projectPubId,
        revision: value.revision,
        brand_name: brandName,
        website,
        product_name: productName,
        competitor_name: competitorName,
        prohibited_claim: prohibitedClaim,
        created_at: createdAt,
      }
    : null;
};

const safeResourcePubId = (value: unknown): string | null => {
  const projected = safeBrowserString(value, 120);
  return projected && /^[A-Za-z][A-Za-z0-9_-]{2,119}$/.test(projected) ? projected : null;
};

const projectResourceData = (
  kind: ProjectResourceKind,
  value: unknown,
): ProjectResourceSafeData | null => {
  if (!isBrowserRecord(value)) return null;
  const safeName = () => safeBrowserString(value.name, 200);
  const optionalWebsite = (): string | null | undefined =>
    value.website === null || value.website === undefined || value.website === ''
      ? null
      : (projectSafeHttpsUrl(value.website) ?? undefined);
  if (kind === 'brands' || kind === 'competitors') {
    const name = safeName();
    const website = optionalWebsite();
    return name && website !== undefined ? { name, website } : null;
  }
  if (kind === 'aliases') {
    const parentPubId = safeResourcePubId(value.parent_pub_id);
    const alias = safeBrowserString(value.value, 500);
    return parentPubId && alias ? { parent_pub_id: parentPubId, value: alias } : null;
  }
  if (kind === 'assets') {
    const parentPubId = safeResourcePubId(value.parent_pub_id);
    const assetKind = safeBrowserString(value.kind, 80);
    const uri = projectSafeHttpsUrl(value.uri);
    const sha256 =
      value.sha256 === null || value.sha256 === undefined
        ? null
        : typeof value.sha256 === 'string' && /^[a-f0-9]{64}$/.test(value.sha256)
          ? value.sha256
          : undefined;
    return parentPubId && assetKind && uri && sha256 !== undefined
      ? { parent_pub_id: parentPubId, kind: assetKind, uri, sha256 }
      : null;
  }
  if (kind === 'query-groups') {
    const name = safeName();
    return name ? { name } : null;
  }
  if (kind === 'query-items') {
    const parentPubId = safeResourcePubId(value.parent_pub_id);
    const text = safeBrowserString(value.text, 5000);
    const priority =
      typeof value.priority === 'number' &&
      Number.isSafeInteger(value.priority) &&
      value.priority >= 0 &&
      value.priority <= 10000
        ? value.priority
        : null;
    return parentPubId && text && priority !== null
      ? { parent_pub_id: parentPubId, text, priority }
      : null;
  }
  if (kind === 'goals') {
    const metric = safeBrowserString(value.metric, 80);
    const state = safeBrowserString(value.state, 30);
    const payload = isBrowserRecord(value.payload) ? value.payload : null;
    const target = payload?.target;
    return metric &&
      ['mention_rate', 'top3_rate', 'citation_coverage'].includes(metric) &&
      state &&
      ['draft', 'active', 'paused', 'achieved'].includes(state) &&
      typeof target === 'number' &&
      Number.isFinite(target) &&
      target >= 0 &&
      target <= 100
      ? { metric, payload: { target }, state }
      : null;
  }
  const changeKind = safeBrowserString(value.kind, 80);
  const state = safeBrowserString(value.state, 30);
  const payload = isBrowserRecord(value.payload) ? value.payload : null;
  if (
    !changeKind ||
    !['add_query', 'pause', 'resume', 'backfill'].includes(changeKind) ||
    !state ||
    !['pending', 'approved', 'rejected', 'applied'].includes(state) ||
    !payload
  ) {
    return null;
  }
  const projectedPayload: ProjectResourceSafePayload = {};
  for (const [key, maxLength] of [
    ['question', 200],
    ['reason', 500],
    ['goal_metric', 80],
    ['priority', 20],
  ] as const) {
    if (payload[key] === undefined) continue;
    const projected = safeBrowserString(payload[key], maxLength);
    if (!projected) return null;
    projectedPayload[key] = projected;
  }
  if (payload.target_percent !== undefined) {
    if (
      typeof payload.target_percent !== 'number' ||
      !Number.isFinite(payload.target_percent) ||
      payload.target_percent < 0 ||
      payload.target_percent > 100
    ) {
      return null;
    }
    projectedPayload.target_percent = payload.target_percent;
  }
  return { kind: changeKind, payload: projectedPayload, state };
};

export function projectProjectResourceView(
  value: unknown,
  expectedProjectPubId: string,
  expectedKind: ProjectResourceKind,
): ProjectResourceView | null {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeResourcePubId(value.pub_id);
  const projectPubId = safeBrowserString(value.project_pub_id, 120);
  const data = projectResourceData(expectedKind, value.data);
  return pubId &&
    projectPubId === expectedProjectPubId &&
    /^prj_[A-Za-z0-9_-]{1,116}$/.test(projectPubId) &&
    value.resource_kind === expectedKind &&
    typeof value.version === 'number' &&
    Number.isSafeInteger(value.version) &&
    value.version >= 1 &&
    data
    ? {
        pub_id: pubId,
        project_pub_id: projectPubId,
        resource_kind: expectedKind,
        version: value.version,
        data,
      }
    : null;
}

const projectResourceWriteMatches = (
  projected: ProjectResourceView,
  kind: ProjectResourceKind,
  body: ProjectResourceWrite,
): boolean => {
  const expected = projectResourceData(kind, body);
  return expected !== null && JSON.stringify(projected.data) === JSON.stringify(expected);
};

const clientProfileWriteMatches = (
  projected: ClientProfileView,
  body: ClientProfileWrite,
): boolean =>
  projected.company_name === body.company_name &&
  projected.contact_role === body.contact_role &&
  projected.audience === body.audience &&
  projected.public_statement === body.public_statement;

const assetConfirmationWriteMatches = (
  projected: AssetConfirmationView,
  body: AssetConfirmationWrite,
): boolean => {
  const website = projectSafeHttpsUrl(body.website);
  return (
    projected.brand_name === body.brand_name &&
    website !== null &&
    projected.website === website &&
    projected.product_name === body.product_name &&
    projected.competitor_name === body.competitor_name &&
    projected.prohibited_claim === body.prohibited_claim
  );
};

export const projectSafeAccountMask = (value: unknown): string | null => {
  const projected = safeBrowserString(value, 120);
  if (!projected || /1[3-9]\d{9}/.test(projected)) return null;
  return /[*＊•·…]/.test(projected) || /(?:尾号|末(?:四|4)位|已隐藏|掩码)/.test(projected)
    ? projected
    : null;
};

export function projectCustomerAccountView(
  value: unknown,
  expectedAccountPubId?: string,
): CustomerAccountView | null {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeBrowserString(value.pub_id, 120);
  const accountMask = projectSafeAccountMask(value.account_mask);
  const platformLabel = safeBrowserString(value.platform_label, 120);
  const ownerLabel = safeBrowserString(value.owner_label, 120);
  const regionLabel = safeBrowserString(value.region_label, 120);
  const authorizationExpiresAt =
    value.authorization_expires_at === null ? null : safeTimestamp(value.authorization_expires_at);
  const lastVerifiedAt =
    value.last_verified_at === null ? null : safeTimestamp(value.last_verified_at);
  const revocationReceiptPubId =
    value.revocation_receipt_pub_id === null
      ? null
      : safeBrowserString(value.revocation_receipt_pub_id, 120);
  const revokedAt = value.revoked_at === null ? null : safeTimestamp(value.revoked_at);
  const rawScopes = Array.isArray(value.scopes) ? value.scopes : null;
  const scopes = (rawScopes ?? []).filter(
    (scope): scope is 'read' | 'query' | 'draft' | 'publish' =>
      scope === 'read' || scope === 'query' || scope === 'draft' || scope === 'publish',
  );
  const custodyMode = safeBrowserEnum(value.custody_mode, [
    'server',
    'customer_device',
    'hybrid',
  ] as const);
  const admissionLevel = safeBrowserEnum(value.admission_level, [
    'catalogued',
    'adapter_ready',
    'login_verified',
    'read_verified',
    'draft_verified',
    'publish_verified',
    'suspended',
  ] as const);
  const sessionHealth = safeBrowserEnum(value.session_health, [
    'healthy',
    'degraded',
    'challenge_required',
    'revoked',
  ] as const);
  const interventionStatus = safeBrowserEnum(value.intervention_status, [
    'none',
    'pending',
    'task_issued',
    'awaiting_platform_probe',
    'paired',
    'refused',
    'rejected',
    'timed_out',
    'expired',
    'failed',
    'completed',
  ] as const);
  const valid =
    pubId !== null &&
    /^pac_[A-Za-z0-9_-]{1,116}$/.test(pubId) &&
    (expectedAccountPubId === undefined || pubId === expectedAccountPubId) &&
    accountMask !== null &&
    platformLabel !== null &&
    ownerLabel !== null &&
    regionLabel !== null &&
    custodyMode !== null &&
    admissionLevel !== null &&
    rawScopes !== null &&
    scopes.length === rawScopes.length &&
    new Set(scopes).size === scopes.length &&
    (value.authorization_expires_at === null || authorizationExpiresAt !== null) &&
    sessionHealth !== null &&
    (value.last_verified_at === null || lastVerifiedAt !== null) &&
    interventionStatus !== null &&
    (value.revocation_receipt_pub_id === null ||
      (revocationReceiptPubId !== null &&
        /^rev_[A-Za-z0-9_-]{1,116}$/.test(revocationReceiptPubId))) &&
    (value.revoked_at === null || revokedAt !== null) &&
    (revocationReceiptPubId === null) === (revokedAt === null) &&
    (revokedAt === null ||
      (sessionHealth === 'revoked' && admissionLevel === 'suspended' && scopes.length === 0));
  return valid
    ? {
        pub_id: pubId,
        account_mask: accountMask,
        platform_label: platformLabel,
        owner_label: ownerLabel,
        custody_mode: custodyMode,
        admission_level: admissionLevel,
        scopes,
        authorization_expires_at: authorizationExpiresAt,
        region_label: regionLabel,
        session_health: sessionHealth,
        last_verified_at: lastVerifiedAt,
        intervention_status: interventionStatus,
        revocation_receipt_pub_id: revocationReceiptPubId,
        revoked_at: revokedAt,
      }
    : null;
}

function projectCustomerAccountWriteView(
  value: unknown,
  expectedAccountPubId?: string,
): CustomerAccountView | null {
  return projectCustomerAccountView(value, expectedAccountPubId);
}

const customerAccountRegistrationMatches = (
  value: CustomerAccountView,
  body: CustomerAccountCreate,
): boolean =>
  value.account_mask === body.account_mask &&
  value.custody_mode === body.custody_mode &&
  value.region_label === body.region &&
  value.scopes.length === 0 &&
  value.authorization_expires_at === null &&
  value.session_health === 'degraded' &&
  value.intervention_status === 'none' &&
  value.revocation_receipt_pub_id === null &&
  value.revoked_at === null;

const customerAuthorizationWriteMatches = (
  value: CustomerAccountView,
  accountPubId: string,
  body: CustomerAuthorizationCreate,
): boolean => {
  const validUntil = safeTimestamp(body.valid_until);
  const expectedScopes = [...new Set(body.scopes)];
  return (
    value.pub_id === accountPubId &&
    validUntil !== null &&
    value.authorization_expires_at !== null &&
    Date.parse(value.authorization_expires_at) === Date.parse(validUntil) &&
    value.scopes.length === expectedScopes.length &&
    expectedScopes.every((scope) => value.scopes.includes(scope)) &&
    value.session_health !== 'revoked' &&
    value.revocation_receipt_pub_id === null &&
    value.revoked_at === null
  );
};

export function projectCustomerPairingView(
  value: unknown,
  expectedAccountPubId: string,
): CustomerPairingView | null {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeBrowserString(value.pub_id, 120);
  const accountPubId = safeBrowserString(value.account_pub_id, 120);
  const accountMask = projectSafeAccountMask(value.account_mask);
  const allowedDomain = safeBrowserString(value.allowed_domain, 255);
  const expiresAt = value.expires_at === null ? null : safeTimestamp(value.expires_at);
  const action = safeBrowserEnum(value.action, ['read', 'query', 'draft', 'publish'] as const);
  const challengeType = safeBrowserEnum(value.challenge_type, [
    'otp',
    'qr',
    'push',
    'passkey',
    'face',
    'graphical',
  ] as const);
  const state = safeBrowserEnum(value.state, [
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
  ] as const);
  const valid =
    pubId !== null &&
    /^int_[A-Za-z0-9_-]{1,116}$/.test(pubId) &&
    accountPubId === expectedAccountPubId &&
    /^pac_[A-Za-z0-9_-]{1,116}$/.test(accountPubId) &&
    accountMask !== null &&
    allowedDomain !== null &&
    allowedDomain === allowedDomain.toLowerCase() &&
    /^(?=.{3,255}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(allowedDomain) &&
    action !== null &&
    challengeType !== null &&
    state !== null &&
    (value.expires_at === null || expiresAt !== null);
  return valid
    ? {
        pub_id: pubId,
        account_pub_id: accountPubId,
        account_mask: accountMask,
        allowed_domain: allowedDomain,
        action,
        challenge_type: challengeType,
        state,
        expires_at: expiresAt,
      }
    : null;
}

function projectCustomerPairingWriteView(
  value: unknown,
  expectedAccountPubId: string,
): CustomerPairingView | null {
  return projectCustomerPairingView(value, expectedAccountPubId);
}

const customerPairingWriteMatches = (
  value: CustomerPairingView,
  body: CustomerPairingCreate,
): boolean =>
  value.allowed_domain === body.allowed_domain.toLowerCase() &&
  value.action === body.action &&
  value.challenge_type === body.challenge_type &&
  value.state === 'pending' &&
  value.expires_at === null;

export function projectResponsibleMemberView(value: unknown): ResponsibleMemberView | null {
  if (!isBrowserRecord(value)) return null;
  const userPubId = safeBrowserString(value.user_pub_id, 120);
  const label = safeBrowserString(value.label, 120);
  const role = safeBrowserEnum(value.role, [
    'customer',
    'operator',
    'analyst',
    'reviewer',
    'admin',
  ] as const);
  const valid =
    userPubId !== null &&
    /^usr_[A-Za-z0-9_-]{1,116}$/.test(userPubId) &&
    label !== null &&
    role !== null;
  return valid ? { user_pub_id: userPubId, label, role } : null;
}

export function projectCustomerEventView(value: unknown): CustomerEventView | null {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeBrowserString(value.pub_id, 120);
  const eventType = safeBrowserString(value.event_type, 120);
  const occurredAt = safeTimestamp(value.occurred_at);
  const valid =
    pubId !== null &&
    /^sev_[A-Za-z0-9_-]{1,116}$/.test(pubId) &&
    eventType !== null &&
    /^[a-z][a-z0-9_.-]{2,119}$/.test(eventType) &&
    occurredAt !== null;
  return valid ? { pub_id: pubId, event_type: eventType, occurred_at: occurredAt } : null;
}

const operationsInterventionState = (
  value: unknown,
): OperationsLifecycleSnapshotProjection['interventions'][number]['state'] | null => {
  if (value === 'none') return 'none';
  if (value === 'pending' || value === 'task_issued' || value === 'awaiting_platform_probe') {
    return 'waiting';
  }
  if (value === 'paired') return 'paired';
  if (value === 'refused' || value === 'rejected') return 'refused';
  if (value === 'timed_out' || value === 'expired') return 'timed_out';
  if (value === 'failed') return 'failed';
  if (value === 'completed') return 'completed';
  return null;
};

const operationsTimestampLabel = (value: string | null): string => {
  if (value === null) return '—';
  const timestamp = projectSafeIsoTimestamp(value);
  if (!timestamp) return '—';
  return `${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(timestamp))} CST`;
};

const operationsDelayLabel = (value: number | null): string => {
  if (value === null) return '—';
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = value % 60;
  return hours > 0
    ? `${hours}h ${minutes}m`
    : minutes > 0
      ? `${minutes}m ${seconds}s`
      : `${seconds}s`;
};

const operationsEventLabel = (value: string): string => {
  const known: Record<string, string> = {
    'account.quarantined': '账号进入隔离',
    'account.revoked': '账号撤销',
    'health_check.completed': '会话健康检查',
    'collection.run.completed': '采集完成',
    'collection.run.completed_with_failures': '采集部分失败',
    'collection.run.failed': '采集失败',
    'collection.run.cancelled': '采集取消',
    'collection.run.running': '采集运行',
    'collection.run.pending': '采集等待',
  };
  return known[value] ?? `安全事件 · ${value}`;
};

export function projectOperationsLifecycleSnapshot(
  value: unknown,
): OperationsLifecycleSnapshotProjection | null {
  if (
    !isBrowserRecord(value) ||
    !isBrowserRecord(value.metrics) ||
    !isBrowserRecord(value.projection) ||
    !Array.isArray(value.activity) ||
    !Array.isArray(value.accounts) ||
    !Array.isArray(value.interventions) ||
    !Array.isArray(value.events)
  ) {
    return null;
  }
  const projectionKeys = ['activity', 'accounts', 'interventions', 'events'] as const;
  const metrics = value.metrics;
  const projection = value.projection;
  const collections = {
    activity: value.activity,
    accounts: value.accounts,
    interventions: value.interventions,
    events: value.events,
  };
  if (
    Object.keys(projection).length !== projectionKeys.length ||
    projectionKeys.some((key) => {
      const metadata = projection[key];
      if (!isBrowserRecord(metadata)) return true;
      const total = safeCount(metadata.total);
      const shown = safeCount(metadata.shown);
      return (
        total === null ||
        shown === null ||
        shown !== collections[key].length ||
        shown > total ||
        metadata.truncated !== shown < total
      );
    }) ||
    projectionKeys.some((key) => collections[key].length > 100)
  ) {
    return null;
  }

  const runningRuns = safeCount(metrics.running_runs);
  const projectCount = safeCount(metrics.project_count);
  const pendingInterventions = safeCount(metrics.pending_interventions);
  const healthySessions = safeCount(metrics.healthy_sessions);
  const totalSessions = safeCount(metrics.total_sessions);
  const delayedRuns = safeCount(metrics.delayed_runs);
  const p95Delay = metrics.p95_delay_seconds === null ? null : safeCount(metrics.p95_delay_seconds);
  if (
    runningRuns === null ||
    projectCount === null ||
    pendingInterventions === null ||
    healthySessions === null ||
    totalSessions === null ||
    delayedRuns === null ||
    (metrics.p95_delay_seconds !== null && p95Delay === null) ||
    healthySessions > totalSessions
  ) {
    return null;
  }

  const activity = value.activity.flatMap((item) => {
    if (!isBrowserRecord(item)) return [];
    const pubId = safeResourcePubId(item.pub_id);
    const occurredAt = projectSafeIsoTimestamp(item.occurred_at);
    const eventType = safeBrowserString(item.event_type, 120);
    const objectMask = safeBrowserString(item.object_mask, 160);
    const result = safeBrowserString(item.result, 120);
    const tone = safeBrowserEnum(item.tone, ['positive', 'warning', 'danger', 'neutral'] as const);
    return pubId &&
      occurredAt &&
      eventType &&
      /^[a-z][a-z0-9_.-]{2,119}$/.test(eventType) &&
      objectMask &&
      result &&
      tone
      ? [
          {
            pubId,
            occurredAtLabel: operationsTimestampLabel(occurredAt),
            eventLabel: operationsEventLabel(eventType),
            objectLabel: objectMask,
            resultLabel: result,
            tone,
          },
        ]
      : [];
  });
  if (activity.length !== value.activity.length) return null;

  const accounts = value.accounts.flatMap((item) => {
    const projected = projectCustomerAccountView(item);
    const interventionStatus = projected
      ? operationsInterventionState(projected.intervention_status)
      : null;
    return projected && interventionStatus
      ? [
          {
            accountMask: projected.account_mask,
            platformLabel: projected.platform_label,
            ownerLabel: projected.owner_label,
            custodyMode:
              projected.custody_mode === 'customer_device'
                ? ('customer-device' as const)
                : projected.custody_mode,
            admissionLevel:
              projected.admission_level as OperationsLifecycleSnapshotProjection['accounts'][number]['admissionLevel'],
            scopes:
              projected.scopes as OperationsLifecycleSnapshotProjection['accounts'][number]['scopes'],
            expiresLabel: operationsTimestampLabel(projected.authorization_expires_at),
            regionLabel: projected.region_label,
            sessionHealth: projected.session_health,
            lastVerifiedLabel: operationsTimestampLabel(projected.last_verified_at),
            interventionStatus,
          },
        ]
      : [];
  });
  if (accounts.length !== value.accounts.length) return null;

  const interventions = value.interventions.flatMap((item) => {
    if (!isBrowserRecord(item)) return [];
    const pubId = safeBrowserString(item.pub_id, 120);
    const accountPubId = safeBrowserString(item.account_pub_id, 120);
    const accountMask = projectSafeAccountMask(item.account_mask);
    const state = operationsInterventionState(item.state);
    const challengeType = safeBrowserEnum(item.challenge_type, [
      'otp',
      'qr',
      'push',
      'passkey',
      'face',
      'graphical',
    ] as const);
    const leaseExpiresAt =
      item.lease_expires_at === null ? null : projectSafeIsoTimestamp(item.lease_expires_at);
    const pairingExpiresAt =
      item.pairing_expires_at === null ? null : projectSafeIsoTimestamp(item.pairing_expires_at);
    return pubId &&
      /^int_[A-Za-z0-9_-]{1,116}$/.test(pubId) &&
      accountPubId &&
      /^pac_[A-Za-z0-9_-]{1,116}$/.test(accountPubId) &&
      accountMask &&
      state &&
      challengeType &&
      (item.lease_expires_at === null || leaseExpiresAt) &&
      (item.pairing_expires_at === null || pairingExpiresAt)
      ? [
          {
            pubId,
            accountMask,
            challengeType,
            state,
            leaseLabel: leaseExpiresAt ? '活动租约' : '无活动租约',
            expiresLabel: operationsTimestampLabel(pairingExpiresAt ?? leaseExpiresAt),
          },
        ]
      : [];
  });
  if (interventions.length !== value.interventions.length) return null;

  const events = value.events.flatMap((item) => {
    if (!isBrowserRecord(item)) return [];
    const pubId = safeBrowserString(item.pub_id, 120);
    const accountPubId = safeBrowserString(item.account_pub_id, 120);
    const accountMask = projectSafeAccountMask(item.account_mask);
    const eventType = safeBrowserString(item.event_type, 120);
    const occurredAt = projectSafeIsoTimestamp(item.occurred_at);
    return pubId &&
      /^sev_[A-Za-z0-9_-]{1,116}$/.test(pubId) &&
      accountPubId &&
      /^pac_[A-Za-z0-9_-]{1,116}$/.test(accountPubId) &&
      accountMask &&
      eventType &&
      /^[a-z][a-z0-9_.-]{2,119}$/.test(eventType) &&
      occurredAt
      ? [
          {
            pubId,
            eventLabel: operationsEventLabel(eventType),
            detailLabel: accountMask,
            occurredAtLabel: operationsTimestampLabel(occurredAt),
          },
        ]
      : [];
  });
  if (events.length !== value.events.length) return null;

  return {
    metrics: {
      runningRuns,
      projectCount,
      pendingInterventions,
      healthySessions,
      totalSessions,
      delayedRuns,
      p95DelayLabel: operationsDelayLabel(p95Delay),
    },
    activity,
    accounts,
    interventions,
    events,
    revocationReceipt: null,
    projectionTruncated: projectionKeys.some((key) => {
      const metadata = projection[key];
      return isBrowserRecord(metadata) && metadata.truncated === true;
    }),
  };
}

function maskIdentitySubject(value: unknown): string | null {
  const subject = safeBrowserString(value, 255);
  if (!subject) return null;
  const email = /^([^@\s])[^@\s]*(@[^@\s]+)$/.exec(subject);
  return email ? `${email[1]}***${email[2]}` : '联系标识已隐藏';
}

export function projectIdentityMemberView(value: unknown): IdentityMemberView | null {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeBrowserString(value.pub_id, 120);
  const userPubId = safeBrowserString(value.user_pub_id, 120);
  const displayName = safeBrowserString(value.display_name, 120);
  const maskedSubject = maskIdentitySubject(value.subject);
  const role = safeBrowserEnum(value.role, [
    'customer',
    'operator',
    'analyst',
    'reviewer',
    'admin',
    'worker',
  ] as const);
  const state = safeBrowserEnum(value.state, ['active', 'revoked'] as const);
  const serviceAccount = typeof value.service_account === 'boolean' ? value.service_account : null;
  const valid =
    pubId !== null &&
    /^mbr_[A-Za-z0-9_-]{1,116}$/.test(pubId) &&
    userPubId !== null &&
    /^usr_[A-Za-z0-9_-]{1,116}$/.test(userPubId) &&
    displayName !== null &&
    maskedSubject !== null &&
    role !== null &&
    state !== null &&
    serviceAccount !== null;
  return valid
    ? {
        pub_id: pubId,
        user_pub_id: userPubId,
        subject: maskedSubject,
        display_name: displayName,
        role,
        state,
        service_account: serviceAccount,
      }
    : null;
}

function projectIdentityMemberWriteView(
  value: unknown,
  constraints: {
    expectedMembershipPubId?: string;
    expectedSubject?: string;
    expectedDisplayName?: string;
    expectedRole?: IdentityMemberView['role'];
    expectedServiceAccount?: boolean;
    expectedState: 'active' | 'revoked';
  },
): IdentityMemberView | null {
  const projected = projectIdentityMemberView(value);
  const valid =
    projected !== null &&
    (constraints.expectedMembershipPubId === undefined ||
      projected.pub_id === constraints.expectedMembershipPubId) &&
    (constraints.expectedSubject === undefined ||
      (isBrowserRecord(value) && value.subject === constraints.expectedSubject)) &&
    (constraints.expectedDisplayName === undefined ||
      projected.display_name === constraints.expectedDisplayName) &&
    (constraints.expectedRole === undefined || projected.role === constraints.expectedRole) &&
    (constraints.expectedServiceAccount === undefined ||
      projected.service_account === constraints.expectedServiceAccount) &&
    projected?.state === constraints.expectedState;
  return valid ? projected : null;
}

export function projectOidcBindingView(value: unknown): OidcBindingView | null {
  if (!isBrowserRecord(value)) return null;
  const userPubId = safeBrowserString(value.user_pub_id, 120);
  const createdAt = safeTimestamp(value.created_at);
  const revokedAt = value.revoked_at === null ? null : safeTimestamp(value.revoked_at);
  const active = typeof value.active === 'boolean' ? value.active : null;
  const valid =
    userPubId !== null &&
    /^usr_[A-Za-z0-9_-]{1,116}$/.test(userPubId) &&
    active !== null &&
    createdAt !== null &&
    (active ? value.revoked_at === null : value.revoked_at !== null && revokedAt !== null);
  return valid
    ? {
        user_pub_id: userPubId,
        active,
        created_at: createdAt,
        revoked_at: revokedAt,
      }
    : null;
}

function projectOidcBindingWriteView(
  value: unknown,
  expectedUserPubId: string,
  expectedActive: boolean,
): OidcBindingView | null {
  const projected = projectOidcBindingView(value);
  return projected?.user_pub_id === expectedUserPubId && projected.active === expectedActive
    ? projected
    : null;
}

export type ReportSummarySafeView = {
  pub_id: string;
  project_pub_id: string;
  title: string;
  state: 'draft' | 'review' | 'approved' | 'published' | 'superseded';
  created_at: string;
  updated_at: string;
};
export type ReportPageProjection = ProjectedContractPage<ReportSummarySafeView>;
export type ProjectReportCatalogProjection = {
  total: number;
  shown: number;
  scanned: number;
  invalid: boolean;
  incomplete: boolean;
};
export type ProjectReportCatalogResult =
  | {
      kind: 'ready';
      page: ReportPageProjection;
      nextCursor: string;
      projection: ProjectReportCatalogProjection;
    }
  | { kind: 'forbidden' | 'unavailable' };

export const projectReportCatalogReadLimits = {
  batchSize: 100,
  maxBatches: 10,
} as const;

/** Projects the generated detail root and binds it to the exact browser request context. */
export function projectReportDetailIdentity(
  value: unknown,
  expectedReportPubId: string,
  expectedProjectPubId: string,
): ReportSummarySafeView | null {
  if (!isBrowserRecord(value)) return null;
  const pubIdCandidate = safeBrowserString(value.pub_id, 120);
  const pubId =
    pubIdCandidate && /^rpt_[A-Za-z0-9_-]{1,116}$/.test(pubIdCandidate) ? pubIdCandidate : null;
  const projectPubIdCandidate = safeBrowserString(value.project_pub_id, 120);
  const projectPubId =
    projectPubIdCandidate && /^prj_[A-Za-z0-9_-]{1,116}$/.test(projectPubIdCandidate)
      ? projectPubIdCandidate
      : null;
  const title = safeBrowserString(value.title, 240);
  const state = safeBrowserEnum(value.state, [
    'draft',
    'review',
    'approved',
    'published',
    'superseded',
  ] as const);
  const createdAt = safeTimestamp(value.created_at);
  const updatedAt = safeTimestamp(value.updated_at);
  if (
    pubId !== expectedReportPubId ||
    projectPubId !== expectedProjectPubId ||
    !title ||
    !state ||
    !createdAt ||
    !updatedAt ||
    new Date(updatedAt).getTime() < new Date(createdAt).getTime()
  ) {
    return null;
  }
  return {
    pub_id: pubId,
    project_pub_id: projectPubId,
    title,
    state,
    created_at: createdAt,
    updated_at: updatedAt,
  };
}

/** Drops runtime response extensions and secret-shaped values before list data can enter UI state. */
export function projectReportPage(
  value: unknown,
  limit = 100,
  requestedCursor?: string,
): ReportPageProjection {
  if (!isBrowserRecord(value) || !Array.isArray(value.data) || !isBrowserRecord(value.page)) {
    return {
      data: [],
      page: { next_cursor: null, has_more: false },
      projection: { total: 0, shown: 0, invalid: true },
    };
  }
  const bounded = value.data.slice(0, Math.max(0, limit));
  const seen = new Set<string>();
  let invalid = false;
  const data = bounded.flatMap((item): ReportSummarySafeView[] => {
    if (!isBrowserRecord(item)) {
      invalid = true;
      return [];
    }
    const pubIdCandidate = safeBrowserString(item.pub_id, 120);
    const pubId =
      pubIdCandidate && /^rpt_[A-Za-z0-9_-]{1,116}$/.test(pubIdCandidate) ? pubIdCandidate : null;
    const projectPubIdCandidate = safeBrowserString(item.project_pub_id, 120);
    const projectPubId =
      projectPubIdCandidate && /^prj_[A-Za-z0-9_-]{1,116}$/.test(projectPubIdCandidate)
        ? projectPubIdCandidate
        : null;
    const title = safeBrowserString(item.title, 240);
    const state = safeBrowserEnum(item.state, [
      'draft',
      'review',
      'approved',
      'published',
      'superseded',
    ] as const);
    const createdAt = safeTimestamp(item.created_at);
    const updatedAt = safeTimestamp(item.updated_at);
    if (
      !pubId ||
      !projectPubId ||
      !title ||
      !state ||
      !createdAt ||
      !updatedAt ||
      new Date(updatedAt).getTime() < new Date(createdAt).getTime() ||
      seen.has(pubId) ||
      (requestedCursor !== undefined && pubId <= requestedCursor)
    ) {
      invalid = true;
      return [];
    }
    seen.add(pubId);
    return [
      {
        pub_id: pubId,
        project_pub_id: projectPubId,
        title,
        state,
        created_at: createdAt,
        updated_at: updatedAt,
      },
    ];
  });
  const page = safePage(value.page);
  const rawHasMore = value.page.has_more;
  const rawNextCursor = value.page.next_cursor;
  if (
    typeof rawHasMore !== 'boolean' ||
    (rawHasMore &&
      (page.next_cursor === null ||
        data.length !== bounded.length ||
        page.next_cursor !== data.at(-1)?.pub_id)) ||
    (!rawHasMore && rawNextCursor !== null)
  ) {
    invalid = true;
  }
  return {
    data,
    page: invalid ? { next_cursor: null, has_more: false } : page,
    projection: {
      total: value.data.length,
      shown: data.length,
      invalid,
    },
  };
}

const projectReportDeliveryBoundary = (
  value: unknown,
  expectedReportPubId: string,
): ReportDeliverySafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'dlv_');
  const reportPubId = projectAnalyticsPubId(value.report_pub_id, 'rpt_');
  const recipientPubId = projectAnalyticsPubId(value.recipient_pub_id, 'usr_');
  const deliveredAt = projectSafeIsoTimestamp(value.delivered_at);
  const confirmedAt =
    value.confirmed_at === null ? null : (projectSafeIsoTimestamp(value.confirmed_at) ?? undefined);
  return pubId &&
    reportPubId === expectedReportPubId &&
    recipientPubId &&
    deliveredAt &&
    confirmedAt !== undefined &&
    (confirmedAt === null || new Date(confirmedAt).getTime() >= new Date(deliveredAt).getTime())
    ? {
        pub_id: pubId,
        report_pub_id: reportPubId,
        recipient_pub_id: recipientPubId,
        delivered_at: deliveredAt,
        confirmed_at: confirmedAt,
        // Confirmation prose is not rendered by Customer Web and may contain private text.
        confirmation_comment: null,
      }
    : null;
};

const isNumericBrowserSecret = (value: number): boolean => {
  if (!Number.isSafeInteger(value)) return false;
  const digits = String(Math.abs(value));
  return /^\d{6}$/.test(digits) || /^1[3-9]\d{9}$/.test(digits);
};

const projectSafeStructuredValue = (
  value: unknown,
  depth = 0,
  budget = { nodes: 0 },
): { ok: true; value: SafeStructuredValue } | { ok: false } => {
  budget.nodes += 1;
  if (budget.nodes > 10_000 || depth > 8) return { ok: false };
  if (value === null || typeof value === 'boolean') return { ok: true, value };
  if (typeof value === 'number') {
    return Number.isFinite(value) && !isNumericBrowserSecret(value)
      ? { ok: true, value }
      : { ok: false };
  }
  if (typeof value === 'string') {
    const projected = safeBrowserString(value, 100_000);
    return projected ? { ok: true, value: projected } : { ok: false };
  }
  if (Array.isArray(value)) {
    if (value.length > 1_000) return { ok: false };
    const projected: SafeStructuredValue[] = [];
    for (const item of value) {
      const child = projectSafeStructuredValue(item, depth + 1, budget);
      if (!child.ok) return { ok: false };
      projected.push(child.value);
    }
    return { ok: true, value: projected };
  }
  if (!isBrowserRecord(value)) return { ok: false };
  const entries = Object.entries(value);
  if (entries.length > 500) return { ok: false };
  const projected = {} as SafeStructuredRecord;
  for (const [key, item] of entries) {
    if (!safeBrowserString(key, 160) || containsBrowserSecretKey(key)) return { ok: false };
    const child = projectSafeStructuredValue(item, depth + 1, budget);
    if (!child.ok) return { ok: false };
    projected[key] = child.value;
  }
  return { ok: true, value: projected };
};

const projectSafeStructuredRecord = (value: unknown): SafeStructuredRecord | null => {
  const projected = projectSafeStructuredValue(value);
  return projected.ok && isBrowserRecord(projected.value)
    ? (projected.value as SafeStructuredRecord)
    : null;
};

const emptyProjection = (): ProjectedCollection<never>['projection'] => ({
  total: 0,
  shown: 0,
  invalid: false,
});

const projectReportComponentBoundary = (
  value: unknown,
  expectedVersionPubId: string,
  evidenceIdProjection: ProjectedCollection<never>['projection'],
): ReportComponentSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'rptc_');
  const versionPubId = isReportVersionPubId(value.report_version_pub_id)
    ? value.report_version_pub_id
    : null;
  const componentType = safeBrowserString(value.component_type, 80);
  const ordinal = safeCount(value.ordinal);
  const payload = projectSafeStructuredRecord(value.payload);
  const source = safeBrowserString(value.source, 40);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  if (
    !pubId ||
    versionPubId !== expectedVersionPubId ||
    !componentType ||
    ordinal === null ||
    !payload ||
    !source ||
    !createdAt
  ) {
    return null;
  }
  if (isBrowserRecord(value.payload) && Array.isArray(value.payload.evidence_pub_ids)) {
    const rawIds = value.payload.evidence_pub_ids;
    const bounded = rawIds.slice(0, reportDetailReadProjectionLimits.sectionEvidenceIds);
    const ids = bounded.flatMap((candidate) => {
      const id = projectAnalyticsPubId(candidate, 'evd_');
      return id ? [id] : [];
    });
    payload.evidence_pub_ids = ids;
    evidenceIdProjection.total += rawIds.length;
    evidenceIdProjection.shown += ids.length;
    evidenceIdProjection.invalid ||= ids.length !== bounded.length;
  }
  return {
    pub_id: pubId,
    report_version_pub_id: versionPubId,
    component_type: componentType,
    ordinal,
    payload,
    source,
    created_at: createdAt,
  };
};

const projectReportFrozenFactBoundary = (
  value: unknown,
  expectedVersionPubId: string,
): ReportFrozenFactSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'rptf_');
  const payload = projectSafeStructuredRecord(value.payload);
  const payloadHash = safeHash(value.payload_hash);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  const ordinal = safeCount(value.ordinal);
  return pubId &&
    value.report_version_pub_id === expectedVersionPubId &&
    payload &&
    payloadHash &&
    createdAt &&
    ordinal !== null
    ? {
        pub_id: pubId,
        report_version_pub_id: expectedVersionPubId,
        ordinal,
        payload,
        payload_hash: payloadHash,
        created_at: createdAt,
      }
    : null;
};

const projectReportArtifactBoundary = (
  value: unknown,
  expectedVersionPubId: string,
): ReportArtifactSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'rpta_');
  const format = safeBrowserString(value.format, 20);
  const evidencePubId = projectAnalyticsPubId(value.evidence_pub_id, 'evd_');
  const mimeType = safeBrowserString(value.mime_type, 120);
  const byteSize = safeCount(value.byte_size);
  const sha256 = safeHash(value.sha256);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId &&
    value.report_version_pub_id === expectedVersionPubId &&
    format &&
    evidencePubId &&
    mimeType &&
    byteSize !== null &&
    sha256 &&
    createdAt
    ? {
        pub_id: pubId,
        report_version_pub_id: expectedVersionPubId,
        format,
        evidence_pub_id: evidencePubId,
        mime_type: mimeType,
        byte_size: byteSize,
        sha256,
        created_at: createdAt,
      }
    : null;
};

const projectReportEvidenceBindingBoundary = (
  value: unknown,
  expectedVersionPubId: string,
): ReportEvidenceBindingSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'rptev_');
  const evidencePubId = projectAnalyticsPubId(value.evidence_pub_id, 'evd_');
  const purpose = safeBrowserString(value.purpose, 120);
  const kind = safeBrowserString(value.kind, 120);
  const accessClass = safeBrowserString(value.access_class, 80);
  const mimeType = safeBrowserString(value.mime_type, 120);
  const byteSize = safeCount(value.byte_size);
  const sha256 = safeHash(value.sha256);
  const anchorCount = safeCount(value.anchor_count);
  const captureTime = projectSafeIsoTimestamp(value.capture_time);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId &&
    value.report_version_pub_id === expectedVersionPubId &&
    evidencePubId &&
    purpose &&
    kind &&
    accessClass &&
    mimeType &&
    byteSize !== null &&
    sha256 &&
    anchorCount !== null &&
    captureTime &&
    createdAt
    ? {
        pub_id: pubId,
        report_version_pub_id: expectedVersionPubId,
        evidence_pub_id: evidencePubId,
        purpose,
        kind,
        access_class: accessClass,
        mime_type: mimeType,
        byte_size: byteSize,
        sha256,
        anchor_count: anchorCount,
        capture_time: captureTime,
        created_at: createdAt,
      }
    : null;
};

const projectReportReviewBoundary = (
  value: unknown,
  expectedVersionPubId: string,
): ReportReviewSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'rvw_');
  const reviewerPubId = projectAnalyticsPubId(value.reviewer_pub_id, 'usr_');
  const decision = safeBrowserString(value.decision, 40);
  const rationale = safeBrowserString(value.rationale, 2_000);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId &&
    value.report_version_pub_id === expectedVersionPubId &&
    reviewerPubId &&
    decision &&
    rationale &&
    createdAt
    ? {
        pub_id: pubId,
        report_version_pub_id: expectedVersionPubId,
        reviewer_pub_id: reviewerPubId,
        decision,
        rationale,
        created_at: createdAt,
      }
    : null;
};

const projectReportCommentBoundary = (
  value: unknown,
  expectedVersionPubId: string,
): ReportCommentSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'cmt_');
  const parentPubId =
    value.parent_pub_id === null
      ? null
      : (projectAnalyticsPubId(value.parent_pub_id, 'cmt_') ?? undefined);
  const authorPubId = projectAnalyticsPubId(value.author_pub_id, 'usr_');
  const body = safeBrowserString(value.body, 2_000);
  const resolvedAt =
    value.resolved_at === null ? null : (projectSafeIsoTimestamp(value.resolved_at) ?? undefined);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId &&
    value.report_version_pub_id === expectedVersionPubId &&
    parentPubId !== undefined &&
    authorPubId &&
    body &&
    resolvedAt !== undefined &&
    createdAt
    ? {
        pub_id: pubId,
        report_version_pub_id: expectedVersionPubId,
        parent_pub_id: parentPubId,
        author_pub_id: authorPubId,
        body,
        resolved_at: resolvedAt,
        created_at: createdAt,
      }
    : null;
};

const projectReportEventBoundary = (
  value: unknown,
  expectedVersionPubId: string,
): ReportEventSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'evt_');
  const versionPubId =
    value.report_version_pub_id === null
      ? null
      : isReportVersionPubId(value.report_version_pub_id)
        ? value.report_version_pub_id
        : undefined;
  const eventType = safeBrowserString(value.event_type, 120);
  const actorPubId = safeResourcePubId(value.actor_pub_id);
  const data = projectSafeStructuredRecord(value.data);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId &&
    versionPubId !== undefined &&
    (versionPubId === null || versionPubId === expectedVersionPubId) &&
    eventType &&
    actorPubId &&
    data &&
    createdAt
    ? {
        pub_id: pubId,
        report_version_pub_id: versionPubId,
        event_type: eventType,
        actor_pub_id: actorPubId,
        data,
        created_at: createdAt,
      }
    : null;
};

const projectReportVersionBoundary = (
  value: unknown,
): {
  data: ReportVersionSafeView;
  projection: Record<ReportVersionReadCollection, ProjectedCollection<never>['projection']>;
} | null => {
  if (
    !isBrowserRecord(value) ||
    !Array.isArray(value.components) ||
    !Array.isArray(value.frozen_facts) ||
    !Array.isArray(value.artifacts) ||
    !Array.isArray(value.evidence_bindings) ||
    !Array.isArray(value.reviews) ||
    !Array.isArray(value.comments) ||
    !Array.isArray(value.events)
  ) {
    return null;
  }
  const pubId = isReportVersionPubId(value.pub_id) ? value.pub_id : null;
  const versionNumber = safeCount(value.version_number);
  const windowStart = projectSafeIsoTimestamp(value.window_start);
  const windowEnd = projectSafeIsoTimestamp(value.window_end);
  const filters = projectSafeStructuredRecord(value.filters);
  const metricVersion = safeBrowserString(value.metric_version, 120);
  const scorerVersion = safeBrowserString(value.scorer_version, 120);
  const factSnapshotHash = safeHash(value.fact_snapshot_hash);
  const status = safeBrowserString(value.status, 40);
  if (
    !pubId ||
    versionNumber === null ||
    versionNumber < 1 ||
    !windowStart ||
    !windowEnd ||
    !filters ||
    !metricVersion ||
    !scorerVersion ||
    !factSnapshotHash ||
    !status
  ) {
    return null;
  }
  const sectionEvidenceIds = emptyProjection();
  const components = projectBoundedCollection(
    value.components,
    reportDetailReadProjectionLimits.components,
    (candidate) => projectReportComponentBoundary(candidate, pubId, sectionEvidenceIds),
  );
  const frozenFacts = projectBoundedCollection(
    value.frozen_facts,
    reportDetailReadProjectionLimits.frozenFacts,
    (candidate) => projectReportFrozenFactBoundary(candidate, pubId),
  );
  const artifacts = projectBoundedCollection(
    value.artifacts,
    reportDetailReadProjectionLimits.artifacts,
    (candidate) => projectReportArtifactBoundary(candidate, pubId),
  );
  const evidenceBindings = projectBoundedCollection(
    value.evidence_bindings,
    reportDetailReadProjectionLimits.evidenceBindings,
    (candidate) => projectReportEvidenceBindingBoundary(candidate, pubId),
  );
  const reviews = projectBoundedTailCollection(
    value.reviews,
    reportDetailReadProjectionLimits.reviews,
    (candidate) => projectReportReviewBoundary(candidate, pubId),
  );
  const comments = projectBoundedTailCollection(
    value.comments,
    reportDetailReadProjectionLimits.comments,
    (candidate) => projectReportCommentBoundary(candidate, pubId),
  );
  const events = projectBoundedTailCollection(
    value.events,
    reportDetailReadProjectionLimits.events,
    (candidate) => projectReportEventBoundary(candidate, pubId),
  );
  return {
    data: {
      pub_id: pubId,
      version_number: versionNumber,
      window_start: windowStart,
      window_end: windowEnd,
      filters,
      metric_version: metricVersion,
      scorer_version: scorerVersion,
      fact_snapshot_hash: factSnapshotHash,
      status,
      components: components.data,
      frozen_facts: frozenFacts.data,
      artifacts: artifacts.data,
      evidence_bindings: evidenceBindings.data,
      reviews: reviews.data,
      comments: comments.data,
      events: events.data,
    },
    projection: {
      components: components.projection,
      section_evidence_ids: sectionEvidenceIds,
      frozen_facts: frozenFacts.projection,
      artifacts: artifacts.projection,
      evidence_bindings: evidenceBindings.projection,
      reviews: reviews.projection,
      comments: comments.projection,
      events: events.projection,
    },
  };
};

const projectEffectRetestBoundary = (
  value: unknown,
  expectedActionPubId: string,
): EffectRetestSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'rts_');
  const result = projectSafeStructuredRecord(value.result);
  const recordedByPubId = projectAnalyticsPubId(value.recorded_by_pub_id, 'usr_');
  const measuredAt = projectSafeIsoTimestamp(value.measured_at);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId &&
    value.action_pub_id === expectedActionPubId &&
    result &&
    recordedByPubId &&
    measuredAt &&
    createdAt
    ? {
        pub_id: pubId,
        action_pub_id: expectedActionPubId,
        measured_at: measuredAt,
        result,
        recorded_by_pub_id: recordedByPubId,
        created_at: createdAt,
      }
    : null;
};

const projectOptimizationActionBoundary = (
  value: unknown,
): {
  data: OptimizationActionSafeView;
  retests: ProjectedCollection<never>['projection'];
} | null => {
  if (!isBrowserRecord(value) || !Array.isArray(value.effect_retests)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'act_');
  const description = safeBrowserString(value.description, 1_000);
  const ownerPubId =
    value.owner_pub_id === null
      ? null
      : (projectAnalyticsPubId(value.owner_pub_id, 'usr_') ?? undefined);
  const state = safeBrowserString(value.state, 40);
  const baseline =
    value.baseline === null ? null : (projectSafeStructuredRecord(value.baseline) ?? undefined);
  const outcome =
    value.outcome === null ? null : (projectSafeStructuredRecord(value.outcome) ?? undefined);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  const updatedAt = projectSafeIsoTimestamp(value.updated_at);
  if (
    !pubId ||
    !description ||
    ownerPubId === undefined ||
    !state ||
    baseline === undefined ||
    outcome === undefined ||
    !createdAt ||
    !updatedAt
  ) {
    return null;
  }
  const retests = projectBoundedTailCollection(
    value.effect_retests,
    reportDetailReadProjectionLimits.effectRetests,
    (candidate) => projectEffectRetestBoundary(candidate, pubId),
  );
  return {
    data: {
      pub_id: pubId,
      description,
      owner_pub_id: ownerPubId,
      state,
      baseline,
      outcome,
      created_at: createdAt,
      updated_at: updatedAt,
      effect_retests: retests.data,
    },
    retests: retests.projection,
  };
};

const projectReportDetailBoundary = (
  value: unknown,
  expectedReportPubId: string,
): ReportDetailProjection | null => {
  if (
    !isBrowserRecord(value) ||
    !Array.isArray(value.versions) ||
    !Array.isArray(value.optimization_actions)
  ) {
    return null;
  }
  const pubId = projectAnalyticsPubId(value.pub_id, 'rpt_');
  const projectPubId = projectAnalyticsPubId(value.project_pub_id, 'prj_');
  const title = safeBrowserString(value.title, 240);
  const state = safeBrowserString(value.state, 40);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  const updatedAt = projectSafeIsoTimestamp(value.updated_at);
  if (
    pubId !== expectedReportPubId ||
    !projectPubId ||
    !title ||
    !state ||
    !createdAt ||
    !updatedAt ||
    new Date(updatedAt).getTime() < new Date(createdAt).getTime()
  ) {
    return null;
  }
  const versionCollections: ReportDetailProjection['projection']['version_collections'] = {};
  const versions = projectBoundedTailCollection(
    value.versions,
    reportDetailReadProjectionLimits.versions,
    (candidate) => {
      const projected = projectReportVersionBoundary(candidate);
      if (!projected) return null;
      versionCollections[projected.data.pub_id] = projected.projection;
      return projected.data;
    },
  );
  const actionRetests: ReportDetailProjection['projection']['action_retests'] = {};
  const actions = projectBoundedTailCollection(
    value.optimization_actions,
    reportDetailReadProjectionLimits.optimizationActions,
    (candidate) => {
      const projected = projectOptimizationActionBoundary(candidate);
      if (!projected) return null;
      actionRetests[projected.data.pub_id] = projected.retests;
      return projected.data;
    },
  );
  return {
    pub_id: pubId,
    project_pub_id: projectPubId,
    title,
    state,
    created_at: createdAt,
    updated_at: updatedAt,
    versions: versions.data,
    optimization_actions: actions.data,
    projection: {
      versions: versions.projection,
      optimization_actions: actions.projection,
      version_collections: versionCollections,
      action_retests: actionRetests,
    },
  };
};

const projectEvidenceAssetBoundary = (value: unknown): EvidenceAssetSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'evd_');
  const kind = safeBrowserString(value.kind, 80);
  const mimeType = safeBrowserString(value.mime_type, 120);
  const captureTime = projectSafeIsoTimestamp(value.capture_time);
  const sha256 = safeHash(value.sha256);
  return pubId && kind && mimeType && captureTime && sha256
    ? {
        pub_id: pubId,
        kind,
        mime_type: mimeType,
        capture_time: captureTime,
        sha256,
      }
    : null;
};

const projectEvidenceAssetPageBoundary = (
  value: unknown,
  limit: number,
): EvidenceAssetProjection | null => {
  if (!isBrowserRecord(value) || !Array.isArray(value.data) || !isBrowserRecord(value.page)) {
    return null;
  }
  const collection = projectBoundedCollection(value.data, limit, projectEvidenceAssetBoundary);
  const nextCursor = projectAnalyticsPubId(value.page.next_cursor, 'evd_');
  const hasMore = value.page.has_more === true && nextCursor !== null;
  const pageIsValid =
    typeof value.page.has_more === 'boolean' &&
    ((value.page.has_more === true && nextCursor !== null) ||
      (value.page.has_more === false && value.page.next_cursor === null));
  return {
    data: collection.data,
    page: {
      next_cursor: hasMore ? nextCursor : null,
      has_more: hasMore,
    },
    projection: {
      ...collection.projection,
      invalid: collection.projection.invalid || !pageIsValid,
    },
  };
};

const invalidProjectedCollection = <T>(): ProjectedCollection<T> => ({
  data: [],
  projection: { total: 0, shown: 0, invalid: true },
});

const projectUnknownIntelligenceCollection = <Projected>(
  value: unknown,
  limit: number,
  projector: (value: Record<string, unknown>) => Projected | null,
  direction: 'head' | 'tail' = 'head',
): ProjectedCollection<Projected> => {
  if (!Array.isArray(value)) return invalidProjectedCollection();
  const project = (candidate: unknown) =>
    isBrowserRecord(candidate) ? projector(candidate) : null;
  return direction === 'tail'
    ? projectBoundedTailCollection(value, limit, project)
    : projectBoundedCollection(value, limit, project);
};

const filterProjectedCollection = <T>(
  collection: ProjectedCollection<T>,
  retain: (value: T) => boolean,
): ProjectedCollection<T> => {
  const data = collection.data.filter(retain);
  return {
    data,
    projection: {
      ...collection.projection,
      shown: data.length,
      invalid: collection.projection.invalid || data.length !== collection.data.length,
    },
  };
};

const filterUniqueChronologicalCollection = <T extends { pub_id: string; created_at: string }>(
  collection: ProjectedCollection<T>,
): ProjectedCollection<T> => {
  const seenPubIds = new Set<string>();
  let previousCreatedAt = Number.NEGATIVE_INFINITY;
  return filterProjectedCollection(collection, (value) => {
    const pubId = typeof value.pub_id === 'string' ? value.pub_id : '';
    const createdAt =
      typeof value.created_at === 'string' ? Date.parse(value.created_at) : Number.NaN;
    if (
      !pubId ||
      !Number.isFinite(createdAt) ||
      seenPubIds.has(pubId) ||
      createdAt < previousCreatedAt
    ) {
      return false;
    }
    seenPubIds.add(pubId);
    previousCreatedAt = createdAt;
    return true;
  });
};

const filterVerdictSupersessionChain = (
  collection: ProjectedCollection<InvestigationVerdictSafeView>,
): ProjectedCollection<InvestigationVerdictSafeView> => {
  let previousVerdictPubId = '';
  let chainIsValid = true;
  return filterProjectedCollection(collection, (verdict) => {
    if (!chainIsValid) return false;
    const pubId = typeof verdict.pub_id === 'string' ? verdict.pub_id : '';
    const supersedesPubId =
      verdict.supersedes_pub_id === null
        ? null
        : typeof verdict.supersedes_pub_id === 'string'
          ? verdict.supersedes_pub_id
          : undefined;
    const valid =
      Boolean(pubId) &&
      (supersedesPubId === null ||
        (Boolean(previousVerdictPubId) && supersedesPubId === previousVerdictPubId));
    if (!valid) {
      chainIsValid = false;
      return false;
    }
    previousVerdictPubId = pubId;
    return true;
  });
};

const filterAppealVerdictConsistency = (
  appeals: ProjectedCollection<InvestigationAppealSafeView>,
  verdicts: ProjectedCollection<InvestigationVerdictSafeView>,
): ProjectedCollection<InvestigationAppealSafeView> => {
  const verdictTimeline = verdicts.data.flatMap((verdict) => {
    const pubId = typeof verdict.pub_id === 'string' ? verdict.pub_id : '';
    const supersedesPubId =
      typeof verdict.supersedes_pub_id === 'string' ? verdict.supersedes_pub_id : '';
    const reviewerPubId =
      typeof verdict.reviewer_pub_id === 'string' ? verdict.reviewer_pub_id : '';
    const rationale = typeof verdict.rationale === 'string' ? verdict.rationale : '';
    const createdAt =
      typeof verdict.created_at === 'string' ? Date.parse(verdict.created_at) : Number.NaN;
    return pubId && reviewerPubId && rationale && Number.isFinite(createdAt)
      ? [{ pubId, supersedesPubId, reviewerPubId, rationale, createdAt }]
      : [];
  });
  const matchedReplacementVerdicts = new Set<string>();
  return filterProjectedCollection(appeals, (appeal) => {
    const state = typeof appeal.state === 'string' ? appeal.state : '';
    const createdAt =
      typeof appeal.created_at === 'string' ? Date.parse(appeal.created_at) : Number.NaN;
    const resolvedAt =
      typeof appeal.resolved_at === 'string' ? Date.parse(appeal.resolved_at) : Number.NaN;
    const resolvedByPubId =
      typeof appeal.resolved_by_pub_id === 'string' ? appeal.resolved_by_pub_id : '';
    const resolutionRationale =
      typeof appeal.resolution_rationale === 'string' ? appeal.resolution_rationale : '';
    const priorVerdict = verdictTimeline.filter((verdict) => verdict.createdAt <= createdAt).at(-1);
    if (!Number.isFinite(createdAt) || !priorVerdict) return false;
    if (state === 'open' || state === 'reviewing') return true;
    if (!Number.isFinite(resolvedAt) || !resolvedByPubId || !resolutionRationale) return false;
    if (state !== 'corrected') {
      const verdictAtResolution = verdictTimeline
        .filter((verdict) => verdict.createdAt <= resolvedAt)
        .at(-1);
      return Boolean(verdictAtResolution && verdictAtResolution.reviewerPubId !== resolvedByPubId);
    }
    const replacementIndex = verdictTimeline.findIndex(
      (verdict) =>
        Boolean(verdict.supersedesPubId) &&
        !matchedReplacementVerdicts.has(verdict.pubId) &&
        verdict.createdAt >= createdAt &&
        verdict.createdAt <= resolvedAt &&
        verdict.reviewerPubId === resolvedByPubId &&
        verdict.rationale === resolutionRationale,
    );
    const replacement = replacementIndex >= 0 ? verdictTimeline[replacementIndex] : undefined;
    const replacedVerdict =
      replacementIndex > 0 ? verdictTimeline[replacementIndex - 1] : undefined;
    if (
      !replacement ||
      !replacedVerdict ||
      replacement.supersedesPubId !== replacedVerdict.pubId ||
      replacedVerdict.reviewerPubId === resolvedByPubId
    ) {
      return false;
    }
    matchedReplacementVerdicts.add(replacement.pubId);
    return true;
  });
};

const projectIntelligenceRatio = (value: unknown): number | null => {
  const normalized =
    typeof value === 'number'
      ? value
      : typeof value === 'string' && /^(?:0(?:\.\d+)?|1(?:\.0+)?)$/.test(value)
        ? Number(value)
        : Number.NaN;
  return Number.isFinite(normalized) && normalized >= 0 && normalized <= 1 ? normalized : null;
};

const projectInvestigationScoreBoundary = (
  value: unknown,
  explanationProjection: ProjectedCollection<never>['projection'],
): InvestigationScoreSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'score_');
  const probability = projectIntelligenceRatio(value.probability);
  const evidenceSufficiency = projectIntelligenceRatio(value.evidence_sufficiency);
  const uncertainty = projectIntelligenceRatio(value.uncertainty);
  const ruleVersion = safeBrowserString(value.rule_version, 120);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  if (
    !pubId ||
    probability === null ||
    evidenceSufficiency === null ||
    uncertainty === null ||
    !ruleVersion ||
    !createdAt
  ) {
    return null;
  }
  const explanationValues = Array.isArray(value.explanation)
    ? value.explanation
    : isBrowserRecord(value.explanation)
      ? Object.values(value.explanation)
      : null;
  const explanations = explanationValues
    ? projectBoundedCollection(
        explanationValues,
        intelligenceReadProjectionLimits.explanations,
        (item) => safeBrowserString(item, 500),
      )
    : invalidProjectedCollection<string>();
  explanationProjection.total = explanations.projection.total;
  explanationProjection.shown = explanations.projection.shown;
  explanationProjection.invalid = explanations.projection.invalid;
  return {
    pub_id: pubId,
    probability,
    evidence_sufficiency: evidenceSufficiency,
    uncertainty,
    rule_version: ruleVersion,
    explanation: explanations.data,
    created_at: createdAt,
  };
};

const projectInvestigationClaimBoundary = (value: unknown): InvestigationClaimSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'clm_');
  const normalizedText = safeBrowserString(value.normalized_text, 1_000);
  const verifiability = safeBrowserString(value.verifiability, 80);
  return pubId && normalizedText && verifiability
    ? {
        pub_id: pubId,
        normalized_text: normalizedText,
        verifiability,
      }
    : null;
};

const projectInvestigationEvidenceBoundary = (
  value: unknown,
): InvestigationEvidenceSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'ce_');
  const claimPubId = projectAnalyticsPubId(value.claim_pub_id, 'clm_');
  const evidencePubId = projectAnalyticsPubId(value.evidence_pub_id, 'evd_');
  const relation: InvestigationEvidenceRelation | null =
    value.relation === 'supports' ||
    value.relation === 'contradicts' ||
    value.relation === 'insufficient'
      ? value.relation
      : null;
  const sourceCluster = safeBrowserString(value.source_cluster, 120);
  const independenceWeight = projectIntelligenceRatio(value.independence_weight);
  const rationale = safeBrowserString(value.rationale, 1_000);
  return pubId &&
    claimPubId &&
    evidencePubId &&
    relation &&
    sourceCluster &&
    independenceWeight !== null &&
    rationale
    ? {
        pub_id: pubId,
        claim_pub_id: claimPubId,
        evidence_pub_id: evidencePubId,
        relation,
        source_cluster: sourceCluster,
        independence_weight: independenceWeight,
        rationale,
      }
    : null;
};

const projectInvestigationSourceBoundary = (value: unknown): InvestigationSourceSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'srca_');
  const sourcePubId = safeResourcePubId(value.source_pub_id);
  const clusterId = safeBrowserString(value.cluster_id, 120);
  const independenceWeight = projectIntelligenceRatio(value.independence_weight);
  const circularCitationRisk = projectIntelligenceRatio(value.circular_citation_risk);
  return pubId &&
    sourcePubId &&
    clusterId &&
    independenceWeight !== null &&
    circularCitationRisk !== null
    ? {
        pub_id: pubId,
        source_pub_id: sourcePubId,
        cluster_id: clusterId,
        independence_weight: independenceWeight,
        circular_citation_risk: circularCitationRisk,
      }
    : null;
};

const intelligenceGraphRelations: readonly InvestigationGraphRelation[] = [
  'supports',
  'contradicts',
  'insufficient',
  'derived_from',
  'near_duplicate',
  'published_by',
  'cites',
  'mentions',
] as const;

const projectInvestigationGraphBoundary = (value: unknown): InvestigationGraphSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const fromPubId = safeResourcePubId(value.from_pub_id);
  const toPubId = safeResourcePubId(value.to_pub_id);
  const relation: InvestigationGraphRelation | null =
    typeof value.relation === 'string' &&
    intelligenceGraphRelations.some((candidate) => candidate === value.relation)
      ? (value.relation as InvestigationGraphRelation)
      : null;
  const weight =
    value.weight === null ? null : (projectIntelligenceRatio(value.weight) ?? undefined);
  const evidencePubId =
    value.evidence_pub_id === null
      ? null
      : (projectAnalyticsPubId(value.evidence_pub_id, 'evd_') ?? undefined);
  return fromPubId && toPubId && relation && weight !== undefined && evidencePubId !== undefined
    ? {
        from_pub_id: fromPubId,
        to_pub_id: toPubId,
        relation,
        weight,
        evidence_pub_id: evidencePubId,
      }
    : null;
};

const projectInvestigationAppealBoundary = (value: unknown): InvestigationAppealSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'apl_');
  const submittedByPubId = projectAnalyticsPubId(value.submitted_by_pub_id, 'usr_');
  const reason = safeBrowserString(value.reason, 10_000);
  const state: InvestigationAppealState | null =
    typeof value.state === 'string' &&
    ['open', 'reviewing', 'upheld', 'corrected', 'rejected'].includes(value.state)
      ? (value.state as InvestigationAppealState)
      : null;
  const resolution: InvestigationAppealResolution | null | undefined =
    value.resolution === null
      ? null
      : typeof value.resolution === 'string' &&
          ['upheld', 'corrected', 'rejected'].includes(value.resolution)
        ? (value.resolution as InvestigationAppealResolution)
        : undefined;
  const resolvedByPubId =
    value.resolved_by_pub_id === null
      ? null
      : (projectAnalyticsPubId(value.resolved_by_pub_id, 'usr_') ?? undefined);
  const resolutionRationale =
    value.resolution_rationale === null
      ? null
      : (safeBrowserString(value.resolution_rationale, 10_000) ?? undefined);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  const updatedAt = projectSafeIsoTimestamp(value.updated_at);
  const resolvedAt =
    value.resolved_at === null ? null : (projectSafeIsoTimestamp(value.resolved_at) ?? undefined);
  const active = state === 'open' || state === 'reviewing';
  const timestampsAreOrdered =
    createdAt &&
    updatedAt &&
    Date.parse(updatedAt) >= Date.parse(createdAt) &&
    (resolvedAt === null ||
      (resolvedAt !== undefined &&
        Date.parse(resolvedAt) >= Date.parse(createdAt) &&
        Date.parse(updatedAt) >= Date.parse(resolvedAt)));
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
  return pubId &&
    submittedByPubId &&
    reason &&
    reason.trim().length > 0 &&
    state &&
    resolution !== undefined &&
    resolvedByPubId !== undefined &&
    resolutionRationale !== undefined &&
    timestampsAreOrdered &&
    transactionIsConsistent
    ? {
        pub_id: pubId,
        state,
        submitted_by_pub_id: submittedByPubId,
        reason,
        resolution,
        resolved_by_pub_id: resolvedByPubId,
        resolution_rationale: resolutionRationale,
        created_at: createdAt,
        updated_at: updatedAt,
        resolved_at: resolvedAt,
      }
    : null;
};

const projectInvestigationVerdictBoundary = (
  value: unknown,
): InvestigationVerdictSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'vrd_');
  const reviewerPubId = projectAnalyticsPubId(value.reviewer_pub_id, 'usr_');
  const rationale = safeBrowserString(value.rationale, 10_000);
  const verdict: InvestigationVerdict | null =
    typeof value.verdict === 'string' &&
    ['likely', 'unlikely', 'uncertain', 'insufficient'].includes(value.verdict)
      ? (value.verdict as InvestigationVerdict)
      : null;
  const supersedesPubId =
    value.supersedes_pub_id === null
      ? null
      : (projectAnalyticsPubId(value.supersedes_pub_id, 'vrd_') ?? undefined);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId &&
    reviewerPubId &&
    rationale &&
    rationale.trim().length > 0 &&
    verdict &&
    supersedesPubId !== undefined &&
    createdAt
    ? {
        pub_id: pubId,
        verdict,
        reviewer_pub_id: reviewerPubId,
        rationale,
        supersedes_pub_id: supersedesPubId,
        created_at: createdAt,
      }
    : null;
};

const projectInvestigationDetailBoundary = (
  value: unknown,
  expectedInvestigationPubId: string,
): InvestigationDetailProjection | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'inv_');
  if (pubId !== expectedInvestigationPubId) return null;
  const explanationProjection = invalidProjectedCollection<never>().projection;
  const seenScorePubIds = new Set<string>();
  let previousScoreCreatedAt = Number.NEGATIVE_INFINITY;
  const scores = projectUnknownIntelligenceCollection(
    value.scores,
    intelligenceReadProjectionLimits.scores,
    (candidate) => {
      const scorePubId = projectAnalyticsPubId(candidate.pub_id, 'score_');
      const createdAt = projectSafeIsoTimestamp(candidate.created_at);
      const createdAtEpoch = createdAt ? Date.parse(createdAt) : Number.NaN;
      if (
        !scorePubId ||
        !Number.isFinite(createdAtEpoch) ||
        seenScorePubIds.has(scorePubId) ||
        createdAtEpoch < previousScoreCreatedAt
      ) {
        return null;
      }
      const projected = projectInvestigationScoreBoundary(candidate, explanationProjection);
      if (!projected) return null;
      seenScorePubIds.add(scorePubId);
      previousScoreCreatedAt = createdAtEpoch;
      return projected;
    },
    'tail',
  );
  const seenClaimPubIds = new Set<string>();
  const claims = filterProjectedCollection(
    projectUnknownIntelligenceCollection(
      value.claims,
      intelligenceReadProjectionLimits.claims,
      projectInvestigationClaimBoundary,
    ),
    (claim) => {
      const claimPubId = typeof claim.pub_id === 'string' ? claim.pub_id : '';
      if (!claimPubId || seenClaimPubIds.has(claimPubId)) return false;
      seenClaimPubIds.add(claimPubId);
      return true;
    },
  );
  const claimPubIds = new Set(claims.data.map((claim) => String(claim.pub_id)));
  const seenClaimEvidencePubIds = new Set<string>();
  const seenClaimEvidencePairs = new Set<string>();
  const evidenceMatrix = filterProjectedCollection(
    projectUnknownIntelligenceCollection(
      value.evidence_matrix,
      intelligenceReadProjectionLimits.evidenceMatrix,
      projectInvestigationEvidenceBoundary,
    ),
    (item) => {
      const pubId = typeof item.pub_id === 'string' ? item.pub_id : '';
      const claimPubId = typeof item.claim_pub_id === 'string' ? item.claim_pub_id : '';
      const evidencePubId = typeof item.evidence_pub_id === 'string' ? item.evidence_pub_id : '';
      const pair = claimPubId && evidencePubId ? `${claimPubId}\u0000${evidencePubId}` : '';
      if (
        !pubId ||
        !claimPubIds.has(claimPubId) ||
        !pair ||
        seenClaimEvidencePubIds.has(pubId) ||
        seenClaimEvidencePairs.has(pair)
      ) {
        return false;
      }
      seenClaimEvidencePubIds.add(pubId);
      seenClaimEvidencePairs.add(pair);
      return true;
    },
  );
  const seenSourceAssessmentPubIds = new Set<string>();
  const seenAssessedSourcePubIds = new Set<string>();
  const sourceIndependence = filterProjectedCollection(
    projectUnknownIntelligenceCollection(
      value.source_independence,
      intelligenceReadProjectionLimits.sourceIndependence,
      projectInvestigationSourceBoundary,
    ),
    (source) => {
      const pubId = typeof source.pub_id === 'string' ? source.pub_id : '';
      const sourcePubId = typeof source.source_pub_id === 'string' ? source.source_pub_id : '';
      if (
        !pubId ||
        !sourcePubId ||
        seenSourceAssessmentPubIds.has(pubId) ||
        seenAssessedSourcePubIds.has(sourcePubId)
      ) {
        return false;
      }
      seenSourceAssessmentPubIds.add(pubId);
      seenAssessedSourcePubIds.add(sourcePubId);
      return true;
    },
  );
  const seenGraphEdges = new Set<string>();
  const graph = filterProjectedCollection(
    projectUnknownIntelligenceCollection(
      value.graph,
      intelligenceReadProjectionLimits.graph,
      projectInvestigationGraphBoundary,
    ),
    (edge) => {
      const fromPubId = typeof edge.from_pub_id === 'string' ? edge.from_pub_id : '';
      const toPubId = typeof edge.to_pub_id === 'string' ? edge.to_pub_id : '';
      const relation = typeof edge.relation === 'string' ? edge.relation : '';
      const edgeKey =
        fromPubId && toPubId && relation ? `${fromPubId}\u0000${toPubId}\u0000${relation}` : '';
      if (!edgeKey || seenGraphEdges.has(edgeKey)) return false;
      seenGraphEdges.add(edgeKey);
      return true;
    },
  );
  const projectedAppeals = filterUniqueChronologicalCollection(
    projectUnknownIntelligenceCollection(
      value.appeals,
      intelligenceReadProjectionLimits.appeals,
      projectInvestigationAppealBoundary,
      'tail',
    ),
  );
  const verdicts = filterVerdictSupersessionChain(
    filterUniqueChronologicalCollection(
      projectUnknownIntelligenceCollection(
        value.verdicts,
        intelligenceReadProjectionLimits.verdicts,
        projectInvestigationVerdictBoundary,
        'tail',
      ),
    ),
  );
  const appeals = filterAppealVerdictConsistency(projectedAppeals, verdicts);
  return {
    pub_id: pubId,
    scores: scores.data,
    claims: claims.data,
    evidence_matrix: evidenceMatrix.data,
    source_independence: sourceIndependence.data,
    graph: graph.data,
    appeals: appeals.data,
    verdicts: verdicts.data,
    projection: {
      scores: scores.projection,
      explanations: explanationProjection,
      claims: claims.projection,
      evidenceMatrix: evidenceMatrix.projection,
      sourceIndependence: sourceIndependence.projection,
      graph: graph.projection,
      appeals: appeals.projection,
      verdicts: verdicts.projection,
    },
  };
};

const projectInvestigationHistoryBoundary = (
  value: unknown,
): InvestigationPageHistorySafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const contentPubId = projectAnalyticsPubId(value.content_pub_id, 'cnt_');
  const versionPubId = projectAnalyticsPubId(value.version_pub_id, 'cntv_');
  const canonicalUrl = projectSafeRelationUrl(value.canonical_url);
  const title = value.title === null ? null : (safeBrowserString(value.title, 300) ?? undefined);
  const versionNumber = safeCount(value.version_number);
  const bodyHash = safeHash(value.body_hash);
  const evidencePubId =
    value.evidence_pub_id === null
      ? null
      : (projectAnalyticsPubId(value.evidence_pub_id, 'evd_') ?? undefined);
  const capturedAt = projectSafeIsoTimestamp(value.captured_at);
  const publishedAt =
    value.published_at === null ? null : (projectSafeIsoTimestamp(value.published_at) ?? undefined);
  const snapshotPubId =
    value.snapshot_pub_id === null
      ? null
      : (projectAnalyticsPubId(value.snapshot_pub_id, 'snap_') ?? undefined);
  const snapshotNumber =
    value.snapshot_number === null
      ? null
      : typeof value.snapshot_number === 'number' &&
          Number.isSafeInteger(value.snapshot_number) &&
          value.snapshot_number > 0
        ? value.snapshot_number
        : undefined;
  const normalizedTextHash =
    value.normalized_text_hash === null
      ? null
      : (safeHash(value.normalized_text_hash) ?? undefined);
  const perceptualHash =
    value.perceptual_hash === null ? null : (safeHash(value.perceptual_hash) ?? undefined);
  return contentPubId &&
    versionPubId &&
    canonicalUrl &&
    title !== undefined &&
    versionNumber !== null &&
    versionNumber > 0 &&
    bodyHash &&
    evidencePubId !== undefined &&
    capturedAt &&
    publishedAt !== undefined &&
    snapshotPubId !== undefined &&
    snapshotNumber !== undefined &&
    ((snapshotPubId === null && snapshotNumber === null) ||
      (snapshotPubId !== null && snapshotNumber !== null)) &&
    normalizedTextHash !== undefined &&
    perceptualHash !== undefined
    ? {
        content_pub_id: contentPubId,
        version_pub_id: versionPubId,
        canonical_url: canonicalUrl,
        title,
        version_number: versionNumber,
        body_hash: bodyHash,
        evidence_pub_id: evidencePubId,
        captured_at: capturedAt,
        published_at: publishedAt,
        snapshot_pub_id: snapshotPubId,
        snapshot_number: snapshotNumber,
        normalized_text_hash: normalizedTextHash,
        perceptual_hash: perceptualHash,
      }
    : null;
};

const projectInvestigationVisualDiffBoundary = (
  value: unknown,
): InvestigationVisualDiffSafeView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'diff_');
  const contentPubId = projectAnalyticsPubId(value.content_pub_id, 'cnt_');
  const beforeVersionPubId = projectAnalyticsPubId(value.before_version_pub_id, 'cntv_');
  const afterVersionPubId = projectAnalyticsPubId(value.after_version_pub_id, 'cntv_');
  const beforeEvidencePubId = projectAnalyticsPubId(value.before_evidence_pub_id, 'evd_');
  const afterEvidencePubId = projectAnalyticsPubId(value.after_evidence_pub_id, 'evd_');
  const beforeHash = isBrowserRecord(value.text_diff)
    ? safeHash(value.text_diff.before_hash)
    : null;
  const afterHash = isBrowserRecord(value.text_diff) ? safeHash(value.text_diff.after_hash) : null;
  const textDiff =
    value.text_diff === null
      ? null
      : beforeHash && afterHash
        ? { before_hash: beforeHash, after_hash: afterHash }
        : undefined;
  const similarity =
    value.similarity === null
      ? null
      : projectIntelligenceRatio(value.similarity) === null
        ? undefined
        : String(projectIntelligenceRatio(value.similarity));
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId &&
    contentPubId &&
    beforeVersionPubId &&
    afterVersionPubId &&
    beforeEvidencePubId &&
    afterEvidencePubId &&
    textDiff !== undefined &&
    similarity !== undefined &&
    typeof value.visual_diff_available === 'boolean' &&
    createdAt
    ? {
        pub_id: pubId,
        content_pub_id: contentPubId,
        before_version_pub_id: beforeVersionPubId,
        after_version_pub_id: afterVersionPubId,
        before_evidence_pub_id: beforeEvidencePubId,
        after_evidence_pub_id: afterEvidencePubId,
        text_diff: textDiff,
        similarity,
        visual_diff_available: value.visual_diff_available,
        created_at: createdAt,
      }
    : null;
};

const normalizeReadProjectionLimit = (value: number | undefined, maximum: number): number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value > 0
    ? Math.min(value, maximum)
    : maximum;

const projectIntelligenceCursorPage = <Source, Projected extends { pub_id: string }>(
  value: unknown,
  limit: number,
  cursorPrefix: string,
  projector: (value: Source) => Projected | null,
): ProjectedContractPage<Projected> | null => {
  if (!isBrowserRecord(value) || !Array.isArray(value.data) || !isBrowserRecord(value.page)) {
    return null;
  }
  const seen = new Set<string>();
  const collection = projectBoundedCollection(value.data as Source[], limit, (candidate) => {
    const projected = projector(candidate);
    if (!projected || seen.has(projected.pub_id)) return null;
    seen.add(projected.pub_id);
    return projected;
  });
  const nextCursor =
    value.page.next_cursor === null
      ? null
      : (projectAnalyticsPubId(value.page.next_cursor, cursorPrefix) ?? undefined);
  const pageIsValid =
    typeof value.page.has_more === 'boolean' &&
    nextCursor !== undefined &&
    ((value.page.has_more === true && nextCursor !== null) ||
      (value.page.has_more === false && nextCursor === null));
  const hasMore = pageIsValid && value.page.has_more === true;
  return {
    data: collection.data,
    page: {
      next_cursor: hasMore ? (nextCursor ?? null) : null,
      has_more: hasMore,
    },
    projection: {
      ...collection.projection,
      invalid: collection.projection.invalid || !pageIsValid,
    },
  };
};

const projectInvestigationSummaryBoundary = (
  item: unknown,
): InvestigationSummarySafeView | null => {
  if (!isBrowserRecord(item)) return null;
  const pubId = projectAnalyticsPubId(item.pub_id, 'inv_');
  const title = safeBrowserString(item.title, 240);
  const state = safeBrowserEnum(item.state, [
    'draft',
    'collecting',
    'review',
    'decided',
    'appealed',
    'corrected',
  ] as const);
  const accessClass = safeBrowserEnum(item.access_class, ['public', 'customer_private'] as const);
  const createdAt = safeTimestamp(item.created_at);
  const updatedAt = safeTimestamp(item.updated_at);
  const probability =
    item.probability === null ? null : (safeUnitDecimal(item.probability) ?? undefined);
  const latestVerdict =
    item.latest_verdict === null
      ? null
      : (safeBrowserEnum(item.latest_verdict, [
          'likely',
          'unlikely',
          'uncertain',
          'insufficient',
        ] as const) ?? undefined);
  const claimCount = safeCount(item.claim_count);
  const sourceClusterCount = safeCount(item.source_cluster_count);
  return pubId &&
    title &&
    state &&
    accessClass &&
    createdAt &&
    updatedAt &&
    new Date(updatedAt).getTime() >= new Date(createdAt).getTime() &&
    probability !== undefined &&
    latestVerdict !== undefined &&
    claimCount !== null &&
    sourceClusterCount !== null
    ? {
        pub_id: pubId,
        title,
        state,
        access_class: accessClass,
        created_at: createdAt,
        updated_at: updatedAt,
        claim_count: claimCount,
        source_cluster_count: sourceClusterCount,
        probability,
        latest_verdict: latestVerdict,
      }
    : null;
};

/** Strict, bounded list projection for case queues; open response extensions never cross this boundary. */
export function projectInvestigationPage(
  value: unknown,
  limit: number = intelligenceReadProjectionLimits.investigations,
): InvestigationPageProjection | null {
  return projectIntelligenceCursorPage(
    value,
    normalizeReadProjectionLimit(limit, intelligenceReadProjectionLimits.investigations),
    'inv_',
    projectInvestigationSummaryBoundary,
  );
}

/** Keeps only the governed dataset summary projection returned by the OpenAPI contract. */
export function projectEvaluationDatasetView(value: unknown): EvaluationDatasetSafeView | null {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'dset_');
  const version = safeBrowserString(value.version, 100);
  const datasetSha256 = safeHash(value.dataset_sha256);
  const state = safeBrowserEnum(value.state, ['draft', 'approved', 'revoked'] as const);
  const caseCount = safeCount(value.case_count);
  const positiveCount = safeCount(value.positive_count);
  const labelerCount = safeCount(value.labeler_count);
  const submittedAt = safeTimestamp(value.submitted_at);
  const approvedAt = value.approved_at === null ? null : safeTimestamp(value.approved_at);
  if (
    !pubId ||
    !version ||
    !datasetSha256 ||
    !state ||
    caseCount === null ||
    caseCount < 20 ||
    positiveCount === null ||
    positiveCount <= 0 ||
    positiveCount >= caseCount ||
    labelerCount === null ||
    labelerCount < 2 ||
    !submittedAt ||
    (value.approved_at !== null && !approvedAt) ||
    (approvedAt !== null && new Date(approvedAt).getTime() < new Date(submittedAt).getTime())
  ) {
    return null;
  }
  return {
    pub_id: pubId,
    version,
    dataset_sha256: datasetSha256,
    state,
    case_count: caseCount,
    positive_count: positiveCount,
    labeler_count: labelerCount,
    submitted_at: submittedAt,
    approved_at: approvedAt,
  };
}

export function projectEvaluationDatasetPage(
  value: unknown,
  limit: number = intelligenceReadProjectionLimits.evaluationDatasets,
): EvaluationDatasetPageProjection | null {
  return projectIntelligenceCursorPage(
    value,
    normalizeReadProjectionLimit(limit, intelligenceReadProjectionLimits.evaluationDatasets),
    'dset_',
    projectEvaluationDatasetView,
  );
}

const evaluationDatasetRegistrationMatches = (
  value: EvaluationDatasetSafeView,
  body: EvaluationDatasetCreate,
): boolean =>
  value.version === body.version &&
  value.state === 'draft' &&
  value.case_count === body.cases.length &&
  value.positive_count === body.cases.filter((item) => item.actual_positive).length &&
  value.labeler_count === body.labeler_count &&
  value.approved_at === null;

const evaluationDatasetApprovalMatches = (
  value: EvaluationDatasetSafeView,
  datasetPubId: string,
): boolean =>
  value.pub_id === datasetPubId && value.state === 'approved' && value.approved_at !== null;

const evaluationCheckKeys = [
  'precision',
  'recall',
  'false_positive_rate',
  'brier_score',
  'expected_calibration_error',
  'explanation_completeness',
] as const;
const requiredEvaluationExplanationFields = [
  'evidence_sufficiency',
  'independent_source_count',
  'uncertainty',
  'rule_version',
  'model_version',
  'human_verdict_state',
] as const;

/** Strictly projects an ephemeral evaluation receipt; raw cases and response extensions are absent. */
export function projectEvaluationRunView(value: unknown): EvaluationRunSafeView | null {
  if (
    !isBrowserRecord(value) ||
    !isBrowserRecord(value.metrics) ||
    !isBrowserRecord(value.admission_checks) ||
    !Array.isArray(value.required_explanation_fields)
  ) {
    return null;
  }
  const pubId = projectAnalyticsPubId(value.pub_id, 'eval_');
  const datasetPubId = projectAnalyticsPubId(value.dataset_pub_id, 'dset_');
  const scorerVersion = safeBrowserString(value.scorer_version, 100);
  const decisionThreshold = safeUnitDecimal(value.decision_threshold);
  const trainingClusterManifestSha256 = safeHash(value.training_cluster_manifest_sha256);
  const trainingClusterCount = safeCount(value.training_cluster_count);
  const sampleCount = safeCount(value.sample_count);
  const admissionPolicyVersion = safeBrowserString(value.admission_policy_version, 120);
  const createdAt = safeTimestamp(value.created_at);
  const calibrationBins =
    typeof value.calibration_bins === 'number' && Number.isInteger(value.calibration_bins)
      ? value.calibration_bins
      : null;
  const admissionPassed =
    typeof value.admission_passed === 'boolean' ? value.admission_passed : null;
  const modelAdmissionState =
    value.model_admission_state === null || value.model_admission_state === undefined
      ? null
      : (safeBrowserEnum(value.model_admission_state, ['admitted', 'revoked'] as const) ??
        undefined);
  const metricPrecision =
    value.metrics.precision === null ? null : safeUnitDecimal(value.metrics.precision);
  const metricRecall = value.metrics.recall === null ? null : safeUnitDecimal(value.metrics.recall);
  const metricFalsePositiveRate =
    value.metrics.false_positive_rate === null
      ? null
      : safeUnitDecimal(value.metrics.false_positive_rate);
  const metricBrier = safeUnitDecimal(value.metrics.brier_score);
  const metricCalibration = safeUnitDecimal(value.metrics.expected_calibration_error);
  const metricCompleteness = safeUnitDecimal(value.metrics.explanation_completeness_rate);
  const metricSampleCount = safeCount(value.metrics.sample_count);
  const metricPositiveCount = safeCount(value.metrics.positive_count);
  const metricNegativeCount = safeCount(value.metrics.negative_count);
  const metricDatasetVersion = safeBrowserString(value.metrics.dataset_version, 100);
  const metricScorerVersion = safeBrowserString(value.metrics.scorer_version, 100);
  const evaluationSha256 = safeHash(value.metrics.evaluation_sha256);
  const checks: Partial<Record<(typeof evaluationCheckKeys)[number], boolean>> = {};
  for (const key of evaluationCheckKeys) {
    const candidate = value.admission_checks[key];
    if (typeof candidate === 'boolean') checks[key] = candidate;
  }
  const explanations = Array.isArray(value.required_explanation_fields)
    ? [...new Set(value.required_explanation_fields)]
    : [];
  if (
    !pubId ||
    !datasetPubId ||
    !scorerVersion ||
    !decisionThreshold ||
    !trainingClusterManifestSha256 ||
    trainingClusterCount === null ||
    trainingClusterCount > 50_000 ||
    calibrationBins === null ||
    calibrationBins < 2 ||
    calibrationBins > 100 ||
    sampleCount === null ||
    sampleCount < 20 ||
    !admissionPolicyVersion ||
    evaluationCheckKeys.some((key) => typeof checks[key] !== 'boolean') ||
    admissionPassed === null ||
    (value.metrics.precision !== null && metricPrecision === null) ||
    (value.metrics.recall !== null && metricRecall === null) ||
    (value.metrics.false_positive_rate !== null && metricFalsePositiveRate === null) ||
    !metricBrier ||
    !metricCalibration ||
    !metricCompleteness ||
    metricSampleCount === null ||
    metricSampleCount !== sampleCount ||
    metricPositiveCount === null ||
    metricNegativeCount === null ||
    metricPositiveCount + metricNegativeCount !== metricSampleCount ||
    !metricDatasetVersion ||
    !metricScorerVersion ||
    metricScorerVersion !== scorerVersion ||
    !evaluationSha256 ||
    modelAdmissionState === undefined ||
    explanations.length !== requiredEvaluationExplanationFields.length ||
    requiredEvaluationExplanationFields.some((field) => !explanations.includes(field)) ||
    !createdAt
  ) {
    return null;
  }
  return {
    pub_id: pubId,
    dataset_pub_id: datasetPubId,
    scorer_version: scorerVersion,
    decision_threshold: decisionThreshold,
    calibration_bins: calibrationBins,
    training_cluster_manifest_sha256: trainingClusterManifestSha256,
    training_cluster_count: trainingClusterCount,
    sample_count: sampleCount,
    admission_policy_version: admissionPolicyVersion,
    admission_checks: checks as EvaluationRunSafeView['admission_checks'],
    admission_passed: admissionPassed,
    model_admission_state: modelAdmissionState,
    metrics: {
      precision: metricPrecision,
      recall: metricRecall,
      false_positive_rate: metricFalsePositiveRate,
      brier_score: metricBrier,
      expected_calibration_error: metricCalibration,
      explanation_completeness_rate: metricCompleteness,
      sample_count: metricSampleCount,
      positive_count: metricPositiveCount,
      negative_count: metricNegativeCount,
      dataset_version: metricDatasetVersion,
      scorer_version: metricScorerVersion,
      evaluation_sha256: evaluationSha256,
    },
    required_explanation_fields: [...requiredEvaluationExplanationFields],
    created_at: createdAt,
  };
}

export function projectEvaluationRunPage(
  value: unknown,
  limit: number = intelligenceReadProjectionLimits.evaluationRuns,
): EvaluationRunPageProjection | null {
  return projectIntelligenceCursorPage(
    value,
    normalizeReadProjectionLimit(limit, intelligenceReadProjectionLimits.evaluationRuns),
    'eval_',
    projectEvaluationRunView,
  );
}

const evaluationRunWriteMatches = (
  value: EvaluationRunSafeView,
  datasetPubId: string,
  body: EvaluationRunCreate,
): boolean =>
  value.dataset_pub_id === datasetPubId &&
  value.scorer_version === body.scorer_version &&
  Number(value.decision_threshold) === Number(body.decision_threshold) &&
  value.calibration_bins === body.calibration_bins &&
  value.training_cluster_count === (body.training_propagation_cluster_digests?.length ?? 0) &&
  value.sample_count === body.predictions.length &&
  value.model_admission_state === null;

export function projectModelAdmissionView(value: unknown): ModelAdmissionSafeView | null {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'madm_');
  const evaluationRunPubId = projectAnalyticsPubId(value.evaluation_run_pub_id, 'eval_');
  const scorerVersion = safeBrowserString(value.scorer_version, 100);
  const state = safeBrowserEnum(value.state, ['admitted', 'revoked'] as const);
  const rationale = safeBrowserString(value.rationale, 2_000);
  const admittedAt = safeTimestamp(value.admitted_at);
  const revokedAt = value.revoked_at === null ? null : safeTimestamp(value.revoked_at);
  if (
    !pubId ||
    !evaluationRunPubId ||
    !scorerVersion ||
    !state ||
    !rationale ||
    !admittedAt ||
    (value.revoked_at !== null && !revokedAt) ||
    (revokedAt !== null && new Date(revokedAt).getTime() < new Date(admittedAt).getTime())
  ) {
    return null;
  }
  return {
    pub_id: pubId,
    evaluation_run_pub_id: evaluationRunPubId,
    scorer_version: scorerVersion,
    state,
    rationale,
    admitted_at: admittedAt,
    revoked_at: revokedAt,
  };
}

const modelAdmissionWriteMatches = (
  value: ModelAdmissionSafeView,
  evaluationRunPubId: string,
  body: ModelAdmissionCreate,
): boolean =>
  value.evaluation_run_pub_id === evaluationRunPubId &&
  value.state === 'admitted' &&
  value.rationale === body.rationale &&
  value.revoked_at === null;

export function projectModelAdmissionPage(
  value: unknown,
  limit: number = intelligenceReadProjectionLimits.modelAdmissions,
): ModelAdmissionPageProjection | null {
  return projectIntelligenceCursorPage(
    value,
    normalizeReadProjectionLimit(limit, intelligenceReadProjectionLimits.modelAdmissions),
    'madm_',
    projectModelAdmissionView,
  );
}

export async function listReports(
  headers: IdentitySessionHeaders,
  filters: { cursor?: string; limit?: number } = {},
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ReportPageProjection>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/reports', {
      params: {
        query: {
          ...(filters.cursor ? { cursor: filters.cursor } : {}),
          limit: filters.limit ?? 50,
        },
        header: headers,
      },
    });
    return result.data
      ? {
          kind: 'ready',
          data: projectReportPage(result.data, filters.limit ?? 50, filters.cursor),
        }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createReport(
  body: ReportCreateInput,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ReportCreateSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST('/api/v2/reports', {
      params: { header: headers },
      body,
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const response = result.data as ReportCreateResponse;
    const reportPubId = safeBrowserString(response.report_pub_id, 120);
    const reportVersionPubId = safeBrowserString(response.report_version_pub_id, 120);
    const state = safeBrowserString(response.state, 40);
    const factSnapshotHash = safeBrowserString(response.fact_snapshot_hash, 64);
    return reportPubId &&
      /^rpt_[A-Za-z0-9_-]{1,116}$/.test(reportPubId) &&
      reportVersionPubId &&
      isReportVersionPubId(reportVersionPubId) &&
      state &&
      factSnapshotHash &&
      /^[0-9a-f]{64}$/.test(factSnapshotHash)
      ? { kind: 'ready', data: { reportPubId, reportVersionPubId, state, factSnapshotHash } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

/**
 * The generated reports contract is tenant-scoped and currently has no project filter.
 * Scan bounded server pages until the current and next project-bound reports are known,
 * while retaining only exact current-project summaries in browser state.
 */
export async function loadProjectReportCatalog(
  headers: IdentitySessionHeaders,
  projectPubId: string,
  requestedCursor = '',
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectReportCatalogResult> {
  const safeProjectPubId = safeBrowserString(projectPubId, 120);
  const safeRequestedCursor = requestedCursor === '' ? '' : safeBrowserString(requestedCursor, 120);
  if (
    !safeProjectPubId ||
    !/^prj_[A-Za-z0-9_-]{1,116}$/.test(safeProjectPubId) ||
    (requestedCursor !== '' &&
      (!safeRequestedCursor || !/^rpt_[A-Za-z0-9_-]{1,116}$/.test(safeRequestedCursor)))
  ) {
    return {
      kind: 'ready',
      page: {
        data: [],
        page: { next_cursor: null, has_more: false },
        projection: { total: 0, shown: 0, invalid: true },
      },
      nextCursor: '',
      projection: {
        total: 0,
        shown: 0,
        scanned: 0,
        invalid: true,
        incomplete: false,
      },
    };
  }

  const matches: ReportPageProjection['data'] = [];
  let scanCursor = safeRequestedCursor;
  let lastScannedCursor = '';
  let scanned = 0;
  let exhausted = false;
  for (let batch = 0; batch < projectReportCatalogReadLimits.maxBatches; batch += 1) {
    const result = await listReports(
      headers,
      {
        ...(scanCursor ? { cursor: scanCursor } : {}),
        limit: projectReportCatalogReadLimits.batchSize,
      },
      client,
    );
    if (result.kind !== 'ready') return result;
    scanned += result.data.projection.total;
    matches.push(
      ...result.data.data.filter((report) => report.project_pub_id === safeProjectPubId),
    );
    if (result.data.projection.invalid) {
      return {
        kind: 'ready',
        page: {
          data: [],
          page: { next_cursor: null, has_more: false },
          projection: { total: scanned, shown: 0, invalid: true },
        },
        nextCursor: '',
        projection: {
          total: scanned,
          shown: matches.length,
          scanned,
          invalid: true,
          incomplete: false,
        },
      };
    }
    if (matches.length >= 2) break;
    const nextServerCursor = result.data.page.next_cursor;
    if (!result.data.page.has_more || typeof nextServerCursor !== 'string') {
      exhausted = true;
      break;
    }
    lastScannedCursor = nextServerCursor;
    scanCursor = nextServerCursor;
  }

  const selected = matches.slice(0, 1);
  const incomplete = matches.length < 2 && !exhausted && Boolean(lastScannedCursor);
  const nextCursor = matches.length >= 2 ? matches[0]!.pub_id : incomplete ? lastScannedCursor : '';
  return {
    kind: 'ready',
    page: {
      data: selected,
      page: {
        next_cursor: nextCursor || null,
        has_more: Boolean(nextCursor),
      },
      projection: {
        total: matches.length,
        shown: selected.length,
        invalid: false,
      },
    },
    nextCursor,
    projection: {
      total: matches.length,
      shown: selected.length,
      scanned,
      invalid: false,
      incomplete,
    },
  };
}

export async function getReport(
  reportPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ReportDetailResult> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/reports/{report_pub_id}', {
      params: { path: { report_pub_id: reportPubId }, header: headers },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectReportDetailBoundary(result.data, reportPubId);
    return projected ? { kind: 'ready', data: projected } : { kind: 'invalid' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listReportDeliveries(
  reportPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ReportDeliveryProjection>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/reports/{report_pub_id}/deliveries',
      {
        params: { path: { report_pub_id: reportPubId }, header: headers },
      },
    );
    return result.data
      ? {
          kind: 'ready',
          data: projectBoundedCollection(
            result.data,
            customerReportReadProjectionLimits.deliveries,
            (value) => projectReportDeliveryBoundary(value, reportPubId),
          ),
        }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createReportDelivery(
  reportPubId: string,
  body: ReportDeliveryCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ReportDeliverySafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/reports/{report_pub_id}/deliveries',
      {
        params: { path: { report_pub_id: reportPubId }, header: headers },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const deliveryPubId = safeBrowserString(result.data.delivery_pub_id, 120);
    return result.data.report_pub_id === reportPubId &&
      deliveryPubId !== null &&
      /^dlv_[A-Za-z0-9_-]{1,116}$/.test(deliveryPubId)
      ? { kind: 'ready', data: { reportPubId, deliveryPubId } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createReportRevision(
  reportPubId: string,
  body: ReportRevisionCreate,
  idempotencyKey: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ReportRevisionSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/reports/{report_pub_id}/versions',
      {
        params: {
          path: { report_pub_id: reportPubId },
          header: { ...headers, 'Idempotency-Key': idempotencyKey },
        },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const candidate = result.data as Record<string, unknown>;
    const reportVersionPubId = candidate.report_version_pub_id;
    const versionNumber = candidate.version_number;
    const factSnapshotHash = candidate.fact_snapshot_hash;
    return candidate.report_pub_id === reportPubId &&
      isReportVersionPubId(reportVersionPubId) &&
      typeof versionNumber === 'number' &&
      Number.isSafeInteger(versionNumber) &&
      versionNumber >= 1 &&
      typeof factSnapshotHash === 'string' &&
      /^[0-9a-f]{64}$/.test(factSnapshotHash)
      ? {
          kind: 'ready',
          data: {
            reportPubId,
            reportVersionPubId,
            versionNumber,
            factSnapshotHash,
          },
        }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function confirmReportDelivery(
  reportPubId: string,
  deliveryPubId: string,
  body: ReportDeliveryConfirm,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ReportDeliveryConfirmationSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/reports/{report_pub_id}/deliveries/{delivery_pub_id}/confirm',
      {
        params: {
          path: { report_pub_id: reportPubId, delivery_pub_id: deliveryPubId },
          header: headers,
        },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    return result.data.delivery_pub_id === deliveryPubId && result.data.state === 'confirmed'
      ? { kind: 'ready', data: { deliveryPubId, state: 'confirmed' } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function reviewReport(
  reportPubId: string,
  versionPubId: string,
  body: ReportReviewCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ReportReviewSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/reviews',
      {
        params: {
          path: { report_pub_id: reportPubId, version_pub_id: versionPubId },
          header: headers,
        },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const response = result.data as ReportReviewCreateResponse;
    const reviewPubId = safeBrowserString(response.review_pub_id, 120);
    return reviewPubId && /^rvw_[A-Za-z0-9_-]{1,116}$/.test(reviewPubId)
      ? { kind: 'ready', data: { reviewPubId } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function commentOnReport(
  reportPubId: string,
  versionPubId: string,
  body: ReportCommentCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ReportCommentSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/comments',
      {
        params: {
          path: { report_pub_id: reportPubId, version_pub_id: versionPubId },
          header: headers,
        },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const commentPubId = safeBrowserString(result.data.comment_pub_id, 120);
    return result.data.report_pub_id === reportPubId &&
      commentPubId !== null &&
      /^cmt_[A-Za-z0-9_-]{1,116}$/.test(commentPubId)
      ? { kind: 'ready', data: { reportPubId, commentPubId } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function publishReport(
  reportPubId: string,
  versionPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<null>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/publish',
      {
        params: {
          path: { report_pub_id: reportPubId, version_pub_id: versionPubId },
          header: headers,
        },
      },
    );
    return result.response.status === 204
      ? { kind: 'ready', data: null }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export type ReportAiDraft = { body: string; model: string };

/** AI 起草章节散文（不落库；前端以 source='ai' 走不可变版本 + 人工确认门）。 */
export async function draftReportSection(
  reportPubId: string,
  body: { title: string; model?: string },
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ReportAiDraft>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/reports/{report_pub_id}/ai-draft',
      {
        params: { path: { report_pub_id: reportPubId }, header: headers },
        body: { title: body.title, ...(body.model ? { model: body.model } : {}) },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const candidate = result.data as Record<string, unknown>;
    const draftBody = safeBrowserString(candidate.body, 32768);
    const model = safeBrowserString(candidate.model, 120);
    return draftBody && model
      ? { kind: 'ready', data: { body: draftBody, model } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

/** AI 起草模型下拉清单（与 intake 调研同一 GEO_RESEARCH_LLM_MODELS 真源）。 */
export async function getReportAiDraftModels(
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<string[]>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/reports/ai-draft-models', {
      params: { header: headers },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectIntakeResearchModels(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

// ── 报告事实建议（报价单四指标，分析链路自动供给；只读 brandrank 层）──────────
// 投影纪律照既有 wrapper：形状/词表 fail-closed、DLP 扫描、计数有界。domain_unset
// 单独成 kind（可操作态：提示运维先配置项目分析域），不与泛化 unavailable 混淆。
export type ReportFactSuggestionMetric =
  | 'brand_appearance_rate'
  | 'rank_distribution'
  | 'top1_appearance_rate'
  | 'top3_appearance_rate'
  | 'top5_appearance_rate'
  | 'competitor_appearance_rate';

export type ReportFactSuggestionRow = {
  metric: ReportFactSuggestionMetric;
  value: number | null;
  unit: 'percent' | 'rank';
  numerator: number;
  denominator: number;
  dimensions: { platform: string; region: string; query: string };
  source: 'system_computed';
  method: 'brandrank-llm-v1';
  domain: string;
  window: { start: string; end: string };
  extra: {
    of_mentions?: number;
    competitor?: string;
    best_rank?: number | null;
    ranks?: number[];
  } | null;
};

export type ReportFactSuggestions = {
  projectPubId: string;
  domain: string;
  windowDays: number;
  window: { start: string; end: string };
  targetBrand: string | null;
  competitors: string[];
  insufficient: boolean;
  insufficientReasons: string[];
  truncated: boolean;
  coverage: { nAnswers: number; nWithExtract: number; nGroups: number };
  factRows: ReportFactSuggestionRow[];
};

export type ReportFactSuggestionsResult =
  | { kind: 'ready'; data: ReportFactSuggestions }
  | { kind: 'domain_unset' }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

const reportFactSuggestionMetrics = [
  'brand_appearance_rate',
  'rank_distribution',
  'top1_appearance_rate',
  'top3_appearance_rate',
  'top5_appearance_rate',
  'competitor_appearance_rate',
] as const;
const REPORT_FACT_SUGGESTION_ROW_LIMIT = 5000; // 与 API 侧 200 组 × 25 行上限对齐

const safeFiniteMetricValue = (value: unknown): number | null | undefined =>
  value === null
    ? null
    : typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 1_000_000
      ? value
      : undefined;

/** 维度串允许如实空串（缺维不臆造归属）；非空走 DLP 安全串。 */
const safeDimensionString = (value: unknown, maxLength: number): string | null =>
  value === '' ? '' : safeBrowserString(value, maxLength);

const projectReportFactSuggestionRow = (value: unknown): ReportFactSuggestionRow | null => {
  if (!isBrowserRecord(value)) return null;
  const metric = safeBrowserEnum(value.metric, reportFactSuggestionMetrics);
  const rowValue = safeFiniteMetricValue(value.value);
  const unit = safeBrowserEnum(value.unit, ['percent', 'rank'] as const);
  const numerator = safeCount(value.numerator);
  const denominator = safeCount(value.denominator);
  const domain = safeBrowserString(value.domain, 40);
  if (
    !metric ||
    rowValue === undefined ||
    !unit ||
    numerator === null ||
    denominator === null ||
    denominator < 1 ||
    !domain ||
    value.source !== 'system_computed' ||
    value.method !== 'brandrank-llm-v1' ||
    !isBrowserRecord(value.dimensions) ||
    !isBrowserRecord(value.window)
  ) {
    return null;
  }
  const platform = safeDimensionString(value.dimensions.platform, 40);
  const region = safeDimensionString(value.dimensions.region, 40);
  const query = safeBrowserString(value.dimensions.query, 500);
  const windowStart = projectSafeIsoTimestamp(value.window.start);
  const windowEnd = projectSafeIsoTimestamp(value.window.end);
  if (platform === null || region === null || !query || !windowStart || !windowEnd) return null;
  let extra: ReportFactSuggestionRow['extra'] = null;
  if (value.extra !== undefined && value.extra !== null) {
    if (!isBrowserRecord(value.extra)) return null;
    const projected: NonNullable<ReportFactSuggestionRow['extra']> = {};
    if (value.extra.of_mentions !== undefined) {
      const ofMentions = safeFiniteMetricValue(value.extra.of_mentions);
      if (ofMentions === null || ofMentions === undefined) return null;
      projected.of_mentions = ofMentions;
    }
    if (value.extra.competitor !== undefined) {
      const competitor = safeBrowserString(value.extra.competitor, 200);
      if (!competitor) return null;
      projected.competitor = competitor;
    }
    if (value.extra.best_rank !== undefined) {
      if (value.extra.best_rank !== null && safeCount(value.extra.best_rank) === null) {
        return null;
      }
      projected.best_rank = value.extra.best_rank as number | null;
    }
    if (value.extra.ranks !== undefined) {
      if (!Array.isArray(value.extra.ranks) || value.extra.ranks.length > 64) return null;
      const ranks: number[] = [];
      for (const rank of value.extra.ranks) {
        const safeRank = safeCount(rank);
        if (safeRank === null || safeRank < 1) return null;
        ranks.push(safeRank);
      }
      projected.ranks = ranks;
    }
    extra = projected;
  }
  if (metric === 'competitor_appearance_rate' && !extra?.competitor) return null;
  if (metric !== 'competitor_appearance_rate' && extra?.competitor) return null;
  return {
    metric,
    value: rowValue,
    unit,
    numerator,
    denominator,
    dimensions: { platform, region, query },
    source: 'system_computed',
    method: 'brandrank-llm-v1',
    domain,
    window: { start: windowStart, end: windowEnd },
    extra,
  };
};

const projectReportFactSuggestions = (value: unknown): ReportFactSuggestions | null => {
  if (!isBrowserRecord(value)) return null;
  const projectPubId = safeBrowserString(value.project_pub_id, 120);
  const domain = safeBrowserString(value.domain, 40);
  const windowDays = safeCount(value.window_days);
  const targetBrand =
    value.target_brand === null ? null : safeBrowserString(value.target_brand, 200);
  const reasons = value.insufficient_reasons;
  if (
    !projectPubId ||
    !domain ||
    windowDays === null ||
    windowDays < 1 ||
    value.target_brand === undefined ||
    (value.target_brand !== null && !targetBrand) ||
    typeof value.insufficient !== 'boolean' ||
    typeof value.truncated !== 'boolean' ||
    !Array.isArray(reasons) ||
    reasons.length > 8 ||
    !reasons.every(
      (reason) =>
        typeof reason === 'string' &&
        ['no_answers', 'no_extraction_coverage', 'target_brand_unset'].includes(reason),
    ) ||
    !Array.isArray(value.competitors) ||
    value.competitors.length > 20 ||
    !isBrowserRecord(value.window) ||
    !isBrowserRecord(value.coverage) ||
    !Array.isArray(value.fact_rows) ||
    value.fact_rows.length > REPORT_FACT_SUGGESTION_ROW_LIMIT
  ) {
    return null;
  }
  const windowStart = projectSafeIsoTimestamp(value.window.start);
  const windowEnd = projectSafeIsoTimestamp(value.window.end);
  const nAnswers = safeCount(value.coverage.n_answers);
  const nWithExtract = safeCount(value.coverage.n_with_extract);
  const nGroups = safeCount(value.coverage.n_groups);
  if (!windowStart || !windowEnd || nAnswers === null || nWithExtract === null || nGroups === null)
    return null;
  const competitors: string[] = [];
  for (const entry of value.competitors) {
    const competitor = safeBrowserString(entry, 200);
    if (!competitor) return null;
    competitors.push(competitor);
  }
  const factRows: ReportFactSuggestionRow[] = [];
  for (const entry of value.fact_rows) {
    const row = projectReportFactSuggestionRow(entry);
    if (!row) return null;
    factRows.push(row);
  }
  return {
    projectPubId,
    domain,
    windowDays,
    window: { start: windowStart, end: windowEnd },
    targetBrand,
    competitors,
    insufficient: value.insufficient,
    insufficientReasons: reasons as string[],
    truncated: value.truncated,
    coverage: { nAnswers, nWithExtract, nGroups },
    factRows,
  };
};

/** 报告事实建议草稿（报价单四指标；空窗/零覆盖 → ready + insufficient 诚实结构）。 */
export async function getReportFactSuggestions(
  projectPubId: string,
  windowDays: number,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ReportFactSuggestionsResult> {
  if (!Number.isSafeInteger(windowDays) || windowDays < 1 || windowDays > 366) {
    return { kind: 'unavailable' };
  }
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/projects/{project_pub_id}/report-fact-suggestions',
      {
        params: {
          path: { project_pub_id: projectPubId },
          query: { window_days: windowDays },
          header: headers,
        },
      },
    );
    if (!result.data) {
      if (result.response.status === 400) {
        const errorBody: unknown = result.error;
        const code =
          isBrowserRecord(errorBody) && isBrowserRecord(errorBody.error)
            ? errorBody.error.code
            : null;
        if (code === 'domain_unset') return { kind: 'domain_unset' };
      }
      return classifyResourceFailure(result.response.status);
    }
    const projected = projectReportFactSuggestions(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createReportAction(
  reportPubId: string,
  body: ReportActionCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ReportActionSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/reports/{report_pub_id}/actions',
      {
        params: { path: { report_pub_id: reportPubId }, header: headers },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const response = result.data as ReportActionCreateResponse;
    const actionPubId = safeBrowserString(response.action_pub_id, 120);
    return actionPubId && /^act_[A-Za-z0-9_-]{1,116}$/.test(actionPubId)
      ? { kind: 'ready', data: { actionPubId } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function updateReportAction(
  reportPubId: string,
  actionPubId: string,
  body: ReportActionUpdate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<null>> {
  try {
    const result = await projectedApiClient(client).PATCH(
      '/api/v2/reports/{report_pub_id}/actions/{action_pub_id}',
      {
        params: {
          path: { report_pub_id: reportPubId, action_pub_id: actionPubId },
          header: headers,
        },
        body,
      },
    );
    return result.response.status === 204
      ? { kind: 'ready', data: null }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createReportEffectRetest(
  reportPubId: string,
  actionPubId: string,
  body: ReportEffectRetestCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ReportEffectRetestSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/reports/{report_pub_id}/actions/{action_pub_id}/effect-retests',
      {
        params: {
          path: { report_pub_id: reportPubId, action_pub_id: actionPubId },
          header: headers,
        },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const response = result.data as ReportEffectRetestCreateResponse;
    const effectRetestPubId = safeBrowserString(response.effect_retest_pub_id, 120);
    return effectRetestPubId && /^rts_[A-Za-z0-9_-]{1,116}$/.test(effectRetestPubId)
      ? { kind: 'ready', data: { effectRetestPubId } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export function reportArtifactUrl(
  reportPubId: string,
  versionPubId: string,
  format: ReportArtifactFormat,
): string {
  return `/api/v2/reports/${encodeURIComponent(reportPubId)}/versions/${encodeURIComponent(
    versionPubId,
  )}/artifacts/${encodeURIComponent(format)}`;
}

export async function getReportArtifact(
  reportPubId: string,
  versionPubId: string,
  format: ReportArtifactFormat,
  expected: ReportArtifactIntegrity,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<VerifiedReportArtifact>> {
  try {
    const expectedSha256 = safeHash(expected.sha256);
    if (
      !expectedSha256 ||
      expected.mimeType !== reportArtifactMediaTypes[format] ||
      !Number.isSafeInteger(expected.byteSize) ||
      expected.byteSize <= 0 ||
      expected.byteSize > 50 * 1024 * 1024
    ) {
      return { kind: 'unavailable' };
    }
    const result = await projectedApiClient(client).GET(
      '/api/v2/reports/{report_pub_id}/versions/{version_pub_id}/artifacts/{format_name}',
      {
        params: {
          path: {
            report_pub_id: reportPubId,
            version_pub_id: versionPubId,
            format_name: format,
          },
          header: headers,
        },
        parseAs: 'blob',
      },
    );
    if (
      !(result.data instanceof Blob) ||
      result.data.type !== expected.mimeType ||
      result.data.size !== expected.byteSize
    ) {
      return result.data
        ? { kind: 'unavailable' }
        : classifyResourceFailure(result.response.status);
    }
    const digest = await globalThis.crypto.subtle.digest(
      'SHA-256',
      await result.data.arrayBuffer(),
    );
    const actualSha256 = [...new Uint8Array(digest)]
      .map((value) => value.toString(16).padStart(2, '0'))
      .join('');
    return actualSha256 === expectedSha256
      ? {
          kind: 'ready',
          data: {
            blob: result.data,
            byteSize: result.data.size,
            mimeType: result.data.type,
            sha256: actualSha256,
          },
        }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listEvidenceAssets(
  headers: IdentitySessionHeaders,
  filters: { cursor?: string; limit?: number } = {},
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<EvidenceAssetProjection>> {
  try {
    const limit =
      typeof filters.limit === 'number' &&
      Number.isSafeInteger(filters.limit) &&
      filters.limit >= 1 &&
      filters.limit <= customerEvidenceReadProjectionLimits.assets
        ? filters.limit
        : 50;
    const result = await projectedApiClient(client).GET('/api/v2/evidence/assets', {
      params: {
        query: {
          ...(filters.cursor ? { cursor: filters.cursor } : {}),
          limit,
        },
        header: headers,
      },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectEvidenceAssetPageBoundary(result.data, limit);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getEvidenceAssetContent(
  evidencePubId: string,
  expected: EvidenceAssetIntegrity,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<VerifiedEvidenceAsset>> {
  try {
    const expectedSha256 = safeHash(expected.sha256);
    if (
      !projectAnalyticsPubId(evidencePubId, 'evd_') ||
      !expectedSha256 ||
      expected.mimeType !== 'image/png' ||
      !safeBrowserString(expected.mimeType, 120) ||
      !Number.isSafeInteger(expected.byteSize) ||
      expected.byteSize <= 0 ||
      expected.byteSize > 30 * 1024 * 1024
    ) {
      return { kind: 'unavailable' };
    }
    const result = await projectedApiClient(client).GET(
      '/api/v2/evidence/assets/{evidence_pub_id}/content',
      {
        params: {
          path: { evidence_pub_id: evidencePubId },
          header: headers,
        },
        parseAs: 'blob',
      },
    );
    if (
      !(result.data instanceof Blob) ||
      result.data.type !== expected.mimeType ||
      result.data.size !== expected.byteSize
    ) {
      return result.data
        ? { kind: 'unavailable' }
        : classifyResourceFailure(result.response.status);
    }
    const digest = await globalThis.crypto.subtle.digest(
      'SHA-256',
      await result.data.arrayBuffer(),
    );
    const actualSha256 = [...new Uint8Array(digest)]
      .map((value) => value.toString(16).padStart(2, '0'))
      .join('');
    return actualSha256 === expectedSha256
      ? {
          kind: 'ready',
          data: {
            blob: result.data,
            byteSize: result.data.size,
            mimeType: result.data.type,
            sha256: actualSha256,
          },
        }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

/**
 * Generates a client-supplied package ID without digit runs that can be mistaken for OTP or
 * complete-phone material. The one-to-one digit mapping preserves the UUID's entropy.
 */
export function createEvidencePackagePubId(
  randomUuid: string = globalThis.crypto.randomUUID(),
): string {
  const compact = randomUuid.replaceAll('-', '').toLowerCase();
  if (!/^[0-9a-f]{32}$/.test(compact)) {
    throw new Error('A valid UUID is required to create an evidence package public ID');
  }
  const digitLetters = 'ghijklmnop';
  return `pkg_${compact.replace(/\d/g, (digit) => digitLetters[Number(digit)] ?? 'q')}`;
}

export async function createEvidencePackage(
  body: EvidencePackageCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<EvidencePackageSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST('/api/v2/evidence/packages', {
      params: { header: headers },
      body,
    });
    if (!result.data) {
      return classifyResourceFailure(result.response.status);
    }
    const response = result.data as EvidencePackageCreateResponse;
    const packagePubId = safeBrowserString(response.package_pub_id, 120);
    const manifestSha256 = safeHash(response.manifest_sha256);
    return packagePubId === body.package_pub_id &&
      /^pkg_[A-Za-z0-9_-]{1,116}$/.test(packagePubId) &&
      manifestSha256 &&
      response.state === 'ready'
      ? {
          kind: 'ready',
          data: { packagePubId, manifestSha256, state: 'ready' },
        }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listEvaluationDatasets(
  headers: IdentitySessionHeaders,
  filters: { cursor?: string; limit?: number } = {},
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<EvaluationDatasetPageProjection>> {
  try {
    const limit = normalizeReadProjectionLimit(
      filters.limit,
      intelligenceReadProjectionLimits.evaluationDatasets,
    );
    const result = await projectedApiClient(client).GET(
      '/api/v2/intelligence/evaluation-datasets',
      {
        params: {
          query: {
            ...(filters.cursor ? { cursor: filters.cursor } : {}),
            limit,
          },
          header: headers,
        },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectEvaluationDatasetPage(result.data, limit);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function registerEvaluationDataset(
  body: EvaluationDatasetCreate,
  idempotencyKey: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<EvaluationDatasetSafeView>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/intelligence/evaluation-datasets',
      {
        params: { header: { ...headers, 'Idempotency-Key': idempotencyKey } },
        body,
      },
    );
    const projected = result.data ? projectEvaluationDatasetView(result.data) : null;
    return projected && evaluationDatasetRegistrationMatches(projected, body)
      ? { kind: 'ready', data: projected }
      : result.data
        ? { kind: 'unavailable' }
        : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function approveEvaluationDataset(
  datasetPubId: string,
  body: EvaluationDatasetApprove,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<EvaluationDatasetSafeView>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/approve',
      {
        params: { path: { dataset_pub_id: datasetPubId }, header: headers },
        body,
      },
    );
    const projected = result.data ? projectEvaluationDatasetView(result.data) : null;
    return projected && evaluationDatasetApprovalMatches(projected, datasetPubId)
      ? { kind: 'ready', data: projected }
      : result.data
        ? { kind: 'unavailable' }
        : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function runEvaluationDataset(
  datasetPubId: string,
  body: EvaluationRunCreate,
  idempotencyKey: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<EvaluationRunSafeView>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/runs',
      {
        params: {
          path: { dataset_pub_id: datasetPubId },
          header: { ...headers, 'Idempotency-Key': idempotencyKey },
        },
        body,
      },
    );
    const projected = result.data ? projectEvaluationRunView(result.data) : null;
    return projected && evaluationRunWriteMatches(projected, datasetPubId, body)
      ? { kind: 'ready', data: projected }
      : result.data
        ? { kind: 'unavailable' }
        : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listEvaluationRuns(
  headers: IdentitySessionHeaders,
  filters: { cursor?: string; limit?: number } = {},
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<EvaluationRunPageProjection>> {
  try {
    const limit = normalizeReadProjectionLimit(
      filters.limit,
      intelligenceReadProjectionLimits.evaluationRuns,
    );
    const result = await projectedApiClient(client).GET('/api/v2/intelligence/evaluation-runs', {
      params: {
        query: {
          ...(filters.cursor ? { cursor: filters.cursor } : {}),
          limit,
        },
        header: headers,
      },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectEvaluationRunPage(result.data, limit);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function admitEvaluatedModel(
  evaluationRunPubId: string,
  body: ModelAdmissionCreate,
  idempotencyKey: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ModelAdmissionSafeView>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/intelligence/evaluation-runs/{evaluation_run_pub_id}/admit',
      {
        params: {
          path: { evaluation_run_pub_id: evaluationRunPubId },
          header: { ...headers, 'Idempotency-Key': idempotencyKey },
        },
        body,
      },
    );
    const projected = result.data ? projectModelAdmissionView(result.data) : null;
    return projected && modelAdmissionWriteMatches(projected, evaluationRunPubId, body)
      ? { kind: 'ready', data: projected }
      : result.data
        ? { kind: 'unavailable' }
        : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listModelAdmissions(
  headers: IdentitySessionHeaders,
  filters: { cursor?: string; limit?: number } = {},
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ModelAdmissionPageProjection>> {
  try {
    const limit = normalizeReadProjectionLimit(
      filters.limit,
      intelligenceReadProjectionLimits.modelAdmissions,
    );
    const result = await projectedApiClient(client).GET('/api/v2/intelligence/model-admissions', {
      params: {
        query: {
          ...(filters.cursor ? { cursor: filters.cursor } : {}),
          limit,
        },
        header: headers,
      },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectModelAdmissionPage(result.data, limit);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listInvestigations(
  headers: IdentitySessionHeaders,
  filters: { cursor?: string; limit?: number } = {},
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<InvestigationPageProjection>> {
  try {
    const limit = normalizeReadProjectionLimit(
      filters.limit,
      intelligenceReadProjectionLimits.investigations,
    );
    const result = await projectedApiClient(client).GET('/api/v2/intelligence/investigations', {
      params: {
        query: {
          ...(filters.cursor ? { cursor: filters.cursor } : {}),
          limit,
        },
        header: headers,
      },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectInvestigationPage(result.data, limit);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getInvestigation(
  investigationPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<InvestigationDetailResult> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/intelligence/investigations/{investigation_pub_id}',
      {
        params: { path: { investigation_pub_id: investigationPubId }, header: headers },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectInvestigationDetailBoundary(result.data, investigationPubId);
    return projected ? { kind: 'ready', data: projected } : { kind: 'invalid' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getInvestigationPageHistory(
  investigationPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<InvestigationPageHistoryProjection>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/intelligence/investigations/{investigation_pub_id}/page-history',
      {
        params: { path: { investigation_pub_id: investigationPubId }, header: headers },
      },
    );
    return result.data
      ? {
          kind: 'ready',
          data: projectBoundedTailCollection(
            result.data,
            intelligenceReadProjectionLimits.historyPages,
            projectInvestigationHistoryBoundary,
          ),
        }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getInvestigationVisualDiffs(
  investigationPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<InvestigationVisualDiffsProjection>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/intelligence/investigations/{investigation_pub_id}/visual-diffs',
      {
        params: { path: { investigation_pub_id: investigationPubId }, header: headers },
      },
    );
    return result.data
      ? {
          kind: 'ready',
          data: projectBoundedTailCollection(
            result.data,
            intelligenceReadProjectionLimits.historyDiffs,
            projectInvestigationVisualDiffBoundary,
          ),
        }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createInvestigationVerdict(
  investigationPubId: string,
  body: VerdictCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<VerdictSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/intelligence/investigations/{investigation_pub_id}/verdicts',
      {
        params: { path: { investigation_pub_id: investigationPubId }, header: headers },
        body,
      },
    );
    if (!result.data) {
      return classifyResourceFailure(result.response.status);
    }
    const response = result.data as VerdictCreateResponse;
    const verdictPubId = safeBrowserString(response.verdict_pub_id, 120);
    return verdictPubId && /^vrd_[A-Za-z0-9_-]{1,116}$/.test(verdictPubId)
      ? { kind: 'ready', data: { verdictPubId } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createInvestigationAppeal(
  investigationPubId: string,
  body: AppealCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<AppealSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/intelligence/investigations/{investigation_pub_id}/appeals',
      {
        params: { path: { investigation_pub_id: investigationPubId }, header: headers },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const response = result.data as AppealCreateResponse;
    const appealPubId = safeBrowserString(response.appeal_pub_id, 120);
    return appealPubId && /^apl_[A-Za-z0-9_-]{1,116}$/.test(appealPubId)
      ? { kind: 'ready', data: { appealPubId } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function resolveInvestigationAppeal(
  investigationPubId: string,
  appealPubId: string,
  body: AppealResolution,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<AppealResolutionSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/intelligence/investigations/{investigation_pub_id}/appeals/{appeal_pub_id}/resolve',
      {
        params: {
          path: {
            investigation_pub_id: investigationPubId,
            appeal_pub_id: appealPubId,
          },
          header: headers,
        },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const response = result.data as AppealResolutionResponse;
    const correctedVerdict = body.corrected_verdict ?? null;
    const rawReplacementVerdictPubId = response.replacement_verdict_pub_id;
    const replacementVerdictPubId =
      rawReplacementVerdictPubId === null
        ? null
        : safeBrowserString(rawReplacementVerdictPubId, 120);
    const semanticallyCorrected = body.resolution === 'corrected';
    return semanticallyCorrected === (correctedVerdict !== null) &&
      (correctedVerdict === null
        ? rawReplacementVerdictPubId === null
        : replacementVerdictPubId !== null &&
          /^vrd_[A-Za-z0-9_-]{1,116}$/.test(replacementVerdictPubId))
      ? { kind: 'ready', data: { replacementVerdictPubId } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function registerCustomerAccount(
  body: CustomerAccountCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<CustomerAccountView>> {
  try {
    const result = await projectedApiClient(client).POST('/api/v2/customer/platform-accounts', {
      params: { header: headers },
      body,
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectCustomerAccountWriteView(result.data);
    return projected && customerAccountRegistrationMatches(projected, body)
      ? { kind: 'ready', data: projected }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function authorizeCustomerAccount(
  accountPubId: string,
  body: CustomerAuthorizationCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<CustomerAccountView>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/customer/platform-accounts/{account_pub_id}/authorizations',
      {
        params: { path: { account_pub_id: accountPubId }, header: headers },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectCustomerAccountWriteView(result.data, accountPubId);
    return projected && customerAuthorizationWriteMatches(projected, accountPubId, body)
      ? { kind: 'ready', data: projected }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createCustomerPairing(
  accountPubId: string,
  body: CustomerPairingCreate,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<CustomerPairingView>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/customer/platform-accounts/{account_pub_id}/pairings',
      {
        params: { path: { account_pub_id: accountPubId }, header: headers },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectCustomerPairingWriteView(result.data, accountPubId);
    return projected && customerPairingWriteMatches(projected, body)
      ? { kind: 'ready', data: projected }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listCustomerPairings(
  accountPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ProjectedCollection<CustomerPairingView>>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/customer/platform-accounts/{account_pub_id}/pairings',
      { params: { path: { account_pub_id: accountPubId }, header: headers } },
    );
    return result.data
      ? {
          kind: 'ready',
          data: projectBoundedCollection(
            result.data,
            customerAccountLifecycleProjectionLimits.pairings,
            (value) => projectCustomerPairingView(value, accountPubId),
          ),
        }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listCustomerAccountEvents(
  accountPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ProjectedCollection<CustomerEventView>>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/customer/platform-accounts/{account_pub_id}/events',
      {
        params: { path: { account_pub_id: accountPubId }, header: headers },
      },
    );
    return result.data
      ? {
          kind: 'ready',
          data: projectBoundedCollection(
            result.data,
            customerAccountLifecycleProjectionLimits.events,
            projectCustomerEventView,
          ),
        }
      : classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function revokeCustomerAccount(
  accountPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<CustomerRevocationSafeReceipt>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/customer/platform-accounts/{account_pub_id}/revoke',
      {
        params: { path: { account_pub_id: accountPubId }, header: headers },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const tenantPubId = safeBrowserString(headers['X-Tenant-Id'], 120);
    const safeAccountPubId = safeBrowserString(accountPubId, 120);
    const workflowId = safeBrowserString(result.data.workflow_id, 500);
    return tenantPubId &&
      safeAccountPubId &&
      /^tnt_[A-Za-z0-9_-]{1,116}$/.test(tenantPubId) &&
      /^pac_[A-Za-z0-9_-]{1,116}$/.test(safeAccountPubId) &&
      workflowId === `account-revocation/${tenantPubId}/${safeAccountPubId}`
      ? { kind: 'ready', data: { accepted: true } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export type MediaPricesPlatform =
  | 'prfabu'
  | 'toumeiw'
  | 'mtpfw'
  | 'meititejia'
  | 'meijiehezi'
  | 'pinda';

export type MediaPricesDatasetRow = {
  name: string;
  prices: Partial<Record<MediaPricesPlatform, number>>;
  ids?: Partial<Record<MediaPricesPlatform, string>>;
  best: number | null;
  best_plat: MediaPricesPlatform | null;
  spread: number | null;
  n_src: number;
  geo: string[];
  geo_n: number;
  portal?: string;
  channel?: string;
  include?: string;
  news_src?: string;
  speed?: string;
  pc_w?: number;
  m_w?: number;
  pub_rate?: number;
  province?: string;
  remark?: string;
  case?: string;
  site?: string;
  whitelist?: boolean;
  ai_rate?: number;
};

export type MediaPricesDatasetStats = {
  counts: Record<string, number>;
  geo_counts: Record<string, number>;
  unique_media: number;
  matched_2plus: number;
  matched_3: number;
  geo_union: number;
  geo_multi_src: number;
  whitelist?: number;
};

export type MediaPricesDataset = {
  generatedAt: string;
  sources: Record<string, string>;
  partial: Record<string, boolean>;
  stats: MediaPricesDatasetStats;
  rows: MediaPricesDatasetRow[];
  sha256: string | null;
};

export const mediaPricesDatasetMaxBytes = geoApiJsonResponseMaxBytes;

const mediaPricesPlatforms = [
  'prfabu',
  'toumeiw',
  'mtpfw',
  'meititejia',
  'meijiehezi',
  'pinda',
] as const;
const mediaPricesGeoKeys = new Set(['a', 'b', 'c', 'd', 'e', 'f', 'z']);

function projectMediaPricesCountMap(value: unknown): Record<string, number> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const projected: Record<string, number> = {};
  for (const platform of mediaPricesPlatforms) {
    const entry = record[platform];
    if (entry === undefined) continue;
    if (typeof entry !== 'number' || !Number.isSafeInteger(entry) || entry < 0 || entry > 200_000) {
      return null;
    }
    projected[platform] = entry;
  }
  return projected;
}

function projectMediaPricesDatasetStats(value: unknown): MediaPricesDatasetStats | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const counts = projectMediaPricesCountMap(record.counts);
  const geoCounts = projectMediaPricesCountMap(record.geo_counts);
  if (!counts || !geoCounts) return null;
  const projectedCounts: Record<
    'unique_media' | 'matched_2plus' | 'matched_3' | 'geo_union' | 'geo_multi_src',
    number
  > = {
    unique_media: 0,
    matched_2plus: 0,
    matched_3: 0,
    geo_union: 0,
    geo_multi_src: 0,
  };
  for (const key of [
    'unique_media',
    'matched_2plus',
    'matched_3',
    'geo_union',
    'geo_multi_src',
  ] as const) {
    const item = record[key];
    if (typeof item !== 'number' || !Number.isSafeInteger(item) || item < 0 || item > 200_000) {
      return null;
    }
    projectedCounts[key] = item;
  }
  // whitelist 为可选扩展（稿源单位名单命中数）：存在时必须是合法非负整数
  let whitelist: number | undefined;
  if (record.whitelist !== undefined) {
    const item = record.whitelist;
    if (typeof item !== 'number' || !Number.isSafeInteger(item) || item < 0 || item > 200_000) {
      return null;
    }
    whitelist = item;
  }
  return {
    counts,
    geo_counts: geoCounts,
    ...projectedCounts,
    ...(whitelist === undefined ? {} : { whitelist }),
  };
}

function projectMediaPricesStringMap(value: unknown): Record<string, string> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const projected: Record<string, string> = {};
  for (const platform of mediaPricesPlatforms) {
    const entry = record[platform];
    if (entry === undefined) continue;
    const safeEntry = safeBrowserString(entry, 120);
    if (!safeEntry) return null;
    projected[platform] = safeEntry;
  }
  return projected;
}

function projectMediaPricesBooleanMap(value: unknown): Record<string, boolean> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const projected: Record<string, boolean> = {};
  for (const platform of mediaPricesPlatforms) {
    const entry = record[platform];
    if (entry === undefined) continue;
    if (typeof entry !== 'boolean') return null;
    projected[platform] = entry;
  }
  return projected;
}

function projectMediaPricesNumber(
  value: unknown,
  { integer = false, maximum = 1_000_000_000 }: { integer?: boolean; maximum?: number } = {},
): number | null {
  return typeof value === 'number' &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= maximum &&
    (!integer || Number.isSafeInteger(value))
    ? value
    : null;
}

function projectMediaPricesNullableNumber(value: unknown): number | null | undefined {
  if (value === null) return null;
  const projected = projectMediaPricesNumber(value);
  return projected === null ? undefined : projected;
}

function projectMediaPricesIds(
  value: unknown,
): Partial<Record<MediaPricesPlatform, string>> | null {
  if (value === undefined || value === null) return {};
  if (typeof value !== 'object' || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const projected: Partial<Record<MediaPricesPlatform, string>> = {};
  for (const platform of mediaPricesPlatforms) {
    const entry = record[platform];
    if (entry === undefined || entry === null || entry === '') continue;
    if ((typeof entry !== 'string' && typeof entry !== 'number') || typeof entry === 'boolean') {
      return null;
    }
    const text = String(entry);
    if (!/^[A-Za-z0-9_-]{1,120}$/u.test(text)) return null;
    projected[platform] = text;
  }
  return projected;
}

function projectMediaPricesOptionalString(
  value: unknown,
  maximumLength: number,
): string | undefined {
  if (value === null || value === undefined || value === '') return undefined;
  return safeBrowserString(value, maximumLength) ?? undefined;
}

/**
 * Optional outbound reference link from the source platform (case/site).
 * Only http(s) navigation URLs without embedded credentials pass; anything
 * else is silently dropped like other optional fields.
 */
function projectMediaPricesLink(value: unknown): string | undefined {
  if (value === null || value === undefined || value === '') return undefined;
  const candidate = safeBrowserString(value, 500);
  if (!candidate) return undefined;
  try {
    const parsed = new URL(candidate);
    return (parsed.protocol === 'https:' || parsed.protocol === 'http:') &&
      !parsed.username &&
      !parsed.password
      ? parsed.toString()
      : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Validates the offline-built media-price comparison dataset envelope.
 * Every returned object is a bounded allow-listed projection. Unknown fields
 * (including deliberately injected secret fields) never enter React state,
 * query caches, exports, URLs, telemetry or diagnostics.
 */
export function projectMediaPricesDataset(
  value: unknown,
  sha256: string | null,
): MediaPricesDataset | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const generatedAt = safeBrowserString(record.generated_at, 64);
  if (!generatedAt) return null;
  const stats = projectMediaPricesDatasetStats(record.stats);
  if (!stats) return null;
  const sources = projectMediaPricesStringMap(record.sources);
  const partial = projectMediaPricesBooleanMap(record.partial);
  if (!sources || !partial) return null;
  if (
    mediaPricesPlatforms.some(
      (platform) =>
        sources[platform] === undefined ||
        stats.counts[platform] === undefined ||
        stats.geo_counts[platform] === undefined,
    )
  ) {
    return null;
  }
  if (!Array.isArray(record.rows) || record.rows.length > 200_000) {
    return null;
  }
  const rows: MediaPricesDatasetRow[] = [];
  const seenNames = new Set<string>();
  const rowsWithPlatformPrice = Object.fromEntries(
    mediaPricesPlatforms.map((platform) => [platform, 0]),
  ) as Record<MediaPricesPlatform, number>;
  let matchedTwoPlus = 0;
  let matchedThree = 0;
  let geoUnion = 0;
  let geoMultiSource = 0;
  let whitelistRows = 0;
  for (const item of record.rows) {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) return null;
    const row = item as Record<string, unknown>;
    const name = safeBrowserString(row.name, 500);
    if (!name || seenNames.has(name)) return null;
    seenNames.add(name);
    if (typeof row.prices !== 'object' || row.prices === null || Array.isArray(row.prices)) {
      return null;
    }
    const prices: Partial<Record<MediaPricesPlatform, number>> = {};
    const ids = projectMediaPricesIds(row.ids);
    if (ids === null) return null;
    for (const platform of mediaPricesPlatforms) {
      const price = (row.prices as Record<string, unknown>)[platform];
      if (price === undefined || price === null) continue;
      const projectedPrice = projectMediaPricesNumber(price);
      if (projectedPrice === null || projectedPrice <= 0) return null;
      prices[platform] = projectedPrice;
      rowsWithPlatformPrice[platform] += 1;
    }
    const best = projectMediaPricesNullableNumber(row.best);
    const spread = projectMediaPricesNullableNumber(row.spread);
    if (best === undefined || spread === undefined) return null;
    const bestPlatform =
      row.best_plat === null
        ? null
        : mediaPricesPlatforms.find((platform) => platform === row.best_plat);
    if (bestPlatform === undefined) return null;
    const sourceCount = projectMediaPricesNumber(row.n_src, {
      integer: true,
      maximum: mediaPricesPlatforms.length,
    });
    const geoCount = projectMediaPricesNumber(row.geo_n, {
      integer: true,
      maximum: mediaPricesPlatforms.length,
    });
    if (
      sourceCount === null ||
      geoCount === null ||
      !Array.isArray(row.geo) ||
      row.geo.length > 7
    ) {
      return null;
    }
    const geo: string[] = [];
    for (const item of row.geo) {
      if (typeof item !== 'string' || !mediaPricesGeoKeys.has(item) || geo.includes(item)) {
        return null;
      }
      geo.push(item);
    }
    const priceEntries = mediaPricesPlatforms.flatMap((platform) => {
      const price = prices[platform];
      return price === undefined ? [] : [{ platform, price }];
    });
    const expectedBest =
      priceEntries.length === 0 ? null : Math.min(...priceEntries.map((entry) => entry.price));
    const expectedBestPlatform =
      expectedBest === null
        ? null
        : (priceEntries.find((entry) => entry.price === expectedBest)?.platform ?? null);
    const spreadRatio =
      expectedBest !== null && priceEntries.length > 1
        ? Math.max(...priceEntries.map((entry) => entry.price)) / expectedBest
        : null;
    const spreadIsConsistent =
      spreadRatio === null
        ? spread === null
        : spread !== null &&
          Math.abs(spread * 10 - Math.round(spread * 10)) < Number.EPSILON * 100 &&
          Math.abs(spread - spreadRatio) <= 0.050000001;
    if (
      sourceCount !== priceEntries.length ||
      best !== expectedBest ||
      bestPlatform !== expectedBestPlatform ||
      !spreadIsConsistent ||
      (geo.length === 0) !== (geoCount === 0)
    ) {
      return null;
    }
    const optionalStrings = {
      portal: projectMediaPricesOptionalString(row.portal, 160),
      channel: projectMediaPricesOptionalString(row.channel, 160),
      include: projectMediaPricesOptionalString(row.include, 160),
      news_src: projectMediaPricesOptionalString(row.news_src, 160),
      speed: projectMediaPricesOptionalString(row.speed, 160),
      province: projectMediaPricesOptionalString(row.province, 160),
      remark: projectMediaPricesOptionalString(row.remark, 1_000),
    };
    const optionalLinks = {
      case: projectMediaPricesLink(row.case),
      site: projectMediaPricesLink(row.site),
    };
    const optionalNumbers = {
      pc_w:
        row.pc_w === null || row.pc_w === undefined
          ? undefined
          : projectMediaPricesNumber(row.pc_w),
      m_w:
        row.m_w === null || row.m_w === undefined ? undefined : projectMediaPricesNumber(row.m_w),
      pub_rate:
        row.pub_rate === null || row.pub_rate === undefined
          ? undefined
          : projectMediaPricesNumber(row.pub_rate),
      ai_rate:
        row.ai_rate === null || row.ai_rate === undefined
          ? undefined
          : projectMediaPricesNumber(row.ai_rate),
    };
    if (Object.values(optionalNumbers).some((entry) => entry === null)) return null;
    rows.push({
      name,
      prices,
      ids,
      best,
      best_plat: bestPlatform,
      spread,
      n_src: sourceCount,
      geo,
      geo_n: geoCount,
      ...Object.fromEntries(
        Object.entries(optionalStrings).filter((entry): entry is [string, string] =>
          Boolean(entry[1]),
        ),
      ),
      ...Object.fromEntries(
        Object.entries(optionalLinks).filter((entry): entry is [string, string] =>
          Boolean(entry[1]),
        ),
      ),
      ...Object.fromEntries(
        Object.entries(optionalNumbers).filter(
          (entry): entry is [string, number] => entry[1] !== undefined,
        ),
      ),
      ...(row.whitelist === true ? { whitelist: true } : {}),
    });
    if (row.whitelist === true) whitelistRows += 1;
    if (sourceCount >= 2) matchedTwoPlus += 1;
    if (sourceCount === mediaPricesPlatforms.length) matchedThree += 1;
    if (geo.length > 0) geoUnion += 1;
    if (geoCount >= 2) geoMultiSource += 1;
  }
  if (
    stats.unique_media !== rows.length ||
    stats.matched_2plus !== matchedTwoPlus ||
    stats.matched_3 !== matchedThree ||
    stats.geo_union !== geoUnion ||
    stats.geo_multi_src !== geoMultiSource ||
    (stats.whitelist !== undefined && stats.whitelist !== whitelistRows) ||
    mediaPricesPlatforms.some(
      (platform) => stats.counts[platform]! < rowsWithPlatformPrice[platform],
    )
  ) {
    return null;
  }
  if (sha256 !== null && !/^[0-9a-f]{64}$/.test(sha256)) return null;
  return {
    generatedAt,
    sources,
    partial,
    stats,
    rows,
    sha256,
  };
}

export type MediaPricesDatasetResult =
  | { kind: 'ready'; data: MediaPricesDataset }
  | { kind: 'forbidden' }
  | { kind: 'missing' }
  | { kind: 'unavailable' };

export async function getMediaPricesDataset(
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<MediaPricesDatasetResult> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/datasets/media-prices', {
      params: { header: headers },
      parseAs: 'arrayBuffer',
    });
    if (!result.data) {
      return result.response.status === 404
        ? { kind: 'missing' }
        : classifyResourceFailure(result.response.status);
    }
    const contentType = (result.response.headers.get('content-type') ?? '').toLowerCase();
    if (!(result.data instanceof ArrayBuffer) || !contentType.includes('application/json')) {
      return { kind: 'unavailable' };
    }
    if (result.data.byteLength <= 0 || result.data.byteLength > mediaPricesDatasetMaxBytes) {
      return { kind: 'unavailable' };
    }
    const shaHeader = result.response.headers.get('x-dataset-sha256');
    if (!shaHeader || !/^[0-9a-f]{64}$/.test(shaHeader)) {
      return { kind: 'unavailable' };
    }
    const digest = await globalThis.crypto.subtle.digest('SHA-256', result.data);
    const sha256 = [...new Uint8Array(digest)]
      .map((item) => item.toString(16).padStart(2, '0'))
      .join('');
    if (sha256 !== shaHeader) return { kind: 'unavailable' };
    let parsed: unknown;
    try {
      parsed = JSON.parse(new TextDecoder().decode(result.data));
    } catch {
      return { kind: 'unavailable' };
    }
    const projected = projectMediaPricesDataset(parsed, sha256);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export type MediaWemediaDatasetRow = {
  name: string;
  platform: string;
  prices: Partial<Record<MediaPricesPlatform, number>>;
  ids?: Partial<Record<MediaPricesPlatform, string>>;
  best: number | null;
  best_plat: MediaPricesPlatform | null;
  spread: number | null;
  n_src: number;
  geo: string[];
  geo_n: number;
  industry?: string;
  account_auth?: string;
  fans?: string;
  reads?: string;
  fans_level?: number;
  reads_level?: number;
  authority?: string;
  can_video?: string;
  weekend?: string;
  province?: string;
  pub_rate?: number;
  ai_rate?: number;
  remark?: string;
  case?: string;
  site?: string;
};

export type MediaWemediaDataset = {
  generatedAt: string;
  sources: Record<string, string>;
  partial: Record<string, boolean>;
  stats: MediaPricesDatasetStats;
  rows: MediaWemediaDatasetRow[];
  sha256: string | null;
};

export function projectMediaWemediaDataset(
  value: unknown,
  sha256: string | null,
): MediaWemediaDataset | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const generatedAt = safeBrowserString(record.generated_at, 64);
  const stats = projectMediaPricesDatasetStats(record.stats);
  const sources = projectMediaPricesStringMap(record.sources);
  const partial = projectMediaPricesBooleanMap(record.partial);
  if (!generatedAt || !stats || !sources || !partial) return null;
  if (
    mediaPricesPlatforms.some(
      (platform) =>
        sources[platform] === undefined ||
        stats.counts[platform] === undefined ||
        stats.geo_counts[platform] === undefined,
    ) ||
    !Array.isArray(record.rows) ||
    record.rows.length > 200_000
  ) {
    return null;
  }

  const rows: MediaWemediaDatasetRow[] = [];
  const seenAccounts = new Set<string>();
  const rowsWithPlatformPrice = Object.fromEntries(
    mediaPricesPlatforms.map((platform) => [platform, 0]),
  ) as Record<MediaPricesPlatform, number>;
  let matchedTwoPlus = 0;
  let matchedAll = 0;
  let geoUnion = 0;
  let geoMultiSource = 0;
  for (const item of record.rows) {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) return null;
    const row = item as Record<string, unknown>;
    const name = safeBrowserString(row.name, 500);
    const platform = safeBrowserString(row.platform, 160);
    if (!name || !platform) return null;
    const accountKey = `${platform}\u0000${name}`;
    if (seenAccounts.has(accountKey)) return null;
    seenAccounts.add(accountKey);
    if (typeof row.prices !== 'object' || row.prices === null || Array.isArray(row.prices)) {
      return null;
    }
    const prices: Partial<Record<MediaPricesPlatform, number>> = {};
    const ids = projectMediaPricesIds(row.ids);
    if (ids === null) return null;
    for (const supplier of mediaPricesPlatforms) {
      const price = (row.prices as Record<string, unknown>)[supplier];
      if (price === undefined || price === null) continue;
      const projectedPrice = projectMediaPricesNumber(price);
      if (projectedPrice === null || projectedPrice <= 0) return null;
      prices[supplier] = projectedPrice;
      rowsWithPlatformPrice[supplier] += 1;
    }
    const entries = mediaPricesPlatforms.flatMap((supplier) => {
      const price = prices[supplier];
      return price === undefined ? [] : [{ supplier, price }];
    });
    const expectedBest =
      entries.length === 0 ? null : Math.min(...entries.map((entry) => entry.price));
    const expectedBestPlatform =
      expectedBest === null
        ? null
        : (entries.find((entry) => entry.price === expectedBest)?.supplier ?? null);
    const best = projectMediaPricesNullableNumber(row.best);
    const spread = projectMediaPricesNullableNumber(row.spread);
    const bestPlatform =
      row.best_plat === null
        ? null
        : mediaPricesPlatforms.find((supplier) => supplier === row.best_plat);
    const sourceCount = projectMediaPricesNumber(row.n_src, {
      integer: true,
      maximum: mediaPricesPlatforms.length,
    });
    const geoCount = projectMediaPricesNumber(row.geo_n, {
      integer: true,
      maximum: mediaPricesPlatforms.length,
    });
    if (
      best === undefined ||
      spread === undefined ||
      bestPlatform === undefined ||
      sourceCount === null ||
      geoCount === null ||
      !Array.isArray(row.geo) ||
      row.geo.length > mediaPricesGeoKeys.size
    ) {
      return null;
    }
    const expectedSpread =
      expectedBest !== null && entries.length > 1
        ? Math.max(...entries.map((entry) => entry.price)) / expectedBest
        : null;
    if (
      sourceCount !== entries.length ||
      best !== expectedBest ||
      bestPlatform !== expectedBestPlatform ||
      (expectedSpread === null
        ? spread !== null
        : spread === null || Math.abs(spread - expectedSpread) > 0.050000001)
    ) {
      return null;
    }
    const geo: string[] = [];
    for (const tag of row.geo) {
      if (typeof tag !== 'string' || !mediaPricesGeoKeys.has(tag) || geo.includes(tag)) {
        return null;
      }
      geo.push(tag);
    }
    if ((geo.length === 0) !== (geoCount === 0)) return null;

    const strings = {
      industry: projectMediaPricesOptionalString(row.industry, 160),
      account_auth: projectMediaPricesOptionalString(row.account_auth, 160),
      fans: projectMediaPricesOptionalString(row.fans, 160),
      reads: projectMediaPricesOptionalString(row.reads, 160),
      authority: projectMediaPricesOptionalString(row.authority, 160),
      can_video: projectMediaPricesOptionalString(row.can_video, 160),
      weekend: projectMediaPricesOptionalString(row.weekend, 160),
      province: projectMediaPricesOptionalString(row.province, 160),
      remark: projectMediaPricesOptionalString(row.remark, 300),
    };
    const numbers = {
      fans_level:
        row.fans_level === null || row.fans_level === undefined
          ? undefined
          : projectMediaPricesNumber(row.fans_level),
      reads_level:
        row.reads_level === null || row.reads_level === undefined
          ? undefined
          : projectMediaPricesNumber(row.reads_level),
      pub_rate:
        row.pub_rate === null || row.pub_rate === undefined
          ? undefined
          : projectMediaPricesNumber(row.pub_rate),
      ai_rate:
        row.ai_rate === null || row.ai_rate === undefined
          ? undefined
          : projectMediaPricesNumber(row.ai_rate),
    };
    if (Object.values(numbers).some((entry) => entry === null)) return null;
    const caseLink = projectMediaPricesLink(row.case);
    const siteLink = projectMediaPricesLink(row.site);
    rows.push({
      name,
      platform,
      prices,
      ids,
      best,
      best_plat: bestPlatform,
      spread,
      n_src: sourceCount,
      geo,
      geo_n: geoCount,
      ...Object.fromEntries(
        Object.entries(strings).filter((entry): entry is [string, string] => Boolean(entry[1])),
      ),
      ...Object.fromEntries(
        Object.entries(numbers).filter(
          (entry): entry is [string, number] => entry[1] !== undefined,
        ),
      ),
      ...(caseLink ? { case: caseLink } : {}),
      ...(siteLink ? { site: siteLink } : {}),
    });
    if (sourceCount >= 2) matchedTwoPlus += 1;
    if (sourceCount === mediaPricesPlatforms.length) matchedAll += 1;
    if (geo.length > 0) geoUnion += 1;
    if (geoCount >= 2) geoMultiSource += 1;
  }
  if (
    stats.unique_media !== rows.length ||
    stats.matched_2plus !== matchedTwoPlus ||
    stats.matched_3 !== matchedAll ||
    stats.geo_union !== geoUnion ||
    stats.geo_multi_src !== geoMultiSource ||
    mediaPricesPlatforms.some(
      (platform) => stats.counts[platform]! < rowsWithPlatformPrice[platform],
    ) ||
    (sha256 !== null && !/^[0-9a-f]{64}$/.test(sha256))
  ) {
    return null;
  }
  return { generatedAt, sources, partial, stats, rows, sha256 };
}

export async function getMediaWemediaDataset(
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<
  | { kind: 'ready'; data: MediaWemediaDataset }
  | { kind: 'forbidden' }
  | { kind: 'missing' }
  | { kind: 'unavailable' }
> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/datasets/media-wemedia', {
      params: { header: headers },
      parseAs: 'arrayBuffer',
    });
    if (!result.data) {
      return result.response.status === 404
        ? { kind: 'missing' }
        : classifyResourceFailure(result.response.status);
    }
    const contentType = (result.response.headers.get('content-type') ?? '').toLowerCase();
    if (
      !(result.data instanceof ArrayBuffer) ||
      !contentType.includes('application/json') ||
      result.data.byteLength <= 0 ||
      result.data.byteLength > mediaWemediaDatasetMaxBytes
    ) {
      return { kind: 'unavailable' };
    }
    const shaHeader = result.response.headers.get('x-dataset-sha256');
    if (!shaHeader || !/^[0-9a-f]{64}$/.test(shaHeader)) return { kind: 'unavailable' };
    const digest = await globalThis.crypto.subtle.digest('SHA-256', result.data);
    const sha256 = [...new Uint8Array(digest)]
      .map((item) => item.toString(16).padStart(2, '0'))
      .join('');
    if (sha256 !== shaHeader) return { kind: 'unavailable' };
    let parsed: unknown;
    try {
      parsed = JSON.parse(new TextDecoder().decode(result.data));
    } catch {
      return { kind: 'unavailable' };
    }
    const projected = projectMediaWemediaDataset(parsed, sha256);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export type MediaPricesRefreshSource = {
  status: 'ok' | 'partial' | 'stale' | 'failed' | 'pending';
  rows: number;
  note: string;
};

export type MediaPricesRefreshStatus = {
  state: 'never' | 'running' | 'done' | 'failed';
  startedAt: string | null;
  updatedAt: string | null;
  message: string;
  sources: Record<string, MediaPricesRefreshSource>;
};

const mediaPricesRefreshSourceStatuses = new Set(['ok', 'partial', 'stale', 'failed', 'pending']);

function projectMediaPricesRefreshText(
  value: unknown,
  maximumLength: number,
  defaultValue?: string,
): string | null {
  if (value === undefined && defaultValue !== undefined) return defaultValue;
  if (value === '') return '';
  return safeBrowserString(value, maximumLength);
}

function projectMediaPricesRefreshTimestamp(value: unknown): string | null | undefined {
  if (value === undefined || value === null) return null;
  const candidate = safeBrowserString(value, 64);
  if (!candidate) return undefined;
  const match =
    /^(?<year>\d{4})-(?<month>\d{2})-(?<day>\d{2}) (?<hour>\d{2}):(?<minute>\d{2}):(?<second>\d{2})$/u.exec(
      candidate,
    );
  if (!match?.groups) return undefined;
  const year = Number(match.groups.year);
  const month = Number(match.groups.month);
  const day = Number(match.groups.day);
  const hour = Number(match.groups.hour);
  const minute = Number(match.groups.minute);
  const second = Number(match.groups.second);
  const instant = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  return instant.getUTCFullYear() === year &&
    instant.getUTCMonth() === month - 1 &&
    instant.getUTCDate() === day &&
    instant.getUTCHours() === hour &&
    instant.getUTCMinutes() === minute &&
    instant.getUTCSeconds() === second
    ? candidate
    : undefined;
}

function projectMediaPricesRefreshEnvelope(
  value: unknown,
  mode: 'authoritative-status' | 'accepted-start',
): MediaPricesRefreshStatus | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const state = record.state;
  if (state !== 'never' && state !== 'running' && state !== 'done' && state !== 'failed') {
    return null;
  }
  const startedAt = projectMediaPricesRefreshTimestamp(record.started_at);
  const updatedAt = projectMediaPricesRefreshTimestamp(record.updated_at);
  const message = projectMediaPricesRefreshText(record.message, 300, '');
  if (startedAt === undefined || updatedAt === undefined || message === null) return null;
  const sources: Record<string, MediaPricesRefreshSource> = {};
  const rawSources = record.sources;
  if (rawSources !== undefined) {
    if (typeof rawSources !== 'object' || rawSources === null || Array.isArray(rawSources)) {
      return null;
    }
    if (Object.keys(rawSources).length > mediaPricesPlatforms.length) return null;
    for (const [name, item] of Object.entries(rawSources)) {
      if (!mediaPricesPlatforms.includes(name as MediaPricesPlatform)) return null;
      if (typeof item !== 'object' || item === null || Array.isArray(item)) return null;
      const source = item as Record<string, unknown>;
      const status = source.status;
      const note = projectMediaPricesRefreshText(source.note, 200);
      if (
        typeof status !== 'string' ||
        !mediaPricesRefreshSourceStatuses.has(status) ||
        typeof source.rows !== 'number' ||
        !Number.isSafeInteger(source.rows) ||
        source.rows < 0 ||
        source.rows > 200_000 ||
        note === null ||
        (status === 'pending' && (source.rows !== 0 || note !== ''))
      ) {
        return null;
      }
      sources[name] = {
        status: status as MediaPricesRefreshSource['status'],
        rows: source.rows,
        note,
      };
    }
  }
  const sourceSetIsComplete =
    Object.keys(sources).length === mediaPricesPlatforms.length &&
    mediaPricesPlatforms.every((platform) => sources[platform] !== undefined);
  if (mode === 'accepted-start') {
    if (
      state !== 'running' ||
      startedAt !== null ||
      updatedAt !== null ||
      message.length === 0 ||
      Object.keys(sources).length !== 0
    ) {
      return null;
    }
  } else if (state === 'never') {
    if (
      startedAt !== null ||
      updatedAt !== null ||
      message !== '' ||
      Object.keys(sources).length !== 0
    ) {
      return null;
    }
  } else {
    if (
      startedAt === null ||
      updatedAt === null ||
      startedAt > updatedAt ||
      message.length === 0 ||
      !sourceSetIsComplete ||
      (state === 'done' &&
        mediaPricesPlatforms.some((platform) => sources[platform]?.status === 'pending'))
    ) {
      return null;
    }
  }
  return {
    state,
    startedAt,
    updatedAt,
    message,
    sources,
  };
}

export function projectMediaPricesRefreshStatus(value: unknown): MediaPricesRefreshStatus | null {
  return projectMediaPricesRefreshEnvelope(value, 'authoritative-status');
}

export async function getMediaPricesRefreshStatus(
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<MediaPricesRefreshStatus>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/datasets/media-prices/refresh-status',
      {
        params: { header: headers },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectMediaPricesRefreshStatus(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export type MediaPricesRefreshRequestResult =
  | { kind: 'started' }
  | { kind: 'already_running' }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };

export async function requestMediaPricesRefresh(
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<MediaPricesRefreshRequestResult> {
  try {
    const result = await projectedApiClient(client).POST('/api/v2/datasets/media-prices/refresh', {
      params: { header: headers },
    });
    if (result.response.status === 202 && result.data) {
      return projectMediaPricesRefreshEnvelope(result.data, 'accepted-start')?.state === 'running'
        ? { kind: 'started' }
        : { kind: 'unavailable' };
    }
    if (result.response.status === 409) return { kind: 'already_running' };
    return classifyResourceFailure(result.response.status);
  } catch {
    return { kind: 'unavailable' };
  }
}

// -- Paid media posting batches safe browser boundary ------------------------

export type PostingBatchStatus =
  | 'draft'
  | 'queued'
  | 'processing'
  | 'partially_submitted'
  | 'submitted'
  | 'published'
  | 'blocked'
  | 'failed'
  | 'canceled';

export type PostingTargetStatus =
  | 'selected'
  | 'queued'
  | 'submitting'
  | 'submitted'
  | 'reviewing'
  | 'published'
  | 'balance_insufficient'
  | 'provider_session_expired'
  | 'provider_confirmation_required'
  | 'unsupported_provider'
  | 'rejected'
  | 'failed'
  | 'canceled';

export type PostingApprovalState = 'draft' | 'pending' | 'approved' | 'rejected';

export type PostingTargetInput = {
  catalogType: 'news' | 'wemedia';
  provider: MediaPricesPlatform;
  mediaName: string;
  mediaPlatform?: string;
};

export type PostingBatchTarget = {
  pubId: string;
  catalogType: 'news' | 'wemedia';
  provider: MediaPricesPlatform;
  mediaName: string;
  mediaPlatform: string;
  quotedPrice: number;
  status: PostingTargetStatus;
  externalOrderId: string;
  publicUrl: string;
  providerMessage: string;
  submittedAt: string | null;
  publishedAt: string | null;
  updatedAt: string;
};

export type PostingBatchEvent = {
  pubId: string;
  targetPubId: string | null;
  eventType: string;
  fromStatus: string;
  toStatus: string;
  message: string;
  createdAt: string;
};

export type PostingBatch = {
  pubId: string;
  sourceFilename: string;
  sourceSha256: string;
  title: string;
  contentText: string;
  imageCount: number;
  customerName: string;
  releaseTime: string | null;
  autoSubmit: boolean;
  spendConfirmedAt: string | null;
  maxTotalAmount: number | null;
  quotedTotalAmount: number;
  status: PostingBatchStatus;
  note: string;
  sopProjectPubId: string | null;
  articleVersionPubId: string | null;
  approvalState: PostingApprovalState;
  approvalRequestedByPubId: string | null;
  approvedByPubId: string | null;
  approvedAt: string | null;
  createdAt: string;
  updatedAt: string;
  targets: PostingBatchTarget[];
  events: PostingBatchEvent[];
};

export type PostingBatchSummary = {
  pubId: string;
  sourceFilename: string;
  sourceSha256: string;
  title: string;
  imageCount: number;
  customerName: string;
  releaseTime: string | null;
  autoSubmit: boolean;
  spendConfirmedAt: string | null;
  maxTotalAmount: number | null;
  quotedTotalAmount: number;
  status: PostingBatchStatus;
  note: string;
  sopProjectPubId: string | null;
  articleVersionPubId: string | null;
  approvalState: PostingApprovalState;
  approvalRequestedByPubId: string | null;
  approvedByPubId: string | null;
  approvedAt: string | null;
  createdAt: string;
  updatedAt: string;
  contentExcerpt: string;
  targetCount: number;
  submittedCount: number;
  publishedCount: number;
};

export type CreatePostingBatchInput = {
  document: File;
  targets: PostingTargetInput[];
  title?: string;
  customerName?: string;
  releaseTime?: string;
  autoSubmit: boolean;
  confirmSpend: boolean;
  maxTotalAmount: number;
  note?: string;
  sopProjectPubId?: string;
  articleVersionPubId?: string;
  idempotencyKey: string;
};

const postingBatchStatuses = new Set<PostingBatchStatus>([
  'draft',
  'queued',
  'processing',
  'partially_submitted',
  'submitted',
  'published',
  'blocked',
  'failed',
  'canceled',
]);
const postingTargetStatuses = new Set<PostingTargetStatus>([
  'selected',
  'queued',
  'submitting',
  'submitted',
  'reviewing',
  'published',
  'balance_insufficient',
  'provider_session_expired',
  'provider_confirmation_required',
  'unsupported_provider',
  'rejected',
  'failed',
  'canceled',
]);
const postingApprovalStates = new Set<PostingApprovalState>([
  'draft',
  'pending',
  'approved',
  'rejected',
]);
const postingCatalogTypes = new Set(['news', 'wemedia']);

const safePostingText = (value: unknown, maximum: number, allowEmpty = true): string | null =>
  typeof value === 'string' &&
  value.length <= maximum &&
  (allowEmpty || value.length > 0) &&
  !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(value)
    ? value
    : null;

const safePostingTimestamp = (value: unknown): string | null | undefined => {
  if (value === null) return null;
  const text = safePostingText(value, 64, false);
  return text && !Number.isNaN(Date.parse(text)) ? text : undefined;
};

const safePostingAmount = (value: unknown, nullable = false): number | null | undefined => {
  if (nullable && value === null) return null;
  if (typeof value !== 'string' || !/^(?:0|[1-9]\d{0,9})(?:\.\d{1,2})?$/u.test(value)) {
    return undefined;
  }
  const amount = Number(value);
  return Number.isFinite(amount) && amount >= 0 && amount <= 1_000_000_000 ? amount : undefined;
};

const safePostingUrl = (value: unknown): string | null => {
  if (value === '') return '';
  const text = safePostingText(value, 1_000, false);
  if (!text) return null;
  try {
    const parsed = new URL(text);
    return ['https:', 'http:'].includes(parsed.protocol) && !parsed.username && !parsed.password
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
};

function projectPostingTarget(value: unknown): PostingBatchTarget | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const pubId = safePostingText(row.pub_id, 120, false);
  const provider = mediaPricesPlatforms.find((item) => item === row.provider);
  const catalogType =
    typeof row.catalog_type === 'string' && postingCatalogTypes.has(row.catalog_type)
      ? (row.catalog_type as 'news' | 'wemedia')
      : undefined;
  const mediaName = safePostingText(row.media_name, 500, false);
  const mediaPlatform = safePostingText(row.media_platform, 160);
  const quotedPrice = safePostingAmount(row.quoted_price);
  const status =
    typeof row.status === 'string' && postingTargetStatuses.has(row.status as PostingTargetStatus)
      ? (row.status as PostingTargetStatus)
      : undefined;
  const externalOrderId = safePostingText(row.external_order_id, 160);
  const publicUrl = safePostingUrl(row.public_url);
  const providerMessage = safePostingText(row.provider_message, 1_000);
  const submittedAt = safePostingTimestamp(row.submitted_at);
  const publishedAt = safePostingTimestamp(row.published_at);
  const updatedAt = safePostingTimestamp(row.updated_at);
  if (
    !pubId ||
    !provider ||
    !catalogType ||
    !mediaName ||
    mediaPlatform === null ||
    quotedPrice === undefined ||
    quotedPrice === null ||
    !status ||
    externalOrderId === null ||
    publicUrl === null ||
    providerMessage === null ||
    submittedAt === undefined ||
    publishedAt === undefined ||
    !updatedAt
  ) {
    return null;
  }
  return {
    pubId,
    catalogType,
    provider,
    mediaName,
    mediaPlatform,
    quotedPrice,
    status,
    externalOrderId,
    publicUrl,
    providerMessage,
    submittedAt,
    publishedAt,
    updatedAt,
  };
}

function projectPostingEvent(value: unknown): PostingBatchEvent | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const pubId = safePostingText(row.pub_id, 120, false);
  const targetPubId =
    row.target_pub_id === null
      ? null
      : (safePostingText(row.target_pub_id, 120, false) ?? undefined);
  const eventType = safePostingText(row.event_type, 160, false);
  const fromStatus = safePostingText(row.from_status, 80);
  const toStatus = safePostingText(row.to_status, 80);
  const message = safePostingText(row.message, 1_000);
  const createdAt = safePostingTimestamp(row.created_at);
  return pubId &&
    targetPubId !== undefined &&
    eventType &&
    fromStatus !== null &&
    toStatus !== null &&
    message !== null &&
    createdAt
    ? { pubId, targetPubId, eventType, fromStatus, toStatus, message, createdAt }
    : null;
}

function projectPostingBatch(value: unknown): PostingBatch | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const pubId = safePostingText(row.pub_id, 120, false);
  const sourceFilename = safePostingText(row.source_filename, 500, false);
  const sourceSha256 =
    typeof row.source_sha256 === 'string' && /^[0-9a-f]{64}$/u.test(row.source_sha256)
      ? row.source_sha256
      : null;
  const title = safePostingText(row.title, 300, false);
  const contentText = safePostingText(row.content_text, 800_000, false);
  const customerName = safePostingText(row.customer_name, 300);
  const releaseTime =
    row.release_time === null
      ? null
      : typeof row.release_time === 'string' && /^\d{4}-\d{2}-\d{2}$/u.test(row.release_time)
        ? row.release_time
        : undefined;
  const spendConfirmedAt = safePostingTimestamp(row.spend_confirmed_at);
  const maxTotalAmount = safePostingAmount(row.max_total_amount, true);
  const quotedTotalAmount = safePostingAmount(row.quoted_total_amount);
  const status =
    typeof row.status === 'string' && postingBatchStatuses.has(row.status as PostingBatchStatus)
      ? (row.status as PostingBatchStatus)
      : undefined;
  const note = safePostingText(row.note, 1_000);
  const sopProjectPubId =
    row.sop_project_pub_id === null
      ? null
      : (safePostingText(row.sop_project_pub_id, 120, false) ?? undefined);
  const articleVersionPubId =
    row.article_version_pub_id === null
      ? null
      : (safePostingText(row.article_version_pub_id, 120, false) ?? undefined);
  const approvalState =
    typeof row.approval_state === 'string' &&
    postingApprovalStates.has(row.approval_state as PostingApprovalState)
      ? (row.approval_state as PostingApprovalState)
      : undefined;
  const approvalRequestedByPubId =
    row.approval_requested_by_pub_id === null
      ? null
      : (safePostingText(row.approval_requested_by_pub_id, 120, false) ?? undefined);
  const approvedByPubId =
    row.approved_by_pub_id === null
      ? null
      : (safePostingText(row.approved_by_pub_id, 120, false) ?? undefined);
  const approvedAt = safePostingTimestamp(row.approved_at);
  const createdAt = safePostingTimestamp(row.created_at);
  const updatedAt = safePostingTimestamp(row.updated_at);
  if (!Array.isArray(row.targets) || !Array.isArray(row.events)) return null;
  const targets = row.targets.map(projectPostingTarget);
  const events = row.events.map(projectPostingEvent);
  if (
    !pubId ||
    !sourceFilename ||
    !sourceSha256 ||
    !title ||
    !contentText ||
    customerName === null ||
    releaseTime === undefined ||
    typeof row.image_count !== 'number' ||
    !Number.isSafeInteger(row.image_count) ||
    row.image_count < 0 ||
    row.image_count > 100 ||
    typeof row.auto_submit !== 'boolean' ||
    spendConfirmedAt === undefined ||
    maxTotalAmount === undefined ||
    quotedTotalAmount === undefined ||
    quotedTotalAmount === null ||
    !status ||
    note === null ||
    sopProjectPubId === undefined ||
    articleVersionPubId === undefined ||
    !approvalState ||
    approvalRequestedByPubId === undefined ||
    approvedByPubId === undefined ||
    approvedAt === undefined ||
    !createdAt ||
    !updatedAt ||
    targets.some((item) => item === null) ||
    events.some((item) => item === null)
  ) {
    return null;
  }
  return {
    pubId,
    sourceFilename,
    sourceSha256,
    title,
    contentText,
    imageCount: row.image_count,
    customerName,
    releaseTime,
    autoSubmit: row.auto_submit,
    spendConfirmedAt,
    maxTotalAmount,
    quotedTotalAmount,
    status,
    note,
    sopProjectPubId,
    articleVersionPubId,
    approvalState,
    approvalRequestedByPubId,
    approvedByPubId,
    approvedAt,
    createdAt,
    updatedAt,
    targets: targets as PostingBatchTarget[],
    events: events as PostingBatchEvent[],
  };
}

function projectPostingBatchSummary(value: unknown): PostingBatchSummary | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const detailShape = {
    ...row,
    content_text: row.content_excerpt,
    targets: [],
    events: [],
  };
  const batch = projectPostingBatch(detailShape);
  const contentExcerpt = safePostingText(row.content_excerpt, 300);
  const counts = [row.target_count, row.submitted_count, row.published_count];
  if (
    !batch ||
    contentExcerpt === null ||
    counts.some((count) => typeof count !== 'number' || !Number.isSafeInteger(count) || count < 0)
  ) {
    return null;
  }
  const { contentText: _contentText, targets: _targets, events: _events, ...summary } = batch;
  return {
    ...summary,
    contentExcerpt,
    targetCount: row.target_count as number,
    submittedCount: row.submitted_count as number,
    publishedCount: row.published_count as number,
  };
}

export type PostingResourceResult<T> =
  | { kind: 'ready'; data: T }
  | { kind: 'forbidden' }
  | { kind: 'conflict' }
  | { kind: 'invalid' }
  | { kind: 'unavailable' };

function classifyPostingFailure(status: number): PostingResourceResult<never> {
  if (status === 401 || status === 403) return { kind: 'forbidden' };
  if (status === 409) return { kind: 'conflict' };
  if (status === 415 || status === 422) return { kind: 'invalid' };
  return { kind: 'unavailable' };
}

export async function listPostingBatches(
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<PostingResourceResult<PostingBatchSummary[]>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/posting/batches', {
      params: { header: headers, query: { limit: 20 } },
    });
    if (!result.data) return classifyPostingFailure(result.response.status);
    const projected = result.data.map(projectPostingBatchSummary);
    return projected.some((item) => item === null)
      ? { kind: 'unavailable' }
      : { kind: 'ready', data: projected as PostingBatchSummary[] };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getPostingBatch(
  batchPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<PostingResourceResult<PostingBatch>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/posting/batches/{batch_pub_id}', {
      params: { header: headers, path: { batch_pub_id: batchPubId } },
    });
    if (!result.data) return classifyPostingFailure(result.response.status);
    const projected = projectPostingBatch(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createPostingBatch(
  input: CreatePostingBatchInput,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<PostingResourceResult<PostingBatch>> {
  const form = new FormData();
  form.set('document', input.document);
  form.set(
    'targets_json',
    JSON.stringify(
      input.targets.map((target) => ({
        catalog_type: target.catalogType,
        provider: target.provider,
        media_name: target.mediaName,
        media_platform: target.mediaPlatform ?? '',
      })),
    ),
  );
  form.set('title', input.title ?? '');
  form.set('customer_name', input.customerName ?? '');
  if (input.releaseTime) form.set('release_time', input.releaseTime);
  form.set('auto_submit', String(input.autoSubmit));
  form.set('confirm_spend', String(input.confirmSpend));
  form.set('max_total_amount', input.maxTotalAmount.toFixed(2));
  form.set('note', input.note ?? '');
  if (input.sopProjectPubId) form.set('sop_project_pub_id', input.sopProjectPubId);
  if (input.articleVersionPubId) form.set('article_version_pub_id', input.articleVersionPubId);
  try {
    const result = await projectedApiClient(client).POST('/api/v2/posting/batches', {
      params: {
        header: {
          ...headers,
          'Idempotency-Key': input.idempotencyKey,
        },
      },
      body: form as never,
      bodySerializer: () => form,
    });
    if (!result.data) return classifyPostingFailure(result.response.status);
    const projected = projectPostingBatch(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function submitPostingBatch(
  batchPubId: string,
  maxTotalAmount: number,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<PostingResourceResult<PostingBatch>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/posting/batches/{batch_pub_id}/submit',
      {
        params: { header: headers, path: { batch_pub_id: batchPubId } },
        body: { confirm_spend: true, max_total_amount: maxTotalAmount.toFixed(2) },
      },
    );
    if (!result.data) return classifyPostingFailure(result.response.status);
    const projected = projectPostingBatch(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function decidePostingApproval(
  batchPubId: string,
  decision: 'approve' | 'reject',
  rationale: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<PostingResourceResult<PostingBatch>> {
  try {
    const api = projectedApiClient(client);
    const options = {
      params: { header: headers, path: { batch_pub_id: batchPubId } },
      body: { rationale },
    };
    const result =
      decision === 'approve'
        ? await api.POST('/api/v2/posting/batches/{batch_pub_id}/approve', options)
        : await api.POST('/api/v2/posting/batches/{batch_pub_id}/reject', options);
    if (!result.data) return classifyPostingFailure(result.response.status);
    const projected = projectPostingBatch(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function backfillPostingTarget(
  batchPubId: string,
  targetPubId: string,
  body: {
    status: 'submitted' | 'reviewing' | 'published' | 'rejected' | 'failed';
    publicUrl: string;
    providerMessage: string;
  },
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<PostingResourceResult<PostingBatch>> {
  try {
    const result = await projectedApiClient(client).PATCH(
      '/api/v2/posting/batches/{batch_pub_id}/targets/{target_pub_id}',
      {
        params: {
          header: headers,
          path: { batch_pub_id: batchPubId, target_pub_id: targetPubId },
        },
        body: {
          status: body.status,
          public_url: body.publicUrl,
          provider_message: body.providerMessage,
        },
      },
    );
    if (!result.data) return classifyPostingFailure(result.response.status);
    const projected = projectPostingBatch(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function refreshPostingBatch(
  batchPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<PostingResourceResult<PostingBatch>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/posting/batches/{batch_pub_id}/refresh',
      {
        params: { header: headers, path: { batch_pub_id: batchPubId } },
      },
    );
    if (!result.data) return classifyPostingFailure(result.response.status);
    const projected = projectPostingBatch(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

// -- S05 GEO source-article SOP safe browser boundary -------------------------

export type SopStageKey =
  | 'project-definition'
  | 'query-set'
  | 'baseline'
  | 'retrieval-review'
  | 'evidence-ledger'
  | 'opportunities'
  | 'writing'
  | 'pre-publish'
  | 'publishing'
  | 'index-watch'
  | 'retest'
  | 'comparison'
  | 'experiments'
  | 'archive-log';

export type SopProjectSummary = {
  pubId: string;
  name: string;
  brandStandardName: string;
  status: 'active' | 'archived';
  updatedAt: string;
};

export type SopProjectPage = {
  data: SopProjectSummary[];
  nextCursor: string | null;
  hasMore: boolean;
};

export type SopDashboardStep = {
  key: SopStageKey;
  stage: string;
  name: string;
  status: 'done' | 'in_progress' | 'empty';
  metrics: { label: string; value: string }[];
};

export type SopDashboardArticle = {
  articlePubId: string;
  title: string;
  status: string;
  versionCount: number;
  publicationReady: boolean;
  hasPublication: boolean;
  maturityLevel: 'L0' | 'L1' | 'L2' | 'L3' | 'L4';
};

export type SopDashboard = {
  project: SopProjectSummary;
  steps: SopDashboardStep[];
  articles: SopDashboardArticle[];
};

export type SopStageItem = {
  pubId: string;
  label: string;
  status: string;
  detail: string;
  createdAt: string;
};

export type SopStageSnapshot = {
  items: SopStageItem[];
  metrics: { label: string; value: string }[];
};

export type SopMutationReceipt = {
  pubId: string;
  relatedPubId: string | null;
  message: string;
};

export type SopProjectCreateInput = {
  name: string;
  brandStandardName: string;
  targetPlatform: string;
  successMetric: string;
};

export type SopMutationCommand =
  | {
      kind: 'update-project';
      projectPubId: string;
      brandStandardName: string;
      aliases: string;
      competitors: string;
      targetPlatform: string;
      successMetric: string;
    }
  | {
      kind: 'query-set';
      projectPubId: string;
      note: string;
      queryText: string;
      layer: 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'G';
      priority: 'P0' | 'P1' | 'P2';
    }
  | {
      kind: 'baseline';
      projectPubId: string;
      queryItemPubId: string;
      platform: string;
      captureStatus:
        | 'success'
        | 'captcha'
        | 'login_wall'
        | 'interrupted'
        | 'incomplete'
        | 'risk_control'
        | 'search_disabled'
        | 'sources_unloaded';
      answerText: string;
      brandMentioned: boolean;
    }
  | {
      kind: 'retrieval-review';
      projectPubId: string;
      insightType: 'query_rewrite' | 'source_selection' | 'answer_usage' | 'statistics' | 'note';
      note: string;
    }
  | {
      kind: 'evidence-ledger';
      projectPubId: string;
      claimText: string;
      sourceName: string;
      sourceUrl: string;
      sourceLevel: 'official' | 'third_party' | 'experience';
      canProve: string;
      cannotProve: string;
      allowedPublic: boolean;
    }
  | {
      kind: 'opportunities';
      projectPubId: string;
      targetQuery: string;
      currentGap: string;
      neededEvidence: string;
      recommendedPlatform: string;
      expectedChange: string;
    }
  | {
      kind: 'writing';
      projectPubId: string;
      opportunityPubId: string;
      title: string;
      body: string;
      changeNote: string;
    }
  | {
      kind: 'pre-publish';
      projectPubId: string;
      articleVersionPubId: string;
      checkType:
        | 'ai_dialogue'
        | 'fact_verification'
        | 'readability'
        | 'extractability'
        | 'title_match'
        | 'entity_disambiguation'
        | 'source_completeness'
        | 'keyword_stuffing'
        | 'compliance'
        | 'rag_recall'
        | 'synonym_test'
        | 'other';
      result: 'pass' | 'warn' | 'fail';
      findings: string;
      publicationReady: boolean;
    }
  | {
      kind: 'publishing';
      projectPubId: string;
      articleVersionPubId: string;
      platform: string;
      accountLabel: string;
    }
  | {
      kind: 'index-watch';
      projectPubId: string;
      publicationPubId: string;
      checkpoint: 'immediate' | 'h24' | 'd3' | 'd7' | 'd14' | 'custom';
      pageAccessible: boolean;
      searchEngineIndexed: boolean;
      platformSearchVisible: boolean;
      aiRetrieved: boolean;
      aiCited: boolean;
      note: string;
    }
  | {
      kind: 'retest';
      projectPubId: string;
      publicationPubId: string;
      queryItemPubId: string;
      platform: string;
      answerText: string;
      brandMentioned: boolean;
      articleAppeared: boolean;
      articleCited: boolean;
      attributionCorrect: boolean;
      newFacts: string;
    }
  | {
      kind: 'comparison';
      projectPubId: string;
      publicationPubId: string;
      queryItemPubId: string;
      baselineAnswerPubId: string;
      retestAnswerPubId: string;
      confidence: 'high' | 'medium' | 'low' | 'none';
      attributionCorrect: boolean;
      conclusion: string;
      nextAction: string;
    }
  | {
      kind: 'experiments';
      projectPubId: string;
      querySetPubId: string;
      hypothesis: string;
      changeDescription: string;
      observationWindow: string;
    }
  | {
      kind: 'archive-log';
      projectPubId: string;
      entryType: 'progress' | 'failure' | 'blocker' | 'decision' | 'note';
      failureClass:
        | ''
        | 'captcha'
        | 'login_wall'
        | 'no_retrieval'
        | 'sources_unloaded'
        | 'not_public'
        | 'not_indexed'
        | 'not_cited'
        | 'wrong_attribution'
        | 'over_extrapolation'
        | 'other';
      content: string;
    };

type SopProjectPageContract =
  paths['/api/v2/sop/projects']['get']['responses']['200']['content']['application/json'];
type SopProjectContract = SopProjectPageContract['data'][number];
type SopDashboardContract =
  paths['/api/v2/sop/projects/{project_pub_id}/dashboard']['get']['responses']['200']['content']['application/json'];

const sopStageKeys = new Set<SopStageKey>([
  'project-definition',
  'query-set',
  'baseline',
  'retrieval-review',
  'evidence-ledger',
  'opportunities',
  'writing',
  'pre-publish',
  'publishing',
  'index-watch',
  'retest',
  'comparison',
  'experiments',
  'archive-log',
]);

const isSopRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const sopSafeString = (value: unknown, maximumLength: number): string | null =>
  typeof value === 'string' &&
  value.length <= maximumLength &&
  !/[\u0000-\u001f\u007f]/u.test(value)
    ? value
    : null;

const projectSopProject = (value: SopProjectContract): SopProjectSummary | null => {
  if (
    !value.pub_id.startsWith('spr_') ||
    !value.name.trim() ||
    !value.brand_standard_name.trim() ||
    (value.status !== 'active' && value.status !== 'archived') ||
    !safeTimestamp(value.updated_at)
  ) {
    return null;
  }
  return {
    pubId: value.pub_id,
    name: value.name,
    brandStandardName: value.brand_standard_name,
    status: value.status,
    updatedAt: value.updated_at,
  };
};

const sopMetricValue = (value: unknown): string | null => {
  if (value === null) return '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value === 'string' && value.length <= 500) return value;
  return null;
};

const projectSopMetrics = (value: unknown): { label: string; value: string }[] => {
  if (!isSopRecord(value)) return [];
  return Object.entries(value)
    .slice(0, 24)
    .flatMap(([label, item]) => {
      const projected = sopMetricValue(item);
      return projected === null ? [] : [{ label, value: projected }];
    });
};

const projectSopDashboard = (value: SopDashboardContract): SopDashboard | null => {
  const project = projectSopProject(value.project);
  if (!project || value.steps.length !== 14 || value.articles.length > 500) return null;
  const steps = value.steps.flatMap((step) => {
    if (
      !sopStageKeys.has(step.key as SopStageKey) ||
      !step.stage ||
      !step.name ||
      !['done', 'in_progress', 'empty'].includes(step.status)
    ) {
      return [];
    }
    return [
      {
        key: step.key as SopStageKey,
        stage: step.stage,
        name: step.name,
        status: step.status as SopDashboardStep['status'],
        metrics: projectSopMetrics(step.metrics),
      },
    ];
  });
  if (steps.length !== 14) return null;
  const articles = value.articles.flatMap((article) => {
    if (
      !article.article_pub_id.startsWith('sar_') ||
      !article.title.trim() ||
      !['L0', 'L1', 'L2', 'L3', 'L4'].includes(article.maturity_level)
    ) {
      return [];
    }
    return [
      {
        articlePubId: article.article_pub_id,
        title: article.title,
        status: article.status,
        versionCount: article.version_count,
        publicationReady: article.publication_ready,
        hasPublication: article.has_publication,
        maturityLevel: article.maturity_level as SopDashboardArticle['maturityLevel'],
      },
    ];
  });
  return articles.length === value.articles.length ? { project, steps, articles } : null;
};

const projectSopStageItem = (value: object): SopStageItem | null => {
  const record = value as Record<string, unknown>;
  const pubId = sopSafeString(record.pub_id, 128);
  if (!pubId || !/^(?:sqs|sqi|sbl|sis|sev|sop|sar|sav|spc|spb|sio|srt|scm|sex|swl)_/.test(pubId)) {
    return null;
  }
  const labelCandidates = [
    record.query_text,
    record.title,
    record.claim_text,
    record.target_query,
    record.hypothesis,
    record.content,
    record.insight_type,
    record.platform,
    record.check_type,
    record.checkpoint,
  ];
  const label =
    labelCandidates
      .map((candidate) => sopSafeString(candidate, 500))
      .find(
        (candidate): candidate is string => candidate !== null && candidate.trim().length > 0,
      ) ?? pubId;
  const status =
    sopSafeString(
      record.status ?? record.capture_status ?? record.result ?? record.entry_type ?? '',
      100,
    ) ?? '';
  const detailCandidates = [
    record.note,
    record.source_name,
    record.current_gap,
    record.recommended_platform,
    record.change_note,
    record.findings,
    record.conclusion,
    record.failure_class,
  ];
  const detail =
    detailCandidates
      .map((candidate) => sopSafeString(candidate, 2_000))
      .find(
        (candidate): candidate is string => candidate !== null && candidate.trim().length > 0,
      ) ?? '';
  const createdAt = sopSafeString(record.created_at, 100) ?? '';
  return { pubId, label, status, detail, createdAt };
};

const projectSopItems = (values: object[], limit = 100): SopStageItem[] =>
  values.slice(0, limit).flatMap((value) => {
    const item = projectSopStageItem(value);
    return item ? [item] : [];
  });

const sopWriteHeaders = (
  headers: IdentitySessionHeaders,
  idempotencyKey: string,
): IdentitySessionHeaders & { 'Idempotency-Key': string } => ({
  ...headers,
  'Idempotency-Key': idempotencyKey,
});

const sopMutationFailure = (status: number): ProjectResourceResult<SopMutationReceipt> =>
  classifyResourceFailure(status);

export async function listSopProjects(
  headers: IdentitySessionHeaders,
  cursorOrOverride: string | null | ProjectedApiClientOverride = null,
  override: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<SopProjectPage>> {
  try {
    const cursor = typeof cursorOrOverride === 'string' ? cursorOrOverride : null;
    const clientOverride =
      cursorOrOverride !== null && typeof cursorOrOverride !== 'string'
        ? cursorOrOverride
        : override;
    const projected = projectedApiClient(clientOverride);
    const result = await projected.GET('/api/v2/sop/projects', {
      params: {
        query: { limit: 100, ...(cursor ? { cursor } : {}) },
        header: headers,
      },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projects = result.data.data.flatMap((value) => {
      const project = projectSopProject(value);
      return project ? [project] : [];
    });
    const rawNextCursor = result.data.page.next_cursor;
    const rawHasMore = result.data.page.has_more;
    const hasMore = rawHasMore === true;
    const nextCursor =
      typeof rawNextCursor === 'string' && /^spr_[A-Za-z0-9_-]{1,124}$/u.test(rawNextCursor)
        ? rawNextCursor
        : null;
    const pageIsValid =
      projects.length === result.data.data.length &&
      typeof rawHasMore === 'boolean' &&
      ((hasMore && nextCursor !== null && nextCursor === projects.at(-1)?.pubId) ||
        (!hasMore && rawNextCursor === null));
    return pageIsValid
      ? {
          kind: 'ready',
          data: { data: projects, nextCursor: hasMore ? nextCursor : null, hasMore },
        }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createSopProject(
  headers: IdentitySessionHeaders,
  body: SopProjectCreateInput,
  idempotencyKey: string,
  override: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<SopMutationReceipt>> {
  try {
    const projected = projectedApiClient(override);
    const result = await projected.POST('/api/v2/sop/projects', {
      params: { header: sopWriteHeaders(headers, idempotencyKey) },
      body: {
        name: body.name,
        brand_standard_name: body.brandStandardName,
        brand_profile: {},
        target_platforms: body.targetPlatform ? [body.targetPlatform] : [],
        success_definition: body.successMetric ? [body.successMetric] : [],
      },
    });
    if (!result.data) return sopMutationFailure(result.response.status);
    return {
      kind: 'ready',
      data: { pubId: result.data.pub_id, relatedPubId: null, message: 'SOP 项目已创建' },
    };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getSopDashboard(
  headers: IdentitySessionHeaders,
  projectPubId: string,
  override: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<SopDashboard>> {
  try {
    const projected = projectedApiClient(override);
    const result = await projected.GET('/api/v2/sop/projects/{project_pub_id}/dashboard', {
      params: { path: { project_pub_id: projectPubId }, header: headers },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const dashboard = projectSopDashboard(result.data);
    return dashboard ? { kind: 'ready', data: dashboard } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function loadSopStage(
  headers: IdentitySessionHeaders,
  projectPubId: string,
  stage: SopStageKey,
  override: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<SopStageSnapshot>> {
  try {
    const projected = projectedApiClient(override);
    if (stage === 'project-definition') {
      const result = await projected.GET('/api/v2/sop/projects/{project_pub_id}', {
        params: { path: { project_pub_id: projectPubId }, header: headers },
      });
      if (!result.data) return classifyResourceFailure(result.response.status);
      return {
        kind: 'ready',
        data: {
          items: [
            {
              pubId: result.data.pub_id,
              label: result.data.brand_standard_name,
              status: result.data.status,
              detail: result.data.name,
              createdAt: result.data.created_at,
            },
          ],
          metrics: [
            { label: '目标平台', value: String(result.data.target_platforms.length) },
            { label: '成功定义', value: String(result.data.success_definition.length) },
          ],
        },
      };
    }
    if (stage === 'query-set') {
      const sets = await projected.GET('/api/v2/sop/projects/{project_pub_id}/query-sets', {
        params: {
          path: { project_pub_id: projectPubId },
          query: { limit: 100 },
          header: headers,
        },
      });
      if (!sets.data) return classifyResourceFailure(sets.response.status);
      const selected = [...sets.data.data]
        .reverse()
        .find((item) => item.status === 'frozen' || item.status === 'draft');
      if (!selected) return { kind: 'ready', data: { items: [], metrics: [] } };
      const items = await projected.GET('/api/v2/sop/query-sets/{query_set_pub_id}/items', {
        params: {
          path: { query_set_pub_id: selected.pub_id },
          query: { limit: 100 },
          header: headers,
        },
      });
      if (!items.data) return classifyResourceFailure(items.response.status);
      return {
        kind: 'ready',
        data: {
          items: projectSopItems(items.data.data),
          metrics: [
            { label: '查询集版本', value: String(selected.version_no) },
            { label: '查询集状态', value: selected.status },
            { label: '查询词数', value: String(items.data.data.length) },
          ],
        },
      };
    }
    if (stage === 'baseline') {
      const result = await projected.GET('/api/v2/sop/projects/{project_pub_id}/baseline-answers', {
        params: {
          path: { project_pub_id: projectPubId },
          query: { limit: 100 },
          header: headers,
        },
      });
      if (!result.data) return classifyResourceFailure(result.response.status);
      return {
        kind: 'ready',
        data: {
          items: projectSopItems(result.data.data),
          metrics: [
            { label: '样本', value: String(result.data.data.length) },
            {
              label: '成功',
              value: String(
                result.data.data.filter((item) => item.capture_status === 'success').length,
              ),
            },
          ],
        },
      };
    }
    if (stage === 'retrieval-review') {
      const result = await projected.GET('/api/v2/sop/projects/{project_pub_id}/insights', {
        params: {
          path: { project_pub_id: projectPubId },
          query: { limit: 100 },
          header: headers,
        },
      });
      if (!result.data) return classifyResourceFailure(result.response.status);
      return {
        kind: 'ready',
        data: { items: projectSopItems(result.data.data), metrics: [] },
      };
    }
    if (stage === 'evidence-ledger') {
      const result = await projected.GET('/api/v2/sop/projects/{project_pub_id}/evidence', {
        params: {
          path: { project_pub_id: projectPubId },
          query: { limit: 100 },
          header: headers,
        },
      });
      if (!result.data) return classifyResourceFailure(result.response.status);
      return {
        kind: 'ready',
        data: {
          items: projectSopItems(result.data.data),
          metrics: [
            {
              label: '允许公开',
              value: String(result.data.data.filter((item) => item.allowed_public).length),
            },
          ],
        },
      };
    }
    if (stage === 'opportunities') {
      const result = await projected.GET('/api/v2/sop/projects/{project_pub_id}/opportunities', {
        params: {
          path: { project_pub_id: projectPubId },
          query: { limit: 100 },
          header: headers,
        },
      });
      if (!result.data) return classifyResourceFailure(result.response.status);
      return {
        kind: 'ready',
        data: { items: projectSopItems(result.data.data), metrics: [] },
      };
    }
    if (stage === 'writing' || stage === 'pre-publish') {
      const result = await projected.GET('/api/v2/sop/projects/{project_pub_id}/articles', {
        params: {
          path: { project_pub_id: projectPubId },
          query: { limit: 100 },
          header: headers,
        },
      });
      if (!result.data) return classifyResourceFailure(result.response.status);
      if (stage === 'writing' || result.data.data.length === 0) {
        return {
          kind: 'ready',
          data: { items: projectSopItems(result.data.data), metrics: [] },
        };
      }
      const article = result.data.data.at(-1)!;
      const detail = await projected.GET('/api/v2/sop/articles/{article_pub_id}', {
        params: { path: { article_pub_id: article.pub_id }, header: headers },
      });
      if (!detail.data) return classifyResourceFailure(detail.response.status);
      return {
        kind: 'ready',
        data: {
          items: projectSopItems(detail.data.versions),
          metrics: [
            { label: '文章', value: article.title },
            { label: '版本', value: String(detail.data.versions.length) },
          ],
        },
      };
    }
    if (
      stage === 'publishing' ||
      stage === 'index-watch' ||
      stage === 'retest' ||
      stage === 'comparison'
    ) {
      const publications = await projected.GET(
        '/api/v2/sop/projects/{project_pub_id}/publications',
        {
          params: {
            path: { project_pub_id: projectPubId },
            query: { limit: 100 },
            header: headers,
          },
        },
      );
      if (!publications.data) return classifyResourceFailure(publications.response.status);
      if (stage === 'publishing' || publications.data.data.length === 0) {
        return {
          kind: 'ready',
          data: { items: projectSopItems(publications.data.data), metrics: [] },
        };
      }
      const publication = publications.data.data.at(-1)!;
      if (stage === 'index-watch') {
        const result = await projected.GET(
          '/api/v2/sop/publications/{publication_pub_id}/observations',
          {
            params: {
              path: { publication_pub_id: publication.pub_id },
              query: { limit: 100 },
              header: headers,
            },
          },
        );
        if (!result.data) return classifyResourceFailure(result.response.status);
        return {
          kind: 'ready',
          data: { items: projectSopItems(result.data.data), metrics: [] },
        };
      }
      if (stage === 'retest') {
        const result = await projected.GET(
          '/api/v2/sop/publications/{publication_pub_id}/retest-answers',
          {
            params: {
              path: { publication_pub_id: publication.pub_id },
              query: { limit: 100 },
              header: headers,
            },
          },
        );
        if (!result.data) return classifyResourceFailure(result.response.status);
        return {
          kind: 'ready',
          data: { items: projectSopItems(result.data.data), metrics: [] },
        };
      }
      const comparisons = await projected.GET(
        '/api/v2/sop/publications/{publication_pub_id}/comparisons',
        {
          params: {
            path: { publication_pub_id: publication.pub_id },
            query: { limit: 100 },
            header: headers,
          },
        },
      );
      if (!comparisons.data) return classifyResourceFailure(comparisons.response.status);
      const summary = await projected.GET(
        '/api/v2/sop/projects/{project_pub_id}/comparison-summary',
        {
          params: { path: { project_pub_id: projectPubId }, header: headers },
        },
      );
      if (!summary.data) return classifyResourceFailure(summary.response.status);
      return {
        kind: 'ready',
        data: {
          items: projectSopItems(comparisons.data.data),
          metrics: [
            {
              label: '文章召回率',
              value:
                summary.data.retrieval.article_recall_rate === null
                  ? '—'
                  : String(summary.data.retrieval.article_recall_rate),
            },
            {
              label: '引用率',
              value:
                summary.data.citation.citation_rate === null
                  ? '—'
                  : String(summary.data.citation.citation_rate),
            },
            {
              label: '品牌提及率',
              value:
                summary.data.brand.retest_mention_rate === null
                  ? '—'
                  : String(summary.data.brand.retest_mention_rate),
            },
          ],
        },
      };
    }
    if (stage === 'experiments') {
      const result = await projected.GET('/api/v2/sop/projects/{project_pub_id}/experiments', {
        params: {
          path: { project_pub_id: projectPubId },
          query: { limit: 100 },
          header: headers,
        },
      });
      if (!result.data) return classifyResourceFailure(result.response.status);
      return {
        kind: 'ready',
        data: { items: projectSopItems(result.data.data), metrics: [] },
      };
    }
    const result = await projected.GET('/api/v2/sop/projects/{project_pub_id}/work-logs', {
      params: {
        path: { project_pub_id: projectPubId },
        query: { limit: 100 },
        header: headers,
      },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    return {
      kind: 'ready',
      data: { items: projectSopItems(result.data.data), metrics: [] },
    };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function mutateSopStage(
  headers: IdentitySessionHeaders,
  command: SopMutationCommand,
  idempotencyKey: string,
  override: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<SopMutationReceipt>> {
  try {
    const projected = projectedApiClient(override);
    const writeHeaders = sopWriteHeaders(headers, idempotencyKey);
    const now = new Date().toISOString();
    if (command.kind === 'update-project') {
      const result = await projected.PATCH('/api/v2/sop/projects/{project_pub_id}', {
        params: {
          path: { project_pub_id: command.projectPubId },
          header: headers,
        },
        body: {
          brand_standard_name: command.brandStandardName,
          brand_profile: {
            aliases: command.aliases
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean),
            // P3 己方内容拉踩检测的竞品真源（judge_own_content_disparagement 读取）
            competitors: command.competitors
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean),
          },
          target_platforms: command.targetPlatform ? [command.targetPlatform] : [],
          success_definition: command.successMetric ? [command.successMetric] : [],
        },
      });
      if (!result.data) return sopMutationFailure(result.response.status);
      return {
        kind: 'ready',
        data: { pubId: result.data.pub_id, relatedPubId: null, message: '项目定义已更新' },
      };
    }
    if (command.kind === 'query-set') {
      const querySet = await projected.POST('/api/v2/sop/projects/{project_pub_id}/query-sets', {
        params: {
          path: { project_pub_id: command.projectPubId },
          header: writeHeaders,
        },
        body: { note: command.note },
      });
      if (!querySet.data) return sopMutationFailure(querySet.response.status);
      const items = await projected.POST('/api/v2/sop/query-sets/{query_set_pub_id}/items', {
        params: {
          path: { query_set_pub_id: querySet.data.pub_id },
          header: sopWriteHeaders(headers, `${idempotencyKey}-items`),
        },
        body: {
          items: [
            {
              query_text: command.queryText,
              layer: command.layer,
              contains_brand: false,
              intent: '',
              persona: '',
              decision_stage: '',
              expected_facts: '',
              priority: command.priority,
            },
          ],
        },
      });
      if (!items.data) return sopMutationFailure(items.response.status);
      const frozen = await projected.POST('/api/v2/sop/query-sets/{query_set_pub_id}/freeze', {
        params: {
          path: { query_set_pub_id: querySet.data.pub_id },
          header: sopWriteHeaders(headers, `${idempotencyKey}-freeze`),
        },
      });
      if (!frozen.data) return sopMutationFailure(frozen.response.status);
      return {
        kind: 'ready',
        data: {
          pubId: querySet.data.pub_id,
          relatedPubId: items.data[0]?.pub_id ?? null,
          message: '查询集已创建、加词并冻结',
        },
      };
    }
    if (command.kind === 'baseline') {
      const result = await projected.POST(
        '/api/v2/sop/projects/{project_pub_id}/baseline-answers',
        {
          params: {
            path: { project_pub_id: command.projectPubId },
            header: writeHeaders,
          },
          body: {
            query_item_pub_id: command.queryItemPubId,
            sample_index: 1,
            platform: command.platform,
            region: '',
            account_label: '',
            mode: '',
            asked_at: now,
            capture_status: command.captureStatus,
            answer_text: command.answerText,
            reasoning_summary: '',
            search_terms: [],
            search_results: [],
            citations: [],
            brand_mentioned: command.brandMentioned,
            mention_context: '',
            key_facts: [],
            evidence_ref: '',
            note: '',
          },
        },
      );
      if (!result.data) return sopMutationFailure(result.response.status);
      return {
        kind: 'ready',
        data: { pubId: result.data.pub_id, relatedPubId: null, message: '基线样本已登记' },
      };
    }
    if (command.kind === 'retrieval-review') {
      const result = await projected.POST('/api/v2/sop/projects/{project_pub_id}/insights', {
        params: {
          path: { project_pub_id: command.projectPubId },
          header: writeHeaders,
        },
        body: { insight_type: command.insightType, payload: {}, note: command.note },
      });
      if (!result.data) return sopMutationFailure(result.response.status);
      return {
        kind: 'ready',
        data: { pubId: result.data.pub_id, relatedPubId: null, message: '检索复盘已登记' },
      };
    }
    if (command.kind === 'evidence-ledger') {
      const result = await projected.POST('/api/v2/sop/projects/{project_pub_id}/evidence', {
        params: {
          path: { project_pub_id: command.projectPubId },
          header: writeHeaders,
        },
        body: {
          claim_text: command.claimText,
          source_name: command.sourceName,
          source_url: command.sourceUrl,
          source_level: command.sourceLevel,
          verified_at: now,
          can_prove: command.canProve,
          cannot_prove: command.cannotProve,
          allowed_public: command.allowedPublic,
          evidence_ref: '',
        },
      });
      if (!result.data) return sopMutationFailure(result.response.status);
      return {
        kind: 'ready',
        data: { pubId: result.data.pub_id, relatedPubId: null, message: '证据条目已登记' },
      };
    }
    if (command.kind === 'opportunities') {
      const created = await projected.POST('/api/v2/sop/projects/{project_pub_id}/opportunities', {
        params: {
          path: { project_pub_id: command.projectPubId },
          header: writeHeaders,
        },
        body: {
          target_query: command.targetQuery,
          current_gap: command.currentGap,
          current_sources: [],
          brand_material: '',
          needed_evidence: command.neededEvidence,
          recommended_platform: command.recommendedPlatform,
          expected_change: command.expectedChange,
        },
      });
      if (!created.data) return sopMutationFailure(created.response.status);
      const selected = await projected.PATCH('/api/v2/sop/opportunities/{opportunity_pub_id}', {
        params: {
          path: { opportunity_pub_id: created.data.pub_id },
          header: headers,
        },
        body: { status: 'selected' },
      });
      if (!selected.data) return sopMutationFailure(selected.response.status);
      return {
        kind: 'ready',
        data: { pubId: created.data.pub_id, relatedPubId: null, message: '内容机会已选定' },
      };
    }
    if (command.kind === 'writing') {
      const article = await projected.POST('/api/v2/sop/projects/{project_pub_id}/articles', {
        params: {
          path: { project_pub_id: command.projectPubId },
          header: writeHeaders,
        },
        body: {
          title: command.title,
          opportunity_pub_id: command.opportunityPubId || null,
        },
      });
      if (!article.data) return sopMutationFailure(article.response.status);
      const version = await projected.POST('/api/v2/sop/articles/{article_pub_id}/versions', {
        params: {
          path: { article_pub_id: article.data.pub_id },
          header: sopWriteHeaders(headers, `${idempotencyKey}-version`),
        },
        body: {
          title: command.title,
          body: command.body,
          change_note: command.changeNote,
        },
      });
      if (!version.data) return sopMutationFailure(version.response.status);
      return {
        kind: 'ready',
        data: {
          pubId: article.data.pub_id,
          relatedPubId: version.data.pub_id,
          message: '文章与首个版本已创建',
        },
      };
    }
    if (command.kind === 'pre-publish') {
      const check = await projected.POST('/api/v2/sop/article-versions/{version_pub_id}/checks', {
        params: {
          path: { version_pub_id: command.articleVersionPubId },
          header: writeHeaders,
        },
        body: {
          check_type: command.checkType,
          result: command.result,
          findings: command.findings,
          checked_by: '',
          checked_at: now,
        },
      });
      if (!check.data) return sopMutationFailure(check.response.status);
      const version = await projected.PATCH('/api/v2/sop/article-versions/{version_pub_id}', {
        params: {
          path: { version_pub_id: command.articleVersionPubId },
          header: headers,
        },
        body: {
          readiness_checklist: { [command.checkType]: command.result === 'pass' },
          publication_ready: command.publicationReady,
        },
      });
      if (!version.data) return sopMutationFailure(version.response.status);
      return {
        kind: 'ready',
        data: {
          pubId: check.data.pub_id,
          relatedPubId: version.data.pub_id,
          message: command.publicationReady ? '检查完成，版本已放行' : '检查结果已登记',
        },
      };
    }
    if (command.kind === 'publishing') {
      const result = await projected.POST(
        '/api/v2/sop/article-versions/{version_pub_id}/publications',
        {
          params: {
            path: { version_pub_id: command.articleVersionPubId },
            header: writeHeaders,
          },
          body: {
            platform: command.platform,
            account_label: command.accountLabel,
            submitted_at: now,
          },
        },
      );
      if (!result.data) return sopMutationFailure(result.response.status);
      return {
        kind: 'ready',
        data: { pubId: result.data.pub_id, relatedPubId: null, message: '发布记录已提交' },
      };
    }
    if (command.kind === 'index-watch') {
      const result = await projected.POST(
        '/api/v2/sop/publications/{publication_pub_id}/observations',
        {
          params: {
            path: { publication_pub_id: command.publicationPubId },
            header: writeHeaders,
          },
          body: {
            checkpoint: command.checkpoint,
            checkpoint_label: '',
            observed_at: now,
            page_accessible: command.pageAccessible,
            search_engine_indexed: command.searchEngineIndexed,
            platform_search_visible: command.platformSearchVisible,
            ai_retrieved: command.aiRetrieved,
            ai_cited: command.aiCited,
            note: command.note,
          },
        },
      );
      if (!result.data) return sopMutationFailure(result.response.status);
      return {
        kind: 'ready',
        data: { pubId: result.data.pub_id, relatedPubId: null, message: '索引观察已登记' },
      };
    }
    if (command.kind === 'retest') {
      const result = await projected.POST(
        '/api/v2/sop/publications/{publication_pub_id}/retest-answers',
        {
          params: {
            path: { publication_pub_id: command.publicationPubId },
            header: writeHeaders,
          },
          body: {
            query_item_pub_id: command.queryItemPubId,
            sample_index: 1,
            platform: command.platform,
            region: '',
            account_label: '',
            mode: '',
            asked_at: now,
            capture_status: 'success',
            answer_text: command.answerText,
            reasoning_summary: '',
            search_terms: [],
            search_results: [],
            citations: [],
            brand_mentioned: command.brandMentioned,
            mention_context: '',
            key_facts: [],
            evidence_ref: '',
            note: '',
            article_appeared: command.articleAppeared,
            article_position: command.articleAppeared ? 1 : null,
            article_cited: command.articleCited,
            citation_position: command.articleCited ? 1 : null,
            brand_attribution_correct: command.attributionCorrect,
            new_facts: command.newFacts
              .split('\n')
              .map((item) => item.trim())
              .filter(Boolean),
            errors_introduced: '',
          },
        },
      );
      if (!result.data) return sopMutationFailure(result.response.status);
      return {
        kind: 'ready',
        data: { pubId: result.data.pub_id, relatedPubId: null, message: '同题复测已登记' },
      };
    }
    if (command.kind === 'comparison') {
      const result = await projected.POST(
        '/api/v2/sop/publications/{publication_pub_id}/comparisons',
        {
          params: {
            path: { publication_pub_id: command.publicationPubId },
            header: writeHeaders,
          },
          body: {
            query_item_pub_id: command.queryItemPubId,
            baseline_answer_pub_id: command.baselineAnswerPubId || null,
            retest_answer_pub_id: command.retestAnswerPubId || null,
            metrics: {},
            new_info_location: '',
            from_article_confidence: command.confidence,
            attribution_correct: command.attributionCorrect,
            conclusion: command.conclusion,
            next_actions: command.nextAction ? [command.nextAction] : [],
          },
        },
      );
      if (!result.data) return sopMutationFailure(result.response.status);
      return {
        kind: 'ready',
        data: { pubId: result.data.pub_id, relatedPubId: null, message: '对比归因已保存' },
      };
    }
    if (command.kind === 'experiments') {
      const result = await projected.POST('/api/v2/sop/projects/{project_pub_id}/experiments', {
        params: {
          path: { project_pub_id: command.projectPubId },
          header: writeHeaders,
        },
        body: {
          hypothesis: command.hypothesis,
          change_description: command.changeDescription,
          controlled_conditions: { frozen_query_set: true },
          query_set_pub_id: command.querySetPubId || null,
          observation_window: command.observationWindow,
        },
      });
      if (!result.data) return sopMutationFailure(result.response.status);
      return {
        kind: 'ready',
        data: { pubId: result.data.pub_id, relatedPubId: null, message: '实验已创建' },
      };
    }
    const result = await projected.POST('/api/v2/sop/projects/{project_pub_id}/work-logs', {
      params: {
        path: { project_pub_id: command.projectPubId },
        header: writeHeaders,
      },
      body: {
        entry_type: command.entryType,
        failure_class: command.failureClass || null,
        content: command.content,
      },
    });
    if (!result.data) return sopMutationFailure(result.response.status);
    return {
      kind: 'ready',
      data: { pubId: result.data.pub_id, relatedPubId: null, message: '工作日志已追加' },
    };
  } catch {
    return { kind: 'unavailable' };
  }
}

// ── Intake（客户信息收集表）投影与写边界 ─────────────────────────────────────
// 契约真源：api/geo_platform/intake（profile 1:1 草稿 + promo/trigger 子表 +
// AI 联网调研预填 + docx 导出 + 公开 form-schema）。投影纪律与其他 customer
// wrapper 一致：边界 DLP、词表/形状 fail-closed、判别联合 ready/forbidden/unavailable。
type IntakeProfileContractView =
  paths['/api/v2/projects/{project_pub_id}/intake/profile']['get']['responses']['200']['content']['application/json'];
export type IntakeProfileWrite =
  paths['/api/v2/projects/{project_pub_id}/intake/profile']['put']['requestBody']['content']['application/json'];
export type IntakeLicenseRow = { name: string; number: string; expiry: string };
export type IntakeProfileView = Pick<
  IntakeProfileContractView,
  | 'project_pub_id'
  | 'exists'
  | 'updated_at'
  | 'contact_person'
  | 'contact_info'
  | 'website'
  | 'wechat'
  | 'douyin'
  | 'social_media'
  | 'audience_desc'
  | 'business_license_code'
  | 'selling_points'
  | 'filler_name'
  | 'ad_review_no'
  | 'ad_review_authority'
  | 'ad_review_expiry'
  | 'review_category'
  | 'pre_review_required'
  | 'truth_confirmed'
  | 'goals'
  | 'audience_type'
  | 'platforms'
  | 'regions'
  | 'trademarks'
  | 'ad_review_doc_types'
  | 'evidence_links'
> & {
  prefilled: Record<string, string>;
  licenses: IntakeLicenseRow[];
};
export type IntakePromoKind = 'product' | 'company';
export type IntakePromoPayload = Record<string, string | string[]>;
export type IntakePromoView = {
  pub_id: string;
  kind: IntakePromoKind;
  payload: IntakePromoPayload;
  created_at: string;
  updated_at: string;
};
export type IntakePromoCreate = { kind: IntakePromoKind; payload: IntakePromoPayload };
export type IntakePromoPatch = { payload: IntakePromoPayload };
export type IntakeTriggerStatus = 'draft' | 'claim_created';
export type IntakeTriggerView = {
  pub_id: string;
  text: string;
  status: IntakeTriggerStatus;
  created_at: string;
};
export type IntakeTriggerBatch = {
  items: IntakeTriggerView[];
  skipped_duplicates: string[];
};
export type IntakeFormSchemaOption = { value: string; label: string };
export type IntakeFormSchemaField = {
  key: string;
  label: string;
  type: string;
  required: boolean;
  hint: string | null;
  options: IntakeFormSchemaOption[];
  items: string[];
};
export type IntakeFormSchemaSection = {
  id: string;
  title: string;
  fields: IntakeFormSchemaField[];
};
export type IntakeFormSchema = {
  title: string;
  note: string;
  sections: IntakeFormSchemaSection[];
};
export type IntakeAiResearchSource = { title: string; url: string };
export type IntakeAiResearchSummary = {
  model: string;
  rounds: number;
  summary: string;
  sources: IntakeAiResearchSource[];
  dropped: Record<string, number>;
  prefilled: string[];
  unavailable: string[];
  unfilled: string[];
  promosCreated: number;
  triggersCreated: number;
  triggersSkipped: number;
  inputTokens: number;
  outputTokens: number;
};
export type IntakeAiResearchResult =
  | { kind: 'ready'; data: IntakeAiResearchSummary }
  | { kind: 'disabled' }
  | { kind: 'forbidden' }
  | { kind: 'unavailable' };
export type IntakeProfileDocx = { blob: Blob; byteSize: number; mimeType: string };

export const customerIntakeProjectionLimits = {
  promos: 50,
  triggers: 200,
  licenses: 20,
  listItems: 100,
  formSchemaSections: 10,
  formSchemaFields: 60,
  formSchemaOptions: 100,
  truthItems: 20,
  researchSources: 50,
} as const;

const intakeProfileFieldKeys = new Set([
  'contact_person',
  'contact_info',
  'website',
  'wechat',
  'douyin',
  'social_media',
  'audience_desc',
  'business_license_code',
  'selling_points',
  'filler_name',
  'ad_review_no',
  'ad_review_authority',
  'ad_review_expiry',
  'review_category',
  'pre_review_required',
  'truth_confirmed',
  'goals',
  'audience_type',
  'platforms',
  'regions',
  'trademarks',
  'ad_review_doc_types',
  'evidence_links',
  'licenses',
  'promos',
  'trigger_questions',
]);
const intakeReviewCategories = new Set(['A', 'B', 'C', 'D', 'none']);
const intakePromoPayloadShape: Record<IntakePromoKind, { scalars: string[]; lists: string[] }> = {
  product: { scalars: ['name', 'category', 'desc', 'price'], lists: ['features'] },
  company: { scalars: ['name', 'advantage', 'cases', 'data'], lists: ['strength'] },
};
const intakeFormFieldTypes = new Set([
  'text',
  'textarea',
  'radio',
  'bool',
  'chips',
  'tags',
  'subform',
  'confirm',
  'date',
]);
const intakeDocxMimeType =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

/** null 透传；非空字符串过 DLP + 限长；其他任何形状 = undefined（判 invalid）。 */
const safeIntakeNullable = (value: unknown, maxLength: number): string | null | undefined => {
  if (value === null || value === undefined) return null;
  return safeBrowserString(value, maxLength) ?? undefined;
};
const safeIntakeBoolean = (value: unknown): boolean | null | undefined => {
  if (value === null || value === undefined) return null;
  return typeof value === 'boolean' ? value : undefined;
};
const safeIntakeStringList = (
  value: unknown,
  maxItems: number,
  maxLength: number,
): string[] | null => {
  if (!Array.isArray(value) || value.length > maxItems) return null;
  const out: string[] = [];
  for (const item of value) {
    const projected = safeBrowserString(item, maxLength);
    if (!projected) return null;
    out.push(projected);
  }
  return out;
};
const safeIntakeLicenses = (value: unknown): IntakeLicenseRow[] | null => {
  if (!Array.isArray(value) || value.length > customerIntakeProjectionLimits.licenses) {
    return null;
  }
  const rows: IntakeLicenseRow[] = [];
  for (const item of value) {
    if (!isBrowserRecord(item)) return null;
    if (Object.keys(item).some((key) => key !== 'name' && key !== 'number' && key !== 'expiry')) {
      return null;
    }
    const cell = (raw: unknown): string | null => {
      if (raw === undefined || raw === null || raw === '') return '';
      return safeBrowserString(raw, 200);
    };
    const name = cell(item.name);
    const number = cell(item.number);
    const expiry = cell(item.expiry);
    if (name === null || number === null || expiry === null) return null;
    rows.push({ name, number, expiry });
  }
  return rows;
};
const safeIntakePrefilled = (value: unknown): Record<string, string> | null => {
  if (!isBrowserRecord(value)) return null;
  const out: Record<string, string> = {};
  for (const [key, raw] of Object.entries(value)) {
    if (!intakeProfileFieldKeys.has(key)) return null;
    const projected = safeBrowserString(raw, 120);
    if (!projected) return null;
    out[key] = projected;
  }
  return out;
};

const projectIntakeProfileBoundaryView = (
  value: unknown,
  expectedProjectPubId: string,
): IntakeProfileView | null => {
  if (!isBrowserRecord(value)) return null;
  const projectPubId = safeBrowserString(value.project_pub_id, 120);
  const exists = typeof value.exists === 'boolean' ? value.exists : null;
  const prefilled = safeIntakePrefilled(value.prefilled);
  const updatedAt =
    value.updated_at === null || value.updated_at === undefined
      ? null
      : (projectSafeIsoTimestamp(value.updated_at) ?? undefined);
  if (
    !projectPubId ||
    projectPubId !== expectedProjectPubId ||
    exists === null ||
    !prefilled ||
    updatedAt === undefined
  ) {
    return null;
  }
  const scalars = {
    contact_person: safeIntakeNullable(value.contact_person, 200),
    contact_info: safeIntakeNullable(value.contact_info, 500),
    website: safeIntakeNullable(value.website, 500),
    wechat: safeIntakeNullable(value.wechat, 200),
    douyin: safeIntakeNullable(value.douyin, 200),
    social_media: safeIntakeNullable(value.social_media, 2000),
    audience_desc: safeIntakeNullable(value.audience_desc, 2000),
    business_license_code: safeIntakeNullable(value.business_license_code, 18),
    selling_points: safeIntakeNullable(value.selling_points, 2000),
    filler_name: safeIntakeNullable(value.filler_name, 200),
    ad_review_no: safeIntakeNullable(value.ad_review_no, 200),
    ad_review_authority: safeIntakeNullable(value.ad_review_authority, 200),
    ad_review_expiry: safeIntakeNullable(value.ad_review_expiry, 40),
  } as const;
  if (Object.values(scalars).some((entry) => entry === undefined)) return null;
  const reviewCategory = safeIntakeNullable(value.review_category, 10);
  if (reviewCategory === undefined) return null;
  if (reviewCategory !== null && !intakeReviewCategories.has(reviewCategory)) return null;
  const preReviewRequired = safeIntakeBoolean(value.pre_review_required);
  const truthConfirmed = safeIntakeBoolean(value.truth_confirmed);
  if (preReviewRequired === undefined || truthConfirmed === undefined) return null;
  const lists = {
    goals: safeIntakeStringList(value.goals, customerIntakeProjectionLimits.listItems, 500),
    audience_type: safeIntakeStringList(
      value.audience_type,
      customerIntakeProjectionLimits.listItems,
      500,
    ),
    platforms: safeIntakeStringList(value.platforms, customerIntakeProjectionLimits.listItems, 500),
    regions: safeIntakeStringList(value.regions, customerIntakeProjectionLimits.listItems, 500),
    trademarks: safeIntakeStringList(
      value.trademarks,
      customerIntakeProjectionLimits.listItems,
      500,
    ),
    ad_review_doc_types: safeIntakeStringList(
      value.ad_review_doc_types,
      customerIntakeProjectionLimits.listItems,
      500,
    ),
    evidence_links: safeIntakeStringList(
      value.evidence_links,
      customerIntakeProjectionLimits.listItems,
      500,
    ),
  } as const;
  if (Object.values(lists).some((entry) => entry === null)) return null;
  const licenses = safeIntakeLicenses(value.licenses);
  if (!licenses) return null;
  return {
    project_pub_id: projectPubId,
    exists,
    prefilled,
    updated_at: updatedAt,
    ...(scalars as Record<keyof typeof scalars, string | null> as unknown as Pick<
      IntakeProfileView,
      keyof typeof scalars
    >),
    review_category: reviewCategory,
    pre_review_required: preReviewRequired,
    truth_confirmed: truthConfirmed,
    ...(lists as Record<keyof typeof lists, string[]> as unknown as Pick<
      IntakeProfileView,
      keyof typeof lists
    >),
    licenses,
  };
};

/** PUT 回读校验：body 里出现的每个字段都必须与返回视图一致（幂等重放也成立）。 */
const intakeProfileWriteMatches = (view: IntakeProfileView, body: IntakeProfileWrite): boolean =>
  (Object.keys(body) as (keyof IntakeProfileWrite)[]).every((key) => {
    const sent = body[key];
    if (sent === undefined) return true;
    const current = view[key as keyof IntakeProfileView];
    return JSON.stringify(sent ?? null) === JSON.stringify(current ?? null);
  });

const projectIntakePromoBoundaryView = (value: unknown): IntakePromoView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeResourcePubId(value.pub_id);
  const kind = value.kind === 'product' || value.kind === 'company' ? value.kind : null;
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  const updatedAt = projectSafeIsoTimestamp(value.updated_at);
  if (!pubId || !kind || !createdAt || !updatedAt || !isBrowserRecord(value.payload)) return null;
  const shape = intakePromoPayloadShape[kind];
  const payload: IntakePromoPayload = {};
  for (const [key, raw] of Object.entries(value.payload)) {
    if (shape.scalars.includes(key)) {
      const projected = raw === '' ? '' : safeBrowserString(raw, 2000);
      if (projected === null) return null;
      payload[key] = projected;
    } else if (shape.lists.includes(key)) {
      const list = safeIntakeStringList(raw, customerIntakeProjectionLimits.listItems, 500);
      if (!list) return null;
      payload[key] = list;
    } else {
      return null;
    }
  }
  return { pub_id: pubId, kind, payload, created_at: createdAt, updated_at: updatedAt };
};

const projectIntakeTriggerBoundaryView = (value: unknown): IntakeTriggerView | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeResourcePubId(value.pub_id);
  const text = safeBrowserString(value.text, 500);
  const status = value.status === 'draft' || value.status === 'claim_created' ? value.status : null;
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId && text && status && createdAt
    ? { pub_id: pubId, text, status, created_at: createdAt }
    : null;
};

const projectIntakeFormSchema = (value: unknown): IntakeFormSchema | null => {
  if (!isBrowserRecord(value)) return null;
  const title = safeBrowserString(value.title, 200);
  const note = safeBrowserString(value.note, 500);
  if (
    !title ||
    !note ||
    !Array.isArray(value.sections) ||
    value.sections.length > customerIntakeProjectionLimits.formSchemaSections
  ) {
    return null;
  }
  const sections: IntakeFormSchemaSection[] = [];
  for (const section of value.sections) {
    if (!isBrowserRecord(section)) return null;
    const id = safeBrowserString(section.id, 60);
    const sectionTitle = safeBrowserString(section.title, 120);
    if (
      !id ||
      !sectionTitle ||
      !Array.isArray(section.fields) ||
      section.fields.length > customerIntakeProjectionLimits.formSchemaFields
    ) {
      return null;
    }
    const fields: IntakeFormSchemaField[] = [];
    for (const field of section.fields) {
      if (!isBrowserRecord(field)) return null;
      const key = safeBrowserString(field.key, 60);
      const label = safeBrowserString(field.label, 200);
      const type = safeBrowserString(field.type, 40);
      if (!key || !label || !type || !intakeFormFieldTypes.has(type)) return null;
      const hint =
        field.hint === undefined || field.hint === null
          ? null
          : (safeBrowserString(field.hint, 500) ?? undefined);
      if (hint === undefined) return null;
      const options: IntakeFormSchemaOption[] = [];
      if (field.options !== undefined) {
        if (
          !Array.isArray(field.options) ||
          field.options.length > customerIntakeProjectionLimits.formSchemaOptions
        ) {
          return null;
        }
        for (const option of field.options) {
          if (!isBrowserRecord(option)) return null;
          const optionValue = safeBrowserString(option.value, 200);
          const optionLabel = safeBrowserString(option.label, 300);
          if (!optionValue || !optionLabel) return null;
          options.push({ value: optionValue, label: optionLabel });
        }
      }
      const items: string[] = [];
      if (field.items !== undefined) {
        if (
          !Array.isArray(field.items) ||
          field.items.length > customerIntakeProjectionLimits.truthItems
        ) {
          return null;
        }
        for (const item of field.items) {
          const projected = safeBrowserString(item, 300);
          if (!projected) return null;
          items.push(projected);
        }
      }
      fields.push({ key, label, type, required: field.required === true, hint, options, items });
    }
    sections.push({ id, title: sectionTitle, fields });
  }
  return { title, note, sections };
};

const projectIntakeAiResearch = (value: unknown): IntakeAiResearchSummary | null => {
  if (!isBrowserRecord(value)) return null;
  const model = safeBrowserString(value.model, 120);
  const summary =
    value.summary === ''
      ? ''
      : value.summary === null
        ? ''
        : safeBrowserString(value.summary, 4000);
  const rounds =
    typeof value.rounds === 'number' &&
    Number.isSafeInteger(value.rounds) &&
    value.rounds >= 1 &&
    value.rounds <= 10
      ? value.rounds
      : null;
  if (!model || summary === null || rounds === null) return null;
  if (
    !Array.isArray(value.sources) ||
    value.sources.length > customerIntakeProjectionLimits.researchSources
  ) {
    return null;
  }
  const sources: IntakeAiResearchSource[] = [];
  for (const source of value.sources) {
    if (!isBrowserRecord(source)) return null;
    const sourceTitle = source.title === '' ? '' : safeBrowserString(source.title, 300);
    const sourceUrl = safeBrowserString(source.url, 2000);
    if (sourceTitle === null || !sourceUrl) return null;
    sources.push({ title: sourceTitle, url: sourceUrl });
  }
  if (!isBrowserRecord(value.dropped) || Object.keys(value.dropped).length > 100) return null;
  const dropped: Record<string, number> = {};
  for (const [key, raw] of Object.entries(value.dropped)) {
    if (!safeBrowserString(key, 120)) return null;
    const count = safeCount(raw);
    if (count === null) return null;
    dropped[key] = count;
  }
  const prefilled = safeIntakeStringList(value.prefilled ?? [], 100, 120);
  const unavailable = safeIntakeStringList(value.unavailable ?? [], 100, 120);
  const unfilled = safeIntakeStringList(value.unfilled ?? [], 100, 120);
  if (!prefilled || !unavailable || !unfilled) return null;
  if (!Array.isArray(value.promos_created) || !Array.isArray(value.triggers_created)) return null;
  if (!Array.isArray(value.triggers_skipped)) return null;
  const usage = isBrowserRecord(value.usage) ? value.usage : {};
  const inputTokens = safeCount(usage.input_tokens) ?? 0;
  const outputTokens = safeCount(usage.output_tokens) ?? 0;
  return {
    model,
    rounds,
    summary,
    sources,
    dropped,
    prefilled,
    unavailable,
    unfilled,
    promosCreated: value.promos_created.length,
    triggersCreated: value.triggers_created.length,
    triggersSkipped: value.triggers_skipped.length,
    inputTokens,
    outputTokens,
  };
};

export async function getIntakeFormSchema(
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<IntakeFormSchema>> {
  try {
    const result = await projectedApiClient(client).GET('/api/v2/intake/form-schema');
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectIntakeFormSchema(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getIntakeProfile(
  projectPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<IntakeProfileView>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/projects/{project_pub_id}/intake/profile',
      {
        params: { path: { project_pub_id: projectPubId }, header: headers },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectIntakeProfileBoundaryView(result.data, projectPubId);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function putIntakeProfile(
  projectPubId: string,
  body: IntakeProfileWrite,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<IntakeProfileView>> {
  try {
    const result = await projectedApiClient(client).PUT(
      '/api/v2/projects/{project_pub_id}/intake/profile',
      {
        params: { path: { project_pub_id: projectPubId }, header: headers },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectIntakeProfileBoundaryView(result.data, projectPubId);
    return projected && intakeProfileWriteMatches(projected, body)
      ? { kind: 'ready', data: projected }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listIntakePromos(
  projectPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ProjectedCollection<IntakePromoView>>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/projects/{project_pub_id}/intake/promos',
      {
        params: { path: { project_pub_id: projectPubId }, header: headers },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const items = (result.data as { items?: unknown }).items;
    if (!Array.isArray(items)) return { kind: 'unavailable' };
    return {
      kind: 'ready',
      data: projectBoundedCollection(
        items,
        customerIntakeProjectionLimits.promos,
        projectIntakePromoBoundaryView,
      ),
    };
  } catch {
    return { kind: 'unavailable' };
  }
}

/** Generated-contract write boundary; callers must supply a fresh, non-secret idempotency key. */
export async function createIntakePromo(
  projectPubId: string,
  body: IntakePromoCreate,
  headers: IdentitySessionHeaders,
  idempotencyKey: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<IntakePromoView>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/projects/{project_pub_id}/intake/promos',
      {
        params: {
          path: { project_pub_id: projectPubId },
          header: { ...headers, 'Idempotency-Key': idempotencyKey },
        },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectIntakePromoBoundaryView(result.data);
    return projected && projected.kind === body.kind
      ? { kind: 'ready', data: projected }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function updateIntakePromo(
  projectPubId: string,
  promoPubId: string,
  body: IntakePromoPatch,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<IntakePromoView>> {
  try {
    const result = await projectedApiClient(client).PATCH(
      '/api/v2/projects/{project_pub_id}/intake/promos/{promo_pub_id}',
      {
        params: {
          path: { project_pub_id: projectPubId, promo_pub_id: promoPubId },
          header: headers,
        },
        body,
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectIntakePromoBoundaryView(result.data);
    return projected && projected.pub_id === promoPubId
      ? { kind: 'ready', data: projected }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function deleteIntakePromo(
  projectPubId: string,
  promoPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<{ deleted: string }>> {
  try {
    const result = await projectedApiClient(client).DELETE(
      '/api/v2/projects/{project_pub_id}/intake/promos/{promo_pub_id}',
      {
        params: {
          path: { project_pub_id: projectPubId, promo_pub_id: promoPubId },
          header: headers,
        },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const deleted = safeResourcePubId((result.data as { deleted?: unknown }).deleted);
    return deleted && deleted === promoPubId
      ? { kind: 'ready', data: { deleted } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listIntakeTriggers(
  projectPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ProjectedCollection<IntakeTriggerView>>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/projects/{project_pub_id}/intake/trigger-questions',
      {
        params: { path: { project_pub_id: projectPubId }, header: headers },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const items = (result.data as { items?: unknown }).items;
    if (!Array.isArray(items)) return { kind: 'unavailable' };
    return {
      kind: 'ready',
      data: projectBoundedCollection(
        items,
        customerIntakeProjectionLimits.triggers,
        projectIntakeTriggerBoundaryView,
      ),
    };
  } catch {
    return { kind: 'unavailable' };
  }
}

/** Generated-contract write boundary; callers must supply a fresh, non-secret idempotency key. */
export async function createIntakeTriggers(
  projectPubId: string,
  text: string,
  headers: IdentitySessionHeaders,
  idempotencyKey: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<IntakeTriggerBatch>> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/projects/{project_pub_id}/intake/trigger-questions',
      {
        params: {
          path: { project_pub_id: projectPubId },
          header: { ...headers, 'Idempotency-Key': idempotencyKey },
        },
        body: { text },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const raw = result.data as { items?: unknown; skipped_duplicates?: unknown };
    if (!Array.isArray(raw.items) || raw.items.length > customerIntakeProjectionLimits.triggers) {
      return { kind: 'unavailable' };
    }
    const items = raw.items.flatMap((item) => {
      const projected = projectIntakeTriggerBoundaryView(item);
      return projected ? [projected] : [];
    });
    const skipped = safeIntakeStringList(
      raw.skipped_duplicates ?? [],
      customerIntakeProjectionLimits.triggers,
      500,
    );
    return items.length === raw.items.length && skipped
      ? { kind: 'ready', data: { items, skipped_duplicates: skipped } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function updateIntakeTrigger(
  projectPubId: string,
  triggerPubId: string,
  text: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<IntakeTriggerView>> {
  try {
    const result = await projectedApiClient(client).PATCH(
      '/api/v2/projects/{project_pub_id}/intake/trigger-questions/{trigger_pub_id}',
      {
        params: {
          path: { project_pub_id: projectPubId, trigger_pub_id: triggerPubId },
          header: headers,
        },
        body: { text },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectIntakeTriggerBoundaryView(result.data);
    return projected && projected.pub_id === triggerPubId
      ? { kind: 'ready', data: projected }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function deleteIntakeTrigger(
  projectPubId: string,
  triggerPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<{ deleted: string }>> {
  try {
    const result = await projectedApiClient(client).DELETE(
      '/api/v2/projects/{project_pub_id}/intake/trigger-questions/{trigger_pub_id}',
      {
        params: {
          path: { project_pub_id: projectPubId, trigger_pub_id: triggerPubId },
          header: headers,
        },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const deleted = safeResourcePubId((result.data as { deleted?: unknown }).deleted);
    return deleted && deleted === triggerPubId
      ? { kind: 'ready', data: { deleted } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

/** 同步长调用（最长 5 轮 LLM，可能 1-3 分钟）；503 llm_disabled → kind:'disabled'。 */
export async function runIntakeAiResearch(
  projectPubId: string,
  body: { brand: string; website?: string; model?: string },
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeAiResearchResult> {
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/projects/{project_pub_id}/intake/ai-research',
      {
        params: { path: { project_pub_id: projectPubId }, header: headers },
        body: {
          brand: body.brand,
          ...(body.website ? { website: body.website } : {}),
          ...(body.model ? { model: body.model } : {}),
        },
      },
    );
    if (!result.data) {
      if (result.response.status === 503) return { kind: 'disabled' };
      return classifyResourceFailure(result.response.status);
    }
    const projected = projectIntakeAiResearch(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

const projectIntakeResearchModels = (value: unknown): string[] | null => {
  if (!isBrowserRecord(value) || !Array.isArray(value.models)) return null;
  if (value.models.length === 0 || value.models.length > 16) return null;
  const models: string[] = [];
  for (const entry of value.models) {
    const model = safeBrowserString(entry, 120);
    if (!model) return null;
    models.push(model);
  }
  return models;
};

export type ResearchModelCatalog = {
  models: string[];
  groups: { provider: string; models: string[] }[];
};

const projectIntakeResearchModelCatalog = (value: unknown): ResearchModelCatalog | null => {
  if (!isBrowserRecord(value)) return null;
  const models = projectIntakeResearchModels({ models: value.models });
  if (!models) return null;
  if (!Array.isArray(value.groups) || value.groups.length > 16) return null;
  const groups: { provider: string; models: string[] }[] = [];
  for (const entry of value.groups) {
    if (!isBrowserRecord(entry)) return null;
    const provider = safeBrowserString(entry.provider, 40);
    const groupModels = projectIntakeResearchModels({ models: entry.models });
    if (!provider || !groupModels) return null;
    groups.push({ provider, models: groupModels });
  }
  return { models, groups };
};

/** 调研模型清单（服务端 GEO_RESEARCH_LLM_MODELS 为唯一真源；groups=按 provider 级联分组）。 */
export async function getIntakeResearchModels(
  projectPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<ResearchModelCatalog>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/projects/{project_pub_id}/intake/research-models',
      {
        params: { path: { project_pub_id: projectPubId }, header: headers },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const projected = projectIntakeResearchModelCatalog(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

/** docx 二进制流下载（blob 通道，校验 MIME 与大小上界；无服务端 sha256 可核对）。 */
export async function getIntakeProfileDocx(
  projectPubId: string,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<IntakeProfileDocx>> {
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/projects/{project_pub_id}/intake/profile.docx',
      {
        params: { path: { project_pub_id: projectPubId }, header: headers },
        parseAs: 'blob',
      },
    );
    if (
      !(result.data instanceof Blob) ||
      result.data.type !== intakeDocxMimeType ||
      result.data.size <= 0 ||
      result.data.size > 20 * 1024 * 1024
    ) {
      return result.data
        ? { kind: 'unavailable' }
        : classifyResourceFailure(result.response.status);
    }
    return {
      kind: 'ready',
      data: { blob: result.data, byteSize: result.data.size, mimeType: result.data.type },
    };
  } catch {
    return { kind: 'unavailable' };
  }
}

export type QuotationGenerationInput = {
  brandName: string;
  targetWords: File;
  quoteDate?: string;
  model?: string;
};

export type GeneratedQuotationDocument = {
  blob: Blob;
  fileName: string;
  sha256: string;
  targetQueryCount: number;
  selectedQueryCount: number;
  opportunityCount: number;
};

export type QuotationGenerationResult =
  | { kind: 'ready'; data: GeneratedQuotationDocument }
  | { kind: 'forbidden' }
  | { kind: 'invalid' }
  | { kind: 'disabled' }
  | { kind: 'failed' }
  | { kind: 'unavailable' };

const quotationXlsxMimeTypes = new Set([
  '',
  'application/octet-stream',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
]);
const quotationMaxWorkbookBytes = 10 * 1024 * 1024;
const quotationMaxDocumentBytes = 20 * 1024 * 1024;

const quotationHeaderCount = (value: string | null, maximum: number): number | null => {
  if (!value || !/^(?:0|[1-9]\d*)$/u.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 1 && parsed <= maximum ? parsed : null;
};

const quotationFileName = (contentDisposition: string | null): string | null => {
  const encoded = /(?:^|;)\s*filename\*=UTF-8''([^;]+)/iu.exec(contentDisposition ?? '')?.[1];
  if (!encoded) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(encoded).normalize('NFC');
  } catch {
    return null;
  }
  const projected = safeBrowserString(decoded, 180);
  return projected && projected.endsWith('.docx') && !/[\\/\x00-\x1f\x7f]/u.test(projected)
    ? projected
    : null;
};

const classifyQuotationFailure = (status: number): QuotationGenerationResult => {
  if (status === 401 || status === 403) return { kind: 'forbidden' };
  if (status === 400 || status === 415 || status === 422) return { kind: 'invalid' };
  if (status === 503) return { kind: 'disabled' };
  if (status === 502) return { kind: 'failed' };
  return { kind: 'unavailable' };
};

/**
 * 运营端报价单生成：multipart 上传 XLSX，DOCX 只经受控 Blob 通道返回；同时校验
 * MIME、ZIP 签名、体积、服务端 SHA-256 与计数元数据，失败时不把响应交给下载层。
 */
export async function generateQuotation(
  input: QuotationGenerationInput,
  headers: IdentitySessionHeaders,
  client: ProjectedApiClientOverride = apiClient,
): Promise<QuotationGenerationResult> {
  const brandName = input.brandName.normalize('NFC').replace(/\s+/gu, ' ').trim();
  const quoteDate = input.quoteDate?.trim() ?? '';
  const model = input.model?.trim() ?? '';
  if (
    !safeBrowserString(brandName, 80) ||
    brandName.length < 2 ||
    !(input.targetWords instanceof File) ||
    !input.targetWords.name.toLowerCase().endsWith('.xlsx') ||
    input.targetWords.size <= 0 ||
    input.targetWords.size > quotationMaxWorkbookBytes ||
    !quotationXlsxMimeTypes.has(input.targetWords.type.toLowerCase()) ||
    (quoteDate !== '' && !/^20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$/u.test(quoteDate)) ||
    (model !== '' && !safeBrowserString(model, 120))
  ) {
    return { kind: 'invalid' };
  }

  const form = new FormData();
  form.set('brand_name', brandName);
  form.set('target_words', input.targetWords);
  if (quoteDate) form.set('quote_date', quoteDate);
  if (model) form.set('model', model);
  try {
    const result = await projectedApiClient(client).POST('/api/v2/quotations/generate', {
      params: { header: headers },
      body: form as never,
      bodySerializer: () => form,
      parseAs: 'blob',
    });
    if (!(result.data instanceof Blob)) return classifyQuotationFailure(result.response.status);

    const sha256 = safeHash(result.response.headers.get('x-quotation-sha256'));
    const fileName = quotationFileName(result.response.headers.get('content-disposition'));
    const targetQueryCount = quotationHeaderCount(
      result.response.headers.get('x-quotation-target-query-count'),
      300,
    );
    const selectedQueryCount = quotationHeaderCount(
      result.response.headers.get('x-quotation-selected-query-count'),
      18,
    );
    const opportunityCount = quotationHeaderCount(
      result.response.headers.get('x-quotation-opportunity-count'),
      16,
    );
    if (
      result.data.type !== quotationDocxMimeType ||
      result.data.size <= 0 ||
      result.data.size > quotationMaxDocumentBytes ||
      !sha256 ||
      !fileName ||
      targetQueryCount === null ||
      selectedQueryCount === null ||
      opportunityCount === null
    ) {
      return { kind: 'unavailable' };
    }
    const bytes = await result.data.arrayBuffer();
    const signature = new Uint8Array(bytes, 0, Math.min(4, bytes.byteLength));
    if (
      signature.length < 4 ||
      signature[0] !== 0x50 ||
      signature[1] !== 0x4b ||
      signature[2] !== 0x03 ||
      signature[3] !== 0x04
    ) {
      return { kind: 'unavailable' };
    }
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    const actualSha256 = [...new Uint8Array(digest)]
      .map((value) => value.toString(16).padStart(2, '0'))
      .join('');
    if (actualSha256 !== sha256) return { kind: 'unavailable' };
    return {
      kind: 'ready',
      data: {
        blob: result.data,
        fileName,
        sha256,
        targetQueryCount,
        selectedQueryCount,
        opportunityCount,
      },
    };
  } catch {
    return { kind: 'unavailable' };
  }
}

// ── Intake Form（免登录客户填表，X-Intake-Token 邀请域）投影与写边界 ──────────
// 契约真源：api/geo_platform/intake_form（token 域匿名端点，principal 从
// X-Intake-Token 解析）。与身份域 wrapper 不同：无租户/角色头，失败语义来自
// 稳定错误码（401 缺头 / 403 invalid|expired|revoked / 409 invite_submitted /
// 429 quota_exhausted / 503 llm_disabled / 502 research_failed），投影纪律不变：
// 边界 DLP、词表/形状 fail-closed、判别联合 ready/failed(code)。
type GeneratedIntakeFormTokenHeaders = NonNullable<
  paths['/api/v2/intake-form/context']['get']['parameters']['header']
>;
export type IntakeFormTokenHeaders = Pick<GeneratedIntakeFormTokenHeaders, 'X-Intake-Token'>;

export type IntakeFormFailureCode =
  | 'intake_token_missing'
  | 'invite_token_invalid'
  | 'invite_token_expired'
  | 'invite_token_revoked'
  | 'invite_submitted'
  | 'quota_exhausted'
  | 'llm_disabled'
  | 'research_failed'
  | 'submit_incomplete'
  | 'validation_failed'
  | 'unavailable';
export type IntakeFormResult<T> =
  | { kind: 'ready'; data: T }
  | { kind: 'failed'; code: IntakeFormFailureCode };

export type IntakeFormInviteState = {
  pub_id: string;
  expires_at: string;
  submitted: boolean;
  submitted_at: string | null;
  ai_quota: number;
  ai_used: number;
  ai_remaining: number;
};
export type IntakeFormBrand = {
  exists: boolean;
  pub_id: string | null;
  name: string | null;
  website: string | null;
  aliases: string[];
};
export type IntakeFormBrandPatch = { name?: string; website?: string | null; aliases?: string[] };
export type IntakeFormCompetitor = {
  pub_id: string;
  name: string;
  website: string | null;
  created_at: string;
};
export type IntakeFormContext = {
  form: IntakeFormSchema;
  brand: IntakeFormBrand;
  competitors: IntakeFormCompetitor[];
  profile: IntakeProfileView;
  invite: IntakeFormInviteState;
};
export type IntakeFormAiResearchSummary = IntakeAiResearchSummary & {
  ai_used: number;
  ai_remaining: number;
};
export type IntakeFormSuggestion = { question: string; core_word: string; heat: number };
export type IntakeFormSuggestions = {
  items: IntakeFormSuggestion[];
  ai_used: number;
  ai_remaining: number;
};
export type IntakeFormSiliconCompetitorSuggestion = { name: string; website: string | null };
export type IntakeFormSiliconCandidates =
  | { available: false }
  | {
      available: true;
      matched: boolean;
      display_name: string | null;
      category_path: string[];
      aliases: string[];
      competitors: IntakeFormSiliconCompetitorSuggestion[];
      disclaimer: string | null;
    };
export type IntakeFormTemplateQuestion = { text: string; intent: string | null };
export type IntakeFormTemplateQuestions =
  | { available: false }
  | { available: true; matched: boolean; questions: IntakeFormTemplateQuestion[] };
export type IntakeFormSubmitReceipt = { submitted: true; submitted_at: string; replay: boolean };

export const intakeFormProjectionLimits = {
  competitors: 50,
  aliases: 50,
  categoryPath: 8,
  siliconCompetitors: 20,
  suggestions: 50,
  templateQuestions: 100,
} as const;

const intakeFormFailureCodes = new Set<IntakeFormFailureCode>([
  'intake_token_missing',
  'invite_token_invalid',
  'invite_token_expired',
  'invite_token_revoked',
  'invite_submitted',
  'quota_exhausted',
  'llm_disabled',
  'research_failed',
  'submit_incomplete',
]);

/** 稳定错误码分类：缺头 401 优先；语义码白名单透传；其余 422 → validation_failed。 */
const classifyIntakeFormFailure = (status: number, error: unknown): IntakeFormFailureCode => {
  if (status === 401) return 'intake_token_missing';
  const code =
    isBrowserRecord(error) && isBrowserRecord(error.error) ? error.error.code : undefined;
  if (typeof code === 'string' && intakeFormFailureCodes.has(code as IntakeFormFailureCode)) {
    return code as IntakeFormFailureCode;
  }
  return status === 422 ? 'validation_failed' : 'unavailable';
};

const intakeFormFailure = <T>(status: number, error: unknown): IntakeFormResult<T> => ({
  kind: 'failed',
  code: classifyIntakeFormFailure(status, error),
});

/** token 形状门槛（与后端一致：非空、无空白、≤200、无控制字符）。 */
export function intakeFormTokenHeaders(token: string): IntakeFormTokenHeaders | null {
  if (
    typeof token !== 'string' ||
    token.length === 0 ||
    token.length > 200 ||
    /\s/u.test(token) ||
    /\p{Cc}/u.test(token)
  ) {
    return null;
  }
  return { 'X-Intake-Token': token };
}

const safeIntakeFormCount = (value: unknown, max: number): number | null =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= max
    ? value
    : null;

const projectIntakeFormInviteState = (value: unknown): IntakeFormInviteState | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeResourcePubId(value.pub_id);
  const expiresAt = projectSafeIsoTimestamp(value.expires_at);
  const submitted = typeof value.submitted === 'boolean' ? value.submitted : null;
  const submittedAt =
    value.submitted_at === null || value.submitted_at === undefined
      ? null
      : (projectSafeIsoTimestamp(value.submitted_at) ?? undefined);
  const aiQuota = safeIntakeFormCount(value.ai_quota, 1000);
  const aiUsed = safeIntakeFormCount(value.ai_used, 1000);
  const aiRemaining = safeIntakeFormCount(value.ai_remaining, 1000);
  if (!pubId || !expiresAt || submitted === null || submittedAt === undefined) return null;
  if (aiQuota === null || aiUsed === null || aiRemaining === null) return null;
  return {
    pub_id: pubId,
    expires_at: expiresAt,
    submitted,
    submitted_at: submittedAt,
    ai_quota: aiQuota,
    ai_used: aiUsed,
    ai_remaining: aiRemaining,
  };
};

const projectIntakeFormBrand = (value: unknown): IntakeFormBrand | null => {
  if (!isBrowserRecord(value)) return null;
  const exists = typeof value.exists === 'boolean' ? value.exists : null;
  const pubId =
    value.pub_id === null || value.pub_id === undefined
      ? null
      : (safeResourcePubId(value.pub_id) ?? undefined);
  const name = safeIntakeNullable(value.name, 200);
  const website = safeIntakeNullable(value.website, 500);
  const aliases = safeIntakeStringList(value.aliases, intakeFormProjectionLimits.aliases, 200);
  if (exists === null || pubId === undefined || name === undefined || website === undefined) {
    return null;
  }
  if (!aliases) return null;
  return { exists, pub_id: pubId, name, website, aliases };
};

const projectIntakeFormCompetitor = (value: unknown): IntakeFormCompetitor | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = safeResourcePubId(value.pub_id);
  const name = safeBrowserString(value.name, 200);
  const website = safeIntakeNullable(value.website, 500);
  const createdAt = projectSafeIsoTimestamp(value.created_at);
  return pubId && name && website !== undefined && createdAt
    ? { pub_id: pubId, name, website, created_at: createdAt }
    : null;
};

const projectIntakeFormContext = (value: unknown): IntakeFormContext | null => {
  if (!isBrowserRecord(value)) return null;
  const form = projectIntakeFormSchema(value.form);
  const brand = projectIntakeFormBrand(value.brand);
  const invite = projectIntakeFormInviteState(value.invite);
  if (!form || !brand || !invite || !isBrowserRecord(value.profile)) return null;
  const expectedProjectPubId = safeBrowserString(value.profile.project_pub_id, 120);
  if (!expectedProjectPubId) return null;
  const profile = projectIntakeProfileBoundaryView(value.profile, expectedProjectPubId);
  if (!profile) return null;
  if (!Array.isArray(value.competitors)) return null;
  if (value.competitors.length > intakeFormProjectionLimits.competitors) return null;
  const competitors: IntakeFormCompetitor[] = [];
  for (const item of value.competitors) {
    const projected = projectIntakeFormCompetitor(item);
    if (!projected) return null;
    competitors.push(projected);
  }
  return { form, brand, competitors, profile, invite };
};

const projectIntakeFormAiResearch = (value: unknown): IntakeFormAiResearchSummary | null => {
  const base = projectIntakeAiResearch(value);
  if (!base || !isBrowserRecord(value)) return null;
  const aiUsed = safeIntakeFormCount(value.ai_used, 1000);
  const aiRemaining = safeIntakeFormCount(value.ai_remaining, 1000);
  if (aiUsed === null || aiRemaining === null) return null;
  return { ...base, ai_used: aiUsed, ai_remaining: aiRemaining };
};

const projectIntakeFormSuggestions = (value: unknown): IntakeFormSuggestions | null => {
  if (!isBrowserRecord(value) || value.candidate_only !== true) return null;
  if (!Array.isArray(value.questions)) return null;
  if (value.questions.length > intakeFormProjectionLimits.suggestions) return null;
  const aiUsed = safeIntakeFormCount(value.ai_used, 1000);
  const aiRemaining = safeIntakeFormCount(value.ai_remaining, 1000);
  if (aiUsed === null || aiRemaining === null) return null;
  const items: IntakeFormSuggestion[] = [];
  for (const item of value.questions) {
    if (!isBrowserRecord(item)) return null;
    const question = safeBrowserString(item.question, 500);
    const coreWord = safeBrowserString(item.core_word, 200);
    const heat = safeIntakeFormCount(item.heat, 100);
    if (!question || !coreWord || heat === null) return null;
    items.push({ question, core_word: coreWord, heat });
  }
  return { items, ai_used: aiUsed, ai_remaining: aiRemaining };
};

const projectIntakeFormSiliconCandidates = (value: unknown): IntakeFormSiliconCandidates | null => {
  if (!isBrowserRecord(value)) return null;
  if (value.available === false) return { available: false };
  if (value.available !== true || typeof value.matched !== 'boolean') return null;
  const brand = isBrowserRecord(value.brand) ? value.brand : null;
  const displayName = brand
    ? (safeIntakeNullable(brand.display_name, 200) ??
      safeIntakeNullable(brand.canonical_name, 200) ??
      null)
    : null;
  const categoryPath = safeIntakeStringList(
    value.category_path ?? [],
    intakeFormProjectionLimits.categoryPath,
    200,
  );
  if (!categoryPath) return null;
  if (!Array.isArray(value.mention_rules)) return null;
  if (value.mention_rules.length > intakeFormProjectionLimits.aliases) return null;
  const aliases: string[] = [];
  for (const rule of value.mention_rules) {
    if (!isBrowserRecord(rule)) return null;
    const text = safeBrowserString(rule.text, 200);
    if (!text) return null;
    aliases.push(text);
  }
  if (!Array.isArray(value.competitors)) return null;
  if (value.competitors.length > intakeFormProjectionLimits.siliconCompetitors) return null;
  const competitors: IntakeFormSiliconCompetitorSuggestion[] = [];
  for (const item of value.competitors) {
    if (!isBrowserRecord(item) || !isBrowserRecord(item.brand)) return null;
    const name =
      safeIntakeNullable(item.brand.display_name, 200) ??
      safeIntakeNullable(item.brand.canonical_name, 200) ??
      null;
    if (!name) return null;
    const website =
      item.brand.website === null || item.brand.website === undefined
        ? null
        : (safeBrowserString(item.brand.website, 500) ?? undefined);
    if (website === undefined) return null;
    competitors.push({ name, website });
  }
  const compliance = isBrowserRecord(value.compliance) ? value.compliance : null;
  const disclaimer = compliance ? (safeIntakeNullable(compliance.disclaimer, 500) ?? null) : null;
  return {
    available: true,
    matched: value.matched,
    display_name: displayName,
    category_path: categoryPath,
    aliases,
    competitors,
    disclaimer,
  };
};

const projectIntakeFormTemplateQuestions = (value: unknown): IntakeFormTemplateQuestions | null => {
  if (!isBrowserRecord(value)) return null;
  if (value.available === false) return { available: false };
  if (value.available !== true || typeof value.matched !== 'boolean') return null;
  if (!Array.isArray(value.questions)) return null;
  if (value.questions.length > intakeFormProjectionLimits.templateQuestions) return null;
  const questions: IntakeFormTemplateQuestion[] = [];
  for (const item of value.questions) {
    if (!isBrowserRecord(item)) return null;
    const text = safeBrowserString(item.text, 500);
    if (!text) return null;
    const intent =
      item.intent === null || item.intent === undefined
        ? null
        : (safeBrowserString(item.intent, 200) ?? undefined);
    if (intent === undefined) return null;
    questions.push({ text, intent });
  }
  return { available: true, matched: value.matched, questions };
};

const projectIntakeFormSubmitReceipt = (value: unknown): IntakeFormSubmitReceipt | null => {
  if (!isBrowserRecord(value) || value.submitted !== true) return null;
  const submittedAt = projectSafeIsoTimestamp(value.submitted_at);
  const replay = typeof value.replay === 'boolean' ? value.replay : null;
  return submittedAt && replay !== null
    ? { submitted: true, submitted_at: submittedAt, replay }
    : null;
};

export async function getIntakeFormContext(
  token: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeFormContext>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).GET('/api/v2/intake-form/context', {
      params: { header },
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeFormContext(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function getIntakeFormProfile(
  token: string,
  expectedProjectPubId: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeProfileView>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).GET('/api/v2/intake-form/profile', {
      params: { header },
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeProfileBoundaryView(result.data, expectedProjectPubId);
    return projected ? { kind: 'ready', data: projected } : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function putIntakeFormProfile(
  token: string,
  expectedProjectPubId: string,
  body: IntakeProfileWrite,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeProfileView>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).PUT('/api/v2/intake-form/profile', {
      params: { header },
      body,
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeProfileBoundaryView(result.data, expectedProjectPubId);
    return projected && intakeProfileWriteMatches(projected, body)
      ? { kind: 'ready', data: projected }
      : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function listIntakeFormPromos(
  token: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<ProjectedCollection<IntakePromoView>>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).GET('/api/v2/intake-form/promos', {
      params: { header },
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const items = (result.data as { items?: unknown }).items;
    if (!Array.isArray(items)) return { kind: 'failed', code: 'unavailable' };
    return {
      kind: 'ready',
      data: projectBoundedCollection(
        items,
        customerIntakeProjectionLimits.promos,
        projectIntakePromoBoundaryView,
      ),
    };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

/** 匿名端按 body 自然幂等（服务端缺省回退 payload 哈希），无需调用方提供幂等键。 */
export async function createIntakeFormPromo(
  token: string,
  body: IntakePromoCreate,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakePromoView>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).POST('/api/v2/intake-form/promos', {
      params: { header },
      body,
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakePromoBoundaryView(result.data);
    return projected && projected.kind === body.kind
      ? { kind: 'ready', data: projected }
      : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function updateIntakeFormPromo(
  token: string,
  promoPubId: string,
  body: IntakePromoPatch,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakePromoView>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).PATCH(
      '/api/v2/intake-form/promos/{promo_pub_id}',
      {
        params: { path: { promo_pub_id: promoPubId }, header },
        body,
      },
    );
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakePromoBoundaryView(result.data);
    return projected && projected.pub_id === promoPubId
      ? { kind: 'ready', data: projected }
      : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function deleteIntakeFormPromo(
  token: string,
  promoPubId: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<{ deleted: string }>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).DELETE(
      '/api/v2/intake-form/promos/{promo_pub_id}',
      {
        params: { path: { promo_pub_id: promoPubId }, header },
      },
    );
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const deleted = safeResourcePubId((result.data as { deleted?: unknown }).deleted);
    return deleted && deleted === promoPubId
      ? { kind: 'ready', data: { deleted } }
      : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function listIntakeFormTriggers(
  token: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<ProjectedCollection<IntakeTriggerView>>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).GET('/api/v2/intake-form/trigger-questions', {
      params: { header },
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const items = (result.data as { items?: unknown }).items;
    if (!Array.isArray(items)) return { kind: 'failed', code: 'unavailable' };
    return {
      kind: 'ready',
      data: projectBoundedCollection(
        items,
        customerIntakeProjectionLimits.triggers,
        projectIntakeTriggerBoundaryView,
      ),
    };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

/** 批量收录：text 每行一条；匿名端按 text 哈希自然幂等。 */
export async function createIntakeFormTriggers(
  token: string,
  text: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeTriggerBatch>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).POST('/api/v2/intake-form/trigger-questions', {
      params: { header },
      body: { text },
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const raw = result.data as { items?: unknown; skipped_duplicates?: unknown };
    if (!Array.isArray(raw.items) || raw.items.length > customerIntakeProjectionLimits.triggers) {
      return { kind: 'failed', code: 'unavailable' };
    }
    const items = raw.items.flatMap((item) => {
      const projected = projectIntakeTriggerBoundaryView(item);
      return projected ? [projected] : [];
    });
    const skipped = safeIntakeStringList(
      raw.skipped_duplicates ?? [],
      customerIntakeProjectionLimits.triggers,
      500,
    );
    return items.length === raw.items.length && skipped
      ? { kind: 'ready', data: { items, skipped_duplicates: skipped } }
      : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function updateIntakeFormTrigger(
  token: string,
  triggerPubId: string,
  text: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeTriggerView>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).PATCH(
      '/api/v2/intake-form/trigger-questions/{trigger_pub_id}',
      {
        params: { path: { trigger_pub_id: triggerPubId }, header },
        body: { text },
      },
    );
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeTriggerBoundaryView(result.data);
    return projected && projected.pub_id === triggerPubId
      ? { kind: 'ready', data: projected }
      : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function deleteIntakeFormTrigger(
  token: string,
  triggerPubId: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<{ deleted: string }>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).DELETE(
      '/api/v2/intake-form/trigger-questions/{trigger_pub_id}',
      {
        params: { path: { trigger_pub_id: triggerPubId }, header },
      },
    );
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const deleted = safeResourcePubId((result.data as { deleted?: unknown }).deleted);
    return deleted && deleted === triggerPubId
      ? { kind: 'ready', data: { deleted } }
      : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

/** 同步长调用（多轮 LLM 联网调研，可能 1-3 分钟）；配额 429 → quota_exhausted。 */
export async function runIntakeFormAiResearch(
  token: string,
  body: { brand: string; website?: string },
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeFormAiResearchSummary>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).POST('/api/v2/intake-form/ai-research', {
      params: { header },
      body: {
        brand: body.brand,
        ...(body.website ? { website: body.website } : {}),
      },
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeFormAiResearch(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

/** AI 扩写问法（candidate_only 不落库；与调研共用配额）。 */
export async function suggestIntakeFormQuestions(
  token: string,
  body: { core_words: string[]; n?: number },
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeFormSuggestions>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).POST('/api/v2/intake-form/query-suggestions', {
      params: { header },
      body: { core_words: body.core_words, n: body.n ?? 12 },
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeFormSuggestions(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

/** SiliconIndex 只读候选；快照缺失时 available:false 优雅降级（整卡隐藏）。 */
export async function getIntakeFormSiliconCandidates(
  token: string,
  name: string | undefined,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeFormSiliconCandidates>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).GET(
      '/api/v2/intake-form/siliconindex/candidates',
      {
        params: { query: name ? { name } : {}, header },
      },
    );
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeFormSiliconCandidates(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

/** SiliconIndex 模板问法预览（candidate_only 不落库）。 */
export async function getIntakeFormSiliconTemplateQuestions(
  token: string,
  body: { region?: string; competitor?: string },
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeFormTemplateQuestions>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).POST(
      '/api/v2/intake-form/siliconindex/template-questions',
      {
        params: { header },
        body: { region: body.region ?? '', competitor: body.competitor ?? '' },
      },
    );
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeFormTemplateQuestions(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function getIntakeFormBrand(
  token: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeFormBrand>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).GET('/api/v2/intake-form/brand', {
      params: { header },
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeFormBrand(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function patchIntakeFormBrand(
  token: string,
  body: IntakeFormBrandPatch,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeFormBrand>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).PATCH('/api/v2/intake-form/brand', {
      params: { header },
      body,
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeFormBrand(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function listIntakeFormCompetitors(
  token: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeFormCompetitor[]>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).GET('/api/v2/intake-form/competitors', {
      params: { header },
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const items = (result.data as { items?: unknown }).items;
    if (!Array.isArray(items) || items.length > intakeFormProjectionLimits.competitors) {
      return { kind: 'failed', code: 'unavailable' };
    }
    const competitors: IntakeFormCompetitor[] = [];
    for (const item of items) {
      const projected = projectIntakeFormCompetitor(item);
      if (!projected) return { kind: 'failed', code: 'unavailable' };
      competitors.push(projected);
    }
    return { kind: 'ready', data: competitors };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function createIntakeFormCompetitor(
  token: string,
  body: { name: string; website?: string | null },
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeFormCompetitor>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).POST('/api/v2/intake-form/competitors', {
      params: { header },
      body: { name: body.name, ...(body.website ? { website: body.website } : {}) },
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeFormCompetitor(result.data);
    return projected && projected.name === body.name
      ? { kind: 'ready', data: projected }
      : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

export async function deleteIntakeFormCompetitor(
  token: string,
  competitorPubId: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<{ deleted: string }>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).DELETE(
      '/api/v2/intake-form/competitors/{competitor_pub_id}',
      {
        params: { path: { competitor_pub_id: competitorPubId }, header },
      },
    );
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const deleted = safeResourcePubId((result.data as { deleted?: unknown }).deleted);
    return deleted && deleted === competitorPubId
      ? { kind: 'ready', data: { deleted } }
      : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

/** 提交（合规亲笔项门在服务端复核；重复提交幂等返回 replay:true）。 */
export async function submitIntakeForm(
  token: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<IntakeFormResult<IntakeFormSubmitReceipt>> {
  const header = intakeFormTokenHeaders(token);
  if (!header) return { kind: 'failed', code: 'intake_token_missing' };
  try {
    const result = await projectedApiClient(client).POST('/api/v2/intake-form/submit', {
      params: { header },
    });
    if (!result.data) return intakeFormFailure(result.response.status, result.error);
    const projected = projectIntakeFormSubmitReceipt(result.data);
    return projected ? { kind: 'ready', data: projected } : { kind: 'failed', code: 'unavailable' };
  } catch {
    return { kind: 'failed', code: 'unavailable' };
  }
}

/* ------------------------------------------------------------------------- */
/* 信源帖子取证分析（/api/v2/post-analysis）浏览器投影边界                        */
/* 词表与 workflows/activities/post_analysis.py 逐字对齐；零合成：非法行/页       */
/* 一律 fail closed（unavailable），绝不编造标签。                                */
/* ------------------------------------------------------------------------- */

export const postAnalysisTaskStatuses = [
  'queued',
  'running',
  'completed',
  'partial',
  'failed',
] as const;
export type PostAnalysisTaskStatus = (typeof postAnalysisTaskStatuses)[number];
export const postAnalysisItemStatuses = [
  'pending',
  'fetching',
  'analyzing',
  'annotating',
  'completed',
  'fetch_failed',
  'analysis_failed',
] as const;
export type PostAnalysisItemStatus = (typeof postAnalysisItemStatuses)[number];
export const postAnalysisAnnotationStatuses = [
  'pending',
  'completed',
  'failed',
  'skipped',
] as const;
export type PostAnalysisAnnotationStatus = (typeof postAnalysisAnnotationStatuses)[number];
export const postAnalysisCategories = [
  'brand_intro',
  'review_ranking',
  'research_report',
  'tech_analysis',
  'evolution_path',
  'brand_story',
  'science_popularization',
  'other',
] as const;
export type PostAnalysisCategory = (typeof postAnalysisCategories)[number];
export const postAnalysisAssetKinds = ['screenshot', 'annotated'] as const;
export type PostAnalysisAssetKind = (typeof postAnalysisAssetKinds)[number];

const postAnalysisSentiments = ['positive', 'neutral', 'negative'] as const;
const postAnalysisDirections = ['target_disparaged', 'disparages_other'] as const;
const postAnalysisSeverities = ['low', 'medium', 'high'] as const;
const postAnalysisVerdicts = ['accurate', 'inaccurate', 'unsupported'] as const;
const postAnalysisAnnotationTypes = ['target_brand', 'disparagement', 'misinformation'] as const;

const postAnalysisAssetMaxBytes = 30 * 1024 * 1024;

export type PostAnalysisTaskSummary = {
  pubId: string;
  targetBrand: string;
  targetBrandAliases: string[];
  status: PostAnalysisTaskStatus;
  urlCount: number;
  error: string | null;
  createdAt: string;
  updatedAt: string;
};

export type PostAnalysisTaskPage = {
  data: PostAnalysisTaskSummary[];
  nextCursor: string | null;
  hasMore: boolean;
};

export type PostAnalysisTaskDetail = {
  task: PostAnalysisTaskSummary;
  statusCounts: { status: PostAnalysisItemStatus; count: number }[];
  /** 命中后自动建立的情报调查（无命中/词表外值 → null）。 */
  investigationPubId: string | null;
};

export type PostAnalysisTaskCreateInput = {
  targetBrand: string;
  targetBrandAliases: string[];
  urls: string[];
  verifyFacts: boolean;
  annotate: boolean;
  /** 命中（GEO帖/拉踩）后自动建立情报调查；缺省按服务端默认 true。 */
  openInvestigation?: boolean;
};

export type PostAnalysisTaskReceipt = {
  pubId: string;
};

export type PostAnalysisItemRow = {
  pubId: string;
  ordinal: number;
  url: string;
  host: string;
  status: PostAnalysisItemStatus;
  annotationStatus: PostAnalysisAnnotationStatus;
  category: PostAnalysisCategory | null;
  categoryLabel: string | null;
  isGeoPost: boolean | null;
  isTargetBrandGeo: boolean | null;
  disparagementCount: number;
  misinformationCount: number;
  error: string | null;
  createdAt: string;
  updatedAt: string;
};

export type PostAnalysisItemPage = {
  data: PostAnalysisItemRow[];
  nextCursor: string | null;
  hasMore: boolean;
};

export type PostAnalysisGeoSignal = {
  signal: string;
  quote: string;
};

export type PostAnalysisBrandMention = {
  brand: string;
  isTargetBrand: boolean;
  sentiment: 'positive' | 'neutral' | 'negative';
  quote: string;
};

export type PostAnalysisDisparagement = {
  direction: 'target_disparaged' | 'disparages_other';
  subjectBrand: string;
  objectBrand: string;
  quote: string;
  severity: 'low' | 'medium' | 'high';
  confidence: number | null;
};

export type PostAnalysisClaimSource = {
  title: string;
  url: string;
};

export type PostAnalysisClaimVerification = {
  verdict: 'accurate' | 'inaccurate' | 'unsupported';
  correction: string;
  confidence: number | null;
  sources: PostAnalysisClaimSource[];
};

export type PostAnalysisClaim = {
  claim: string;
  quote: string;
  aboutTargetBrand: boolean;
  verification: PostAnalysisClaimVerification | null;
};

export type PostAnalysisView = {
  summary: string;
  isGeoPost: boolean;
  geoConfidence: number | null;
  geoSignals: PostAnalysisGeoSignal[];
  category: PostAnalysisCategory;
  categoryLabel: string;
  categoryRationale: string;
  brandMentions: PostAnalysisBrandMention[];
  isTargetBrandGeo: boolean;
  disparagement: PostAnalysisDisparagement[];
  claims: PostAnalysisClaim[];
};

export type PostAnalysisAnnotation = {
  type: 'target_brand' | 'disparagement' | 'misinformation';
  quote: string;
  note: string;
  matched: boolean;
};

export type PostAnalysisValidationSummary = {
  droppedTotal: number;
  claimsVerified: number;
  verificationErrors: number;
};

export type PostAnalysisAssetIntegrity = {
  sha256: string;
  byteSize: number;
  mimeType: string;
};

export type PostAnalysisItemDetail = {
  pubId: string;
  ordinal: number;
  url: string;
  host: string;
  status: PostAnalysisItemStatus;
  annotationStatus: PostAnalysisAnnotationStatus;
  finalUrl: string | null;
  httpStatus: number | null;
  extractor: string | null;
  textSha256: string | null;
  error: string | null;
  analysis: PostAnalysisView | null;
  analysisValidation: PostAnalysisValidationSummary | null;
  annotations: PostAnalysisAnnotation[];
  screenshotAsset: PostAnalysisAssetIntegrity | null;
  annotatedAsset: PostAnalysisAssetIntegrity | null;
  createdAt: string;
  updatedAt: string;
};

export type VerifiedPostAnalysisAsset = PostAnalysisAssetIntegrity & { blob: Blob };

const postAnalysisTaskPubIdPattern = /^pat_[A-Za-z0-9_-]{1,124}$/u;
const postAnalysisItemPubIdPattern = /^pai_[A-Za-z0-9_-]{1,124}$/u;

/** 情报调查 pub_id 投影：inv_ 词表外的值一律 fail-closed 降级 null（不阻断任务详情）。 */
const projectPostAnalysisInvestigationPubId = (value: unknown): string | null =>
  value === null || value === undefined ? null : projectAnalyticsPubId(value, 'inv_');

const postAnalysisConfidence = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;

const postAnalysisBoundedCount = (value: unknown, maximum: number): number | null => {
  const projected = safeCount(value);
  return projected !== null && projected <= maximum ? projected : null;
};

const projectPostAnalysisTaskView = (value: unknown): PostAnalysisTaskSummary | null => {
  if (!isBrowserRecord(value)) return null;
  const pubId = projectAnalyticsPubId(value.pub_id, 'pat_');
  const targetBrand = safePostingText(value.target_brand, 200, false);
  const status = safeBrowserEnum(value.status, postAnalysisTaskStatuses);
  const urlCount =
    typeof value.url_count === 'number' &&
    Number.isSafeInteger(value.url_count) &&
    value.url_count >= 1 &&
    value.url_count <= 50
      ? value.url_count
      : null;
  const createdAt = safeTimestamp(value.created_at);
  const updatedAt = safeTimestamp(value.updated_at);
  if (!pubId || !targetBrand || !status || urlCount === null || !createdAt || !updatedAt) {
    return null;
  }
  const targetBrandAliases = (
    Array.isArray(value.target_brand_aliases) ? value.target_brand_aliases : []
  )
    .flatMap((alias) => {
      const projected = safePostingText(alias, 200, false);
      return projected ? [projected] : [];
    })
    .slice(0, 20);
  let error: string | null = null;
  if (value.error !== null && value.error !== undefined) {
    const projectedError = safePostingText(value.error, 2_000, false);
    if (!projectedError) return null;
    error = projectedError;
  }
  return { pubId, targetBrand, targetBrandAliases, status, urlCount, error, createdAt, updatedAt };
};

type PostAnalysisItemCore = {
  pubId: string;
  ordinal: number;
  url: string;
  host: string;
  status: PostAnalysisItemStatus;
  annotationStatus: PostAnalysisAnnotationStatus;
  error: string | null;
  createdAt: string;
  updatedAt: string;
};

/** 列表行与详情共用的标量字段（徽章派生字段只在列表行投影，详情没有这些列）。 */
const projectPostAnalysisItemCore = (
  value: Record<string, unknown>,
): PostAnalysisItemCore | null => {
  const pubId = projectAnalyticsPubId(value.pub_id, 'pai_');
  const ordinal = postAnalysisBoundedCount(value.ordinal, 500);
  const url = safePostingUrl(value.url);
  const host = safePostingText(value.host, 200, false);
  const status = safeBrowserEnum(value.status, postAnalysisItemStatuses);
  const annotationStatus = safeBrowserEnum(value.annotation_status, postAnalysisAnnotationStatuses);
  const createdAt = safeTimestamp(value.created_at);
  const updatedAt = safeTimestamp(value.updated_at);
  if (
    !pubId ||
    ordinal === null ||
    ordinal < 1 ||
    !url ||
    !host ||
    !status ||
    !annotationStatus ||
    !createdAt ||
    !updatedAt
  ) {
    return null;
  }
  let error: string | null = null;
  if (value.error !== null && value.error !== undefined) {
    const projectedError = safePostingText(value.error, 2_000, false);
    if (!projectedError) return null;
    error = projectedError;
  }
  return { pubId, ordinal, url, host, status, annotationStatus, error, createdAt, updatedAt };
};

const projectPostAnalysisItemRow = (value: unknown): PostAnalysisItemRow | null => {
  if (!isBrowserRecord(value)) return null;
  const core = projectPostAnalysisItemCore(value);
  if (!core) return null;
  let category: PostAnalysisCategory | null = null;
  if (value.category !== null && value.category !== undefined) {
    const projectedCategory = safeBrowserEnum(value.category, postAnalysisCategories);
    if (!projectedCategory) return null;
    category = projectedCategory;
  }
  let categoryLabel: string | null = null;
  if (value.category_label !== null && value.category_label !== undefined) {
    const projectedLabel = safePostingText(value.category_label, 50, false);
    if (!projectedLabel) return null;
    categoryLabel = projectedLabel;
  }
  const isGeoPost =
    value.is_geo_post === null || value.is_geo_post === undefined
      ? null
      : typeof value.is_geo_post === 'boolean'
        ? value.is_geo_post
        : undefined;
  const isTargetBrandGeo =
    value.is_target_brand_geo === null || value.is_target_brand_geo === undefined
      ? null
      : typeof value.is_target_brand_geo === 'boolean'
        ? value.is_target_brand_geo
        : undefined;
  const disparagementCount = postAnalysisBoundedCount(value.disparagement_count, 10_000);
  const misinformationCount = postAnalysisBoundedCount(value.misinformation_count, 10_000);
  if (
    isGeoPost === undefined ||
    isTargetBrandGeo === undefined ||
    disparagementCount === null ||
    misinformationCount === null
  ) {
    return null;
  }
  return {
    ...core,
    category,
    categoryLabel,
    isGeoPost,
    isTargetBrandGeo,
    disparagementCount,
    misinformationCount,
  };
};

const projectPostAnalysisView = (value: unknown): PostAnalysisView | null => {
  if (!isBrowserRecord(value)) return null;
  const summary = safePostingText(value.summary, 1_000, false);
  const category = safeBrowserEnum(value.category, postAnalysisCategories);
  if (
    !summary ||
    !category ||
    typeof value.is_geo_post !== 'boolean' ||
    typeof value.is_target_brand_geo !== 'boolean'
  ) {
    return null;
  }
  const geoSignals = (Array.isArray(value.geo_signals) ? value.geo_signals : [])
    .slice(0, 50)
    .flatMap((entry) => {
      if (!isBrowserRecord(entry)) return [];
      const signal = safePostingText(entry.signal, 500, false);
      const quote = safePostingText(entry.quote, 500, false);
      return signal && quote ? [{ signal, quote }] : [];
    });
  const brandMentions = (Array.isArray(value.brand_mentions) ? value.brand_mentions : [])
    .slice(0, 100)
    .flatMap((entry) => {
      if (!isBrowserRecord(entry) || typeof entry.is_target_brand !== 'boolean') return [];
      const brand = safePostingText(entry.brand, 200, false);
      const sentiment = safeBrowserEnum(entry.sentiment, postAnalysisSentiments);
      const quote = safePostingText(entry.quote, 500, false);
      return brand && sentiment && quote
        ? [{ brand, isTargetBrand: entry.is_target_brand, sentiment, quote }]
        : [];
    });
  const disparagement = (Array.isArray(value.disparagement) ? value.disparagement : [])
    .slice(0, 50)
    .flatMap((entry) => {
      if (!isBrowserRecord(entry)) return [];
      const direction = safeBrowserEnum(entry.direction, postAnalysisDirections);
      const subjectBrand = safePostingText(entry.subject_brand, 200, false);
      const objectBrand = safePostingText(entry.object_brand, 200, false);
      const quote = safePostingText(entry.quote, 500, false);
      const severity = safeBrowserEnum(entry.severity, postAnalysisSeverities);
      return direction && subjectBrand && objectBrand && quote && severity
        ? [
            {
              direction,
              subjectBrand,
              objectBrand,
              quote,
              severity,
              confidence: postAnalysisConfidence(entry.confidence),
            },
          ]
        : [];
    });
  const claims = (Array.isArray(value.claims) ? value.claims : [])
    .slice(0, 10)
    .flatMap((entry): PostAnalysisClaim[] => {
      if (!isBrowserRecord(entry) || typeof entry.about_target_brand !== 'boolean') return [];
      const claim = safePostingText(entry.claim, 500, false);
      const quote = safePostingText(entry.quote, 500, false);
      if (!claim || !quote) return [];
      let verification: PostAnalysisClaimVerification | null = null;
      if (entry.verification !== null && entry.verification !== undefined) {
        if (!isBrowserRecord(entry.verification)) return [];
        const verdict = safeBrowserEnum(entry.verification.verdict, postAnalysisVerdicts);
        if (!verdict) return [];
        const sources = (
          Array.isArray(entry.verification.sources) ? entry.verification.sources : []
        )
          .slice(0, 10)
          .flatMap((source) => {
            if (!isBrowserRecord(source)) return [];
            const url = safePostingUrl(source.url);
            if (!url) return [];
            const title = safePostingText(source.title, 200, true) ?? '';
            return [{ title: title || url, url }];
          });
        verification = {
          verdict,
          correction: safePostingText(entry.verification.correction, 1_000, true) ?? '',
          confidence: postAnalysisConfidence(entry.verification.confidence),
          sources,
        };
      }
      return [{ claim, quote, aboutTargetBrand: entry.about_target_brand, verification }];
    });
  return {
    summary,
    isGeoPost: value.is_geo_post,
    geoConfidence: postAnalysisConfidence(value.geo_confidence),
    geoSignals,
    category,
    categoryLabel: safePostingText(value.category_label, 50, false) ?? category,
    categoryRationale: safePostingText(value.category_rationale, 500, true) ?? '',
    brandMentions,
    isTargetBrandGeo: value.is_target_brand_geo,
    disparagement,
    claims,
  };
};

const projectPostAnalysisAnnotations = (value: unknown): PostAnalysisAnnotation[] => {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 500).flatMap((entry) => {
    if (!isBrowserRecord(entry)) return [];
    const type = safeBrowserEnum(entry.type, postAnalysisAnnotationTypes);
    const quote = safePostingText(entry.quote, 500, false);
    if (!type || !quote) return [];
    const note = safePostingText(entry.note, 1_100, true) ?? '';
    return [{ type, quote, note, matched: entry.matched === true }];
  });
};

const projectPostAnalysisValidation = (value: unknown): PostAnalysisValidationSummary | null => {
  if (value === null || value === undefined) return null;
  if (!isBrowserRecord(value)) return null;
  const dropped = isBrowserRecord(value.dropped) ? value.dropped : {};
  const droppedTotal = Object.values(dropped).reduce<number>(
    (total, count) => total + (safeCount(count) ?? 0),
    0,
  );
  return {
    droppedTotal,
    claimsVerified: safeCount(value.claims_verified) ?? 0,
    verificationErrors: safeCount(value.verification_errors) ?? 0,
  };
};

const projectPostAnalysisAssetIntegrity = (value: unknown): PostAnalysisAssetIntegrity | null => {
  if (value === null || value === undefined || !isBrowserRecord(value)) return null;
  const sha256 = safeHash(value.sha256);
  const byteSize =
    typeof value.byte_size === 'number' &&
    Number.isSafeInteger(value.byte_size) &&
    value.byte_size > 0 &&
    value.byte_size <= postAnalysisAssetMaxBytes
      ? value.byte_size
      : null;
  if (!sha256 || byteSize === null || value.mime_type !== 'image/png') return null;
  return { sha256, byteSize, mimeType: 'image/png' };
};

const projectPostAnalysisItemDetail = (value: unknown): PostAnalysisItemDetail | null => {
  if (!isBrowserRecord(value)) return null;
  const core = projectPostAnalysisItemCore(value);
  if (!core) return null;
  let finalUrl: string | null = null;
  if (value.final_url !== null && value.final_url !== undefined) {
    const projectedUrl = safePostingUrl(value.final_url);
    if (!projectedUrl) return null;
    finalUrl = projectedUrl;
  }
  let httpStatus: number | null = null;
  if (value.http_status !== null && value.http_status !== undefined) {
    const projectedStatus = safeCount(value.http_status);
    if (projectedStatus === null || projectedStatus < 100 || projectedStatus > 599) return null;
    httpStatus = projectedStatus;
  }
  let extractor: string | null = null;
  if (value.extractor !== null && value.extractor !== undefined) {
    const projectedExtractor = safePostingText(value.extractor, 100, false);
    if (!projectedExtractor) return null;
    extractor = projectedExtractor;
  }
  let textSha256: string | null = null;
  if (value.text_sha256 !== null && value.text_sha256 !== undefined) {
    const projectedHash = safeHash(value.text_sha256);
    if (!projectedHash) return null;
    textSha256 = projectedHash;
  }
  return {
    ...core,
    finalUrl,
    httpStatus,
    extractor,
    textSha256,
    analysis: projectPostAnalysisView(value.analysis),
    analysisValidation: projectPostAnalysisValidation(value.analysis_validation),
    annotations: projectPostAnalysisAnnotations(value.annotations),
    screenshotAsset: projectPostAnalysisAssetIntegrity(value.screenshot_asset),
    annotatedAsset: projectPostAnalysisAssetIntegrity(value.annotated_asset),
  };
};

export async function listPostAnalysisTasks(
  headers: IdentitySessionHeaders,
  cursor: string | null = null,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<PostAnalysisTaskPage>> {
  try {
    const requestedCursor = cursor && postAnalysisTaskPubIdPattern.test(cursor) ? cursor : null;
    const result = await projectedApiClient(client).GET('/api/v2/post-analysis/tasks', {
      params: {
        query: { limit: 100, ...(requestedCursor ? { cursor: requestedCursor } : {}) },
        header: headers,
      },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const tasks = result.data.data.flatMap((value) => {
      const task = projectPostAnalysisTaskView(value);
      return task ? [task] : [];
    });
    const rawNextCursor = result.data.page.next_cursor;
    const rawHasMore = result.data.page.has_more;
    const hasMore = rawHasMore === true;
    const nextCursor =
      typeof rawNextCursor === 'string' && postAnalysisTaskPubIdPattern.test(rawNextCursor)
        ? rawNextCursor
        : null;
    const pageIsValid =
      tasks.length === result.data.data.length &&
      typeof rawHasMore === 'boolean' &&
      ((hasMore && nextCursor !== null && nextCursor === tasks.at(-1)?.pubId) ||
        (!hasMore && rawNextCursor === null));
    return pageIsValid
      ? { kind: 'ready', data: { data: tasks, nextCursor: hasMore ? nextCursor : null, hasMore } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function createPostAnalysisTask(
  headers: IdentitySessionHeaders,
  body: PostAnalysisTaskCreateInput,
  idempotencyKey: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<PostAnalysisTaskReceipt>> {
  try {
    const result = await projectedApiClient(client).POST('/api/v2/post-analysis/tasks', {
      params: { header: sopWriteHeaders(headers, idempotencyKey) },
      body: {
        target_brand: body.targetBrand,
        target_brand_aliases: body.targetBrandAliases,
        urls: body.urls,
        options: {
          verify_facts: body.verifyFacts,
          annotate: body.annotate,
          // 生成的 TaskOptions 三键均为必填（exactOptionalPropertyTypes 下无法条件展开）；
          // 缺省显式回落服务端默认值 true。
          open_investigation: body.openInvestigation ?? true,
        },
      },
    });
    if (!result.data) return classifyResourceFailure(result.response.status);
    const task = projectPostAnalysisTaskView(result.data);
    return task ? { kind: 'ready', data: { pubId: task.pubId } } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getPostAnalysisTask(
  headers: IdentitySessionHeaders,
  taskPubId: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<PostAnalysisTaskDetail>> {
  try {
    if (!projectAnalyticsPubId(taskPubId, 'pat_')) return { kind: 'unavailable' };
    const result = await projectedApiClient(client).GET(
      '/api/v2/post-analysis/tasks/{task_pub_id}',
      {
        params: { path: { task_pub_id: taskPubId }, header: headers },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const task = projectPostAnalysisTaskView(result.data);
    if (!task) return { kind: 'unavailable' };
    const statusCounts = Object.entries(result.data.status_counts).flatMap(([status, count]) => {
      const projectedStatus = safeBrowserEnum(status, postAnalysisItemStatuses);
      const projectedCount = postAnalysisBoundedCount(count, 10_000);
      return projectedStatus && projectedCount !== null
        ? [{ status: projectedStatus, count: projectedCount }]
        : [];
    });
    return {
      kind: 'ready',
      data: {
        task,
        statusCounts,
        investigationPubId: projectPostAnalysisInvestigationPubId(result.data.investigation_pub_id),
      },
    };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function listPostAnalysisItems(
  headers: IdentitySessionHeaders,
  taskPubId: string,
  cursor: string | null = null,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<PostAnalysisItemPage>> {
  try {
    if (!projectAnalyticsPubId(taskPubId, 'pat_')) return { kind: 'unavailable' };
    const requestedCursor = cursor && postAnalysisItemPubIdPattern.test(cursor) ? cursor : null;
    const result = await projectedApiClient(client).GET(
      '/api/v2/post-analysis/tasks/{task_pub_id}/items',
      {
        params: {
          path: { task_pub_id: taskPubId },
          query: { limit: 100, ...(requestedCursor ? { cursor: requestedCursor } : {}) },
          header: headers,
        },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const items = result.data.data.flatMap((value) => {
      const item = projectPostAnalysisItemRow(value);
      return item ? [item] : [];
    });
    const rawNextCursor = result.data.page.next_cursor;
    const rawHasMore = result.data.page.has_more;
    const hasMore = rawHasMore === true;
    const nextCursor =
      typeof rawNextCursor === 'string' && postAnalysisItemPubIdPattern.test(rawNextCursor)
        ? rawNextCursor
        : null;
    const pageIsValid =
      items.length === result.data.data.length &&
      typeof rawHasMore === 'boolean' &&
      ((hasMore && nextCursor !== null && nextCursor === items.at(-1)?.pubId) ||
        (!hasMore && rawNextCursor === null));
    return pageIsValid
      ? { kind: 'ready', data: { data: items, nextCursor: hasMore ? nextCursor : null, hasMore } }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getPostAnalysisItem(
  headers: IdentitySessionHeaders,
  itemPubId: string,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<PostAnalysisItemDetail>> {
  try {
    if (!projectAnalyticsPubId(itemPubId, 'pai_')) return { kind: 'unavailable' };
    const result = await projectedApiClient(client).GET(
      '/api/v2/post-analysis/items/{item_pub_id}',
      {
        params: { path: { item_pub_id: itemPubId }, header: headers },
      },
    );
    if (!result.data) return classifyResourceFailure(result.response.status);
    const detail = projectPostAnalysisItemDetail(result.data);
    return detail ? { kind: 'ready', data: detail } : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}

/**
 * 截图/标注图字节流（verified-Blob 边界，getEvidenceAssetContent 同款）：
 * MIME/尺寸先验相等，SHA-256 摘要比对通过才交给调用方。
 */
export async function getPostAnalysisItemAsset(
  headers: IdentitySessionHeaders,
  itemPubId: string,
  kind: PostAnalysisAssetKind,
  expected: PostAnalysisAssetIntegrity,
  client: ProjectedApiClientOverride = apiClient,
): Promise<ProjectResourceResult<VerifiedPostAnalysisAsset>> {
  try {
    const expectedSha256 = safeHash(expected.sha256);
    if (
      !projectAnalyticsPubId(itemPubId, 'pai_') ||
      !safeBrowserEnum(kind, postAnalysisAssetKinds) ||
      !expectedSha256 ||
      expected.mimeType !== 'image/png' ||
      !Number.isSafeInteger(expected.byteSize) ||
      expected.byteSize <= 0 ||
      expected.byteSize > postAnalysisAssetMaxBytes
    ) {
      return { kind: 'unavailable' };
    }
    const result = await projectedApiClient(client).GET(
      '/api/v2/post-analysis/items/{item_pub_id}/assets/{kind}',
      {
        params: {
          path: { item_pub_id: itemPubId, kind },
          header: headers,
        },
        parseAs: 'blob',
      },
    );
    if (
      !(result.data instanceof Blob) ||
      result.data.type !== expected.mimeType ||
      result.data.size !== expected.byteSize
    ) {
      return result.data
        ? { kind: 'unavailable' }
        : classifyResourceFailure(result.response.status);
    }
    const digest = await globalThis.crypto.subtle.digest(
      'SHA-256',
      await result.data.arrayBuffer(),
    );
    const actualSha256 = [...new Uint8Array(digest)]
      .map((value) => value.toString(16).padStart(2, '0'))
      .join('');
    return actualSha256 === expectedSha256
      ? {
          kind: 'ready',
          data: {
            blob: result.data,
            byteSize: result.data.size,
            mimeType: result.data.type,
            sha256: actualSha256,
          },
        }
      : { kind: 'unavailable' };
  } catch {
    return { kind: 'unavailable' };
  }
}
