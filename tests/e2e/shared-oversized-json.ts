import { expect, test } from './runtime-fixture';
import { expectAccessible } from './accessibility';

type OversizedJsonBoundaryOptions = {
  product: string;
  path: string;
  role: 'customer' | 'operator' | 'analyst' | 'reviewer';
  heading: string;
};

export function verifyOversizedJsonBoundary({
  product,
  path,
  role,
  heading,
}: OversizedJsonBoundaryOptions) {
  test(`${product} rejects oversized decoded JSON before parsing or business reads`, async ({
    page,
  }) => {
    await page.addInitScript(
      ({ actorRole }) => {
        localStorage.setItem('geo.session.tenant', 'tnt_oversized_json_safe');
        localStorage.setItem('geo.session.actor', 'subject-oversized-json-safe');
        localStorage.setItem('geo.session.role', actorRole);
        const nativeFetch = globalThis.fetch.bind(globalThis);
        Reflect.set(globalThis, '__geoOversizedIdentityReads', 0);
        globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
          const requestUrl =
            typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
          const pathname = new URL(requestUrl, globalThis.location.origin).pathname;
          if (pathname === '/api/v2/identity/session') {
            Reflect.set(
              globalThis,
              '__geoOversizedIdentityReads',
              Number(Reflect.get(globalThis, '__geoOversizedIdentityReads')) + 1,
            );
            return new Response(
              JSON.stringify({
                tenant_pub_id: 'tnt_oversized_json_safe',
                user_pub_id: 'usr_oversized_json_safe',
                role: actorRole,
                permissions: ['project:read'],
                token: 'Bearer oversized-json-browser-canary',
                profile_path: '/secret/browser/profile/oversized-json-canary',
              }),
              {
                status: 200,
                headers: {
                  'Cache-Control': 'no-store',
                  'Content-Type': 'application/json',
                  'Content-Length': String(25 * 1024 * 1024 + 1),
                },
              },
            );
          }
          return nativeFetch(input, init);
        };
      },
      { actorRole: role },
    );

    await page.goto(path);
    await expect(page.getByRole('alert')).toContainText('加载失败');
    await expect(page.getByRole('button', { name: '重试此区域' })).toBeVisible();
    await expect(page.getByText(heading, { exact: true })).toHaveCount(0);
    expect(
      await page.evaluate(() => Number(Reflect.get(globalThis, '__geoOversizedIdentityReads'))),
    ).toBe(1);
    const surfaces = await page.evaluate(() => ({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: JSON.stringify(localStorage),
      sessionStorage: JSON.stringify(sessionStorage),
      history: JSON.stringify(history.state),
    }));
    expect(JSON.stringify(surfaces)).not.toMatch(
      /oversized-json-browser-canary|\/secret\/browser\/profile\/oversized-json-canary|Bearer/i,
    );
    await expectAccessible(page);
  });

  test(`${product} rejects genuinely gzip-compressed JSON by decoded bytes before business reads`, async ({
    page,
  }) => {
    await page.addInitScript(
      ({ actorRole }) => {
        localStorage.setItem('geo.session.tenant', 'tnt_oversized_gzip_safe');
        localStorage.setItem('geo.session.actor', 'subject-oversized-gzip-safe');
        localStorage.setItem('geo.session.role', actorRole);
        const nativeFetch = globalThis.fetch.bind(globalThis);
        Reflect.set(globalThis, '__geoOversizedGzipIdentityReads', 0);
        Reflect.set(globalThis, '__geoOversizedGzipResponseFacts', null);
        globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
          const outbound = new Request(input, init);
          if (new URL(outbound.url).pathname !== '/api/v2/identity/session') {
            return nativeFetch(outbound);
          }

          Reflect.set(
            globalThis,
            '__geoOversizedGzipIdentityReads',
            Number(Reflect.get(globalThis, '__geoOversizedGzipIdentityReads')) + 1,
          );
          const headers = new Headers(outbound.headers);
          headers.set('X-Geo-E2E-Decoded-Json-Boundary', actorRole);
          const response = await nativeFetch(new Request(outbound, { headers }));
          const decodedBody = await response.arrayBuffer();
          const encodedLength = Number(response.headers.get('content-length'));
          const declaredDecodedLength = Number(response.headers.get('x-geo-e2e-decoded-length'));
          Reflect.set(globalThis, '__geoOversizedGzipResponseFacts', {
            contentEncoding: response.headers.get('content-encoding'),
            encodedBelowBoundary:
              Number.isSafeInteger(encodedLength) &&
              encodedLength > 0 &&
              encodedLength < 25 * 1024 * 1024,
            decodedAboveBoundary: decodedBody.byteLength > 25 * 1024 * 1024,
            decodedLengthMatches:
              Number.isSafeInteger(declaredDecodedLength) &&
              declaredDecodedLength === decodedBody.byteLength,
            status: response.status,
          });
          return new Response(decodedBody, {
            headers: response.headers,
            status: response.status,
            statusText: response.statusText,
          });
        };
      },
      { actorRole: role },
    );

    await page.goto(path);
    await expect(page.getByRole('alert')).toContainText('加载失败');
    await expect(page.getByRole('button', { name: '重试此区域' })).toBeVisible();
    await expect(page.getByText(heading, { exact: true })).toHaveCount(0);
    expect(
      await page.evaluate(() => Number(Reflect.get(globalThis, '__geoOversizedGzipIdentityReads'))),
    ).toBe(1);
    expect(
      await page.evaluate(() => Reflect.get(globalThis, '__geoOversizedGzipResponseFacts')),
    ).toEqual({
      contentEncoding: 'gzip',
      encodedBelowBoundary: true,
      decodedAboveBoundary: true,
      decodedLengthMatches: true,
      status: 200,
    });
    const surfaces = await page.evaluate(() => ({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: JSON.stringify(localStorage),
      sessionStorage: JSON.stringify(sessionStorage),
      history: JSON.stringify(history.state),
    }));
    expect(JSON.stringify(surfaces)).not.toMatch(
      /oversized-gzip-browser-canary|\/secret\/browser\/profile\/oversized-gzip-canary|Bearer/i,
    );
    await expectAccessible(page);
  });
}
