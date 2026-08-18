import { getHealth } from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { ProductShell, StatePanel, useOptionalExperienceContext } from '@geo/design-system';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import { PostingWorkspace } from './PostingWorkspace';

export default function PostingRoute() {
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const role = experience?.roles.find(
    (candidate): candidate is 'operator' | 'reviewer' | 'admin' =>
      candidate === 'operator' || candidate === 'reviewer' || candidate === 'admin',
  );
  return (
    <ProductShell
      product="Operations Web"
      title="发帖工作台"
      description="平台账号托管、DOCX 发稿配置与逐媒体状态追踪。"
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId="posting"
      probe={getHealth}
    >
      {() =>
        !experience || !headers || !role ? (
          <StatePanel state="forbidden" />
        ) : (
          <PostingWorkspace
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
