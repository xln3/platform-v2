import { createConfiguredStep } from './ConfiguredStep';
import { boolValue, textValue } from './types';

export const EvidenceLedger = createConfiguredStep({
  title: '阶段4 · 证据账本',
  description: '把允许写进文章的事实、来源等级和证明边界变成可核验条目。',
  dependency: '来源可公开访问；“不能证明”同样需要填写。',
  submitLabel: '登记证据',
  fields: [
    { name: 'claimText', label: '事实 / 主张', type: 'textarea', required: true },
    { name: 'sourceName', label: '来源名称', type: 'text', required: true },
    { name: 'sourceUrl', label: '来源 URL', type: 'text', required: true },
    {
      name: 'sourceLevel',
      label: '来源等级',
      type: 'select',
      initial: 'official',
      options: [
        { value: 'official', label: '官方一手' },
        { value: 'third_party', label: '独立第三方' },
        { value: 'experience', label: '经验性材料' },
      ],
    },
    { name: 'canProve', label: '能够证明', type: 'textarea' },
    { name: 'cannotProve', label: '不能证明', type: 'textarea' },
    { name: 'allowedPublic', label: '允许公开传播', type: 'checkbox' },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'evidence-ledger',
    projectPubId,
    claimText: textValue(values, 'claimText'),
    sourceName: textValue(values, 'sourceName'),
    sourceUrl: textValue(values, 'sourceUrl'),
    sourceLevel: textValue(values, 'sourceLevel') as 'official' | 'third_party' | 'experience',
    canProve: textValue(values, 'canProve'),
    cannotProve: textValue(values, 'cannotProve'),
    allowedPublic: boolValue(values, 'allowedPublic'),
  }),
});
