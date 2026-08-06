const SOP_READ_ROLES = new Set(['operator', 'analyst', 'reviewer', 'admin']);
const SOP_WRITE_ROLES = new Set(['operator', 'analyst', 'admin']);

export function getSopAccess(roles: readonly string[]): {
  canRead: boolean;
  canWrite: boolean;
} {
  return {
    canRead: roles.some((role) => SOP_READ_ROLES.has(role)),
    canWrite: roles.some((role) => SOP_WRITE_ROLES.has(role)),
  };
}
