import { ServiceShell } from '../ServiceShell';
import { RiskWorkspace } from './RiskWorkspace';

export default function ServiceRiskRoute() {
  return (
    <ServiceShell
      navId="service-inbound-risk"
      title="被拉踩内容核查"
      description="旧地址兼容：检查相关问答全部 U 页面中的目标品牌风险与证据。"
      blurb="该地址已归入服务 3；服务 2 的己方内容核查使用独立工作台。"
    >
      {(session, project) => <RiskWorkspace session={session} project={project} />}
    </ServiceShell>
  );
}
