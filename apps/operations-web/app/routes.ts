import { index, route, type RouteConfig } from '@react-router/dev/routes';
export default [
  index('shell.tsx'),
  route('login', 'features/login/route.tsx'),
  route('execution', 'features/execution/route.tsx'),
  route('media-prices', 'features/media-prices/route.tsx'),
  route('posting', 'features/posting/route.tsx'),
  route('quotations', 'features/quotations/route.tsx'),
  route('onboarding', 'features/onboarding/route.tsx'),
  route('service-visibility', 'features/services/service-visibility/route.tsx'),
  route('service-outbound-risk', 'features/services/service-outbound-risk/route.tsx'),
  route('service-inbound-risk', 'features/services/service-inbound-risk/route.tsx'),
  // Compatibility route for bookmarked links from the former merged workbench.
  route('service-risk', 'features/services/service-risk/route.tsx'),
  route('service-site-audit', 'features/services/service-site-audit/route.tsx'),
  route('service-pilot', 'features/services/service-pilot/route.tsx'),
  route('formal-reports', 'features/services/formal-reports/route.tsx'),
  route('sop', 'features/sop/route.tsx'),
  route('sop/projects/:projectPubId', 'features/sop/project-route.tsx'),
  route('post-analysis', 'features/post-analysis/route.tsx'),
  route('post-analysis/tasks/:taskPubId', 'features/post-analysis/task-route.tsx'),
  route('accounts', 'features/account-governance/accounts-route.tsx'),
  route('browsers', 'features/account-governance/browsers-route.tsx'),
] satisfies RouteConfig;
