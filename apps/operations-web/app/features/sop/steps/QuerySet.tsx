import { createConfiguredStep } from './ConfiguredStep';
import { textValue } from './types';

export const QuerySet = createConfiguredStep({
  title: '阶段1 · 查询词全集',
  description: '创建一个新版本、录入首条问题并立即冻结，保证后续同题复测可追溯。',
  dependency: '项目定义已固定；新增版本会 supersede 旧 frozen 版本。',
  submitLabel: '创建并冻结查询集',
  fields: [
    { name: 'note', label: '版本说明', type: 'text' },
    { name: 'queryText', label: '用户查询句', type: 'textarea', required: true },
    {
      name: 'layer',
      label: '查询层级',
      type: 'select',
      initial: 'A',
      options: 'ABCDEFG'.split('').map((value) => ({ value, label: `${value} 类` })),
    },
    {
      name: 'priority',
      label: '优先级',
      type: 'select',
      initial: 'P0',
      options: ['P0', 'P1', 'P2'].map((value) => ({ value, label: value })),
    },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'query-set',
    projectPubId,
    note: textValue(values, 'note'),
    queryText: textValue(values, 'queryText'),
    layer: textValue(values, 'layer') as 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'G',
    priority: textValue(values, 'priority') as 'P0' | 'P1' | 'P2',
  }),
});
