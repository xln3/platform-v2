import { getHealth } from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { ProductShell, StatePanel, useOptionalExperienceContext } from '@geo/design-system';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import { getPostAnalysisAccess } from './access';
import { PostAnalysisTasks } from './PostAnalysisTasks';

export default function PostAnalysisRoute() {
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const access = getPostAnalysisAccess(experience?.roles ?? []);
  return (
    <ProductShell
      product="Operations Web"
      title="帖子分析"
      description="信源帖子取证分析：抓取留证、GEO 判定、拉踩识别与事实核验。"
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId="post-analysis"
      probe={getHealth}
    >
      {() =>
        !experience || !headers || !access.canRead ? (
          <main className="pa-page">
            <StatePanel state="forbidden" />
          </main>
        ) : (
          <PostAnalysisTasks headers={headers} canWrite={access.canWrite} />
        )
      }
    </ProductShell>
  );
}
