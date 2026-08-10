import { ServiceShell } from '../ServiceShell';
import { VisibilityWorkspace } from './VisibilityWorkspace';

export default function ServiceVisibilityRoute() {
  return (
    <ServiceShell
      navId="service-visibility"
      title="品牌GEO评测"
      description="品牌GEO推荐结果评测：品牌在 AI 回答中的提及、排名与竞品对比。"
      blurb="看品牌在各 AI 平台的回答里被提到多少次、排第几、和竞品比表现如何。"
    >
      {(session, project) => <VisibilityWorkspace session={session} project={project} />}
    </ServiceShell>
  );
}
