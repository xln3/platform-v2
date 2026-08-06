import { createConfiguredStep } from './ConfiguredStep';
import { boolValue, textValue } from './types';

export const PrePublish = createConfiguredStep({
  title: '阶段8 · 发布前验证',
  description: '记录 AI 可采纳性、事实、实体和合规检查；放行是服务端发布硬门。',
  dependency: '复制文章版本 sav_ 公开 ID。仅在全部停止条件通过后勾选放行。',
  submitLabel: '登记检查结果',
  fields: [
    { name: 'articleVersionPubId', label: '文章版本公开 ID', type: 'text', required: true },
    {
      name: 'checkType',
      label: '检查类型',
      type: 'select',
      initial: 'fact_verification',
      options: [
        'ai_dialogue',
        'fact_verification',
        'readability',
        'extractability',
        'title_match',
        'entity_disambiguation',
        'source_completeness',
        'keyword_stuffing',
        'compliance',
        'rag_recall',
        'synonym_test',
        'other',
      ].map((value) => ({ value, label: value })),
    },
    {
      name: 'result',
      label: '检查结论',
      type: 'select',
      initial: 'pass',
      options: ['pass', 'warn', 'fail'].map((value) => ({ value, label: value })),
    },
    { name: 'findings', label: '检查发现', type: 'textarea' },
    { name: 'publicationReady', label: '全部停止条件已通过，允许发布', type: 'checkbox' },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'pre-publish',
    projectPubId,
    articleVersionPubId: textValue(values, 'articleVersionPubId'),
    checkType: textValue(values, 'checkType') as
      | 'ai_dialogue'
      | 'fact_verification'
      | 'readability'
      | 'extractability'
      | 'title_match'
      | 'entity_disambiguation'
      | 'source_completeness'
      | 'keyword_stuffing'
      | 'compliance'
      | 'rag_recall'
      | 'synonym_test'
      | 'other',
    result: textValue(values, 'result') as 'pass' | 'warn' | 'fail',
    findings: textValue(values, 'findings'),
    publicationReady: boolValue(values, 'publicationReady'),
  }),
});
