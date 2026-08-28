import type { components } from './schema.generated';

type FormalProductionCreate = components['schemas']['FormalProductionCreate'];
type Assert<T extends true> = T;
type IsAssignable<Value, Target> = Value extends Target ? true : false;

type CommonCreateFields = {
  project_pub_id: 'prj_typecheck';
  window_start: '2026-08-01';
  window_end: '2026-08-20';
  document_status: 'internal_review';
  candidate_group_strategy: 'preregistered_scope_v1';
  version: 'V1.0';
  prepared_by: '项目组';
  prepared_date: '2026-08-20';
};

type LegacyWithoutCatalog = CommonCreateFields & { services: [1, 4] };
type QuotationServiceFive = CommonCreateFields & {
  services: [5];
  service_catalog_version: 'quotation_services_v2';
  metric_snapshot_set_pub_id: 'mss_typecheck';
  metric_snapshot_set_hash: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
  metric_snapshot_filters: {
    model: [];
    region: [];
    mode: [];
  };
};
type InvalidLegacyServiceFive = CommonCreateFields & { services: [5] };
type InvalidExplicitLegacyServiceFive = CommonCreateFields & {
  services: [5];
  service_catalog_version: 'legacy_report_services_v1';
};

type _LegacyCompatibility = Assert<IsAssignable<LegacyWithoutCatalog, FormalProductionCreate>>;
type _QuotationServiceFiveAccepted = Assert<
  IsAssignable<QuotationServiceFive, FormalProductionCreate>
>;
type _ImplicitLegacyServiceFiveRejected = Assert<
  IsAssignable<InvalidLegacyServiceFive, FormalProductionCreate> extends false ? true : false
>;
type _ExplicitLegacyServiceFiveRejected = Assert<
  IsAssignable<InvalidExplicitLegacyServiceFive, FormalProductionCreate> extends false
    ? true
    : false
>;

export type FormalProductionCreateContractChecks =
  | _LegacyCompatibility
  | _QuotationServiceFiveAccepted
  | _ImplicitLegacyServiceFiveRejected
  | _ExplicitLegacyServiceFiveRejected;
