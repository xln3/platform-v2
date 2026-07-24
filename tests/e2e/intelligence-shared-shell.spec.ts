import { verifySharedShellActions } from './shared-shell-actions';
import { verifyHostileUrlBoundary } from './shared-hostile-url';

verifySharedShellActions({
  product: 'Intelligence Web',
  path: '/platform/intelligence/',
  role: 'reviewer',
  targetLabel: 'Claim 矩阵',
  targetSection: 'claims',
});

verifyHostileUrlBoundary({
  product: 'Intelligence Web',
  path: '/platform/intelligence/',
  role: 'reviewer',
  heading: '证据调查台',
  defaultSection: '案件',
});
