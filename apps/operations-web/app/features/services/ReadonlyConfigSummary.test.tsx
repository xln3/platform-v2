// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ReadonlyConfigSummary } from './ReadonlyConfigSummary';

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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ReadonlyConfigSummary', () => {
  it('shows an honest empty state with navigation and no write controls', async () => {
    mockCurrent({ effective: null, next_pending: null });
    render(<ReadonlyConfigSummary session={session} projectPubId="prj_test" />);

    expect(await screen.findByText(/没有已生效的冻结配置/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '前往开户向导' }).getAttribute('href')).toBe(
      '/platform/operations/onboarding',
    );
    expect(document.querySelector('input, textarea, select')).toBeNull();
    expect(screen.queryByRole('button', { name: /冻结|启动|周期/ })).toBeNull();
  });

  it('maps a legacy snapshot to consumer Web and marks city-era regions honestly', async () => {
    mockCurrent({
      effective: revision({
        snapshot: {
          query_groups: [{ name: '核心问题', items: [{ text: '品牌怎么样？', priority: 1 }] }],
          models: ['doubao'],
          modes: ['normal'],
          regions: ['北京'],
          frequency: 'manual',
          effective_at: '2026-08-20T00:00:00Z',
          channel: 'api',
        },
      }),
      next_pending: null,
    });

    render(<ReadonlyConfigSummary session={session} projectPubId="prj_test" />);
    expect(await screen.findByText('网页端应用')).toBeTruthy();
    expect(screen.getByText('北京 · 旧版地域值')).toBeTruthy();
    expect(screen.getByText('未冻结', { selector: 'dd' })).toBeTruthy();
    expect(document.body.textContent).not.toContain('channel=api');
    expect(document.body.textContent).not.toContain('采集来源：API');
    expect((document.querySelector('img') as HTMLImageElement).getAttribute('src')).toMatch(
      /platform-icons\/doubao\.png$/,
    );
  });

  it('renders canonical v2 targets, three surfaces, task scale, pending revision and question pages', async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(globalThis.navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    mockCurrent({
      effective: revision({
        question_groups: [
          {
            name: '评测问题',
            items: Array.from({ length: 5 }, (_, index) => ({
              text: `冻结问题 ${index + 1}`,
              priority: index + 1,
            })),
          },
        ],
        snapshot: {
          schema_version: 'collection-config-v2',
          question_set_revision: 'qsr_20260824',
          collection_targets: [
            {
              platform: 'doubao',
              collection_surface: 'consumer_web',
              product_variant: 'consumer-web-default',
              interaction_modes: ['normal', 'deep_think'],
            },
            {
              platform: 'deepseek',
              collection_surface: 'provider_api',
              product_variant: 'api-v1',
              interaction_modes: ['normal'],
            },
            {
              platform: 'yuanbao',
              collection_surface: 'consumer_app',
              product_variant: 'android-stable',
              interaction_modes: ['normal'],
            },
          ],
          province_codes: ['110000', '310000'],
          samples_per_cell: 3,
          schedule_policy: { frequency: 'daily' },
          comparison_policy_revision: 'cmp_1',
        },
      }),
      next_pending: revision({
        revision: 8,
        pub_id: 'cfv_pending',
        effective_at: '2027-01-01T00:00:00Z',
      }),
    });

    render(<ReadonlyConfigSummary session={session} projectPubId="prj_test" />);
    await screen.findByText('120 个主采样位');
    fireEvent.click(screen.getByRole('button', { name: '复制完整哈希' }));
    expect(writeText).toHaveBeenCalledWith('a'.repeat(64));
    expect((await screen.findByText('完整哈希已复制。')).getAttribute('role')).toBe('status');
    const sourceDefinition = screen.getByText('采集来源').parentElement?.querySelector('dd');
    expect(sourceDefinition?.textContent).toContain('API');
    expect(sourceDefinition?.textContent).toContain('网页端应用');
    expect(sourceDefinition?.textContent).toContain('移动端 APP');
    expect(screen.getByText('北京市（GB110000）')).toBeTruthy();
    expect(screen.getByText('上海市（GB310000）')).toBeTruthy();
    expect(screen.queryByText(/旧版地域值/)).toBeNull();
    expect(screen.getByText(/下一待生效版本：v8/)).toBeTruthy();
    const questionBlock = screen.getByRole('heading', { name: '问题明细' }).parentElement!;
    expect(within(questionBlock).getAllByRole('listitem')).toHaveLength(4);
    expect(screen.queryByText('冻结问题 5')).toBeNull();
    fireEvent.click(within(questionBlock).getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('冻结问题 5')).toBeTruthy();
    expect(within(questionBlock).getAllByRole('listitem')).toHaveLength(1);
    expect(document.querySelector('textarea, select')).toBeNull();
    expect(screen.getByRole('spinbutton', { name: '跳转页码' })).toBeTruthy();
    expect(screen.queryByText('频率', { exact: true })).toBeNull();
    expect(screen.queryByRole('button', { name: /冻结|启动|周期/ })).toBeNull();
  });
});

function mockCurrent(payload: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string | URL | Request) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      expect(url.pathname).toBe('/api/v2/projects/prj_test/config/current');
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

function revision(
  overrides: Partial<{
    pub_id: string;
    revision: number;
    effective_at: string;
    frozen_at: string;
    snapshot_hash: string;
    snapshot: Record<string, unknown>;
    question_groups: Array<{ name: string; items: Array<{ text: string; priority: number }> }>;
  }> = {},
) {
  return {
    pub_id: 'cfv_effective',
    revision: 7,
    effective_at: '2026-08-20T00:00:00Z',
    frozen_at: '2026-08-19T00:00:00Z',
    snapshot_hash: 'a'.repeat(64),
    snapshot: {},
    question_groups: [],
    ...overrides,
  };
}
