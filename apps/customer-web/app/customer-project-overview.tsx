import type { CustomerFiveService } from '@geo/api-client';

export function CustomerProjectOverview({
  projectLabel,
  services,
}: {
  projectLabel: string;
  services: readonly CustomerFiveService[];
}) {
  const activeServices = services.filter((service) => service.entitlementState === 'active');
  const signedDeliveries = activeServices.filter((service) => service.latestDelivery !== null);
  const answerCount =
    activeServices.find((service) => service.serviceNumber === 1)?.summary?.answerCount ?? null;

  return (
    <div className="workspace-stack">
      <section className="panel">
        <span className="overline">Current customer project</span>
        <h2>{projectLabel}</h2>
        <p className="panel-subtitle">
          本页只汇总当前登录项目的授权范围、已有结果和正式交付物；具体评测指标归入对应服务页。
        </p>
      </section>
      <div className="source-grid">
        <article>
          <span className="overline">服务范围</span>
          <h3>
            {activeServices.length} / {services.length} 项服务已开通
          </h3>
          <p className="panel-subtitle">
            {activeServices.length
              ? activeServices
                  .map((service) => `${service.serviceNumber} · ${service.name}`)
                  .join('、')
              : '当前没有处于有效授权期的服务。'}
          </p>
          <a href="?section=services">查看服务总览</a>
        </article>
        <article>
          <span className="overline">基础评测</span>
          <h3>
            {answerCount === null
              ? '尚无可交付数据'
              : `${answerCount.toLocaleString('zh-CN')} 条真实回答`}
          </h3>
          <p className="panel-subtitle">AI 推荐、排名、竞品与信源指标统一在服务 1 中查看。</p>
          <a href="?section=service-1">查看 AI 推荐排名测试</a>
        </article>
        <article>
          <span className="overline">正式交付</span>
          <h3>{signedDeliveries.length} 份已签发交付物</h3>
          <p className="panel-subtitle">未签发材料不会作为正式报告展示，报告与原始证据分别归档。</p>
          <a href="?section=reports">查看报告</a>
        </article>
      </div>
    </div>
  );
}
