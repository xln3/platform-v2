// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SiteAuditWorkspace } from './SiteAuditWorkspace';

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
  pub_id: 'prj_audit',
  name: '审计项目',
  state: 'active',
  updated_at: '2026-08-09T00:00:00Z',
};

const emptySuggestions = {
  batch_pub_id: null,
  generated_at: null,
  model: null,
  suggestions: [],
};

function stubApis(reportPayload: unknown, suggestionsPayload: unknown = emptySuggestions) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string | URL | Request) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      expect(url).toContain('project');
      const payload = url.includes('site-suggestions') ? suggestionsPayload : reportPayload;
      if (!url.includes('site-suggestions')) {
        expect(url).toContain('/api/v2/analytics/source-audit');
        expect(url).toContain('project_pub_id=prj_audit');
      }
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

function stubSourceAudit(payload: unknown, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string | URL | Request) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.includes('site-suggestions')) {
        return new Response(JSON.stringify(emptySuggestions), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      expect(url).toContain('/api/v2/analytics/source-audit');
      expect(url).toContain('project_pub_id=prj_audit');
      return new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

const fullReport = {
  project_pub_id: 'prj_audit',
  start: '2026-07-11',
  end: '2026-08-09',
  own_site_host: 'www.example.com',
  answers_total: 20,
  answers_with_citation: 12,
  citation_coverage_rate: 0.6,
  answers_with_own_site_citation: 5,
  own_site_answer_citation_rate: 0.25,
  own_site_share_of_cited_answers: 5 / 12,
  citation_references_total: 40,
  own_site_citation_references: 6,
  own_site_reference_share: 0.15,
  own_site_cited_text_answers: 4,
  own_site_cited_text_evidence_rate: 0.8,
  documents_total: 4,
  own_site_documents: 1,
  own_site_share: 0.25,
  // 报价单口径：只统计 own_site 文档的 transcript 判定（1/1），不混算第三方 host。
  own_site_transcript_total: 1,
  own_site_transcript_accurate: 1,
  own_site_transcript_accuracy_rate: 1.0,
  own_site_adoption_evaluated_answers: 1,
  own_site_adoption_verified_answers: 1,
  own_site_adoption_rate: 1.0,
  verdicts: {
    transcript: { accurate: 3, inaccurate: 1, unsupported: 0, unverifiable: 0 },
    factual: { accurate: 2, inaccurate: 1, unsupported: 1, unverifiable: 0 },
  },
  answer_hosts: [
    { host: 'www.example.com', is_own_site: true, answers: 5, references: 6 },
    { host: 'news.thirdparty.com', is_own_site: false, answers: 8, references: 10 },
  ],
  hosts: [
    {
      host: 'www.example.com',
      is_own_site: true,
      documents: 1,
      transcript_total: 1,
      transcript_accurate: 1,
    },
    {
      host: 'news.thirdparty.com',
      is_own_site: false,
      documents: 3,
      transcript_total: 3,
      transcript_accurate: 2,
    },
  ],
  items: [
    {
      pub_id: 'doc_1',
      url: 'https://www.example.com/products',
      host: 'www.example.com',
      final_url: 'https://www.example.com/products',
      http_status: 200,
      extract_status: 'ok',
      fetched_at: '2026-08-08T10:00:00Z',
      is_own_site: true,
      audits: [
        {
          dimension: 'transcript',
          verdict: 'accurate',
          audit_status: 'done',
          rationale: '转述与原文一致',
        },
      ],
    },
  ],
};

describe('SiteAuditWorkspace', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('renders metric cards, verdict distribution, hosts and item details', async () => {
    stubSourceAudit(fullReport);
    render(<SiteAuditWorkspace session={session} project={project} />);
    await screen.findAllByText('25.0%');
    expect(screen.getByText('官网引用率')).toBeTruthy();
    expect(screen.getByText('引用官网的 AI 回答 5/20')).toBeTruthy();
    expect(screen.getByText('官网内容采纳率')).toBeTruthy();
    expect(screen.getAllByText('100.0%')).toHaveLength(2);
    expect(screen.getByText(/已验证采纳 1/)).toBeTruthy();
    expect(screen.getByText('转述准确 1/1')).toBeTruthy();
    expect(screen.getByText('www.example.com（官网）')).toBeTruthy();
    expect(screen.getByText('news.thirdparty.com')).toBeTruthy();
    expect(screen.getByText('AI 回答引用的网站来源')).toBeTruthy();
    expect(screen.getByText(/这里展示回答引用事实/)).toBeTruthy();
    expect(screen.getByText('转述:准确')).toBeTruthy();
    expect(screen.getByText('转述与原文一致')).toBeTruthy();
  });

  it('shows 数据不足 when the own-site adoption rate is null', async () => {
    stubSourceAudit({
      ...fullReport,
      own_site_transcript_total: 0,
      own_site_transcript_accurate: 0,
      own_site_transcript_accuracy_rate: null,
      own_site_adoption_evaluated_answers: 0,
      own_site_adoption_verified_answers: 0,
      own_site_adoption_rate: null,
    });
    render(<SiteAuditWorkspace session={session} project={project} />);
    expect(await screen.findAllByText('数据不足')).toHaveLength(2);
    expect(screen.getByText(/已验证采纳 0/)).toBeTruthy();
  });

  it('renders site suggestions with labels, severity badges and evidence link', async () => {
    stubApis(fullReport, {
      batch_pub_id: 'sas_batch1',
      generated_at: '2026-08-09T12:00:00Z',
      model: 'gpt-5.6-luna',
      suggestions: [
        {
          category: 'citability',
          severity: 'high',
          title: '产品页缺少可引用的规格表',
          detail: '为重疾险产品页补充结构化规格表，便于 AI 引用。',
          evidence_document_pub_id: 'doc_1',
        },
        {
          category: 'crawlability',
          severity: 'low',
          title: '官网未提交 sitemap',
          detail: '提交 sitemap 提升抓取覆盖率。',
          evidence_document_pub_id: null,
        },
      ],
    });
    render(<SiteAuditWorkspace session={session} project={project} />);
    await screen.findByText('官网内容问题与优化建议');
    await screen.findByText('产品页缺少可引用的规格表');
    expect(screen.getByText('可引用性')).toBeTruthy();
    expect(screen.getByText('可抓取性')).toBeTruthy();
    expect(screen.getByText('高')).toBeTruthy();
    expect(screen.getByText('低')).toBeTruthy();
    const evidence = screen.getByText('证据文档');
    expect(evidence.getAttribute('href')).toBe('https://www.example.com/products');
    // 无证据 pub_id 的建议行显示占位符。
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
    expect(screen.getByText(/批次 sas_batch1/)).toBeTruthy();
  });

  it('shows the suggestions empty state when no batch exists', async () => {
    stubApis(fullReport, emptySuggestions);
    render(<SiteAuditWorkspace session={session} project={project} />);
    await screen.findByText('尚无官网优化建议——信源审计分析后自动生成。');
  });

  it('shows the empty state when no audit data exists yet', async () => {
    stubSourceAudit({
      project_pub_id: 'prj_audit',
      start: '2026-07-11',
      end: '2026-08-09',
      own_site_host: null,
      documents_total: 0,
      own_site_documents: 0,
      own_site_share: null,
      own_site_transcript_total: 0,
      own_site_transcript_accurate: 0,
      own_site_adoption_rate: null,
      verdicts: {
        transcript: { accurate: 0, inaccurate: 0, unsupported: 0, unverifiable: 0 },
        factual: { accurate: 0, inaccurate: 0, unsupported: 0, unverifiable: 0 },
      },
      answer_hosts: [],
      hosts: [],
      items: [],
    });
    render(<SiteAuditWorkspace session={session} project={project} />);
    await screen.findByText('尚无信源审计数据——采集 run 完成后自动生成。');
  });

  it('shows an inline failure when the endpoint is unavailable', async () => {
    stubSourceAudit({ error: { code: 'http_404' } }, 404);
    render(<SiteAuditWorkspace session={session} project={project} />);
    await screen.findByText(/信源审计数据暂不可用/);
  });
});
