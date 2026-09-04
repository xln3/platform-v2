// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FormalReportProductionProgress } from '../api';
import {
  ProductionProgressStepper,
  ProductionProgressStepperView,
} from './ProductionProgressStepper';

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

function stage(stageName: string, status: string) {
  return { stage: stageName, status, entered_at: null };
}

function progressPayload(
  overrides: Record<string, unknown> = {},
): FormalReportProductionProgress {
  return {
    production_pub_id: 'frp_test_001',
    source: 'workflow',
    failed: false,
    error_code: null,
    stages: [
      stage('queued', 'done'),
      stage('binding_snapshot', 'done'),
      stage('preflight', 'current'),
      stage('running', 'pending'),
      stage('awaiting_review', 'pending'),
      stage('finalizing', 'pending'),
      stage('signed', 'pending'),
    ],
    ...overrides,
  } as FormalReportProductionProgress;
}

describe('ProductionProgressStepperView', () => {
  afterEach(() => {
    cleanup();
  });

  it('highlights the current stage and checks done stages', () => {
    const { container } = render(
      <ProductionProgressStepperView progress={progressPayload()} />,
    );
    const steps = [...container.querySelectorAll('.formal-progress-step')];
    expect(steps).toHaveLength(7);
    expect(steps[0]?.className).toContain('done');
    expect(steps[0]?.textContent).toContain('✓');
    expect(steps[2]?.className).toContain('current');
    expect(steps[2]?.textContent).toContain('预检');
    expect(steps[3]?.className).toContain('pending');
    expect(steps[3]?.textContent).toContain('生成报告');
    expect(steps[3]?.textContent).not.toContain('✓');
  });

  it('marks the failed stage red and surfaces the error code', () => {
    const failed = progressPayload({
      failed: true,
      error_code: 'production_failed',
      stages: [
        stage('queued', 'done'),
        stage('binding_snapshot', 'done'),
        stage('preflight', 'done'),
        stage('running', 'failed'),
        stage('awaiting_review', 'pending'),
        stage('finalizing', 'pending'),
        stage('signed', 'pending'),
      ],
    });
    const { container } = render(<ProductionProgressStepperView progress={failed} />);
    const steps = [...container.querySelectorAll('.formal-progress-step')];
    expect(steps[3]?.className).toContain('failed');
    expect(steps[3]?.textContent).toContain('✗');
    expect(screen.getByText('失败：production_failed')).toBeTruthy();
  });

  it('notes the honest db fallback source', () => {
    render(
      <ProductionProgressStepperView progress={progressPayload({ source: 'db_fallback' })} />,
    );
    expect(screen.getByText('工作流不可达，显示库内状态')).toBeTruthy();
  });

  it('renders a fully done chain for a signed production', () => {
    const signed = progressPayload({
      stages: [
        stage('queued', 'done'),
        stage('binding_snapshot', 'done'),
        stage('preflight', 'done'),
        stage('running', 'done'),
        stage('awaiting_review', 'done'),
        stage('finalizing', 'done'),
        stage('signed', 'done'),
      ],
    });
    const { container } = render(<ProductionProgressStepperView progress={signed} />);
    expect(container.querySelectorAll('.formal-progress-step.done')).toHaveLength(7);
    expect(container.querySelector('.formal-progress-step.current')).toBeNull();
  });
});

describe('ProductionProgressStepper', () => {
  const urls: string[] = [];
  let payload: unknown = progressPayload();

  beforeEach(() => {
    urls.length = 0;
    payload = progressPayload();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = input instanceof Request ? input.url : String(input);
        urls.push(url);
        return Response.json(payload);
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('fetches the progress endpoint and renders the stepper', async () => {
    render(<ProductionProgressStepper session={session} productionPubId="frp_test_001" />);
    await screen.findByText('预检');
    expect(urls[0]).toContain('/api/v2/reports/formal-productions/frp_test_001/progress');
  });

  it('polls every 15 seconds while the production is not terminal', async () => {
    vi.useFakeTimers();
    render(<ProductionProgressStepper session={session} productionPubId="frp_test_001" />);
    await vi.advanceTimersByTimeAsync(0);
    expect(urls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(urls).toHaveLength(2);
  });

  it('stops polling once the production reaches a terminal state', async () => {
    payload = progressPayload({
      stages: [
        stage('queued', 'done'),
        stage('binding_snapshot', 'done'),
        stage('preflight', 'done'),
        stage('running', 'done'),
        stage('awaiting_review', 'done'),
        stage('finalizing', 'done'),
        stage('signed', 'done'),
      ],
    });
    vi.useFakeTimers();
    render(<ProductionProgressStepper session={session} productionPubId="frp_test_001" />);
    await vi.advanceTimersByTimeAsync(0);
    expect(urls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(45_000);
    expect(urls).toHaveLength(1);
  });

  it('renders nothing for an invalid payload or a failed fetch', async () => {
    payload = { items: [] };
    const { container } = render(
      <ProductionProgressStepper session={session} productionPubId="frp_test_001" />,
    );
    await waitFor(() => expect(urls).toHaveLength(1));
    expect(container.querySelector('.formal-progress')).toBeNull();

    cleanup();
    urls.length = 0;
    vi.mocked(fetch).mockImplementationOnce(async (input: string | URL | Request) => {
      urls.push(input instanceof Request ? input.url : String(input));
      throw new Error('network down');
    });
    const second = render(
      <ProductionProgressStepper session={session} productionPubId="frp_test_001" />,
    );
    await waitFor(() => expect(urls).toHaveLength(1));
    expect(second.container.querySelector('.formal-progress')).toBeNull();
  });
});
