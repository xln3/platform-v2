import { ServiceShell } from '../ServiceShell';
import { OutboundRiskWorkspace } from './OutboundRiskWorkspace';

export default function ServiceOutboundRiskRoute() {
  return (
    <ServiceShell
      navId="service-outbound-risk"
      title="主动拉踩内容核查"
      description="服务 2 · 对项目冻结范围内全部 U 信源帖子做实体—关系发现、证据审核与案例冻结。"
      blurb="作者、发布方和委托方归属只影响归因措辞，不是帖子入池门槛；occurrence 分母不会被 URL 去重、抓取失败或无实体结果缩小。"
    >
      {(session, project) => <OutboundRiskWorkspace session={session} project={project} />}
    </ServiceShell>
  );
}
