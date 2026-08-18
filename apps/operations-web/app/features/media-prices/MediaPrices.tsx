import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  getMediaPricesDataset,
  getMediaPricesRefreshStatus,
  requestMediaPricesRefresh,
  type IdentitySessionHeaders,
  type MediaPricesDataset,
  type MediaPricesDatasetRow,
  type MediaPricesPlatform,
  type MediaPricesRefreshStatus,
} from '@geo/api-client';
import {
  containsClientSecret,
  FilterBar,
  Pagination,
  StatePanel,
  TableRegion,
  downloadSafeGeneratedFile,
  updateClientUrlParameters,
} from '@geo/design-system';
import { WemediaPrices } from './WemediaPrices';
import {
  createPostingHandoff,
  postingSelectionKey,
  type ComparisonPostingSelection,
  type PostingProviderOption,
} from '../posting/selection-handoff';
import './media-prices.css';

const PLATFORMS: { key: MediaPricesPlatform; label: string }[] = [
  { key: 'prfabu', label: 'prfabu' },
  { key: 'toumeiw', label: '投媒网' },
  { key: 'mtpfw', label: '媒体批发网' },
  { key: 'meititejia', label: '媒体特价网' },
  { key: 'meijiehezi', label: '媒介盒子' },
  { key: 'pinda', label: '品达发稿' },
];

export const GEO_PLATFORM_LABELS: Record<string, string> = {
  a: 'DeepSeek',
  b: '豆包',
  c: '通义千问',
  d: '腾讯元宝',
  e: '文心一言',
  f: 'Kimi',
  z: '其他',
};
const GEO_BADGE_SHORT: Record<string, string> = {
  a: 'DeepSeek',
  b: '豆包',
  c: '通义',
  d: '元宝',
  e: '文心',
  f: 'Kimi',
  z: '其他',
};
const GEO_ORDER = ['a', 'b', 'c', 'd', 'e', 'f', 'z'] as const;

const PAGE_SIZE = 100;
type MediaCatalogTab = 'news' | 'wemedia';

function readMediaCatalogTab(): MediaCatalogTab {
  if (typeof window === 'undefined') return 'news';
  return new URL(window.location.href).searchParams.get('media_tab') === 'wemedia'
    ? 'wemedia'
    : 'news';
}

export type MediaPricesSort = 'best-asc' | 'best-desc' | 'save-desc' | 'pcw-desc' | 'pubrate-desc';

export type MediaPricesPriceBand = 'all' | 'le100' | '100-500' | '500-2000' | 'gt2000' | 'none';

export type MediaPricesFilters = {
  search: string;
  onlyGeo: boolean;
  onlyMultiSrc: boolean;
  whitelistOnly: boolean;
  geoKeys: string[];
  priceBand: MediaPricesPriceBand;
  portal: string;
  include: string;
  sort: MediaPricesSort;
};

export const defaultMediaPricesFilters: MediaPricesFilters = {
  search: '',
  onlyGeo: false,
  onlyMultiSrc: false,
  whitelistOnly: false,
  geoKeys: [],
  priceBand: 'all',
  portal: '',
  include: '',
  sort: 'best-asc',
};

const mediaPricesUrlKeys = {
  search: 'media_q',
  onlyGeo: 'media_geo_only',
  onlyMultiSrc: 'media_multi_only',
  whitelistOnly: 'media_wl',
  geoKeys: 'media_geo',
  priceBand: 'media_band',
  portal: 'media_portal',
  include: 'media_include',
  sort: 'media_sort',
  page: 'media_page',
} as const;
const mediaPricesPriceBands = new Set<MediaPricesPriceBand>([
  'all',
  'le100',
  '100-500',
  '500-2000',
  'gt2000',
  'none',
]);
const mediaPricesSorts = new Set<MediaPricesSort>([
  'best-asc',
  'best-desc',
  'save-desc',
  'pcw-desc',
  'pubrate-desc',
]);

const safeMediaPricesUrlText = (value: string | null, maximumLength: number): string =>
  value &&
  value.length <= maximumLength &&
  !/[\u0000-\u001f\u007f]/u.test(value) &&
  !containsClientSecret(value)
    ? value
    : '';

export function readMediaPricesUrlState(
  value: string | URL = typeof window === 'undefined'
    ? 'https://geo.invalid/platform/operations/media-prices'
    : window.location.href,
): { filters: MediaPricesFilters; page: number } {
  const url = value instanceof URL ? value : new URL(value, 'https://geo.invalid');
  const priceBand = url.searchParams.get(mediaPricesUrlKeys.priceBand);
  const sort = url.searchParams.get(mediaPricesUrlKeys.sort);
  const rawGeoKeys = (url.searchParams.get(mediaPricesUrlKeys.geoKeys) ?? '').split(',');
  const geoKeys = GEO_ORDER.filter((key) => rawGeoKeys.includes(key));
  const rawPage = Number(url.searchParams.get(mediaPricesUrlKeys.page) ?? '1');
  return {
    filters: {
      search: safeMediaPricesUrlText(url.searchParams.get(mediaPricesUrlKeys.search), 160),
      onlyGeo: url.searchParams.get(mediaPricesUrlKeys.onlyGeo) === '1',
      onlyMultiSrc: url.searchParams.get(mediaPricesUrlKeys.onlyMultiSrc) === '1',
      whitelistOnly: url.searchParams.get(mediaPricesUrlKeys.whitelistOnly) === '1',
      geoKeys,
      priceBand:
        priceBand && mediaPricesPriceBands.has(priceBand as MediaPricesPriceBand)
          ? (priceBand as MediaPricesPriceBand)
          : 'all',
      portal: safeMediaPricesUrlText(url.searchParams.get(mediaPricesUrlKeys.portal), 160),
      include: safeMediaPricesUrlText(url.searchParams.get(mediaPricesUrlKeys.include), 160),
      sort:
        sort && mediaPricesSorts.has(sort as MediaPricesSort)
          ? (sort as MediaPricesSort)
          : 'best-asc',
    },
    page: Number.isSafeInteger(rawPage) && rawPage >= 1 && rawPage <= 2_000 ? rawPage : 1,
  };
}

export function writeMediaPricesUrlState(
  filters: MediaPricesFilters,
  page: number,
  replace = false,
): boolean {
  if (typeof window === 'undefined') return false;
  const updates = Object.fromEntries(
    Object.values(mediaPricesUrlKeys).map((key) => [key, null]),
  ) as Record<string, string | null>;
  const safeSearch = safeMediaPricesUrlText(filters.search, 160);
  const safePortal = safeMediaPricesUrlText(filters.portal, 160);
  const safeInclude = safeMediaPricesUrlText(filters.include, 160);
  const safeGeoKeys = GEO_ORDER.filter((key) => filters.geoKeys.includes(key));
  if (safeSearch) updates[mediaPricesUrlKeys.search] = safeSearch;
  if (filters.onlyGeo) updates[mediaPricesUrlKeys.onlyGeo] = '1';
  if (filters.onlyMultiSrc) updates[mediaPricesUrlKeys.onlyMultiSrc] = '1';
  if (filters.whitelistOnly) updates[mediaPricesUrlKeys.whitelistOnly] = '1';
  if (safeGeoKeys.length > 0) {
    updates[mediaPricesUrlKeys.geoKeys] = safeGeoKeys.join(',');
  }
  if (filters.priceBand !== 'all' && mediaPricesPriceBands.has(filters.priceBand)) {
    updates[mediaPricesUrlKeys.priceBand] = filters.priceBand;
  }
  if (safePortal) updates[mediaPricesUrlKeys.portal] = safePortal;
  if (safeInclude) updates[mediaPricesUrlKeys.include] = safeInclude;
  if (filters.sort !== 'best-asc' && mediaPricesSorts.has(filters.sort)) {
    updates[mediaPricesUrlKeys.sort] = filters.sort;
  }
  if (Number.isSafeInteger(page) && page > 1 && page <= 2_000) {
    updates[mediaPricesUrlKeys.page] = String(page);
  }
  return updateClientUrlParameters(updates, [], replace);
}

export function isPlatformBestPrice(
  row: MediaPricesDatasetRow,
  platform: MediaPricesPlatform,
): boolean {
  const price = row.prices[platform];
  return price != null && row.best != null && price === row.best;
}

/** prfabu 非全网最低时按最低价采购的节省比例（0..1），否则为 null。 */
export function prfabuSavings(row: MediaPricesDatasetRow): number | null {
  const prfabu = row.prices.prfabu;
  if (prfabu == null || prfabu <= 0 || row.best == null || row.best >= prfabu) return null;
  return (prfabu - row.best) / prfabu;
}

function byNullableNumberDesc(
  pick: (row: MediaPricesDatasetRow) => number | null,
): (left: MediaPricesDatasetRow, right: MediaPricesDatasetRow) => number {
  return (left, right) => {
    const a = pick(left);
    const b = pick(right);
    if (a == null && b == null) return 0;
    if (a == null) return 1;
    if (b == null) return -1;
    return b - a;
  };
}

export function sortMediaPricesRows(
  rows: MediaPricesDatasetRow[],
  sort: MediaPricesSort,
): MediaPricesDatasetRow[] {
  const copy = [...rows];
  if (sort === 'best-asc' || sort === 'best-desc') {
    const direction = sort === 'best-asc' ? 1 : -1;
    copy.sort((left, right) => {
      if (left.best == null && right.best == null) return 0;
      if (left.best == null) return 1;
      if (right.best == null) return -1;
      return (left.best - right.best) * direction;
    });
    return copy;
  }
  const picker =
    sort === 'save-desc'
      ? prfabuSavings
      : sort === 'pcw-desc'
        ? (row: MediaPricesDatasetRow) => row.pc_w ?? null
        : (row: MediaPricesDatasetRow) => row.pub_rate ?? null;
  copy.sort(byNullableNumberDesc(picker));
  return copy;
}

export function filterMediaPricesRows(
  rows: MediaPricesDatasetRow[],
  filters: MediaPricesFilters,
): MediaPricesDatasetRow[] {
  const search = filters.search.trim().toLowerCase();
  const filtered = rows.filter((row) => {
    if (search) {
      const haystack =
        `${row.name} ${row.portal ?? ''} ${row.channel ?? ''} ${row.province ?? ''} ${row.remark ?? ''}`.toLowerCase();
      if (!haystack.includes(search)) return false;
    }
    if (filters.onlyGeo && row.geo.length === 0) return false;
    if (filters.onlyMultiSrc && row.n_src < 2) return false;
    if (filters.whitelistOnly && row.whitelist !== true) return false;
    if (filters.geoKeys.length > 0 && !filters.geoKeys.some((key) => row.geo.includes(key))) {
      return false;
    }
    if (filters.priceBand !== 'all') {
      if (filters.priceBand === 'none') {
        if (row.best != null) return false;
      } else {
        if (row.best == null) return false;
        if (filters.priceBand === 'le100' && row.best > 100) return false;
        if (filters.priceBand === '100-500' && (row.best <= 100 || row.best > 500)) return false;
        if (filters.priceBand === '500-2000' && (row.best <= 500 || row.best > 2000)) return false;
        if (filters.priceBand === 'gt2000' && row.best <= 2000) return false;
      }
    }
    if (filters.portal && (row.portal ?? '') !== filters.portal) return false;
    if (filters.include && (row.include ?? '') !== filters.include) return false;
    return true;
  });
  return sortMediaPricesRows(filtered, filters.sort);
}

export function summarizeMediaPrices(rows: MediaPricesDatasetRow[]): {
  total: number;
  prfabuNotBest: number;
  avgSavePct: number | null;
} {
  let notBest = 0;
  let saveSum = 0;
  for (const row of rows) {
    const save = prfabuSavings(row);
    if (save != null) {
      notBest += 1;
      saveSum += save;
    }
  }
  return {
    total: rows.length,
    prfabuNotBest: notBest,
    avgSavePct: notBest > 0 ? (saveSum / notBest) * 100 : null,
  };
}

function csvEscape(value: string | number | null | undefined): string {
  const text = value == null ? '' : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function buildMediaPricesCsv(rows: MediaPricesDatasetRow[]): string {
  const lines = [
    [
      'name',
      'portal',
      'channel',
      'include',
      'news_src',
      'speed',
      'pc_w',
      'm_w',
      'pub_rate',
      'ai_rate',
      'geo',
      'geo_n',
      ...PLATFORMS.map((platform) => platform.key),
      'best',
      'best_plat',
      'n_src',
      'remark',
      'case',
      'site',
      'whitelist',
    ].join(','),
  ];
  for (const row of rows) {
    lines.push(
      [
        row.name,
        row.portal ?? '',
        row.channel ?? '',
        row.include ?? '',
        row.news_src ?? '',
        row.speed ?? '',
        row.pc_w ?? '',
        row.m_w ?? '',
        row.pub_rate ?? '',
        row.ai_rate ?? '',
        row.geo.join('|'),
        row.geo_n,
        ...PLATFORMS.map((platform) => row.prices[platform.key] ?? ''),
        row.best ?? '',
        row.best_plat ?? '',
        row.n_src,
        row.remark ?? '',
        row.case ?? '',
        row.site ?? '',
        row.whitelist === true ? '1' : '',
      ]
        .map(csvEscape)
        .join(','),
    );
  }
  return lines.join('\r\n');
}

function topValues(
  rows: MediaPricesDatasetRow[],
  pick: (row: MediaPricesDatasetRow) => string | undefined,
  cap: number,
): string[] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const value = pick(row);
    if (value) counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], 'zh-CN'))
    .slice(0, cap)
    .map(([value]) => value);
}

function formatPrice(value: number | null | undefined): string {
  return value == null ? '—' : `¥${value}`;
}

export const REFRESH_POLL_INTERVAL_MS = 10_000;
export const REFRESH_POLL_TIMEOUT_MS = 45 * 60_000;

export type RefreshSourcePresentation = {
  staleSession: string[];
  staleOther: string[];
  partial: string[];
  failed: string[];
};

export function presentRefreshSources(
  sources: MediaPricesRefreshStatus['sources'],
): RefreshSourcePresentation {
  const presentation: RefreshSourcePresentation = {
    staleSession: [],
    staleOther: [],
    partial: [],
    failed: [],
  };
  for (const [name, source] of Object.entries(sources)) {
    if (source.status === 'stale' && source.note === 'session_expired') {
      presentation.staleSession.push(name);
    } else if (source.status === 'stale') {
      presentation.staleOther.push(name);
    } else if (source.status === 'partial') {
      presentation.partial.push(name);
    } else if (source.status === 'failed') {
      presentation.failed.push(name);
    }
  }
  return presentation;
}

export function buildRefreshDoneNotice(status: MediaPricesRefreshStatus): {
  tone: 'info' | 'warn';
  text: string;
} {
  const presentation = presentRefreshSources(status.sources);
  const warnings: string[] = [];
  for (const name of presentation.staleSession) {
    warnings.push(`${name} 会话失效，沿用旧数据（请到发帖页重新登录平台账号）`);
  }
  if (presentation.staleOther.length > 0) {
    warnings.push(`${presentation.staleOther.join('、')} 数据陈旧，沿用旧数据`);
  }
  if (presentation.partial.length > 0) {
    warnings.push(`${presentation.partial.join('、')} 仅完成部分采集`);
  }
  if (presentation.failed.length > 0) {
    warnings.push(`${presentation.failed.join('、')} 拉取失败，本次未更新该源`);
  }
  const base = `刷新完成：${status.message || '数据集已更新'}`;
  return warnings.length > 0
    ? { tone: 'warn', text: `${base}；${warnings.join('；')}` }
    : { tone: 'info', text: base };
}

export function isRefreshTerminal(state: MediaPricesRefreshStatus['state']): boolean {
  return state === 'done' || state === 'failed';
}

export function mediaPricesRefreshRevision(status: MediaPricesRefreshStatus): string {
  return JSON.stringify([
    status.state,
    status.startedAt,
    status.updatedAt,
    status.message,
    PLATFORMS.map(({ key }) => {
      const source = status.sources[key];
      return source ? [key, source.status, source.rows, source.note] : [key, null];
    }),
  ]);
}

export type RefreshStatusReadState = 'loading' | 'ready' | 'unavailable' | 'forbidden';

export function formatRefreshCardSubtitle(
  status: MediaPricesRefreshStatus | null,
  readState: RefreshStatusReadState = 'ready',
): string {
  if (readState === 'loading') return '正在读取刷新状态…';
  if (readState === 'unavailable') return '刷新状态读取失败';
  if (readState === 'forbidden') return '无权查看刷新状态';
  if (!status || status.state === 'never') return '尚未刷新';
  if (status.state === 'done') {
    return status.message ? `上次刷新：${status.message}` : '上次刷新完成';
  }
  if (status.state === 'running') return `正在刷新：${status.message || '…'}`;
  return `上次刷新失败：${status.message || '未知错误'}`;
}

type Session = {
  tenantId: string;
  actorId: string;
  role: 'operator' | 'reviewer' | 'admin';
  headers: IdentitySessionHeaders;
};

export function MediaPrices({ session }: { session: Session | undefined }) {
  const initialUrlState = useMemo(() => readMediaPricesUrlState(), []);
  const [activeCatalog, setActiveCatalog] = useState<MediaCatalogTab>(() => readMediaCatalogTab());
  const [wemediaReloadRevision, setWemediaReloadRevision] = useState(0);
  const [postingSelections, setPostingSelections] = useState<
    Record<string, ComparisonPostingSelection>
  >({});
  const [handoffNotice, setHandoffNotice] = useState<string | null>(null);
  const canManage = session !== undefined;
  const requestTenant = session?.headers['X-Tenant-Id'];
  const requestActor = session?.headers['X-Actor-Id'];
  const requestRole = session?.headers['X-Actor-Role'];
  const requestHeaders = useMemo<IdentitySessionHeaders>(
    () => ({
      ...(requestTenant ? { 'X-Tenant-Id': requestTenant } : {}),
      ...(requestActor ? { 'X-Actor-Id': requestActor } : {}),
      ...(requestRole ? { 'X-Actor-Role': requestRole } : {}),
    }),
    [requestActor, requestRole, requestTenant],
  );
  const [state, setState] = useState<'loading' | 'ready' | 'forbidden' | 'missing' | 'unavailable'>(
    'loading',
  );
  const [dataset, setDataset] = useState<MediaPricesDataset | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [filters, setFilters] = useState<MediaPricesFilters>(initialUrlState.filters);
  const [page, setPage] = useState(initialUrlState.page);
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<MediaPricesRefreshStatus | null>(null);
  const [refreshStatusReadState, setRefreshStatusReadState] =
    useState<RefreshStatusReadState>('loading');
  const [refreshStatusAttempt, setRefreshStatusAttempt] = useState(0);
  const [refreshRunning, setRefreshRunning] = useState(false);
  const [refreshProgress, setRefreshProgress] = useState<string | null>(null);
  const [refreshNotice, setRefreshNotice] = useState<{
    tone: 'info' | 'warn' | 'error';
    text: string;
  } | null>(null);
  const activeRequestHeadersRef = useRef(requestHeaders);
  const refreshStatusReadGenerationRef = useRef(0);
  const pollingRef = useRef(false);
  const pollingGenerationRef = useRef(0);
  const pollTimerRef = useRef<number | null>(null);
  const pollStartedAtRef = useRef(0);
  const refreshTerminalBaselineRef = useRef<string | null>(null);
  const observedCurrentRunStatusRef = useRef(false);
  const refreshSubmissionScopeRef = useRef<IdentitySessionHeaders | null>(null);
  activeRequestHeadersRef.current = requestHeaders;

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    void getMediaPricesDataset(requestHeaders).then((result) => {
      if (cancelled) return;
      if (result.kind === 'ready') {
        setDataset(result.data);
        setState('ready');
      } else {
        setDataset(null);
        setState(result.kind);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [attempt, requestHeaders]);

  useEffect(() => {
    const restoreUrlState = () => {
      const restored = readMediaPricesUrlState();
      setFilters(restored.filters);
      setPage(restored.page);
      setActiveCatalog(readMediaCatalogTab());
      setExportNotice(null);
    };
    window.addEventListener('popstate', restoreUrlState);
    return () => window.removeEventListener('popstate', restoreUrlState);
  }, []);

  const updateFilters = (patch: Partial<MediaPricesFilters>, replace = false) => {
    const next = { ...filters, ...patch };
    setFilters(next);
    setPage(1);
    setExportNotice(null);
    writeMediaPricesUrlState(next, 1, replace);
  };

  const filtered = useMemo(
    () => (dataset ? filterMediaPricesRows(dataset.rows, filters) : []),
    [dataset, filters],
  );
  const summary = useMemo(() => summarizeMediaPrices(filtered), [filtered]);
  const portals = useMemo(
    () => (dataset ? topValues(dataset.rows, (row) => row.portal, 100) : []),
    [dataset],
  );
  const includes = useMemo(
    () => (dataset ? topValues(dataset.rows, (row) => row.include, 50) : []),
    [dataset],
  );
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pageRows = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);
  const selectedPostingItems = useMemo(() => Object.values(postingSelections), [postingSelections]);
  const selectNewsRow = (row: MediaPricesDatasetRow, checked: boolean) => {
    const key = postingSelectionKey('news', row.name);
    setPostingSelections((current) => {
      if (!checked) {
        const next = { ...current };
        delete next[key];
        return next;
      }
      const options = Object.fromEntries(
        PLATFORMS.flatMap(({ key: provider }) => {
          const quotedPrice = row.prices[provider];
          const providerMediaId = row.ids?.[provider];
          return quotedPrice != null && providerMediaId
            ? [[provider, { quotedPrice, providerMediaId } satisfies PostingProviderOption]]
            : [];
        }),
      ) as Partial<Record<MediaPricesPlatform, PostingProviderOption>>;
      const provider =
        (row.best_plat && options[row.best_plat] ? row.best_plat : undefined) ??
        PLATFORMS.map((item) => item.key).find((candidate) => options[candidate] !== undefined);
      if (!provider || !dataset?.sha256) return current;
      return {
        ...current,
        [key]: {
          key,
          catalogType: 'news',
          catalogSha256: dataset.sha256,
          mediaName: row.name,
          mediaPlatform: '',
          options,
          provider,
        },
      };
    });
  };
  useEffect(() => {
    if (page === safePage) return;
    setPage(safePage);
    writeMediaPricesUrlState(filters, safePage, true);
  }, [filters, page, safePage]);

  const exportCsv = () => {
    const ok = downloadSafeGeneratedFile({
      kind: 'csv',
      fileName: 'media-prices.csv',
      content: buildMediaPricesCsv(filtered),
    });
    setExportNotice(
      ok
        ? `已导出 ${filtered.length} 条筛选结果（media-prices.csv）。`
        : '导出失败：超出 2MB 上限或内容未通过安全校验，请缩小筛选范围。',
    );
  };

  const reloadDataset = useCallback(async (headers: IdentitySessionHeaders) => {
    const result = await getMediaPricesDataset(headers);
    if (activeRequestHeadersRef.current !== headers) return false;
    if (result.kind === 'ready') {
      setDataset(result.data);
      setState('ready');
      return true;
    }
    setDataset(null);
    setState(result.kind);
    return false;
  }, []);

  const stopPolling = useCallback(() => {
    pollingRef.current = false;
    pollingGenerationRef.current += 1;
    if (pollTimerRef.current != null) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const pollTick = useCallback(
    async (generation: number, headers: IdentitySessionHeaders) => {
      const ownsPollingScope = () =>
        pollingRef.current &&
        pollingGenerationRef.current === generation &&
        activeRequestHeadersRef.current === headers;
      if (!ownsPollingScope()) return;
      const result = await getMediaPricesRefreshStatus(headers);
      if (!ownsPollingScope()) return;
      if (result.kind === 'ready') {
        setRefreshStatusReadState('ready');
        const status = result.data;
        if (status.state === 'running') {
          observedCurrentRunStatusRef.current = true;
        }
        const terminalRevisionIsCurrent =
          !isRefreshTerminal(status.state) ||
          (refreshTerminalBaselineRef.current === null
            ? observedCurrentRunStatusRef.current
            : mediaPricesRefreshRevision(status) !== refreshTerminalBaselineRef.current);
        if (!terminalRevisionIsCurrent) {
          setRefreshProgress('刷新已接受，正在等待新的终态记录…');
        } else {
          setLastRefresh(status);
        }
        if (status.state === 'done' && terminalRevisionIsCurrent) {
          stopPolling();
          setRefreshProgress('刷新完成，正在读取新快照…');
          const reloaded = await reloadDataset(headers);
          if (activeRequestHeadersRef.current !== headers) return;
          setRefreshRunning(false);
          setRefreshProgress(null);
          if (reloaded) {
            setWemediaReloadRevision((value) => value + 1);
            setRefreshNotice(buildRefreshDoneNotice(status));
          } else {
            setRefreshNotice(null);
          }
          return;
        }
        if (status.state === 'failed' && terminalRevisionIsCurrent) {
          stopPolling();
          setRefreshRunning(false);
          setRefreshProgress(null);
          setRefreshNotice({ tone: 'error', text: `刷新失败：${status.message || '未知错误'}` });
          return;
        }
        if (terminalRevisionIsCurrent) {
          setRefreshProgress(status.message || '刷新进行中…');
        }
      } else if (result.kind === 'forbidden') {
        stopPolling();
        setRefreshStatusReadState('forbidden');
        setRefreshRunning(false);
        setRefreshProgress(null);
        setRefreshNotice(null);
        return;
      } else {
        setRefreshProgress('刷新状态暂不可用，正在重试…');
      }
      if (!ownsPollingScope()) return;
      if (Date.now() - pollStartedAtRef.current > REFRESH_POLL_TIMEOUT_MS) {
        stopPolling();
        setRefreshStatusReadState('unavailable');
        setRefreshRunning(false);
        setRefreshProgress(null);
        setRefreshNotice({
          tone: 'warn',
          text: '刷新等待超时（45 分钟），请稍后查看刷新记录。',
        });
        return;
      }
      pollTimerRef.current = window.setTimeout(
        () => void pollTick(generation, headers),
        REFRESH_POLL_INTERVAL_MS,
      );
    },
    [reloadDataset, stopPolling],
  );

  const startPolling = useCallback(
    (headers: IdentitySessionHeaders) => {
      stopPolling();
      pollingRef.current = true;
      const generation = pollingGenerationRef.current;
      pollStartedAtRef.current = Date.now();
      setRefreshRunning(true);
      void pollTick(generation, headers);
    },
    [pollTick, stopPolling],
  );

  useEffect(() => {
    const headers = requestHeaders;
    const readGeneration = refreshStatusReadGenerationRef.current + 1;
    refreshStatusReadGenerationRef.current = readGeneration;
    let cancelled = false;
    stopPolling();
    refreshSubmissionScopeRef.current = null;
    refreshTerminalBaselineRef.current = null;
    observedCurrentRunStatusRef.current = false;
    setLastRefresh(null);
    setRefreshStatusReadState('loading');
    setRefreshRunning(false);
    setRefreshProgress(null);
    setRefreshNotice(null);
    if (!canManage) {
      setRefreshStatusReadState('ready');
      return () => {
        cancelled = true;
        stopPolling();
      };
    }
    void getMediaPricesRefreshStatus(headers).then((result) => {
      if (
        cancelled ||
        refreshStatusReadGenerationRef.current !== readGeneration ||
        activeRequestHeadersRef.current !== headers
      ) {
        return;
      }
      if (result.kind !== 'ready') {
        setRefreshStatusReadState(result.kind);
        return;
      }
      setRefreshStatusReadState('ready');
      setLastRefresh(result.data);
      if (result.data.state === 'running') {
        observedCurrentRunStatusRef.current = true;
        startPolling(headers);
      }
    });
    return () => {
      cancelled = true;
      if (refreshSubmissionScopeRef.current === headers) {
        refreshSubmissionScopeRef.current = null;
      }
      stopPolling();
    };
  }, [canManage, refreshStatusAttempt, requestHeaders, startPolling, stopPolling]);

  const startRefresh = async () => {
    if (!canManage) return;
    const headers = requestHeaders;
    if (refreshSubmissionScopeRef.current !== null) return;
    const priorRefreshStatusReadState = refreshStatusReadState;
    refreshSubmissionScopeRef.current = headers;
    refreshStatusReadGenerationRef.current += 1;
    refreshTerminalBaselineRef.current = lastRefresh
      ? mediaPricesRefreshRevision(lastRefresh)
      : null;
    observedCurrentRunStatusRef.current = false;
    setRefreshNotice(null);
    setRefreshProgress('正在请求刷新…');
    setRefreshStatusReadState('loading');
    setRefreshRunning(true);
    const result = await requestMediaPricesRefresh(headers);
    if (
      activeRequestHeadersRef.current !== headers ||
      refreshSubmissionScopeRef.current !== headers
    ) {
      return;
    }
    refreshSubmissionScopeRef.current = null;
    if (result.kind === 'started') {
      setRefreshProgress('刷新已启动…');
      startPolling(headers);
      return;
    }
    if (result.kind === 'already_running') {
      setRefreshNotice({ tone: 'info', text: '已有刷新进行中，继续跟踪进度。' });
      startPolling(headers);
      return;
    }
    setRefreshRunning(false);
    setRefreshProgress(null);
    if (result.kind === 'forbidden') {
      setRefreshStatusReadState('forbidden');
      setRefreshNotice(null);
      return;
    }
    setRefreshStatusReadState(
      priorRefreshStatusReadState === 'loading' ? 'unavailable' : priorRefreshStatusReadState,
    );
    setRefreshNotice({ tone: 'error', text: '刷新请求失败，请稍后重试。' });
  };

  if (state === 'loading') {
    return (
      <section className="media-prices" aria-label="媒体比价台">
        <StatePanel state="loading" />
      </section>
    );
  }
  if (state === 'forbidden') {
    return (
      <section className="media-prices" aria-label="媒体比价台">
        <StatePanel state="forbidden" />
      </section>
    );
  }
  if (state === 'missing') {
    return (
      <section className="media-prices" aria-label="媒体比价台">
        <div className="media-prices-state warning">
          <p>数据集尚未生成，可直接在本页启动首次生成。</p>
          {canManage ? (
            <button
              type="button"
              className="primary"
              disabled={refreshRunning || refreshStatusReadState === 'forbidden'}
              onClick={() => void startRefresh()}
            >
              {refreshRunning ? '生成中…' : '立即生成数据集'}
            </button>
          ) : (
            <p>请登录 Operations Web 后生成数据集。</p>
          )}
          {refreshProgress ? <p role="status">{refreshProgress}</p> : null}
          {refreshNotice ? (
            <div className={`media-prices-notice ${refreshNotice.tone}`} role="alert">
              {refreshNotice.text}
            </div>
          ) : null}
        </div>
      </section>
    );
  }
  if (state === 'unavailable' || !dataset) {
    return (
      <section className="media-prices" aria-label="媒体比价台">
        <StatePanel state="failed" onRetry={() => setAttempt((value) => value + 1)} />
      </section>
    );
  }

  const partialSources = Object.entries(dataset.partial)
    .filter(([, value]) => value)
    .map(([key]) => dataset.sources[key] ?? key);

  return (
    <section className="media-prices" aria-label="媒体比价数据集">
      <header className="media-prices-heading">
        <div>
          <span className="eyebrow">offline dataset artifact</span>
          <h2>离线数据集快照</h2>
          <p>
            六平台新闻 / 自媒体目录比价 · 新闻快照生成于 {dataset.generatedAt}
            {dataset.sha256 ? ` · sha256 ${dataset.sha256.slice(0, 12)}…` : ''}
            {partialSources.length > 0 ? ` · ${partialSources.join('、')}为部分采集` : ''}
          </p>
        </div>
        <div className="media-prices-actions">
          {canManage ? (
            <button
              type="button"
              className="primary"
              disabled={refreshRunning || refreshStatusReadState === 'forbidden'}
              onClick={() => void startRefresh()}
            >
              {refreshRunning ? '刷新中…' : '刷新数据'}
            </button>
          ) : (
            <a className="operations-login-link" href="/platform/operations/login">
              内部人员登录
            </a>
          )}
          {activeCatalog === 'news' ? (
            <button type="button" onClick={exportCsv}>
              导出筛选 CSV
            </button>
          ) : null}
        </div>
      </header>
      <div className="media-catalog-tabs" role="tablist" aria-label="媒体目录类型">
        {(
          [
            ['news', '新闻媒体'],
            ['wemedia', '自媒体'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={activeCatalog === key}
            className={activeCatalog === key ? 'active' : ''}
            onClick={() => {
              setActiveCatalog(key);
              setExportNotice(null);
              updateClientUrlParameters(
                { media_tab: key === 'wemedia' ? 'wemedia' : null },
                [],
                false,
              );
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {canManage && refreshProgress ? (
        <div className="media-prices-notice progress" role="status" aria-live="polite">
          {refreshProgress}
        </div>
      ) : null}
      {canManage && refreshNotice ? (
        <div
          className={`media-prices-notice ${refreshNotice.tone}`}
          role={refreshNotice.tone === 'error' ? 'alert' : 'status'}
        >
          {refreshNotice.text}
        </div>
      ) : null}
      {canManage && refreshStatusReadState === 'forbidden' ? (
        <div className="media-prices-notice error" role="alert">
          权限不足：无法查看或启动数据刷新。
        </div>
      ) : null}
      {canManage && refreshStatusReadState === 'unavailable' && !refreshRunning ? (
        <div className="media-prices-notice error retryable" role="alert">
          <span>刷新状态读取失败，当前状态未知。</span>
          <button type="button" onClick={() => setRefreshStatusAttempt((current) => current + 1)}>
            重试刷新状态
          </button>
        </div>
      ) : null}
      {activeCatalog === 'news' && exportNotice ? (
        <div className="media-prices-notice" role="status">
          {exportNotice}
        </div>
      ) : null}
      {activeCatalog === 'news' ? (
        <>
          <section className="metric-row">
            <article>
              <span>六平台目录数</span>
              <strong className="metric-split">
                {PLATFORMS.map((platform) => (
                  <em key={platform.key}>
                    {platform.label} {dataset.stats.counts[platform.key] ?? 0}
                  </em>
                ))}
              </strong>
            </article>
            <article>
              <span>去重总数</span>
              <strong>{dataset.stats.unique_media}</strong>
            </article>
            <article>
              <span>≥2 家重合</span>
              <strong>{dataset.stats.matched_2plus}</strong>
            </article>
            <article>
              <span>GEO 并集</span>
              <strong>{dataset.stats.geo_union}</strong>
            </article>
            <article>
              <span>GEO 多源交叉</span>
              <strong>{dataset.stats.geo_multi_src}</strong>
            </article>
            <article title="互联网新闻信息稿源单位名单（网信办，截至 2025 年 6 月）">
              <span>稿源白名单</span>
              <strong>{dataset.stats.whitelist ?? '—'}</strong>
            </article>
            <article className="metric-refresh">
              <span>数据更新</span>
              <strong className="metric-refresh-value">{dataset.generatedAt}</strong>
              <small
                title={
                  canManage
                    ? formatRefreshCardSubtitle(lastRefresh, refreshStatusReadState)
                    : '公开只读快照'
                }
              >
                {canManage
                  ? formatRefreshCardSubtitle(lastRefresh, refreshStatusReadState)
                  : '公开只读快照'}
              </small>
            </article>
          </section>

          <FilterBar label="媒体比价筛选" className="media-prices-filters">
            <input
              type="search"
              aria-label="搜索媒体"
              placeholder="搜索名称 / 门户 / 省份 / 备注"
              value={filters.search}
              onChange={(event) => updateFilters({ search: event.target.value }, true)}
            />
            <label>
              <input
                type="checkbox"
                checked={filters.onlyGeo}
                onChange={(event) => updateFilters({ onlyGeo: event.target.checked })}
              />
              仅 GEO
            </label>
            <label>
              <input
                type="checkbox"
                checked={filters.onlyMultiSrc}
                onChange={(event) => updateFilters({ onlyMultiSrc: event.target.checked })}
              />
              仅多源
            </label>
            <label title="互联网新闻信息稿源单位名单（网信办，截至 2025 年 6 月）">
              <input
                type="checkbox"
                checked={filters.whitelistOnly}
                onChange={(event) => updateFilters({ whitelistOnly: event.target.checked })}
              />
              稿源白名单
            </label>
            <div className="geo-filter" role="group" aria-label="AI 平台筛选">
              {GEO_ORDER.map((key) => (
                <button
                  key={key}
                  type="button"
                  className={`geo-badge geo-${key}${filters.geoKeys.includes(key) ? ' active' : ''}`}
                  aria-pressed={filters.geoKeys.includes(key)}
                  onClick={() =>
                    updateFilters({
                      geoKeys: filters.geoKeys.includes(key)
                        ? filters.geoKeys.filter((item) => item !== key)
                        : [...filters.geoKeys, key],
                    })
                  }
                >
                  {GEO_PLATFORM_LABELS[key]}
                </button>
              ))}
            </div>
            <select
              aria-label="价格带"
              value={filters.priceBand}
              onChange={(event) =>
                updateFilters({ priceBand: event.target.value as MediaPricesPriceBand })
              }
            >
              <option value="all">全部价格带</option>
              <option value="le100">最低价 ≤100</option>
              <option value="100-500">100–500</option>
              <option value="500-2000">500–2000</option>
              <option value="gt2000">&gt;2000</option>
              <option value="none">无报价</option>
            </select>
            <select
              aria-label="门户"
              value={filters.portal}
              onChange={(event) => updateFilters({ portal: event.target.value })}
            >
              <option value="">全部门户</option>
              {portals.map((portal) => (
                <option key={portal} value={portal}>
                  {portal}
                </option>
              ))}
            </select>
            <select
              aria-label="收录"
              value={filters.include}
              onChange={(event) => updateFilters({ include: event.target.value })}
            >
              <option value="">全部收录</option>
              {includes.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
            <select
              aria-label="排序"
              value={filters.sort}
              onChange={(event) => updateFilters({ sort: event.target.value as MediaPricesSort })}
            >
              <option value="best-asc">最低价 ↑</option>
              <option value="best-desc">最低价 ↓</option>
              <option value="save-desc">相对 prfabu 省% ↓</option>
              <option value="pcw-desc">权重 PC ↓</option>
              <option value="pubrate-desc">出稿率 ↓</option>
            </select>
          </FilterBar>

          <p className="media-prices-summary" aria-live="polite">
            筛选 {summary.total} 条
          </p>

          {dataset.rows.length === 0 ? (
            <StatePanel state="real-zero" />
          ) : pageRows.length === 0 ? (
            <StatePanel state="empty" />
          ) : (
            <TableRegion label="媒体比价结果" className="media-prices-table">
              <table>
                <thead>
                  <tr>
                    {canManage ? <th>选择</th> : null}
                    <th>名称</th>
                    <th>门户</th>
                    <th>类型</th>
                    <th>收录</th>
                    <th>权重PC</th>
                    <th>权重M</th>
                    <th>出稿率</th>
                    <th>GEO</th>
                    {PLATFORMS.map((platform) => (
                      <th key={platform.key}>{platform.label}</th>
                    ))}
                    <th>最低价</th>
                    <th>参考链接</th>
                    <th>备注</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((row) => (
                    <tr key={row.name}>
                      {canManage ? (
                        <td data-label="选择">
                          <input
                            type="checkbox"
                            aria-label={`选择${row.name}发帖`}
                            checked={
                              postingSelections[postingSelectionKey('news', row.name)] !== undefined
                            }
                            disabled={
                              !dataset.sha256 ||
                              !PLATFORMS.some(
                                ({ key }) => row.prices[key] != null && Boolean(row.ids?.[key]),
                              )
                            }
                            onChange={(event) => selectNewsRow(row, event.target.checked)}
                          />
                        </td>
                      ) : null}
                      <td data-label="名称" className="cell-name" title={row.name}>
                        {row.name}
                        {row.whitelist === true ? (
                          <span
                            className="wl-badge"
                            title="互联网新闻信息稿源单位名单（网信办，截至 2025 年 6 月）"
                          >
                            稿源
                          </span>
                        ) : null}
                      </td>
                      <td data-label="门户">{row.portal ?? '—'}</td>
                      <td data-label="类型">{row.channel ?? '—'}</td>
                      <td data-label="收录">{row.include ?? '—'}</td>
                      <td data-label="权重PC">{row.pc_w ?? '—'}</td>
                      <td data-label="权重M">{row.m_w ?? '—'}</td>
                      <td data-label="出稿率">{row.pub_rate ?? '—'}</td>
                      <td data-label="GEO">
                        {row.geo.length === 0
                          ? '—'
                          : row.geo.map((key) => (
                              <span
                                key={key}
                                className={`geo-badge geo-${key}`}
                                title={GEO_PLATFORM_LABELS[key] ?? key}
                              >
                                {GEO_BADGE_SHORT[key] ?? key}
                              </span>
                            ))}
                      </td>
                      {PLATFORMS.map((platform) => (
                        <td
                          key={platform.key}
                          data-label={platform.label}
                          className={
                            isPlatformBestPrice(row, platform.key) ? 'price best' : 'price'
                          }
                        >
                          {formatPrice(row.prices[platform.key])}
                        </td>
                      ))}
                      <td data-label="最低价" className="price">
                        {formatPrice(row.best)}
                      </td>
                      <td data-label="参考链接" className="cell-links">
                        {row.case || row.site ? (
                          <>
                            {row.case ? (
                              <a href={row.case} target="_blank" rel="noopener noreferrer">
                                案例
                              </a>
                            ) : null}
                            {row.site ? (
                              <a href={row.site} target="_blank" rel="noopener noreferrer">
                                站点
                              </a>
                            ) : null}
                          </>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td data-label="备注" className="cell-remark" title={row.remark ?? ''}>
                        {row.remark ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableRegion>
          )}
          <Pagination
            page={safePage}
            pageCount={pageCount}
            onPageChange={(nextPage) => {
              setPage(nextPage);
              setExportNotice(null);
              writeMediaPricesUrlState(filters, nextPage);
            }}
            label="媒体比价分页"
          />
        </>
      ) : null}
      <WemediaPrices
        session={session}
        active={activeCatalog === 'wemedia'}
        reloadRevision={wemediaReloadRevision}
        postingSelections={postingSelections}
        onTogglePosting={(row, catalogSha256, checked) => {
          const key = postingSelectionKey('wemedia', row.name, row.platform);
          setPostingSelections((current) => {
            if (!checked) {
              const next = { ...current };
              delete next[key];
              return next;
            }
            const options = Object.fromEntries(
              PLATFORMS.flatMap(({ key: provider }) => {
                const quotedPrice = row.prices[provider];
                const providerMediaId = row.ids?.[provider];
                return quotedPrice != null && providerMediaId
                  ? [[provider, { quotedPrice, providerMediaId } satisfies PostingProviderOption]]
                  : [];
              }),
            ) as Partial<Record<MediaPricesPlatform, PostingProviderOption>>;
            const provider =
              (row.best_plat && options[row.best_plat] ? row.best_plat : undefined) ??
              PLATFORMS.map((item) => item.key).find(
                (candidate) => options[candidate] !== undefined,
              );
            if (!provider) return current;
            return {
              ...current,
              [key]: {
                key,
                catalogType: 'wemedia',
                catalogSha256,
                mediaName: row.name,
                mediaPlatform: row.platform,
                options,
                provider,
              },
            };
          });
        }}
      />
      {session && selectedPostingItems.length > 0 ? (
        <section className="posting-handoff-tray" aria-labelledby="posting-handoff-tray-title">
          <header>
            <div>
              <span className="eyebrow">posting selection</span>
              <h3 id="posting-handoff-tray-title">已选 {selectedPostingItems.length} 个目标</h3>
              <p>在这里确定每个媒体的采购平台，然后进入独立发帖页。</p>
            </div>
            <button type="button" onClick={() => setPostingSelections({})}>
              清空
            </button>
          </header>
          <div className="posting-selection-list">
            {selectedPostingItems.map((selection) => (
              <article key={selection.key}>
                <div>
                  <strong>{selection.mediaName}</strong>
                  <small>
                    {selection.catalogType === 'wemedia'
                      ? `自媒体 · ${selection.mediaPlatform}`
                      : '新闻媒体'}
                  </small>
                </div>
                <label>
                  采购平台
                  <select
                    aria-label={`${selection.mediaName}采购平台`}
                    value={selection.provider}
                    onChange={(event) => {
                      const provider = event.target.value as MediaPricesPlatform;
                      if (!selection.options[provider]) return;
                      setPostingSelections((current) => ({
                        ...current,
                        [selection.key]: { ...selection, provider },
                      }));
                    }}
                  >
                    {PLATFORMS.flatMap(({ key, label }) => {
                      const option = selection.options[key];
                      return option ? (
                        <option key={key} value={key}>
                          {label} · ¥{option.quotedPrice}
                          {key === 'prfabu' ? ' · 可自动' : ' · 下单待接入'}
                        </option>
                      ) : (
                        []
                      );
                    })}
                  </select>
                </label>
                <button
                  type="button"
                  aria-label={`移除${selection.mediaName}`}
                  onClick={() =>
                    setPostingSelections((current) => {
                      const next = { ...current };
                      delete next[selection.key];
                      return next;
                    })
                  }
                >
                  移除
                </button>
              </article>
            ))}
          </div>
          <div className="posting-handoff-actions">
            <p>选单只在当前浏览器标签会话中保存两小时；发帖服务端仍会重新核验。</p>
            <button
              type="button"
              className="primary"
              onClick={() => {
                const created = createPostingHandoff({
                  tenantId: session.tenantId,
                  actorId: session.actorId,
                  selections: selectedPostingItems,
                });
                if (!created) {
                  setHandoffNotice('选单保存失败，请确认浏览器允许会话存储后重试。');
                  return;
                }
                window.location.assign(created.href);
              }}
            >
              去发帖页配置内容
            </button>
          </div>
          {handoffNotice ? (
            <div className="media-prices-notice error" role="alert">
              {handoffNotice}
            </div>
          ) : null}
        </section>
      ) : null}
      <footer className="security-note">
        {canManage
          ? '比价数据由离线脚本刷新；本页只选择媒体和采购平台，账号及发帖内容统一在发帖页管理。'
          : '当前为公开只读视图；刷新数据与选择投放目标仅对已登录的内部人员开放。'}
      </footer>
    </section>
  );
}
