// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BrandVisibilityRow, Project } from '../api';
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
  const collectionRunUrls: string[] = [];
  let brandRows: BrandVisibilityRow[];

  beforeEach(() => {
    brandVisibilityUrls.length = 0;
    collectionRunUrls.length = 0;
    brandRows = [
      {
        rank: 1,
        brand: '新大陆',
        score: 1,
        avg_rank: 1,
        occurrences: 1,
        appearance_rate: 10,
        industry_fit: 'scenario_specific_adjacent',
        eligibility_note: '仅在数字身份/网证场景作为竞品。',
      },
    ];
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
              result: {
                overall: {
                  merged: brandRows,
                },
                entity_resolution: {
                  mode: 'governed_hybrid_v2',
                  master: {
                    revision: 'cybersecurity-20260826.3',
                    aggregation_level: 'brand_family',
                  },
                  counts: {
                    alias_collapses_within_answers: 3,
                    unclassified_distinct_names: 2,
                  },
                },
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (url.includes('/collection/runs/summary')) {
          return new Response(
            JSON.stringify({
              project_pub_id: 'prj_test',
              run_count: 3,
              active_run_count: 0,
              total_tasks: 30,
              completed_tasks: 30,
              failed_tasks: 0,
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (url.includes('/collection/runs?')) {
          collectionRunUrls.push(url);
          return new Response(JSON.stringify([run(1), run(2)]), {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
              'X-Page': '1',
              'X-Page-Size': '2',
              'X-Total-Count': '3',
              'X-Page-Count': '2',
              'X-Has-More': 'true',
            },
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
              page: { page: 1, page_size: 4, total_count: 0, total_pages: 0 },
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
    await screen.findByText('新大陆');
    await screen.findByText('场景型相关');
    await screen.findByText(/同一答案内消除 3 次重复别名/);
    await screen.findByText(/另有 2 个名称待语义复核/);
    expect(screen.getByRole('heading', { name: '采样进度' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '采样记录' })).toBeTruthy();
    await waitFor(() => expect(collectionRunUrls).toHaveLength(1));
    expect(new URL(collectionRunUrls[0]!).searchParams.get('page_size')).toBe('2');
    expect(document.querySelectorAll('.runs-panel tbody tr')).toHaveLength(2);

    expect(brandVisibilityUrls).toHaveLength(1);
    const url = new URL(brandVisibilityUrls[0]!);
    expect(url.searchParams.has('industry')).toBe(false);
    expect(url.searchParams.get('window_days')).toBe('30');
  });

  it('shows ten brands by default and lets the operator change the page size', async () => {
    brandRows = Array.from({ length: 21 }, (_, index) => ({
      rank: index + 1,
      brand: `品牌${index + 1}`,
      score: 100 - index,
      avg_rank: index + 1,
      occurrences: 21 - index,
      appearance_rate: 50 - index,
    }));

    render(<VisibilityWorkspace session={session} project={projectWith('cybersecurity')} />);
    const pageSizeSelect = (await screen.findByRole('combobox', {
      name: '品牌可见度榜单每页显示数量',
    })) as HTMLSelectElement;
    expect(pageSizeSelect.value).toBe('10');

    const table = screen.getByRole('table', { name: '品牌可见度榜单' });
    expect(within(table).getAllByRole('row')).toHaveLength(11);
    expect(within(table).getByText('品牌10', { exact: true })).toBeTruthy();
    expect(within(table).queryByText('品牌11', { exact: true })).toBeNull();

    const pagination = screen.getByRole('navigation', { name: '品牌可见度榜单分页' });
    expect(within(pagination).getByText('第 1 / 3 页')).toBeTruthy();
    fireEvent.click(within(pagination).getByRole('button', { name: '下一页' }));
    expect(await within(table).findByText('品牌11', { exact: true })).toBeTruthy();
    expect(within(table).queryByText('品牌1', { exact: true })).toBeNull();

    fireEvent.change(pageSizeSelect, { target: { value: '20' } });
    await waitFor(() => expect(within(pagination).getByText('第 1 / 2 页')).toBeTruthy());
    expect(within(table).getAllByRole('row')).toHaveLength(21);
    expect(within(table).getByText('品牌1', { exact: true })).toBeTruthy();
    expect(within(table).queryByText('品牌21', { exact: true })).toBeNull();
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
              page: { page: 1, page_size: 4, total_count: 0, total_pages: 0 },
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

function run(index: number) {
  return {
    pub_id: `run_${index}`,
    project_pub_id: 'prj_test',
    config_version_pub_id: 'cfv_test',
    workflow_id: `geo-collection/${index}`,
    temporal_run_id: null,
    state: 'completed',
    total_tasks: 10,
    completed_tasks: 10,
    failed_tasks: 0,
    paused: false,
    error_code: null,
    source: 'manual',
    schedule_pub_id: null,
    retry_of_run_pub_id: null,
    initiated_by_pub_id: 'usr_test',
    created_at: `2026-08-${String(24 - index).padStart(2, '0')}T00:00:00Z`,
    updated_at: `2026-08-${String(24 - index).padStart(2, '0')}T01:00:00Z`,
  };
}
