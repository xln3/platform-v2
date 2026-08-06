import { verifySharedShellActions } from './shared-shell-actions';
import { verifyHostileUrlBoundary } from './shared-hostile-url';
import { verifyOversizedJsonBoundary } from './shared-oversized-json';

verifySharedShellActions({
  product: 'Intelligence Web',
  path: '/platform/intelligence/',
  role: 'reviewer',
  liveNavLabelsWithoutBadges: ['裁决与申诉'],
});

verifyHostileUrlBoundary({
  product: 'Intelligence Web',
  path: '/platform/intelligence/',
  role: 'reviewer',
  heading: '证据调查台',
  defaultSection: '案件',
});

verifyOversizedJsonBoundary({
  product: 'Intelligence Web',
  path: '/platform/intelligence/',
  role: 'reviewer',
  heading: '证据调查台',
});
