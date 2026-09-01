import { describe, expect, it } from 'vitest';
import {
  containsClientSecret,
  containsClientSecretKey,
  containsUnsafeClientControlCharacter,
} from './browser-security';

describe('canonical browser security projection', () => {
  it.each([
    '\u0000',
    '\u001f',
    '\u007f',
    '\u0085',
    '\u009f',
    '\u2028',
    '\u2029',
    '\u202e',
    '\u2066',
  ])('rejects unsafe control %j', (control) => {
    expect(containsUnsafeClientControlCharacter(`安全${control}伪装`)).toBe(true);
  });

  it('accepts ordinary multilingual identity text', () => {
    expect(containsUnsafeClientControlCharacter('中意人寿 · 项目 A')).toBe(false);
  });

  it('normalizes encoded and compatibility-form secret candidates', () => {
    expect(containsClientSecret('cookie%3Dproduction-canary')).toBe(true);
    expect(containsClientSecret('Ｐａｓｓｗｏｒｄ：production-canary')).toBe(true);
    expect(containsClientSecretKey('authoriz\u200bation')).toBe(true);
  });
});
