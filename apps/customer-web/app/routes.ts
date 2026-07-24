import { index, route, type RouteConfig } from '@react-router/dev/routes';
export default [
  index('shell.tsx'),
  route('experience-states', 'state-matrix.tsx'),
  route('experience-performance', 'performance-matrix.tsx'),
] satisfies RouteConfig;
