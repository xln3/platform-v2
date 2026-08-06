import { createConfiguredStep } from './ConfiguredStep';
import { textValue } from './types';

export const Experiments = createConfiguredStep({
  title: '阶段14 · 持续实验',
  description: '明确假设、唯一改动、固定条件和观察窗口，防止把相关性当因果。',
  dependency: '建议复制本轮 frozen 查询集 sqs_ ID。',
  submitLabel: '创建实验',
  fields: [
    { name: 'querySetPubId', label: '查询集公开 ID', type: 'text' },
    { name: 'hypothesis', label: '实验假设', type: 'textarea', required: true },
    { name: 'changeDescription', label: '本轮唯一改动', type: 'textarea', required: true },
    { name: 'observationWindow', label: '观察窗口', type: 'text', required: true },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'experiments',
    projectPubId,
    querySetPubId: textValue(values, 'querySetPubId'),
    hypothesis: textValue(values, 'hypothesis'),
    changeDescription: textValue(values, 'changeDescription'),
    observationWindow: textValue(values, 'observationWindow'),
  }),
});
