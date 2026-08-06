import { createConfiguredStep } from './ConfiguredStep';
import { textValue } from './types';

export const Writing = createConfiguredStep({
  title: '阶段7 · 文章写作',
  description: '保存文章实体与不可变正文版本；正文哈希由服务端计算。',
  dependency: '建议复制 selected 机会的 sop_ 公开 ID；也可留空独立建稿。',
  submitLabel: '创建文章与版本',
  fields: [
    { name: 'opportunityPubId', label: '内容机会公开 ID', type: 'text' },
    { name: 'title', label: '文章标题', type: 'text', required: true },
    { name: 'body', label: '文章正文', type: 'textarea', required: true },
    { name: 'changeNote', label: '版本说明', type: 'text' },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'writing',
    projectPubId,
    opportunityPubId: textValue(values, 'opportunityPubId'),
    title: textValue(values, 'title'),
    body: textValue(values, 'body'),
    changeNote: textValue(values, 'changeNote'),
  }),
});
