import { ServiceShell } from '../ServiceShell';
import { RiskWorkspace } from './RiskWorkspace';

export default function ServiceRiskRoute() {
  return (
    <ServiceShell
      navId="service-risk"
      title="内容风险核查"
      description="品牌GEO内容生态风险核查：目标品牌相关的抹黑拉踩判定与典型案例取证。"
      blurb="只看 AI 回答与公开信源里针对目标品牌的风险表述、出处和证据。"
    >
      {(session, project) => <RiskWorkspace session={session} project={project} />}
    </ServiceShell>
  );
}
