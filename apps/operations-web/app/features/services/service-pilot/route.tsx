import { ServiceShell } from '../ServiceShell';
import { PilotWorkspace } from './PilotWorkspace';

export default function ServicePilotRoute() {
  return (
    <ServiceShell
      navId="service-pilot"
      title="试点效果验证"
      description="GEO试点与效果验证：优化前后用同一查询集复测，对比核心指标变化。"
      blurb="用同一组问题在优化前后各测一轮，直接对比提及率、排名等指标的变化。"
    >
      {(session, project) => <PilotWorkspace session={session} project={project} />}
    </ServiceShell>
  );
}
