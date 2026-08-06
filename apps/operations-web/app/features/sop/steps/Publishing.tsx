import { createConfiguredStep } from './ConfiguredStep';
import { textValue } from './types';

export const Publishing = createConfiguredStep({
  title: '阶段9 · 发布管理',
  description: '登记人工提交记录；系统不会自动登录或代发第三方平台。',
  dependency: '文章版本必须已 publication_ready，否则服务端以 409 拒绝。',
  submitLabel: '登记发布提交',
  fields: [
    { name: 'articleVersionPubId', label: '文章版本公开 ID', type: 'text', required: true },
    { name: 'platform', label: '发布平台', type: 'text', required: true },
    { name: 'accountLabel', label: '账号标签（非秘密）', type: 'text' },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'publishing',
    projectPubId,
    articleVersionPubId: textValue(values, 'articleVersionPubId'),
    platform: textValue(values, 'platform'),
    accountLabel: textValue(values, 'accountLabel'),
  }),
});
