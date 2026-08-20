import { useId } from 'react';

type Props = {
  label: string;
  explanation: string;
};

/** 指标名旁的可悬浮、可聚焦问号说明。 */
export function MetricHelp({ label, explanation }: Props) {
  const tooltipId = useId();

  return (
    <span className="metric-label">
      {label}
      <span className="metric-help">
        <button
          type="button"
          className="metric-help-trigger"
          aria-label={`${label}计算方式`}
          aria-describedby={tooltipId}
        >
          ?
        </button>
        <span id={tooltipId} className="metric-help-tooltip" role="tooltip">
          {explanation}
        </span>
      </span>
    </span>
  );
}
