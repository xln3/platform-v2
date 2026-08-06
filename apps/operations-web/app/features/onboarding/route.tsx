import { getHealth } from '@geo/api-client';
import { ProductShell, useOptionalExperienceContext } from '@geo/design-system';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import { OnboardingWizard } from './OnboardingWizard';
import './onboarding.css';

export default function OnboardingRoute() {
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const role = experience?.roles.find(
    (candidate): candidate is 'operator' | 'reviewer' | 'admin' =>
      candidate === 'operator' || candidate === 'reviewer' || candidate === 'admin',
  );
  return (
    <ProductShell
      product="Operations Web"
      title="开户向导"
      description="一次性建立客户、项目、资料与监测配置，冻结首版并出回执。"
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId="onboarding"
      probe={getHealth}
    >
      {() =>
        experience && headers && role ? (
          <main className="onboarding-plane">
            <OnboardingWizard
              session={{
                tenantId: experience.tenantPubId,
                actorId: experience.userPubId,
                role,
                headers,
              }}
            />
          </main>
        ) : (
          <main className="onboarding-plane">
            <div className="onboarding-state warning">
              尚未建立运营会话。请通过身份入口登录后再执行开户。
            </div>
          </main>
        )
      }
    </ProductShell>
  );
}
