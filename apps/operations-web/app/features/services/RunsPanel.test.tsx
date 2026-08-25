// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RunsPanel } from './RunsPanel';

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

function run(index: number, project = 'prj_alpha') {
  return {
    pub_id: `run_${index}`,
    project_pub_id: project,
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('RunsPanel', () => {
  it.each([0, 1, 4])('renders %i records without adding fake rows', async (count) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        if (url.pathname.endsWith('/summary')) return summary(count);
        return json(
          Array.from({ length: count }, (_, index) => run(index + 1)),
          pageHeaders(1, count),
        );
      }),
    );

    render(<RunsPanel session={session} projectPubId="prj_alpha" readOnly />);
    if (count === 0) {
      expect(await screen.findByText(/尚无采集 run/)).toBeTruthy();
    } else {
      const table = await screen.findByRole('table');
      expect(within(table).getAllByRole('row')).toHaveLength(count + 1);
    }
  });

  it('reaches the fifth and ninth records through direct numbered-page requests', async () => {
    const requested: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        if (url.pathname.endsWith('/summary')) return summary(9);
        expect(url.searchParams.get('project_pub_id')).toBe('prj_alpha');
        expect(url.searchParams.get('page_size')).toBe('4');
        const page = url.searchParams.get('page') ?? '1';
        requested.push(page);
        if (page === '1') {
          return json([run(1), run(2), run(3), run(4)], pageHeaders(1, 9));
        }
        if (page === '2') {
          return json([run(5), run(6), run(7), run(8)], pageHeaders(2, 9));
        }
        return json([run(9)], pageHeaders(3, 9));
      }),
    );

    render(<RunsPanel session={session} projectPubId="prj_alpha" readOnly />);
    await screen.findByText('run_4');
    expect(screen.queryByText('run_5')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('run_5')).toBeTruthy();
    expect(screen.queryByText('run_1')).toBeNull();
    fireEvent.change(screen.getByRole('spinbutton', { name: '跳转页码' }), {
      target: { value: '3' },
    });
    fireEvent.click(screen.getByRole('button', { name: '跳转' }));
    expect(await screen.findByText('run_9')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '上一页' }));
    expect(await screen.findByText('run_5')).toBeTruthy();
    expect(requested).toEqual(['1', '2', '3', '2']);
  });

  it('resets the cursor for a project change and opens answers in a dialog', async () => {
    const runRequests: URL[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        if (url.pathname.endsWith('/summary')) return summary(5);
        if (url.pathname.endsWith('/analytics/answers')) {
          expect(url.searchParams.get('run_pub_id')).toBe('run_1');
          return json({ data: [], page: { next_cursor: null, has_more: false } });
        }
        runRequests.push(url);
        return json([run(1, url.searchParams.get('project_pub_id') ?? '')], pageHeaders(1, 5));
      }),
    );

    const view = render(<RunsPanel session={session} projectPubId="prj_alpha" readOnly />);
    fireEvent.click(await screen.findByRole('button', { name: '问答' }));
    expect(await screen.findByRole('dialog', { name: /运行 run_1 的问答/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '关闭运行问答' }));

    view.rerender(<RunsPanel session={session} projectPubId="prj_beta" readOnly />);
    await waitFor(() =>
      expect(
        runRequests.some(
          (url) =>
            url.searchParams.get('project_pub_id') === 'prj_beta' &&
            url.searchParams.get('page') === '1',
        ),
      ).toBe(true),
    );
  });

  it('ignores a delayed old-project summary after the new project succeeds', async () => {
    let resolveAlpha!: (response: Response) => void;
    const alphaSummary = new Promise<Response>((resolve) => {
      resolveAlpha = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const project = url.searchParams.get('project_pub_id') ?? '';
        if (url.pathname.endsWith('/summary')) {
          return project === 'prj_alpha' ? alphaSummary : summary(2, 'prj_beta');
        }
        return json([run(1, project)], pageHeaders(1, 1));
      }),
    );

    const view = render(<RunsPanel session={session} projectPubId="prj_alpha" readOnly />);
    await screen.findByText('run_1');
    view.rerender(<RunsPanel session={session} projectPubId="prj_beta" readOnly />);
    expect(await screen.findByText(/项目共 2 个 run/)).toBeTruthy();
    await act(async () => resolveAlpha(summary(9, 'prj_alpha')));
    expect(screen.getByText(/项目共 2 个 run/)).toBeTruthy();
    expect(screen.queryByText(/项目共 9 个 run/)).toBeNull();
  });

  it('does not let an old-project failure clear the new summary', async () => {
    let rejectAlpha!: (cause: Error) => void;
    const alphaSummary = new Promise<Response>((_resolve, reject) => {
      rejectAlpha = reject;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const project = url.searchParams.get('project_pub_id') ?? '';
        if (url.pathname.endsWith('/summary')) {
          return project === 'prj_alpha' ? alphaSummary : summary(3, 'prj_beta');
        }
        return json([run(1, project)], pageHeaders(1, 1));
      }),
    );

    const view = render(<RunsPanel session={session} projectPubId="prj_alpha" readOnly />);
    await screen.findByText('run_1');
    view.rerender(<RunsPanel session={session} projectPubId="prj_beta" readOnly />);
    expect(await screen.findByText(/项目共 3 个 run/)).toBeTruthy();
    await act(async () => rejectAlpha(new Error('delayed_alpha_failure')));
    expect(screen.getByText(/项目共 3 个 run/)).toBeTruthy();
  });
});

function json(value: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

function summary(count: number, projectPubId = 'prj_alpha') {
  return json({
    project_pub_id: projectPubId,
    run_count: count,
    active_run_count: 0,
    total_tasks: count * 10,
    completed_tasks: count * 10,
    failed_tasks: 0,
  });
}

function pageHeaders(page: number, totalCount: number, pageSize = 4) {
  return {
    'X-Page': String(page),
    'X-Page-Size': String(pageSize),
    'X-Total-Count': String(totalCount),
    'X-Page-Count': String(Math.ceil(totalCount / pageSize)),
    'X-Has-More': page < Math.ceil(totalCount / pageSize) ? 'true' : 'false',
  };
}
