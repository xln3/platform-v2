import type { CSSProperties } from 'react';

export const COLLECTION_PLATFORM_SLUGS = [
  'doubao',
  'deepseek',
  'yiyan',
  'tongyi',
  'yuanbao',
] as const;

// provider_api 采集模态（2026-08-31 起，ADR-0008 三采集面之一的 v1 管线执行体）：
// 官方 API 直连 slug，与网页模拟（consumer_web）五 slug 并列——slug 即采集来源
// 判别；无地域维度（服务端任务矩阵把 region 折叠为哨兵 "api"），INV-1 无出口
// 声明故不进测量分母。需运营在 worker env 配置对应平台 Key（见
// deploy/production/worker-adapters.env.example），未配置的平台题级诚实失败。
export const PROVIDER_API_PLATFORM_SLUGS = [
  'doubao_api',
  'deepseek_api',
  'yiyan_api',
  'tongyi_api',
  'yuanbao_api',
] as const;

export type WebCollectionPlatformSlug = (typeof COLLECTION_PLATFORM_SLUGS)[number];
export type ProviderApiPlatformSlug = (typeof PROVIDER_API_PLATFORM_SLUGS)[number];
export type CollectionPlatformSlug = WebCollectionPlatformSlug | ProviderApiPlatformSlug;

export type PlatformDisplay = {
  slug: CollectionPlatformSlug;
  label: string;
  icon: string;
  alt: string;
};

export const PLATFORM_REGISTRY: Readonly<Record<CollectionPlatformSlug, PlatformDisplay>> = {
  doubao: {
    slug: 'doubao',
    label: '豆包',
    icon: '/platform/operations/platform-icons/doubao.png',
    alt: '豆包',
  },
  deepseek: {
    slug: 'deepseek',
    label: 'DeepSeek',
    icon: '/platform/operations/platform-icons/deepseek.png',
    alt: 'DeepSeek',
  },
  yiyan: {
    slug: 'yiyan',
    label: '文心一言',
    icon: '/platform/operations/platform-icons/yiyan.png',
    alt: '文心一言',
  },
  tongyi: {
    slug: 'tongyi',
    label: '通义千问',
    icon: '/platform/operations/platform-icons/tongyi.png',
    alt: '通义千问',
  },
  yuanbao: {
    slug: 'yuanbao',
    label: '腾讯元宝',
    icon: '/platform/operations/platform-icons/yuanbao.png',
    alt: '腾讯元宝',
  },
  doubao_api: {
    slug: 'doubao_api',
    label: '豆包（API）',
    icon: '/platform/operations/platform-icons/doubao.png',
    alt: '豆包（API）',
  },
  deepseek_api: {
    slug: 'deepseek_api',
    label: 'DeepSeek（API）',
    icon: '/platform/operations/platform-icons/deepseek.png',
    alt: 'DeepSeek（API）',
  },
  yiyan_api: {
    slug: 'yiyan_api',
    label: '文心一言（API）',
    icon: '/platform/operations/platform-icons/yiyan.png',
    alt: '文心一言（API）',
  },
  tongyi_api: {
    slug: 'tongyi_api',
    label: '通义千问（API）',
    icon: '/platform/operations/platform-icons/tongyi.png',
    alt: '通义千问（API）',
  },
  yuanbao_api: {
    slug: 'yuanbao_api',
    label: '腾讯元宝（API）',
    icon: '/platform/operations/platform-icons/yuanbao.png',
    alt: '腾讯元宝（API）',
  },
};

export const PLATFORM_LABELS: Readonly<Record<CollectionPlatformSlug, string>> = Object.freeze(
  Object.fromEntries(
    [...COLLECTION_PLATFORM_SLUGS, ...PROVIDER_API_PLATFORM_SLUGS].map((slug) => [
      slug,
      PLATFORM_REGISTRY[slug].label,
    ]),
  ) as Record<CollectionPlatformSlug, string>,
);

export function isCollectionPlatformSlug(value: string): value is CollectionPlatformSlug {
  return Object.hasOwn(PLATFORM_REGISTRY, value);
}

export function isProviderApiSlug(value: string): value is ProviderApiPlatformSlug {
  return (PROVIDER_API_PLATFORM_SLUGS as readonly string[]).includes(value);
}

export function platformDisplayName(value: string): string {
  return isCollectionPlatformSlug(value) ? PLATFORM_REGISTRY[value].label : value;
}

const badgeStyle: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.5rem',
};

const iconStyle: CSSProperties = {
  width: '1.5rem',
  height: '1.5rem',
  flex: '0 0 1.5rem',
  objectFit: 'contain',
};

export function PlatformBadge({
  platform,
  iconOnly = false,
}: {
  platform: string;
  iconOnly?: boolean;
}) {
  if (!isCollectionPlatformSlug(platform)) return <span>{platform}</span>;
  const item = PLATFORM_REGISTRY[platform];
  return (
    <span style={badgeStyle} data-platform={item.slug}>
      <img
        src={item.icon}
        alt={iconOnly ? item.alt : ''}
        aria-hidden={iconOnly ? undefined : true}
        width={24}
        height={24}
        loading="lazy"
        style={iconStyle}
      />
      {iconOnly ? null : <span>{item.label}</span>}
    </span>
  );
}
