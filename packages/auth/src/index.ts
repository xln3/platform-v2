import {
  getIdentitySession,
  type IdentitySessionHeaders,
  type IdentitySessionResponse,
} from '@geo/api-client';
import {
  containsClientSecret,
  type ExperienceContextValue,
  type ExperienceLoadResult,
} from '@geo/design-system';

export type TenantContext = { tenantId: string; userId: string; roles: readonly string[] };
export const hasRole = (context: TenantContext, role: string): boolean =>
  context.roles.includes(role);

type BrowserRole = IdentitySessionResponse['role'];
type ProductRole = ExperienceContextValue['roles'][number];
let validatedRequestHeaders: IdentitySessionHeaders | null = null;

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
  value.length > 0 && value.length <= maxLength && !containsClientSecret(value);
const safeProjectLabel = (value: string | undefined): string =>
  value && isSafeProjectedValue(value, 120) ? value : '未命名项目';
const buildEnvironment = (
  import.meta as ImportMeta & {
    env?: { DEV?: boolean; VITE_ALLOW_CONTRACT_FIXTURES?: string };
  }
).env;
const allowContractFixtures =
  buildEnvironment?.DEV === true || buildEnvironment?.VITE_ALLOW_CONTRACT_FIXTURES === 'true';

function readBrowserHints(fixture: ExperienceFixture): {
  headers: IdentitySessionHeaders;
  explicit: boolean;
  invalid: boolean;
} {
  if (typeof window === 'undefined') {
    return {
      explicit: false,
      invalid: false,
      headers: {
        'X-Tenant-Id': fixture.tenantPubId,
        'X-Actor-Id': fixture.actorSubject,
        'X-Actor-Role': fixture.actorRole,
      },
    };
  }
  const tenant = localStorage.getItem('geo.session.tenant');
  const actor = localStorage.getItem('geo.session.actor');
  const role = localStorage.getItem('geo.session.role');
  const explicit = tenant !== null || actor !== null || role !== null;
  const invalid =
    explicit &&
    (!tenant ||
      !actor ||
      !role ||
      tenant.length > 120 ||
      actor.length > 255 ||
      containsClientSecret(`${tenant ?? ''} ${actor ?? ''} ${role ?? ''}`) ||
      !browserRoles.has(role as BrowserRole));
  if (invalid) {
    localStorage.removeItem('geo.session.tenant');
    localStorage.removeItem('geo.session.actor');
    localStorage.removeItem('geo.session.role');
  }
  if (!tenant || !actor || !role || invalid) {
    return {
      explicit,
      invalid,
      headers: {
        'X-Tenant-Id': fixture.tenantPubId,
        'X-Actor-Id': fixture.actorSubject,
        'X-Actor-Role': fixture.actorRole,
      },
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
  fixture: ExperienceFixture,
): () => Promise<ExperienceLoadResult> {
  return async () => {
    validatedRequestHeaders = null;
    const { headers, explicit, invalid } = readBrowserHints(fixture);
    if (invalid) return { kind: 'forbidden' };
    if (allowContractFixtures && !explicit) {
      return { kind: 'fixture', value: { ...fixture, source: 'contract-fixture' } };
    }
    const result = await getIdentitySession(headers);
    if (result.kind === 'forbidden') {
      return explicit || !allowContractFixtures
        ? { kind: 'forbidden' }
        : { kind: 'fixture', value: { ...fixture, source: 'contract-fixture' } };
    }
    if (result.kind === 'unavailable') {
      return explicit || !allowContractFixtures
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
