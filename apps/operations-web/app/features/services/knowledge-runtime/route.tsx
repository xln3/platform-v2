import { ServiceShell } from '../ServiceShell';
import { KnowledgeRuntimeWorkspace } from './KnowledgeRuntimeWorkspace';

export default function KnowledgeRuntimeRoute() {
  return (
    <ServiceShell
      navId="knowledge-runtime"
      title="共享知识判断"
      description="按请求选择已准入模型，核对品牌实体、身份关系与比较资格。"
      blurb="确定性知识与模型假设分开显示；模型选择只影响本次请求，不会改写已发布知识。"
      allowAnalyst
    >
      {(session, project) => <KnowledgeRuntimeWorkspace session={session} project={project} />}
    </ServiceShell>
  );
}
