// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Project } from '../api';
import { VisibilityWorkspace } from './VisibilityWorkspace';

vi.mock('@geo/api-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@geo/api-client')>();
  return {
    ...actual,
    getAnalyticsOverview: vi.fn(async () => ({ kind: 'ready', data: { data: [] } })),
    getAnalyticsBreakdown: vi.fn(async () => ({ kind: 'ready', data: { data: [] } })),
    getAnalyticsCompetitors: vi.fn(async () => ({ kind: 'ready', data: { data: [] } })),
  };
});

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

function projectWith(domain: string | null): Project {
  return {
    pub_id: 'prj_test',
    name: '测试项目',
    state: 'active',
    updated_at: '2026-08-10T00:00:00Z',
    brandrank_domain: domain,
  };
}

describe('VisibilityWorkspace', () => {
  const brandVisibilityUrls: string[] = [];

  beforeEach(() => {
    brandVisibilityUrls.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes('/brand-visibility')) {
          brandVisibilityUrls.push(url);
          return new Response(
            JSON.stringify({
              project_pub_id: 'prj_test',
              window_days: 30,
              domain: 'cybersecurity',
              result: { overall: { merged: [] } },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (url.includes('/analytics/sampling-progress')) {
          return new Response(
            JSON.stringify({
              project_pub_id: 'prj_test',
              config_revision_start: null,
              config_revision_end: null,
              columns: [],
              rows: [],
              observed_cells: 0,
              total_cells: 0,
              answer_count: 0,
              latest_capture_time: null,
              live_runs: 0,
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        // RunsPanel 的 run 列表：空列表即可。
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('requests brand visibility without an industry param and shows the project rule pack domain', async () => {
    render(<VisibilityWorkspace session={session} project={projectWith('cybersecurity')} />);
    await screen.findByText(/品牌可见度榜单（近 30 天 · 规则包：cybersecurity）/);
    await screen.findByText(/该时间窗内可用于榜单的真实答案不足。/);
    expect(screen.getByRole('heading', { name: '采样进度' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '采样记录' })).toBeTruthy();

    expect(brandVisibilityUrls).toHaveLength(1);
    const url = new URL(brandVisibilityUrls[0]!);
    expect(url.searchParams.has('industry')).toBe(false);
    expect(url.searchParams.get('window_days')).toBe('30');
  });

  it('shows the brandrank_domain_unresolved guidance when the project has no rule pack domain', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes('/brand-visibility')) {
          return new Response(JSON.stringify({ error: { code: 'brandrank_domain_unresolved' } }), {
            status: 400,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        if (url.includes('/analytics/sampling-progress')) {
          return new Response(
            JSON.stringify({
              project_pub_id: 'prj_test',
              config_revision_start: null,
              config_revision_end: null,
              columns: [],
              rows: [],
              observed_cells: 0,
              total_cells: 0,
              answer_count: 0,
              latest_capture_time: null,
              live_runs: 0,
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
    render(<VisibilityWorkspace session={session} project={projectWith(null)} />);
    await screen.findByText(/项目未设置品牌规则包域，请先在项目设置中配置 brandrank_domain/);
    expect(screen.getByText(/规则包信息加载中…/)).toBeTruthy();
  });
});
