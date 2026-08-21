import { ServiceShell } from '../ServiceShell';
import { FormalReportWorkspace } from './FormalReportWorkspace';

export default function FormalReportsRoute() {
  return (
    <ServiceShell
      navId="formal-reports"
      title="正式报告生成"
      description="服务 1–4 的冻结事实、正式制品、审阅签发与审计下载入口。"
      blurb="按新五项服务选择事实窗口，每项服务独立生成 DOCX 与证据产物。"
    >
      {(session, project) => <FormalReportWorkspace session={session} project={project} />}
    </ServiceShell>
  );
}
