// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  MediaPricesDataset,
  MediaPricesRefreshStatus,
  MediaWemediaDataset,
} from '@geo/api-client';
import {
  getMediaPricesDataset,
  getMediaPricesRefreshStatus,
  getMediaWemediaDataset,
  requestMediaPricesRefresh,
} from '@geo/api-client';
import { downloadSafeGeneratedFile } from '@geo/design-system';
import {
  MediaPrices,
  REFRESH_POLL_INTERVAL_MS,
  REFRESH_POLL_TIMEOUT_MS,
  buildMediaPricesCsv,
  buildRefreshDoneNotice,
  defaultMediaPricesFilters,
  filterMediaPricesRows,
  formatRefreshCardSubtitle,
  isPlatformBestPrice,
  presentRefreshSources,
  prfabuSavings,
  readMediaPricesUrlState,
  summarizeMediaPrices,
  writeMediaPricesUrlState,
} from './MediaPrices';

vi.mock('@geo/api-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@geo/api-client')>();
  return {
    ...original,
    getMediaPricesDataset: vi.fn(),
    getMediaWemediaDataset: vi.fn(),
    getMediaPricesRefreshStatus: vi.fn(),
    requestMediaPricesRefresh: vi.fn(),
  };
});
vi.mock('@geo/design-system', async (importOriginal) => {
  const original = await importOriginal<typeof import('@geo/design-system')>();
  return { ...original, downloadSafeGeneratedFile: vi.fn(() => true) };
});

const session = {
  tenantId: 'tnt_test',
  actorId: 'usr_test',
  role: 'operator' as const,
  headers: {
    'X-Tenant-Id': 'tnt_test',
    'X-Actor-Id': 'subject_test',
    'X-Actor-Role': 'operator',
  },
};

const fixtureDataset: MediaPricesDataset = {
  generatedAt: '2026-07-27 15:53',
  sources: {
    prfabu: 'prfabu媒体管家',
    toumeiw: '投媒网',
    mtpfw: '媒体批发网',
    meititejia: '媒体特价网',
    meijiehezi: '媒介盒子',
    pinda: '品达发稿',
  },
  partial: { toumeiw: false },
  stats: {
    counts: { prfabu: 3, toumeiw: 2, mtpfw: 1, meititejia: 1, meijiehezi: 1, pinda: 1 },
    geo_counts: {
      prfabu: 1,
      toumeiw: 0,
      mtpfw: 0,
      meititejia: 0,
      meijiehezi: 0,
      pinda: 0,
    },
    unique_media: 4,
    matched_2plus: 3,
    matched_3: 0,
    geo_union: 2,
    geo_multi_src: 0,
    whitelist: 1,
  },
  rows: [
    {
      name: '人民网',
      prices: { prfabu: 100, toumeiw: 80 },
      ids: { prfabu: '1001', toumeiw: '2001' },
      best: 80,
      best_plat: 'toumeiw',
      spread: 1.3,
      n_src: 2,
      geo: ['b'],
      geo_n: 1,
      portal: '门户网站',
      channel: '新闻',
      include: '收录',
      remark: '普通备注',
      case: 'https://case.example.com/renminwang/1',
      site: 'http://www.people.com.cn/',
      whitelist: true,
    },
    {
      name: '新华网',
      prices: { prfabu: 50 },
      ids: { prfabu: '1002' },
      best: 50,
      best_plat: 'prfabu',
      spread: null,
      n_src: 1,
      geo: [],
      geo_n: 0,
      portal: '门户网站',
    },
    {
      name: '特价盒子媒体',
      prices: { meititejia: 60, meijiehezi: 55 },
      ids: { meititejia: '4001', meijiehezi: '5001' },
      best: 55,
      best_plat: 'meijiehezi',
      spread: 1.1,
      n_src: 2,
      geo: [],
      geo_n: 0,
      portal: '其他门户',
      channel: '新闻资讯',
      news_src: '非新闻源',
      pub_rate: 80,
    },
    {
      name: '价差媒体',
      prices: { prfabu: 300, mtpfw: 90 },
      ids: { prfabu: '1003', mtpfw: '3001' },
      best: 90,
      best_plat: 'mtpfw',
      spread: 3.3,
      n_src: 2,
      geo: ['a', 'f'],
      geo_n: 1,
      portal: '客户端',
    },
  ],
  sha256: 'a'.repeat(64),
};

const fixtureWemediaDataset: MediaWemediaDataset = {
  generatedAt: '2026-07-28 18:30',
  sources: fixtureDataset.sources,
  partial: {
    prfabu: false,
    toumeiw: false,
    mtpfw: false,
    meititejia: false,
    meijiehezi: false,
    pinda: false,
  },
  stats: {
    counts: { prfabu: 2, toumeiw: 1, mtpfw: 1, meititejia: 1, meijiehezi: 1, pinda: 1 },
    geo_counts: {
      prfabu: 1,
      toumeiw: 0,
      mtpfw: 0,
      meititejia: 0,
      meijiehezi: 0,
      pinda: 0,
    },
    unique_media: 2,
    matched_2plus: 1,
    matched_3: 0,
    geo_union: 1,
    geo_multi_src: 0,
  },
  rows: [
    {
      name: '融媒观察',
      platform: '百家号',
      prices: { prfabu: 100, toumeiw: 80 },
      ids: { prfabu: '1101', toumeiw: '2101' },
      best: 80,
      best_plat: 'toumeiw',
      spread: 1.3,
      n_src: 2,
      geo: ['e'],
      geo_n: 1,
      industry: '新闻',
      account_auth: '蓝V认证',
      fans: '1万-5万',
      reads: '5001-1万',
      fans_level: 50,
      reads_level: 10,
    },
    {
      name: '生活方式号',
      platform: '今日头条',
      prices: { prfabu: 30 },
      ids: { prfabu: '1102' },
      best: 30,
      best_plat: 'prfabu',
      spread: null,
      n_src: 1,
      geo: [],
      geo_n: 0,
      industry: '生活',
    },
  ],
  sha256: 'b'.repeat(64),
};

function mockReady() {
  vi.mocked(getMediaPricesDataset).mockResolvedValue({ kind: 'ready', data: fixtureDataset });
  vi.mocked(getMediaWemediaDataset).mockResolvedValue({
    kind: 'ready',
    data: fixtureWemediaDataset,
  });
}

const neverRefresh: MediaPricesRefreshStatus = {
  state: 'never',
  startedAt: null,
  updatedAt: null,
  message: '',
  sources: {},
};

const doneRefresh: MediaPricesRefreshStatus = {
  state: 'done',
  startedAt: '2026-07-27 16:00:00',
  updatedAt: '2026-07-27 16:01:12',
  message: 'prfabu 3 · 投媒网 2(限流) · 媒体批发网 1 · 媒体特价网 1 · 媒介盒子 1 · 品达发稿 1',
  sources: {
    prfabu: { status: 'ok', rows: 3, note: '' },
    toumeiw: { status: 'partial', rows: 2, note: 'rate_limited' },
    mtpfw: { status: 'ok', rows: 1, note: '' },
    meititejia: { status: 'ok', rows: 1, note: '' },
    meijiehezi: { status: 'ok', rows: 1, note: '' },
    pinda: { status: 'ok', rows: 1, note: '' },
  },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe('media prices pure logic', () => {
  it('filters by GEO toggle, geo platform keys and multi-source', () => {
    const rows = fixtureDataset.rows;
    expect(
      filterMediaPricesRows(rows, { ...defaultMediaPricesFilters, onlyGeo: true }).map(
        (row) => row.name,
      ),
    ).toEqual(['人民网', '价差媒体']);
    expect(
      filterMediaPricesRows(rows, { ...defaultMediaPricesFilters, geoKeys: ['a'] }).map(
        (row) => row.name,
      ),
    ).toEqual(['价差媒体']);
    expect(
      filterMediaPricesRows(rows, { ...defaultMediaPricesFilters, geoKeys: ['b', 'f'] }).map(
        (row) => row.name,
      ),
    ).toEqual(['人民网', '价差媒体']);
    expect(
      filterMediaPricesRows(rows, { ...defaultMediaPricesFilters, onlyMultiSrc: true }).map(
        (row) => row.name,
      ),
    ).toEqual(['特价盒子媒体', '人民网', '价差媒体']);
    expect(
      filterMediaPricesRows(rows, { ...defaultMediaPricesFilters, search: '新华' }).map(
        (row) => row.name,
      ),
    ).toEqual(['新华网']);
    expect(
      filterMediaPricesRows(rows, { ...defaultMediaPricesFilters, whitelistOnly: true }).map(
        (row) => row.name,
      ),
    ).toEqual(['人民网']);
  });

  it('detects the platform holding the best price and prfabu savings', () => {
    const [people, xinhua, , spread] = fixtureDataset.rows;
    expect(people && isPlatformBestPrice(people, 'toumeiw')).toBe(true);
    expect(people && isPlatformBestPrice(people, 'prfabu')).toBe(false);
    expect(xinhua && isPlatformBestPrice(xinhua, 'prfabu')).toBe(true);
    expect(xinhua && isPlatformBestPrice(xinhua, 'mtpfw')).toBe(false);
    expect(people && prfabuSavings(people)).toBeCloseTo(0.2);
    expect(xinhua && prfabuSavings(xinhua)).toBeNull();
    expect(spread && prfabuSavings(spread)).toBeCloseTo(0.7);
    const special = fixtureDataset.rows[2];
    expect(special && isPlatformBestPrice(special, 'meijiehezi')).toBe(true);
    expect(special && isPlatformBestPrice(special, 'meititejia')).toBe(false);
    const summary = summarizeMediaPrices(fixtureDataset.rows);
    expect(summary.total).toBe(4);
    expect(summary.prfabuNotBest).toBe(2);
    expect(summary.avgSavePct).toBeCloseTo(45);
  });

  it('escapes csv fields and summarizes savings honestly', () => {
    const csv = buildMediaPricesCsv(fixtureDataset.rows);
    const lines = csv.split('\r\n');
    expect(lines).toHaveLength(5);
    expect(lines[0]).toContain('name,portal,channel');
    expect(lines[0]).toContain('prfabu,toumeiw,mtpfw,meititejia,meijiehezi,pinda,best');
    expect(lines[0]).toContain('remark,case,site,whitelist');
    expect(lines[0]).not.toContain('spread');
    expect(lines[1]).toContain('人民网');
    expect(lines[1]).toContain('b');
    expect(lines[1]).toContain('https://case.example.com/renminwang/1');
    expect(lines[1]).toContain(',1');
    const specialLine = lines.find((line) => line.startsWith('特价盒子媒体'));
    expect(specialLine).toContain(',60,55,');
    const quoted = buildMediaPricesCsv([
      { ...fixtureDataset.rows[0]!, name: '含,逗号"引号"', remark: '换\n行' },
    ]);
    expect(quoted).toContain('"含,逗号""引号"""');
    expect(quoted).toContain('"换\n行"');
  });

  it('round-trips bounded URL filters and refuses secret-shaped query values', () => {
    const state = readMediaPricesUrlState(
      'https://geo.invalid/platform/operations/media-prices?media_q=%E6%96%B0%E5%8D%8E&media_geo=a%2Cf&media_band=100-500&media_sort=save-desc&media_wl=1&media_page=2',
    );
    expect(state).toEqual({
      filters: {
        ...defaultMediaPricesFilters,
        search: '新华',
        geoKeys: ['a', 'f'],
        priceBand: '100-500',
        sort: 'save-desc',
        whitelistOnly: true,
      },
      page: 2,
    });

    window.history.replaceState(null, '', '/platform/operations/media-prices');
    expect(
      writeMediaPricesUrlState(
        { ...defaultMediaPricesFilters, search: 'OTP 824911', portal: '门户网站' },
        3,
      ),
    ).toBe(true);
    const url = new URL(window.location.href);
    expect(url.searchParams.has('media_q')).toBe(false);
    expect(url.searchParams.get('media_portal')).toBe('门户网站');
    expect(url.searchParams.get('media_page')).toBe('3');
    expect(window.location.href).not.toMatch(/OTP|824911/i);
  });
});

describe('MediaPrices', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/platform/operations/media-prices');
    mockReady();
    vi.mocked(downloadSafeGeneratedFile).mockReturnValue(true);
    vi.mocked(getMediaPricesRefreshStatus).mockResolvedValue({
      kind: 'ready',
      data: neverRefresh,
    });
    vi.mocked(requestMediaPricesRefresh).mockResolvedValue({ kind: 'started' });
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it('renders stats and highlights the best platform price in green', async () => {
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    expect(screen.getByText('去重总数')).toBeTruthy();
    expect(screen.getByText('GEO 多源交叉')).toBeTruthy();
    const bestCell = screen
      .getAllByText('¥80')
      .find((element) => element.getAttribute('data-label') === '投媒网');
    expect(bestCell?.className).toContain('best');
    expect(screen.getByText('¥100').className).not.toContain('best');
    const caseLink = screen.getByRole('link', { name: '案例' });
    expect(caseLink.getAttribute('href')).toBe('https://case.example.com/renminwang/1');
    expect(caseLink.getAttribute('target')).toBe('_blank');
    expect(caseLink.getAttribute('rel')).toContain('noopener');
    expect(screen.getByRole('link', { name: '站点' }).getAttribute('href')).toBe(
      'http://www.people.com.cn/',
    );
    expect(document.querySelector('.wl-badge')?.textContent).toBe('稿源');
    expect(screen.queryByText('3.3x')).toBeNull();
    expect(screen.getByText('筛选 4 条')).toBeTruthy();
    expect(screen.queryByText(/非全网最低|平均可省/u)).toBeNull();
  });

  it('renders all six platform price columns with best-price highlight', async () => {
    render(<MediaPrices session={session} />);
    await screen.findByText('特价盒子媒体');
    for (const label of ['prfabu', '投媒网', '媒体批发网', '媒体特价网', '媒介盒子', '品达发稿']) {
      expect(screen.getAllByText(label).some((element) => element.tagName === 'TH')).toBe(true);
    }
    const bestCell = screen
      .getAllByText('¥55')
      .find((element) => element.getAttribute('data-label') === '媒介盒子');
    expect(bestCell?.className).toContain('best');
    const notBest = screen
      .getAllByText('¥60')
      .find((element) => element.getAttribute('data-label') === '媒体特价网');
    expect(notBest?.className).not.toContain('best');
    expect(screen.getByText('新闻资讯')).toBeTruthy();
  });

  it('freezes selected news and self-media provider choices before handing off to posting', async () => {
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    expect(screen.queryByText('自动发帖配置')).toBeNull();

    fireEvent.click(screen.getByLabelText('选择人民网发帖'));
    const newsProvider = screen.getByLabelText('人民网采购平台') as HTMLSelectElement;
    expect(newsProvider.value).toBe('toumeiw');
    fireEvent.change(newsProvider, { target: { value: 'prfabu' } });
    expect((screen.getByLabelText('人民网采购平台') as HTMLSelectElement).value).toBe('prfabu');

    fireEvent.click(screen.getByRole('tab', { name: '自媒体' }));
    await screen.findByText('融媒观察');
    fireEvent.click(screen.getByLabelText('选择融媒观察发帖'));
    expect(screen.getByLabelText('融媒观察采购平台')).toBeTruthy();
    expect(screen.getByText('已选 2 个目标')).toBeTruthy();
    expect(screen.getByRole('button', { name: '去发帖页配置内容' })).toBeTruthy();
  });

  it('keeps provider credentials and DOCX controls off the comparison page', async () => {
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    expect(screen.queryByText('平台账号与自动登录')).toBeNull();
    expect(screen.queryByLabelText(/密码/u)).toBeNull();
    expect(screen.queryByLabelText('图文 DOCX')).toBeNull();
  });

  it('loads the separate self-media dataset only when its tab is first opened and retains it', async () => {
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    expect(getMediaWemediaDataset).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('tab', { name: '自媒体' }));
    await screen.findByText('融媒观察');
    expect(getMediaWemediaDataset).toHaveBeenCalledTimes(1);
    expect(screen.getByText('账号去重数')).toBeTruthy();
    expect(screen.getByText('1万-5万')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('自媒体平台'), {
      target: { value: '今日头条' },
    });
    expect(screen.queryByText('融媒观察')).toBeNull();
    expect(screen.getByText('生活方式号')).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: '新闻媒体' }));
    await screen.findByText('人民网');
    fireEvent.click(screen.getByRole('tab', { name: '自媒体' }));
    await screen.findByText('生活方式号');
    expect(getMediaWemediaDataset).toHaveBeenCalledTimes(1);
    expect(new URL(window.location.href).searchParams.get('media_tab')).toBe('wemedia');
  });

  it('filters rows with the GEO toggle and AI platform buttons', async () => {
    render(<MediaPrices session={session} />);
    await screen.findByText('价差媒体');
    fireEvent.click(screen.getByLabelText('仅 GEO'));
    await waitFor(() => expect(screen.queryByText('新华网')).toBeNull());
    expect(screen.getByText('人民网')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('仅 GEO'));
    await screen.findByText('新华网');
    fireEvent.click(screen.getByRole('button', { name: 'DeepSeek' }));
    await waitFor(() => expect(screen.queryByText('人民网')).toBeNull());
    expect(screen.getByText('价差媒体')).toBeTruthy();
    expect(new URL(window.location.href).searchParams.get('media_geo')).toBe('a');
  });

  it('restores filters from URL and responds to browser history without another API read', async () => {
    window.history.replaceState(
      null,
      '',
      '/platform/operations/media-prices?media_geo=b&media_sort=save-desc',
    );
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    expect(screen.queryByText('新华网')).toBeNull();
    expect(screen.queryByText('价差媒体')).toBeNull();

    window.history.replaceState(null, '', '/platform/operations/media-prices?media_geo=a%2Cf');
    window.dispatchEvent(new PopStateEvent('popstate'));
    await screen.findByText('价差媒体');
    expect(screen.queryByText('人民网')).toBeNull();
    expect(getMediaPricesDataset).toHaveBeenCalledTimes(1);
  });

  it('warns when the export exceeds the safe download limit', async () => {
    vi.mocked(downloadSafeGeneratedFile).mockReturnValue(false);
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    fireEvent.click(screen.getByRole('button', { name: '导出筛选 CSV' }));
    await screen.findByText(/超出 2MB 上限/);
    expect(downloadSafeGeneratedFile).toHaveBeenCalledTimes(1);
  });

  it('starts first-time dataset generation in the page without terminal instructions', async () => {
    vi.mocked(getMediaPricesDataset).mockResolvedValue({ kind: 'missing' });
    render(<MediaPrices session={session} />);
    await screen.findByText('数据集尚未生成，可直接在本页启动首次生成。');
    expect(screen.queryByText(/脚本|终端/u)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '立即生成数据集' }));
    await screen.findByRole('button', { name: '生成中…' });
    expect(requestMediaPricesRefresh).toHaveBeenCalledTimes(1);
  });

  it('shows the forbidden state for unauthorized roles', async () => {
    vi.mocked(getMediaPricesDataset).mockResolvedValue({ kind: 'forbidden' });
    render(<MediaPrices session={session} />);
    await waitFor(() => expect(document.querySelector('.state-forbidden')).toBeTruthy());
  });

  it('retries only the failed dataset region and accepts an authoritative real zero', async () => {
    vi.mocked(getMediaPricesDataset)
      .mockResolvedValueOnce({ kind: 'unavailable' })
      .mockResolvedValueOnce({
        kind: 'ready',
        data: {
          ...fixtureDataset,
          rows: [],
          stats: {
            ...fixtureDataset.stats,
            counts: { prfabu: 0, toumeiw: 0, mtpfw: 0 },
            unique_media: 0,
            matched_2plus: 0,
            geo_union: 0,
            geo_multi_src: 0,
          },
        },
      });
    render(<MediaPrices session={session} />);
    fireEvent.click(await screen.findByRole('button', { name: '重试此区域' }));
    expect(await screen.findByText('结果为 0')).toBeTruthy();
    expect(getMediaPricesDataset).toHaveBeenCalledTimes(2);
  });

  it('shows the generated time and last refresh summary in the data-freshness card', async () => {
    vi.mocked(getMediaPricesRefreshStatus).mockResolvedValue({
      kind: 'ready',
      data: doneRefresh,
    });
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    expect(screen.getByText('数据更新')).toBeTruthy();
    expect(screen.getByText('2026-07-27 15:53')).toBeTruthy();
    expect(
      screen.getByText(
        '上次刷新：prfabu 3 · 投媒网 2(限流) · 媒体批发网 1 · 媒体特价网 1 · 媒介盒子 1 · 品达发稿 1',
      ),
    ).toBeTruthy();
  });

  it('does not mislabel an unavailable refresh-status read as never refreshed and retries locally', async () => {
    vi.mocked(getMediaPricesRefreshStatus)
      .mockResolvedValueOnce({ kind: 'unavailable' })
      .mockResolvedValueOnce({ kind: 'ready', data: neverRefresh });
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    await screen.findByText('刷新状态读取失败，当前状态未知。');
    expect(screen.queryByText('尚未刷新')).toBeNull();
    expect(getMediaPricesDataset).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '重试刷新状态' }));

    expect(await screen.findByText('尚未刷新')).toBeTruthy();
    expect(getMediaPricesRefreshStatus).toHaveBeenCalledTimes(2);
    expect(getMediaPricesDataset).toHaveBeenCalledTimes(1);
  });

  it('does not let a delayed initial status overwrite a refresh completed afterward', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const delayedInitialStatus =
      deferred<Awaited<ReturnType<typeof getMediaPricesRefreshStatus>>>();
    let statusReads = 0;
    vi.mocked(getMediaPricesRefreshStatus).mockImplementation(() => {
      statusReads += 1;
      if (statusReads === 1) return delayedInitialStatus.promise;
      return Promise.resolve({
        kind: 'ready',
        data:
          statusReads === 2
            ? { ...neverRefresh, state: 'running', message: '本轮刷新进行中' }
            : doneRefresh,
      });
    });
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    await screen.findByText('正在读取刷新状态…');

    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    await screen.findByText('本轮刷新进行中');
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    await screen.findByText(
      /刷新完成：prfabu 3 · 投媒网 2\(限流\) · 媒体批发网 1 · 媒体特价网 1 · 媒介盒子 1 · 品达发稿 1/,
    );
    await screen.findByText(
      '上次刷新：prfabu 3 · 投媒网 2(限流) · 媒体批发网 1 · 媒体特价网 1 · 媒介盒子 1 · 品达发稿 1',
    );

    await act(async () => {
      delayedInitialStatus.resolve({ kind: 'ready', data: neverRefresh });
      await Promise.resolve();
    });

    expect(screen.queryByText('尚未刷新')).toBeNull();
    expect(
      screen.getByText(
        '上次刷新：prfabu 3 · 投媒网 2(限流) · 媒体批发网 1 · 媒体特价网 1 · 媒介盒子 1 · 品达发稿 1',
      ),
    ).toBeTruthy();
    expect(statusReads).toBe(3);
  });

  it('exposes refresh-status permission denial without issuing a refresh write', async () => {
    vi.mocked(getMediaPricesRefreshStatus).mockResolvedValue({ kind: 'forbidden' });
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    await screen.findByText('权限不足：无法查看或启动数据刷新。');
    expect(screen.getByText('无权查看刷新状态')).toBeTruthy();
    expect(screen.getByRole('button', { name: '刷新数据' })).toHaveProperty('disabled', true);
    expect(requestMediaPricesRefresh).not.toHaveBeenCalled();
  });

  it('polls to done, reloads the dataset and shows the per-source summary', async () => {
    vi.mocked(getMediaPricesRefreshStatus)
      .mockResolvedValueOnce({ kind: 'ready', data: neverRefresh })
      .mockResolvedValue({ kind: 'ready', data: doneRefresh });
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    expect(
      await screen.findByText(
        /刷新完成：prfabu 3 · 投媒网 2\(限流\) · 媒体批发网 1 · 媒体特价网 1 · 媒介盒子 1 · 品达发稿 1/,
      ),
    ).toBeTruthy();
    await waitFor(() => expect(getMediaPricesDataset).toHaveBeenCalledTimes(2));
    expect(requestMediaPricesRefresh).toHaveBeenCalledTimes(1);
  });

  it('keeps polling when the first terminal status is unchanged from before the refresh', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const newerDone: MediaPricesRefreshStatus = {
      ...doneRefresh,
      startedAt: '2026-07-27 21:00:00',
      updatedAt: '2026-07-27 21:00:02',
      message: '新一轮刷新完成',
    };
    vi.mocked(getMediaPricesRefreshStatus)
      .mockResolvedValueOnce({ kind: 'ready', data: doneRefresh })
      .mockResolvedValueOnce({ kind: 'ready', data: doneRefresh })
      .mockResolvedValueOnce({ kind: 'ready', data: newerDone });
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    await screen.findByText(
      '上次刷新：prfabu 3 · 投媒网 2(限流) · 媒体批发网 1 · 媒体特价网 1 · 媒介盒子 1 · 品达发稿 1',
    );

    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    await screen.findByText('刷新已接受，正在等待新的终态记录…');
    expect(screen.queryByText(/刷新完成：/)).toBeNull();
    expect(getMediaPricesDataset).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });

    expect(await screen.findByText(/刷新完成：新一轮刷新完成/)).toBeTruthy();
    expect(getMediaPricesRefreshStatus).toHaveBeenCalledTimes(3);
    expect(getMediaPricesDataset).toHaveBeenCalledTimes(2);
  });

  it('fails closed instead of retaining an old dataset when the completed refresh reload is forbidden', async () => {
    vi.mocked(getMediaPricesDataset)
      .mockResolvedValueOnce({ kind: 'ready', data: fixtureDataset })
      .mockResolvedValueOnce({ kind: 'forbidden' });
    vi.mocked(getMediaPricesRefreshStatus)
      .mockResolvedValueOnce({ kind: 'ready', data: neverRefresh })
      .mockResolvedValueOnce({ kind: 'ready', data: doneRefresh });
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));

    await screen.findByText('无权查看');
    expect(screen.queryByText('人民网')).toBeNull();
    expect(screen.queryByText(/刷新完成：/)).toBeNull();
    expect(getMediaPricesDataset).toHaveBeenCalledTimes(2);
  });

  it('does not let an older completed-refresh reload clear the new identity refresh state', async () => {
    const delayedOldReload = deferred<Awaited<ReturnType<typeof getMediaPricesDataset>>>();
    let oldDatasetReads = 0;
    vi.mocked(getMediaPricesDataset).mockImplementation((headers) => {
      if (headers['X-Actor-Id'] === 'subject_test') {
        oldDatasetReads += 1;
        return oldDatasetReads === 1
          ? Promise.resolve({ kind: 'ready', data: fixtureDataset })
          : delayedOldReload.promise;
      }
      return Promise.resolve({
        kind: 'ready',
        data: {
          ...fixtureDataset,
          generatedAt: '2026-07-27 20:05',
          rows: [{ ...fixtureDataset.rows[0]!, name: '新身份媒体' }],
        },
      });
    });
    let oldStatusReads = 0;
    vi.mocked(getMediaPricesRefreshStatus).mockImplementation((headers) => {
      if (headers['X-Actor-Id'] === 'subject_test') {
        oldStatusReads += 1;
        return Promise.resolve({
          kind: 'ready',
          data: oldStatusReads === 1 ? neverRefresh : doneRefresh,
        });
      }
      return Promise.resolve({
        kind: 'ready',
        data: { ...neverRefresh, state: 'running', message: '新身份刷新进行中' },
      });
    });
    const nextSession = {
      ...session,
      actorId: 'usr_next',
      headers: {
        ...session.headers,
        'X-Actor-Id': 'subject_next',
      },
    };

    const view = render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    await screen.findByText('刷新完成，正在读取新快照…');
    await waitFor(() => expect(oldDatasetReads).toBe(2));

    view.rerender(<MediaPrices session={nextSession} />);
    await screen.findByText('新身份媒体');
    await screen.findByText('新身份刷新进行中');
    expect(screen.getByRole('button', { name: '刷新中…' })).toHaveProperty('disabled', true);

    await act(async () => {
      delayedOldReload.resolve({ kind: 'ready', data: fixtureDataset });
      await Promise.resolve();
    });

    expect(screen.getByText('新身份刷新进行中')).toBeTruthy();
    expect(screen.getByRole('button', { name: '刷新中…' })).toHaveProperty('disabled', true);
    expect(
      screen.queryByText(
        '刷新完成：prfabu 3 · 投媒网 2(限流) · 媒体批发网 1 · 媒体特价网 1 · 媒介盒子 1 · 品达发稿 1',
      ),
    ).toBeNull();
  });

  it('stops an accepted refresh immediately when status polling becomes forbidden', async () => {
    vi.mocked(getMediaPricesRefreshStatus)
      .mockResolvedValueOnce({ kind: 'ready', data: neverRefresh })
      .mockResolvedValueOnce({ kind: 'forbidden' });
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));

    await screen.findByText('权限不足：无法查看或启动数据刷新。');
    expect(screen.getByText('无权查看刷新状态')).toBeTruthy();
    expect(screen.getByRole('button', { name: '刷新数据' })).toHaveProperty('disabled', true);
    expect(requestMediaPricesRefresh).toHaveBeenCalledTimes(1);
    expect(getMediaPricesRefreshStatus).toHaveBeenCalledTimes(2);
  });

  it('keeps tracking progress when another refresh is already running (409)', async () => {
    vi.mocked(requestMediaPricesRefresh).mockResolvedValue({ kind: 'already_running' });
    vi.mocked(getMediaPricesRefreshStatus)
      .mockResolvedValueOnce({ kind: 'ready', data: neverRefresh })
      .mockResolvedValue({
        kind: 'ready',
        data: { ...neverRefresh, state: 'running', message: '拉取 prfabu 第2页…' },
      });
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    expect(await screen.findByText('已有刷新进行中，继续跟踪进度。')).toBeTruthy();
    expect(await screen.findByText('拉取 prfabu 第2页…')).toBeTruthy();
    expect(screen.getByRole('button', { name: '刷新中…' })).toHaveProperty('disabled', true);
  });

  it('warns in yellow when a source fell back because the session expired', async () => {
    const staleDone: MediaPricesRefreshStatus = {
      ...doneRefresh,
      message: 'prfabu 19087(会话失效沿用旧数据) · 投媒网 2 · 媒体批发网 1',
      sources: {
        prfabu: { status: 'stale', rows: 19087, note: 'session_expired' },
        toumeiw: { status: 'ok', rows: 2, note: '' },
        mtpfw: { status: 'ok', rows: 1, note: '' },
      },
    };
    vi.mocked(getMediaPricesRefreshStatus)
      .mockResolvedValueOnce({ kind: 'ready', data: neverRefresh })
      .mockResolvedValue({ kind: 'ready', data: staleDone });
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    const warning = await screen.findByText(
      /prfabu 会话失效，沿用旧数据（请到发帖页重新登录平台账号）/,
    );
    expect(warning.className).toContain('warn');
  });

  it('shows a red error bar when the refresh fails', async () => {
    vi.mocked(getMediaPricesRefreshStatus)
      .mockResolvedValueOnce({ kind: 'ready', data: neverRefresh })
      .mockResolvedValue({
        kind: 'ready',
        data: { ...neverRefresh, state: 'failed', message: 'httpx.ConnectError' },
      });
    render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    const failure = await screen.findByText('刷新失败：httpx.ConnectError');
    expect(failure.className).toContain('error');
    expect(failure.getAttribute('role')).toBe('alert');
  });

  it('discards an older identity poll before it can update freshness or reread the dataset', async () => {
    const delayedOldPoll = deferred<Awaited<ReturnType<typeof getMediaPricesRefreshStatus>>>();
    let oldStatusReads = 0;
    vi.mocked(getMediaPricesRefreshStatus).mockImplementation((headers) => {
      if (headers['X-Actor-Id'] === 'subject_test') {
        oldStatusReads += 1;
        if (oldStatusReads === 1) {
          return Promise.resolve({
            kind: 'ready',
            data: { ...neverRefresh, state: 'running', message: '旧身份刷新进行中' },
          });
        }
        return delayedOldPoll.promise;
      }
      return Promise.resolve({ kind: 'ready', data: neverRefresh });
    });
    const nextSession = {
      ...session,
      actorId: 'usr_next',
      headers: {
        ...session.headers,
        'X-Actor-Id': 'subject_next',
      },
    };

    const view = render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    await waitFor(() => expect(oldStatusReads).toBe(2));
    view.rerender(<MediaPrices session={nextSession} />);
    await screen.findByText('尚未刷新');

    await act(async () => {
      delayedOldPoll.resolve({
        kind: 'ready',
        data: { ...doneRefresh, message: '旧身份刷新已完成' },
      });
      await Promise.resolve();
    });

    expect(screen.queryByText(/旧身份刷新已完成/)).toBeNull();
    expect(
      vi.mocked(getMediaPricesDataset).mock.calls.map(([headers]) => headers['X-Actor-Id']),
    ).toEqual(['subject_test', 'subject_next']);
  });

  it('discards an accepted refresh response after the initiating identity changes', async () => {
    const delayedStart = deferred<Awaited<ReturnType<typeof requestMediaPricesRefresh>>>();
    vi.mocked(requestMediaPricesRefresh).mockReturnValue(delayedStart.promise);
    vi.mocked(getMediaPricesRefreshStatus).mockImplementation((headers) =>
      Promise.resolve({
        kind: 'ready',
        data:
          headers['X-Actor-Id'] === 'subject_test'
            ? neverRefresh
            : { ...neverRefresh, message: '新身份无刷新任务' },
      }),
    );
    const nextSession = {
      ...session,
      actorId: 'usr_next',
      headers: {
        ...session.headers,
        'X-Actor-Id': 'subject_next',
      },
    };

    const view = render(<MediaPrices session={session} />);
    await screen.findByText('人民网');
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    await screen.findByText('正在请求刷新…');
    view.rerender(<MediaPrices session={nextSession} />);
    await screen.findByText('尚未刷新');

    await act(async () => {
      delayedStart.resolve({ kind: 'started' });
      await Promise.resolve();
    });

    expect(screen.queryByText('刷新已启动…')).toBeNull();
    expect(screen.getByRole('button', { name: '刷新数据' })).toHaveProperty('disabled', false);
    expect(
      vi.mocked(getMediaPricesRefreshStatus).mock.calls.map(([headers]) => headers['X-Actor-Id']),
    ).toEqual(['subject_test', 'subject_next']);
  });
});

describe('media prices refresh pure logic', () => {
  it('keeps long-running supplier refresh polling bounded', () => {
    expect(REFRESH_POLL_INTERVAL_MS).toBe(10_000);
    expect(REFRESH_POLL_TIMEOUT_MS).toBe(45 * 60_000);
  });

  it('maps per-source statuses to presentations and notices', () => {
    const presentation = presentRefreshSources({
      prfabu: { status: 'stale', rows: 1, note: 'session_expired' },
      toumeiw: { status: 'partial', rows: 2, note: 'rate_limited' },
      mtpfw: { status: 'failed', rows: 0, note: 'boom' },
    });
    expect(presentation.staleSession).toEqual(['prfabu']);
    expect(presentation.staleOther).toEqual([]);
    expect(presentation.partial).toEqual(['toumeiw']);
    expect(presentation.failed).toEqual(['mtpfw']);
    const notice = buildRefreshDoneNotice({
      ...doneRefresh,
      sources: {
        prfabu: { status: 'stale', rows: 1, note: 'session_expired' },
      },
    });
    expect(notice.tone).toBe('warn');
    expect(notice.text).toContain('请到发帖页重新登录平台账号');
    const partialNotice = buildRefreshDoneNotice(doneRefresh);
    expect(partialNotice.tone).toBe('warn');
    expect(partialNotice.text).toContain('toumeiw 仅完成部分采集');
    const staleNotice = buildRefreshDoneNotice({
      ...doneRefresh,
      sources: {
        prfabu: { status: 'stale', rows: 1, note: 'source_unavailable' },
      },
    });
    expect(staleNotice.tone).toBe('warn');
    expect(staleNotice.text).toContain('prfabu 数据陈旧，沿用旧数据');
    expect(
      buildRefreshDoneNotice({
        ...doneRefresh,
        sources: {
          prfabu: { status: 'ok', rows: 3, note: '' },
          toumeiw: { status: 'ok', rows: 2, note: '' },
          mtpfw: { status: 'ok', rows: 1, note: '' },
        },
      }).tone,
    ).toBe('info');
  });

  it('formats the freshness card subtitle for every refresh state', () => {
    expect(formatRefreshCardSubtitle(null, 'loading')).toBe('正在读取刷新状态…');
    expect(formatRefreshCardSubtitle(null, 'unavailable')).toBe('刷新状态读取失败');
    expect(formatRefreshCardSubtitle(null, 'forbidden')).toBe('无权查看刷新状态');
    expect(formatRefreshCardSubtitle(null)).toBe('尚未刷新');
    expect(formatRefreshCardSubtitle(neverRefresh)).toBe('尚未刷新');
    expect(formatRefreshCardSubtitle(doneRefresh)).toBe(
      '上次刷新：prfabu 3 · 投媒网 2(限流) · 媒体批发网 1 · 媒体特价网 1 · 媒介盒子 1 · 品达发稿 1',
    );
    expect(
      formatRefreshCardSubtitle({ ...neverRefresh, state: 'running', message: '拉取中' }),
    ).toBe('正在刷新：拉取中');
    expect(formatRefreshCardSubtitle({ ...neverRefresh, state: 'failed', message: '' })).toBe(
      '上次刷新失败：未知错误',
    );
  });
});
