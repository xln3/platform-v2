import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getIdentitySession } = vi.hoisted(() => ({ getIdentitySession: vi.fn() }));

vi.mock('@geo/api-client', () => ({ getIdentitySession }));

import {
  createExperienceLoader,
  getValidatedIdentityHeaders,
  type ExperienceFixture,
} from './index';

const fixture: ExperienceFixture = {
  tenantPubId: 'tnt_fixture',
  tenantLabel: 'Fixture tenant',
  projectPubId: 'prj_fixture',
  projectLabel: 'Fixture project',
  userPubId: 'usr_fixture',
  userLabel: 'Fixture user',
  roles: ['customer'],
  actorSubject: 'customer-fixture',
  actorRole: 'customer',
};

function installStorage(values: Record<string, string> = {}) {
  const data = new Map(Object.entries(values));
  const storage = {
    getItem: vi.fn((key: string) => data.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => data.set(key, value)),
    removeItem: vi.fn((key: string) => data.delete(key)),
    clear: vi.fn(() => data.clear()),
    key: vi.fn(() => null),
    get length() {
      return data.size;
    },
  };
  vi.stubGlobal('window', { localStorage: storage });
  vi.stubGlobal('localStorage', storage);
  return { data, storage };
}

describe('createExperienceLoader', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it('uses the explicitly labelled fixture only when no browser identity was supplied', async () => {
    installStorage();
    getIdentitySession.mockResolvedValue({ kind: 'unavailable' });

    await expect(createExperienceLoader(fixture)()).resolves.toEqual({
      kind: 'fixture',
      value: { ...fixture, source: 'contract-fixture' },
    });
  });

  it('fails closed and does not probe the API for partial or secret-looking hints', async () => {
    const partial = installStorage({ 'geo.session.tenant': 'tnt_only' });
    await expect(createExperienceLoader(fixture)()).resolves.toEqual({ kind: 'forbidden' });
    expect(getIdentitySession).not.toHaveBeenCalled();
    expect(partial.data.size).toBe(0);

    vi.clearAllMocks();
    const secret = installStorage({
      'geo.session.tenant': 'tnt_safe',
      'geo.session.actor': 'owner 13800138000',
      'geo.session.role': 'customer',
    });
    await expect(createExperienceLoader(fixture)()).resolves.toEqual({ kind: 'forbidden' });
    expect(getIdentitySession).not.toHaveBeenCalled();
    expect(secret.data.size).toBe(0);
  });

  it('never falls back after an explicit forbidden or unavailable identity response', async () => {
    installStorage({
      'geo.session.tenant': 'tnt_live',
      'geo.session.actor': 'subject-live',
      'geo.session.role': 'customer',
    });
    getIdentitySession.mockResolvedValueOnce({ kind: 'forbidden' });
    await expect(createExperienceLoader(fixture)()).resolves.toEqual({ kind: 'forbidden' });

    getIdentitySession.mockResolvedValueOnce({ kind: 'unavailable' });
    await expect(createExperienceLoader(fixture)()).resolves.toEqual({ kind: 'unavailable' });
  });

  it('projects only safe generated session and project values', async () => {
    installStorage({
      'geo.session.tenant': 'tnt_live',
      'geo.session.actor': 'subject-live',
      'geo.session.role': 'customer',
    });
    getIdentitySession.mockResolvedValue({
      kind: 'ready',
      session: {
        tenant_pub_id: 'tnt_live',
        user_pub_id: 'usr_live',
        role: 'customer',
        permissions: [],
      },
      projects: {
        data: [{ pub_id: 'prj_live', name: 'Cookie=canary', status: 'active' }],
        next_cursor: null,
      },
    });

    const result = await createExperienceLoader(fixture)();
    expect(result).toMatchObject({
      kind: 'ready',
      value: {
        projectPubId: 'prj_live',
        projectLabel: '未命名项目',
        source: 'live',
      },
    });
    expect(getValidatedIdentityHeaders()).toEqual({
      'X-Tenant-Id': 'tnt_live',
      'X-Actor-Id': 'subject-live',
      'X-Actor-Role': 'customer',
    });
    expect(JSON.stringify(result)).not.toContain('Cookie=canary');
  });

  it('does not mistake numeric public-id suffixes for standalone OTP values', async () => {
    installStorage({
      'geo.session.tenant': 'tnt_live_123456',
      'geo.session.actor': 'customer@example.test',
      'geo.session.role': 'customer',
    });
    getIdentitySession.mockResolvedValue({
      kind: 'ready',
      session: {
        tenant_pub_id: 'tnt_live_123456',
        user_pub_id: 'usr_live_654321',
        role: 'customer',
        permissions: ['project:read'],
      },
      projects: {
        data: [{ pub_id: 'prj_live_abcdef', name: '真实联调项目', status: 'active' }],
        next_cursor: null,
      },
    });
    await expect(createExperienceLoader(fixture)()).resolves.toMatchObject({
      kind: 'ready',
      value: { projectPubId: 'prj_live_abcdef', projectLabel: '真实联调项目' },
    });
  });

  it('rejects unsafe identity values and non-product worker sessions', async () => {
    installStorage({
      'geo.session.tenant': 'tnt_live',
      'geo.session.actor': 'subject-live',
      'geo.session.role': 'customer',
    });
    getIdentitySession.mockResolvedValueOnce({
      kind: 'ready',
      session: {
        tenant_pub_id: 'tnt_live',
        user_pub_id: 'Bearer secret',
        role: 'customer',
        permissions: [],
      },
      projects: { data: [], next_cursor: null },
    });
    await expect(createExperienceLoader(fixture)()).resolves.toEqual({ kind: 'forbidden' });

    getIdentitySession.mockResolvedValueOnce({
      kind: 'ready',
      session: {
        tenant_pub_id: 'tnt_live',
        user_pub_id: 'usr_worker',
        role: 'worker',
        permissions: [],
      },
      projects: { data: [], next_cursor: null },
    });
    await expect(createExperienceLoader(fixture)()).resolves.toEqual({ kind: 'forbidden' });
    expect(getValidatedIdentityHeaders()).toBeNull();
  });
});
