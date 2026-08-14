import { getHealth } from '@geo/api-client';
import { ProductShell, StatePanel, useOptionalExperienceContext } from '@geo/design-system';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import type { SessionContext } from '../execution/api';
import { AccountsPage } from './AccountsPage';
import './account-governance.css';

export default function AccountsRoute() {
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
      title="采集账号管理"
      description="手机号 × 平台的地域绑定、额度预算与运行状态账本。"
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId="accounts"
      probe={getHealth}
    >
      {() =>
        !session ? (
          <main className="acct-gov-page">
            <StatePanel state="forbidden" />
          </main>
        ) : (
          <AccountsPage session={session} />
        )
      }
    </ProductShell>
  );
}
