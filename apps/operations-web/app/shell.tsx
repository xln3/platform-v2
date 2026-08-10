import { useEffect, useState } from 'react';
import { getHealth, getOperationsLifecycle } from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { ProductShell, useOptionalExperienceContext } from '@geo/design-system';
import { Navigate } from 'react-router';
import {
  OperationsLifecycleWorkspace,
  fixtureOperationsLifecycleSnapshot,
  type OperationsLifecycleSnapshot,
} from './lifecycle-snapshot';

export const operationsNav = [
  { id: 'overview', label: '总览' },
  {
    id: 'service-visibility',
    label: '品牌GEO评测',
    href: '/platform/operations/service-visibility',
  },
  {
    id: 'service-risk',
    label: '内容风险核查',
    href: '/platform/operations/service-risk',
  },
  {
    id: 'service-site-audit',
    label: '官网引用能效',
    href: '/platform/operations/service-site-audit',
  },
  {
    id: 'service-pilot',
    label: '试点效果验证',
    href: '/platform/operations/service-pilot',
  },
  {
    id: 'execution',
    label: '执行与账号',
    href: '/platform/operations/execution',
  },
  {
    id: 'onboarding',
    label: '开户向导',
    href: '/platform/operations/onboarding',
  },
  {
    id: 'post-analysis',
    label: '帖子分析',
    href: '/platform/operations/post-analysis',
  },
  {
    id: 'sop',
    label: '信源 SOP',
    href: '/platform/operations/sop',
  },
  {
    id: 'media-prices',
    label: '媒体比价台',
    href: '/platform/operations/media-prices',
  },
  { id: 'sessions', label: '会话健康', badge: '3' },
  { id: 'interventions', label: '待人工', badge: '2' },
  { id: 'events', label: '事件审计' },
];

const liveOperationsNav = operationsNav.map((item) => ({
  id: item.id,
  label: item.label,
  ...(item.href ? { href: item.href } : {}),
}));

export const operationsRouteNav = operationsNav.map((item) => ({
  id: item.id,
  label: item.label,
  href: item.href ?? `/platform/operations/${item.id === 'overview' ? '' : `?section=${item.id}`}`,
  ...(item.badge ? { badge: item.badge } : {}),
}));

export const liveOperationsRouteNav = operationsRouteNav.map((item) => ({
  id: item.id,
  label: item.label,
  href: item.href,
}));

export default function Shell() {
  const experience = useOptionalExperienceContext();
  const fixtureMode = experience?.source !== 'live';
  const [liveSnapshot, setLiveSnapshot] = useState<OperationsLifecycleSnapshot | null>(null);
  const [liveState, setLiveState] = useState<'loading' | 'ready' | 'failed' | 'forbidden'>(
    fixtureMode ? 'ready' : 'loading',
  );
  useEffect(() => {
    if (fixtureMode) {
      setLiveSnapshot(null);
      setLiveState('ready');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveSnapshot(null);
      setLiveState('failed');
      return;
    }
    let cancelled = false;
    setLiveSnapshot(null);
    setLiveState('loading');
    void getOperationsLifecycle(headers).then((result) => {
      if (cancelled) return;
      if (result.kind === 'ready') {
        setLiveSnapshot(result.data);
        setLiveState('ready');
      } else {
        setLiveSnapshot(null);
        setLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [experience?.tenantPubId, fixtureMode]);
  if (!experience) {
    return <Navigate to="login" replace />;
  }
  const snapshot = fixtureMode ? fixtureOperationsLifecycleSnapshot : liveSnapshot;
  return (
    <ProductShell
      product="Operations Web"
      title="运行总览"
      description="可靠采集、会话生命周期、人工接管与数据新鲜度。"
      probe={getHealth}
      nav={experience?.source === 'live' ? liveOperationsNav : operationsNav}
    >
      {(active) => (
        <OperationsLifecycleWorkspace
          section={
            active === 'sessions' || active === 'interventions' || active === 'events'
              ? active
              : 'overview'
          }
          snapshot={snapshot}
          unavailableState={liveState === 'ready' ? 'insufficient' : liveState}
        />
      )}
    </ProductShell>
  );
}
