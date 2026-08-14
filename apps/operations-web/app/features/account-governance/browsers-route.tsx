import { getHealth } from '@geo/api-client';
import { ProductShell, StatePanel, useOptionalExperienceContext } from '@geo/design-system';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import type { SessionContext } from '../execution/api';
import { BrowsersPage } from './BrowsersPage';
import './account-governance.css';

export default function BrowsersRoute() {
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const role = experience?.roles.find(
    (candidate): candidate is 'operator' | 'reviewer' | 'admin' =>
      candidate === 'operator' || candidate === 'reviewer' || candidate === 'admin',
  );
  const session: SessionContext | null =
    experience && headers && role
      ? { tenantId: experience.tenantPubId, actorId: experience.userPubId, role, headers }
      : null;
  return (
    <ProductShell
      product="Operations Web"
      title="采集浏览器管理"
      description="常驻浏览器实例实况：开启时长、内存、出口地域与平台绑定。"
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId="browsers"
      probe={getHealth}
    >
      {() =>
        !session ? (
          <main className="acct-gov-page">
            <StatePanel state="forbidden" />
          </main>
        ) : (
          <BrowsersPage session={session} />
        )
      }
    </ProductShell>
  );
}
