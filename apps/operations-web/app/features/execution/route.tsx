import { getHealth } from '@geo/api-client';
import { ProductShell, useOptionalExperienceContext } from '@geo/design-system';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import { ExecutionControlPlane } from './ExecutionControlPlane';

export default function ExecutionRoute() {
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const role = experience?.roles.find(
    (candidate): candidate is 'operator' | 'reviewer' | 'admin' =>
      candidate === 'operator' || candidate === 'reviewer' || candidate === 'admin',
  );
  return (
    <ProductShell
      product="Operations Web"
      title="执行与账号"
      description="所有控制经 FastAPI 与工作流 Signal；页面只展示掩码和无秘密摘要。"
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId="execution"
      probe={getHealth}
    >
      {() =>
        !experience || !headers || !role ? (
          <main className="execution-plane">
            <div className="execution-state warning">
              尚未建立运营会话。请通过身份入口登录；页面不会把账号秘密写入浏览器存储。
            </div>
          </main>
        ) : (
          <ExecutionControlPlane
            session={{
              tenantId: experience.tenantPubId,
              actorId: experience.userPubId,
              role,
              headers,
            }}
          />
        )
      }
    </ProductShell>
  );
}
