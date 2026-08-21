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
  { id: 'overview', label: '项目与商务总览', group: '项目与商务' },
  {
    id: 'service-visibility',
    label: '1 · AI 推荐排名测试',
    href: '/platform/operations/service-visibility',
    group: '五项服务生产',
  },
  {
    id: 'service-outbound-risk',
    label: '2 · 主动拉踩内容核查',
    href: '/platform/operations/service-outbound-risk',
    group: '五项服务生产',
  },
  {
    id: 'service-inbound-risk',
    label: '3 · 被拉踩内容核查',
    href: '/platform/operations/service-inbound-risk',
    group: '五项服务生产',
  },
  {
    id: 'service-site-audit',
    label: '4 · 官网引用效率',
    href: '/platform/operations/service-site-audit',
    group: '五项服务生产',
  },
  {
    id: 'service-pilot',
    label: '5 · 内容发布试点',
    href: '/platform/operations/service-pilot',
    group: '五项服务生产',
  },
  {
    id: 'formal-reports',
    label: '正式报告生成',
    href: '/platform/operations/formal-reports',
    group: '报告与交付',
  },
  {
    // 报告工作室是独立 app（/platform/reports/），无深链路由，纯 <a> 跳转整页打开。
    id: 'reports-delivery',
    label: '报告交付',
    href: '/platform/reports/',
    group: '报告与交付',
  },
  {
    id: 'execution',
    label: '执行与账号',
    href: '/platform/operations/execution',
    group: '采集',
  },
  {
    id: 'accounts',
    label: '账号管理',
    href: '/platform/operations/accounts',
    group: '采集',
  },
  {
    id: 'browsers',
    label: '浏览器管理',
    href: '/platform/operations/browsers',
    group: '采集',
  },
  {
    id: 'onboarding',
    label: '开户向导',
    href: '/platform/operations/onboarding',
    group: '项目与商务',
  },
  {
    id: 'post-analysis',
    label: '帖子分析',
    href: '/platform/operations/post-analysis',
    group: '分析',
  },
  {
    id: 'sop',
    label: '信源 SOP',
    href: '/platform/operations/sop',
    group: '内容生产与发布',
  },
  {
    id: 'media-prices',
    label: '媒体比价台',
    href: '/platform/operations/media-prices',
    group: '内容生产与发布',
  },
  {
    id: 'posting',
    label: '发帖',
    href: '/platform/operations/posting',
    group: '内容生产与发布',
  },
  {
    id: 'quotation-generator',
    label: '报价单生成',
    href: '/platform/operations/quotations',
    group: '项目与商务',
  },
  { id: 'sessions', label: '会话健康', badge: '3', group: '采集' },
  { id: 'interventions', label: '人工接管', badge: '2', group: '采集' },
  { id: 'events', label: '采集事件审计', group: '采集' },
];

const liveOperationsNav = operationsNav.map((item) => ({
  id: item.id,
  label: item.label,
  ...(item.href ? { href: item.href } : {}),
  ...(item.group ? { group: item.group } : {}),
}));

export const operationsRouteNav = operationsNav.map((item) => ({
  id: item.id,
  label: item.label,
  href: item.href ?? `/platform/operations/${item.id === 'overview' ? '' : `?section=${item.id}`}`,
  ...(item.group ? { group: item.group } : {}),
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
