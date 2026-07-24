import { Links, Meta, Outlet, Scripts, ScrollRestoration } from 'react-router';
import { ValidatedExperienceProvider } from '@geo/design-system';
import { createExperienceLoader } from '@geo/auth';
import '@geo/design-system/styles.css';
const loadExperience = createExperienceLoader({
  tenantPubId: 'tnt_01K0CONTRACTFIXTURE0000000',
  tenantLabel: '云岫智能',
  projectPubId: 'prj_01K0CONTRACTFIXTURE0000000',
  projectLabel: 'Q3 监测报告',
  userPubId: 'usr_01K0CONTRACTFIXTURE0000000',
  userLabel: '沈禾',
  roles: ['analyst', 'reviewer'],
  actorSubject: 'analyst-contract-fixture',
  actorRole: 'analyst',
});
export default function Root() {
  return (
    <html lang="zh-CN">
      <head>
        <title>GEO Platform · Report Studio</title>
        <Meta />
        <Links />
        <link
          rel="icon"
          href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%2317613a'/%3E%3Cpath d='M18 20h28v8H26v8h18v8H18z' fill='white'/%3E%3C/svg%3E"
        />
      </head>
      <body>
        <ValidatedExperienceProvider
          load={loadExperience}
          allowedRoles={['analyst', 'reviewer', 'admin']}
        >
          <Outlet />
        </ValidatedExperienceProvider>
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}
