import { useParams } from 'react-router';
import { getHealth } from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { ProductShell, StatePanel, useOptionalExperienceContext } from '@geo/design-system';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import { getSopAccess } from './access';
import { SopWorkspace } from './SopWorkspace';

export default function SopProjectRoute() {
  const { projectPubId } = useParams();
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const access = getSopAccess(experience?.roles ?? []);
  return (
    <ProductShell
      product="Operations Web"
      title="信源 SOP 项目"
      description="逐阶段监测真实数据，并在同一工作区完成受控人工操作。"
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId="sop"
      probe={getHealth}
    >
      {() =>
        !projectPubId || !experience || !headers || !access.canRead ? (
          <main className="sop-page">
            <StatePanel state="forbidden" />
          </main>
        ) : (
          <SopWorkspace projectPubId={projectPubId} headers={headers} canWrite={access.canWrite} />
        )
      }
    </ProductShell>
  );
}
