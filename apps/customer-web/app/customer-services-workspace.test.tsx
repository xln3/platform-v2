// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
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
  });

  it('shows the official-site U/V/W stage without turning unknown into zero', () => {
    render(<CustomerServicesWorkspace focus={4} />);

    expect(screen.getByText('已进入 V，尚无可验证 W 片段')).toBeTruthy();
    expect(screen.getByText('官网 U occurrence')).toBeTruthy();
    expect(screen.getByText('18')).toBeTruthy();
    expect(screen.getByText('6')).toBeTruthy();
    expect(screen.getByText('0')).toBeTruthy();
  });

  it('labels partial, unobserved and not-applicable counts explicitly', () => {
    expect(formatObservationCount(18, 'partial')).toBe('18（部分可观察）');
    expect(formatObservationCount(null, 'unobserved')).toBe('不可观察');
    expect(formatObservationCount(null, 'not_applicable')).toBe('不适用（尚未进入上一阶段）');
    expect(formatObservationCount(0, 'observed')).toBe('0');
  });
});
