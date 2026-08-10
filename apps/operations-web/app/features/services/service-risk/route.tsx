import { ServiceShell } from '../ServiceShell';
import { RiskWorkspace } from './RiskWorkspace';

export default function ServiceRiskRoute() {
  return (
    <ServiceShell
      navId="service-risk"
      title="内容风险核查"
      description="品牌GEO内容生态风险核查：AI 回答中的抹黑拉踩判定与典型案例取证。"
      blurb="看 AI 回答里有没有针对品牌的抹黑拉踩内容、出自哪里、证据是什么。"
    >
      {(session, project) => <RiskWorkspace session={session} project={project} />}
    </ServiceShell>
  );
}
