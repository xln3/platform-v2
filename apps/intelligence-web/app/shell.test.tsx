// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router';

vi.mock('@xyflow/react', () => ({
  ReactFlow: () => <div data-testid="react-flow" />,
  Background: () => null,
  Controls: () => null,
}));

import Shell, {
  investigationProjectionLimits,
  liveGraphEdgeIdentity,
  projectLiveGraphEdges,
  projectLiveHistory,
  projectLiveInvestigation,
  projectLiveSourceRows,
  selectLiveHistoryView,
} from './shell';
const renderShell = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Shell />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('Intelligence Web', () => {
  beforeEach(() => {
    history.replaceState(null, '', '/platform/intelligence/');
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

  it('explains atomic claims and source independence', async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: 'Claim 矩阵' }));
    expect(screen.getByText(/独立一手来源不足 2 个/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '多源证据' }));
    await user.selectOptions(screen.getByLabelText('筛选同源簇'), 'C-07');
    expect(screen.getAllByText('同源传播')).toHaveLength(2);
  });

  it('provides a table equivalent for the propagation graph', async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: '传播关系' }));
    expect(screen.getByTestId('react-flow')).toBeTruthy();
    expect(screen.getByRole('table', { name: '传播图节点与关系' })).toBeTruthy();
    expect(screen.getByText('相似度 0.91')).toBeTruthy();
  });

  it('uses the service relation tuple as the stable graph edge identity', () => {
    const supports = {
      from: 'evd_shared_graph',
      to: 'clm_shared_graph',
      relation: 'supports',
    };
    const mentions = { ...supports, relation: 'mentions' };

    expect(liveGraphEdgeIdentity(supports)).toBe(
      'live-edge:evd_shared_graph:supports:clm_shared_graph',
    );
    expect(liveGraphEdgeIdentity(mentions)).toBe(
      'live-edge:evd_shared_graph:mentions:clm_shared_graph',
    );
    expect(liveGraphEdgeIdentity(supports)).not.toBe(liveGraphEdgeIdentity(mentions));
    expect(
      projectLiveGraphEdges([
        { ...supports, weight: 0.9, evidenceId: 'evd_shared_graph' },
        { ...mentions, weight: 0.6, evidenceId: '' },
      ]),
    ).toEqual([
      expect.objectContaining({
        id: liveGraphEdgeIdentity(supports),
        label: 'supports 0.90',
        type: 'liveParallel',
        data: { labelOffset: -14 },
        pathOptions: { curvature: 0.2 },
      }),
      expect.objectContaining({
        id: liveGraphEdgeIdentity(mentions),
        label: 'mentions 0.60',
        type: 'liveParallel',
        data: { labelOffset: 14 },
        pathOptions: { curvature: 0.55 },
      }),
    ]);
  });

  it('keeps repeated evidence relations uniquely keyed under the assessed source cluster', () => {
    const projection = projectLiveInvestigation({
      pub_id: 'inv_shared_source',
      scores: [
        {
          pub_id: 'score_shared_source',
          probability: '0.72',
          evidence_sufficiency: '0.83',
          uncertainty: '0.18',
          rule_version: 'shared-source-v1',
          explanation: ['同一来源可以分别支持多个原子 Claim。'],
          created_at: '2026-07-25T00:00:00Z',
        },
      ],
      claims: [
        {
          pub_id: 'clm_shared_source_a',
          normalized_text: '第一个原子 Claim',
          verifiability: 'verifiable',
        },
        {
          pub_id: 'clm_shared_source_b',
          normalized_text: '第二个原子 Claim',
          verifiability: 'verifiable',
        },
      ],
      evidence_matrix: [
        {
          pub_id: 'ce_shared_source_a',
          claim_pub_id: 'clm_shared_source_a',
          evidence_pub_id: 'evd_shared_source',
          relation: 'supports',
          source_cluster: 'relation-cluster-a',
          independence_weight: '0.8',
          rationale: '支持第一个 Claim。',
        },
        {
          pub_id: 'ce_shared_source_b',
          claim_pub_id: 'clm_shared_source_b',
          evidence_pub_id: 'evd_shared_source',
          relation: 'contradicts',
          source_cluster: 'relation-cluster-b',
          independence_weight: '0.7',
          rationale: '反驳第二个 Claim。',
        },
      ],
      source_independence: [
        {
          pub_id: 'srca_shared_source',
          source_pub_id: 'evd_shared_source',
          cluster_id: 'assessed-source-cluster',
          independence_weight: '0.9',
          circular_citation_risk: '0.1',
        },
      ],
      graph: [],
      appeals: [],
      verdicts: [],
    } as never);

    expect(projection).not.toBeNull();
    const rows = projectLiveSourceRows(projection!);
    expect(rows).toEqual([
      expect.objectContaining({
        id: 'ce_shared_source_a',
        source: 'evd_shared_source',
        cluster: 'assessed-source-cluster',
        stance: '支持',
      }),
      expect.objectContaining({
        id: 'ce_shared_source_b',
        source: 'evd_shared_source',
        cluster: 'assessed-source-cluster',
        stance: '反驳',
      }),
    ]);
    expect(new Set(rows.map((row) => row.id)).size).toBe(rows.length);
  });

  it('exposes the governed Anti-GEO model admission boundary', async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: '模型准入' }));
    expect(screen.getByRole('heading', { name: '模型校准与准入' })).toBeTruthy();
    expect(screen.getByRole('table', { name: '校准数据集治理状态' })).toBeTruthy();
    expect(screen.getByRole('table', { name: '模型评估指标与准入状态' })).toBeTruthy();
    expect(screen.getByText('external-approved-v1')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: '独立审批' }));
    const rationale = screen.getByLabelText('独立复核理由');
    await user.type(rationale, 'Cookie=SESSION-calibration-canary');
    expect(screen.getByText(/不能包含秘密或敏感凭据/)).toBeTruthy();
    expect((screen.getByRole('button', { name: '确认审批' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    await user.clear(rationale);
    await user.type(rationale, '已独立核验外部标签策略和不可变来源证据');
    await user.click(screen.getByRole('button', { name: '确认审批' }));
    expect(await screen.findByText(/数据集已由独立审核者批准/)).toBeTruthy();
  });

  it('records a versioned verdict, appeal and evidence package', async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: /裁决与申诉/ }));
    await user.click(screen.getByRole('button', { name: '确认高风险表述' }));
    const reason = screen.getByLabelText('申诉理由');
    await user.type(reason, 'Cookie=SESSION-intelligence-form-canary');
    expect(screen.getByText(/请勿在申诉中粘贴验证码/)).toBeTruthy();
    expect((screen.getByRole('button', { name: '提交申诉' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    await user.clear(reason);
    await user.type(reason, '新增登记材料需要重新复核');
    await user.click(screen.getByRole('button', { name: '提交申诉' }));
    expect(screen.getByText(/原裁决保持可追溯/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '记录二次复核' }));
    expect(screen.getAllByText('reviewed').length).toBeGreaterThan(0);
  });

  it('projects page history without retaining secret-shaped extensions or diff bodies', () => {
    const projection = projectLiveHistory(
      [
        {
          content_pub_id: 'cnt_safe_01',
          version_pub_id: 'cntv_safe_01',
          evidence_pub_id: 'evd_safe_01',
          canonical_url: 'https://evidence.example/page?token=Bearer%20query-canary',
          title: 'Bearer title-canary',
          version_number: 1,
          body_hash: 'a'.repeat(64),
          captured_at: '2026-07-25T01:00:00Z',
          snapshot_number: 1,
          cookie: 'SESSION=history-canary',
        },
        {
          content_pub_id: 'cnt_safe_01',
          version_pub_id: 'cntv_safe_02',
          evidence_pub_id: 'evd_safe_02',
          canonical_url: 'https://evidence.example/page',
          title: '安全页面标题',
          version_number: 2,
          body_hash: 'b'.repeat(64),
          captured_at: '2026-07-25T02:00:00Z',
          snapshot_number: 2,
          profile_path: '/secret/profile/history-canary',
        },
        {
          content_pub_id: 'cnt_safe_01',
          version_pub_id: 'cntv_ambiguous_time',
          evidence_pub_id: 'evd_safe_03',
          canonical_url: 'https://evidence.example/ambiguous',
          title: '不得展示为 2001 年的页面',
          version_number: 3,
          body_hash: 'c'.repeat(64),
          captured_at: '1',
          snapshot_number: 3,
        },
      ] as never,
      [
        {
          pub_id: 'diff_safe_01',
          content_pub_id: 'cnt_safe_01',
          before_version_pub_id: 'cntv_safe_01',
          after_version_pub_id: 'cntv_safe_02',
          before_evidence_pub_id: 'evd_safe_01',
          after_evidence_pub_id: 'evd_safe_02',
          created_at: '2026-07-25T02:01:00Z',
          similarity: 0.75,
          visual_diff_available: false,
          text_diff: {
            before_hash: 'a'.repeat(64),
            after_hash: 'b'.repeat(64),
            unified: 'OTP 824911 · Bearer diff-canary',
          },
          token: 'Bearer response-canary',
        },
      ] as never,
    );

    expect(projection.pages).toHaveLength(2);
    expect(projection.pages[0]).toMatchObject({
      title: '无标题页面',
      source: 'evidence.example',
    });
    expect(projection.diffs).toHaveLength(1);
    expect(projection.diffs[0]?.similarity).toBe(0.75);
    expect(projection.invalidProjection).toContain('historyPages');
    expect(JSON.stringify(projection)).not.toMatch(
      /query-canary|title-canary|history-canary|824911|Bearer|SESSION=|profile/i,
    );
  });

  it('binds the selected history page and diff to one content item', () => {
    const projection = projectLiveHistory(
      [
        {
          content_pub_id: 'cnt_history_primary',
          version_pub_id: 'cntv_history_primary_01',
          evidence_pub_id: 'evd_history_primary_01',
          canonical_url: 'https://primary.example/page',
          title: '主要页面',
          version_number: 1,
          body_hash: 'a'.repeat(64),
          captured_at: '2026-07-25T01:00:00Z',
          snapshot_number: 1,
        },
        {
          content_pub_id: 'cnt_history_primary',
          version_pub_id: 'cntv_history_primary_02',
          evidence_pub_id: 'evd_history_primary_02',
          canonical_url: 'https://primary.example/page',
          title: '主要页面（修订）',
          version_number: 2,
          body_hash: 'b'.repeat(64),
          captured_at: '2026-07-25T03:00:00Z',
          snapshot_number: 2,
        },
        {
          content_pub_id: 'cnt_history_trailing',
          version_pub_id: 'cntv_history_trailing_01',
          evidence_pub_id: 'evd_history_trailing_01',
          canonical_url: 'https://trailing.example/page',
          title: '另一页面',
          version_number: 1,
          body_hash: 'c'.repeat(64),
          captured_at: '2026-07-25T02:00:00Z',
          snapshot_number: 1,
        },
      ] as never,
      [
        {
          pub_id: 'diff_history_primary',
          content_pub_id: 'cnt_history_primary',
          before_version_pub_id: 'cntv_history_primary_01',
          after_version_pub_id: 'cntv_history_primary_02',
          before_evidence_pub_id: 'evd_history_primary_01',
          after_evidence_pub_id: 'evd_history_primary_02',
          created_at: '2026-07-25T03:01:00Z',
          similarity: 0.75,
          visual_diff_available: false,
          text_diff: {
            before_hash: 'a'.repeat(64),
            after_hash: 'b'.repeat(64),
          },
        },
      ] as never,
    );

    const defaultView = selectLiveHistoryView(projection);
    expect(defaultView.activeContentPubId).toBe('cnt_history_primary');
    expect(defaultView.previousPage?.versionPubId).toBe('cntv_history_primary_01');
    expect(defaultView.currentPage?.versionPubId).toBe('cntv_history_primary_02');
    expect(defaultView.selectedDiff?.pubId).toBe('diff_history_primary');

    const trailingView = selectLiveHistoryView(projection, 'cnt_history_trailing');
    expect(trailingView.currentPage?.versionPubId).toBe('cntv_history_trailing_01');
    expect(trailingView.previousPage).toBeNull();
    expect(trailingView.selectedDiff).toBeNull();
  });

  it('drops visual diffs that do not close over the projected version and evidence chain', () => {
    const projection = projectLiveHistory(
      [
        {
          content_pub_id: 'cnt_chain_safe',
          version_pub_id: 'cntv_chain_safe_01',
          evidence_pub_id: 'evd_chain_safe_01',
          canonical_url: 'https://chain.example/page',
          title: '第一版',
          version_number: 1,
          body_hash: 'a'.repeat(64),
          captured_at: '2026-07-25T01:00:00Z',
          snapshot_number: 1,
        },
        {
          content_pub_id: 'cnt_chain_safe',
          version_pub_id: 'cntv_chain_safe_02',
          evidence_pub_id: 'evd_chain_safe_02',
          canonical_url: 'https://chain.example/page',
          title: '第二版',
          version_number: 2,
          body_hash: 'b'.repeat(64),
          captured_at: '2026-07-25T02:00:00Z',
          snapshot_number: 2,
        },
      ] as never,
      [
        {
          pub_id: 'diff_chain_safe',
          content_pub_id: 'cnt_chain_safe',
          before_version_pub_id: 'cntv_chain_safe_01',
          after_version_pub_id: 'cntv_chain_safe_02',
          before_evidence_pub_id: 'evd_chain_safe_01',
          after_evidence_pub_id: 'evd_chain_safe_02',
          created_at: '2026-07-25T02:01:00Z',
          similarity: 0.75,
          visual_diff_available: false,
          text_diff: {
            before_hash: 'a'.repeat(64),
            after_hash: 'b'.repeat(64),
          },
        },
        {
          pub_id: 'diff_chain_wrong_content',
          content_pub_id: 'cnt_chain_other',
          before_version_pub_id: 'cntv_chain_safe_01',
          after_version_pub_id: 'cntv_chain_safe_02',
          before_evidence_pub_id: 'evd_chain_safe_01',
          after_evidence_pub_id: 'evd_chain_safe_02',
          created_at: '2026-07-25T02:01:00Z',
          similarity: 0.7,
          visual_diff_available: false,
          text_diff: {
            before_hash: 'a'.repeat(64),
            after_hash: 'b'.repeat(64),
          },
        },
        {
          pub_id: 'diff_chain_wrong_evidence',
          content_pub_id: 'cnt_chain_safe',
          before_version_pub_id: 'cntv_chain_safe_01',
          after_version_pub_id: 'cntv_chain_safe_02',
          before_evidence_pub_id: 'evd_chain_safe_01',
          after_evidence_pub_id: 'evd_chain_other',
          created_at: '2026-07-25T02:01:00Z',
          similarity: 0.65,
          visual_diff_available: false,
          text_diff: {
            before_hash: 'a'.repeat(64),
            after_hash: 'b'.repeat(64),
          },
        },
        {
          pub_id: 'diff_chain_wrong_hash',
          content_pub_id: 'cnt_chain_safe',
          before_version_pub_id: 'cntv_chain_safe_01',
          after_version_pub_id: 'cntv_chain_safe_02',
          before_evidence_pub_id: 'evd_chain_safe_01',
          after_evidence_pub_id: 'evd_chain_safe_02',
          created_at: '2026-07-25T02:01:00Z',
          similarity: 0.5,
          visual_diff_available: false,
          text_diff: {
            before_hash: 'c'.repeat(64),
            after_hash: 'd'.repeat(64),
            unified: 'Bearer chain-diff-canary',
          },
          cookie: 'SESSION=chain-diff-canary',
        },
      ] as never,
    );

    expect(projection.pages).toHaveLength(2);
    expect(projection.diffs).toEqual([
      expect.objectContaining({
        pubId: 'diff_chain_safe',
        similarity: 0.75,
      }),
    ]);
    expect(projection.invalidProjection).toContain('historyDiffs');
    expect(JSON.stringify(projection)).not.toMatch(/chain-diff-canary|Bearer|SESSION=/i);
  });

  it('fails closed when critical rows are malformed below every projection limit', () => {
    const projection = projectLiveInvestigation({
      pub_id: 'inv_integrity_safe',
      scores: [
        {
          pub_id: 'score_integrity_safe',
          probability: '0.84',
          evidence_sufficiency: '0.91',
          uncertainty: '0.16',
          rule_version: 'integrity-v1',
          explanation: { basis: '真实安全解释' },
          created_at: '2026-07-25T00:00:00Z',
        },
        {
          pub_id: 'score_integrity_safe',
          probability: '0.99',
          evidence_sufficiency: '0.99',
          uncertainty: '0.01',
          rule_version: 'integrity-duplicate',
          explanation: { basis: 'Bearer duplicate-score-canary' },
          created_at: '2026-07-25T00:01:00Z',
        },
        {
          pub_id: 'score_integrity_reverse',
          probability: '0.12',
          evidence_sufficiency: '0.22',
          uncertainty: '0.88',
          rule_version: 'integrity-reverse',
          explanation: { basis: 'SESSION=reverse-score-canary' },
          created_at: '2026-07-24T23:59:00Z',
        },
      ],
      claims: [
        {
          pub_id: 'clm_integrity_safe',
          normalized_text: '安全 Claim',
          verifiability: 'verifiable',
        },
        {
          pub_id: 'clm_integrity_safe',
          normalized_text: 'Bearer duplicate-claim-canary',
          verifiability: 'verifiable',
        },
      ],
      evidence_matrix: [
        {
          pub_id: 'ce_integrity_safe',
          claim_pub_id: 'clm_integrity_safe',
          evidence_pub_id: 'evd_integrity_safe',
          relation: 'supports',
          source_cluster: 'cluster-safe',
          independence_weight: '0.9',
          rationale: '安全理由',
        },
        {
          pub_id: 'ce_integrity_hostile',
          claim_pub_id: 'clm_integrity_safe',
          evidence_pub_id: 'evd_integrity_hostile',
          relation: 'supports',
          source_cluster: 'cluster-safe',
          independence_weight: '0.8',
          rationale: 'Bearer integrity-row-canary',
        },
        {
          pub_id: 'ce_integrity_cross_claim',
          claim_pub_id: 'clm_integrity_other',
          evidence_pub_id: 'evd_integrity_cross_claim',
          relation: 'supports',
          source_cluster: 'cluster-safe',
          independence_weight: '0.8',
          rationale: '跨案件 Claim 关系不得保留',
          cookie: 'SESSION=cross-claim-canary',
        },
        {
          pub_id: 'ce_integrity_duplicate_pair',
          claim_pub_id: 'clm_integrity_safe',
          evidence_pub_id: 'evd_integrity_safe',
          relation: 'supports',
          source_cluster: 'cluster-safe',
          independence_weight: '0.7',
          rationale: '重复关系不得保留',
        },
      ],
      source_independence: [
        {
          pub_id: 'srca_integrity_safe',
          source_pub_id: 'evd_integrity_safe',
          cluster_id: 'cluster-safe',
          independence_weight: '0.9',
          circular_citation_risk: '0.1',
        },
        {
          pub_id: 'srca_integrity_duplicate_source',
          source_pub_id: 'evd_integrity_safe',
          cluster_id: 'cluster-other',
          independence_weight: '0.7',
          circular_citation_risk: '0.2',
          token: 'Bearer duplicate-source-canary',
        },
      ],
      graph: [
        {
          from_pub_id: 'evd_integrity_safe',
          to_pub_id: 'clm_integrity_safe',
          relation: 'supports',
          weight: '0.9',
          evidence_pub_id: 'evd_integrity_safe',
        },
        {
          from_pub_id: 'evd_integrity_safe',
          to_pub_id: 'clm_integrity_safe',
          relation: 'supports',
          weight: '0.7',
          evidence_pub_id: 'evd_integrity_other',
          token: 'Bearer duplicate-graph-canary',
        },
        {
          from_pub_id: 'cntv_integrity_safe',
          to_pub_id: 'ent_integrity_safe',
          relation: 'organized_by',
          weight: '0.8',
          evidence_pub_id: null,
          cookie: 'SESSION=invalid-graph-canary',
        },
      ],
      appeals: [
        {
          pub_id: 'apl_integrity_safe',
          state: 'open',
          submitted_by_pub_id: 'usr_integrity_submitter',
          reason: '申请复核当前裁决。',
          resolution: null,
          resolved_by_pub_id: null,
          resolution_rationale: null,
          created_at: '2026-07-25T03:00:00Z',
          updated_at: '2026-07-25T03:00:00Z',
          resolved_at: null,
        },
        {
          pub_id: 'apl_integrity_safe',
          state: 'reviewing',
          submitted_by_pub_id: 'usr_integrity_submitter',
          reason: '申请复核当前裁决。',
          resolution: null,
          resolved_by_pub_id: null,
          resolution_rationale: null,
          created_at: '2026-07-25T03:01:00Z',
          updated_at: '2026-07-25T03:01:00Z',
          resolved_at: null,
          token: 'Bearer duplicate-appeal-canary',
        },
        {
          pub_id: 'apl_integrity_reverse',
          state: 'open',
          submitted_by_pub_id: 'usr_integrity_submitter',
          reason: '申请复核当前裁决。',
          resolution: null,
          resolved_by_pub_id: null,
          resolution_rationale: null,
          created_at: '2026-07-25T02:59:00Z',
          updated_at: '2026-07-25T02:59:00Z',
          resolved_at: null,
          cookie: 'SESSION=reverse-appeal-canary',
        },
      ],
      verdicts: [
        {
          pub_id: 'vrd_integrity_safe',
          verdict: 'likely',
          reviewer_pub_id: 'usr_integrity_reviewer',
          rationale: '安全人工裁决理由。',
          supersedes_pub_id: null,
          created_at: '2026-07-25T02:00:00Z',
        },
        {
          pub_id: 'vrd_integrity_safe',
          verdict: 'unlikely',
          reviewer_pub_id: 'usr_integrity_reviewer',
          rationale: '安全人工裁决理由。',
          supersedes_pub_id: null,
          created_at: '2026-07-25T02:01:00Z',
          token: 'Bearer duplicate-verdict-canary',
        },
        {
          pub_id: 'vrd_integrity_reverse',
          verdict: 'insufficient',
          reviewer_pub_id: 'usr_integrity_reviewer',
          rationale: '安全人工裁决理由。',
          supersedes_pub_id: null,
          created_at: '2026-07-25T01:59:00Z',
          cookie: 'SESSION=reverse-verdict-canary',
        },
      ],
    } as never);

    expect(projection).not.toBeNull();
    expect(projection?.probability).toBe(0.84);
    expect(projection?.evidenceSufficiency).toBe(0.91);
    expect(projection?.invalidProjection).toEqual(
      expect.arrayContaining([
        'claims',
        'evidenceMatrix',
        'sourceIndependence',
        'graph',
        'appeals',
        'scores',
        'verdicts',
      ]),
    );
    expect(projection?.claims).toHaveLength(1);
    expect(projection?.evidenceMatrix).toEqual([
      expect.objectContaining({ id: 'ce_integrity_safe', claimId: 'clm_integrity_safe' }),
    ]);
    expect(projection?.sourceIndependence).toEqual([
      expect.objectContaining({ id: 'srca_integrity_safe', sourceId: 'evd_integrity_safe' }),
    ]);
    expect(projection?.graph).toEqual([
      expect.objectContaining({
        from: 'evd_integrity_safe',
        to: 'clm_integrity_safe',
        relation: 'supports',
      }),
    ]);
    expect(projection?.projectionNotices.evidenceMatrix).toBeUndefined();
    expect(projection?.verdictState).toBe('pending');
    expect(projection?.openAppealPubId).toBe('');
    expect(JSON.stringify(projection)).not.toMatch(
      /integrity-row-canary|duplicate-claim-canary|cross-claim-canary|duplicate-source-canary|duplicate-graph-canary|invalid-graph-canary|duplicate-score-canary|reverse-score-canary|duplicate-appeal-canary|reverse-appeal-canary|duplicate-verdict-canary|reverse-verdict-canary|Bearer|SESSION=/i,
    );
  });

  it('binds a projected investigation detail to the requested root resource', () => {
    const response = {
      pub_id: 'inv_actual_safe',
      scores: [],
      claims: [],
      evidence_matrix: [],
      source_independence: [],
      graph: [],
      appeals: [],
      verdicts: [],
    };

    expect(projectLiveInvestigation(response as never, 'inv_requested_safe')).toBeNull();
    expect(projectLiveInvestigation(response as never, 'inv_actual_safe')?.investigationPubId).toBe(
      'inv_actual_safe',
    );
    expect(
      projectLiveInvestigation(
        { ...response, pub_id: 'Bearer cross-investigation-canary' },
        'inv_requested_safe',
      ),
    ).toBeNull();
  });

  it('accepts only a linear verdict supersession chain', () => {
    const detail = (broken: boolean) => ({
      pub_id: broken ? 'inv_verdict_chain_broken' : 'inv_verdict_chain_valid',
      scores: [
        {
          pub_id: broken ? 'score_verdict_chain_broken' : 'score_verdict_chain_valid',
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
          pub_id: broken ? 'vrd_verdict_chain_broken_01' : 'vrd_verdict_chain_valid_01',
          verdict: 'likely',
          reviewer_pub_id: 'usr_verdict_reviewer_01',
          rationale: '初次人工裁决理由。',
          supersedes_pub_id: null,
          created_at: '2026-07-25T01:00:00Z',
        },
        {
          pub_id: broken ? 'vrd_verdict_chain_broken_02' : 'vrd_verdict_chain_valid_02',
          verdict: 'unlikely',
          reviewer_pub_id: 'usr_verdict_reviewer_02',
          rationale: '复核后的人工裁决理由。',
          supersedes_pub_id: broken ? 'vrd_verdict_chain_missing' : 'vrd_verdict_chain_valid_01',
          created_at: '2026-07-25T02:00:00Z',
          token: broken ? 'Bearer broken-verdict-chain-canary' : undefined,
        },
      ],
    });

    const valid = projectLiveInvestigation(detail(false) as never);
    const broken = projectLiveInvestigation(detail(true) as never);
    const boundaryFiltered = projectLiveInvestigation({
      ...detail(false),
      verdicts: detail(false).verdicts.slice(0, 1),
      projection: {
        scores: { total: 1, shown: 1, invalid: false },
        explanations: { total: 1, shown: 1, invalid: false },
        claims: { total: 0, shown: 0, invalid: false },
        evidenceMatrix: { total: 0, shown: 0, invalid: false },
        sourceIndependence: { total: 0, shown: 0, invalid: false },
        graph: { total: 0, shown: 0, invalid: false },
        appeals: { total: 0, shown: 0, invalid: false },
        verdicts: { total: 2, shown: 1, invalid: true },
      },
    } as never);

    expect(valid?.verdictState).toBe('rejected');
    expect(valid?.invalidProjection).not.toContain('verdicts');
    expect(broken?.verdictState).toBe('pending');
    expect(broken?.invalidProjection).toContain('verdicts');
    expect(boundaryFiltered?.verdictState).toBe('pending');
    expect(boundaryFiltered?.projectionNotices.verdicts).toEqual({ total: 2, shown: 1 });
    expect(boundaryFiltered?.invalidProjection).toContain('verdicts');
    expect(JSON.stringify(broken)).not.toMatch(/broken-verdict-chain-canary|Bearer/i);
  });

  it('accepts only appeals backed by the projected verdict history', () => {
    const detail = (
      kind: 'consistent' | 'before-verdict' | 'missing-correction',
    ): Record<string, unknown> => {
      const investigationPubId = `inv_appeal_${kind.replace('-', '_')}`;
      return {
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
            state: kind === 'before-verdict' ? 'open' : 'corrected',
            submitted_by_pub_id: 'usr_appeal_submitter',
            reason: '新增独立来源申请重新复核。',
            resolution: kind === 'before-verdict' ? null : 'corrected',
            resolved_by_pub_id: kind === 'before-verdict' ? null : 'usr_appeal_reviewer',
            resolution_rationale: kind === 'before-verdict' ? null : '独立复核确认需要更正原裁决。',
            created_at: kind === 'before-verdict' ? '2026-07-25T00:30:00Z' : '2026-07-25T01:30:00Z',
            updated_at: kind === 'before-verdict' ? '2026-07-25T00:30:00Z' : '2026-07-25T02:30:00Z',
            resolved_at: kind === 'before-verdict' ? null : '2026-07-25T02:30:00Z',
            token: kind === 'consistent' ? undefined : 'Bearer impossible-appeal-history-canary',
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
          ...(kind === 'consistent'
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
      };
    };

    const consistent = projectLiveInvestigation(detail('consistent') as never);
    const beforeVerdict = projectLiveInvestigation(detail('before-verdict') as never);
    const missingCorrection = projectLiveInvestigation(detail('missing-correction') as never);

    expect(consistent?.verdictState).toBe('rejected');
    expect(consistent?.invalidProjection).not.toContain('appeals');
    for (const invalid of [beforeVerdict, missingCorrection]) {
      expect(invalid?.verdictState).toBe('pending');
      expect(invalid?.openAppealPubId).toBe('');
      expect(invalid?.invalidProjection).toContain('appeals');
      expect(JSON.stringify(invalid)).not.toMatch(/impossible-appeal-history-canary|Bearer/i);
    }
  });

  it('fails closed on appeal rows that contradict the resolution transaction', () => {
    const projection = projectLiveInvestigation({
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
          pub_id: 'apl_transaction_same_reviewer',
          state: 'rejected',
          submitted_by_pub_id: 'usr_transaction_submitter',
          reason: '新增独立来源申请重新复核。',
          resolution: 'rejected',
          resolved_by_pub_id: 'usr_transaction_submitter',
          resolution_rationale: 'Bearer same-reviewer-rationale-canary',
          created_at: '2026-07-25T02:00:00Z',
          updated_at: '2026-07-25T03:00:00Z',
          resolved_at: '2026-07-25T03:00:00Z',
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
    } as never);

    expect(projection?.verdictState).toBe('pending');
    expect(projection?.openAppealPubId).toBe('');
    expect(projection?.invalidProjection).toContain('appeals');
    expect(JSON.stringify(projection)).not.toMatch(/same-reviewer-rationale-canary|Bearer/i);
  });

  it('requires a corrected appeal to preserve the independent reviewer transaction', () => {
    const detail = (
      variant: 'consistent' | 'prior-self-review' | 'replacement-rationale',
    ): Record<string, unknown> => {
      const investigationPubId = `inv_appeal_independence_${variant.replaceAll('-', '_')}`;
      const resolverPubId = 'usr_appeal_resolution_reviewer';
      const resolutionRationale = '独立复核确认需要更正原裁决。';
      return {
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
            state: 'corrected',
            submitted_by_pub_id: 'usr_appeal_submitter',
            reason: '新增独立来源申请重新复核。',
            resolution: 'corrected',
            resolved_by_pub_id: resolverPubId,
            resolution_rationale: resolutionRationale,
            created_at: '2026-07-25T01:30:00Z',
            updated_at: '2026-07-25T02:30:00Z',
            resolved_at: '2026-07-25T02:30:00Z',
            token:
              variant === 'consistent' ? undefined : `Bearer appeal-independence-${variant}-canary`,
          },
        ],
        verdicts: [
          {
            pub_id: `vrd_${investigationPubId}_01`,
            verdict: 'likely',
            reviewer_pub_id:
              variant === 'prior-self-review' ? resolverPubId : 'usr_appeal_prior_reviewer',
            rationale: '原人工裁决理由。',
            supersedes_pub_id: null,
            created_at: '2026-07-25T01:00:00Z',
          },
          {
            pub_id: `vrd_${investigationPubId}_02`,
            verdict: 'unlikely',
            reviewer_pub_id: resolverPubId,
            rationale:
              variant === 'replacement-rationale' ? '与申诉解决理由不一致。' : resolutionRationale,
            supersedes_pub_id: `vrd_${investigationPubId}_01`,
            created_at: '2026-07-25T02:00:00Z',
          },
        ],
      };
    };

    const consistent = projectLiveInvestigation(detail('consistent') as never);
    expect(consistent?.verdictState).toBe('rejected');
    expect(consistent?.invalidProjection).not.toContain('appeals');
    for (const variant of ['prior-self-review', 'replacement-rationale'] as const) {
      const invalid = projectLiveInvestigation(detail(variant) as never);
      expect(invalid?.verdictState).toBe('pending');
      expect(invalid?.invalidProjection).toContain('appeals');
      expect(JSON.stringify(invalid)).not.toMatch(/appeal-independence-.*-canary|Bearer/i);
    }
  });

  it('bounds oversized investigation collections and marks governance data incomplete', () => {
    const projection = projectLiveInvestigation({
      pub_id: 'inv_projection_limit_safe',
      scores: [
        {
          pub_id: 'score_projection_limit_safe',
          probability: 0.7,
          evidence_sufficiency: 0.8,
          uncertainty: 0.2,
          rule_version: 'rule-safe',
          explanation: Array.from({ length: 50 }, (_, index) =>
            index === 2 ? 'Bearer projection-canary' : `规则解释 ${index}`,
          ),
          created_at: '2026-07-25T00:00:00Z',
        },
      ],
      claims: Array.from({ length: 250 }, (_, index) => ({
        pub_id: `clm_limit_${index}`,
        normalized_text: `Claim ${index}`,
        verifiability: 'verifiable',
      })),
      evidence_matrix: Array.from({ length: 550 }, (_, index) => ({
        pub_id: `ce_limit_${index}`,
        claim_pub_id: `clm_limit_${index % 200}`,
        evidence_pub_id: `evd_limit_${index}`,
        relation: 'supports',
        source_cluster: `cluster_${index % 20}`,
        independence_weight: 0.8,
        rationale: `安全理由 ${index}`,
      })),
      source_independence: Array.from({ length: 550 }, (_, index) => ({
        pub_id: `srca_limit_${index}`,
        source_pub_id: `evd_limit_${index}`,
        cluster_id: `cluster_${index % 20}`,
        independence_weight: 0.8,
        circular_citation_risk: 0.1,
      })),
      graph: Array.from({ length: 500 }, (_, index) => ({
        from_pub_id: `src_graph_${index}`,
        to_pub_id: `dst_graph_${index}`,
        relation: 'supports',
        weight: 0.8,
        evidence_pub_id: `evd_graph_${index}`,
      })),
      appeals: Array.from({ length: 201 }, (_, index) => {
        const timestamp = new Date(Date.UTC(2026, 6, 25, 1, 0, index)).toISOString();
        const active = index === 200;
        return {
          pub_id: `apl_limit_${index}`,
          state: active ? 'open' : 'rejected',
          submitted_by_pub_id: `usr_limit_submitter_${index}`,
          reason: `请求复核第 ${index} 条裁决。`,
          resolution: active ? null : 'rejected',
          resolved_by_pub_id: active ? null : `usr_limit_reviewer_${index}`,
          resolution_rationale: active ? null : `独立复核驳回第 ${index} 条申诉。`,
          created_at: timestamp,
          updated_at: timestamp,
          resolved_at: active ? null : timestamp,
        };
      }),
      verdicts: Array.from({ length: 201 }, (_, index) => ({
        pub_id: `vrd_limit_${index}`,
        verdict: 'likely',
        reviewer_pub_id: `usr_limit_verdict_reviewer_${index}`,
        rationale: `第 ${index} 条安全人工裁决理由。`,
        supersedes_pub_id: null,
        created_at: new Date(Date.UTC(2026, 6, 25, 2, 0, index)).toISOString(),
      })),
    } as never);

    expect(projection).not.toBeNull();
    expect(projection?.claims).toHaveLength(investigationProjectionLimits.claims);
    expect(projection?.evidenceMatrix).toHaveLength(investigationProjectionLimits.evidenceMatrix);
    expect(projection?.sourceIndependence).toHaveLength(
      investigationProjectionLimits.sourceIndependence,
    );
    expect(projection?.graph).toHaveLength(investigationProjectionLimits.graph);
    expect(projection?.projectionNotices.graph).toEqual({
      total: 500,
      shown: investigationProjectionLimits.graph,
    });
    expect(projection?.projectionNotices.appeals).toEqual({
      total: 201,
      shown: investigationProjectionLimits.appeals,
    });
    expect(projection?.projectionNotices.verdicts).toEqual({
      total: 201,
      shown: investigationProjectionLimits.verdicts,
    });
    expect(projection?.projectionNotices.explanations).toEqual({ total: 50, shown: 39 });
    expect(projection?.invalidProjection).toEqual(expect.arrayContaining(['explanations']));
    expect(projection?.verdictState).toBe('pending');
    expect(projection?.openAppealPubId).toBe('');
    expect(JSON.stringify(projection)).not.toContain('projection-canary');
  });
});
