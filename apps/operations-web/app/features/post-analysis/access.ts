const POST_ANALYSIS_READ_ROLES = new Set(['operator', 'analyst', 'reviewer', 'admin']);
const POST_ANALYSIS_WRITE_ROLES = new Set(['operator', 'analyst', 'admin']);

export function getPostAnalysisAccess(roles: readonly string[]): {
  canRead: boolean;
  canWrite: boolean;
} {
  return {
    canRead: roles.some((role) => POST_ANALYSIS_READ_ROLES.has(role)),
    canWrite: roles.some((role) => POST_ANALYSIS_WRITE_ROLES.has(role)),
  };
}
