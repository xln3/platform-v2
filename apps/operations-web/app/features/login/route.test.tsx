// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest';
import { createOperationsSession, customerHomeForSearch } from './route';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('operations email login boundary', () => {
  it('preserves only a valid project landing hint for customer navigation', () => {
    expect(customerHomeForSearch('?project=prj_shengbang_2026&access_token=must-not-forward')).toBe(
      '/platform/customer/?project=prj_shengbang_2026',
    );
    expect(customerHomeForSearch('?project=../../operations')).toBe('/platform/customer/');
  });

  it('posts credentials only to the same-origin Platform V2 identity endpoint', async () => {
    const request = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            tenant_pub_id: 'tnt_login_test',
            user_pub_id: 'usr_operator_test',
            role: 'operator',
            permissions: ['project:read'],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
    );

    await expect(
      createOperationsSession(
        { email: '  operator@example.com ', password: 'safe-test-password' },
        request,
      ),
    ).resolves.toBe('operations');

    expect(request).toHaveBeenCalledWith(
      '/api/v2/identity/login',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify({
          email: 'operator@example.com',
          password: 'safe-test-password',
        }),
      }),
    );
  });

  it('routes customer sessions to the customer application', async () => {
    const request = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            tenant_pub_id: 'tnt_login_test',
            user_pub_id: 'usr_customer_test',
            role: 'customer',
            permissions: ['project:read'],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
    );

    await expect(
      createOperationsSession(
        { email: 'customer@example.com', password: 'safe-test-password' },
        request,
      ),
    ).resolves.toBe('customer');
  });

  it('rejects a malformed successful session projection', async () => {
    const request = vi.fn(async () => new Response('{}', { status: 200 }));

    await expect(
      createOperationsSession(
        { email: 'operator@example.com', password: 'safe-test-password' },
        request,
      ),
    ).resolves.toBe('unavailable');
  });

  it('maps rejected credentials without retaining a server response body', async () => {
    const request = vi.fn(async () => new Response('secret-shaped server detail', { status: 401 }));

    await expect(
      createOperationsSession(
        { email: 'operator@example.com', password: 'wrong-test-password' },
        request,
      ),
    ).resolves.toBe('invalid_credentials');
  });

  it('submits an existing six-character password without a client-side length rule', async () => {
    const request = vi.fn(async () => new Response('{}', { status: 401 }));

    await expect(
      createOperationsSession({ email: 'operator@example.com', password: 'abc123' }, request),
    ).resolves.toBe('invalid_credentials');
    expect(request).toHaveBeenCalledOnce();
  });

  it('rejects malformed input before making a request', async () => {
    const request = vi.fn();

    await expect(
      createOperationsSession({ email: 'not-an-email', password: 'short' }, request),
    ).resolves.toBe('invalid_input');
    expect(request).not.toHaveBeenCalled();
  });
});
