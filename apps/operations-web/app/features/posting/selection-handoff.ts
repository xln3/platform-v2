import type { MediaPricesPlatform } from '@geo/api-client';

export type PostingProviderOption = {
  providerMediaId: string;
  quotedPrice: number;
};

export type ComparisonPostingSelection = {
  key: string;
  catalogType: 'news' | 'wemedia';
  catalogSha256: string;
  mediaName: string;
  mediaPlatform: string;
  provider: MediaPricesPlatform;
  options: Partial<Record<MediaPricesPlatform, PostingProviderOption>>;
};

export type PostingHandoffTarget = {
  catalogType: 'news' | 'wemedia';
  catalogSha256: string;
  provider: MediaPricesPlatform;
  providerMediaId: string;
  mediaName: string;
  mediaPlatform: string;
  quotedPrice: number;
};

export type PostingHandoff = {
  id: string;
  createdAt: number;
  targets: PostingHandoffTarget[];
};

export type PostingHandoffReadResult =
  | { kind: 'ready'; data: PostingHandoff }
  | { kind: 'missing' | 'expired' | 'forbidden' | 'invalid' };

const HANDOFF_VERSION = 1;
const HANDOFF_TTL_MS = 2 * 60 * 60 * 1_000;
const HANDOFF_KEY_PREFIX = 'geo:posting-handoff:v1:';
const PROVIDERS = new Set<MediaPricesPlatform>([
  'prfabu',
  'toumeiw',
  'mtpfw',
  'meititejia',
  'meijiehezi',
  'pinda',
]);

type StoredHandoff = PostingHandoff & {
  version: number;
  tenantId: string;
  actorId: string;
};

function safeText(value: unknown, maximum: number, allowEmpty = false): value is string {
  return (
    typeof value === 'string' &&
    value.length <= maximum &&
    (allowEmpty || value.length > 0) &&
    !/[\u0000-\u001f\u007f]/u.test(value)
  );
}

function safeTarget(value: unknown): value is PostingHandoffTarget {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return (
    (row.catalogType === 'news' || row.catalogType === 'wemedia') &&
    typeof row.catalogSha256 === 'string' &&
    /^[0-9a-f]{64}$/u.test(row.catalogSha256) &&
    typeof row.provider === 'string' &&
    PROVIDERS.has(row.provider as MediaPricesPlatform) &&
    typeof row.providerMediaId === 'string' &&
    /^[A-Za-z0-9_-]{1,120}$/u.test(row.providerMediaId) &&
    safeText(row.mediaName, 500) &&
    safeText(row.mediaPlatform, 160, true) &&
    typeof row.quotedPrice === 'number' &&
    Number.isFinite(row.quotedPrice) &&
    row.quotedPrice > 0 &&
    row.quotedPrice <= 1_000_000 &&
    (row.catalogType === 'wemedia' || row.mediaPlatform === '')
  );
}

function handoffId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 18)}`;
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function postingSelectionKey(
  catalogType: 'news' | 'wemedia',
  mediaName: string,
  mediaPlatform = '',
): string {
  return `${catalogType}\u0000${mediaPlatform}\u0000${mediaName}`;
}

export function selectedPostingTarget(
  selection: ComparisonPostingSelection,
): PostingHandoffTarget | null {
  const option = selection.options[selection.provider];
  if (!option) return null;
  const target: PostingHandoffTarget = {
    catalogType: selection.catalogType,
    catalogSha256: selection.catalogSha256,
    provider: selection.provider,
    providerMediaId: option.providerMediaId,
    mediaName: selection.mediaName,
    mediaPlatform: selection.mediaPlatform,
    quotedPrice: option.quotedPrice,
  };
  return safeTarget(target) ? target : null;
}

export function createPostingHandoff(input: {
  tenantId: string;
  actorId: string;
  selections: ComparisonPostingSelection[];
  now?: number;
}): { id: string; href: string } | null {
  const targetStorage = storage();
  const targets = input.selections.map(selectedPostingTarget);
  if (
    !targetStorage ||
    !safeText(input.tenantId, 120) ||
    !safeText(input.actorId, 120) ||
    targets.length < 1 ||
    targets.length > 50 ||
    targets.some((target) => target === null)
  ) {
    return null;
  }
  const id = handoffId();
  if (!/^[A-Za-z0-9_-]{16,64}$/u.test(id)) return null;
  const record: StoredHandoff = {
    version: HANDOFF_VERSION,
    id,
    tenantId: input.tenantId,
    actorId: input.actorId,
    createdAt: input.now ?? Date.now(),
    targets: targets as PostingHandoffTarget[],
  };
  try {
    targetStorage.setItem(`${HANDOFF_KEY_PREFIX}${id}`, JSON.stringify(record));
  } catch {
    return null;
  }
  return {
    id,
    href: `/platform/operations/posting?handoff=${encodeURIComponent(id)}`,
  };
}

export function loadPostingHandoff(input: {
  id: string;
  tenantId: string;
  actorId: string;
  now?: number;
}): PostingHandoffReadResult {
  const targetStorage = storage();
  if (!targetStorage || !/^[A-Za-z0-9_-]{16,64}$/u.test(input.id)) return { kind: 'invalid' };
  let raw: string | null;
  try {
    raw = targetStorage.getItem(`${HANDOFF_KEY_PREFIX}${input.id}`);
  } catch {
    return { kind: 'invalid' };
  }
  if (raw === null) return { kind: 'missing' };
  if (raw.length <= 0 || raw.length > 100_000) return { kind: 'invalid' };
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return { kind: 'invalid' };
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { kind: 'invalid' };
  const record = value as Record<string, unknown>;
  if (
    record.version !== HANDOFF_VERSION ||
    record.id !== input.id ||
    !safeText(record.tenantId, 120) ||
    !safeText(record.actorId, 120) ||
    typeof record.createdAt !== 'number' ||
    !Number.isSafeInteger(record.createdAt) ||
    !Array.isArray(record.targets) ||
    record.targets.length < 1 ||
    record.targets.length > 50 ||
    !record.targets.every(safeTarget)
  ) {
    return { kind: 'invalid' };
  }
  if (record.tenantId !== input.tenantId || record.actorId !== input.actorId) {
    return { kind: 'forbidden' };
  }
  const now = input.now ?? Date.now();
  if (record.createdAt > now + 60_000 || now - record.createdAt > HANDOFF_TTL_MS) {
    try {
      targetStorage.removeItem(`${HANDOFF_KEY_PREFIX}${input.id}`);
    } catch {
      // Expiry is still enforced even when browser storage cleanup is unavailable.
    }
    return { kind: 'expired' };
  }
  return {
    kind: 'ready',
    data: {
      id: input.id,
      createdAt: record.createdAt,
      targets: record.targets as PostingHandoffTarget[],
    },
  };
}

export function removePostingHandoff(id: string): void {
  if (!/^[A-Za-z0-9_-]{16,64}$/u.test(id)) return;
  try {
    storage()?.removeItem(`${HANDOFF_KEY_PREFIX}${id}`);
  } catch {
    // The server-side catalog identity checks remain the correctness boundary.
  }
}
