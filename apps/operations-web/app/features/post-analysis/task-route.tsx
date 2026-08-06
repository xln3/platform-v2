import { useParams } from 'react-router';
import { getHealth } from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { ProductShell, StatePanel, useOptionalExperienceContext } from '@geo/design-system';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import { getPostAnalysisAccess } from './access';
import { PostAnalysisTaskDetail } from './PostAnalysisTaskDetail';

export default function PostAnalysisTaskRoute() {
  const { taskPubId } = useParams();
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const access = getPostAnalysisAccess(experience?.roles ?? []);
  return (
    <ProductShell
      product="Operations Web"
      title="帖子分析任务"
      description="逐条目的取证状态、GEO 判定、拉踩与事实核验明细。"
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId="post-analysis"
      probe={getHealth}
    >
      {() =>
        !taskPubId || !experience || !headers || !access.canRead ? (
          <main className="pa-page">
            <StatePanel state="forbidden" />
          </main>
        ) : (
          <PostAnalysisTaskDetail taskPubId={taskPubId} headers={headers} />
        )
      }
    </ProductShell>
  );
}
