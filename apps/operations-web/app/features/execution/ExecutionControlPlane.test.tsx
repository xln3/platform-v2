// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ExecutionControlPlane } from './ExecutionControlPlane';

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

describe('ExecutionControlPlane', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        let data: unknown = [];
        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (url.pathname === '/api/v2/projects') {
          data = {
            data: [
              {
                pub_id: 'prj_test',
                name: 'Catalog Project',
                state: 'active',
                updated_at: '2026-07-24T08:00:00Z',
              },
            ],
            page: { next_cursor: null, has_more: false },
          };
        } else if (url.pathname === '/api/v2/platform-accounts') {
          data = [
            {
              pub_id: 'pac_test',
              platform: 'fixed',
              account_mask: 'fixture-***09',
              owner_pub_id: 'own_test',
              purpose: 'measure',
              responsible_pub_id: 'usr_test',
              custody_mode: 'server',
              region: 'CN-BJ',
              state: 'active',
              admission_level: 'adapter_ready',
              last_passed_at: null,
              scopes: ['read', 'query'],
              authorization_expires_at: '2026-07-25T08:00:00Z',
              profile_state: 'ACTIVE',
              profile_version: 2,
              profile_constraints: ['READ_ONLY'],
              lease_expires_at: null,
            },
          ];
          headers['X-Total-Count'] = '1';
          headers['X-Active-Count'] = '1';
        } else if (url.pathname === '/api/v2/collection/runs/cursor') {
          data = [
            {
              pub_id: 'run_test',
              project_pub_id: 'prj_test',
              config_version_pub_id: 'cfv_test',
              workflow_id: 'geo-collection/test',
              state: 'running',
              total_tasks: 4,
              completed_tasks: 2,
              failed_tasks: 0,
              paused: false,
              error_code: null,
              created_at: '2026-07-24T07:00:00Z',
              updated_at: '2026-07-24T08:00:00Z',
            },
          ];
        } else if (url.pathname === '/api/v2/collection/runs/summary') {
          data = {
            project_pub_id: url.searchParams.get('project_pub_id'),
            run_count: 1,
            active_run_count: 1,
            total_tasks: 4,
            completed_tasks: 2,
            failed_tasks: 0,
          };
        } else if (url.pathname === '/api/v2/projects/prj_test/config/current') {
          data = { effective: null, next_pending: null };
        }
        return new Response(JSON.stringify(data), {
          status: 200,
          headers,
        });
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('renders real lifecycle projections and never renders secret fields', async () => {
    render(<ExecutionControlPlane session={session} />);
    await screen.findByText('fixture-***09');
    expect(screen.getAllByText('2/4')).toHaveLength(2);
    expect(screen.getByText('adapter_ready')).toBeTruthy();
    const rendered = document.body.textContent?.toLowerCase() ?? '';
    expect(rendered).not.toContain('sid=secret');
    expect(rendered).not.toContain('/tmp/profile');
    expect(rendered).toContain('不会显示 cookie');
  });

  it('shows reconnect state after a network failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new Error('offline'))),
    );
    render(<ExecutionControlPlane session={session} />);
    await waitFor(() => expect(screen.getByText(/连接中断/)).toBeTruthy());
  });

  it('creates a scoped customer-terminal bundle without offering direct completion', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        let data: unknown = [];
        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (url.pathname === '/api/v2/projects') {
          data = { data: [], page: { next_cursor: null, has_more: false } };
        }
        if (url.pathname === '/api/v2/platform-accounts') {
          data = [
            {
              pub_id: 'pac_customer',
              platform: 'fixed',
              account_mask: 'customer-***01',
              owner_pub_id: 'own_test',
              purpose: 'measure',
              responsible_pub_id: 'usr_test',
              custody_mode: 'customer_device',
              region: 'CN-BJ',
              state: 'active',
              admission_level: 'adapter_ready',
              last_passed_at: null,
              scopes: ['read'],
              authorization_expires_at: '2026-07-25T08:00:00Z',
              profile_state: null,
              profile_version: null,
              profile_constraints: [],
              lease_expires_at: null,
            },
          ];
          headers['X-Total-Count'] = '1';
          headers['X-Active-Count'] = '1';
        }
        if (url.pathname === '/api/v2/interventions') {
          data = [
            {
              pub_id: 'int_customer',
              account_pub_id: 'pac_customer',
              account_mask: 'customer-***01',
              challenge_type: 'passkey',
              allowed_domain: 'example.com',
              action: 'read',
              state: 'pending',
              pairing_expires_at: null,
              platform_result: null,
            },
          ];
          headers['X-Total-Count'] = '1';
          headers['X-Open-Count'] = '1';
        }
        if (url.pathname === '/api/v2/collection/runs/summary') {
          data = {
            project_pub_id: null,
            run_count: 0,
            active_run_count: 0,
            total_tasks: 0,
            completed_tasks: 0,
            failed_tasks: 0,
          };
        }
        if (url.pathname === '/api/v2/interventions/int_customer/pair') {
          data = {
            intervention_pub_id: 'int_customer',
            pairing_token: 'one-time-token',
            server_public_key_sha256: 'a'.repeat(64),
            allowed_domain: 'example.com',
            action: 'read',
            challenge_type: 'passkey',
            expires_at: '2026-07-25T08:10:00Z',
          };
        }
        return new Response(JSON.stringify(data), {
          status: 200,
          headers,
        });
      }),
    );
    render(<ExecutionControlPlane session={session} />);
    expect(await screen.findAllByText('customer-***01')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: '安全配对' }));
    const bundle = await screen.findByLabelText('客户终端一次性配对包');
    expect((bundle as HTMLTextAreaElement).value).toContain('"allowed_domain":"example.com"');
    expect((bundle as HTMLTextAreaElement).value).toContain(
      `"server_public_key_sha256":"${'a'.repeat(64)}"`,
    );
    expect(screen.queryByRole('button', { name: '平台已确认，恢复' })).toBeNull();
  });

  it('keeps seven four-row cursors independent, uses full summaries, and targets the selected run', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const request = input instanceof Request ? input : null;
      const url = new URL(request?.url ?? String(input));
      const method = request?.method ?? init?.method ?? 'GET';
      const responseHeaders: Record<string, string> = { 'Content-Type': 'application/json' };

      if (
        method === 'POST' &&
        /^\/api\/v2\/collection\/runs\/run_\d+\/cancel$/.test(url.pathname)
      ) {
        return new Response(JSON.stringify({ workflow_id: `cancel:${url.pathname}` }), {
          status: 200,
          headers: responseHeaders,
        });
      }
      if (url.pathname === '/api/v2/collection/runs/summary') {
        return jsonResponse(
          {
            project_pub_id: url.searchParams.get('project_pub_id'),
            run_count: 9,
            active_run_count: 7,
            total_tasks: 90,
            completed_tasks: 70,
            failed_tasks: 3,
          },
          responseHeaders,
        );
      }
      if (url.pathname === '/api/v2/operations/platform-sla') {
        return jsonResponse([], responseHeaders);
      }
      if (/^\/api\/v2\/projects\/prj_\d+\/config\/current$/.test(url.pathname)) {
        return jsonResponse({ effective: null, next_pending: null }, responseHeaders);
      }

      const kind = operationalKind(url.pathname);
      if (kind !== null) {
        expect(url.searchParams.get('limit')).toBe('4');
        const offset = cursorOffset(url.searchParams.get('cursor'));
        const data = Array.from({ length: Math.min(4, 9 - offset) }, (_, index) =>
          operationalItem(kind, offset + index + 1),
        );
        const hasMore = offset + data.length < 9;
        const nextCursor = hasMore ? `opaque-${kind}-${offset + data.length}` : null;
        if (kind === 'projects') {
          return jsonResponse(
            { data, page: { next_cursor: nextCursor, has_more: hasMore } },
            responseHeaders,
          );
        }
        responseHeaders['X-Has-More'] = String(hasMore);
        responseHeaders['X-Total-Count'] = '9';
        if (nextCursor) responseHeaders['X-Next-Cursor'] = nextCursor;
        if (kind === 'accounts') responseHeaders['X-Active-Count'] = '7';
        if (kind === 'interventions') responseHeaders['X-Open-Count'] = '6';
        return jsonResponse(data, responseHeaders);
      }
      return jsonResponse([], responseHeaders);
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<ExecutionControlPlane session={session} />);
    await screen.findByText('run_1');
    expect(screen.getByText('70/90')).toBeTruthy();

    expect(rowsFor('项目与冻结计划', '.project-list > article')).toHaveLength(4);
    expect(rowsFor('运行与任务矩阵', 'tbody > tr')).toHaveLength(4);
    expect(rowsFor('周期监测', 'tbody > tr')).toHaveLength(4);
    expect(rowsFor('平台账号目录与 Profile 健康', '.account-grid > article')).toHaveLength(4);
    expect(rowsFor('Break-glass 双人审批', '.break-glass-list > article')).toHaveLength(4);
    expect(rowsFor('人工接管队列', '.intervention-list > article')).toHaveLength(4);
    expect(rowsFor('工作流与会话时间线', '.timeline > li')).toHaveLength(4);

    fireEvent.click(nextButton('运行与任务矩阵分页'));
    await screen.findByText('run_5');
    expect(screen.queryByText('run_1')).toBeNull();
    expect(screen.getByText('schedule_1')).toBeTruthy();
    fireEvent.click(nextButton('运行与任务矩阵分页'));
    await screen.findByText('run_9');
    expect(rowsFor('运行与任务矩阵', 'tbody > tr')).toHaveLength(1);
    expect(screen.getByText('schedule_1')).toBeTruthy();

    fireEvent.click(nextButton('周期监测分页'));
    await screen.findByText('schedule_5');
    expect(screen.getByText('run_9')).toBeTruthy();

    fireEvent.click(nextButton('项目与冻结计划分页'));
    await screen.findByText('Project 5');
    expect(screen.getByText('run_9')).toBeTruthy();
    expect(screen.getByText('schedule_5')).toBeTruthy();

    const runNineRow = screen.getByText('run_9').closest('tr');
    expect(runNineRow).not.toBeNull();
    fireEvent.click(within(runNineRow!).getByRole('button', { name: '取消' }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const url = new URL(input instanceof Request ? input.url : String(input));
          return url.pathname === '/api/v2/collection/runs/run_9/cancel';
        }),
      ).toBe(true),
    );
    expect(
      fetchMock.mock.calls.some(([input]) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        return /^\/api\/v2\/collection\/runs\/run_[1-8]\/cancel$/.test(url.pathname);
      }),
    ).toBe(false);
  });
});

type OperationalKind =
  | 'projects'
  | 'runs'
  | 'schedules'
  | 'accounts'
  | 'break-glass'
  | 'interventions'
  | 'events';

function operationalKind(pathname: string): OperationalKind | null {
  const paths: Record<string, OperationalKind> = {
    '/api/v2/projects': 'projects',
    '/api/v2/collection/runs/cursor': 'runs',
    '/api/v2/schedules': 'schedules',
    '/api/v2/platform-accounts': 'accounts',
    '/api/v2/break-glass': 'break-glass',
    '/api/v2/interventions': 'interventions',
    '/api/v2/platform-events': 'events',
  };
  return paths[pathname] ?? null;
}

function cursorOffset(cursor: string | null): number {
  if (!cursor) return 0;
  const parsed = Number(cursor.split('-').at(-1));
  return Number.isInteger(parsed) ? parsed : 0;
}

function operationalItem(kind: OperationalKind, index: number) {
  const timestamp = `2026-08-${String(index).padStart(2, '0')}T08:00:00Z`;
  if (kind === 'projects') {
    return {
      pub_id: `prj_${index}`,
      name: `Project ${index}`,
      state: 'active',
      updated_at: timestamp,
    };
  }
  if (kind === 'runs') {
    return {
      pub_id: `run_${index}`,
      project_pub_id: `prj_${index}`,
      config_version_pub_id: `cfv_${index}`,
      workflow_id: `workflow/${index}`,
      state: 'running',
      total_tasks: 10,
      completed_tasks: 1,
      failed_tasks: 0,
      paused: false,
      error_code: null,
      source: 'manual',
      schedule_pub_id: null,
      retry_of_run_pub_id: null,
      initiated_by_pub_id: 'usr_test',
      created_at: timestamp,
      updated_at: timestamp,
    };
  }
  if (kind === 'schedules') {
    return {
      pub_id: `schedule_${index}`,
      project_pub_id: `prj_${index}`,
      config_version_pub_id: `cfv_${index}`,
      interval_minutes: 60,
      timezone: 'Asia/Shanghai',
      state: 'active',
      next_run_at: timestamp,
      last_run_at: null,
      last_run_pub_id: null,
      responsible_pub_id: 'usr_test',
      created_by_pub_id: 'usr_test',
      version: 1,
      created_at: timestamp,
      updated_at: timestamp,
    };
  }
  if (kind === 'accounts') {
    return {
      pub_id: `account_${index}`,
      platform: 'doubao',
      account_mask: `account-***${index}`,
      owner_pub_id: 'own_test',
      purpose: 'measure',
      responsible_pub_id: 'usr_test',
      custody_mode: 'server',
      region: 'CN-BJ',
      state: 'active',
      admission_level: 'adapter_ready',
      last_passed_at: null,
      scopes: ['read'],
      authorization_expires_at: timestamp,
      profile_state: 'ACTIVE',
      profile_version: 1,
      profile_constraints: ['READ_ONLY'],
      profile_expires_at: timestamp,
      lease_expires_at: null,
    };
  }
  if (kind === 'break-glass') {
    return {
      pub_id: `break_glass_${index}`,
      account_pub_id: `account_${index}`,
      requested_by: 'usr_test',
      reason: `Reason ${index}`,
      state: 'pending',
      approvals: 0,
      expires_at: timestamp,
    };
  }
  if (kind === 'interventions') {
    return {
      pub_id: `intervention_${index}`,
      account_pub_id: `account_${index}`,
      account_mask: `account-***${index}`,
      account_custody_mode: 'server',
      challenge_type: 'otp',
      allowed_domain: 'example.com',
      action: 'read',
      state: 'pending',
      pairing_expires_at: null,
      platform_result: null,
      assigned_to_pub_id: 'usr_test',
      due_at: timestamp,
      resolution_note: '',
    };
  }
  return {
    pub_id: `event_${index}`,
    account_pub_id: `account_${index}`,
    event_type: `event.${index}`,
    summary: {},
    occurred_at: timestamp,
  };
}

function jsonResponse(data: unknown, headers: Record<string, string>) {
  return new Response(JSON.stringify(data), { status: 200, headers });
}

function rowsFor(heading: string, selector: string) {
  const section = screen.getByRole('heading', { name: heading }).closest('section');
  expect(section).not.toBeNull();
  return section!.querySelectorAll(selector);
}

function nextButton(label: string) {
  return within(screen.getByRole('navigation', { name: label })).getByRole('button', {
    name: '下一页',
  });
}
