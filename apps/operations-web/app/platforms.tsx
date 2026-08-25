import type { CSSProperties } from 'react';

export const COLLECTION_PLATFORM_SLUGS = [
  'doubao',
  'deepseek',
  'yiyan',
  'tongyi',
  'yuanbao',
] as const;

export type CollectionPlatformSlug = (typeof COLLECTION_PLATFORM_SLUGS)[number];

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
};

export const PLATFORM_LABELS: Readonly<Record<CollectionPlatformSlug, string>> = Object.freeze(
  Object.fromEntries(
    COLLECTION_PLATFORM_SLUGS.map((slug) => [slug, PLATFORM_REGISTRY[slug].label]),
  ) as Record<CollectionPlatformSlug, string>,
);

export function isCollectionPlatformSlug(value: string): value is CollectionPlatformSlug {
  return Object.hasOwn(PLATFORM_REGISTRY, value);
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
