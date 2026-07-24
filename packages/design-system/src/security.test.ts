// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';
import {
  containsClientSecret,
  projectSafeAccountSummary,
  projectSafeExperienceContext,
  redactClientDiagnostic,
  sanitizeClientUrl,
} from './index';

describe('containsClientSecret', () => {
  it('detects bare OTP and phone values inside ordinary form text', () => {
    expect(containsClientSecret('请使用验证码 824911 完成操作')).toBe(true);
    expect(containsClientSecret('联系人为 13800138000')).toBe(true);
    expect(containsClientSecret('tnt_live_123456')).toBe(false);
    expect(containsClientSecret('制造企业如何选择可信知识库')).toBe(false);
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
      ownerLabel: '责任人 13800138000',
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
    for (const secret of ['session-canary', '13800138000', '394820', '/profile/']) {
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
        'customer 13800138000',
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
      '394820',
      '/secret/browser/profile',
    ]) {
      expect(serialized).not.toContain(secret);
    }
  });
});
