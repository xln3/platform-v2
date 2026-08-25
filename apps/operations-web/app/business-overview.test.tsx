// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { OperationsLifecycleSnapshotProjection } from '@geo/api-client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fixtureOperationsLifecycleSnapshot } from './lifecycle-snapshot';
import {
  BusinessOverviewContainer,
  createFixtureOperationsBusinessOverview,
} from './business-overview';
import { OperationsActiveWorkspace } from './shell';

vi.mock('@geo/auth', async () => {
  const actual = await vi.importActual<typeof import('@geo/auth')>('@geo/auth');
  return {
    ...actual,
    getValidatedIdentityHeaders: () => ({
      'X-Tenant-Id': 'tnt_business_test',
      'X-Actor-Id': 'operator-business-test',
      'X-Actor-Role': 'operator',
    }),
  };
});

afterEach(() => {
  cleanup();
  window.history.replaceState({}, '', '/platform/operations/');
  vi.clearAllMocks();
});

describe('Operations business overview', () => {
  it('renders truthful portfolio facts, 4 + 1 pagination, and capability boundaries', async () => {
    render(<BusinessOverviewContainer fixtureMode roles={['operator']} />);

    expect(await screen.findByRole('heading', { name: '项目组合' })).toBeTruthy();
    expect(screen.getAllByText('华东品牌增长').length).toBeGreaterThan(0);
    expect(screen.queryByRole('link', { name: '星河科技' })).toBeNull();
    expect(
      screen
        .getAllByRole('link', { name: '华东品牌增长' })
        .every(
          (link) =>
            link.getAttribute('href') ===
            '/platform/operations/sop/projects/prj_fixture_business_05',
        ),
    ).toBe(true);
    expect(screen.queryByText('历史项目归档')).toBeNull();
    expect(screen.getAllByText('报告已签发').length).toBeGreaterThan(0);
    expect(screen.queryByText('合同已签')).toBeNull();
    expect(screen.queryByText('未购买')).toBeNull();
    expect(screen.getAllByText('尚无服务权益记录。').length).toBeGreaterThan(0);
    expect(screen.getAllByText('暂无运行记录。').length).toBeGreaterThan(0);
    expect(screen.getAllByText('暂无正式报告。').length).toBeGreaterThan(0);
    expect(screen.getAllByText('暂无交付记录。').length).toBeGreaterThan(0);
    expect(screen.getAllByText('事实尚未确认').length).toBeGreaterThan(0);
    expect(screen.getAllByText('缺资产确认').length).toBeGreaterThan(0);
    expect(screen.getAllByText('缺冻结配置').length).toBeGreaterThan(0);
    expect(
      screen.getByText('系统目前未保存可查询的报价历史、已签合同、开票应收与回款台账。'),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findAllByText('历史项目归档')).not.toHaveLength(0);
    expect(screen.getByText('5–5 / 5')).toBeTruthy();
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByRole('button', { name: '上一页' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('restores filters in the URL, resets pagination, and keeps empty states distinct', async () => {
    render(<BusinessOverviewContainer fixtureMode roles={['reviewer']} />);
    await screen.findByRole('heading', { name: '项目组合' });

    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findAllByText('历史项目归档')).not.toHaveLength(0);
    fireEvent.change(screen.getByLabelText('项目状态'), { target: { value: 'draft' } });
    await waitFor(() => expect(screen.getAllByText('新品首版评测').length).toBeGreaterThan(0));
    expect(screen.getByText('1–1 / 1')).toBeTruthy();
    expect((screen.getByRole('button', { name: '上一页' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(new URL(window.location.href).searchParams.get('project_state')).toBe('draft');
    expect(screen.queryByRole('link', { name: '新建客户 / 开户' })).toBeNull();

    fireEvent.change(screen.getByLabelText('客户或项目'), { target: { value: '不存在的项目' } });
    fireEvent.submit(screen.getByRole('form', { name: '筛选项目组合' }));
    expect(await screen.findByText('没有符合当前筛选条件的项目。')).toBeTruthy();
    expect(screen.queryByText('当前租户尚无项目。')).toBeNull();
    expect(new URL(window.location.href).searchParams.get('q')).toBe('不存在的项目');
  });

  it('distinguishes an empty tenant from an empty filtered result', async () => {
    const empty = createFixtureOperationsBusinessOverview({}, undefined);
    empty.summary = {
      scope: 'filtered',
      tenantProjectCount: 0,
      projectCount: 0,
      projectStateCounts: { draft: 0, active: 0, paused: 0, archived: 0 },
      setupReadyProjectCount: 0,
      projectWithEntitlementRecordCount: 0,
      activeEntitlementCount: 0,
      attentionProjectCount: 0,
    };
    empty.items = [];
    empty.page = { limit: 4, nextCursor: null, hasMore: false, filteredTotal: 0 };
    const businessLoader = vi.fn(async () => ({ kind: 'ready' as const, data: empty }));
    render(
      <BusinessOverviewContainer
        fixtureMode={false}
        roles={['reviewer']}
        loadBusiness={businessLoader}
      />,
    );
    expect(await screen.findByText('当前租户尚无项目。')).toBeTruthy();
    expect(screen.queryByText('没有符合当前筛选条件的项目。')).toBeNull();
    expect(screen.queryByRole('link', { name: '前往开户向导' })).toBeNull();
  });

  it('requests only the active root data source and never falls back to fixture on failure', async () => {
    const businessLoader = vi.fn(async () => ({ kind: 'unavailable' as const }));
    const lifecycleProjection: OperationsLifecycleSnapshotProjection = {
      ...fixtureOperationsLifecycleSnapshot!,
      accounts: fixtureOperationsLifecycleSnapshot!.accounts.map((account) => ({
        ...account,
        interventionStatus: account.interventionStatus ?? 'none',
      })),
      revocationReceipt: null,
    };
    const lifecycleLoader = vi.fn(async () => ({
      kind: 'ready' as const,
      data: lifecycleProjection,
    }));
    const { rerender } = render(
      <OperationsActiveWorkspace
        section="overview"
        fixtureMode={false}
        roles={['operator']}
        loadBusiness={businessLoader}
        loadLifecycle={lifecycleLoader}
      />,
    );
    expect(await screen.findByText('加载失败')).toBeTruthy();
    expect(businessLoader).toHaveBeenCalledTimes(1);
    expect(lifecycleLoader).not.toHaveBeenCalled();
    expect(screen.queryByText('星河科技')).toBeNull();

    rerender(
      <OperationsActiveWorkspace
        section="sessions"
        fixtureMode={false}
        roles={['operator']}
        loadBusiness={businessLoader}
        loadLifecycle={lifecycleLoader}
      />,
    );
    expect(await screen.findByRole('heading', { name: '授权、租约与会话健康' })).toBeTruthy();
    expect(lifecycleLoader).toHaveBeenCalledTimes(1);
    expect(businessLoader).toHaveBeenCalledTimes(1);
  });

  it('ignores a stale response after a newer filter request completes', async () => {
    let resolveStale:
      | ((value: ReturnType<typeof createFixtureOperationsBusinessOverview>) => void)
      | undefined;
    const stale = new Promise<ReturnType<typeof createFixtureOperationsBusinessOverview>>(
      (resolve) => {
        resolveStale = resolve;
      },
    );
    const businessLoader = vi
      .fn()
      .mockImplementationOnce(async () => ({
        kind: 'ready' as const,
        data: createFixtureOperationsBusinessOverview({}, undefined),
      }))
      .mockImplementationOnce(async () => ({ kind: 'ready' as const, data: await stale }))
      .mockImplementationOnce(async () => ({
        kind: 'ready' as const,
        data: createFixtureOperationsBusinessOverview({ q: '青禾零售' }, undefined),
      }));
    render(
      <BusinessOverviewContainer
        fixtureMode={false}
        roles={['operator']}
        loadBusiness={businessLoader}
      />,
    );
    const search = await screen.findByLabelText('客户或项目');
    fireEvent.change(search, { target: { value: '远山制造' } });
    fireEvent.submit(screen.getByRole('form', { name: '筛选项目组合' }));
    await waitFor(() => expect(businessLoader).toHaveBeenCalledTimes(2));
    fireEvent.change(screen.getByLabelText('客户或项目'), { target: { value: '青禾零售' } });
    fireEvent.submit(screen.getByRole('form', { name: '筛选项目组合' }));
    expect(await screen.findAllByText('历史项目归档')).not.toHaveLength(0);
    resolveStale?.(createFixtureOperationsBusinessOverview({ q: '远山制造' }, undefined));
    await Promise.resolve();
    expect(screen.queryByText('新品首版评测')).toBeNull();
  });
});
