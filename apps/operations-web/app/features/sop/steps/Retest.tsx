import { createConfiguredStep } from './ConfiguredStep';
import { boolValue, textValue } from './types';

export const Retest = createConfiguredStep({
  title: '阶段11 · 同题复测',
  description: '用冻结查询词和相同平台条件登记发布后回答，保留引用与归属事实。',
  dependency: '需要 spb_ 发布 ID 与 frozen 查询集中的 sqi_ 查询词 ID。',
  submitLabel: '登记复测样本',
  fields: [
    { name: 'publicationPubId', label: '发布记录公开 ID', type: 'text', required: true },
    { name: 'queryItemPubId', label: '查询词公开 ID', type: 'text', required: true },
    { name: 'platform', label: 'AI 平台', type: 'text', required: true },
    { name: 'answerText', label: '发布后原始回答', type: 'textarea', required: true },
    { name: 'brandMentioned', label: '提及目标品牌', type: 'checkbox' },
    { name: 'articleAppeared', label: '目标文章出现在检索结果', type: 'checkbox' },
    { name: 'articleCited', label: '目标文章被引用', type: 'checkbox' },
    { name: 'attributionCorrect', label: '品牌归属正确', type: 'checkbox' },
    { name: 'newFacts', label: '新增事实（每行一项）', type: 'textarea' },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'retest',
    projectPubId,
    publicationPubId: textValue(values, 'publicationPubId'),
    queryItemPubId: textValue(values, 'queryItemPubId'),
    platform: textValue(values, 'platform'),
    answerText: textValue(values, 'answerText'),
    brandMentioned: boolValue(values, 'brandMentioned'),
    articleAppeared: boolValue(values, 'articleAppeared'),
    articleCited: boolValue(values, 'articleCited'),
    attributionCorrect: boolValue(values, 'attributionCorrect'),
    newFacts: textValue(values, 'newFacts'),
  }),
});
