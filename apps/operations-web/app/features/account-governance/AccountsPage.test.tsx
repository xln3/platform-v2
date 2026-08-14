// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CollectionAccountRow, CollectionRegionRow, PlatformAccountCell } from './api';
import { AccountsPage, RuntimeStateBadge } from './AccountsPage';

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

function makeCell(overrides: Partial<PlatformAccountCell> = {}): PlatformAccountCell {
  return {
    platform_account_pub_id: 'pac_doubao_1',
    region_gb: null,
    quota_day: 10,
    quota_week: null,
    quota_year: null,
    used_today: 3,
    used_week: 9,
    used_year: 41,
    quota_reset_at: null,
    quota_resume_at: null,
    runtime_state: 'idle',
    current_run_pub_id: null,
    muted_until: null,
    state_reason: null,
    browser_instance_key: 'doubao_bj',
    ...overrides,
  };
}

function makeAccountRow(overrides: Record<string, unknown> = {}): CollectionAccountRow {
  return {
    phone_account_pub_id: 'phone_1',
    phone_masked: '133****2231',
    owner_note: '号主老张',
    state: 'active',
    sms_link_state: 'ok',
    last_sms_at: '2026-08-13T02:00:00Z',
    push_link_state: 'untested',
    last_push_test_at: null,
    platforms: {
      doubao: makeCell(),
      yiyan: null,
      deepseek: makeCell({
        platform_account_pub_id: 'pac_ds_1',
        runtime_state: 'muted',
        muted_until: new Date(Date.now() + 62 * 60_000).toISOString(),
        state_reason: '已被禁言至 2026 年 8 月 14 日 13:02',
      }),
      yuanbao: makeCell({
        platform_account_pub_id: 'pac_yb_1',
        runtime_state: 'quota_exhausted',
        quota_resume_at: new Date(Date.now() + 3 * 3_600_000).toISOString(),
      }),
      tongyi: makeCell({
        platform_account_pub_id: 'pac_ty_1',
        runtime_state: 'error',
        state_reason: '连续 3 次 answer_capture_incomplete',
      }),
    },
    ...overrides,
  } as CollectionAccountRow;
}

const REGIONS: CollectionRegionRow[] = [
  {
    region_pub_id: 'rgn_1',
    region_gb: '110000',
    name: '北京',
    source: 'wukong',
    proxy_env_key: 'GEO_PROXY_BJ_URL',
    relay_unit: 'proxy-relay@bj.service',
    exit_ip_last: '106.37.143.183',
    last_probe_at: null,
    state: 'ok',
    note: null,
  },
];

type FetchCall = { method: string; path: string; body?: unknown };

function installFetch(handlers: {
  accounts?: unknown;
  regions?: unknown;
  patch?: { status: number; body: unknown };
  linkTest?: { status: number; body: unknown };
  createAccount?: { status: number; body: unknown };
  events?: unknown;
}) {
  const calls: FetchCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    const method = init?.method ?? 'GET';
    const path = url.pathname;
    calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const json = (status: number, payload: unknown) =>
      new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    if (method === 'GET' && path === '/api/v2/collection-accounts')
      return json(200, handlers.accounts ?? []);
    if (method === 'GET' && path === '/api/v2/collection-regions')
      return json(200, handlers.regions ?? []);
    if (method === 'PATCH' && path.startsWith('/api/v2/collection-platform-accounts/')) {
      const patch = handlers.patch ?? { status: 200, body: {} };
      return json(patch.status, patch.body);
    }
    if (method === 'POST' && path.endsWith('/link-test')) {
      const linkTest = handlers.linkTest ?? { status: 200, body: { ok: true, channel: 'sms' } };
      return json(linkTest.status, linkTest.body);
    }
    if (method === 'POST' && path === '/api/v2/collection-accounts') {
      const created = handlers.createAccount ?? { status: 201, body: {} };
      return json(created.status, created.body);
    }
    if (method === 'GET' && path.endsWith('/events')) return json(200, handlers.events ?? []);
    return json(404, { error: { code: 'not_found' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('AccountsPage', () => {
  it('渲染账号行：五平台格、状态徽章映射、null 格显示 —', async () => {
    installFetch({ accounts: [makeAccountRow()], regions: REGIONS });
    render(<AccountsPage session={session} />);

    const table = await screen.findByRole('table', { name: '采集账号列表' });
    expect(within(table).getByText('133****2231')).toBeTruthy();
    // null 格（文心一言未登记）
    expect(within(table).getAllByText('—').length).toBeGreaterThan(0);
    // 状态徽章映射
    expect(within(table).getByText('空闲')).toBeTruthy();
    expect(within(table).getByText(/^禁/)).toBeTruthy();
    expect(within(table).getByText(/^额度尽/)).toBeTruthy();
    const errorBadge = within(table).getByText('异常');
    expect(errorBadge.getAttribute('title')).toContain('answer_capture_incomplete');
    // 禁言倒计时
    expect(within(table).getByText(/剩余 1 小时/)).toBeTruthy();
    // 额度显示 used/quota
    expect(within(table).getAllByText('3/10').length).toBeGreaterThan(0);
  });

  it('RuntimeStateBadge 覆盖 running/captcha 映射', () => {
    render(
      <>
        <RuntimeStateBadge
          cell={makeCell({ runtime_state: 'running', current_run_pub_id: 'run_ABC' })}
          now={Date.now()}
        />
        <RuntimeStateBadge cell={makeCell({ runtime_state: 'captcha' })} now={Date.now()} />
      </>,
    );
    expect(screen.getByText('运行中')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'run_ABC' })).toBeTruthy();
    expect(screen.getByText('验证码中')).toBeTruthy();
  });

  it('地域改选走二次确认弹窗并以 confirm=true 提交 PATCH', async () => {
    const calls = installFetch({ accounts: [makeAccountRow()], regions: REGIONS });
    render(<AccountsPage session={session} />);

    const select = await screen.findByLabelText('豆包地域');
    fireEvent.change(select, { target: { value: '110000' } });

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/该手机号在该平台的地域绑定变更/)).toBeTruthy();
    expect(within(dialog).getByText('110000')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: '确认变更' }));

    await waitFor(() => {
      const patch = calls.find(
        (call) =>
          call.method === 'PATCH' && call.path === '/api/v2/collection-platform-accounts/pac_doubao_1',
      );
      expect(patch?.body).toEqual({ region_gb: '110000', confirm: true });
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('region_ip_mismatch 时弹层如实展示错误', async () => {
    installFetch({
      accounts: [makeAccountRow()],
      regions: REGIONS,
      patch: { status: 409, body: { error: { code: 'region_ip_mismatch' } } },
    });
    render(<AccountsPage session={session} />);

    const select = await screen.findByLabelText('豆包地域');
    fireEvent.change(select, { target: { value: '110000' } });
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '确认变更' }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('地域IP不匹配');
    expect(alert.textContent).toContain('region_ip_mismatch');
  });

  it('转码测试弹层显示 guidance 与回执倒计时', async () => {
    const calls = installFetch({
      accounts: [makeAccountRow()],
      regions: REGIONS,
      linkTest: {
        status: 200,
        body: {
          ok: true,
          channel: 'sms',
          wait_window_s: 120,
          guidance: '请向该手机号发送一条测试验证码短信',
        },
      },
    });
    render(<AccountsPage session={session} />);

    fireEvent.click(await screen.findByRole('button', { name: '测试转码链路' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('请向该手机号发送一条测试验证码短信')).toBeTruthy();
    expect(within(dialog).getByText(/等待回执：剩余 \d+ 秒/)).toBeTruthy();
    const linkTest = calls.find(
      (call) => call.method === 'POST' && call.path === '/api/v2/collection-accounts/phone_1/link-test',
    );
    expect(linkTest?.body).toEqual({ channel: 'sms' });
  });

  it('添加帐号 409 时提示手机号已存在', async () => {
    installFetch({
      accounts: [],
      regions: REGIONS,
      createAccount: { status: 409, body: { error: { code: 'phone_already_exists' } } },
    });
    render(<AccountsPage session={session} />);

    fireEvent.click(await screen.findByRole('button', { name: '添加帐号' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByPlaceholderText('11 位手机号'), {
      target: { value: '13300002231' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '提交' }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('该手机号已存在');
  });

  it('事件抽屉展开后加载时间线', async () => {
    installFetch({
      accounts: [makeAccountRow()],
      regions: REGIONS,
      events: [
        {
          event_pub_id: 'evt_1',
          event_type: 'region_change',
          actor: 'usr_operator',
          phone_account_pub_id: 'phone_1',
          platform_account_pub_id: 'pac_doubao_1',
          browser_pub_id: null,
          region_pub_id: 'rgn_1',
          old_value: null,
          new_value: '110000',
          evidence: '管理页二次确认',
          run_pub_id: null,
          created_at: '2026-08-13T03:00:00Z',
        },
      ],
    });
    render(<AccountsPage session={session} />);

    fireEvent.click(await screen.findByRole('button', { name: '事件' }));
    expect(await screen.findByText('region_change')).toBeTruthy();
    expect(screen.getByText('管理页二次确认')).toBeTruthy();
    expect(screen.getByText(/→ 110000/)).toBeTruthy();
  });
});
