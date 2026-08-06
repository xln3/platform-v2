import { verifySharedShellActions } from './shared-shell-actions';
import { verifyHostileUrlBoundary } from './shared-hostile-url';
import { verifyOversizedJsonBoundary } from './shared-oversized-json';

verifySharedShellActions({
  product: 'Report Studio',
  path: '/platform/reports/',
  role: 'analyst',
  liveNavLabelsWithoutBadges: ['审核发布'],
});

verifyHostileUrlBoundary({
  product: 'Report Studio',
  path: '/platform/reports/',
  role: 'analyst',
  heading: '报告工作室',
  defaultSection: '数据窗口',
});

verifyOversizedJsonBoundary({
  product: 'Report Studio',
  path: '/platform/reports/',
  role: 'analyst',
  heading: '报告工作室',
});
