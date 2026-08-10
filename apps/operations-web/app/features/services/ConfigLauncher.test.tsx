// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfigLauncher } from './ConfigLauncher';

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

type RecordedCall = { url: string; method: string; body: unknown; idempotencyKey: string | null };

function requestOf(input: string | URL | Request): Request {
  return input instanceof Request
    ? input
    : new Request(typeof input === 'string' ? input : input.href);
}

describe('ConfigLauncher', () => {
  const calls: RecordedCall[] = [];

  beforeEach(() => {
    calls.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const request = requestOf(input);
        const body = request.method === 'POST' ? await request.clone().json() : null;
        calls.push({
          url: request.url,
          method: request.method,
          body,
          idempotencyKey: request.headers.get('Idempotency-Key'),
        });
        const data = request.url.includes('/config/freeze')
          ? {
              pub_id: 'cfv_frozen',
              revision: 3,
              effective_at: '2026-08-09T00:00:00Z',
              frozen_at: '2026-08-09T00:00:00Z',
              snapshot_hash: 'abcdef1234567890',
              snapshot: {},
            }
          : request.url.includes('/api/v2/schedules')
            ? {
                pub_id: 'sch_new',
                project_pub_id: 'prj_test',
                config_version_pub_id: 'cfv_frozen',
                interval_minutes: 1440,
                timezone: 'Asia/Shanghai',
                state: 'active',
                next_run_at: '2026-08-11T00:00:00+08:00',
                last_run_at: null,
                last_run_pub_id: null,
                responsible_pub_id: 'usr_test',
                created_by_pub_id: 'usr_test',
                version: 1,
                created_at: '2026-08-10T00:00:00Z',
                updated_at: '2026-08-10T00:00:00Z',
              }
            : { run_pub_id: 'run_new', workflow_id: 'geo-collection/new' };
        return new Response(JSON.stringify(data), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  function fillQuestions(text: string) {
    fireEvent.change(screen.getByPlaceholderText('国内网络空间资产搜索引擎哪家强'), {
      target: { value: text },
    });
  }

  it('estimates task volume as questions × platform-modes × regions × samples', () => {
    render(
      <ConfigLauncher
        session={session}
        projectPubId="prj_test"
        groupName="品牌AI认知评测"
        queryPlaceholder="国内网络空间资产搜索引擎哪家强"
      />,
    );
    // 缺省平台 doubao/deepseek/yiyan：deepseek/yiyan 双 mode（normal+deep_think），
    // 其余单 normal → 平台×模式数 = 1+2+2 = 5。
    expect(
      screen.getByText(/0 题 × 5 平台×模式 × 2 地域 = 每轮 0 任务，采样 2 轮共 0 任务/),
    ).toBeTruthy();
    fillQuestions('问题一\n问题二\n\n问题三');
    expect(
      screen.getByText(/3 题 × 5 平台×模式 × 2 地域 = 每轮 30 任务，采样 2 轮共 60 任务/),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '上海' }));
    expect(
      screen.getByText(/3 题 × 5 平台×模式 × 1 地域 = 每轮 15 任务，采样 2 轮共 30 任务/),
    ).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/采样次数/), { target: { value: '4' } });
    expect(
      screen.getByText(/3 题 × 5 平台×模式 × 1 地域 = 每轮 15 任务，采样 4 轮共 60 任务/),
    ).toBeTruthy();
  });

  it('counts tongyi as dual-mode (normal + deep_think) after the 20260810 unlock', () => {
    render(
      <ConfigLauncher
        session={session}
        projectPubId="prj_test"
        groupName="品牌AI认知评测"
        queryPlaceholder="国内网络空间资产搜索引擎哪家强"
      />,
    );
    // 勾选通义千问（思考研究解锁后 normal+deep_think 两种）→ 平台×模式数 5 + 2 = 7
    fireEvent.click(screen.getByLabelText('通义千问'));
    expect(
      screen.getByText(/0 题 × 7 平台×模式 × 2 地域 = 每轮 0 任务，采样 2 轮共 0 任务/),
    ).toBeTruthy();
  });

  it('freezes once and starts one run per sample with distinct idempotency keys', async () => {
    render(
      <ConfigLauncher
        session={session}
        projectPubId="prj_test"
        groupName="品牌AI认知评测"
        queryPlaceholder="国内网络空间资产搜索引擎哪家强"
      />,
    );
    fillQuestions('问题一\n问题二');
    fireEvent.change(screen.getByLabelText(/采样次数/), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: '冻结并启动采样' }));
    await screen.findByText(/配置 v3 已冻结（abcdef12）；已启动 3 个采样 run/);

    const freezeCalls = calls.filter((call) => call.url.includes('/config/freeze'));
    const runCalls = calls.filter(
      (call) => call.url.includes('/collection/runs') && call.method === 'POST',
    );
    expect(freezeCalls).toHaveLength(1);
    expect(runCalls).toHaveLength(3);

    const freezeBody = freezeCalls[0]?.body as {
      query_groups: { name: string; items: { text: string; priority: number }[] }[];
      regions: string[];
      models: string[];
      modes: string[];
      frequency: string;
    };
    expect(freezeBody.query_groups).toEqual([
      {
        name: '品牌AI认知评测',
        items: [
          { text: '问题一', priority: 1 },
          { text: '问题二', priority: 2 },
        ],
      },
    ]);
    expect(freezeBody.regions).toEqual(['北京', '上海']);
    expect(freezeBody.models).toEqual(['doubao', 'deepseek', 'yiyan']);
    expect(freezeBody.modes).toEqual(['normal', 'deep_think']);
    expect(freezeBody.frequency).toBe('manual');

    const runBody = runCalls[0]?.body as {
      project_pub_id: string;
      config_version_pub_id: string;
      requires_intervention: boolean;
      account_pub_id: string | null;
    };
    expect(runBody.project_pub_id).toBe('prj_test');
    expect(runBody.config_version_pub_id).toBe('cfv_frozen');
    expect(runBody.requires_intervention).toBe(false);
    expect(runBody.account_pub_id).toBeNull();

    const keys = calls.map((call) => call.idempotencyKey);
    for (const key of keys) {
      expect(key).toBeTruthy();
      expect(key!.length).toBeGreaterThanOrEqual(16);
    }
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('freezes without starting runs and reports the frozen version', async () => {
    render(
      <ConfigLauncher
        session={session}
        projectPubId="prj_test"
        groupName="GEO试点验证"
        queryPlaceholder="国内网络空间资产搜索引擎哪家强"
      />,
    );
    fillQuestions('基线问题');
    fireEvent.click(screen.getByRole('button', { name: '仅冻结配置' }));
    await screen.findByText(/配置 v3 已冻结（abcdef12）/);
    expect(calls.filter((call) => call.url.includes('/config/freeze'))).toHaveLength(1);
    expect(calls.filter((call) => call.url.includes('/collection/runs'))).toHaveLength(0);
  });

  it('creates a real schedule after freezing when frequency is daily', async () => {
    render(
      <ConfigLauncher
        session={session}
        projectPubId="prj_test"
        groupName="品牌AI认知评测"
        queryPlaceholder="国内网络空间资产搜索引擎哪家强"
      />,
    );
    fillQuestions('问题一');
    fireEvent.change(screen.getByLabelText(/频率/), { target: { value: 'daily' } });
    fireEvent.click(screen.getByRole('button', { name: '仅冻结配置' }));
    await screen.findByText(/配置 v3 已冻结（abcdef12）/);
    await screen.findByText(/已创建调度：每日（每 1440 分钟），下次运行/);

    const scheduleCalls = calls.filter((call) => call.url.includes('/api/v2/schedules'));
    expect(scheduleCalls).toHaveLength(1);
    const scheduleBody = scheduleCalls[0]?.body as {
      project_pub_id: string;
      config_version_pub_id: string;
      interval_minutes: number;
      timezone: string;
      next_run_at: string;
      responsible_pub_id: string;
    };
    expect(scheduleBody.project_pub_id).toBe('prj_test');
    expect(scheduleBody.config_version_pub_id).toBe('cfv_frozen');
    expect(scheduleBody.interval_minutes).toBe(1440);
    expect(scheduleBody.timezone).toBe('Asia/Shanghai');
    expect(scheduleBody.responsible_pub_id).toBe('usr_test');
    // next_run_at 必须带时区（Z 或 ±hh:mm 偏移）。
    expect(scheduleBody.next_run_at).toMatch(/(Z|[+-]\d{2}:\d{2})$/);
    expect(Number.isNaN(Date.parse(scheduleBody.next_run_at))).toBe(false);

    const freezeBody = calls.find((call) => call.url.includes('/config/freeze'))?.body as {
      frequency: string;
    };
    expect(freezeBody.frequency).toBe('daily');
    expect(screen.getByRole('link', { name: '前往「执行与账号」查看调度' })).toBeTruthy();
  });

  it('surfaces a partial-success error when schedule creation fails after launch', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const request = requestOf(input);
        if (request.url.includes('/api/v2/schedules')) {
          return new Response(JSON.stringify({ error: { code: 'permission_denied' } }), {
            status: 403,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        const data = request.url.includes('/config/freeze')
          ? {
              pub_id: 'cfv_frozen',
              revision: 3,
              effective_at: '2026-08-09T00:00:00Z',
              frozen_at: '2026-08-09T00:00:00Z',
              snapshot_hash: 'abcdef1234567890',
              snapshot: {},
            }
          : { run_pub_id: 'run_new', workflow_id: 'geo-collection/new' };
        return new Response(JSON.stringify(data), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
    render(
      <ConfigLauncher
        session={session}
        projectPubId="prj_test"
        groupName="品牌AI认知评测"
        queryPlaceholder="国内网络空间资产搜索引擎哪家强"
      />,
    );
    fillQuestions('问题一');
    fireEvent.change(screen.getByLabelText(/频率/), { target: { value: 'daily' } });
    fireEvent.click(screen.getByRole('button', { name: '冻结并启动采样' }));
    // 部分成功：冻结与采样照常回执，调度失败原因明示不吞错。
    await screen.findByText(/配置 v3 已冻结（abcdef12）；已启动 2 个采样 run/);
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain(
        '配置已冻结，但自动调度创建失败：permission_denied',
      ),
    );
  });

  it('shows errors inline when the freeze fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Promise.resolve(
          new Response(JSON.stringify({ error: { code: 'permission_denied' } }), {
            status: 403,
            headers: { 'Content-Type': 'application/json' },
          }),
        ),
      ),
    );
    render(
      <ConfigLauncher
        session={session}
        projectPubId="prj_test"
        groupName="品牌AI认知评测"
        queryPlaceholder="国内网络空间资产搜索引擎哪家强"
      />,
    );
    fillQuestions('问题一');
    fireEvent.click(screen.getByRole('button', { name: '冻结并启动采样' }));
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('permission_denied'),
    );
  });
});
