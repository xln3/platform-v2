import { createConfiguredStep } from './ConfiguredStep';
import { boolValue, textValue } from './types';

export const Comparison = createConfiguredStep({
  title: '阶段12–13 · 对比归因',
  description: '逐题连接基线与复测，判断新增信息是否来自文章并诊断失败类型。',
  dependency: '需要 spb_、sqi_，建议同时填写 sbl_ 与 srt_ 以保留证据链。',
  submitLabel: '保存对比归因',
  fields: [
    { name: 'publicationPubId', label: '发布记录公开 ID', type: 'text', required: true },
    { name: 'queryItemPubId', label: '查询词公开 ID', type: 'text', required: true },
    { name: 'baselineAnswerPubId', label: '基线答案公开 ID', type: 'text' },
    { name: 'retestAnswerPubId', label: '复测答案公开 ID', type: 'text' },
    {
      name: 'confidence',
      label: '来自文章的置信度',
      type: 'select',
      initial: 'none',
      options: ['none', 'low', 'medium', 'high'].map((value) => ({ value, label: value })),
    },
    { name: 'attributionCorrect', label: '品牌归属正确', type: 'checkbox' },
    { name: 'conclusion', label: '归因结论', type: 'textarea', required: true },
    { name: 'nextAction', label: '下一步', type: 'textarea' },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'comparison',
    projectPubId,
    publicationPubId: textValue(values, 'publicationPubId'),
    queryItemPubId: textValue(values, 'queryItemPubId'),
    baselineAnswerPubId: textValue(values, 'baselineAnswerPubId'),
    retestAnswerPubId: textValue(values, 'retestAnswerPubId'),
    confidence: textValue(values, 'confidence') as 'high' | 'medium' | 'low' | 'none',
    attributionCorrect: boolValue(values, 'attributionCorrect'),
    conclusion: textValue(values, 'conclusion'),
    nextAction: textValue(values, 'nextAction'),
  }),
});
