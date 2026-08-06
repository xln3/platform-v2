import { getHealth } from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { ProductShell, StatePanel, useOptionalExperienceContext } from '@geo/design-system';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import { getSopAccess } from './access';
import { SopProjects } from './SopProjects';

export default function SopRoute() {
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const access = getSopAccess(experience?.roles ?? []);
  return (
    <ProductShell
      product="Operations Web"
      title="信源 SOP"
      description="GEO 信源型文章从查询基线到发布后引用归因的全流程工作区。"
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId="sop"
      probe={getHealth}
    >
      {() =>
        !experience || !headers || !access.canRead ? (
          <main className="sop-page">
            <StatePanel state="forbidden" />
          </main>
        ) : (
          <SopProjects headers={headers} canWrite={access.canWrite} />
        )
      }
    </ProductShell>
  );
}
