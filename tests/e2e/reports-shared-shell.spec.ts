import { verifySharedShellActions } from './shared-shell-actions';
import { verifyHostileUrlBoundary } from './shared-hostile-url';

verifySharedShellActions({
  product: 'Report Studio',
  path: '/platform/reports/',
  role: 'analyst',
  targetLabel: 'KPI Trace',
  targetSection: 'trace',
});

verifyHostileUrlBoundary({
  product: 'Report Studio',
  path: '/platform/reports/',
  role: 'analyst',
  heading: '报告工作室',
  defaultSection: '数据窗口',
});
