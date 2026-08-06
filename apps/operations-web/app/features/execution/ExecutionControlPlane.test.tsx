// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
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

  it('creates a scoped customer-terminal bundle without offering direct completion', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        let data: unknown = [];
        if (url.includes('/projects?')) data = { data: [] };
        if (url.endsWith('/platform-accounts'))
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
        if (url.endsWith('/interventions'))
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
        if (url.endsWith('/interventions/int_customer/pair'))
          data = {
            intervention_pub_id: 'int_customer',
            pairing_token: 'one-time-token',
            server_public_key_sha256: 'a'.repeat(64),
            allowed_domain: 'example.com',
            action: 'read',
            challenge_type: 'passkey',
            expires_at: '2026-07-25T08:10:00Z',
          };
        return new Response(JSON.stringify(data), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
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
});
