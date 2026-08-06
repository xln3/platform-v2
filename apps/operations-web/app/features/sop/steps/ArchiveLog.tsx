import { createConfiguredStep } from './ConfiguredStep';
import { textValue } from './types';

export const ArchiveLog = createConfiguredStep({
  title: '阶段15 · 归档与工作日志',
  description: '追加记录进展、失败、阻塞和决策；日志不可修改或删除。',
  dependency: '任何阶段都可追加，失败必须保留真实原因。',
  submitLabel: '追加工作日志',
  fields: [
    {
      name: 'entryType',
      label: '日志类型',
      type: 'select',
      initial: 'progress',
      options: ['progress', 'failure', 'blocker', 'decision', 'note'].map((value) => ({
        value,
        label: value,
      })),
    },
    {
      name: 'failureClass',
      label: '失败分类',
      type: 'select',
      initial: '',
      options: [
        '',
        'captcha',
        'login_wall',
        'no_retrieval',
        'sources_unloaded',
        'not_public',
        'not_indexed',
        'not_cited',
        'wrong_attribution',
        'over_extrapolation',
        'other',
      ].map((value) => ({ value, label: value || '不适用' })),
    },
    { name: 'content', label: '日志正文', type: 'textarea', required: true },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'archive-log',
    projectPubId,
    entryType: textValue(values, 'entryType') as
      | 'progress'
      | 'failure'
      | 'blocker'
      | 'decision'
      | 'note',
    failureClass: textValue(values, 'failureClass') as
      | ''
      | 'captcha'
      | 'login_wall'
      | 'no_retrieval'
      | 'sources_unloaded'
      | 'not_public'
      | 'not_indexed'
      | 'not_cited'
      | 'wrong_attribution'
      | 'over_extrapolation'
      | 'other',
    content: textValue(values, 'content'),
  }),
});
