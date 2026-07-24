import { index, route, type RouteConfig } from '@react-router/dev/routes';
export default [
  index('shell.tsx'),
  route('execution', 'features/execution/route.tsx'),
] satisfies RouteConfig;
