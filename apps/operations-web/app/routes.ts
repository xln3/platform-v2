import { index, route, type RouteConfig } from '@react-router/dev/routes';
export default [
  index('shell.tsx'),
  route('login', 'features/login/route.tsx'),
  route('execution', 'features/execution/route.tsx'),
  route('media-prices', 'features/media-prices/route.tsx'),
  route('onboarding', 'features/onboarding/route.tsx'),
  route('service-visibility', 'features/services/service-visibility/route.tsx'),
  route('service-risk', 'features/services/service-risk/route.tsx'),
  route('service-site-audit', 'features/services/service-site-audit/route.tsx'),
  route('service-pilot', 'features/services/service-pilot/route.tsx'),
  route('sop', 'features/sop/route.tsx'),
  route('sop/projects/:projectPubId', 'features/sop/project-route.tsx'),
  route('post-analysis', 'features/post-analysis/route.tsx'),
  route('post-analysis/tasks/:taskPubId', 'features/post-analysis/task-route.tsx'),
] satisfies RouteConfig;
