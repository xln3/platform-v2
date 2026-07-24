export type WorkflowState =
  | 'scheduled'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type WorkflowStep = {
  id: string;
  label: string;
  state: WorkflowState;
  detail?: string;
};

export function WorkflowTimeline({ label, steps }: { label: string; steps: WorkflowStep[] }) {
  return (
    <ol className="workflow-timeline" aria-label={label}>
      {steps.map((step) => (
        <li key={step.id} data-state={step.state}>
          <span className="workflow-marker" aria-hidden="true" />
          <div>
            <strong>{step.label}</strong>
            {step.detail ? <small>{step.detail}</small> : null}
          </div>
          <span className="badge badge-neutral">{step.state}</span>
        </li>
      ))}
    </ol>
  );
}
