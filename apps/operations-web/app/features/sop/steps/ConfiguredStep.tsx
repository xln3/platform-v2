import { useMemo, useState, type FormEvent } from 'react';
import {
  Badge,
  FormField,
  MetricGrid,
  Pagination,
  StatePanel,
  TableRegion,
} from '@geo/design-system';
import type { SopConsoleValue, SopStepDefinition, SopStepProps } from './types';

const statusTone = (status: string): 'positive' | 'warning' | 'neutral' =>
  status === 'done' || status === 'success' || status === 'pass' || status === 'public'
    ? 'positive'
    : status === 'in_progress' || status === 'warn' || status === 'failed'
      ? 'warning'
      : 'neutral';

export function ConfiguredStep({
  definition,
  projectPubId,
  tab,
  step,
  snapshot,
  loadState,
  canWrite,
  busy,
  onRetry,
  onPageChange,
  onSubmit,
}: SopStepProps & { definition: SopStepDefinition }) {
  const initialValues = useMemo(
    () =>
      Object.fromEntries(
        definition.fields.map((field) => [
          field.name,
          field.initial ?? (field.type === 'checkbox' ? false : ''),
        ]),
      ) as Record<string, SopConsoleValue>,
    [definition],
  );
  const [values, setValues] = useState(initialValues);
  const [validation, setValidation] = useState('');

  if (tab === 'monitor') {
    if (loadState === 'loading') return <StatePanel state="loading" />;
    if (loadState === 'forbidden') return <StatePanel state="forbidden" />;
    if (loadState === 'failed') return <StatePanel state="failed" onRetry={onRetry} />;
    const metrics = [...step.metrics, ...(snapshot?.metrics ?? [])].map((metric) => ({
      label: metric.label,
      value: metric.value,
      detail: `${step.stage} · ${step.name}`,
    }));
    return (
      <div className="sop-step-pane" aria-label={`${definition.title}监测`}>
        {metrics.length > 0 ? <MetricGrid metrics={metrics} /> : null}
        {!snapshot || snapshot.items.length === 0 ? (
          <StatePanel state="empty" />
        ) : (
          <TableRegion label={`${definition.title}记录`}>
            <table className="data-table sop-table">
              <thead>
                <tr>
                  <th>公开 ID</th>
                  <th>内容</th>
                  <th>状态</th>
                  <th>说明</th>
                  <th>记录时间</th>
                </tr>
              </thead>
              <tbody>
                {snapshot.items.map((item) => (
                  <tr key={item.pubId}>
                    <td>
                      <code>{item.pubId}</code>
                    </td>
                    <td>{item.label}</td>
                    <td>
                      <Badge tone={statusTone(item.status)}>{item.status || '已记录'}</Badge>
                    </td>
                    <td>{item.detail || '—'}</td>
                    <td>{item.createdAt || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination
              page={snapshot.page.page}
              pageCount={snapshot.page.totalPages}
              totalItems={snapshot.page.totalCount}
              onPageChange={onPageChange}
              label={`${definition.title}记录分页`}
            />
          </TableRegion>
        )}
      </div>
    );
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const missing = definition.fields.find(
      (field) =>
        field.required &&
        field.type !== 'checkbox' &&
        String(values[field.name] ?? '').trim().length === 0,
    );
    if (missing) {
      setValidation(`请填写“${missing.label}”`);
      return;
    }
    setValidation('');
    void onSubmit(definition.buildCommand(projectPubId, values));
  };

  return (
    <div className="sop-step-pane sop-console" aria-label={`${definition.title}操作台`}>
      <div className="sop-console-intro">
        <div>
          <span className="overline">操作台</span>
          <h3>{definition.title}</h3>
          <p>{definition.description}</p>
        </div>
        <Badge tone={canWrite ? 'info' : 'neutral'}>{canWrite ? '可写会话' : '只读审核会话'}</Badge>
      </div>
      <div className="sop-dependency">
        <strong>前置数据</strong>
        <span>{definition.dependency}</span>
      </div>
      {!canWrite ? (
        <StatePanel state="forbidden" />
      ) : (
        <form className="sop-form" onSubmit={submit}>
          {definition.fields.map((field) => {
            const id = `sop-${step.key}-${field.name}`;
            if (field.type === 'checkbox') {
              return (
                <label className="sop-checkbox" key={field.name} htmlFor={id}>
                  <input
                    id={id}
                    type="checkbox"
                    checked={values[field.name] === true}
                    onChange={(event) => {
                      const checked = event.currentTarget.checked;
                      setValues((current) => ({
                        ...current,
                        [field.name]: checked,
                      }));
                    }}
                  />
                  <span>{field.label}</span>
                </label>
              );
            }
            return (
              <FormField id={id} label={field.label} hint={field.hint} key={field.name}>
                {field.type === 'textarea' ? (
                  <textarea
                    id={id}
                    rows={5}
                    value={String(values[field.name] ?? '')}
                    placeholder={field.placeholder}
                    onChange={(event) => {
                      const value = event.currentTarget.value;
                      setValues((current) => ({
                        ...current,
                        [field.name]: value,
                      }));
                    }}
                  />
                ) : field.type === 'select' ? (
                  <select
                    id={id}
                    value={String(values[field.name] ?? '')}
                    onChange={(event) => {
                      const value = event.currentTarget.value;
                      setValues((current) => ({
                        ...current,
                        [field.name]: value,
                      }));
                    }}
                  >
                    {(field.options ?? []).map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id={id}
                    value={String(values[field.name] ?? '')}
                    placeholder={field.placeholder}
                    onChange={(event) => {
                      const value = event.currentTarget.value;
                      setValues((current) => ({
                        ...current,
                        [field.name]: value,
                      }));
                    }}
                  />
                )}
              </FormField>
            );
          })}
          {validation ? (
            <div className="sop-validation" role="alert">
              {validation}
            </div>
          ) : null}
          <button className="button button-primary" type="submit" disabled={busy}>
            {busy ? '正在提交…' : definition.submitLabel}
          </button>
        </form>
      )}
    </div>
  );
}

export const createConfiguredStep = (definition: SopStepDefinition) => (props: SopStepProps) => (
  <ConfiguredStep {...props} definition={definition} />
);
