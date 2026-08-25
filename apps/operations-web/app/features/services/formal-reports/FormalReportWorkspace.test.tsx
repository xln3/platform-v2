// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FormalReportWorkspace } from './FormalReportWorkspace';

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

const project = {
  pub_id: 'prj_test',
  tenant_pub_id: 'tnt_test',
  name: '盛邦安全 GEO',
  customer_name: '盛邦安全',
  state: 'active',
  updated_at: '2026-08-12T00:00:00Z',
};

function production(overrides: Record<string, unknown> = {}) {
  return {
    pub_id: 'frp_test_001',
    project_pub_id: 'prj_test',
    services: [1, 2, 3],
    service_catalog_version: 'quotation_services_v2',
    status: 'queued',
    document_status: 'internal_review',
    window_start: '2026-08-01',
    window_end: '2026-08-12',
    before_window: null,
    after_window: null,
    candidate_group_strategy: 'preregistered_scope_v1',
    workflow_id: 'formal-report/frp_test_001',
    fact_snapshot_hash: null,
    outputs: [],
    error_code: null,
    created_at: '2026-08-12T12:00:00Z',
    updated_at: '2026-08-12T12:00:00Z',
    ...overrides,
  };
}

type RecordedCall = {
  method: string;
  url: string;
  body: unknown;
  idempotencyKey: string | null;
};

describe('FormalReportWorkspace', () => {
  const calls: RecordedCall[] = [];
  let listItems: unknown[] = [];

  beforeEach(() => {
    calls.length = 0;
    listItems = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const request =
          input instanceof Request
            ? input
            : new Request(input instanceof URL ? input.href : input, init);
        const body = request.method === 'POST' ? await request.clone().json() : null;
        calls.push({
          method: request.method,
          url: request.url,
          body,
          idempotencyKey: request.headers.get('Idempotency-Key'),
        });
        if (request.method === 'POST') {
          const created = production();
          listItems = [created];
          return new Response(JSON.stringify(created), {
            status: 201,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        return new Response(JSON.stringify({ items: listItems }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  function fillService5SopProject() {
    fireEvent.change(screen.getByLabelText('服务 5 内容 SOP 项目 ID'), {
      target: { value: 'spr_test_owned_content' },
    });
  }

  it('starts an idempotent production with selected services and a frozen window', async () => {
    render(<FormalReportWorkspace session={session} project={project} />);
    await screen.findByText('当前项目还没有正式报告生产记录。');

    fireEvent.click(screen.getByLabelText(/服务 5 · 发帖提排名/));
    fireEvent.change(screen.getByLabelText('2. 事实窗口开始'), {
      target: { value: '2026-08-01' },
    });
    fireEvent.change(screen.getByLabelText('事实窗口结束'), {
      target: { value: '2026-08-12' },
    });
    fireEvent.click(screen.getByRole('button', { name: '冻结事实并启动生成' }));

    await screen.findByText(/生产请求 frp_test_001 已进入排队中状态/);
    const create = calls.find((call) => call.method === 'POST');
    expect(create?.url).toContain('/api/v2/reports/formal-productions');
    expect(create?.idempotencyKey?.length).toBeGreaterThanOrEqual(16);
    expect(create?.body).toEqual({
      project_pub_id: 'prj_test',
      services: [1, 2, 3, 4],
      service_catalog_version: 'quotation_services_v2',
      window_start: '2026-08-01',
      window_end: '2026-08-12',
      document_status: 'internal_review',
      candidate_group_strategy: 'preregistered_scope_v1',
      version: 'V1.0',
      prepared_by: 'usr_test',
      prepared_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
    });
  });

  it('requires separated service-5 arms and submits both windows', async () => {
    render(<FormalReportWorkspace session={session} project={project} />);
    await screen.findByText('当前项目还没有正式报告生产记录。');
    fillService5SopProject();

    fireEvent.change(screen.getByLabelText('发布前开始'), {
      target: { value: '2026-06-01' },
    });
    fireEvent.change(screen.getByLabelText('发布前结束'), {
      target: { value: '2026-06-30' },
    });
    fireEvent.change(screen.getByLabelText('发布后开始'), {
      target: { value: '2026-07-01' },
    });
    fireEvent.change(screen.getByLabelText('发布后结束'), {
      target: { value: '2026-07-31' },
    });
    fireEvent.click(screen.getByRole('button', { name: '冻结事实并启动生成' }));
    await screen.findByText(/生产请求 frp_test_001/);

    const body = calls.find((call) => call.method === 'POST')?.body as Record<string, unknown>;
    expect(body.services).toEqual([1, 2, 3, 4, 5]);
    expect(body.service_catalog_version).toBe('quotation_services_v2');
    expect(body.sop_project_pub_id).toBe('spr_test_owned_content');
    expect(body.before_window).toEqual({ start: '2026-06-01', end: '2026-06-30' });
    expect(body.after_window).toEqual({ start: '2026-07-01', end: '2026-07-31' });

    fireEvent.change(screen.getByLabelText('发布后开始'), {
      target: { value: '2026-06-30' },
    });
    expect(screen.getByRole('alert').textContent).toContain('必须按时间分离');
    expect(
      (screen.getByRole('button', { name: '冻结事实并启动生成' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('requires an explicit SOP project only while service 5 is selected', async () => {
    render(<FormalReportWorkspace session={session} project={project} />);
    await screen.findByText('当前项目还没有正式报告生产记录。');

    expect(screen.getByRole('alert').textContent).toContain('SOP 项目 ID');
    expect(screen.getByLabelText('服务 5 内容 SOP 项目 ID')).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/服务 5 · 发帖提排名/));
    expect(screen.queryByLabelText('服务 5 内容 SOP 项目 ID')).toBeNull();
    expect((screen.getByLabelText(/服务 2 · 找拉踩帖/) as HTMLInputElement).checked).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: '冻结事实并启动生成' }));
    await screen.findByText(/生产请求 frp_test_001/);
    const body = calls.find((call) => call.method === 'POST')?.body as Record<string, unknown>;
    expect(body.services).toEqual([1, 2, 3, 4]);
    expect(body).not.toHaveProperty('sop_project_pub_id');
  });

  it('requires and submits a human review record for a delivery candidate', async () => {
    render(<FormalReportWorkspace session={session} project={project} />);
    await screen.findByText('当前项目还没有正式报告生产记录。');
    fillService5SopProject();

    fireEvent.change(screen.getByLabelText('3. 文档状态'), {
      target: { value: 'delivery_candidate' },
    });
    expect(screen.getByRole('alert').textContent).toContain('必须先填写复核人和复核日期');
    fireEvent.change(screen.getByLabelText('复核人'), { target: { value: '复核员甲' } });
    fireEvent.change(screen.getByLabelText('复核日期（中国标准时间）'), {
      target: { value: '2026-08-14' },
    });
    fireEvent.click(screen.getByRole('button', { name: '冻结事实并启动生成' }));
    await screen.findByText(/生产请求 frp_test_001/);

    const body = calls.find((call) => call.method === 'POST')?.body as Record<string, unknown>;
    expect(body.document_status).toBe('delivery_candidate');
    expect(body.candidate_group_strategy).toBe('preregistered_scope_v1');
    expect(body.reviewed_by).toBe('复核员甲');
    expect(body.reviewed_date).toBe('2026-08-14');
  });

  it('shows auditable artifacts and rejects an unsafe server-provided download URL', async () => {
    listItems = [
      production({
        status: 'awaiting_review',
        fact_snapshot_hash: 'a'.repeat(64),
        outputs: [
          {
            service_number: 1,
            report_pub_id: 'rpt_1',
            report_version_pub_id: 'rptv_1',
            fact_snapshot_hash: 'd'.repeat(64),
            artifacts: [
              {
                format: 'docx',
                sha256: 'b'.repeat(64),
                byte_size: 2048,
                mime_type:
                  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                download_url: '/api/v2/reports/formal-productions/frp_test_001/artifacts/1/docx',
              },
            ],
          },
          {
            service_number: 2,
            report_pub_id: 'rpt_2',
            report_version_pub_id: 'rptv_2',
            fact_snapshot_hash: 'e'.repeat(64),
            artifacts: [
              {
                format: 'pdf',
                sha256: 'c'.repeat(64),
                byte_size: 4096,
                mime_type: 'application/pdf',
                download_url: 'https://evil.invalid/report.pdf',
              },
            ],
          },
        ],
      }),
    ];
    render(<FormalReportWorkspace session={session} project={project} />);

    await screen.findByText('待审阅');
    const link = screen.getByRole('link', { name: /服务 1 · 测试 DOCX/ });
    expect(link.getAttribute('href')).toContain(
      '/formal-productions/frp_test_001/artifacts/1/docx',
    );
    expect(screen.getByText(/服务 2 · 找拉踩帖 PDF（下载地址无效）/)).toBeTruthy();
    expect(screen.getByText('2.0 KB · bbbbbbbbbbbb…')).toBeTruthy();
    expect(screen.getByText('aaaaaaaaaaaaaaaa…')).toBeTruthy();
  });

  it('keeps historical service numbers labelled with the legacy catalog', async () => {
    listItems = [
      production({
        services: [2, 3, 4],
        service_catalog_version: 'legacy_report_services_v1',
      }),
    ];
    render(<FormalReportWorkspace session={session} project={project} />);

    await screen.findByText(/服务 2 · 内容生态风险核查/);
    expect(screen.getByText(/服务 3 · 官网引用能效评估/)).toBeTruthy();
    expect(screen.getByText(/服务 4 · GEO 试点与效果验证/)).toBeTruthy();
  });

  it('surfaces list failures and offers an explicit retry', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ error: { code: 'reporting_unavailable' } }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    render(<FormalReportWorkspace session={session} project={project} />);
    await waitFor(() => expect(screen.getByText(/生产记录加载失败/)).toBeTruthy());
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy();
  });

  it('lets a reviewer submit an idempotent decision with a rationale', async () => {
    listItems = [production({ status: 'awaiting_review', document_status: 'delivery_candidate' })];
    render(<FormalReportWorkspace session={{ ...session, role: 'reviewer' }} project={project} />);

    await screen.findByText('待审阅');
    expect(
      (screen.getByRole('button', { name: '当前角色仅可查看/审阅' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.change(screen.getByLabelText(/^审阅意见 /), {
      target: { value: '已核对冻结事实、证据与产物哈希。' },
    });
    fireEvent.click(screen.getByRole('button', { name: '批准签发' }));

    await screen.findByText(/Temporal 正在执行签发/);
    const review = calls.find(
      (call) => call.method === 'POST' && call.url.includes('/frp_test_001/review'),
    );
    expect(review?.idempotencyKey?.length).toBeGreaterThanOrEqual(16);
    expect(review?.body).toEqual({
      decision: 'approved',
      rationale: '已核对冻结事实、证据与产物哈希。',
    });
  });

  it('does not offer signing for an internal-review artifact', async () => {
    listItems = [production({ status: 'awaiting_review', document_status: 'internal_review' })];
    render(<FormalReportWorkspace session={{ ...session, role: 'reviewer' }} project={project} />);

    await screen.findByText('只有客户交付候选稿可提交人工批准；内部审核稿不可签发。');
    expect(screen.queryByRole('button', { name: '批准签发' })).toBeNull();
    expect(screen.getByRole('button', { name: '退回修改' })).toBeTruthy();
  });
});
