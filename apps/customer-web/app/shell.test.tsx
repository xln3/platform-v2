// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router';
import type {
  AnalyticsBreakdownResponse,
  AnalyticsOverviewSafeResponse,
  CustomerAccountView,
  CustomerEventView,
  CustomerPairingView,
  ReportDetailProjection,
  ReportDeliverySafeView,
} from '@geo/api-client';
import Shell, {
  INTAKE_TRUTH_CONFIRM_ITEMS,
  analyticsMetricDataState,
  analyticsRateChartState,
  customerAccountLifecycleProjectionLimits,
  customerEvidenceProjectionLimits,
  customerGovernanceHistoryLimit,
  groupLiveEvidenceByPurpose,
  customerMonitoringProjectionLimits,
  intakeProfileSchema,
  projectAnalyticsBreakdown,
  projectAnalyticsBreakdownResult,
  projectAnalyticsCompetitors,
  projectAnalyticsOverview,
  projectAnalyticsOverviewResult,
  mergeAnswerRelationProjection,
  projectAnswerRelations,
  projectAssetConfirmationPage,
  projectAssetConfirmationViews,
  projectClientProfilePage,
  projectClientProfileViews,
  projectCustomerAnswerPage,
  projectCustomerAccount,
  projectCustomerAccountCollection,
  projectCustomerEventResult,
  projectCustomerPairingResult,
  projectCustomerPairingStage,
  projectCustomerRevocationReceipt,
  projectEvidenceAssetPage,
  projectEvidenceAssets,
  projectCustomerReportVersions,
  projectReportDeliveryViews,
  projectResponsibleMemberViews,
  projectResponsibleMemberResult,
  safeOfficialShareUrl,
} from './shell';

describe('Customer platform account lifecycle', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    history.replaceState(null, '', '/platform/customer/');
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              status: 'ok',
              service: 'geo-platform-v2',
              version: 'contract-v1',
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('drops unknown analytics secrets before values can enter React state', () => {
    const projected = projectAnalyticsOverview([
      {
        metric: 'mention_rate',
        value: 0.75,
        numerator: 3,
        denominator: 4,
        state: 'ready',
        metric_version: 'metric-v1',
        scorer_version: 'scorer-v1',
        filter_hash: 'safe',
        trace_tokens: ['Bearer trace-token-canary'],
        cookie: 'SESSION=analytics-state-canary',
        profile_path: '/secret/profile/analytics-state-canary',
        otp: 123456,
      },
    ] as never);

    expect(projected).toEqual([
      {
        metric: 'mention_rate',
        value: 0.75,
        numerator: 3,
        denominator: 4,
        state: 'ready',
        metric_version: 'metric-v1',
        scorer_version: 'scorer-v1',
        filter_hash: 'safe',
      },
    ]);
    expect(projected[0]).not.toHaveProperty('trace_tokens');
    expect(JSON.stringify(projected)).not.toMatch(/Cookie|Bearer|123456|profile/i);
  });

  it('fails closed on unknown metric states and distinguishes completed zero from no sample', () => {
    const projection = projectAnalyticsOverviewResult([
      {
        metric: 'mention_rate',
        value: 0,
        numerator: 0,
        denominator: 4,
        state: 'ready',
        metric_version: 'metric-v1',
        scorer_version: 'scorer-v1',
        filter_hash: 'safe',
      },
      {
        metric: 'citation_coverage',
        value: 0,
        numerator: -1,
        denominator: -8,
        state: 'Cookie=metric-state-canary',
        metric_version: 'metric-v1',
        scorer_version: 'scorer-v1',
        filter_hash: 'safe',
      },
    ]);
    const [zero] = projection.data;

    expect(zero?.state).toBe('ready');
    expect(analyticsMetricDataState(zero)).toBe('real-zero');
    expect(projection.invalid).toBe(true);
    expect(projection.data).toHaveLength(1);
    expect(analyticsMetricDataState(undefined)).toBe('insufficient');
    expect(analyticsRateChartState(0, 4)).toBe('real-zero');
    expect(analyticsRateChartState(0, 0)).toBe('insufficient');
    expect(analyticsRateChartState(null, 4)).toBe('insufficient');
    expect(JSON.stringify(projection)).not.toContain('metric-state-canary');
  });

  it('projects every signed-terminal outcome without inventing a live completion', () => {
    expect(projectCustomerPairingStage('completed')).toBe('completed');
    expect(projectCustomerPairingStage('rejected')).toBe('refused');
    expect(projectCustomerPairingStage('expired')).toBe('timed_out');
    expect(projectCustomerPairingStage('awaiting_platform_probe')).toBe('waiting');
    expect(projectCustomerPairingStage('failed')).toBe('failed');
    expect(projectCustomerPairingStage('unknown_future_state')).toBe('failed');
    expect(projectCustomerPairingStage(undefined)).toBe('failed');
  });

  it('shows a revocation receipt only after a safe backend deletion verification', () => {
    const base: CustomerAccountView = {
      pub_id: 'pac_customer_safe',
      account_mask: '尾号 · 7391',
      platform_label: '豆包',
      owner_label: '当前客户',
      custody_mode: 'customer_device',
      admission_level: 'suspended',
      scopes: [],
      authorization_expires_at: null,
      region_label: '中国大陆',
      session_health: 'revoked',
      last_verified_at: null,
      intervention_status: 'none',
      revocation_receipt_pub_id: 'rev_customer_safe',
      revoked_at: '2026-07-25T06:00:00Z',
    };
    expect(projectCustomerRevocationReceipt(base)).toEqual({
      receiptId: 'rev_customer_safe',
      revokedAtLabel: '2026-07-25 06:00',
      actorLabel: '未在客户安全投影中公开',
      leasesStopped: true,
      sessionsClosed: true,
      secretCopiesPurged: true,
    });
    expect(projectCustomerRevocationReceipt({ ...base, revoked_at: null })).toBeNull();
    expect(projectCustomerRevocationReceipt({ ...base, revoked_at: '1' })).toBeNull();
    expect(
      projectCustomerAccount({
        ...base,
        admission_level: 'read_verified',
        session_health: 'healthy',
        authorization_expires_at: '2026-09-30T15:59:59+08:00',
        last_verified_at: '2026-07-25T06:00:00Z',
        revocation_receipt_pub_id: null,
        revoked_at: null,
      }),
    ).toMatchObject({
      expiresLabel: '2026-09-30',
      lastVerifiedLabel: '2026-07-25 06:00',
    });
    expect(
      projectCustomerAccount({
        ...base,
        authorization_expires_at: '1',
        last_verified_at: '2026-07-25T06:00:00Z',
      }),
    ).toBeNull();
    expect(
      projectCustomerRevocationReceipt({
        ...base,
        revocation_receipt_pub_id: 'Cookie=revocation-canary',
      }),
    ).toBeNull();
  });

  it('projects report deliveries without retaining recipient, comments, or hostile extensions', () => {
    const safeDelivery: ReportDeliverySafeView = {
      pub_id: 'dlv_customer_safe',
      report_pub_id: 'rpt_customer_safe',
      recipient_pub_id: 'usr_customer_safe',
      delivered_at: '2026-07-25T07:00:00Z',
      confirmed_at: null,
      confirmation_comment: null,
    };
    const projected = projectReportDeliveryViews(
      [
        {
          ...safeDelivery,
          confirmation_comment: 'Bearer delivery-comment-canary',
          cookie: 'SESSION=delivery-extension-canary',
          otp: 318294,
        },
        {
          ...safeDelivery,
          pub_id: 'dlv_hostile',
          recipient_pub_id: 'Bearer recipient-canary',
        },
        {
          ...safeDelivery,
          pub_id: 'dlv_wrong_report',
          report_pub_id: 'rpt_other',
        },
        {
          ...safeDelivery,
          pub_id: 'dlv_ambiguous_time',
          delivered_at: '1',
        },
      ],
      'rpt_customer_safe',
      'usr_customer_safe',
    );

    expect(projected).toEqual({
      data: [
        {
          id: 'dlv_customer_safe',
          reportId: 'rpt_customer_safe',
          deliveredAt: '2026-07-25T07:00:00Z',
          confirmedAt: null,
        },
      ],
      total: 4,
      shown: 1,
      invalid: true,
    });
    expect(JSON.stringify(projected)).not.toMatch(/recipient|comment|Cookie|Bearer|318294/i);
  });

  it('binds ordered published report versions and their artifacts before enabling customer actions', () => {
    const version = (
      versionNumber: number,
      overrides: Record<string, unknown> = {},
    ): ReportDetailProjection['versions'][number] =>
      ({
        pub_id: `rptv_customer_safe_${versionNumber}`,
        version_number: versionNumber,
        window_start: '2026-07-01T00:00:00Z',
        window_end: '2026-07-21T23:59:59Z',
        filters: {},
        metric_version: 'metric-v1',
        scorer_version: 'scorer-v1',
        fact_snapshot_hash: 'a'.repeat(64),
        status: 'published',
        components: [],
        frozen_facts: [],
        artifacts: [],
        evidence_bindings: [],
        reviews: [],
        comments: [],
        events: [],
        ...overrides,
      }) as never;
    const safeArtifact = {
      pub_id: 'rpta_customer_safe_pdf',
      report_version_pub_id: 'rptv_customer_safe_2',
      format: 'pdf',
      evidence_pub_id: 'evd_customer_safe_pdf',
      mime_type: 'application/pdf',
      byte_size: 48,
      sha256: 'b'.repeat(64),
      created_at: '2026-07-25T01:00:00Z',
    };
    const safe = projectCustomerReportVersions([
      version(1),
      version(2, { artifacts: [safeArtifact] }),
    ]);
    expect(safe).toMatchObject({
      versions: [
        { id: 'rptv_customer_safe_2', versionNumber: 2, status: 'published' },
        { id: 'rptv_customer_safe_1', versionNumber: 1, status: 'published' },
      ],
      currentVersionId: 'rptv_customer_safe_2',
      artifacts: [
        {
          format: 'pdf',
          byteSize: 48,
          mimeType: 'application/pdf',
          sha256: 'b'.repeat(64),
        },
      ],
      versionTotal: 2,
      versionShown: 2,
      artifactTotal: 1,
      artifactShown: 1,
      invalidVersions: false,
      invalidArtifacts: false,
    });

    const oversized = projectCustomerReportVersions(
      Array.from({ length: 101 }, (_, index) => version(index + 1)),
    );
    expect(oversized).toMatchObject({
      currentVersionId: 'rptv_customer_safe_101',
      versionTotal: 101,
      versionShown: 100,
      invalidVersions: false,
    });
    const unsafeOmittedVersion = projectCustomerReportVersions(
      Array.from({ length: 101 }, (_, index) =>
        index === 0
          ? version(500, {
              pub_id: 'rptv_customer_omitted_hostile',
              title: 'Bearer omitted-version-canary',
            } as never)
          : version(index + 1),
      ),
    );
    expect(unsafeOmittedVersion).toMatchObject({
      currentVersionId: '',
      artifacts: [],
      invalidVersions: true,
    });
    expect(JSON.stringify(unsafeOmittedVersion)).not.toMatch(/omitted-version-canary|Bearer/i);

    const boundaryTruncated = projectCustomerReportVersions(
      Array.from({ length: 100 }, (_, index) => version(index + 2)),
      {
        versions: { total: 101, shown: 100, invalid: false },
        version_collections: {},
      } as never,
    );
    expect(boundaryTruncated).toMatchObject({
      currentVersionId: '',
      artifacts: [],
      versionTotal: 101,
      versionShown: 100,
      invalidVersions: true,
    });

    const unsafeOrder = projectCustomerReportVersions([
      version(2),
      version(1, { pub_id: 'rptv_customer_safe_duplicate_order' }),
    ]);
    expect(unsafeOrder).toMatchObject({
      currentVersionId: '',
      artifacts: [],
      invalidVersions: true,
    });
    const unsafeArtifact = projectCustomerReportVersions([
      version(2, {
        artifacts: [
          {
            ...safeArtifact,
            report_version_pub_id: 'rptv_customer_other',
            token: 'Bearer customer-artifact-binding-canary',
          },
        ],
      }),
    ]);
    expect(unsafeArtifact).toMatchObject({
      currentVersionId: '',
      artifacts: [],
      invalidVersions: false,
      invalidArtifacts: true,
    });
    const boundaryTruncatedArtifact = projectCustomerReportVersions(
      [version(2, { artifacts: [safeArtifact] })],
      {
        versions: { total: 1, shown: 1, invalid: false },
        version_collections: {
          rptv_customer_safe_2: {
            artifacts: { total: 2, shown: 1, invalid: false },
          },
        },
      } as never,
    );
    expect(boundaryTruncatedArtifact).toMatchObject({
      currentVersionId: '',
      artifacts: [],
      artifactTotal: 2,
      artifactShown: 1,
      invalidVersions: false,
      invalidArtifacts: true,
    });
    expect(JSON.stringify(unsafeArtifact)).not.toMatch(/customer-artifact-binding-canary|Bearer/i);
  });

  it('drops hostile customer profile and atomic asset fields before React state', () => {
    const profile = {
      pub_id: 'cpv_safe',
      project_pub_id: 'prj_safe',
      revision: 1,
      company_name: '安全企业',
      contact_role: '品牌负责人',
      audience: '企业采购团队',
      public_statement: '可公开核验的安全声明。',
      created_at: '2026-07-25T00:00:00Z',
    };
    const confirmation = {
      pub_id: 'acv_safe',
      project_pub_id: 'prj_safe',
      revision: 1,
      brand_name: '安全品牌',
      website: 'https://example.test',
      product_name: '安全产品',
      competitor_name: '安全竞品',
      prohibited_claim: '未经验证的第一',
      created_at: '2026-07-25T00:00:00Z',
    };

    expect(
      projectClientProfileViews([
        profile,
        { ...profile, pub_id: 'cpv_hostile', audience: 'Bearer profile-state-canary' },
        { ...profile, pub_id: 'cpv_ambiguous_time', created_at: '1' },
      ]),
    ).toEqual([profile]);
    expect(
      projectAssetConfirmationViews([
        confirmation,
        {
          ...confirmation,
          pub_id: 'acv_hostile',
          website: 'https://user:proxy-password@example.test',
        },
        { ...confirmation, pub_id: 'acv_ambiguous_time', created_at: '1' },
      ]),
    ).toEqual([{ ...confirmation, website: 'https://example.test/' }]);
  });

  it('bounds governance history pages and binds every version to the current project and cursor', () => {
    const profile = {
      pub_id: 'cpv_safe_5',
      project_pub_id: 'prj_safe',
      revision: 5,
      company_name: '安全企业',
      contact_role: '品牌负责人',
      audience: '企业采购团队',
      public_statement: '可公开核验的安全声明。',
      created_at: '2026-07-25T00:00:00Z',
    };
    const confirmation = {
      pub_id: 'acv_safe_5',
      project_pub_id: 'prj_safe',
      revision: 5,
      brand_name: '安全品牌',
      website: 'https://example.test',
      product_name: '安全产品',
      competitor_name: '安全竞品',
      prohibited_claim: '未经验证的第一',
      created_at: '2026-07-25T00:00:00Z',
    };
    const truncatedProfiles = projectClientProfilePage(
      {
        data: [
          profile,
          { ...profile, pub_id: 'cpv_safe_4', revision: 4 },
          { ...profile, pub_id: 'cpv_safe_3', revision: 3 },
        ],
        next_cursor: '4',
      },
      'prj_safe',
    );
    expect(truncatedProfiles).toMatchObject({
      total: 3,
      invalid: false,
      nextCursor: '',
    });
    expect(truncatedProfiles.data).toHaveLength(customerGovernanceHistoryLimit);

    const invalidProfiles = projectClientProfilePage(
      {
        data: [
          profile,
          {
            ...profile,
            pub_id: 'cpv_other_project',
            project_pub_id: 'prj_other',
            revision: 4,
            audience: 'Bearer cross-project-profile-canary',
          },
        ],
        next_cursor: '3',
      },
      'prj_safe',
      6,
    );
    expect(invalidProfiles).toMatchObject({
      total: 2,
      invalid: true,
      nextCursor: '',
    });
    expect(invalidProfiles.data).toEqual([profile]);

    const invalidConfirmations = projectAssetConfirmationPage(
      {
        data: [
          confirmation,
          {
            ...confirmation,
            pub_id: 'acv_duplicate_revision',
            revision: 5,
            prohibited_claim: 'Cookie=asset-history-canary',
          },
        ],
        next_cursor: '5',
      },
      'prj_safe',
      6,
    );
    expect(invalidConfirmations).toMatchObject({
      total: 2,
      invalid: true,
      nextCursor: '',
    });
    expect(invalidConfirmations.data).toEqual([
      { ...confirmation, website: 'https://example.test/' },
    ]);
    expect(JSON.stringify({ invalidProfiles, invalidConfirmations })).not.toMatch(
      /cross-project-profile-canary|asset-history-canary|Bearer|Cookie/i,
    );
  });

  it('drops evidence assets whose capture time is ambiguous or impossible', () => {
    const safeAsset = {
      pub_id: 'evd_safe',
      purpose: 'frozen_fact_or_component',
      kind: 'answer_screenshot',
      access_class: 'customer_private',
      mime_type: 'image/png',
      byte_size: 512,
      sha256: 'a'.repeat(64),
      anchor_count: 1,
      capture_time: '2026-07-25T08:00:00Z',
      created_at: '2026-07-25T08:00:00Z',
    };
    expect(
      projectEvidenceAssets({
        data: [
          safeAsset,
          { ...safeAsset, pub_id: 'evd_ambiguous', capture_time: '1' },
          { ...safeAsset, pub_id: 'evd_impossible', capture_time: '2026-02-30T08:00:00Z' },
        ],
        page: { next_cursor: null, has_more: false },
      } as never),
    ).toEqual([
      {
        id: 'evd_safe',
        kind: 'answer_screenshot',
        mimeType: 'image/png',
        capturedAt: '2026-07-25 08:00',
        integrity: 'aaaaaaaa…',
      },
    ]);
    expect(
      projectEvidenceAssetPage({
        data: [
          safeAsset,
          { ...safeAsset, pub_id: 'evd_ambiguous', capture_time: '1' },
          { ...safeAsset, pub_id: 'evd_over_limit' },
        ],
        page: { next_cursor: null, has_more: false },
      } as never),
    ).toMatchObject({ total: 3, invalid: true, assets: [{ id: 'evd_safe' }] });
  });

  it('bounds answer relations and discloses invalid or truncated evidence facts', () => {
    const citations = Array.from({ length: 201 }, (_, index) => ({
      pub_id: `cit_${index}`,
      ordinal: index + 1,
      canonical_url: `https://source${index}.example/article`,
      host: `source${index}.example`,
      title: `来源 ${index}`,
      cited_text: null,
      own_source: false,
      content_hash: 'c'.repeat(64),
    }));
    const evidence = Array.from({ length: 201 }, (_, index) => ({
      pub_id: `evd_relation_${index}`,
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
              pub_id: `anch_${anchorIndex}`,
              text_start: anchorIndex,
              text_end: anchorIndex + 1,
              bbox: null,
              page_number: null,
              quote_hash: 'd'.repeat(64),
            }))
          : [],
    }));
    const historyRows = Array.from({ length: 201 }, (_, index) => ({
      pub_id: `diff_${index}`,
      before_evidence_pub_id: `evd_before_${index}`,
      after_evidence_pub_id: `evd_after_${index}`,
      similarity: 0.75,
      visual_diff_available: true,
      created_at: '2026-07-25T08:00:00Z',
    }));
    const projected = projectAnswerRelations(
      {
        answer_pub_id: 'ans_relation_safe',
        citations,
        evidence,
        history: historyRows,
      },
      'ans_relation_safe',
    );

    expect(projected?.citations).toHaveLength(customerEvidenceProjectionLimits.citations);
    expect(projected?.evidence).toHaveLength(customerEvidenceProjectionLimits.evidence);
    expect(projected?.evidence[0]?.anchorCount).toBe(customerEvidenceProjectionLimits.anchors);
    expect(projected?.history).toHaveLength(customerEvidenceProjectionLimits.history);
    expect(projected?.history[0]?.id).toBe('diff_1');
    expect(projected?.projectionNotices.map((item) => item.key)).toEqual([
      'customer-answer-citations',
      'customer-answer-evidence',
      'customer-answer-anchors',
      'customer-answer-history',
    ]);
    expect(projected?.invalidProjection).toEqual([]);

    const clientBounded = projectAnswerRelations(
      {
        answer_pub_id: 'ans_relation_safe',
        citations: citations.slice(0, 199),
        evidence: evidence
          .slice(0, 200)
          .map((item, index) =>
            index === 0 ? { ...item, anchors: item.anchors.slice(0, 200) } : item,
          ),
        history: historyRows.slice(-199),
      },
      'ans_relation_safe',
    );
    expect(clientBounded).not.toBeNull();
    const merged = mergeAnswerRelationProjection(clientBounded!, {
      citations: { total: 201, shown: 199, invalid: true },
      evidence: { total: 201, shown: 200, invalid: false },
      anchors: { total: 201, shown: 200, invalid: false },
      history: { total: 201, shown: 199, invalid: true },
    });
    expect(merged.projectionNotices.map((item) => item.key)).toEqual([
      'customer-answer-citations',
      'customer-answer-evidence',
      'customer-answer-anchors',
      'customer-answer-history',
    ]);
    expect(merged.invalidProjection).toEqual(['citations', 'history']);

    expect(
      projectAnswerRelations(
        {
          answer_pub_id: 'ans_other',
          citations: [],
          evidence: [],
          history: [],
        },
        'ans_relation_safe',
      ),
    ).toBeNull();

    const invalid = projectAnswerRelations(
      {
        answer_pub_id: 'ans_relation_safe',
        citations: [
          {
            ...citations[0]!,
            canonical_url: 'https://user:proxy-password@source.example/article',
          },
        ],
        evidence: [
          {
            ...evidence[0]!,
            sha256: 'Bearer evidence-hash-canary',
            anchors: [
              {
                pub_id: 'anch_invalid',
                text_start: 8,
                text_end: 4,
                bbox: null,
                page_number: null,
                quote_hash: 'd'.repeat(64),
              },
            ],
          },
        ],
        history: [{ ...historyRows[0]!, created_at: '1' }],
      } as never,
      'ans_relation_safe',
    );
    expect(invalid?.citations).toEqual([]);
    expect(invalid?.evidence).toEqual([]);
    expect(invalid?.history).toEqual([]);
    expect(invalid?.invalidProjection).toEqual(
      expect.arrayContaining(['citations', 'evidence', 'anchors', 'history']),
    );
    expect(JSON.stringify(invalid)).not.toMatch(/proxy-password|Bearer|canary/i);
  });

  it('accepts only each platform official share host and path', () => {
    expect(safeOfficialShareUrl('https://www.doubao.com/thread/abc', 'doubao')).toBe(
      'https://www.doubao.com/thread/abc',
    );
    expect(safeOfficialShareUrl('https://chat.deepseek.com/share/abc', 'DeepSeek')).toBe(
      'https://chat.deepseek.com/share/abc',
    );
    expect(safeOfficialShareUrl('https://mr.baidu.com/r/abc', '文心一言')).toBe(
      'https://mr.baidu.com/r/abc',
    );
    expect(safeOfficialShareUrl('https://wenxin.baidu.com/share/abc', 'yiyan')).toBe(
      'https://wenxin.baidu.com/share/abc',
    );
    expect(safeOfficialShareUrl('https://evil.example/thread/abc', 'doubao')).toBeNull();
    expect(safeOfficialShareUrl('https://www.doubao.com/thread/abc', 'deepseek')).toBeNull();
    expect(safeOfficialShareUrl('http://chat.deepseek.com/share/abc', 'deepseek')).toBeNull();
  });

  it('separates runtime, official share, AI-open previews, verified brand proof and legacy review', () => {
    const evidenceRows = [
      ['evd_runtime_safe', 'answer_screenshot', 'answer_page', []],
      ['evd_share_image_safe', 'share_image', 'official_share_image', []],
      ['evd_share_link_safe', 'share_link', 'official_share_link', []],
      ['evd_open_safe', 'source_screenshot', 'ai_opened_source_preview', []],
      [
        'evd_brand_safe',
        'source_screenshot',
        'brand_mention_source_snapshot',
        [
          {
            pub_id: 'anch_brand_safe',
            text_start: 10,
            text_end: 14,
            bbox: {
              x: 100,
              y: 80,
              width: 200,
              height: 40,
              confidence: 1,
              image_width: 700,
              image_height: 300,
            },
            page_number: 1,
            quote_hash: 'b'.repeat(64),
          },
        ],
      ],
      ['evd_legacy_safe', 'source_screenshot', 'cited_source_snapshot', []],
      ['evd_unproven_safe', 'source_screenshot', 'brand_mention_source_snapshot', []],
    ].map(([pub_id, kind, relation_type, anchors]) => ({
      pub_id,
      relation_type,
      kind,
      access_class: 'customer_private',
      sha256: 'a'.repeat(64),
      mime_type: kind === 'share_link' ? 'application/json' : 'image/png',
      byte_size: 512,
      source_url: 'https://source.example/page',
      capture_time: '2026-08-12T08:00:00Z',
      anchors,
    }));
    const relation = projectAnswerRelations(
      {
        answer_pub_id: 'ans_purpose_safe',
        citations: [],
        answer_citations: [],
        evidence: evidenceRows,
        opened_source_previews: [evidenceRows[3]],
        brand_mention_evidence: [evidenceRows[4]],
        history: [],
      },
      'ans_purpose_safe',
    );
    expect(relation).not.toBeNull();
    const groups = groupLiveEvidenceByPurpose(relation!.evidence);
    expect(groups.runtimeAnswerScreenshots.map((asset) => asset.id)).toEqual([
      'evd_runtime_safe',
    ]);
    expect(groups.officialShareImages.map((asset) => asset.id)).toEqual([
      'evd_share_image_safe',
    ]);
    expect(groups.officialShareLinks.map((asset) => asset.id)).toEqual(['evd_share_link_safe']);
    expect(groups.aiOpenedPagePreviews.map((asset) => asset.id)).toEqual(['evd_open_safe']);
    expect(groups.brandMentionScreenshots.map((asset) => asset.id)).toEqual(['evd_brand_safe']);
    expect(groups.sourceReviewScreenshots.map((asset) => asset.id)).toEqual([
      'evd_legacy_safe',
      'evd_unproven_safe',
    ]);
  });

  it('bounds answer pages and preserves unknown mention as distinct from a real zero', () => {
    const safeAnswer = {
      pub_id: 'ans_safe',
      project_pub_id: 'prj_safe',
      run_pub_id: 'run_safe',
      config_version_pub_id: 'cfv_safe',
      query_pub_id: 'qry_safe',
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
      citation_count: 0,
    };
    const projected = projectCustomerAnswerPage(
      {
        data: [
          safeAnswer,
          {
            ...safeAnswer,
            pub_id: 'ans_invalid',
            response_text: 'Bearer answer-page-canary',
          },
          { ...safeAnswer, pub_id: 'ans_over_limit' },
        ],
        page: { next_cursor: null, has_more: false },
      },
      'prj_safe',
    );

    expect(projected).toMatchObject({
      total: 3,
      invalid: true,
      answers: [
        {
          id: 'ans_safe',
          mention: null,
          cited: [],
          capturedAt: '2026-07-25 08:00',
        },
      ],
    });
    expect(JSON.stringify(projected)).not.toContain('answer-page-canary');
  });

  it('projects responsible-member choices without PII or secret-shaped labels', () => {
    expect(
      projectResponsibleMemberViews([
        { user_pub_id: 'usr_safe', label: '成员 · 00000001', role: 'operator' },
        {
          user_pub_id: 'usr_hostile',
          label: 'Bearer responsible-member-canary',
          role: 'operator',
        },
        { user_pub_id: 'usr_unknown', label: '未知角色', role: 'owner' },
      ]),
    ).toEqual([{ user_pub_id: 'usr_safe', label: '成员 · 00000001', role: 'operator' }]);
  });

  it('bounds account lifecycle collections and distinguishes truncation from invalid rows', () => {
    const safeAccount: CustomerAccountView = {
      pub_id: 'pac_customer_safe',
      account_mask: '尾号 · 7391',
      platform_label: '豆包',
      owner_label: '当前客户',
      custody_mode: 'customer_device',
      admission_level: 'read_verified',
      scopes: ['read', 'query'],
      authorization_expires_at: '2026-12-31T15:59:59Z',
      region_label: '中国大陆 · 华北',
      session_health: 'healthy',
      last_verified_at: '2026-07-25T06:00:00Z',
      intervention_status: 'none',
      revocation_receipt_pub_id: null,
      revoked_at: null,
    };
    const accounts = projectCustomerAccountCollection([
      { ...safeAccount, token: 'Bearer ignored-account-extension' } as CustomerAccountView,
      { ...safeAccount, pub_id: 'pac_customer_older' },
    ]);
    expect(accounts).toMatchObject({ total: 2, shown: 1, invalid: false });
    expect(accounts.data[0]).toMatchObject({
      pubId: 'pac_customer_safe',
      summary: { accountMask: '尾号 · 7391', admissionLevel: 'read_verified' },
    });
    expect(JSON.stringify(accounts)).not.toContain('ignored-account-extension');

    const invalidAccount = projectCustomerAccountCollection([
      {
        ...safeAccount,
        scopes: ['read', 'Bearer account-scope-canary'],
      },
    ]);
    expect(invalidAccount).toMatchObject({ total: 1, shown: 0, invalid: true, data: [] });

    const members = projectResponsibleMemberResult(
      [
        { user_pub_id: 'usr_safe_a', label: '成员 · 00000001', role: 'operator' },
        { user_pub_id: 'usr_safe_a', label: '成员 · 00000002', role: 'operator' },
        { user_pub_id: 'usr_over_limit', label: '成员 · 00000003', role: 'reviewer' },
      ],
      2,
    );
    expect(members).toMatchObject({ total: 3, shown: 1, invalid: true });
    expect(customerAccountLifecycleProjectionLimits).toEqual({
      accounts: 1,
      responsibleMembers: 100,
      events: 100,
      pairings: 50,
    });
  });

  it('projects ordered events and account-bound pairing states without retaining secrets', () => {
    const events = projectCustomerEventResult([
      {
        pub_id: 'sev_new',
        event_type: 'customer_pairing.completed',
        occurred_at: '2026-07-25T08:01:00Z',
        cookie: 'SESSION=event-extension-canary',
      } as CustomerEventView,
      {
        pub_id: 'sev_old',
        event_type: 'customer_pairing.requested',
        occurred_at: '2026-07-25T08:00:00Z',
      },
      {
        pub_id: 'sev_out_of_order',
        event_type: 'customer_pairing.stale',
        occurred_at: '2026-07-25T09:00:00Z',
      },
    ]);
    expect(events).toMatchObject({ total: 3, shown: 2, invalid: true });
    expect(events.data.map((event) => event.id)).toEqual(['sev_new', 'sev_old']);
    expect(JSON.stringify(events)).not.toContain('event-extension-canary');

    const pairing = {
      pub_id: 'int_pairing_safe',
      account_pub_id: 'pac_customer_safe',
      account_mask: '尾号 · 7391',
      allowed_domain: 'doubao.com',
      action: 'read',
      challenge_type: 'qr',
      state: 'completed',
      expires_at: '2026-07-25T08:05:00Z',
    };
    const pairings = projectCustomerPairingResult(
      [
        { ...pairing, otp: '824911' },
        {
          ...pairing,
          pub_id: 'int_cross_account',
          account_pub_id: 'pac_other',
          profile_path: '/secret/profile/pairing-unit-canary',
        },
      ] as unknown as CustomerPairingView[],
      'pac_customer_safe',
      'int_pairing_safe',
    );
    expect(pairings).toMatchObject({
      total: 2,
      shown: 1,
      invalid: true,
      current: { pubId: 'int_pairing_safe', stage: 'completed' },
    });
    expect(JSON.stringify(pairings)).not.toMatch(/824911|pairing-unit-canary/);
  });

  it('projects analytics breakdown facts without retaining hostile extension fields', () => {
    expect(
      projectAnalyticsBreakdown(
        [
          {
            group_by: 'question',
            day: null,
            model: null,
            region: null,
            mode: null,
            question_pub_id: 'qry_safe',
            question_text: '客户如何选择平台',
            answer_count: 4,
            mentioned_count: 3,
            mention_rate: 0.75,
            average_rank: 2,
            citation_coverage: 0.5,
            token: 'Bearer analytics-breakdown-unit-canary',
          },
        ] as unknown as AnalyticsBreakdownResponse,
        'question',
      ),
    ).toEqual([
      {
        key: 'qry_safe',
        day: null,
        model: null,
        region: null,
        mode: null,
        questionPubId: 'qry_safe',
        questionText: '客户如何选择平台',
        answerCount: 4,
        mentionedCount: 3,
        mentionRate: 0.75,
        averageRank: 2,
        citationCoverage: 0.5,
      },
    ]);
  });

  it('bounds monitoring collections and separates malformed rows from ordinary truncation', () => {
    const overview = projectAnalyticsOverviewResult(
      [
        ['mention_rate', 0.5],
        ['average_rank', 2],
        ['top3_rate', 0.4],
        ['citation_coverage', 0.25],
        ['mention_rate', 0.9],
      ].map(([metric, value]) => ({
        metric,
        value,
        numerator: 2,
        denominator: 4,
        state: 'ready',
        metric_version: 'metric-v1',
        scorer_version: 'scorer-v1',
        filter_hash: 'safe',
      })) as AnalyticsOverviewSafeResponse,
    );
    expect(overview).toMatchObject({
      total: 5,
      invalid: false,
    });
    expect(overview.data).toHaveLength(customerMonitoringProjectionLimits.overview);

    const competitors = projectAnalyticsCompetitors(
      Array.from({ length: 51 }, (_, index) => ({
        competitor: index === 49 ? 'Bearer competitor-canary' : `竞品 ${index}`,
        mention_rate: 0.5,
        mention_count: 2,
        answer_count: 4,
      })) as never,
    );
    expect(competitors).toMatchObject({ total: 51, invalid: true });
    expect(competitors.data).toHaveLength(customerMonitoringProjectionLimits.competitors - 1);

    const questions = projectAnalyticsBreakdownResult(
      Array.from({ length: 101 }, (_, index) => ({
        group_by: 'question',
        day: null,
        model: null,
        region: null,
        mode: null,
        question_pub_id: index === 99 ? 'Cookie=question-canary' : `qry_${index}`,
        question_text: `问题 ${index}`,
        answer_count: 4,
        mentioned_count: 2,
        mention_rate: 0.5,
        average_rank: 2,
        citation_coverage: 0.5,
      })) as never,
      'question',
    );
    expect(questions).toMatchObject({ total: 101, invalid: true });
    expect(questions.data).toHaveLength(customerMonitoringProjectionLimits.question - 1);

    const impossibleDay = projectAnalyticsBreakdownResult(
      [
        {
          group_by: 'day',
          day: '2026-02-30',
          answer_count: 4,
          mentioned_count: 2,
          mention_rate: 0.5,
          average_rank: 2,
          citation_coverage: 0.5,
        },
      ] as never,
      'day',
    );
    expect(impossibleDay).toEqual({ data: [], total: 1, invalid: true });
    expect(JSON.stringify({ competitors, questions })).not.toMatch(/Bearer|Cookie|canary/i);
  });

  // 20+ 步真实 userEvent 交互在共享 CI runner 上会越过 vitest 默认 5s（首跑实测 5118ms），
  // 给足 20s 窗口；断言内容不变。
  it(
    'completes pairing and revocation without asking for or persisting secrets',
    { timeout: 20000 },
    async () => {
      const user = userEvent.setup();
      const { container } = render(<Shell />);

      await user.click(screen.getByRole('button', { name: /平台账号/ }));
      expect(screen.getByRole('heading', { name: '客户终端安全配对' })).toBeTruthy();
      expect(container.querySelector('input[type="password"]')).toBeNull();

      await user.clear(screen.getByLabelText('账号掩码'));
      await user.type(screen.getByLabelText('账号掩码'), 'customer@example.test');
      await user.click(screen.getByRole('button', { name: '登记授权' }));
      expect(screen.getByText('只填写带 *、尾号或其他明确隐藏标记的账号掩码')).toBeTruthy();
      await user.clear(screen.getByLabelText('账号掩码'));
      await user.type(screen.getByLabelText('账号掩码'), 'customer-***21');
      await user.clear(screen.getByLabelText('运营责任人'));
      await user.type(screen.getByLabelText('运营责任人'), '周岚');
      await user.selectOptions(screen.getByLabelText('托管模式'), 'customer-device');
      await user.click(screen.getByRole('button', { name: '登记授权' }));
      expect(await screen.findByText(/授权登记已更新/)).toBeTruthy();

      await user.click(screen.getByRole('button', { name: '创建一次性配对' }));
      expect(screen.getByText('请二次确认本次任务')).toBeTruthy();
      expect(screen.getByText(/请勿在聊天或普通表单粘贴验证码/)).toBeTruthy();

      await user.click(screen.getByRole('button', { name: '确认并进入配对演示' }));
      const pairingVisual = screen.getByRole('img', { name: /一次性安全配对二维码占位/ });
      expect(pairingVisual.getAttribute('data-visual-evidence')).toBe('payload-free');
      expect(pairingVisual.querySelector('img,canvas,svg,picture,video,object,embed')).toBeNull();
      expect(pairingVisual.getAttribute('style')).toBeNull();
      await user.click(screen.getByRole('button', { name: '终端已连接' }));
      expect(screen.getByText('请在豆包原生页面完成验证')).toBeTruthy();
      expect(container.querySelector('input[type="password"]')).toBeNull();

      await user.click(screen.getByRole('button', { name: '模拟平台确认完成' }));
      expect(screen.getByText('配对与验证已完成')).toBeTruthy();
      expect(screen.getByText(/准入保持 read_verified/)).toBeTruthy();
      await user.click(screen.getByRole('button', { name: '撤销授权' }));
      expect(screen.getByRole('heading', { name: '撤销已执行' })).toBeTruthy();
      expect(screen.getByText('删除托管秘密副本')).toBeTruthy();

      const forbidden = [
        'SESSION=',
        'Bearer ',
        'dlp-canary',
        '/secret/browser/profile',
        '13800138000',
      ];
      const surfaces = [
        container.textContent ?? '',
        location.href,
        JSON.stringify(localStorage),
        JSON.stringify(sessionStorage),
      ];
      for (const surface of surfaces)
        for (const secret of forbidden) expect(surface).not.toContain(secret);
    },
  );

  it.each([
    ['拒绝', '本次配对已拒绝'],
    ['超时', '一次性配对已超时'],
  ])('supports the %s terminal state', async (outcome, expected) => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: /平台账号/ }));
    await user.click(screen.getByRole('button', { name: '创建一次性配对' }));
    if (outcome === '拒绝') {
      await user.click(screen.getByRole('button', { name: '拒绝' }));
    } else {
      await user.click(screen.getByRole('button', { name: '确认并进入配对演示' }));
      await user.click(screen.getByRole('button', { name: '模拟超时' }));
    }
    expect(screen.getByRole('heading', { name: expected })).toBeTruthy();
    expect(screen.getByRole('button', { name: '重新开始' })).toBeTruthy();
  });

  it('validates truth confirmation and creates a new profile version', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: '资料' }));
    await user.click(screen.getByRole('button', { name: '保存并生成版本' }));
    expect(screen.getByText('提交前必须确认资料真实性')).toBeTruthy();
    await user.click(screen.getByRole('checkbox', { name: /我确认上述客户声明真实/ }));
    await user.click(screen.getByRole('button', { name: '保存并生成版本' }));
    expect(await screen.findByText(/客户声明 v3/)).toBeTruthy();
  });

  it('adds a validated brand, product and customer-confirmed competitor', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: '品牌产品' }));
    await user.type(screen.getByLabelText('品牌名称'), '澄明云');
    await user.clear(screen.getByLabelText('官方 HTTPS 网站'));
    await user.type(screen.getByLabelText('官方 HTTPS 网站'), 'https://example.test');
    await user.type(screen.getByLabelText('产品或服务'), '可信知识助手');
    await user.type(screen.getByLabelText('客户指定竞品'), '北辰智库');
    await user.type(screen.getByLabelText('禁止使用的表述'), '未经证明的行业第一');
    await user.click(
      screen.getByRole('checkbox', { name: /我确认品牌、产品、竞品与禁止表述真实/ }),
    );
    await user.click(screen.getByRole('button', { name: '登记资产' }));
    expect(screen.getByText('澄明云')).toBeTruthy();
    expect(screen.getByText('可信知识助手')).toBeTruthy();
    expect(screen.getByText('北辰智库')).toBeTruthy();
  });

  it('validates and submits a configuration request without mutating scheduling truth', async () => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: '问题目标' }));
    await user.click(screen.getByRole('button', { name: '提交审核' }));
    expect(screen.getByText('问题至少需要 8 个字')).toBeTruthy();
    expect(screen.getByText('请说明至少 10 个字的业务原因')).toBeTruthy();
    await user.type(screen.getByLabelText('关注问题'), '制造企业如何选择可信的私有化知识库？');
    await user.type(screen.getByLabelText('业务原因'), '需要覆盖客户采购决策阶段的真实比较问题。');
    await user.click(screen.getByRole('button', { name: '提交审核' }));
    expect(screen.getByText('待运营审核')).toBeTruthy();
    expect(screen.getByText('制造企业如何选择可信的私有化知识库？')).toBeTruthy();
  });

  it('paginates answers and opens an anchored evidence diff dialog', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: '回答证据' }));
    expect(screen.getByText('企业知识库如何选择？')).toBeTruthy();
    expect(screen.getByText('Answer ans_01')).toBeTruthy();
    await user.click(screen.getAllByRole('button', { name: '查看回答截图' })[0]!);
    expect(screen.getByRole('dialog', { name: '证据与历史差异' })).toBeTruthy();
    expect(screen.getByRole('img', { name: /锚点高亮品牌提及/ })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '关闭证据弹窗' }));
    await user.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('私有化大模型方案对比')).toBeTruthy();
    await user.selectOptions(screen.getByLabelText('回答地域'), '上海');
    expect(await screen.findByText('企业知识库如何选择？')).toBeTruthy();
    expect(screen.getByText('第 1 / 1 页')).toBeTruthy();
  });

  it('submits a report question and records customer receipt confirmation', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: '报告' }));
    const question = screen.getByLabelText('问题');
    await user.type(question, 'Cookie=SESSION-customer-question-canary');
    expect(screen.getByText(/请勿在普通表单粘贴验证码/)).toBeTruthy();
    expect((screen.getByRole('button', { name: '提交问题' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    await user.clear(question);
    await user.type(question, 'Top 3 目标值如何复算？');
    await user.click(screen.getByRole('button', { name: '提交问题' }));
    expect(screen.getByText('Top 3 目标值如何复算？')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '确认收到 v1.2' }));
    expect(screen.getByText('已确认接收 v1.2')).toBeTruthy();
  });

  it('invites a member and displays only a masked email', async () => {
    const user = userEvent.setup();
    const { container } = render(<Shell />);
    await user.click(screen.getByRole('button', { name: '成员' }));
    await user.type(screen.getByLabelText('姓名'), '周岚');
    await user.type(screen.getByLabelText('工作邮箱'), 'zhoulan@example.test');
    await user.selectOptions(screen.getByLabelText('项目角色'), 'member');
    await user.click(screen.getByRole('button', { name: '发送邀请' }));
    expect(screen.getByText('周岚')).toBeTruthy();
    expect(screen.getByText('z***@example.test')).toBeTruthy();
    expect(container.textContent).not.toContain('zhoulan@example.test');
  });
});

describe('Customer intake workspace', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    history.replaceState(null, '', '/platform/customer/');
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              status: 'ok',
              service: 'geo-platform-v2',
              version: 'contract-v1',
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const intakeValidBase = {
    contactPerson: '林澄',
    contactInfo: 'lin.cheng@example.test',
    website: '',
    wechat: '',
    douyin: '',
    socialMedia: '',
    audienceDesc: '',
    sellingPoints: '',
    fillerName: '',
    businessLicenseCode: '',
    reviewCategory: '',
    preReviewRequired: false,
    adReviewNo: '',
    adReviewAuthority: '',
    adReviewExpiry: '',
    goals: ['提升AI搜索曝光'],
    audienceType: ['B2B企业客户'],
    platforms: ['豆包'],
    adReviewDocTypes: [],
    regionsText: '',
    trademarksText: '',
    evidenceText: '',
    licenses: [{ name: '', number: '', expiry: '' }],
    truthItems: [] as string[],
  };

  it('renders the intake form with vocab options, fixture content and dynamic lists', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: '信息表' }));
    expect(screen.getByRole('heading', { name: '客户信息收集表' })).toBeTruthy();
    expect(screen.getByLabelText(/联系人/)).toBeTruthy();
    expect(screen.getByText('提升AI搜索曝光')).toBeTruthy();
    expect(screen.getByText(INTAKE_TRUTH_CONFIRM_ITEMS[0] as string)).toBeTruthy();
    expect(screen.getByRole('heading', { name: /拟推广产品/ })).toBeTruthy();
    expect(screen.getByText('云岫知识库私有化部署')).toBeTruthy();
    expect(screen.getByText('预算 50 万的制造企业知识库怎么选')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'AI 一键调研预填' })).toBeTruthy();
  });

  it('renders the collapsible AI operations dock with a per-operation model drawer', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    // 默认展开：列出 AI 操作与当前模型徽章；fixture 环境下模型下拉禁用
    expect(screen.getByLabelText('AI 操作面板')).toBeTruthy();
    expect(screen.getByText('AI 操作')).toBeTruthy();
    expect(screen.getByText('默认模型')).toBeTruthy();
    const select = screen.getByLabelText('AI 一键调研预填模型选择') as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    // 折叠后清单消失、状态记忆到 localStorage；再展开恢复
    await user.click(screen.getByRole('button', { name: '收起 AI 面板' }));
    expect(screen.queryByText('AI 操作')).toBeNull();
    expect(localStorage.getItem('geo.ai.dock.expanded')).toBe('0');
    await user.click(screen.getByRole('button', { name: '展开 AI 面板' }));
    expect(screen.getByText('AI 操作')).toBeTruthy();
  });

  it('gates saving on the five truth confirmations and then saves explicitly', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: '信息表' }));
    await user.click(screen.getByRole('button', { name: '保存信息表' }));
    expect(screen.getByText(/请逐条勾选信息真实性确认/)).toBeTruthy();
    for (const item of INTAKE_TRUTH_CONFIRM_ITEMS) {
      await user.click(screen.getByRole('checkbox', { name: item }));
    }
    await user.click(screen.getByRole('button', { name: '保存信息表' }));
    expect(await screen.findByText('信息表已保存')).toBeTruthy();
  });

  it('validates vocab membership, license format and DLP before any save', async () => {
    expect(intakeProfileSchema.safeParse(intakeValidBase).success).toBe(true);
    expect(
      intakeProfileSchema.safeParse({ ...intakeValidBase, goals: ['词表外目标'] }).success,
    ).toBe(false);
    expect(
      intakeProfileSchema.safeParse({ ...intakeValidBase, platforms: ['不存在的平台'] }).success,
    ).toBe(false);
    expect(
      intakeProfileSchema.safeParse({ ...intakeValidBase, businessLicenseCode: 'abc' }).success,
    ).toBe(false);
    expect(
      intakeProfileSchema.safeParse({
        ...intakeValidBase,
        contactPerson: 'Cookie=SESSION=intake-canary',
      }).success,
    ).toBe(false);

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: '信息表' }));
    const contact = screen.getByLabelText(/联系人/);
    await user.clear(contact);
    await user.type(contact, 'Cookie=SESSION=intake-canary');
    const licenseCode = screen.getByLabelText(/统一社会信用代码/);
    await user.clear(licenseCode);
    await user.type(licenseCode, 'abc');
    await user.click(screen.getByRole('button', { name: '保存信息表' }));
    expect(
      screen.getByText('请勿在普通表单粘贴验证码、Cookie、token、密码、完整手机号或 profile 路径'),
    ).toBeTruthy();
    expect(screen.getByText('统一社会信用代码须为 18 位数字或大写字母')).toBeTruthy();
    expect(screen.queryByText('信息表已保存')).toBeNull();
  });
});
