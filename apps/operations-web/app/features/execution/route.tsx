import { ExecutionControlPlane } from './ExecutionControlPlane';
import { useOptionalExperienceContext } from '@geo/design-system';
import { getValidatedIdentityHeaders } from '@geo/auth';

export default function ExecutionRoute() {
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const role = experience?.roles.find(
    (candidate): candidate is 'operator' | 'reviewer' | 'admin' =>
      candidate === 'operator' || candidate === 'reviewer' || candidate === 'admin',
  );
  if (!experience || !headers || !role) {
    return (
      <main className="execution-plane">
        <div className="execution-state warning">
          尚未建立运营会话。请通过身份入口登录；页面不会把账号秘密写入浏览器存储。
        </div>
      </main>
    );
  }
  return (
    <ExecutionControlPlane
      session={{
        tenantId: experience.tenantPubId,
        actorId: experience.userPubId,
        role,
        headers,
      }}
    />
  );
}
