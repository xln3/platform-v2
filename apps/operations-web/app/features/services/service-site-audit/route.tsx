import { ServiceShell } from '../ServiceShell';
import { SiteAuditWorkspace } from './SiteAuditWorkspace';

export default function ServiceSiteAuditRoute() {
  return (
    <ServiceShell
      navId="service-site-audit"
      title="官网引用能效"
      description="官网内容AI引用能效评估：官网作为信源被 AI 引用的占比与转述准确度。"
      blurb="看 AI 回答引用官网内容的比例，以及引用时转述得准不准。"
    >
      {(session, project) => <SiteAuditWorkspace session={session} project={project} />}
    </ServiceShell>
  );
}
