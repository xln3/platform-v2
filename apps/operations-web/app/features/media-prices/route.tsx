import { getHealth } from '@geo/api-client';
import { ProductShell, useOptionalExperienceContext } from '@geo/design-system';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import { MediaPrices } from './MediaPrices';

export default function MediaPricesRoute() {
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const role = experience?.roles.find(
    (candidate): candidate is 'operator' | 'reviewer' | 'admin' =>
      candidate === 'operator' || candidate === 'reviewer' || candidate === 'admin',
  );
  return (
    <ProductShell
      product="Operations Web"
      title="媒体比价台"
      description="多平台媒体比价、DOCX 自动发帖配置与逐媒体状态追踪。"
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId="media-prices"
      probe={getHealth}
    >
      {() => (
        <MediaPrices
          session={
            experience && headers && role
              ? {
                  tenantId: experience.tenantPubId,
                  actorId: experience.userPubId,
                  role,
                  headers,
                }
              : undefined
          }
        />
      )}
    </ProductShell>
  );
}
