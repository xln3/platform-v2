import { createConfiguredStep } from './ConfiguredStep';
import { boolValue, textValue } from './types';

export const Baseline = createConfiguredStep({
  title: '阶段2 · 发布前基线',
  description: '保存目标 AI 的真实回答、采集结果和品牌提及状态；失败样本也必须如实记录。',
  dependency: '复制“查询词全集”监测表中的 sqi_ 公开 ID。',
  submitLabel: '登记基线样本',
  fields: [
    { name: 'queryItemPubId', label: '查询词公开 ID', type: 'text', required: true },
    { name: 'platform', label: 'AI 平台', type: 'text', required: true },
    {
      name: 'captureStatus',
      label: '采集状态',
      type: 'select',
      initial: 'success',
      options: [
        'success',
        'captcha',
        'login_wall',
        'interrupted',
        'incomplete',
        'risk_control',
        'search_disabled',
        'sources_unloaded',
      ].map((value) => ({ value, label: value })),
    },
    { name: 'answerText', label: '原始回答', type: 'textarea' },
    { name: 'brandMentioned', label: '回答提及目标品牌', type: 'checkbox' },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'baseline',
    projectPubId,
    queryItemPubId: textValue(values, 'queryItemPubId'),
    platform: textValue(values, 'platform'),
    captureStatus: textValue(values, 'captureStatus') as
      | 'success'
      | 'captcha'
      | 'login_wall'
      | 'interrupted'
      | 'incomplete'
      | 'risk_control'
      | 'search_disabled'
      | 'sources_unloaded',
    answerText: textValue(values, 'answerText'),
    brandMentioned: boolValue(values, 'brandMentioned'),
  }),
});
