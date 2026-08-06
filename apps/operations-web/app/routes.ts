import { index, route, type RouteConfig } from '@react-router/dev/routes';
export default [
  index('shell.tsx'),
  route('login', 'features/login/route.tsx'),
  route('execution', 'features/execution/route.tsx'),
  route('media-prices', 'features/media-prices/route.tsx'),
  route('onboarding', 'features/onboarding/route.tsx'),
  route('sop', 'features/sop/route.tsx'),
  route('sop/projects/:projectPubId', 'features/sop/project-route.tsx'),
] satisfies RouteConfig;
