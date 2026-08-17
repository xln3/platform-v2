import { verifySharedShellActions } from './shared-shell-actions';
import { verifyHostileUrlBoundary } from './shared-hostile-url';
import { verifyOversizedJsonBoundary } from './shared-oversized-json';

verifySharedShellActions({
  product: 'Customer Web',
  path: '/platform/customer/',
  role: 'customer',
  liveNavLabelsWithoutBadges: ['平台账号'],
});

verifyHostileUrlBoundary({
  product: 'Customer Web',
  path: '/platform/customer/',
  role: 'customer',
  heading: '客户工作台',
  defaultSection: '经营总览',
});

verifyOversizedJsonBoundary({
  product: 'Customer Web',
  path: '/platform/customer/',
  role: 'customer',
  heading: '客户工作台',
});
