import { verifySharedShellActions } from './shared-shell-actions';
import { verifyHostileUrlBoundary } from './shared-hostile-url';

verifySharedShellActions({
  product: 'Operations Web',
  path: '/platform/operations/',
  role: 'operator',
  targetLabel: '会话健康',
  targetSection: 'sessions',
});

verifyHostileUrlBoundary({
  product: 'Operations Web',
  path: '/platform/operations/',
  role: 'operator',
  heading: '运行总览',
  defaultSection: '总览',
});
