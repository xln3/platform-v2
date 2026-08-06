// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { OnboardingWizard } from './OnboardingWizard';
import type { SessionContext } from './api';

const baseSession: SessionContext = {
  tenantId: 'tnt_test',
  actorId: 'usr_test',
  role: 'operator',
  headers: {
    'X-Tenant-Id': 'tnt_test',
    'X-Actor-Id': 'subject_test',
    'X-Actor-Role': 'operator',
  },
};

const onboardingView = {
  customer_pub_id: 'cus_test',
  project_pub_id: 'prj_test',
  config_version_pub_id: 'cfv_test',
  config_revision: 1,
  task_count: 10,
  mvp_document_url: '/api/v2/onboarding/prj_test/documents/mvp',
  measurement_requirements_url: '/api/v2/onboarding/prj_test/documents/measurement-requirements',
};

function fillRequiredFields() {
  fireEvent.change(screen.getByLabelText('客户名称'), { target: { value: '测试客户' } });
  fireEvent.change(screen.getByLabelText('项目名称'), { target: { value: '测试项目' } });
  fireEvent.change(screen.getByLabelText('对接人角色'), { target: { value: '市场总监' } });
  fireEvent.change(screen.getByLabelText('目标受众'), { target: { value: 'B2B企业客户' } });
  fireEvent.change(screen.getByLabelText('对外公开口径'), { target: { value: '公开口径' } });
  fireEvent.change(screen.getByLabelText('品牌名称'), { target: { value: '测试品牌' } });
  fireEvent.change(screen.getByLabelText('官网'), { target: { value: 'https://example.com' } });
  fireEvent.change(screen.getByLabelText('产品名称'), { target: { value: '测试产品' } });
  fireEvent.change(screen.getByLabelText('禁用宣称'), { target: { value: '不得承诺收益' } });
  fireEvent.change(screen.getByLabelText('评测目标'), { target: { value: '提升AI搜索曝光' } });
  fireEvent.change(screen.getByLabelText(/监测问题/), {
    target: { value: '品牌口碑如何？\n产品值得买吗？' },
  });
  fireEvent.click(screen.getByLabelText(/客户已书面确认/));
}

describe('OnboardingWizard', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        const method = typeof input === 'object' && 'method' in input ? input.method : 'GET';
        if (url.endsWith('/api/v2/onboarding') && method === 'POST') {
          return new Response(JSON.stringify(onboardingView), {
            status: 201,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        return new Response('not found', { status: 404 });
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('submits a full onboarding payload with an idempotency key and shows the receipt', async () => {
    render(<OnboardingWizard session={baseSession} />);
    fillRequiredFields();
    fireEvent.change(screen.getByLabelText(/竞品/), { target: { value: '竞品甲, 竞品乙' } });
    fireEvent.click(screen.getByRole('button', { name: '提交开户' }));
    await screen.findByTestId('onboarding-receipt');
    expect(screen.getByText('cus_test')).toBeTruthy();
    expect(screen.getByText('prj_test')).toBeTruthy();
    expect(screen.getByText('10 个')).toBeTruthy();
    const mvp = screen.getByRole('link', { name: /MVP 服务文档/ }) as HTMLAnchorElement;
    expect(mvp.href).toContain('/api/v2/onboarding/prj_test/documents/mvp');
    const requirements = screen.getByRole('link', {
      name: /GEO评测需求表/,
    }) as HTMLAnchorElement;
    expect(requirements.href).toContain(
      '/api/v2/onboarding/prj_test/documents/measurement-requirements',
    );

    const fetchMock = vi.mocked(fetch);
    const [input] = fetchMock.mock.calls[0] as [Request];
    expect(input.url).toContain('/api/v2/onboarding');
    expect(input.headers.get('Idempotency-Key')).toMatch(/^onboarding-[0-9a-f-]{36}$/);
    expect(input.headers.get('X-Actor-Role')).toBe('operator');
    const body = (await input.json()) as Record<string, unknown>;
    expect(body.customer_name).toBe('测试客户');
    expect(body.questions).toEqual(['品牌口碑如何？', '产品值得买吗？']);
    expect(body.competitors).toEqual(['竞品甲', '竞品乙']);
    expect(body.models).toEqual(['doubao', 'deepseek', 'yiyan', 'tongyi', 'yuanbao']);
    expect(body.regions).toEqual(['全国']);
    expect(body.frequency).toBe('weekly');
    expect(body.truth_confirmed).toBe(true);
  });

  it('keeps the submit button disabled until truth is confirmed', () => {
    render(<OnboardingWizard session={baseSession} />);
    fillRequiredFields();
    fireEvent.click(screen.getByLabelText(/客户已书面确认/));
    const submit = screen.getByRole('button', { name: '提交开户' }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText(/客户已书面确认/));
    expect(submit.disabled).toBe(false);
  });

  it('gates submission for read-only roles', () => {
    render(<OnboardingWizard session={{ ...baseSession, role: 'reviewer' }} />);
    expect(screen.getByText(/当前角色仅可查看/)).toBeTruthy();
    const submit = screen.getByRole('button', { name: '提交开户' }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });

  it('surfaces the backend error code on failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: { code: 'truth_confirmation_required' } }), {
            status: 400,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    );
    render(<OnboardingWizard session={baseSession} />);
    fillRequiredFields();
    fireEvent.click(screen.getByRole('button', { name: '提交开户' }));
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('truth_confirmation_required'),
    );
  });
});
