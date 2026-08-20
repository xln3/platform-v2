// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CollectionBrowserRow } from './api';
import { ActivityBadge, BrowsersPage } from './BrowsersPage';

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

const GB = 1 << 30;

function makeBrowser(overrides: Partial<CollectionBrowserRow> = {}): CollectionBrowserRow {
  return {
    browser_pub_id: 'brw_1',
    instance_key: 'doubao_bj',
    platform: 'doubao',
    region_gb: '110000',
    exit_ip: null,
    cdp_port: 19222,
    systemd_unit: 'browser@doubao_bj.service',
    activity: 'idle',
    error_streak: 0,
    breaker_until: null,
    muted_until: null,
    started_at: null,
    uptime_s: 300_000,
    rss_bytes: Math.round(2.2 * GB),
    bindings: { doubao: 'phone_1' },
    ...overrides,
  };
}

const ACCOUNTS = [
  {
    phone_account_pub_id: 'phone_1',
    phone_masked: '133****2231',
    owner_note: null,
    state: 'active',
    sms_link_state: 'ok',
    last_sms_at: null,
    push_link_state: 'untested',
    last_push_test_at: null,
    platforms: { doubao: null, yiyan: null, deepseek: null, yuanbao: null, tongyi: null },
  },
];

const REGIONS = [
  {
    region_pub_id: 'rgn_1',
    region_gb: '110000',
    name: '北京',
    source: 'wukong',
    proxy_env_key: null,
    relay_unit: 'proxy-relay@bj.service',
    exit_ip_last: null,
    last_probe_at: null,
    state: 'ok',
    note: null,
  },
];

type FetchCall = { method: string; path: string };

function installFetch(handlers: {
  browsers?: unknown;
  restart?: { status: number; body: unknown };
  releaseLock?: { status: number; body: unknown };
  sync?: { status: number; body: unknown };
}) {
  const calls: FetchCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    const method = init?.method ?? 'GET';
    const path = url.pathname;
    calls.push({ method, path });
    const json = (status: number, payload: unknown) =>
      new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    if (method === 'GET' && path === '/api/v2/collection-browsers')
      return json(200, handlers.browsers ?? []);
    if (method === 'GET' && path === '/api/v2/collection-accounts') return json(200, ACCOUNTS);
    if (method === 'GET' && path === '/api/v2/collection-regions') return json(200, REGIONS);
    if (method === 'POST' && path.endsWith('/restart')) {
      const restart = handlers.restart ?? {
        status: 200,
        body: { ok: true, executed: false, detail: 'manual_restart_window_required' },
      };
      return json(restart.status, restart.body);
    }
    if (method === 'POST' && path.endsWith('/release-lock')) {
      const release = handlers.releaseLock ?? {
        status: 200,
        body: { ok: true, released: true, detail: 'fence_released' },
      };
      return json(release.status, release.body);
    }
    if (method === 'POST' && path === '/api/v2/collection-browsers/sync') {
      const sync = handlers.sync ?? {
        status: 200,
        body: { synced: 3, created: 1, updated: 2, errors: [], instances: ['doubao_bj'] },
      };
      return json(sync.status, sync.body);
    }
    return json(404, { error: { code: 'not_found' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('BrowsersPage', () => {
  it('渲染实例行：时长/内存水位标色、绑定账号链接、null 展示', async () => {
    installFetch({ browsers: [makeBrowser()] });
    render(<BrowsersPage session={session} />);

    const table = await screen.findByRole('table', { name: '采集浏览器列表' });
    expect(within(table).getByText('doubao_bj')).toBeTruthy();
    // 3.47 天 > 3 天 → 标黄；2.2 GB > 2 GB → 标红
    const uptimeCell = within(table).getByText('3 天 11 小时');
    expect(uptimeCell.className).toContain('acct-gov-warn');
    const rssCell = within(table).getByText('2.2 GB');
    expect(rssCell.className).toContain('acct-gov-bad');
    // 地域名拼接 + 未探测 IP
    expect(within(table).getByText('110000 北京')).toBeTruthy();
    expect(within(table).getByText('未探测')).toBeTruthy();
    // 绑定账号 → 脱敏手机号 + 跳账号管理页锚点；未绑定平台 → —
    const link = within(table).getByRole('link', { name: '133****2231' });
    expect(link.getAttribute('href')).toBe('/platform/operations/accounts#acct-phone_1');
    expect(within(table).getAllByText('—').length).toBe(4);
  });

  it('重启只登记请求并如实提示需运维窗口', async () => {
    const calls = installFetch({ browsers: [makeBrowser()] });
    render(<BrowsersPage session={session} />);

    fireEvent.click(await screen.findByRole('button', { name: '重启' }));
    const toast = await screen.findByRole('status');
    expect(toast.textContent).toContain('已登记，需运维窗口执行');
    expect(
      calls.some(
        (call) =>
          call.method === 'POST' && call.path === '/api/v2/collection-browsers/doubao_bj/restart',
      ),
    ).toBe(true);
  });

  it('释放锁 toast 透传后端 detail', async () => {
    installFetch({ browsers: [makeBrowser()] });
    render(<BrowsersPage session={session} />);

    fireEvent.click(await screen.findByRole('button', { name: '释放锁' }));
    const toast = await screen.findByRole('status');
    expect(toast.textContent).toContain('fence_released');
  });

  it('同步实例清单展示 created/updated/errors', async () => {
    installFetch({
      browsers: [makeBrowser()],
      sync: {
        status: 200,
        body: { synced: 4, created: 2, updated: 1, errors: ['browser@x: 探测超时'], instances: [] },
      },
    });
    render(<BrowsersPage session={session} />);

    fireEvent.click(await screen.findByRole('button', { name: '同步实例清单' }));
    const toast = await screen.findByRole('status');
    expect(toast.textContent).toContain('新增 2、更新 1、错误 1');
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('同步错误：browser@x: 探测超时');
  });

  it('ActivityBadge：error_streak>0 显示异常并悬停明细，captcha 显示验证码中', () => {
    const future = new Date(Date.now() + 3_600_000).toISOString();
    render(
      <>
        <ActivityBadge
          row={makeBrowser({ error_streak: 3, breaker_until: future, activity: 'busy' })}
          now={Date.now()}
        />
        <ActivityBadge row={makeBrowser({ activity: 'captcha' })} now={Date.now()} />
        <ActivityBadge row={makeBrowser({ activity: 'busy' })} now={Date.now()} />
      </>,
    );
    const abnormal = screen.getByText('异常');
    expect(abnormal.getAttribute('title')).toContain('连续失败 3 次');
    expect(abnormal.getAttribute('title')).toContain('熔断至');
    expect(screen.getByText('验证码中')).toBeTruthy();
    expect(screen.getByText('行动中')).toBeTruthy();
  });
});
