import { GeoBarChart, type ChartDatum } from '@geo/charts';
import { ProductShell, type NavItem } from '@geo/design-system';
import { getHealth } from '@geo/api-client';

/** Shared QA pages keep a way back: the shell sidebar links to the workbench and the sibling matrix. */
const qaNav: NavItem[] = [
  { id: 'workbench', label: '客户工作台', href: '/platform/customer/' },
  { id: 'qa-states', label: '状态语义矩阵', href: '/platform/customer/experience-states' },
  { id: 'qa-performance', label: '性能矩阵', href: '/platform/customer/experience-performance' },
];

const chartData: ChartDatum[] = Array.from({ length: 120 }, (_, index) => ({
  label: `模型维度 ${String(index + 1).padStart(3, '0')}`,
  value: (index * 17) % 101,
  state: 'ready',
}));
const tableRows = Array.from({ length: 500 }, (_, index) => ({
  id: `row_${String(index + 1).padStart(4, '0')}`,
  question: `第 ${index + 1} 个高基数问题：制造业知识库如何验证来源、权限与更新边界？`,
  model: ['豆包', 'DeepSeek', '元宝', 'Kimi'][index % 4],
  value: `${(index * 13) % 101}%`,
}));
const longText = '这是一段用于验证超长证据文本换行、滚动和读屏稳定性的内容。'.repeat(240);

export default function PerformanceMatrix() {
  return (
    <ProductShell
      product="Customer Web"
      title="大表、大图与长文本矩阵"
      description="仅用于验证共享组件在高基数、长内容与三端视口下的稳定性。"
      nav={qaNav}
      currentNavId="qa-performance"
      probe={getHealth}
    >
      {() => (
        <div className="performance-matrix">
          <section className="panel">
            <h2>120 维 ECharts</h2>
            <GeoBarChart title="高基数模型指标" valueSuffix="%" data={chartData} />
          </section>
          <section className="panel table-scroll" tabIndex={0} aria-label="500 行数据表">
            <h2>500 行问题明细</h2>
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>问题</th>
                  <th>模型</th>
                  <th>提及率</th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((row) => (
                  <tr key={row.id}>
                    <td>{row.id}</td>
                    <td>{row.question}</td>
                    <td>{row.model}</td>
                    <td>{row.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className="panel">
            <h2>超长证据文本</h2>
            <div className="long-evidence" tabIndex={0}>
              {longText}
            </div>
          </section>
        </div>
      )}
    </ProductShell>
  );
}
