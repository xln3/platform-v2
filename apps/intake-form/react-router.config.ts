import type { Config } from '@react-router/dev/config';

const e2eBuild = process.env.GEO_E2E_BUILD === '1';
const releaseBuild = process.env.GEO_FRONTEND_RELEASE_BUILD === '1';

if (e2eBuild && releaseBuild) {
  throw new Error('GEO_E2E_BUILD and GEO_FRONTEND_RELEASE_BUILD are mutually exclusive');
}

export default {
  ssr: false,
  basename: '/platform/intake-form/',
  buildDirectory: releaseBuild ? 'build-release' : e2eBuild ? 'build-e2e' : 'build',
} satisfies Config;
