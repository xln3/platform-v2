import type { CustomerFiveService } from '@geo/api-client';

type DeliveryStep = {
  index: string;
  title: string;
  description: string;
  output: string;
};

type GanttRow = {
  task: string;
  detail: string;
  start: number;
  end: number;
  tone: 'blue' | 'cyan' | 'violet' | 'amber' | 'green';
};

const deliverySteps: readonly DeliveryStep[] = [
  {
    index: '01',
    title: '业务问题建模',
    description: '把品牌、产品、场景与客户决策问题拆成可冻结的问题矩阵。',
    output: '范围清单 · 统计口径',
  },
  {
    index: '02',
    title: '多模型真实采样',
    description: '在已授权的平台、地域与回答模式下重复采集，保留运行条件。',
    output: '完整回答 · 平台凭证',
  },
  {
    index: '03',
    title: '认知与信源诊断',
    description: '定位品牌是否出现、首次出现顺序、竞品共现和引用来源。',
    output: '指标明细 · 证据锚点',
  },
  {
    index: '04',
    title: '内容优化执行',
    description: '按官网事实、第三方信源和高价值场景形成分阶段内容动作。',
    output: '优化清单 · 发布台账',
  },
  {
    index: '05',
    title: '同口径复测验收',
    description: '沿用冻结问题和统计方法复测，区分观察变化与因果结论。',
    output: '前后对比 · 验收报告',
  },
];

const ganttPhases = ['准备期', '基线周', '优化月 1', '优化月 2', '优化月 3', '观察期'];

const ganttRows: readonly GanttRow[] = [
  {
    task: '范围与验收口径',
    detail: '冻结问题、平台、地域、重复次数和指标分母',
    start: 0,
    end: 1,
    tone: 'blue',
  },
  {
    task: '基线采样与证据固化',
    detail: '真实回答、引用、截图、运行条件与异常记录',
    start: 1,
    end: 2,
    tone: 'cyan',
  },
  {
    task: '诊断与行动方案',
    detail: '认知偏差、竞争位置、信源缺口与优先动作',
    start: 1,
    end: 3,
    tone: 'violet',
  },
  {
    task: '官网与外部信源优化',
    detail: '事实表达、结构化内容、场景文章与发布台账',
    start: 2,
    end: 5,
    tone: 'amber',
  },
  {
    task: '阶段复测与校准',
    detail: '沿用冻结问题观察变化，按月复盘优化方向',
    start: 3,
    end: 5,
    tone: 'blue',
  },
  {
    task: '验收与长效监测',
    detail: '效果报告、证据归档与下一周期建议',
    start: 5,
    end: 6,
    tone: 'green',
  },
];

const milestones = [
  { code: 'M0', title: '口径冻结', description: '范围与验收标准双方确认' },
  { code: 'M1', title: '基线完成', description: '交付首轮诊断与证据索引' },
  { code: 'M2', title: '方案确认', description: '优化动作、责任人与验证方式确认' },
  { code: 'M3', title: '阶段复测', description: '同口径观察变化并校准动作' },
  { code: 'M4', title: '项目验收', description: '效果报告与证据包归档' },
] as const;

function Methodology() {
  return (
    <section className="delivery-showcase-section" aria-labelledby="delivery-method-title">
      <div className="delivery-section-heading">
        <div>
          <span>DELIVERY METHOD</span>
          <h3 id="delivery-method-title">从业务问题到可验证结果</h3>
        </div>
        <p>每一步都对应客户可查看的产物，结果可以回到具体问题、回答和信源。</p>
      </div>
      <ol className="delivery-method-grid">
        {deliverySteps.map((step) => (
          <li key={step.index}>
            <div className="delivery-step-number">{step.index}</div>
            <h4>{step.title}</h4>
            <p>{step.description}</p>
            <span>{step.output}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function CurrentProjectSnapshot({
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
    <section className="delivery-showcase-section" aria-labelledby="current-project-title">
      <div className="delivery-section-heading">
        <div>
          <span>CURRENT PROJECT ONLY</span>
          <h3 id="current-project-title">当前项目成果展示</h3>
        </div>
        <p>本区域只使用当前登录项目的授权投影，不加载、不引用其他客户名称、数据或交付物。</p>
      </div>
      <article className="current-project-card">
        <div className="current-project-main">
          <span>当前授权项目</span>
          <h4>{projectLabel}</h4>
          <p>
            已开通服务及其结果均来自当前项目接口。服务未开通、数据不可观察或交付物未签发时，页面保持明确状态，不用其他项目案例补位。
          </p>
          <div className="current-project-services" aria-label="当前项目已开通服务">
            {activeServices.length ? (
              activeServices.map((service) => (
                <span key={service.serviceCode}>
                  {service.serviceNumber} · {service.name}
                </span>
              ))
            ) : (
              <span>当前没有处于有效授权期的服务</span>
            )}
          </div>
        </div>
        <dl className="current-project-metrics">
          <div>
            <dt>有效授权服务</dt>
            <dd>
              {activeServices.length} / {services.length}
            </dd>
          </div>
          <div>
            <dt>已签发交付物</dt>
            <dd>{signedDeliveries.length}</dd>
          </div>
          <div>
            <dt>已完成真实回答</dt>
            <dd>{answerCount === null ? '—' : answerCount.toLocaleString('zh-CN')}</dd>
          </div>
        </dl>
        <aside className="current-project-privacy">
          <strong>项目隔离规则</strong>
          <ul>
            <li>项目名称来自当前认证会话</li>
            <li>服务状态与结果来自当前项目授权接口</li>
            <li>共享前端不内置其他客户名称或案例数据</li>
          </ul>
        </aside>
      </article>
    </section>
  );
}

function DeliveryGantt() {
  return (
    <section className="delivery-showcase-section" aria-labelledby="delivery-gantt-title">
      <div className="delivery-section-heading">
        <div>
          <span>REFERENCE SCHEDULE</span>
          <h3 id="delivery-gantt-title">标准 90 天实施甘特图</h3>
        </div>
        <p>用于说明工作依赖与建议节奏；实际日期、完成状态和验收点以已确认项目计划为准。</p>
      </div>
      <div className="delivery-gantt-wrap">
        <table className="delivery-gantt">
          <caption className="visually-hidden">GEO 项目标准 90 天参考实施计划</caption>
          <thead>
            <tr>
              <th scope="col">工作流</th>
              {ganttPhases.map((phase) => (
                <th scope="col" key={phase}>
                  {phase}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ganttRows.map((row) => (
              <tr key={row.task} aria-label={`${row.task}：${row.detail}`}>
                <th scope="row">
                  <strong>{row.task}</strong>
                  <span>{row.detail}</span>
                </th>
                {ganttPhases.map((phase, phaseIndex) => {
                  const active = phaseIndex >= row.start && phaseIndex < row.end;
                  const position =
                    phaseIndex === row.start
                      ? 'start'
                      : phaseIndex === row.end - 1
                        ? 'end'
                        : 'middle';
                  return (
                    <td key={phase}>
                      {active ? (
                        <span
                          aria-hidden="true"
                          className={`gantt-bar gantt-${row.tone} gantt-${position}`}
                        />
                      ) : null}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ol className="delivery-milestones" aria-label="项目里程碑">
        {milestones.map((milestone) => (
          <li key={milestone.code}>
            <span>{milestone.code}</span>
            <div>
              <strong>{milestone.title}</strong>
              <p>{milestone.description}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function CustomerProjectShowcase({
  projectLabel,
  services,
}: {
  projectLabel: string;
  services: readonly CustomerFiveService[];
}) {
  return (
    <div className="customer-project-showcase">
      <section className="delivery-blueprint-hero" aria-labelledby="delivery-blueprint-title">
        <div className="delivery-blueprint-copy">
          <span>PROJECT DELIVERY BLUEPRINT</span>
          <h2 id="delivery-blueprint-title">看得见过程，也经得起复核</h2>
          <p>
            客户工作台把当前项目的服务范围、阶段动作、验收节点和证据交付放在同一张项目蓝图中。
            每个结论都能回到真实回答与信源，每个优化动作都有复测口径。
          </p>
        </div>
        <dl className="delivery-blueprint-stats">
          <div>
            <dd>5</dd>
            <dt>项独立服务</dt>
          </div>
          <div>
            <dd>3 层</dd>
            <dt>回答·引用·原文</dt>
          </div>
          <div>
            <dd>90 天</dd>
            <dt>参考实施节奏</dt>
          </div>
          <div>
            <dd>4 类</dd>
            <dt>成套交付物</dt>
          </div>
        </dl>
      </section>
      <Methodology />
      <CurrentProjectSnapshot projectLabel={projectLabel} services={services} />
      <DeliveryGantt />
      <aside className="delivery-trust-strip" aria-label="结果可信边界">
        <strong>结果可信边界</strong>
        <span>分子与分母同时展示</span>
        <span>数据窗口与样本范围可见</span>
        <span>不把缺失数据记为零</span>
        <span>未审批材料不标记为正式交付</span>
      </aside>
    </div>
  );
}
