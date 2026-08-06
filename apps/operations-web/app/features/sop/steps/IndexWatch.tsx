import { createConfiguredStep } from './ConfiguredStep';
import { boolValue, textValue } from './types';

export const IndexWatch = createConfiguredStep({
  title: '阶段10 · 公开与索引观察',
  description: '在固定时间点登记公开可访问、平台检索、搜索索引与 AI 检索状态。',
  dependency: '复制发布记录 spb_ 公开 ID；同一 checkpoint 只能登记一次。',
  submitLabel: '登记观察点',
  fields: [
    { name: 'publicationPubId', label: '发布记录公开 ID', type: 'text', required: true },
    {
      name: 'checkpoint',
      label: '观察点',
      type: 'select',
      initial: 'immediate',
      options: ['immediate', 'h24', 'd3', 'd7', 'd14', 'custom'].map((value) => ({
        value,
        label: value,
      })),
    },
    { name: 'pageAccessible', label: '页面公开可访问', type: 'checkbox' },
    { name: 'searchEngineIndexed', label: '搜索引擎已索引', type: 'checkbox' },
    { name: 'platformSearchVisible', label: '平台内搜索可见', type: 'checkbox' },
    { name: 'aiRetrieved', label: '目标 AI 已检索', type: 'checkbox' },
    { name: 'aiCited', label: '目标 AI 已引用', type: 'checkbox' },
    { name: 'note', label: '观察备注', type: 'textarea' },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'index-watch',
    projectPubId,
    publicationPubId: textValue(values, 'publicationPubId'),
    checkpoint: textValue(values, 'checkpoint') as
      | 'immediate'
      | 'h24'
      | 'd3'
      | 'd7'
      | 'd14'
      | 'custom',
    pageAccessible: boolValue(values, 'pageAccessible'),
    searchEngineIndexed: boolValue(values, 'searchEngineIndexed'),
    platformSearchVisible: boolValue(values, 'platformSearchVisible'),
    aiRetrieved: boolValue(values, 'aiRetrieved'),
    aiCited: boolValue(values, 'aiCited'),
    note: textValue(values, 'note'),
  }),
});
