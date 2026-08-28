// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import type { CustomerFiveService } from '@geo/api-client';
import { afterEach, describe, expect, it } from 'vitest';
import { CustomerProjectShowcase } from './customer-project-showcase';
import { CustomerServicesWorkspace, formatObservationCount } from './customer-services-workspace';

afterEach(cleanup);

describe('CustomerServicesWorkspace', () => {
  it('keeps all five entitlements distinct and withholds inactive results', () => {
    render(<CustomerServicesWorkspace />);

    expect(screen.getByText('我的五项服务')).toBeTruthy();
    expect(screen.getByText('主动拉踩内容核查')).toBeTruthy();
    expect(screen.getByText('被拉踩内容核查')).toBeTruthy();
    expect(screen.getAllByText('未开通')).toHaveLength(2);
    expect(screen.getAllByText('该服务未处于有效授权期，接口不会返回分析结果。')).toHaveLength(2);
    expect(screen.getByText('从业务问题到可验证结果')).toBeTruthy();
    expect(screen.getByText('当前项目成果展示')).toBeTruthy();
    expect(screen.getByText('当前客户项目')).toBeTruthy();
    expect(screen.getByText('共享前端不内置其他客户名称或案例数据')).toBeTruthy();
    expect(screen.getByText('标准 90 天实施甘特图')).toBeTruthy();
    expect(document.body.textContent).not.toContain('盛邦安全');
    expect(document.body.textContent).not.toContain('中英人寿');
    expect(document.body.textContent).not.toContain('143 / 144');
    expect(document.body.textContent).not.toContain('264');
  });

  it('shows the official-site U/V/W stage without turning unknown into zero', () => {
    render(<CustomerServicesWorkspace focus={4} />);

    expect(screen.getByText('已进入 V，尚无可验证 W 片段')).toBeTruthy();
    expect(screen.getByText('官网 U occurrence')).toBeTruthy();
    expect(screen.getByText('18')).toBeTruthy();
    expect(screen.getByText('6')).toBeTruthy();
    expect(screen.getByText('0')).toBeTruthy();
    expect(screen.queryByText('标准 90 天实施甘特图')).toBeNull();
  });

  it('labels partial, unobserved and not-applicable counts explicitly', () => {
    expect(formatObservationCount(18, 'partial')).toBe('18（部分可观察）');
    expect(formatObservationCount(null, 'unobserved')).toBe('不可观察');
    expect(formatObservationCount(null, 'not_applicable')).toBe('不适用（尚未进入上一阶段）');
    expect(formatObservationCount(0, 'observed')).toBe('0');
  });

  it('shows the current customer project and authorized results without masking them', () => {
    const ownService = {
      serviceNumber: 1,
      serviceCode: 'ranking_test',
      name: '当前客户推荐排名测试',
      entitlementState: 'active',
      catalogVersion: 'quotation-services-v2',
      summary: {
        answerCount: 37,
        officialSiteStage: null,
        officialSiteUOccurrences: null,
        officialSiteVOccurrences: null,
        officialSiteWOccurrences: null,
        uObservation: null,
        vObservation: null,
        wObservation: null,
      },
      latestDelivery: null,
    } satisfies CustomerFiveService;

    render(<CustomerProjectShowcase projectLabel="甲方完整项目名称" services={[ownService]} />);

    expect(screen.getByText('甲方完整项目名称')).toBeTruthy();
    expect(screen.getByText(/当前客户推荐排名测试/u)).toBeTruthy();
    expect(screen.getByText('37')).toBeTruthy();
  });
});
