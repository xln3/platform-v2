// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import {
  OperationsLifecycleWorkspace,
  fixtureOperationsLifecycleSnapshot,
  projectSafeOperationsLifecycleSnapshot,
} from './lifecycle-snapshot';

afterEach(cleanup);

describe('OperationsLifecycleSnapshot', () => {
  it('drops secret-shaped and unknown S01 fields before they reach UI state', () => {
    const canary = 'SESSION=operations-lifecycle-canary';
    const projected = projectSafeOperationsLifecycleSnapshot({
      metrics: {
        runningRuns: 2,
        projectCount: 1,
        pendingInterventions: 1,
        healthySessions: 1,
        totalSessions: 2,
        delayedRuns: 1,
        p95DelayLabel: '3m 10s',
        token: canary,
      },
      accounts: [
        {
          accountMask: '平台账号 · 尾号 4821',
          platformLabel: '目标平台',
          ownerLabel: '已验证责任人',
          custodyMode: 'customer-device',
          admissionLevel: 'adapter_ready',
          scopes: ['read', 'publish', 'delete'],
          expiresLabel: '2026-08-31 18:00 CST',
          regionLabel: '中国大陆',
          sessionHealth: 'challenge_required',
          lastVerifiedLabel: '尚未 live 验证',
          interventionStatus: 'waiting',
          profile_path: '/secret/profile/operations-canary',
        },
        {
          accountMask: canary,
          platformLabel: '不得推断的平台账号',
          ownerLabel: '不得推断的所有者',
        },
      ],
      activity: [
        {
          pubId: 'evt_safe_activity',
          occurredAtLabel: '10:20',
          eventLabel: '租约暂停',
          objectLabel: 'opaque account',
          resultLabel: '等待客户',
          tone: 'warning',
          cookie: canary,
        },
        {
          pubId: 'evt_secret_activity',
          occurredAtLabel: '10:21',
          eventLabel: canary,
          objectLabel: 'opaque account',
          resultLabel: '失败',
        },
      ],
      interventions: [
        {
          pubId: 'int_safe_waiting',
          accountMask: '平台账号 · 尾号 4821',
          challengeType: 'passkey',
          state: 'waiting',
          leaseLabel: '已暂停',
          expiresLabel: '05:00',
          pairing_token: canary,
        },
      ],
      events: [
        {
          pubId: 'evt_safe_revoked',
          eventLabel: '撤销完成',
          detailLabel: '活动会话已关闭',
          occurredAtLabel: '10:22',
          otp: '394820',
        },
      ],
      revocationReceipt: {
        receiptId: 'rvr_safe_receipt',
        revokedAtLabel: '2026-07-25 10:22 CST',
        actorLabel: '已验证客户管理员',
        leasesStopped: true,
        sessionsClosed: true,
        secretCopiesPurged: true,
        authorization: `Bearer ${canary}`,
      },
      rawProfile: { path: '/secret/profile/operations-canary' },
    });

    expect(projected).not.toBeNull();
    expect(projected?.accounts).toHaveLength(1);
    expect(projected?.activity).toHaveLength(1);
    expect(projected?.interventions).toHaveLength(1);
    expect(projected?.events).toHaveLength(1);
    expect(projected?.accounts[0]?.scopes).toEqual(['read', 'publish']);
    const serialized = JSON.stringify(projected);
    expect(serialized).not.toMatch(
      /operations-lifecycle-canary|pairing_token|profile_path|rawProfile|authorization|394820/i,
    );
  });

  it('renders every S03-owned lifecycle view from one projected snapshot', () => {
    expect(fixtureOperationsLifecycleSnapshot).not.toBeNull();
    const snapshot = fixtureOperationsLifecycleSnapshot!;

    const { rerender } = render(
      <OperationsLifecycleWorkspace section="sessions" snapshot={snapshot} />,
    );
    expect(screen.getByRole('heading', { name: '授权、租约与会话健康' })).toBeTruthy();
    expect(screen.getByText('适配器就绪 · 未经 live 验证')).toBeTruthy();

    rerender(<OperationsLifecycleWorkspace section="interventions" snapshot={snapshot} />);
    expect(screen.getByRole('heading', { name: '人工接管队列' })).toBeTruthy();
    expect(screen.getByText('扫码')).toBeTruthy();
    expect(screen.getByText('Push MFA')).toBeTruthy();
    expect(screen.getAllByText('等待客户')).toHaveLength(2);

    rerender(<OperationsLifecycleWorkspace section="events" snapshot={snapshot} />);
    expect(screen.getByRole('heading', { name: '账号生命周期事件' })).toBeTruthy();
    expect(screen.getByText('账号进入隔离')).toBeTruthy();
    expect(screen.getByRole('article', { name: /撤销回执/ })).toBeTruthy();
  });

  it('fails closed without inferring lifecycle content when S01 has not supplied a snapshot', () => {
    render(<OperationsLifecycleWorkspace section="sessions" snapshot={null} />);

    expect(screen.getByText('样本不足')).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '授权、租约与会话健康' })).toBeNull();
    expect(screen.queryByText('平台账号')).toBeNull();
  });
});
