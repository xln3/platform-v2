import { useEffect, useId, useRef, useState } from 'react';

export type ChartDatum = {
  label: string;
  value: number | null;
  state: 'ready' | 'real-zero' | 'insufficient' | 'failed';
};

export function GeoBarChart({
  title,
  data,
  valueSuffix = '',
}: {
  title: string;
  data: ChartDatum[];
  valueSuffix?: string;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const tableId = useId();
  const [rendered, setRendered] = useState(false);
  useEffect(() => {
    let disposed = false;
    let dispose: (() => void) | undefined;
    void import('echarts/core')
      .then(async ({ init, use }) => {
        const [{ BarChart }, { GridComponent, TooltipComponent }, { CanvasRenderer }] =
          await Promise.all([
            import('echarts/charts'),
            import('echarts/components'),
            import('echarts/renderers'),
          ]);
        if (disposed || !canvasRef.current) return;
        use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);
        const chart = init(canvasRef.current);
        dispose = () => chart.dispose();
        chart.setOption({
          animation: false,
          grid: { left: 92, right: 28, top: 16, bottom: 28 },
          xAxis: { type: 'value', min: 0, max: 100 },
          yAxis: { type: 'category', data: data.map((item) => item.label) },
          tooltip: {
            trigger: 'axis',
            valueFormatter: (value: unknown) => `${String(value)}${valueSuffix}`,
          },
          series: [
            {
              type: 'bar',
              data: data.map((item) =>
                item.state === 'ready' || item.state === 'real-zero' ? item.value : null,
              ),
              itemStyle: { color: '#176b51', borderRadius: [0, 5, 5, 0] },
            },
          ],
        });
        const resize = () => chart.resize();
        window.addEventListener('resize', resize);
        dispose = () => {
          window.removeEventListener('resize', resize);
          chart.dispose();
        };
        setRendered(true);
      })
      .catch(() => {
        if (!disposed) setRendered(false);
      });
    return () => {
      disposed = true;
      dispose?.();
    };
  }, [data, valueSuffix]);
  return (
    <section className="geo-chart" aria-labelledby={tableId}>
      <div ref={canvasRef} className="geo-chart-canvas" aria-hidden="true" />
      <span className="sr-only" role="status">
        {rendered ? `${title}图表已渲染` : `${title}图表加载中`}
      </span>
      <table className="data-table geo-chart-table">
        <caption id={tableId}>{title}（图表的可访问数据表）</caption>
        <thead>
          <tr>
            <th>维度</th>
            <th>值</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {data.map((item) => (
            <tr key={item.label}>
              <td>{item.label}</td>
              <td>{item.value === null ? '—' : `${item.value}${valueSuffix}`}</td>
              <td>{item.state}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
