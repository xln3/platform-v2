import { useEffect, useState } from 'react';
import { getHealth, getOperationsLifecycle } from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { ProductShell, useOptionalExperienceContext } from '@geo/design-system';
import { Navigate } from 'react-router';
import { BusinessOverviewContainer, type BusinessOverviewLoader } from './business-overview';
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
    projectAware: true,
    group: '五项服务生产',
  },
  {
    id: 'service-outbound-risk',
    label: '2 · 主动拉踩内容核查',
    href: '/platform/operations/service-outbound-risk',
    projectAware: true,
    group: '五项服务生产',
  },
  {
    id: 'service-inbound-risk',
    label: '3 · 被拉踩内容核查',
    href: '/platform/operations/service-inbound-risk',
    projectAware: true,
    group: '五项服务生产',
  },
  {
    id: 'service-site-audit',
    label: '4 · 官网引用效率',
    href: '/platform/operations/service-site-audit',
    projectAware: true,
    group: '五项服务生产',
  },
  {
    id: 'service-pilot',
    label: '5 · 内容发布试点',
    href: '/platform/operations/service-pilot',
    projectAware: true,
    group: '五项服务生产',
  },
  {
    id: 'formal-reports',
    label: '正式报告生成',
    href: '/platform/operations/formal-reports',
    projectAware: true,
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
  {
    id: 'sessions',
    label: '会话健康',
    href: '/platform/operations/execution#platform-accounts',
    badge: '3',
    group: '采集',
  },
  {
    id: 'interventions',
    label: '人工接管',
    href: '/platform/operations/execution#interventions',
    badge: '2',
    group: '采集',
  },
  {
    id: 'events',
    label: '采集事件审计',
    href: '/platform/operations/execution#events',
    group: '采集',
  },
];

const liveOperationsNav = operationsNav.map((item) => ({
  id: item.id,
  label: item.label,
  ...(item.href ? { href: item.href } : {}),
  ...(item.group ? { group: item.group } : {}),
  ...(item.projectAware ? { projectAware: true } : {}),
}));

export const operationsRouteNav = operationsNav.map((item) => ({
  id: item.id,
  label: item.label,
  href: item.href ?? `/platform/operations/${item.id === 'overview' ? '' : `?section=${item.id}`}`,
  ...(item.group ? { group: item.group } : {}),
  ...(item.badge ? { badge: item.badge } : {}),
  ...(item.projectAware ? { projectAware: true } : {}),
}));

export const liveOperationsRouteNav = operationsRouteNav.map((item) => ({
  id: item.id,
  label: item.label,
  href: item.href,
  ...(item.projectAware ? { projectAware: true } : {}),
}));

export type OperationsRootSection = 'overview' | 'sessions' | 'interventions' | 'events';

type LifecycleLoader = typeof getOperationsLifecycle;

export function OperationsActiveWorkspace({
  section,
  fixtureMode,
  roles,
  loadBusiness,
  loadLifecycle = getOperationsLifecycle,
}: {
  section: OperationsRootSection;
  fixtureMode: boolean;
  roles: readonly string[];
  loadBusiness?: BusinessOverviewLoader;
  loadLifecycle?: LifecycleLoader;
}) {
  if (section === 'overview') {
    return (
      <BusinessOverviewContainer
        fixtureMode={fixtureMode}
        roles={roles}
        {...(loadBusiness ? { loadBusiness } : {})}
      />
    );
  }
  return (
    <OperationsLifecycleSection
      section={section}
      fixtureMode={fixtureMode}
      loadLifecycle={loadLifecycle}
    />
  );
}

export function OperationsLifecycleSection({
  section,
  fixtureMode,
  loadLifecycle = getOperationsLifecycle,
}: {
  section: Exclude<OperationsRootSection, 'overview'>;
  fixtureMode: boolean;
  loadLifecycle?: LifecycleLoader;
}) {
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
    void loadLifecycle(headers).then((result) => {
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
  }, [fixtureMode, loadLifecycle]);
  return (
    <OperationsLifecycleWorkspace
      section={section}
      snapshot={fixtureMode ? fixtureOperationsLifecycleSnapshot : liveSnapshot}
      unavailableState={liveState === 'ready' ? 'insufficient' : liveState}
    />
  );
}

export default function Shell() {
  const experience = useOptionalExperienceContext();
  const fixtureMode = experience?.source !== 'live';
  if (!experience) {
    return <Navigate to="login" replace />;
  }
  return (
    <ProductShell
      product="Operations Web"
      title="项目商务总览"
      description="按现有系统事实汇总项目建档、服务、执行与交付；报价、签约及回款台账尚未接入。"
      probe={getHealth}
      nav={experience?.source === 'live' ? liveOperationsNav : operationsNav}
    >
      {(active) => {
        const section: OperationsRootSection =
          active === 'sessions' || active === 'interventions' || active === 'events'
            ? active
            : 'overview';
        return (
          <OperationsActiveWorkspace
            section={section}
            fixtureMode={fixtureMode}
            roles={experience.roles}
          />
        );
      }}
    </ProductShell>
  );
}
