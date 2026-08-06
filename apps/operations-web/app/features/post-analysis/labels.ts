/**
 * 词表中文化：与 workflows/activities/post_analysis.py 的词表一字不差
 * （任务/条目/标注状态、拉踩方向、核验结论、情绪、严重度、类别、标注类型）。
 */

export type PaBadgeTone = 'neutral' | 'positive' | 'warning' | 'danger' | 'info';

const taskStatusLabels: Record<string, string> = {
  queued: '排队中',
  running: '分析中',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
};

const taskStatusTones: Record<string, PaBadgeTone> = {
  queued: 'info',
  running: 'info',
  completed: 'positive',
  partial: 'warning',
  failed: 'danger',
};

const itemStatusLabels: Record<string, string> = {
  pending: '待处理',
  fetching: '抓取中',
  analyzing: '分析中',
  annotating: '标注中',
  completed: '已完成',
  fetch_failed: '抓取失败',
  analysis_failed: '分析失败',
};

const itemStatusTones: Record<string, PaBadgeTone> = {
  pending: 'neutral',
  fetching: 'info',
  analyzing: 'info',
  annotating: 'info',
  completed: 'positive',
  fetch_failed: 'danger',
  analysis_failed: 'danger',
};

const annotationStatusLabels: Record<string, string> = {
  pending: '待标注',
  completed: '已标注',
  failed: '标注失败',
  skipped: '未标注',
};

const directionLabels: Record<string, string> = {
  target_disparaged: '目标品牌被拉踩',
  disparages_other: '拉踩别家',
};

const verdictLabels: Record<string, string> = {
  accurate: '属实',
  inaccurate: '不实',
  unsupported: '无法证实',
};

const verdictTones: Record<string, PaBadgeTone> = {
  accurate: 'positive',
  inaccurate: 'danger',
  unsupported: 'warning',
};

const sentimentLabels: Record<string, string> = {
  positive: '正面',
  neutral: '中性',
  negative: '负面',
};

const severityLabels: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
};

const severityTones: Record<string, PaBadgeTone> = {
  low: 'neutral',
  medium: 'warning',
  high: 'danger',
};

const annotationTypeLabels: Record<string, string> = {
  target_brand: '目标品牌提及',
  disparagement: '拉踩内容',
  misinformation: '不实信息',
};

const annotationTypeTones: Record<string, PaBadgeTone> = {
  target_brand: 'info',
  disparagement: 'danger',
  misinformation: 'warning',
};

const categoryFallbackLabels: Record<string, string> = {
  brand_intro: '品牌介绍',
  review_ranking: '评测榜单',
  research_report: '调研报告',
  tech_analysis: '技术解析',
  evolution_path: '演进路径',
  brand_story: '品牌故事',
  science_popularization: '科普介绍',
  other: '其他',
};

const labelFor = (map: Record<string, string>, key: string): string => map[key] ?? key;

export const taskStatusLabel = (status: string): string => labelFor(taskStatusLabels, status);
export const taskStatusTone = (status: string): PaBadgeTone => taskStatusTones[status] ?? 'neutral';
export const itemStatusLabel = (status: string): string => labelFor(itemStatusLabels, status);
export const itemStatusTone = (status: string): PaBadgeTone => itemStatusTones[status] ?? 'neutral';
export const annotationStatusLabel = (status: string): string =>
  labelFor(annotationStatusLabels, status);
export const directionLabel = (direction: string): string => labelFor(directionLabels, direction);
export const verdictLabel = (verdict: string): string => labelFor(verdictLabels, verdict);
export const verdictTone = (verdict: string): PaBadgeTone => verdictTones[verdict] ?? 'neutral';
export const sentimentLabel = (sentiment: string): string => labelFor(sentimentLabels, sentiment);
export const severityLabel = (severity: string): string => labelFor(severityLabels, severity);
export const severityTone = (severity: string): PaBadgeTone => severityTones[severity] ?? 'neutral';
export const annotationTypeLabel = (type: string): string => labelFor(annotationTypeLabels, type);
export const annotationTypeTone = (type: string): PaBadgeTone =>
  annotationTypeTones[type] ?? 'neutral';
/** 后端 ItemListRow/analysis 已带 category_label；缺省时按类别词表兜底，再兜底原始 key。 */
export const categoryLabel = (key: string, label: string | null | undefined): string =>
  label || categoryFallbackLabels[key] || key;

export function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export function formatConfidence(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}
