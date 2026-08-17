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

function createStorage(values: Record<string, string>) {
  const data = new Map(Object.entries(values));
  const storage = {
    getItem: vi.fn((key: string) => data.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => data.set(key, value)),
    removeItem: vi.fn((key: string) => data.delete(key)),
    clear: vi.fn(() => data.clear()),
    key: vi.fn((index: number) => [...data.keys()][index] ?? null),
    get length() {
      return data.size;
    },
  };
  return { data, storage };
}

function installStorage(
  values: Record<string, string> = {},
  sessionValues: Record<string, string> = {},
  href = 'http://localhost/',
) {
  const local = createStorage(values);
  const session = createStorage(sessionValues);
  vi.stubGlobal('window', {
    localStorage: local.storage,
    sessionStorage: session.storage,
    location: { href },
  });
  vi.stubGlobal('localStorage', local.storage);
  vi.stubGlobal('sessionStorage', session.storage);
  return {
    data: local.data,
    storage: local.storage,
    sessionData: session.data,
    sessionStorage: session.storage,
  };
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

    vi.clearAllMocks();
    const control = installStorage({
      'geo.session.tenant': 'tnt_safe\u0000actor_collision',
      'geo.session.actor': 'subject-live',
      'geo.session.role': 'customer',
    });
    await expect(createExperienceLoader(fixture)()).resolves.toEqual({ kind: 'forbidden' });
    expect(getIdentitySession).not.toHaveBeenCalled();
    expect(control.data.size).toBe(0);
  });

  it('scrubs normalized secret keys and values from both browser stores before identity use', async () => {
    const browserStorage = installStorage(
      {
        'geo.session.tenant': 'tnt_live',
        'geo.session.actor': 'subject-live',
        'geo.session.role': 'customer',
        'geo.ａｃｃｅｓｓ＿ｔｏｋｅｎ': 'opaque-fullwidth-key-canary',
        'geo.preference.theme': 'dark',
        'geo.legacy.note': '联系人 １３８００１３８０００',
      },
      {
        'geo.to\u200bken': 'opaque-zero-width-key-canary',
        'geo.legacy.profile': String.raw`profile_dir=C:\Users\runner\Profile 1`,
        'geo.preference.panel': 'expanded',
      },
    );
    getIdentitySession.mockResolvedValue({ kind: 'unavailable' });

    await expect(createExperienceLoader(fixture)()).resolves.toEqual({ kind: 'unavailable' });
    expect(getIdentitySession).toHaveBeenCalledOnce();
    expect(Object.fromEntries(browserStorage.data)).toEqual({
      'geo.session.tenant': 'tnt_live',
      'geo.session.actor': 'subject-live',
      'geo.session.role': 'customer',
      'geo.preference.theme': 'dark',
    });
    expect(Object.fromEntries(browserStorage.sessionData)).toEqual({
      'geo.preference.panel': 'expanded',
    });
  });

  it('clears an oversized browser store and fails closed without probing identity', async () => {
    const oversized = Object.fromEntries(
      Array.from({ length: 10_001 }, (_, index) => [`geo.legacy.${index}`, 'safe']),
    );
    const browserStorage = installStorage({
      ...oversized,
      'geo.session.tenant': 'tnt_live',
      'geo.session.actor': 'subject-live',
      'geo.session.role': 'customer',
    });

    await expect(createExperienceLoader(fixture)()).resolves.toEqual({ kind: 'forbidden' });
    expect(getIdentitySession).not.toHaveBeenCalled();
    expect(browserStorage.data.size).toBe(0);
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

  it('lets only the newest concurrent bootstrap own validated request headers', async () => {
    const browserStorage = installStorage({
      'geo.session.tenant': 'tnt_live',
      'geo.session.actor': 'subject-first',
      'geo.session.role': 'customer',
    });
    let resolveFirst!: (value: unknown) => void;
    let resolveSecond!: (value: unknown) => void;
    getIdentitySession
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSecond = resolve;
          }),
      );

    const load = createExperienceLoader(fixture);
    const first = load();
    browserStorage.storage.setItem('geo.session.actor', 'subject-second');
    const second = load();
    resolveSecond({
      kind: 'ready',
      session: {
        tenant_pub_id: 'tnt_live',
        user_pub_id: 'usr_second',
        role: 'customer',
        permissions: [],
      },
      projects: { data: [], next_cursor: null },
    });

    await expect(second).resolves.toMatchObject({
      kind: 'ready',
      value: { userPubId: 'usr_second' },
    });
    expect(getValidatedIdentityHeaders()).toEqual({
      'X-Tenant-Id': 'tnt_live',
      'X-Actor-Id': 'subject-second',
      'X-Actor-Role': 'customer',
    });

    resolveFirst({
      kind: 'ready',
      session: {
        tenant_pub_id: 'tnt_live',
        user_pub_id: 'usr_first',
        role: 'customer',
        permissions: [],
      },
      projects: { data: [], next_cursor: null },
    });
    await expect(first).resolves.toEqual({ kind: 'unavailable' });
    expect(getValidatedIdentityHeaders()).toEqual({
      'X-Tenant-Id': 'tnt_live',
      'X-Actor-Id': 'subject-second',
      'X-Actor-Role': 'customer',
    });
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
        user_pub_id: 'usr_safe\u202e',
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

  it('derives validated request headers from the session when no browser hints exist', async () => {
    // 生产 cookie 会话（operations-web 登录只写 cookie，不写 localStorage hints）：
    //  mutation guard 依赖 getValidatedIdentityHeaders() 非空，否则静默拦截一切写操作。
    //  无 hints 且无 fixture 时才走到会话探测（等价于生产 VITE_ALLOW_CONTRACT_FIXTURES=false）。
    installStorage({});
    getIdentitySession.mockResolvedValue({
      kind: 'ready',
      session: {
        tenant_pub_id: 'tnt_cookie',
        user_pub_id: 'usr_cookie',
        role: 'customer',
        permissions: [],
      },
      projects: {
        data: [{ pub_id: 'prj_cookie', name: 'Cookie 会话项目', status: 'active' }],
        next_cursor: null,
      },
    });

    await expect(createExperienceLoader()()).resolves.toMatchObject({
      kind: 'ready',
      value: { projectPubId: 'prj_cookie', source: 'live' },
    });
    expect(getValidatedIdentityHeaders()).toEqual({
      'X-Tenant-Id': 'tnt_cookie',
      'X-Actor-Id': 'usr_cookie',
      'X-Actor-Role': 'customer',
    });
  });

  it('selects an authorized project explicitly requested by the URL', async () => {
    installStorage(
      {
        'geo.session.tenant': 'tnt_live',
        'geo.session.actor': 'subject-live',
        'geo.session.role': 'customer',
      },
      {},
      'https://example.test/platform/customer/?project=prj_security',
    );
    getIdentitySession.mockResolvedValue({
      kind: 'ready',
      session: {
        tenant_pub_id: 'tnt_live',
        user_pub_id: 'usr_live',
        role: 'customer',
        permissions: ['project:read'],
      },
      projects: {
        data: [
          {
            pub_id: 'prj_testdeep',
            name: 'testdeep',
            state: 'paused',
            updated_at: '2026-08-17T01:00:00Z',
          },
          {
            pub_id: 'prj_security',
            name: '盛邦安全-GEO验证',
            state: 'draft',
            updated_at: '2026-08-16T01:00:00Z',
          },
        ],
        meta: { next_cursor: null },
      },
    });

    await expect(createExperienceLoader(fixture)()).resolves.toMatchObject({
      kind: 'ready',
      value: {
        projectPubId: 'prj_security',
        projectLabel: '盛邦安全-GEO验证',
        projects: [
          { projectPubId: 'prj_security', state: 'draft' },
          { projectPubId: 'prj_testdeep', state: 'paused' },
        ],
      },
    });
  });

  it('prefers a usable project state over an older arbitrary public-id order', async () => {
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
        permissions: ['project:read'],
      },
      projects: {
        data: [
          {
            pub_id: 'prj_000_testdeep',
            name: 'testdeep',
            state: 'paused',
            updated_at: '2026-08-17T02:00:00Z',
          },
          {
            pub_id: 'prj_999_security',
            name: '盛邦安全-GEO验证',
            state: 'draft',
            updated_at: '2026-08-16T02:00:00Z',
          },
        ],
        meta: { next_cursor: null },
      },
    });

    await expect(createExperienceLoader(fixture)()).resolves.toMatchObject({
      kind: 'ready',
      value: { projectPubId: 'prj_999_security', projectLabel: '盛邦安全-GEO验证' },
    });
  });

  it('defaults to the most recently updated usable project and remembers it', async () => {
    const browserStorage = installStorage({
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
        permissions: ['project:read'],
      },
      projects: {
        data: [
          {
            pub_id: 'prj_active_old',
            name: '早期进行中项目',
            state: 'active',
            updated_at: '2026-07-01T01:00:00Z',
          },
          {
            pub_id: 'prj_draft_latest',
            name: '盛邦安全-GEO验证',
            state: 'draft',
            updated_at: '2026-08-17T01:00:00Z',
          },
        ],
        meta: { next_cursor: null },
      },
    });

    await expect(createExperienceLoader(fixture)()).resolves.toMatchObject({
      kind: 'ready',
      value: { projectPubId: 'prj_draft_latest', projectLabel: '盛邦安全-GEO验证' },
    });
    expect(browserStorage.data.get('geo.preference.last-project')).toBe('prj_draft_latest');
  });

  it('restores the last authorized project instead of replacing the customer context', async () => {
    installStorage({
      'geo.session.tenant': 'tnt_live',
      'geo.session.actor': 'subject-live',
      'geo.session.role': 'customer',
      'geo.preference.last-project': 'prj_active_old',
    });
    getIdentitySession.mockResolvedValue({
      kind: 'ready',
      session: {
        tenant_pub_id: 'tnt_live',
        user_pub_id: 'usr_live',
        role: 'customer',
        permissions: ['project:read'],
      },
      projects: {
        data: [
          {
            pub_id: 'prj_active_old',
            name: '用户上次查看的项目',
            state: 'active',
            updated_at: '2026-07-01T01:00:00Z',
          },
          {
            pub_id: 'prj_draft_latest',
            name: '最近更新项目',
            state: 'draft',
            updated_at: '2026-08-17T01:00:00Z',
          },
        ],
        meta: { next_cursor: null },
      },
    });

    await expect(createExperienceLoader(fixture)()).resolves.toMatchObject({
      kind: 'ready',
      value: { projectPubId: 'prj_active_old', projectLabel: '用户上次查看的项目' },
    });
  });

  it('fails closed when the URL requests a project outside the authorized project list', async () => {
    installStorage(
      {
        'geo.session.tenant': 'tnt_live',
        'geo.session.actor': 'subject-live',
        'geo.session.role': 'customer',
      },
      {},
      'https://example.test/platform/customer/?project=prj_other_tenant',
    );
    getIdentitySession.mockResolvedValue({
      kind: 'ready',
      session: {
        tenant_pub_id: 'tnt_live',
        user_pub_id: 'usr_live',
        role: 'customer',
        permissions: ['project:read'],
      },
      projects: {
        data: [
          {
            pub_id: 'prj_security',
            name: '盛邦安全-GEO验证',
            state: 'draft',
            updated_at: '2026-08-17T01:00:00Z',
          },
        ],
        meta: { next_cursor: null },
      },
    });

    await expect(createExperienceLoader(fixture)()).resolves.toEqual({ kind: 'forbidden' });
  });
});
