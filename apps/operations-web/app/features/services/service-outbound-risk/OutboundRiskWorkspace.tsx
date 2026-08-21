import type { Project } from '../api';

export function OutboundRiskWorkspace({ project }: { project: Project }) {
  const projectQuery = encodeURIComponent(project.pub_id);
  return (
    <>
      <p className="service-note">
        服务 2
        只核查有作者、委托、审批或其他己方归属证据的已投/拟投内容。互联网中看似有利的第三方帖子不会被自动认作客户投放。
      </p>

      <section className="execution-card">
        <div className="section-title">
          <h2>己方内容核查流水线</h2>
          <span>归属确认 → 定稿触发 → 逐字证据 → 人工审核 → 独立交付</span>
        </div>
        <ol className="service-production-steps">
          <li>在信源 SOP 中登记稿件、作者/委托/审批依据；只有明确归属的定稿版本进入本服务。</li>
          <li>
            定稿后由版本化任务检查主动贬低、不当比较和待核事实；证据引用必须能回校验到该稿件版本。
          </li>
          <li>运营人员复核结果并修改稿件；发布执行与公开 URL 证据在发帖工作台留痕。</li>
          <li>服务 2 单独冻结事实、审核和签发，不与服务 3 的外部风险候选混用。</li>
        </ol>
        <div className="actions">
          <a className="button" href={`/platform/operations/sop?project=${projectQuery}`}>
            打开信源 SOP
          </a>
          <a className="button" href={`/platform/operations/posting?project=${projectQuery}`}>
            打开发帖工作台
          </a>
          <a
            className="button"
            href={`/platform/operations/formal-reports?project=${projectQuery}`}
          >
            生成服务 2 报告
          </a>
        </div>
      </section>

      <section className="execution-card">
        <div className="section-title">
          <h2>边界说明</h2>
          <span>与服务 3 独立</span>
        </div>
        <p className="setup-summary">
          本页不读取互联网 U 页面作为“己方内容”。外部页面对目标品牌的拉踩风险请进入服务
          3；两项服务可以复用逐字证据校验能力，但输入、任务、审核、报告和客户交付均保持独立。
        </p>
      </section>
    </>
  );
}
