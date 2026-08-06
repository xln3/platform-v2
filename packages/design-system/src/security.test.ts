// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest';
import {
  containsClientSecret,
  containsClientSecretKey,
  createSafeExperienceScopeKey,
  createStructuredClientScopeKey,
  installClientBrowserSecurity,
  installClientDiagnosticSecurity,
  installClientNavigationSecurity,
  installClientWindowNameSecurity,
  navigateClientSection,
  projectSafeAccountSummary,
  projectSafeClientHistoryState,
  projectSafeExperienceContext,
  projectSafeProductNavigation,
  redactClientDiagnostic,
  safeClientDiagnosticEventName,
  sanitizeClientUrl,
  scrubClientStorage,
  updateClientUrlParameters,
} from './index';

describe('containsClientSecret', () => {
  it('detects bare OTP and phone values inside ordinary form text', () => {
    expect(containsClientSecret('请使用验证码 824911 完成操作')).toBe(true);
    expect(containsClientSecret('联系人为 13800138000')).toBe(true);
    expect(containsClientSecret('owner13800138000canary')).toBe(true);
    expect(containsClientSecret('tnt_live_123456')).toBe(false);
    expect(containsClientSecret('制造企业如何选择可信知识库')).toBe(false);
  });

  it('normalizes encoded, full-width and zero-width secrets plus cross-platform profile paths', () => {
    const hostileValues = [
      '联系人为 138 0013 8000',
      '联系人为 138\u200b0013\u200b8000',
      '联系人为 １３８００１３８０００',
      '请使用验证码 824-911 完成操作',
      'Bearer%2520encoded-session-canary',
      String.raw`profile_dir=C:\Users\runner\AppData\Local\Chromium\User Data\Profile 1`,
      String.raw`\\server\browser-profiles\tenant-a`,
    ];

    expect(hostileValues.every((value) => containsClientSecret(value))).toBe(true);
    expect(containsClientSecret('2026-07 版本 · 138 项来源 · 911 项规则')).toBe(false);
  });
});

describe('containsClientSecretKey', () => {
  it('normalizes full-width, zero-width and encoded secret property names', () => {
    expect(containsClientSecretKey('ａｃｃｅｓｓ＿ｔｏｋｅｎ')).toBe(true);
    expect(containsClientSecretKey('to\u200bken')).toBe(true);
    expect(containsClientSecretKey('profile%255Fpath')).toBe(true);
    expect(containsClientSecretKey('Ｃｏｏｋｉｅ')).toBe(true);
    expect(containsClientSecretKey('metric_version')).toBe(false);
  });
});

describe('sanitizeClientUrl', () => {
  it('removes secret and invalid route values without discarding safe query state', () => {
    window.history.replaceState(
      { navigationIndex: 7 },
      '',
      '/platform/customer/?section=Bearer%20route-canary&access_token=token-canary&safe_note=retained',
    );

    expect(sanitizeClientUrl(['home', 'accounts'])).toBe(true);

    const sanitized = new URL(window.location.href);
    expect(sanitized.searchParams.get('safe_note')).toBe('retained');
    expect(sanitized.searchParams.has('section')).toBe(false);
    expect(sanitized.searchParams.has('access_token')).toBe(false);
    expect(window.history.state).toEqual({ navigationIndex: 7 });
    expect(sanitizeClientUrl(['home', 'accounts'])).toBe(false);
  });

  it('resets coupled pagination when a cursor is secret-shaped', () => {
    window.history.replaceState(
      null,
      '',
      '/platform/customer/?section=reports&report_page=2&report_cursor=rpt_Bearer%20cursor-canary',
    );

    expect(sanitizeClientUrl(['home', 'reports'])).toBe(true);

    const sanitized = new URL(window.location.href);
    expect(sanitized.searchParams.get('section')).toBe('reports');
    expect(sanitized.searchParams.has('report_cursor')).toBe(false);
    expect(sanitized.searchParams.has('report_page')).toBe(false);
  });

  it('removes multi-encoded secret fragments while retaining a bounded public anchor', () => {
    window.history.replaceState(
      { navigationIndex: 8 },
      '',
      '/platform/customer/?section=home&safe_note=retained#access_token%3DBearer%2520fragment-canary%26otp%253D824911',
    );

    expect(sanitizeClientUrl(['home', 'accounts'])).toBe(true);
    expect(window.location.hash).toBe('');
    expect(new URL(window.location.href).searchParams.get('safe_note')).toBe('retained');
    expect(window.history.state).toEqual({ navigationIndex: 8 });

    window.history.replaceState(
      { navigationIndex: 9 },
      '',
      '/platform/customer/?section=accounts#public-evidence-anchor',
    );
    expect(sanitizeClientUrl(['home', 'accounts'])).toBe(false);
    expect(window.location.hash).toBe('#public-evidence-anchor');
  });

  it('removes standalone secret names from path and fragment without echoing them', () => {
    window.history.replaceState(
      { navigationIndex: 10 },
      '',
      '/platform/customer/access%255Ftoken#profile%255Fpath',
    );

    expect(sanitizeClientUrl(['home', 'accounts'])).toBe(true);
    expect(window.location.pathname).toBe('/platform/customer/');
    expect(window.location.hash).toBe('');
    expect(window.history.state).toEqual({ navigationIndex: 10 });
    expect(window.location.href).not.toMatch(/access|token|profile/i);
  });

  it('installs the navigation boundary before router popstate consumers', () => {
    const uninstall = installClientNavigationSecurity(['home', 'accounts']);
    try {
      window.history.pushState(
        {
          navigationIndex: 11,
          access_token: 'history-install-canary',
        },
        '',
        '/platform/customer/access%255Ftoken?section=accounts#profile%255Fpath',
      );
      window.dispatchEvent(new PopStateEvent('popstate'));

      expect(window.location.pathname).toBe('/platform/customer/');
      expect(window.location.search).toBe('?section=accounts');
      expect(window.location.hash).toBe('');
      expect(window.history.state).toEqual({ navigationIndex: 11 });
    } finally {
      uninstall();
    }
  });

  it('projects pushState and replaceState before the browser retains either entry', () => {
    const originalPushState = window.history.pushState;
    const originalReplaceState = window.history.replaceState;
    const uninstall = installClientNavigationSecurity(['home', 'accounts']);
    try {
      window.history.pushState(
        {
          navigationIndex: 12,
          nested: { safe: 'retained', note: 'OTP 824911' },
          access_token: 'history-write-canary',
        },
        '',
        '/platform/customer/access%255Ftoken?section=accounts&access_token=url-write-canary#profile%255Fpath',
      );
      expect(window.location.pathname).toBe('/platform/customer/');
      expect(window.location.search).toBe('?section=accounts');
      expect(window.location.hash).toBe('');
      expect(window.history.state).toEqual({
        navigationIndex: 12,
        nested: { safe: 'retained' },
      });

      window.history.replaceState(
        {
          navigationIndex: 13,
          safe: true,
          profilePath: '/secret/browser/profile/history-replace-canary',
        },
        '',
        '/platform/customer/?section=accounts&otp=824911',
      );
      expect(window.location.search).toBe('?section=accounts');
      expect(window.history.state).toEqual({ navigationIndex: 13, safe: true });
    } finally {
      uninstall();
    }
    expect(window.history.pushState).toBe(originalPushState);
    expect(window.history.replaceState).toBe(originalReplaceState);
  });

  it('scrubs existing storage and rejects secret writes before either browser store retains them', () => {
    const storagePrototype = window.Storage.prototype;
    const originalSetItem = storagePrototype.setItem;
    window.localStorage.clear();
    window.sessionStorage.clear();
    originalSetItem.call(window.localStorage, 'geo.access_token', 'existing-storage-key-canary');
    originalSetItem.call(window.localStorage, 'geo.preference.theme', 'dark');
    originalSetItem.call(window.sessionStorage, 'geo.legacy.note', 'OTP 824911');
    const uninstall = installClientBrowserSecurity(['home', 'accounts']);
    try {
      expect(window.localStorage.getItem('geo.access_token')).toBeNull();
      expect(window.sessionStorage.getItem('geo.legacy.note')).toBeNull();
      expect(window.localStorage.getItem('geo.preference.theme')).toBe('dark');

      window.localStorage.setItem('geo.ａｃｃｅｓｓ＿ｔｏｋｅｎ', 'write-key-canary');
      window.sessionStorage.setItem('geo.legacy.note', '联系人 13800138000');
      window.sessionStorage.setItem('geo.preference.panel', 'expanded');
      expect(window.localStorage.getItem('geo.ａｃｃｅｓｓ＿ｔｏｋｅｎ')).toBeNull();
      expect(window.sessionStorage.getItem('geo.legacy.note')).toBeNull();
      expect(window.sessionStorage.getItem('geo.preference.panel')).toBe('expanded');
    } finally {
      uninstall();
      window.localStorage.clear();
      window.sessionStorage.clear();
    }
    expect(storagePrototype.setItem).toBe(originalSetItem);
  });

  it('clears and seals the cross-navigation window name surface', () => {
    window.name = 'Bearer bootstrap-window-name-canary';
    const uninstall = installClientWindowNameSecurity();
    try {
      expect(window.name).toBe('');
      window.name =
        'Cookie=session-window-name-canary OTP 824911 profile_path=/secret/profile/window-name';
      expect(window.name).toBe('');
    } finally {
      uninstall();
    }

    expect(window.name).toBe('');
    window.name = 'safe-name-after-uninstall';
    expect(window.name).toBe('safe-name-after-uninstall');
    window.name = '';
  });

  it('reports required-hint removal and clears an oversized storage projection', () => {
    window.localStorage.clear();
    window.localStorage.setItem('geo.session.actor', 'OTP 824911');
    expect(scrubClientStorage(window.localStorage, new Set(['geo.session.actor']))).toMatchObject({
      removedRequiredHint: true,
      clearedOversizedStorage: false,
      removedEntries: 1,
    });
    expect(window.localStorage.length).toBe(0);
  });

  it('projects global errors and unhandled rejections before browser default reporting', () => {
    vi.useFakeTimers();
    const diagnostics: unknown[] = [];
    const listener = (event: Event) => {
      diagnostics.push((event as CustomEvent<unknown>).detail);
    };
    const originalUrl = window.location.href;
    const originalLocalStorage = JSON.stringify(localStorage);
    const originalSessionStorage = JSON.stringify(sessionStorage);
    const windowError = new Error('Cookie=session-global-error-canary OTP 824911', {
      cause: { profile_path: '/secret/browser/profile/global-error-canary' },
    });
    windowError.name = 'Bearer global-error-canary';
    const errorEvent = new ErrorEvent('error', {
      cancelable: true,
      error: windowError,
      message: 'proxy_password=global-error-canary',
    });
    const rejectionEvent = new Event('unhandledrejection', { cancelable: true });
    Object.defineProperty(rejectionEvent, 'reason', {
      value: new TypeError('13800138000 token=global-rejection-canary'),
    });
    window.addEventListener(safeClientDiagnosticEventName, listener);
    const uninstall = installClientDiagnosticSecurity();
    let relayTimerCount = 0;
    try {
      window.dispatchEvent(errorEvent);
      window.dispatchEvent(rejectionEvent);
    } finally {
      relayTimerCount = vi.getTimerCount();
      vi.clearAllTimers();
      vi.useRealTimers();
      uninstall();
      window.removeEventListener(safeClientDiagnosticEventName, listener);
    }

    expect(relayTimerCount).toBe(2);
    expect(errorEvent.defaultPrevented).toBe(true);
    expect(rejectionEvent.defaultPrevented).toBe(true);
    expect(diagnostics).toEqual([
      {
        kind: 'window_error',
        errorName: 'Error',
        componentFrames: 0,
        hasCause: true,
      },
      {
        kind: 'unhandled_rejection',
        errorName: 'TypeError',
        componentFrames: 0,
        hasCause: false,
      },
    ]);
    expect(JSON.stringify(diagnostics)).not.toMatch(
      /Bearer|Cookie|session|token|OTP|824911|proxy_password|profile|13800138000|global-(?:error|rejection)-canary/i,
    );
    expect(window.location.href).toBe(originalUrl);
    expect(JSON.stringify(localStorage)).toBe(originalLocalStorage);
    expect(JSON.stringify(sessionStorage)).toBe(originalSessionStorage);
  });

  it('retains the exact public profile section while rejecting profile path names', () => {
    window.history.replaceState({ navigationIndex: 12 }, '', '/platform/customer/?section=profile');
    const uninstall = installClientNavigationSecurity(['home', 'profile', 'profile_path']);
    try {
      expect(window.location.search).toBe('?section=profile');
      window.history.pushState(
        { navigationIndex: 13 },
        '',
        '/platform/customer/?section=profile_path',
      );
      window.dispatchEvent(new PopStateEvent('popstate'));
      expect(window.location.search).toBe('');
      expect(window.history.state).toEqual({ navigationIndex: 13 });
    } finally {
      uninstall();
    }
  });

  it('bounds parameter names, values, count and total URL length before browser history retains them', () => {
    const params = new URLSearchParams({
      section: 'accounts',
      safe_note: 'retained',
      oversized: 'x'.repeat(501),
      ['k'.repeat(81)]: 'unsafe-name',
    });
    for (let index = 0; index < 40; index += 1) {
      params.append(`safe_${index}`, 'x'.repeat(400));
    }
    window.history.replaceState(null, '', `/platform/customer/?${params.toString()}`);

    expect(sanitizeClientUrl(['home', 'accounts'])).toBe(true);

    const sanitized = new URL(window.location.href);
    expect(sanitized.searchParams.get('section')).toBe('accounts');
    expect(sanitized.searchParams.get('safe_note')).toBe('retained');
    expect(sanitized.searchParams.has('oversized')).toBe(false);
    expect([...sanitized.searchParams.keys()]).not.toContain('k'.repeat(81));
    expect([...sanitized.searchParams].length).toBeLessThanOrEqual(32);
    expect(sanitized.toString().length).toBeLessThanOrEqual(4_096);
  });

  it('projects browser history state without retaining secret keys, values or cycles', () => {
    const hostileState = {
      idx: 12,
      usr: 'router-history-key',
      access_token: 'opaque-history-canary',
      nested: {
        safe: 'retained',
        note: 'OTP 824911',
        challenge: 824911,
        mobile: 13800138000,
      },
      list: ['ready', 'Bearer history-list-canary'],
      oversized: 'x'.repeat(501),
    };
    expect(projectSafeClientHistoryState(hostileState)).toEqual({
      value: {
        idx: 12,
        usr: 'router-history-key',
        nested: { safe: 'retained' },
        list: ['ready'],
      },
      changed: true,
    });

    window.history.replaceState(hostileState, '', '/platform/customer/?section=accounts');
    expect(sanitizeClientUrl(['home', 'accounts'])).toBe(true);
    expect(window.history.state).toEqual({
      idx: 12,
      usr: 'router-history-key',
      nested: { safe: 'retained' },
      list: ['ready'],
    });
    expect(JSON.stringify(window.history.state)).not.toMatch(
      /access_token|opaque-history-canary|824911|13800138000|Bearer|history-list-canary/,
    );

    const cyclic: { safe: string; self?: unknown } = { safe: 'retained' };
    cyclic.self = cyclic;
    expect(projectSafeClientHistoryState(cyclic)).toEqual({
      value: { safe: 'retained' },
      changed: true,
    });
  });

  it('navigates between allow-listed sections only after applying the shared URL boundary', () => {
    window.history.replaceState(
      null,
      '',
      '/platform/customer/?access_token=token-canary#OTP%3A%20824911',
    );

    expect(navigateClientSection('reports', ['home', 'reports'])).toBe(true);
    expect(window.location.search).toBe('?section=reports');
    expect(window.location.hash).toBe('');

    const safeUrl = window.location.href;
    expect(navigateClientSection('Bearer unsafe', ['home', 'reports'])).toBe(false);
    expect(window.location.href).toBe(safeUrl);
  });

  it('updates bounded public URL filters while deleting secret-shaped values before history', () => {
    window.history.replaceState(
      { access_token: 'stale-history-canary' },
      '',
      '/platform/operations/media-prices?access_token=stale-url-canary&retained=public',
    );

    expect(
      updateClientUrlParameters(
        {
          retained: null,
          media_geo: 'a,f',
          media_q: 'OTP 824911',
        },
        [],
      ),
    ).toBe(true);

    const url = new URL(window.location.href);
    expect(url.searchParams.get('media_geo')).toBe('a,f');
    expect(url.searchParams.has('media_q')).toBe(false);
    expect(url.searchParams.has('retained')).toBe(false);
    expect(url.searchParams.has('access_token')).toBe(false);
    expect(window.history.state).toEqual({});
    expect(window.location.href).not.toMatch(/824911|stale-url-canary/i);
  });
});

describe('projectSafeProductNavigation', () => {
  it('retains only unique safe labels and bounded internal platform links', () => {
    const projected = projectSafeProductNavigation([
      { id: 'home', label: '首页' },
      {
        id: 'execution',
        label: '执行任务',
        href: '/platform/operations/execution',
      },
      {
        id: 'sessions',
        label: '会话健康',
        href: '/platform/operations/?section=sessions',
      },
      {
        id: 'unsafe-link',
        label: '危险链接',
        href: '/platform/operations/execution?access_token=token-navigation-canary',
      },
      { id: 'secret-label', label: 'Bearer navigation-label-canary' },
      { id: 'home', label: '重复首页' },
      { id: 'badge-secret', label: '计数', badge: 'OTP: 824911' },
      { id: 'external', label: '外部地址', href: 'https://example.invalid/platform/reports' },
    ]);

    expect(projected).toEqual([
      { id: 'home', label: '首页' },
      {
        id: 'execution',
        label: '执行任务',
        href: '/platform/operations/execution',
      },
      {
        id: 'sessions',
        label: '会话健康',
        href: '/platform/operations/?section=sessions',
      },
      { id: 'unsafe-link', label: '危险链接', disabledExternal: true },
      { id: 'external', label: '外部地址', disabledExternal: true },
    ]);
    expect(JSON.stringify(projected)).not.toMatch(
      /access_token|token-navigation-canary|Bearer|navigation-label-canary|824911|example\.invalid/,
    );
  });
});

describe('projectSafeExperienceContext', () => {
  it('redacts secret-shaped values even when they arrive through allow-listed identity fields', () => {
    expect(
      projectSafeExperienceContext({
        tenantPubId: 'Cookie=session-canary',
        tenantLabel: '租户 OTP: 824911',
        projectPubId: '/profiles/secret-project',
        projectLabel: '联系人 13800138000',
        userPubId: 'Bearer access-canary',
        userLabel: 'proxy_password=secret',
        roles: ['customer'],
        source: 'live',
      }),
    ).toEqual({
      tenantPubId: 'tnt_redacted',
      tenantLabel: '租户已隐藏',
      projectPubId: '',
      projectLabel: '未命名项目',
      userPubId: 'usr_redacted',
      userLabel: '用户已隐藏',
      roles: ['customer'],
      source: 'live',
    });
  });

  it('rejects control and bidi identity text and encodes the remaining scope structurally', () => {
    const projected = projectSafeExperienceContext({
      tenantPubId: 'tnt_safe\u0000prj_collision',
      tenantLabel: '安全租户\u202e',
      projectPubId: 'prj_safe',
      projectLabel: '安全项目\n伪造行',
      userPubId: 'usr_safe\u2066',
      userLabel: '安全用户',
      roles: ['reviewer', 'analyst'],
      source: 'live',
    });

    expect(projected).toEqual({
      tenantPubId: 'tnt_redacted',
      tenantLabel: '租户已隐藏',
      projectPubId: 'prj_safe',
      projectLabel: '未命名项目',
      userPubId: 'usr_redacted',
      userLabel: '安全用户',
      roles: ['reviewer', 'analyst'],
      source: 'live',
    });
    const scope = createSafeExperienceScopeKey(projected);
    expect(scope).not.toContain('\u0000');
    expect(JSON.parse(scope)).toEqual([
      'tnt_redacted',
      'prj_safe',
      'usr_redacted',
      ['analyst', 'reviewer'],
      'live',
    ]);
  });
});

describe('createStructuredClientScopeKey', () => {
  it('keeps hostile delimiter placement structurally distinct without retaining a raw NUL', () => {
    const left = createStructuredClientScopeKey(['tenant\u0000actor', 'role']);
    const right = createStructuredClientScopeKey(['tenant', 'actor\u0000role']);

    expect(left).not.toBe(right);
    expect(left).not.toContain('\u0000');
    expect(right).not.toContain('\u0000');
    expect(JSON.parse(left)).toEqual(['tenant\u0000actor', 'role']);
    expect(JSON.parse(right)).toEqual(['tenant', 'actor\u0000role']);
  });
});

describe('projectSafeAccountSummary', () => {
  it('drops secret and unknown fields from a hostile API response', () => {
    const result = projectSafeAccountSummary({
      accountMask: '尾号 · 4821',
      platformLabel: '豆包',
      ownerLabel: '客户管理员',
      custodyMode: 'hybrid',
      admissionLevel: 'adapter_ready',
      scopes: ['read', 'query', 'delete', 'pay'],
      expiresLabel: '2026-09-30',
      regionLabel: '中国大陆',
      sessionHealth: 'healthy',
      lastVerifiedLabel: '尚未 live 验证',
      interventionStatus: 'none',
      cookie: 'SESSION=dlp-canary-cookie',
      token: 'dlp-canary-token',
      otp: '394820',
      proxyPassword: 'dlp-canary-proxy-password',
      phone: '13800138000',
      profilePath: '/secret/browser/profile',
      biometricTemplate: 'dlp-canary-face-vector',
      nested: { authorization: 'Bearer dlp-canary' },
    });
    expect(result.scopes).toEqual(['read', 'query']);
    expect(Object.keys(result).sort()).toEqual([
      'accountMask',
      'admissionLevel',
      'custodyMode',
      'expiresLabel',
      'interventionStatus',
      'lastVerifiedLabel',
      'ownerLabel',
      'platformLabel',
      'regionLabel',
      'scopes',
      'sessionHealth',
    ]);
    const serialized = JSON.stringify(result);
    for (const canary of [
      'cookie',
      'token',
      '394820',
      'proxy',
      '13800138000',
      '/secret',
      'face-vector',
      'Bearer',
    ]) {
      expect(serialized).not.toContain(canary);
    }
  });

  it('fails closed for invalid inputs', () => {
    expect(
      projectSafeAccountSummary({
        custodyMode: 'download-profile',
        admissionLevel: 'live_everything',
        sessionHealth: 'secret-exported',
        scopes: 'publish',
      }),
    ).toMatchObject({
      accountMask: '账号已隐藏',
      custodyMode: 'customer-device',
      admissionLevel: 'catalogued',
      scopes: [],
      sessionHealth: 'degraded',
    });
  });

  it('replaces secret-shaped values hidden in allow-listed account fields', () => {
    const result = projectSafeAccountSummary({
      accountMask: 'Cookie=session-canary',
      platformLabel: '豆包',
      ownerLabel: 'owner13800138000canary',
      custodyMode: 'hybrid',
      admissionLevel: 'read_verified',
      scopes: ['read'],
      expiresLabel: 'OTP 394820',
      regionLabel: '中国大陆',
      sessionHealth: 'healthy',
      lastVerifiedLabel: '/var/browser/profile/customer-a',
      interventionStatus: 'none',
    });
    expect(result).toMatchObject({
      accountMask: '账号已隐藏',
      platformLabel: '豆包',
      ownerLabel: '所有者已隐藏',
      expiresLabel: '—',
      lastVerifiedLabel: '尚未验证',
    });
    const serialized = JSON.stringify(result);
    for (const secret of [
      'session-canary',
      '13800138000',
      'owner13800138000canary',
      '394820',
      '/profile/',
    ]) {
      expect(serialized).not.toContain(secret);
    }
  });

  it('redacts secrets recursively before telemetry or error reporting', () => {
    const diagnostic = redactClientDiagnostic({
      code: 'render_failed',
      message: 'safe context',
      cookie: 'SESSION=dlp-canary-cookie',
      request: {
        authorization: 'Bearer dlp-canary-token',
        url: '/platform/customer/?model=deepseek',
        reason: 'adapter delayed',
      },
      list: [{ otp: '394820', status: 'failed' }, '/secret/browser/profile'],
      disguised: [
        'customer13800138000canary',
        'OTP 394820',
        'proxy password: proxy-canary',
        '/var/browser/profile/customer-a',
        'biometric face vector',
      ],
      numericDisguised: {
        challenge: 394820,
        subject: 13800138000,
        safeHttpStatus: 503,
        safeMetric: 68.4,
      },
      ｔｏｋｅｎ: 'fullwidth-key-canary',
      'co\u200bokie': 'zero-width-key-canary',
      'profile%255Fpath': 'encoded-key-canary',
    });
    expect(diagnostic).toEqual({
      code: 'render_failed',
      message: 'safe context',
      request: {
        url: '/platform/customer/?model=deepseek',
        reason: 'adapter delayed',
      },
      list: [{ status: 'failed' }, '[redacted]'],
      disguised: ['[redacted]', '[redacted]', '[redacted]', '[redacted]', '[redacted]'],
      numericDisguised: {
        challenge: '[redacted]',
        subject: '[redacted]',
        safeHttpStatus: 503,
        safeMetric: 68.4,
      },
    });
    const serialized = JSON.stringify(diagnostic);
    for (const secret of [
      'SESSION=',
      'Bearer ',
      'dlp-canary',
      'customer13800138000canary',
      '394820',
      '/secret/browser/profile',
      'fullwidth-key-canary',
      'zero-width-key-canary',
      'encoded-key-canary',
    ]) {
      expect(serialized).not.toContain(secret);
    }
  });
});
