// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  CustomerMetricTrace,
  RecommendationTopKGroup,
  type MetricV2ContributionPage,
  type MetricV2Definition,
  type MetricV2Summary,
} from './customer-metric-trace';

afterEach(cleanup);

const hash = 'a'.repeat(64);
const metric = (name = 'ai_recommendation_organic_top3_visibility_rate_v2'): MetricV2Summary => ({
  snapshot_pub_id: `msn_${name.slice(0, 20)}`,
  snapshot_hash: hash,
  metric_name: name,
  metric_version: '2.0.0',
  state: 'ready',
  state_reason_codes: ['coverage_ready'],
  value: 0.25,
  observed_value: 0.25,
  answer_weighted_value: 0.25,
  raw_numerator: 1,
  raw_denominator: 4,
  weighted_numerator: 0.25,
  weighted_denominator: 1,
  unique_query_count: 12,
  candidate_answer_count: 5,
  known_answer_count: 4,
  unknown_answer_count: 1,
  failed_answer_count: 0,
  not_applicable_answer_count: 0,
  excluded_answer_count: 0,
  design_cell_count: 4,
  coverage: {
    collection: 1,
    query_context: 1,
    semantic: 0.8,
    evidence: 1,
    semantic_by_capability: { rank_semantics: 0.8 },
  },
  adjudication_sensitivity: { lower: 0.24, upper: 0.26 },
  missing_bounds: { lower: 0.2, upper: 0.4 },
  decision_method_mix: { hybrid: 1 },
  contribution_set_hash: hash,
  query_contribution_set_hash: hash,
  design_contribution_set_hash: hash,
});

const definition: MetricV2Definition = {
  business_question: '中性推荐查询中，焦点品牌进入 Top3 的回答占多少？',
  denominator_description: '所有查询适用且列表结构语义已知的中性 AI 推荐有效回答。',
  outcome_source: 'hybrid',
  query_predicate: { exposure_is: 'brand_neutral' },
  outcome_expression: { event_numeric_compare: { field: 'rank', op: 'lte', value: 3 } },
  required_semantic_capabilities: ['rank_semantics'],
  decision_task_refs: [{ task_ref: 'rank-semantics@2.0.0' }],
};

const page: MetricV2ContributionPage = {
  snapshot_pub_id: metric().snapshot_pub_id,
  snapshot_candidate_count: 5,
  filtered_count: 2,
  next_cursor: null,
  has_more: false,
  data: [
    {
      answer_pub_id: 'ans_hit',
      query_key: 'qry_organic',
      query_text: '推荐几家安全公司',
      analysis_lenses: ['ai_recommendation'],
      requested_operations: ['recommend'],
      exposure_role: 'brand_neutral',
      model: 'DeepSeek',
      region: '华东',
      mode: 'normal',
      eligibility_status: 'included_hit',
      reason_codes: ['rank_within_k'],
      numerator_contribution: 1,
      denominator_contribution: 1,
      query_weight: 0.5,
      design_cell_weight: 1,
      repeat_weight: 1,
      final_weight: 0.5,
      weighted_numerator: 0.5,
      weighted_denominator: 0.5,
      supporting_events: [
        {
          event_pub_id: 'ase_hit',
          event_type: 'recommendation_list_rank',
          event_value: { rank: 3 },
          answer_excerpt: '推荐名单中，盛邦安全位列第三。',
          answer_text_start: 6,
          answer_text_end: 18,
        },
      ],
      supporting_decisions: [
        {
          decision_pub_id: 'sdr_rank',
          decision_hash: 'd'.repeat(64),
          task: 'rank-semantics',
          version: '2.0.0',
          method: 'hybrid',
          status: 'accepted',
          rationale_summary: '正文构成明确有序的推荐列表。',
          calibrated_confidence: 0.98,
          rubric_hash: hash,
          result: { rankable: true, target_rank: 3 },
          reason_codes: ['rank_within_k'],
          evidence_refs: [{ event_pub_id: 'ase_hit', relation: 'supports' }],
        },
      ],
      answer_excerpt: '推荐名单中，盛邦安全位列第三。',
      answer_detail_href:
        `/api/v2/customer-dashboard/projects/prj_test/answer-library/answers/ans_hit?` +
        `metric_snapshot_set_pub_id=mss_test&metric_snapshot_set_hash=${hash}`,
    },
    {
      answer_pub_id: 'ans_unknown',
      query_key: 'qry_unknown',
      query_text: '推荐几家安全公司',
      analysis_lenses: ['ai_recommendation'],
      requested_operations: ['recommend'],
      exposure_role: 'brand_neutral',
      model: '豆包',
      region: '华北',
      mode: 'normal',
      eligibility_status: 'analysis_unknown',
      reason_codes: ['judge_disagreement'],
      numerator_contribution: 0,
      denominator_contribution: 0,
      query_weight: 0.5,
      design_cell_weight: 1,
      repeat_weight: 1,
      final_weight: 0,
      weighted_numerator: 0,
      weighted_denominator: 0,
      supporting_events: [],
      supporting_decisions: [],
      answer_excerpt: null,
      answer_detail_href:
        `/api/v2/customer-dashboard/projects/prj_test/answer-library/answers/ans_unknown?` +
        `metric_snapshot_set_pub_id=mss_test&metric_snapshot_set_hash=${hash}`,
    },
  ],
};

describe('CustomerMetricTrace', () => {
  it('shows complete denominator states, immutable ids, decisions and evidence', async () => {
    const loadContributions = vi.fn(async () => page);
    render(
      <CustomerMetricTrace
        snapshotSetId="mss_test"
        snapshotSetHash={hash}
        metric={metric()}
        definition={definition}
        loadContributions={loadContributions}
        correctDecision={vi.fn()}
        onClose={() => undefined}
      />,
    );

    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '关闭计算明细' }));
    await waitFor(() =>
      expect(loadContributions).toHaveBeenCalledWith(metric().snapshot_pub_id, null),
    );
    expect(await screen.findByText('命中')).toBeTruthy();
    expect(screen.getAllByText('内容证据不足，当前无法判断').length).toBeGreaterThan(0);
    expect(screen.getAllByText('judge_disagreement').length).toBeGreaterThan(0);
    expect(screen.getByText('正文构成明确有序的推荐列表。')).toBeTruthy();
    expect(screen.getByText('推荐名单中，盛邦安全位列第三。')).toBeTruthy();
    expect(screen.getByText(/coverage_ready/u)).toBeTruthy();
    expect(screen.getByText(/design 1\.000000 × repeat 1\.000000 = final 0\.500000/u)).toBeTruthy();
    expect(screen.getByText(/展开 decision evidence 引用/u)).toBeTruthy();
    expect(screen.getByText('mss_test')).toBeTruthy();
    expect(screen.getByText(/筛选或分页不会改变快照合计/u)).toBeTruthy();
  });

  it('keeps the three Top3 denominator views together', async () => {
    const onInspect = vi.fn();
    render(
      <RecommendationTopKGroup
        visibility={metric('ai_recommendation_organic_top3_visibility_rate_v2')}
        rankable={metric('ai_recommendation_rankable_response_rate_v2')}
        conditional={metric('ai_recommendation_organic_top3_given_rankable_rate_v2')}
        onInspect={onInspect}
      />,
    );
    const group = screen.getByRole('region', { name: 'Top3 完整指标组' });
    expect(within(group).getByText('中性 AI 推荐 Top3 可见率（全部回答）')).toBeTruthy();
    expect(within(group).getByText('可排序回答覆盖率')).toBeTruthy();
    expect(within(group).getByText('Top3 率（仅可排序回答）')).toBeTruthy();
    await userEvent.click(within(group).getAllByRole('button', { name: '查看计算明细' })[0]!);
    expect(onInspect).toHaveBeenCalledTimes(1);
  });

  it('closes from Escape without making a new semantic decision', async () => {
    const onClose = vi.fn();
    const loadContributions = vi.fn(async () => page);
    render(
      <CustomerMetricTrace
        snapshotSetId="mss_test"
        snapshotSetHash={hash}
        metric={metric()}
        definition={definition}
        loadContributions={loadContributions}
        correctDecision={vi.fn()}
        onClose={onClose}
      />,
    );
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(loadContributions).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['llm_api_auth_missing', 'LLM API 未配置'],
    ['llm_api_rate_limited', 'LLM API 限流'],
    ['llm_api_timeout', 'LLM API 超时'],
    ['llm_api_budget_exhausted', 'LLM 调用预算不足'],
    ['llm_api_network_error', 'LLM API 不可用'],
    ['llm_api_adapter_error', 'LLM API 不可用'],
  ] as const)(
    'labels %s as infrastructure failure without requesting review',
    async (reason, copy) => {
      const outagePage: MetricV2ContributionPage = {
        ...page,
        data: page.data.map((row) =>
          row.answer_pub_id === 'ans_unknown'
            ? {
                ...row,
                eligibility_status: 'analysis_failed',
                reason_codes: ['decision_failed'],
                supporting_decisions: [
                  {
                    decision_pub_id: 'sdr_llm_outage',
                    decision_hash: 'c'.repeat(64),
                    task: 'rank-semantics',
                    version: '2.0.0',
                    method: 'model',
                    status: 'failed',
                    rationale_summary: null,
                    calibrated_confidence: null,
                    rubric_hash: hash,
                    result: {},
                    reason_codes: [reason],
                    evidence_refs: [],
                  },
                ],
              }
            : row,
        ),
      };
      render(
        <CustomerMetricTrace
          snapshotSetId="mss_test"
          snapshotSetHash={hash}
          metric={{ ...metric(), unknown_answer_count: 0, failed_answer_count: 1 }}
          definition={definition}
          loadContributions={async () => outagePage}
          correctDecision={vi.fn()}
          onClose={() => undefined}
        />,
      );

      expect((await screen.findAllByText(copy)).length).toBeGreaterThan(0);
      expect(screen.getByText(/内容证据不足 0 · 系统异常 1/u)).toBeTruthy();
      expect(screen.queryByText('内容证据不足，当前无法判断')).toBeNull();
      expect(screen.getAllByRole('button', { name: '纠错' })).toHaveLength(1);
    },
  );

  it('does not mislabel a non-LLM analysis failure as an API outage', async () => {
    const failedPage: MetricV2ContributionPage = {
      ...page,
      data: page.data.map((row) =>
        row.answer_pub_id === 'ans_unknown'
          ? {
              ...row,
              eligibility_status: 'analysis_failed',
              reason_codes: ['evidence_integrity_failed'],
              supporting_decisions: [],
            }
          : row,
      ),
    };
    render(
      <CustomerMetricTrace
        snapshotSetId="mss_test"
        snapshotSetHash={hash}
        metric={{ ...metric(), unknown_answer_count: 0, failed_answer_count: 1 }}
        definition={definition}
        loadContributions={async () => failedPage}
        correctDecision={vi.fn()}
        onClose={() => undefined}
      />,
    );

    expect((await screen.findAllByText('系统分析失败')).length).toBeGreaterThan(0);
    expect(screen.queryByText('LLM API 不可用')).toBeNull();
  });

  it('corrects only the explicitly opened decision and submits its CAS hash', async () => {
    const loadContributions = vi.fn(async () => page);
    const correctDecision = vi.fn(async () => ({
      kind: 'submitted' as const,
      recomputeJobPubId: 'mrj_customer_correction',
    }));
    render(
      <CustomerMetricTrace
        snapshotSetId="mss_test"
        snapshotSetHash={hash}
        metric={metric()}
        definition={definition}
        loadContributions={loadContributions}
        correctDecision={correctDecision}
        onClose={() => undefined}
      />,
    );

    const correctionButton = await screen.findByRole('button', { name: '纠错' });
    expect(screen.queryByRole('form', { name: '纠正 rank-semantics 判定' })).toBeNull();
    await userEvent.click(correctionButton);
    const form = screen.getByRole('form', { name: '纠正 rank-semantics 判定' });
    expect(within(form).getByText(/只修正这一条具体事实/u)).toBeTruthy();
    const resultInput = within(form).getByLabelText('修正后的结构化判定');
    expect((resultInput as HTMLTextAreaElement).value).toBe(
      JSON.stringify({ rankable: true, target_rank: 3 }, null, 2),
    );
    fireEvent.change(resultInput, {
      target: { value: JSON.stringify({ rankable: true, target_rank: 2 }, null, 2) },
    });
    await userEvent.type(within(form).getByLabelText('纠错理由'), '原文是并列第二，不是第三。');
    await userEvent.click(within(form).getByRole('button', { name: '提交纠错并重算' }));

    await waitFor(() =>
      expect(correctDecision).toHaveBeenCalledWith({
        decisionPubId: 'sdr_rank',
        expectedDecisionHash: 'd'.repeat(64),
        result: { rankable: true, target_rank: 2 },
        rationaleSummary: '原文是并列第二，不是第三。',
      }),
    );
    expect(within(form).getByRole('status').textContent).toContain(
      '纠错已提交，受影响指标正在自动重算。当前冻结快照不会被改写。',
    );
  });

  it('keeps invalid JSON local and explains a stale correction conflict', async () => {
    const correctDecision = vi.fn().mockResolvedValueOnce({ kind: 'conflict' as const });
    render(
      <CustomerMetricTrace
        snapshotSetId="mss_test"
        snapshotSetHash={hash}
        metric={metric()}
        definition={definition}
        loadContributions={async () => page}
        correctDecision={correctDecision}
        onClose={() => undefined}
      />,
    );
    await userEvent.click(await screen.findByRole('button', { name: '纠错' }));
    const form = screen.getByRole('form', { name: '纠正 rank-semantics 判定' });
    fireEvent.change(within(form).getByLabelText('修正后的结构化判定'), {
      target: { value: '{not-json}' },
    });
    await userEvent.type(within(form).getByLabelText('纠错理由'), '原判定不正确。');
    await userEvent.click(within(form).getByRole('button', { name: '提交纠错并重算' }));
    expect(within(form).getByRole('alert').textContent).toContain('必须是有效的 JSON 对象');
    expect(correctDecision).not.toHaveBeenCalled();

    fireEvent.change(within(form).getByLabelText('修正后的结构化判定'), {
      target: { value: JSON.stringify({ rankable: false, target_rank: null }) },
    });
    await userEvent.click(within(form).getByRole('button', { name: '提交纠错并重算' }));
    expect((await within(form).findByRole('alert')).textContent).toContain(
      '这条判定已被其他纠错更新',
    );
  });
});
