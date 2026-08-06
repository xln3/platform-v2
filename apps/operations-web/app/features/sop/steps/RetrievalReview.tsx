import { createConfiguredStep } from './ConfiguredStep';
import { textValue } from './types';

export const RetrievalReview = createConfiguredStep({
  title: '阶段3 · 检索复盘',
  description: '记录查询改写、来源选择和答案使用方式，保持事实观察与推测分离。',
  dependency: '已有至少一条发布前基线样本。',
  submitLabel: '保存复盘洞察',
  fields: [
    {
      name: 'insightType',
      label: '洞察类型',
      type: 'select',
      initial: 'source_selection',
      options: ['query_rewrite', 'source_selection', 'answer_usage', 'statistics', 'note'].map(
        (value) => ({ value, label: value }),
      ),
    },
    { name: 'note', label: '观察与解释', type: 'textarea', required: true },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'retrieval-review',
    projectPubId,
    insightType: textValue(values, 'insightType') as
      | 'query_rewrite'
      | 'source_selection'
      | 'answer_usage'
      | 'statistics'
      | 'note',
    note: textValue(values, 'note'),
  }),
});
