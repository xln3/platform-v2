import type { Page } from './runtime-fixture';

const syntheticHttpRuleIdPattern = /^[a-z][a-z0-9-]{0,63}$/;
const syntheticHttpPathPattern = /^\/api\/v2\/[a-z0-9_./-]+$/;
const syntheticHttpMethods = new Set(['GET', 'POST', 'PATCH', 'DELETE']);
const syntheticHttpCounts = new WeakMap<Page, Record<string, number>>();

export type SyntheticHttpResponseRule = {
  id: string;
  path: string;
  match?: 'exact' | 'prefix';
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  status: number;
  body?: unknown;
  remaining?: number;
  passthrough?: boolean;
};

type SafeSyntheticHttpResponseRule = Required<
  Pick<SyntheticHttpResponseRule, 'id' | 'path' | 'match' | 'method' | 'status' | 'passthrough'>
> &
  Pick<SyntheticHttpResponseRule, 'body' | 'remaining'>;

function validateSyntheticHttpRuleId(id: string): void {
  if (!syntheticHttpRuleIdPattern.test(id)) {
    throw new Error('Invalid synthetic HTTP response rule id');
  }
}

export function projectSyntheticHttpResponseRules(
  rules: readonly SyntheticHttpResponseRule[],
): SafeSyntheticHttpResponseRule[] {
  if (rules.length < 1 || rules.length > 10) {
    throw new Error('Synthetic HTTP response rules must contain between 1 and 10 entries');
  }
  const ids = new Set<string>();
  return rules.map((rule) => {
    validateSyntheticHttpRuleId(rule.id);
    if (ids.has(rule.id)) throw new Error('Synthetic HTTP response rule ids must be unique');
    ids.add(rule.id);
    if (!syntheticHttpPathPattern.test(rule.path)) {
      throw new Error('Invalid synthetic HTTP response path');
    }
    if (
      !Number.isSafeInteger(rule.status) ||
      (rule.status !== 204 && (rule.status < 400 || rule.status > 599))
    ) {
      throw new Error(
        'Synthetic HTTP responses are restricted to explicit 4xx/5xx tests or passthrough 204 successes',
      );
    }
    const passthrough = rule.passthrough ?? false;
    if (
      (rule.status === 204 && (!passthrough || rule.body !== undefined)) ||
      (rule.status !== 204 && passthrough)
    ) {
      throw new Error('Only bodyless 204 responses may use synthetic HTTP passthrough');
    }
    const method = rule.method ?? 'GET';
    if (!syntheticHttpMethods.has(method)) {
      throw new Error('Invalid synthetic HTTP response method');
    }
    if (
      rule.remaining !== undefined &&
      (!Number.isSafeInteger(rule.remaining) || rule.remaining < 1 || rule.remaining > 10)
    ) {
      throw new Error('Invalid synthetic HTTP response limit');
    }
    return {
      id: rule.id,
      path: rule.path,
      match: rule.match ?? 'exact',
      method,
      status: rule.status,
      body: rule.body,
      remaining: rule.remaining,
      passthrough,
    };
  });
}

export async function installSyntheticHttpResponses(
  page: Page,
  rules: readonly SyntheticHttpResponseRule[],
): Promise<void> {
  const safeRules = projectSyntheticHttpResponseRules(rules);
  if (syntheticHttpCounts.has(page)) {
    throw new Error('Synthetic HTTP responses may only be installed once per page');
  }
  const counts = Object.fromEntries(safeRules.map((rule) => [rule.id, 0]));
  const rulesById = new Map(safeRules.map((rule) => [rule.id, rule]));
  syntheticHttpCounts.set(page, counts);
  await page.exposeBinding('__geoSyntheticHttpTake', (_source, id: unknown) => {
    if (typeof id !== 'string' || !rulesById.has(id)) {
      throw new Error('Invalid synthetic HTTP response binding request');
    }
    const rule = rulesById.get(id)!;
    const seen = counts[id] ?? 0;
    if (rule.remaining !== undefined && seen >= rule.remaining) return false;
    counts[id] = seen + 1;
    return true;
  });
  await page.addInitScript((installedRules: SafeSyntheticHttpResponseRule[]) => {
    const nativeFetch = globalThis.fetch.bind(globalThis);
    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const isRequest = typeof Request !== 'undefined' && input instanceof Request;
      const requestLocation =
        typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const pathname = new URL(requestLocation, globalThis.location.origin).pathname;
      const method = (init?.method ?? (isRequest ? input.method : 'GET')).toUpperCase();
      const takeSyntheticResponse = Reflect.get(globalThis, '__geoSyntheticHttpTake') as (
        id: string,
      ) => Promise<boolean>;
      for (const rule of installedRules) {
        const pathMatches =
          rule.match === 'prefix' ? pathname.startsWith(rule.path) : pathname === rule.path;
        if (pathMatches && method === rule.method && (await takeSyntheticResponse(rule.id))) {
          if (rule.passthrough) await nativeFetch(input, init);
          const syntheticResponse = new Response(
            rule.status === 204 ? null : JSON.stringify(rule.body ?? {}),
            {
              // Keep the browser-internal transport successful so Chromium never emits a
              // delayed resource error. The generated client sees only the projected status
              // below, while no raw URL, body or diagnostic is retained by the harness.
              status: 200,
              headers:
                rule.status === 204
                  ? { 'Cache-Control': 'no-store' }
                  : {
                      'Cache-Control': 'no-store',
                      'Content-Type': 'application/json',
                    },
            },
          );
          Object.defineProperties(syntheticResponse, {
            status: { value: rule.status },
            ok: { value: rule.status >= 200 && rule.status < 300 },
            statusText: { value: '' },
          });
          return syntheticResponse;
        }
      }
      return nativeFetch(input, init);
    };
  }, safeRules);
}

export async function syntheticHttpResponseCount(page: Page, id: string): Promise<number> {
  validateSyntheticHttpRuleId(id);
  const value = syntheticHttpCounts.get(page)?.[id];
  return Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : 0;
}
