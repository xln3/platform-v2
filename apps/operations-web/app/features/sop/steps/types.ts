import type { SopDashboardStep, SopMutationCommand, SopStageSnapshot } from '@geo/api-client';

export type SopConsoleValue = string | boolean;

export type SopConsoleField = {
  name: string;
  label: string;
  type: 'text' | 'textarea' | 'select' | 'checkbox';
  placeholder?: string;
  hint?: string;
  required?: boolean;
  options?: { value: string; label: string }[];
  initial?: SopConsoleValue;
};

export type SopStepDefinition = {
  title: string;
  description: string;
  dependency: string;
  submitLabel: string;
  fields: SopConsoleField[];
  buildCommand: (
    projectPubId: string,
    values: Record<string, SopConsoleValue>,
  ) => SopMutationCommand;
};

export type SopStepProps = {
  projectPubId: string;
  tab: 'monitor' | 'console';
  step: SopDashboardStep;
  snapshot: SopStageSnapshot | null;
  loadState: 'loading' | 'ready' | 'failed' | 'forbidden';
  canWrite: boolean;
  busy: boolean;
  onRetry: () => void;
  onSubmit: (command: SopMutationCommand) => Promise<void>;
};

export const textValue = (values: Record<string, SopConsoleValue>, key: string): string => {
  const value = values[key];
  return typeof value === 'string' ? value : '';
};

export const boolValue = (values: Record<string, SopConsoleValue>, key: string): boolean =>
  values[key] === true;
