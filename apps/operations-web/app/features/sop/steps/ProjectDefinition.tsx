import { createConfiguredStep } from './ConfiguredStep';
import { textValue } from './types';

export const ProjectDefinition = createConfiguredStep({
  title: '阶段0 · 项目定义',
  description: '固定品牌实体、目标 AI 条件与本轮成功定义，避免执行中漂移。',
  dependency: '已创建 SOP 项目。',
  submitLabel: '保存项目定义',
  fields: [
    { name: 'brandStandardName', label: '标准品牌名', type: 'text', required: true },
    { name: 'aliases', label: '常见简称（逗号分隔）', type: 'text' },
    {
      name: 'competitors',
      label: '竞品清单（逗号分隔）',
      type: 'text',
      hint: '用于 AI 回答中的竞品对比、推荐格局和风险表述判定。',
    },
    { name: 'targetPlatform', label: '目标 AI / 模式', type: 'text', required: true },
    { name: 'successMetric', label: '本轮成功定义', type: 'textarea', required: true },
  ],
  buildCommand: (projectPubId, values) => ({
    kind: 'update-project',
    projectPubId,
    brandStandardName: textValue(values, 'brandStandardName'),
    aliases: textValue(values, 'aliases'),
    competitors: textValue(values, 'competitors'),
    targetPlatform: textValue(values, 'targetPlatform'),
    successMetric: textValue(values, 'successMetric'),
  }),
});
