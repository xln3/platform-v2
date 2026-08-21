import { ServiceShell } from '../ServiceShell';
import { RiskWorkspace } from '../service-risk/RiskWorkspace';

export default function ServiceInboundRiskRoute() {
  return (
    <ServiceShell
      navId="service-inbound-risk"
      title="被拉踩内容核查"
      description="服务 3 · 对相关问答的全部 U 候选页面进行风险判定、事实核查和证据复核。"
      blurb="范围以问答采集实际返回的全部 U 为分母，不只检查 V、最终引用或 W。"
    >
      {(session, project) => <RiskWorkspace session={session} project={project} />}
    </ServiceShell>
  );
}
