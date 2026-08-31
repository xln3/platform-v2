import { useEffect, useState } from 'react';
import {
  getCustomerFiveServices,
  type CustomerFiveService,
  type CustomerFiveServices,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { Badge, StatePanel, useOptionalExperienceContext } from '@geo/design-system';
import { CustomerProjectOverview } from './customer-project-overview';
import { CustomerSamplingProgressEntry } from './customer-sampling-progress';

const fixtureServices: CustomerFiveServices = {
  projectPubId: 'prj_fixture',
  services: [
    {
      serviceNumber: 1,
      serviceCode: 'ranking_test',
      name: 'AI 推荐排名效果测试',
      entitlementState: 'active',
      catalogVersion: 'quotation-services-v2',
      summary: {
        answerCount: 120,
        officialSiteStage: null,
        officialSiteUOccurrences: null,
        officialSiteVOccurrences: null,
        officialSiteWOccurrences: null,
        uObservation: null,
        vObservation: null,
        wObservation: null,
      },
      latestDelivery: null,
    },
    {
      serviceNumber: 2,
      serviceCode: 'outbound_disparagement_audit',
      name: '主动拉踩内容核查',
      entitlementState: 'inactive',
      catalogVersion: null,
      summary: null,
      latestDelivery: null,
    },
    {
      serviceNumber: 3,
      serviceCode: 'inbound_disparagement_audit',
      name: '被拉踩内容核查',
      entitlementState: 'active',
      catalogVersion: 'quotation-services-v2',
      summary: null,
      latestDelivery: null,
    },
    {
      serviceNumber: 4,
      serviceCode: 'official_site_audit',
      name: '官网内容 AI 引用效率分析',
      entitlementState: 'active',
      catalogVersion: 'quotation-services-v2',
      summary: {
        answerCount: null,
        officialSiteStage: 'v_not_w',
        officialSiteUOccurrences: 18,
        officialSiteVOccurrences: 6,
        officialSiteWOccurrences: 0,
        uObservation: 'observed',
        vObservation: 'observed',
        wObservation: 'observed',
      },
      latestDelivery: null,
    },
    {
      serviceNumber: 5,
      serviceCode: 'content_publishing_pilot',
      name: '内容发布与排名提升试点',
      entitlementState: 'inactive',
      catalogVersion: null,
      summary: null,
      latestDelivery: null,
    },
  ],
};

const entitlementLabel: Record<CustomerFiveService['entitlementState'], string> = {
  inactive: '未开通',
  active: '已开通',
  suspended: '已暂停',
  expired: '已到期',
};

const officialStageLabel: Record<string, string> = {
  u_unobserved: 'U 阶段不可观察，不能判断官网是否进入候选',
  u_partially_observed: 'U 阶段仅部分可观察，不能把缺失部分记为零',
  not_in_u: '官网尚未进入 U 候选',
  v_unobserved: '已进入 U，V 阶段不可观察',
  v_partially_observed: '已进入 U，V 阶段仅部分可观察',
  u_not_v: '已进入 U，尚未进入 V',
  w_pending: '已进入 V，W 内容贡献仍在分析',
  v_not_w: '已进入 V，尚无可验证 W 片段',
  entered_w: '已有内容进入 W',
};

export function formatObservationCount(count: number | null, observation: string | null): string {
  if (observation === 'unobserved') return '不可观察';
  if (observation === 'not_applicable') return '不适用（尚未进入上一阶段）';
  if (observation === 'partial') {
    return count === null ? '部分可观察' : `${count}（部分可观察）`;
  }
  return count === null ? '尚无可交付数据' : String(count);
}

function ServiceResult({ service }: { service: CustomerFiveService }) {
  if (service.entitlementState !== 'active') {
    return <p className="panel-subtitle">该服务未处于有效授权期，接口不会返回分析结果。</p>;
  }
  if (service.serviceNumber === 1 && service.summary?.answerCount !== null) {
    return <p>当前授权投影包含 {service.summary?.answerCount ?? 0} 条已完成真实回答。</p>;
  }
  if (service.serviceNumber === 4 && service.summary) {
    const summary = service.summary;
    return (
      <dl className="case-summary">
        <div>
          <dt>当前环节</dt>
          <dd>{officialStageLabel[summary.officialSiteStage ?? ''] ?? '等待可解释结果'}</dd>
        </div>
        <div>
          <dt>官网 U occurrence</dt>
          <dd>{formatObservationCount(summary.officialSiteUOccurrences, summary.uObservation)}</dd>
        </div>
        <div>
          <dt>官网 V occurrence</dt>
          <dd>{formatObservationCount(summary.officialSiteVOccurrences, summary.vObservation)}</dd>
        </div>
        <div>
          <dt>官网 W occurrence</dt>
          <dd>{formatObservationCount(summary.officialSiteWOccurrences, summary.wObservation)}</dd>
        </div>
      </dl>
    );
  }
  return (
    <p className="panel-subtitle">
      该服务已开通；客户侧只展示已审核交付物，不披露内部候选、规则或生产过程。
    </p>
  );
}

export function CustomerServicesWorkspace({
  focus,
  mode = 'services',
}: {
  focus?: 1 | 2 | 3 | 4 | 5;
  mode?: 'overview' | 'services';
}) {
  const experience = useOptionalExperienceContext();
  const fixture = experience?.source !== 'live';
  const [state, setState] = useState<'loading' | 'ready' | 'failed' | 'forbidden'>(
    fixture ? 'ready' : 'loading',
  );
  const [view, setView] = useState<CustomerFiveServices | null>(fixture ? fixtureServices : null);

  useEffect(() => {
    if (fixture) {
      setView(fixtureServices);
      setState('ready');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    const projectPubId = experience?.projectPubId;
    if (!headers || !projectPubId) {
      setView(null);
      setState('failed');
      return;
    }
    let cancelled = false;
    setState('loading');
    void getCustomerFiveServices(headers, projectPubId).then((result) => {
      if (cancelled) return;
      if (result.kind === 'ready') {
        setView(result.data);
        setState('ready');
      } else {
        setView(null);
        setState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [experience?.projectPubId, fixture]);

  const samplingProgressEntry = focus === 1 ? <CustomerSamplingProgressEntry /> : null;
  if (state !== 'ready') {
    return (
      <div className="workspace-stack">
        {samplingProgressEntry}
        <StatePanel state={state} />
      </div>
    );
  }
  if (!view) {
    return (
      <div className="workspace-stack">
        {samplingProgressEntry}
        <StatePanel state="empty" />
      </div>
    );
  }
  if (mode === 'overview') {
    return (
      <CustomerProjectOverview
        projectLabel={experience?.projectLabel ?? '当前客户项目'}
        services={view.services}
      />
    );
  }
  const services = focus
    ? view.services.filter((service) => service.serviceNumber === focus)
    : view.services;
  return (
    <div className="workspace-stack">
      <section className="panel">
        <span className="overline">Contract-scoped delivery</span>
        <h2>{focus ? services[0]?.name : '我的五项服务'}</h2>
        <p className="panel-subtitle">
          每项服务独立授权、审核和交付。未开通服务只显示状态，不返回内部分析结果。
        </p>
      </section>
      {samplingProgressEntry}
      <div className="source-grid">
        {services.map((service) => (
          <article key={service.serviceCode}>
            <div className="account-head">
              <div>
                <span className="overline">服务 {service.serviceNumber}</span>
                <h3>{service.name}</h3>
              </div>
              <Badge tone={service.entitlementState === 'active' ? 'positive' : 'neutral'}>
                {entitlementLabel[service.entitlementState]}
              </Badge>
            </div>
            <ServiceResult service={service} />
            {service.latestDelivery ? (
              <p>
                最新交付：<a href="?section=reports">{service.latestDelivery.title}</a>
              </p>
            ) : service.entitlementState === 'active' ? (
              <p className="panel-subtitle">暂无已签发交付物。</p>
            ) : null}
          </article>
        ))}
      </div>
    </div>
  );
}
