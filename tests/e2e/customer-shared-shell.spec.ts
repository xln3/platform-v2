import { verifySharedShellActions } from './shared-shell-actions';
import { verifyHostileUrlBoundary } from './shared-hostile-url';

verifySharedShellActions({
  product: 'Customer Web',
  path: '/platform/customer/',
  role: 'customer',
  targetLabel: '资料',
  targetSection: 'profile',
});

verifyHostileUrlBoundary({
  product: 'Customer Web',
  path: '/platform/customer/',
  role: 'customer',
  heading: '客户工作台',
  defaultSection: '首页',
});
