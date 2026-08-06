import {
  getIdentitySession,
  type IdentitySessionHeaders,
  type IdentitySessionProjection,
} from '@geo/api-client';
import {
  containsClientSecret,
  containsUnsafeClientControlCharacter,
  scrubClientStorage,
  type ExperienceContextValue,
  type ExperienceLoadResult,
} from '@geo/design-system';

export type TenantContext = { tenantId: string; userId: string; roles: readonly string[] };
export const hasRole = (context: TenantContext, role: string): boolean =>
  context.roles.includes(role);

type BrowserRole = IdentitySessionProjection['role'];
type ProductRole = ExperienceContextValue['roles'][number];
let validatedRequestHeaders: IdentitySessionHeaders | null = null;
let experienceLoadGeneration = 0;

/**
 * Returns only the already validated non-secret S01 request projection. It is memory-only and
 * unavailable for contract fixtures, failed sessions and workers.
 */
export function getValidatedIdentityHeaders(): IdentitySessionHeaders | null {
  return validatedRequestHeaders ? { ...validatedRequestHeaders } : null;
}

export type ExperienceFixture = ExperienceContextValue & {
  actorSubject: string;
  actorRole: BrowserRole;
};

const productRoles = new Set<ProductRole>(['customer', 'operator', 'analyst', 'reviewer', 'admin']);
const browserRoles = new Set<BrowserRole>([
  'customer',
  'operator',
  'analyst',
  'reviewer',
  'admin',
  'worker',
]);
const safeLabel = (prefix: string, pubId: string) => `${prefix} · ${pubId.slice(-6)}`;
const isSafeProjectedValue = (value: string, maxLength: number): boolean =>
  value.length > 0 &&
  value.length <= maxLength &&
  !containsUnsafeClientControlCharacter(value) &&
  !containsClientSecret(value);
const safeProjectLabel = (value: string | undefined): string =>
  value && isSafeProjectedValue(value, 120) ? value : '未命名项目';
const allowContractFixtures =
  import.meta.env.DEV || import.meta.env.VITE_ALLOW_CONTRACT_FIXTURES === 'true';
const sessionHintKeys = ['geo.session.tenant', 'geo.session.actor', 'geo.session.role'] as const;
const sessionHintKeySet = new Set<string>(sessionHintKeys);

function readBrowserHints(fixture?: ExperienceFixture): {
  headers: IdentitySessionHeaders | null;
  explicit: boolean;
  invalid: boolean;
} {
  if (typeof window === 'undefined') {
    return {
      explicit: false,
      invalid: false,
      headers: fixture
        ? {
            'X-Tenant-Id': fixture.tenantPubId,
            'X-Actor-Id': fixture.actorSubject,
            'X-Actor-Role': fixture.actorRole,
          }
        : null,
    };
  }
  const explicit = sessionHintKeys.some((key) => localStorage.getItem(key) !== null);
  const localStorageScrub = scrubClientStorage(localStorage, sessionHintKeySet);
  scrubClientStorage(sessionStorage);
  if (!allowContractFixtures) {
    // Production human identity is cookie/provider-owned. Never forward browser-controlled
    // actor claims, even when a backend currently ignores them.
    for (const key of sessionHintKeys) localStorage.removeItem(key);
    return { explicit: false, invalid: false, headers: {} };
  }
  const tenant = localStorage.getItem('geo.session.tenant');
  const actor = localStorage.getItem('geo.session.actor');
  const role = localStorage.getItem('geo.session.role');
  const invalid =
    localStorageScrub.clearedOversizedStorage ||
    (explicit &&
      (localStorageScrub.removedRequiredHint ||
        !tenant ||
        !actor ||
        !role ||
        tenant.length > 120 ||
        actor.length > 255 ||
        containsUnsafeClientControlCharacter(`${tenant ?? ''}${actor ?? ''}${role ?? ''}`) ||
        containsClientSecret(`${tenant ?? ''} ${actor ?? ''} ${role ?? ''}`) ||
        !browserRoles.has(role as BrowserRole)));
  if (invalid) {
    for (const key of sessionHintKeys) localStorage.removeItem(key);
  }
  if (!tenant || !actor || !role || invalid) {
    return {
      explicit,
      invalid,
      headers: fixture
        ? {
            'X-Tenant-Id': fixture.tenantPubId,
            'X-Actor-Id': fixture.actorSubject,
            'X-Actor-Role': fixture.actorRole,
          }
        : null,
    };
  }
  return {
    explicit,
    invalid: false,
    headers: {
      'X-Tenant-Id': tenant.slice(0, 120),
      'X-Actor-Id': actor.slice(0, 255),
      'X-Actor-Role': role.slice(0, 40),
    },
  };
}

export function createExperienceLoader(
  fixture?: ExperienceFixture,
): () => Promise<ExperienceLoadResult> {
  return async () => {
    const loadGeneration = ++experienceLoadGeneration;
    validatedRequestHeaders = null;
    const browserHints = readBrowserHints(fixture);
    let { headers } = browserHints;
    const { explicit, invalid } = browserHints;
    if (invalid) return { kind: 'forbidden' };
    if (allowContractFixtures && fixture && !explicit) {
      return { kind: 'fixture', value: { ...fixture, source: 'contract-fixture' } };
    }
    headers ??= {};
    const result = await getIdentitySession(headers);
    if (loadGeneration !== experienceLoadGeneration) return { kind: 'unavailable' };
    if (result.kind === 'forbidden') {
      return explicit || !allowContractFixtures
        ? { kind: 'forbidden' }
        : !fixture
          ? { kind: 'forbidden' }
          : { kind: 'fixture', value: { ...fixture, source: 'contract-fixture' } };
    }
    if (result.kind === 'unavailable') {
      return explicit || !allowContractFixtures
        ? { kind: 'unavailable' }
        : !fixture
          ? { kind: 'unavailable' }
          : { kind: 'fixture', value: { ...fixture, source: 'contract-fixture' } };
    }
    if (!productRoles.has(result.session.role as ProductRole)) return { kind: 'forbidden' };
    if (
      !isSafeProjectedValue(result.session.tenant_pub_id, 120) ||
      !isSafeProjectedValue(result.session.user_pub_id, 255)
    ) {
      return { kind: 'forbidden' };
    }
    validatedRequestHeaders = { ...headers };
    const project = result.projects.data[0];
    const projectPubId = project && isSafeProjectedValue(project.pub_id, 120) ? project.pub_id : '';
    return {
      kind: 'ready',
      value: {
        tenantPubId: result.session.tenant_pub_id,
        tenantLabel: safeLabel('租户', result.session.tenant_pub_id),
        projectPubId,
        projectLabel: project ? safeProjectLabel(project.name) : '暂无可用项目',
        userPubId: result.session.user_pub_id,
        userLabel: safeLabel('用户', result.session.user_pub_id),
        roles: [result.session.role as ProductRole],
        source: 'live',
      },
    };
  };
}
