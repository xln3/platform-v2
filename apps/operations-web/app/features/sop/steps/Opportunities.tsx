import { createConfiguredStep } from './ConfiguredStep';
import { textValue } from './types';

export const Opportunities = createConfiguredStep({
  title: '阶段5–6 · 内容机会与信源',
  description: '从真实答案缺口选择有证据支撑、适合目标平台的内容机会。',
  dependency: '已有检索复盘与证据账本；提交后机会自动标记 selected。',
  submitLabel: '选定内容机会',
  fields: [
    { name: 'targetQuery', label: '目标查询', type: 'textarea', required: true },
    { name: 'currentGap', label: '当前答案缺口', type: 'textarea', required: true },
    { name: 'neededEvidence', label: '还需证据', type: 'textarea' },
    { name: 'recommendedPlatform', label: '推荐发布平台', type: 'text', required: true },
    { name: 'expectedChange', label: '预期答案变化', type: 'textarea' },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'opportunities',
    projectPubId,
    targetQuery: textValue(values, 'targetQuery'),
    currentGap: textValue(values, 'currentGap'),
    neededEvidence: textValue(values, 'neededEvidence'),
    recommendedPlatform: textValue(values, 'recommendedPlatform'),
    expectedChange: textValue(values, 'expectedChange'),
  }),
});
