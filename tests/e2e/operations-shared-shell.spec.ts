import { verifySharedShellActions } from './shared-shell-actions';
import { verifyHostileUrlBoundary } from './shared-hostile-url';
import { verifyOversizedJsonBoundary } from './shared-oversized-json';

verifySharedShellActions({
  product: 'Operations Web',
  path: '/platform/operations/',
  role: 'operator',
  liveNavLabelsWithoutBadges: ['会话健康', '待人工'],
  internalLink: {
    label: '执行与账号',
    href: '/platform/operations/execution',
  },
});

verifyHostileUrlBoundary({
  product: 'Operations Web',
  path: '/platform/operations/',
  role: 'operator',
  heading: '运行总览',
  defaultSection: '总览',
});

verifyOversizedJsonBoundary({
  product: 'Operations Web',
  path: '/platform/operations/',
  role: 'operator',
  heading: '运行总览',
});
