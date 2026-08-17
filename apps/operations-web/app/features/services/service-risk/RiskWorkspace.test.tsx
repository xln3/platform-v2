// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RiskWorkspace } from './RiskWorkspace';

const session = {
  tenantId: 'tnt_test',
  actorId: 'usr_test',
  role: 'operator' as const,
  headers: {
    'X-Tenant-Id': 'tnt_test',
    'X-Actor-Id': 'subject_test',
    'X-Actor-Role': 'operator',
  },
};

const project = {
  pub_id: 'prj_risk',
  name: '风险项目',
  state: 'active',
  updated_at: '2026-08-17T00:00:00Z',
};

function stubRiskApis(cases: unknown[] = []) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string | URL | Request) => {
      const url = new URL(
        typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
        'https://operations.example.test',
      );
      if (url.pathname.endsWith('/disparagement/rate')) {
        expect(url.searchParams.get('project_pub_id')).toBe('prj_risk');
        expect(url.searchParams.get('dimension')).toBe('target_brand');
        return new Response(
          JSON.stringify([
            {
              dimension: 'target_brand',
              value: '目标品牌',
              judgments: 20,
              disparagement_count: 3,
              disparagement_rate: 0.15,
              negative_count: 5,
              support_count: 8,
              experimental_count: 0,
              metric_version: 'disparagement-aggregation-v1',
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.pathname.endsWith('/disparagement/cases')) {
        expect(url.searchParams.get('project_pub_id')).toBe('prj_risk');
        return new Response(JSON.stringify(cases), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.pathname.endsWith('/analytics/answers')) {
        expect(url.searchParams.get('project_pub_id')).toBe('prj_risk');
        expect(url.searchParams.get('answer_pub_id')).toBe('ans_risk_case');
        expect(url.searchParams.get('limit')).toBe('1');
        return new Response(
          JSON.stringify({
            data: [
              {
                pub_id: 'ans_risk_case',
                project_pub_id: 'prj_risk',
                run_pub_id: 'run_risk',
                config_version_pub_id: 'cfg_risk',
                query_pub_id: 'qry_risk',
                query_text: '目标品牌是否值得选择？',
                response_text: '这是被判定为拉踩的 AI 回答全文。',
                model: 'doubao',
                region: 'CN',
                mode: 'normal',
                eligible: true,
                degraded: false,
                capture_time: '2026-08-17T01:02:03Z',
                mentioned: true,
                rank: 1,
                sentiment: 'negative',
                recommendation_state: null,
                citation_count: 0,
              },
            ],
            page: { next_cursor: null, has_more: false },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (
        url.pathname.endsWith('/analytics/answers/ans_risk_case/relations') ||
        url.pathname.endsWith('/collection/tasks/ans_risk_case/trace')
      ) {
        return new Response(JSON.stringify({ detail: { code: 'task_not_found' } }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      throw new Error(`unexpected request: ${url.pathname}`);
    }),
  );
}

describe('RiskWorkspace', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('starts with target-brand statistics instead of sampling records', async () => {
    stubRiskApis();
    render(<RiskWorkspace session={session} project={project} />);

    await screen.findByRole('heading', { name: '目标品牌风险概览' });
    expect(screen.queryByRole('heading', { name: '采样记录' })).toBeNull();
    expect(screen.getByText('20')).toBeTruthy();
    expect(screen.getByText('15.0%')).toBeTruthy();
    expect(screen.getByText('3/20')).toBeTruthy();
    expect(screen.getByText(/竞品自身风险不进入本页统计/)).toBeTruthy();
  });

  it('provides an accessible question-mark formula for every risk metric', async () => {
    stubRiskApis();
    render(<RiskWorkspace session={session} project={project} />);
    await screen.findByText('15.0%');

    for (const label of ['判定数', '拉踩次数', '拉踩率', '负面', '支持']) {
      expect(screen.getByRole('button', { name: `${label}计算方式` }).textContent).toBe('?');
    }
    expect(screen.getByText(/拉踩次数 ÷ 判定数 × 100%/)).toBeTruthy();
    expect(screen.getByText(/单纯负面批评未必构成拉踩/)).toBeTruthy();
  });

  it('opens answer provenance in the answer detail and keeps document provenance as a link', async () => {
    stubRiskApis([
      {
        judgment_pub_id: 'jdg_answer',
        subject_type: 'answer',
        subject_pub_id: 'ans_risk_case',
        platform: 'doubao',
        subject_brand: '竞品甲',
        target_brand: '目标品牌',
        attitude: 'negative',
        evidence_quote: '回答中的拉踩表述',
        confidence: 0.95,
        method: 'llm',
        model: 'audit-model',
        prompt_version: 'disparage-v2',
        source_url: null,
        created_at: '2026-08-17T01:02:03Z',
        content_origin: 'collection',
        fact_check: null,
      },
      {
        judgment_pub_id: 'jdg_document',
        subject_type: 'source_document',
        subject_pub_id: 'doc_risk_case',
        platform: 'example.com',
        subject_brand: '竞品乙',
        target_brand: '目标品牌',
        attitude: 'negative',
        evidence_quote: '公开信源中的拉踩表述',
        confidence: 0.9,
        method: 'llm',
        model: 'audit-model',
        prompt_version: 'disparage-v2',
        source_url: 'https://example.com/risk-source',
        created_at: '2026-08-17T01:02:03Z',
        content_origin: 'collection',
        fact_check: null,
      },
    ]);
    render(<RiskWorkspace session={session} project={project} />);

    const sourceLink = await screen.findByRole('link', { name: '查看原文' });
    expect(sourceLink.getAttribute('href')).toBe('https://example.com/risk-source');

    fireEvent.click(screen.getByRole('button', { name: '查看回答' }));
    expect(await screen.findByRole('dialog', { name: '问答详情' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '目标品牌是否值得选择？' })).toBeTruthy();
    expect(screen.getByText('这是被判定为拉踩的 AI 回答全文。')).toBeTruthy();
  });
});
