import { ServiceShell } from '../ServiceShell';
import { OutboundRiskWorkspace } from './OutboundRiskWorkspace';

export default function ServiceOutboundRiskRoute() {
  return (
    <ServiceShell
      navId="service-outbound-risk"
      title="主动拉踩内容核查"
      description="服务 2 · 对有己方归属证据的已投/拟投内容做发布前合规风险核查。"
      blurb="输入边界以稿件归属和版本证据为准，不从互联网帖子推断客户投放关系。"
    >
      {(_session, project) => <OutboundRiskWorkspace project={project} />}
    </ServiceShell>
  );
}
