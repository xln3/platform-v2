// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ExecutionControlPlane } from './ExecutionControlPlane';

const session = { tenantId: 'tnt_test', actorId: 'usr_test', role: 'operator' as const };

describe('ExecutionControlPlane', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = String(input);
        const data = url.includes('/projects?')
          ? {
              data: [
                {
                  pub_id: 'prj_test',
                  name: 'Catalog Project',
                  state: 'active',
                  updated_at: '2026-07-24T08:00:00Z',
                },
              ],
            }
          : url.endsWith('/platform-accounts')
            ? [
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
              ]
            : url.endsWith('/collection/runs')
              ? [
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
                    updated_at: '2026-07-24T08:00:00Z',
                  },
                ]
              : [];
        return new Response(JSON.stringify(data), {
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
});
