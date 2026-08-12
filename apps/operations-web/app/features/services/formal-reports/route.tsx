import { ServiceShell } from '../ServiceShell';
import { FormalReportWorkspace } from './FormalReportWorkspace';

export default function FormalReportsRoute() {
  return (
    <ServiceShell
      navId="formal-reports"
      title="正式报告生成"
      description="服务 1–4 的冻结事实、正式制品、审阅签发与审计下载入口。"
      blurb="选择服务与事实窗口，从平台启动可重放的正式报告生产，不需要登录服务器执行脚本。"
    >
      {(session, project) => <FormalReportWorkspace session={session} project={project} />}
    </ServiceShell>
  );
}
