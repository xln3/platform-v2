import { useEffect, useMemo, useRef, useState } from 'react';
import {
  getMediaWemediaDataset,
  type IdentitySessionHeaders,
  type MediaPricesPlatform,
  type MediaWemediaDataset,
  type MediaWemediaDatasetRow,
} from '@geo/api-client';
import {
  FilterBar,
  Pagination,
  StatePanel,
  TableRegion,
  downloadSafeGeneratedFile,
} from '@geo/design-system';
import { postingSelectionKey, type PostingSelection } from './PostingComposer';

const PLATFORMS: { key: MediaPricesPlatform; label: string }[] = [
  { key: 'prfabu', label: 'prfabu' },
  { key: 'toumeiw', label: '投媒网' },
  { key: 'mtpfw', label: '媒体批发网' },
  { key: 'meititejia', label: '媒体特价网' },
  { key: 'meijiehezi', label: '媒介盒子' },
  { key: 'pinda', label: '品达发稿' },
];
const GEO_ORDER = ['a', 'b', 'c', 'd', 'e', 'f', 'z'] as const;
const GEO_PLATFORM_LABELS: Record<string, string> = {
  a: 'DeepSeek',
  b: '豆包',
  c: '通义千问',
  d: '腾讯元宝',
  e: '文心一言',
  f: 'Kimi',
  z: '其他',
};
const AUDIENCE_RANK: Record<string, number> = {
  '0-1000': 1,
  '1001-5000': 5,
  '5001-1万': 10,
  '1万-5万': 50,
  '5万-10万': 100,
  '10万以上': 101,
  '10万-100万': 1000,
  '100万以上': 1001,
};
const PAGE_SIZE = 100;

function audienceRank(value: string | undefined): number {
  if (!value) return -1;
  const labelled = AUDIENCE_RANK[value];
  if (labelled !== undefined) return labelled;
  const numeric = Number(value.replaceAll(',', ''));
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : -1;
}

type Session = {
  tenantId: string;
  actorId: string;
  role: 'operator' | 'reviewer' | 'admin';
  headers: IdentitySessionHeaders;
};

export type WemediaSort = 'best-asc' | 'best-desc' | 'save-desc' | 'fans-desc' | 'reads-desc';
export type WemediaPriceBand = 'all' | 'le100' | '100-500' | '500-2000' | 'gt2000' | 'none';
export type WemediaFilters = {
  search: string;
  onlyGeo: boolean;
  onlyMultiSrc: boolean;
  geoKeys: string[];
  priceBand: WemediaPriceBand;
  platform: string;
  sort: WemediaSort;
};

export const defaultWemediaFilters: WemediaFilters = {
  search: '',
  onlyGeo: false,
  onlyMultiSrc: false,
  geoKeys: [],
  priceBand: 'all',
  platform: '',
  sort: 'best-asc',
};

function prfabuSavings(row: MediaWemediaDatasetRow): number | null {
  const prfabu = row.prices.prfabu;
  if (prfabu == null || prfabu <= 0 || row.best == null || row.best >= prfabu) return null;
  return (prfabu - row.best) / prfabu;
}

export function filterWemediaRows(
  rows: MediaWemediaDatasetRow[],
  filters: WemediaFilters,
): MediaWemediaDatasetRow[] {
  const search = filters.search.trim().toLowerCase();
  const filtered = rows.filter((row) => {
    if (
      search &&
      !`${row.name} ${row.platform} ${row.industry ?? ''} ${row.province ?? ''} ${row.remark ?? ''}`
        .toLowerCase()
        .includes(search)
    ) {
      return false;
    }
    if (filters.onlyGeo && row.geo.length === 0) return false;
    if (filters.onlyMultiSrc && row.n_src < 2) return false;
    if (filters.geoKeys.length > 0 && !filters.geoKeys.some((key) => row.geo.includes(key))) {
      return false;
    }
    if (filters.platform && row.platform !== filters.platform) return false;
    if (filters.priceBand === 'none') return row.best == null;
    if (filters.priceBand !== 'all') {
      if (row.best == null) return false;
      if (filters.priceBand === 'le100' && row.best > 100) return false;
      if (filters.priceBand === '100-500' && (row.best <= 100 || row.best > 500)) return false;
      if (filters.priceBand === '500-2000' && (row.best <= 500 || row.best > 2000)) return false;
      if (filters.priceBand === 'gt2000' && row.best <= 2000) return false;
    }
    return true;
  });
  return [...filtered].sort((left, right) => {
    if (filters.sort === 'save-desc') {
      return (prfabuSavings(right) ?? -1) - (prfabuSavings(left) ?? -1);
    }
    if (filters.sort === 'fans-desc') {
      return (
        (right.fans_level ?? audienceRank(right.fans)) -
        (left.fans_level ?? audienceRank(left.fans))
      );
    }
    if (filters.sort === 'reads-desc') {
      return (
        (right.reads_level ?? audienceRank(right.reads)) -
        (left.reads_level ?? audienceRank(left.reads))
      );
    }
    if (left.best == null && right.best == null) return 0;
    if (left.best == null) return 1;
    if (right.best == null) return -1;
    return filters.sort === 'best-desc' ? right.best - left.best : left.best - right.best;
  });
}

function csvEscape(value: string | number | null | undefined): string {
  const text = value == null ? '' : String(value);
  return /[",\r\n]/u.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function buildWemediaCsv(rows: MediaWemediaDatasetRow[]): string {
  const lines = [
    [
      'name',
      'platform',
      'industry',
      'account_auth',
      'fans',
      'reads',
      'geo',
      ...PLATFORMS.map(({ key }) => key),
      'best',
      'best_plat',
      'n_src',
      'remark',
      'case',
      'site',
    ].join(','),
  ];
  for (const row of rows) {
    lines.push(
      [
        row.name,
        row.platform,
        row.industry,
        row.account_auth,
        row.fans,
        row.reads,
        row.geo.join('|'),
        ...PLATFORMS.map(({ key }) => row.prices[key]),
        row.best,
        row.best_plat,
        row.n_src,
        row.remark,
        row.case,
        row.site,
      ]
        .map(csvEscape)
        .join(','),
    );
  }
  return lines.join('\r\n');
}

function formatPrice(value: number | null | undefined): string {
  return value == null ? '—' : `¥${value}`;
}

function isBest(row: MediaWemediaDatasetRow, platform: MediaPricesPlatform): boolean {
  return row.best != null && row.prices[platform] === row.best;
}

export function WemediaPrices({
  session,
  active,
  reloadRevision,
  postingSelections,
  onTogglePosting,
}: {
  session: Session | undefined;
  active: boolean;
  reloadRevision: number;
  postingSelections: Record<string, PostingSelection>;
  onTogglePosting: (row: MediaWemediaDatasetRow, checked: boolean) => void;
}) {
  const canSelect = session !== undefined;
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
  const [state, setState] = useState<
    'idle' | 'loading' | 'ready' | 'forbidden' | 'missing' | 'unavailable'
  >('idle');
  const [dataset, setDataset] = useState<MediaWemediaDataset | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [filters, setFilters] = useState(defaultWemediaFilters);
  const [page, setPage] = useState(1);
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const loadedKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!active) return;
    const loadKey = `${requestHeaders['X-Tenant-Id'] ?? ''}\u0000${requestHeaders['X-Actor-Id'] ?? ''}\u0000${reloadRevision}\u0000${attempt}`;
    if (loadedKeyRef.current === loadKey) return;
    let cancelled = false;
    setState('loading');
    void getMediaWemediaDataset(requestHeaders).then((result) => {
      if (cancelled) return;
      if (result.kind === 'ready') {
        setDataset(result.data);
        setState('ready');
        loadedKeyRef.current = loadKey;
      } else {
        setDataset(null);
        setState(result.kind);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [active, attempt, reloadRevision, requestHeaders]);

  const filtered = useMemo(
    () => (dataset ? filterWemediaRows(dataset.rows, filters) : []),
    [dataset, filters],
  );
  const accountPlatforms = useMemo(
    () =>
      dataset
        ? [...new Set(dataset.rows.map((row) => row.platform))].sort((a, b) =>
            a.localeCompare(b, 'zh-CN'),
          )
        : [],
    [dataset],
  );
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pageRows = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  if (!active) return null;
  if (state === 'idle' || state === 'loading') return <StatePanel state="loading" />;
  if (state === 'forbidden') return <StatePanel state="forbidden" />;
  if (state === 'missing') {
    return (
      <div className="media-prices-state warning">
        {canSelect ? '自媒体数据集尚未生成，请点击上方“刷新数据”构建。' : '自媒体数据集尚未生成。'}
      </div>
    );
  }
  if (state === 'unavailable' || !dataset) {
    return <StatePanel state="failed" onRetry={() => setAttempt((value) => value + 1)} />;
  }

  return (
    <div className="wemedia-dataset">
      <div className="media-prices-subheading">
        <p>
          自媒体目录按需加载 · 生成于 {dataset.generatedAt}
          {dataset.sha256 ? ` · sha256 ${dataset.sha256.slice(0, 12)}…` : ''}
        </p>
        <button
          type="button"
          onClick={() => {
            const content = buildWemediaCsv(filtered);
            const exported = downloadSafeGeneratedFile({
              kind: 'csv',
              fileName: 'media-wemedia.csv',
              content,
            });
            setExportNotice(
              exported
                ? `已导出 ${filtered.length} 条筛选结果（media-wemedia.csv）。`
                : '导出内容超出 2MB 上限，请缩小筛选范围后重试。',
            );
          }}
        >
          导出自媒体 CSV
        </button>
      </div>
      {exportNotice ? (
        <div className="media-prices-notice" role="status">
          {exportNotice}
        </div>
      ) : null}
      <section className="metric-row">
        <article>
          <span>六平台目录数</span>
          <strong className="metric-split">
            {PLATFORMS.map(({ key, label }) => (
              <em key={key}>
                {label} {dataset.stats.counts[key] ?? 0}
              </em>
            ))}
          </strong>
        </article>
        <article>
          <span>账号去重数</span>
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
      </section>

      <FilterBar label="自媒体比价筛选" className="media-prices-filters">
        <input
          type="search"
          aria-label="搜索自媒体"
          placeholder="搜索账号 / 平台 / 行业 / 备注"
          value={filters.search}
          onChange={(event) => {
            setFilters((current) => ({ ...current, search: event.target.value }));
            setPage(1);
          }}
        />
        <label>
          <input
            type="checkbox"
            checked={filters.onlyGeo}
            onChange={(event) => {
              setFilters((current) => ({ ...current, onlyGeo: event.target.checked }));
              setPage(1);
            }}
          />
          仅 GEO
        </label>
        <label>
          <input
            type="checkbox"
            checked={filters.onlyMultiSrc}
            onChange={(event) => {
              setFilters((current) => ({ ...current, onlyMultiSrc: event.target.checked }));
              setPage(1);
            }}
          />
          仅多源
        </label>
        <div className="geo-filter" role="group" aria-label="自媒体 AI 平台筛选">
          {GEO_ORDER.map((key) => (
            <button
              key={key}
              type="button"
              className={`geo-badge geo-${key}${filters.geoKeys.includes(key) ? ' active' : ''}`}
              aria-pressed={filters.geoKeys.includes(key)}
              onClick={() => {
                setFilters((current) => ({
                  ...current,
                  geoKeys: current.geoKeys.includes(key)
                    ? current.geoKeys.filter((item) => item !== key)
                    : [...current.geoKeys, key],
                }));
                setPage(1);
              }}
            >
              {GEO_PLATFORM_LABELS[key]}
            </button>
          ))}
        </div>
        <select
          aria-label="自媒体价格带"
          value={filters.priceBand}
          onChange={(event) => {
            setFilters((current) => ({
              ...current,
              priceBand: event.target.value as WemediaPriceBand,
            }));
            setPage(1);
          }}
        >
          <option value="all">全部价格带</option>
          <option value="le100">最低价 ≤100</option>
          <option value="100-500">100–500</option>
          <option value="500-2000">500–2000</option>
          <option value="gt2000">&gt;2000</option>
          <option value="none">无报价</option>
        </select>
        <select
          aria-label="自媒体平台"
          value={filters.platform}
          onChange={(event) => {
            setFilters((current) => ({ ...current, platform: event.target.value }));
            setPage(1);
          }}
        >
          <option value="">全部账号平台</option>
          {accountPlatforms.map((platform) => (
            <option key={platform} value={platform}>
              {platform}
            </option>
          ))}
        </select>
        <select
          aria-label="自媒体排序"
          value={filters.sort}
          onChange={(event) =>
            setFilters((current) => ({ ...current, sort: event.target.value as WemediaSort }))
          }
        >
          <option value="best-asc">最低价 ↑</option>
          <option value="best-desc">最低价 ↓</option>
          <option value="save-desc">相对 prfabu 省% ↓</option>
          <option value="fans-desc">粉丝量级 ↓</option>
          <option value="reads-desc">阅读量级 ↓</option>
        </select>
      </FilterBar>

      <p className="media-prices-summary" aria-live="polite">
        筛选 {filtered.length} 条；数据集仅在首次打开本 Tab 时下载。
      </p>
      {dataset.rows.length === 0 ? (
        <StatePanel state="real-zero" />
      ) : pageRows.length === 0 ? (
        <StatePanel state="empty" />
      ) : (
        <TableRegion label="自媒体比价结果" className="media-prices-table">
          <table>
            <thead>
              <tr>
                {canSelect ? <th>选择</th> : null}
                <th>名称</th>
                <th>平台</th>
                <th>行业</th>
                <th>认证</th>
                <th>粉丝数</th>
                <th>阅读数</th>
                <th>GEO</th>
                {PLATFORMS.map(({ key, label }) => (
                  <th key={key}>{label}</th>
                ))}
                <th>最低价</th>
                <th>参考链接</th>
                <th>备注</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row) => (
                <tr key={`${row.platform}\u0000${row.name}`}>
                  {canSelect ? (
                    <td data-label="选择">
                      <input
                        type="checkbox"
                        aria-label={`选择${row.name}发帖`}
                        checked={
                          postingSelections[
                            postingSelectionKey('wemedia', row.name, row.platform)
                          ] !== undefined
                        }
                        disabled={row.best_plat == null}
                        onChange={(event) => onTogglePosting(row, event.target.checked)}
                      />
                    </td>
                  ) : null}
                  <td data-label="名称" className="cell-name" title={row.name}>
                    {row.name}
                  </td>
                  <td data-label="平台">{row.platform}</td>
                  <td data-label="行业">{row.industry ?? '—'}</td>
                  <td data-label="认证">{row.account_auth ?? '—'}</td>
                  <td data-label="粉丝数">{row.fans ?? '—'}</td>
                  <td data-label="阅读数">{row.reads ?? '—'}</td>
                  <td data-label="GEO">
                    {row.geo.length === 0
                      ? '—'
                      : row.geo.map((key) => (
                          <span
                            key={key}
                            className={`geo-badge geo-${key}`}
                            title={GEO_PLATFORM_LABELS[key] ?? key}
                          >
                            {GEO_PLATFORM_LABELS[key] ?? key}
                          </span>
                        ))}
                  </td>
                  {PLATFORMS.map(({ key, label }) => (
                    <td
                      key={key}
                      data-label={label}
                      className={isBest(row, key) ? 'price best' : 'price'}
                    >
                      {formatPrice(row.prices[key])}
                    </td>
                  ))}
                  <td data-label="最低价" className="price">
                    {formatPrice(row.best)}
                  </td>
                  <td data-label="参考链接" className="cell-links">
                    {row.case ? (
                      <a href={row.case} target="_blank" rel="noopener noreferrer">
                        案例
                      </a>
                    ) : null}
                    {row.site ? (
                      <a href={row.site} target="_blank" rel="noopener noreferrer">
                        主页
                      </a>
                    ) : null}
                    {!row.case && !row.site ? '—' : null}
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
        onPageChange={(nextPage) => setPage(nextPage)}
        label="自媒体比价分页"
      />
    </div>
  );
}
