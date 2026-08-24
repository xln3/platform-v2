import { describe, expect, it } from 'vitest';
import { productionFrontendURL } from './production_browser_topology.mjs';

describe('production browser topology', () => {
  it('targets the customer and operations security-domain ports directly', () => {
    expect(
      productionFrontendURL('https://127.0.0.1:8443', '/platform/customer/?section=services'),
    ).toBe('https://127.0.0.1:8787/platform/customer/?section=services');
    expect(
      productionFrontendURL('https://39.105.175.14:8443', '/platform/operations/?section=overview'),
    ).toBe('https://39.105.175.14:8788/platform/operations/?section=overview');
  });

  it('keeps backend-served applications and isolated candidates on the base origin', () => {
    expect(productionFrontendURL('https://127.0.0.1:8443', '/platform/intelligence/')).toBe(
      'https://127.0.0.1:8443/platform/intelligence/',
    );
    expect(productionFrontendURL('https://127.0.0.1:8443', '/platform/reports/')).toBe(
      'https://127.0.0.1:8443/platform/reports/',
    );
    expect(productionFrontendURL('https://127.0.0.1:8443', '/platform/customer/', true)).toBe(
      'https://127.0.0.1:8443/platform/customer/',
    );
  });
});
