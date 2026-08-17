import {
  getIdentitySession,
  type IdentitySessionHeaders,
  type IdentitySessionProjection,
} from '@geo/api-client';
import {
  containsClientSecret,
  containsUnsafeClientControlCharacter,
  identitySessionHintStorageKeys,
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
const projectStateOrder = { active: 0, draft: 1, paused: 2, archived: 3 } as const;
const projectAvailabilityOrder = { active: 0, draft: 0, paused: 1, archived: 2 } as const;
const lastProjectStorageKey = 'geo.preference.last-project';
const validProjectPubId = (value: string | null): value is string =>
  typeof value === 'string' && /^prj_[A-Za-z0-9_-]{1,116}$/.test(value);
const requestedProjectPubId = (): string | null => {
  if (typeof window === 'undefined' || typeof window.location?.href !== 'string') return null;
  const candidate = new URL(window.location.href).searchParams.get('project');
  if (!candidate) return null;
  return validProjectPubId(candidate) ? candidate : '__invalid__';
};
const rememberedProjectPubId = (): string | null => {
  if (typeof window === 'undefined') return null;
  try {
    const candidate = window.localStorage.getItem(lastProjectStorageKey);
    if (validProjectPubId(candidate)) return candidate;
    if (candidate !== null) window.localStorage.removeItem(lastProjectStorageKey);
  } catch {
    // Storage is an optional convenience; authorization always comes from the API project list.
  }
  return null;
};
const rememberProjectPubId = (projectPubId: string): void => {
  if (typeof window === 'undefined' || !validProjectPubId(projectPubId)) return;
  try {
    window.localStorage.setItem(lastProjectStorageKey, projectPubId);
  } catch {
    // A blocked/full browser store must not prevent project access.
  }
};
const allowContractFixtures =
  import.meta.env.DEV || import.meta.env.VITE_ALLOW_CONTRACT_FIXTURES === 'true';
const sessionHintKeys = identitySessionHintStorageKeys;
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
    // Cookie-only sessions carry no localStorage hints; derive the identity triple from the
    // validated session so mutation guards can fingerprint the actor. In native_session mode
    // the API authenticates by cookie and ignores these headers, so projection is safe.
    validatedRequestHeaders = {
      'X-Tenant-Id': headers['X-Tenant-Id'] ?? result.session.tenant_pub_id,
      'X-Actor-Id': headers['X-Actor-Id'] ?? result.session.user_pub_id,
      'X-Actor-Role': headers['X-Actor-Role'] ?? result.session.role,
    };
    const projects = [...result.projects.data].sort((left, right) => {
      const availabilityDelta =
        (projectAvailabilityOrder[left.state] ?? Number.MAX_SAFE_INTEGER) -
        (projectAvailabilityOrder[right.state] ?? Number.MAX_SAFE_INTEGER);
      if (availabilityDelta !== 0) return availabilityDelta;
      const updatedDelta = Date.parse(right.updated_at) - Date.parse(left.updated_at);
      if (Number.isFinite(updatedDelta) && updatedDelta !== 0) return updatedDelta;
      const stateDelta =
        (projectStateOrder[left.state] ?? Number.MAX_SAFE_INTEGER) -
        (projectStateOrder[right.state] ?? Number.MAX_SAFE_INTEGER);
      if (stateDelta !== 0) return stateDelta;
      return left.pub_id.localeCompare(right.pub_id);
    });
    const requestedProject = requestedProjectPubId();
    if (requestedProject === '__invalid__') return { kind: 'forbidden' };
    const rememberedProject = requestedProject ? null : rememberedProjectPubId();
    const project =
      projects.find((candidate) => candidate.pub_id === (requestedProject ?? rememberedProject)) ??
      projects[0];
    if (requestedProject && project?.pub_id !== requestedProject) return { kind: 'forbidden' };
    if (rememberedProject && project?.pub_id !== rememberedProject) {
      try {
        window.localStorage.removeItem(lastProjectStorageKey);
      } catch {
        // Invalid stale preference is already ignored.
      }
    }
    if (project) rememberProjectPubId(project.pub_id);
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
        projects: projects.map((candidate) => ({
          projectPubId: candidate.pub_id,
          projectLabel: safeProjectLabel(candidate.name),
          state: candidate.state,
        })),
        source: 'live',
      },
    };
  };
}
