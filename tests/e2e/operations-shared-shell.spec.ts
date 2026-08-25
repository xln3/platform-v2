import { verifySharedShellActions } from './shared-shell-actions';
import { verifyHostileUrlBoundary } from './shared-hostile-url';
import { verifyOversizedJsonBoundary } from './shared-oversized-json';

verifySharedShellActions({
  product: 'Operations Web',
  path: '/platform/operations/',
  role: 'operator',
  liveNavLabelsWithoutBadges: ['会话健康', '人工接管'],
  internalLink: {
    label: '执行与账号',
    href: '/platform/operations/execution',
    projectAware: false,
  },
});

verifyHostileUrlBoundary({
  product: 'Operations Web',
  path: '/platform/operations/',
  role: 'operator',
  heading: '项目商务总览',
  defaultSection: '项目与商务总览',
});

verifyOversizedJsonBoundary({
  product: 'Operations Web',
  path: '/platform/operations/',
  role: 'operator',
  heading: '项目商务总览',
  failureSurface: 'login',
});
