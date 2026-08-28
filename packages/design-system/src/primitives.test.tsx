// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  clearSafePdfCanvas,
  CursorPagination,
  Dialog,
  downloadSafeGeneratedFile,
  ExperienceProvider,
  type ExperienceLoadResult,
  identitySessionHintStorageKeys,
  logoutPlatformSession,
  MetricGrid,
  ModelSelect,
  type ModelSelectOption,
  Pagination,
  platformLoginHref,
  ProjectionLimitNotice,
  ProductShell,
  projectSafePdfPageViewport,
  projectSafeHtmlDocument,
  safePdfDocumentLimits,
  safePdfDocumentOptions,
  SafeHtmlDocument,
  StatePanel,
  Toast,
  ValidatedExperienceProvider,
  VerifiedBlobDownload,
  type VerifiedBlobDownloadResult,
  useExperienceContext,
} from './index';

const { logoutIdentitySessionMock } = vi.hoisted(() => ({
  logoutIdentitySessionMock: vi.fn(async () => true),
}));
vi.mock('@geo/api-client', () => ({ logoutIdentitySession: logoutIdentitySessionMock }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('shared experience primitives', () => {
  it('shares grouped model selection with default, capability and price semantics', () => {
    const onChange = vi.fn();
    render(
      <ModelSelect
        label="分析模型"
        ariaLabel="拉踩分析模型选择"
        value="fast-model"
        options={[
          {
            value: 'fast-model',
            label: 'Fast Model',
            group: 'provider-a',
            capability: '适合全量初筛',
            priceLabel: '输入 $0.20 / 输出 $1.20（每百万 tokens）',
            isDefault: true,
            recommended: true,
          },
          { value: 'deep-model', group: 'provider-b', recommended: true },
        ]}
        onChange={onChange}
      />,
    );

    const select = screen.getByRole('combobox', { name: '拉踩分析模型选择' });
    expect(within(select).getByRole('group', { name: 'provider-a' })).toBeTruthy();
    expect(screen.getByRole('option', { name: /Fast Model（默认/ })).toBeTruthy();
    expect(screen.getByText('适合全量初筛')).toBeTruthy();
    expect(
      screen.getByText(/输入 \$0\.20/, { selector: '.model-select-detail strong' }),
    ).toBeTruthy();
    fireEvent.change(select, { target: { value: 'deep-model' } });
    expect(onChange).toHaveBeenCalledWith('deep-model');
  });

  it('keeps two model-selector instances independent while sharing one option contract', () => {
    const options: ModelSelectOption[] = [
      { value: 'gpt-model', group: 'gpt' },
      { value: 'qwen-model', group: 'qwen' },
      { value: 'claude-model', group: 'claude' },
    ];
    const IndependentSelectors = () => {
      const [researchModel, setResearchModel] = useState('gpt-model');
      const [service2Model, setService2Model] = useState('qwen-model');
      return (
        <>
          <ModelSelect
            label="品牌调研"
            ariaLabel="品牌调研模型"
            value={researchModel}
            options={options.map((option) => ({ ...option }))}
            onChange={setResearchModel}
          />
          <ModelSelect
            label="Service 2"
            ariaLabel="Service 2 分析模型"
            value={service2Model}
            options={options.map((option) => ({ ...option }))}
            onChange={setService2Model}
          />
        </>
      );
    };
    render(<IndependentSelectors />);

    const research = screen.getByRole('combobox', { name: '品牌调研模型' });
    const service2 = screen.getByRole('combobox', { name: 'Service 2 分析模型' });
    fireEvent.change(research, { target: { value: 'claude-model' } });

    expect((research as HTMLSelectElement).value).toBe('claude-model');
    expect((service2 as HTMLSelectElement).value).toBe('qwen-model');
    fireEvent.change(service2, { target: { value: 'gpt-model' } });
    expect((research as HTMLSelectElement).value).toBe('claude-model');
    expect((service2 as HTMLSelectElement).value).toBe('gpt-model');
  });

  it('drops every Query cache and local state when the safe experience scope changes', async () => {
    const first = {
      tenantPubId: 'tnt_safe',
      tenantLabel: '安全租户',
      projectPubId: 'prj_safe',
      projectLabel: '安全项目',
      userPubId: 'usr_first',
      userLabel: '用户一',
      roles: ['reviewer'] as const,
      source: 'live' as const,
    };
    const second = { ...first, userPubId: 'usr_second', userLabel: '用户二' };
    const QueryProbe = () => {
      const context = useExperienceContext();
      const [localOwner] = useState(context.userPubId);
      const result = useQuery({
        queryKey: ['shared-identity-cache-canary'],
        queryFn: async () => context.userPubId,
        staleTime: Number.POSITIVE_INFINITY,
      });
      return <output>{`${localOwner}:${result.data ?? 'loading'}`}</output>;
    };
    const renderExperience = (value: typeof first) => (
      <ExperienceProvider value={value}>
        <QueryProbe />
      </ExperienceProvider>
    );
    const { rerender } = render(renderExperience(first));

    expect(await screen.findByText('usr_first:usr_first')).toBeTruthy();
    rerender(renderExperience(second));

    expect(await screen.findByText('usr_second:usr_second')).toBeTruthy();
    expect(screen.queryByText(/usr_first/)).toBeNull();
  });

  it('drops a failed error boundary when the safe experience scope changes', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const first = {
      tenantPubId: 'tnt_safe',
      tenantLabel: '安全租户',
      projectPubId: 'prj_safe',
      projectLabel: '安全项目',
      userPubId: 'usr_failed',
      userLabel: '失败用户',
      roles: ['reviewer'] as const,
      source: 'live' as const,
    };
    const second = { ...first, userPubId: 'usr_recovered', userLabel: '恢复用户' };
    const IdentityFailureProbe = () => {
      const context = useExperienceContext();
      if (context.userPubId === 'usr_failed') {
        throw new Error('identity-scoped render failure');
      }
      return <output>{context.userPubId}</output>;
    };
    const renderExperience = (value: typeof first) => (
      <ExperienceProvider value={value}>
        <IdentityFailureProbe />
      </ExperienceProvider>
    );
    const { rerender } = render(renderExperience(first));

    expect(screen.getByRole('alert').textContent).toContain('此页面暂时无法显示');
    rerender(renderExperience(second));

    expect(screen.getByText('usr_recovered')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('caches only the safe identity, tenant and project projection during bootstrap', async () => {
    const load = vi.fn(async () => ({
      kind: 'ready' as const,
      value: {
        tenantPubId: 'Cookie=session-bootstrap-canary',
        tenantLabel: '安全租户',
        projectPubId: 'prj_safe',
        projectLabel: '联系人 13800138000',
        userPubId: 'usr_safe',
        userLabel: 'Bearer bootstrap-canary',
        roles: ['customer'] as const,
        source: 'live' as const,
      },
    }));
    const ContextProbe = () => {
      const context = useExperienceContext();
      return <output>{JSON.stringify(context)}</output>;
    };

    render(
      <ValidatedExperienceProvider load={load} allowedRoles={['customer']}>
        <ContextProbe />
      </ValidatedExperienceProvider>,
    );

    await waitFor(() => expect(load).toHaveBeenCalledOnce());
    const output = await screen.findByText(/tnt_redacted/);
    expect(output.textContent).toContain('"projectPubId":"prj_safe"');
    expect(output.textContent).toContain('"projectLabel":"未命名项目"');
    expect(output.textContent).toContain('"userLabel":"用户已隐藏"');
    expect(output.textContent).not.toMatch(/Cookie|Bearer|13800138000|bootstrap-canary/i);
  });

  it('starts a new bootstrap generation when the loader changes and discards the older response', async () => {
    let resolveFirst!: (value: ExperienceLoadResult) => void;
    const firstLoad = vi.fn(
      () =>
        new Promise<ExperienceLoadResult>((resolve) => {
          resolveFirst = resolve;
        }),
    );
    const secondLoad = vi.fn(
      async (): Promise<ExperienceLoadResult> => ({
        kind: 'ready',
        value: {
          tenantPubId: 'tnt_safe',
          tenantLabel: '安全租户',
          projectPubId: 'prj_safe',
          projectLabel: '安全项目',
          userPubId: 'usr_second_bootstrap',
          userLabel: '当前用户',
          roles: ['customer'],
          source: 'live',
        },
      }),
    );
    const ContextProbe = () => <output>{useExperienceContext().userPubId}</output>;
    const renderBootstrap = (load: () => Promise<ExperienceLoadResult>) => (
      <ValidatedExperienceProvider load={load} allowedRoles={['customer']}>
        <ContextProbe />
      </ValidatedExperienceProvider>
    );
    const { rerender } = render(renderBootstrap(firstLoad));

    await waitFor(() => expect(firstLoad).toHaveBeenCalledOnce());
    rerender(renderBootstrap(secondLoad));

    expect(await screen.findByText('usr_second_bootstrap')).toBeTruthy();
    expect(secondLoad).toHaveBeenCalledOnce();
    resolveFirst({
      kind: 'ready',
      value: {
        tenantPubId: 'tnt_safe',
        tenantLabel: '安全租户',
        projectPubId: 'prj_safe',
        projectLabel: '安全项目',
        userPubId: 'usr_stale_bootstrap',
        userLabel: '过期用户',
        roles: ['customer'],
        source: 'live',
      },
    });

    await waitFor(() => expect(screen.queryByText('usr_stale_bootstrap')).toBeNull());
    expect(screen.getByText('usr_second_bootstrap')).toBeTruthy();
  });

  it('redacts bootstrap failures before diagnostics and exposes only the shared retry state', async () => {
    const onDiagnostic = vi.fn();
    render(
      <ValidatedExperienceProvider
        load={async () => {
          throw new Error('Bearer bootstrap-error-canary OTP 824911');
        }}
        allowedRoles={['customer']}
        onDiagnostic={onDiagnostic}
      >
        <span>不可显示的业务内容</span>
      </ValidatedExperienceProvider>,
    );

    expect((await screen.findByRole('alert')).textContent).toContain('加载失败');
    expect(screen.queryByText('不可显示的业务内容')).toBeNull();
    expect(JSON.stringify(onDiagnostic.mock.calls)).not.toMatch(
      /Bearer|bootstrap-error-canary|824911/i,
    );
    expect(onDiagnostic).toHaveBeenCalledWith({
      kind: 'experience_bootstrap_error',
      errorName: 'Error',
      componentFrames: 0,
      hasCause: false,
    });
  });

  it('retries an unavailable bootstrap locally without reloading the document', async () => {
    const load = vi
      .fn()
      .mockResolvedValueOnce({ kind: 'unavailable' as const })
      .mockResolvedValueOnce({
        kind: 'ready' as const,
        value: {
          tenantPubId: 'tnt_retry_safe',
          tenantLabel: '安全租户',
          projectPubId: 'prj_retry_safe',
          projectLabel: '局部恢复项目',
          userPubId: 'usr_retry_safe',
          userLabel: '安全用户',
          roles: ['customer'] as const,
          source: 'live' as const,
        },
      });

    render(
      <ValidatedExperienceProvider load={load} allowedRoles={['customer']}>
        <span>局部恢复成功</span>
      </ValidatedExperienceProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '重试此区域' }));
    expect(await screen.findByText('局部恢复成功')).toBeTruthy();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('renders an explicitly public child when no validated experience is available', async () => {
    const load = vi.fn(async () => ({ kind: 'forbidden' as const }));

    render(
      <ValidatedExperienceProvider load={load} allowedRoles={['operator']} allowAnonymous>
        <span>公开比价内容</span>
      </ValidatedExperienceProvider>,
    );

    expect(await screen.findByText('公开比价内容')).toBeTruthy();
    expect(screen.queryByText('无权查看')).toBeNull();
  });

  it('keeps every non-ready data state semantically distinct', () => {
    const states = [
      ['loading', '正在加载', 'status'],
      ['empty', '暂无数据', 'status'],
      ['real-zero', '结果为 0', 'status'],
      ['insufficient', '样本不足', 'status'],
      ['failed', '加载失败', 'alert'],
      ['delayed', '数据延迟', 'status'],
      ['forbidden', '无权查看', 'status'],
    ] as const;

    const { rerender } = render(<StatePanel state="loading" />);
    for (const [state, title, role] of states) {
      rerender(<StatePanel state={state} />);
      expect(screen.getByRole(role).textContent).toContain(title);
    }
  });

  it('labels a completed zero independently from missing and insufficient metrics', () => {
    render(
      <MetricGrid
        metrics={[
          {
            label: '真实零指标',
            value: '0.0%',
            detail: '0 / 4 · 已完成',
            state: 'real-zero',
          },
          {
            label: '不足指标',
            value: '—',
            detail: '— / 0 · 样本不足',
            state: 'insufficient',
          },
          {
            label: '正常指标',
            value: '75.0%',
            detail: '3 / 4 · 已完成',
            state: 'ready',
          },
        ]}
      />,
    );

    expect(screen.getByText('真实 0')).toBeTruthy();
    expect(screen.getAllByText('样本不足')).toHaveLength(1);
    expect(screen.queryByText('暂无数据')).toBeNull();
    expect(screen.getByText('正常指标').closest('article')?.textContent).not.toContain('真实 0');
  });

  it('clamps pagination and never emits an out-of-range page', () => {
    const onPageChange = vi.fn();
    const { rerender } = render(
      <Pagination page={-8} pageCount={3} onPageChange={onPageChange} label="回答分页" />,
    );
    expect(screen.getByText('第 1 / 3 页')).toBeTruthy();
    expect((screen.getByRole('button', { name: '上一页' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(onPageChange).toHaveBeenLastCalledWith(2);

    rerender(<Pagination page={99} pageCount={0} onPageChange={onPageChange} label="回答分页" />);
    expect(screen.getByText('第 1 / 1 页')).toBeTruthy();
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('renders cursor pagination without inventing a total page count', () => {
    const onPrevious = vi.fn();
    const onNext = vi.fn();
    render(
      <CursorPagination
        page={3}
        hasPrevious
        hasNext={false}
        onPrevious={onPrevious}
        onNext={onNext}
        label="运行分页"
      />,
    );

    expect(screen.getByRole('navigation', { name: '运行分页' })).toBeTruthy();
    expect(screen.getByText('第 3 页')).toBeTruthy();
    expect(screen.queryByText(/共/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '上一页' }));
    expect(onPrevious).toHaveBeenCalledOnce();
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('renders a bounded numbered window, total count and direct page jump', () => {
    const onPageChange = vi.fn();
    render(
      <Pagination
        page={17}
        pageCount={34}
        totalItems={136}
        onPageChange={onPageChange}
        label="采样进度分页"
      />,
    );

    expect(screen.getByText(/共 136 条/)).toBeTruthy();
    expect(screen.getByText('第 17 / 34 页')).toBeTruthy();
    for (const page of [1, 16, 17, 18, 34]) {
      expect(screen.getByRole('button', { name: `第 ${page} 页` })).toBeTruthy();
    }
    expect(screen.queryByRole('button', { name: '第 15 页' })).toBeNull();

    fireEvent.change(screen.getByRole('spinbutton', { name: '跳转页码' }), {
      target: { value: '34' },
    });
    fireEvent.click(screen.getByRole('button', { name: '跳转' }));
    expect(onPageChange).toHaveBeenLastCalledWith(34);
  });

  it('discloses bounded browser projections without claiming the collection is complete', () => {
    render(
      <ProjectionLimitNotice
        items={[{ key: 'evidence', label: '证据关系', total: 500, shown: 120 }]}
      />,
    );
    const status = screen.getByRole('status');
    expect(status.textContent).toContain('证据关系：服务返回 500 条，浏览器安全视图展示 120 条');
    expect(status.textContent).toContain('当前视图不会静默声称数据完整');
  });

  it('reconstructs a rich static HTML report without carrying raw markup or active attributes', () => {
    const projection = projectSafeHtmlDocument(`
      <!doctype html>
      <title>季度报告</title>
      <main class="remote-layout" data-layout="ignored">
        <h1>执行摘要</h1>
        <p>结论已由 <strong>两项独立证据</strong> 支持。</p>
        <ul><li>来源一</li><li>来源二</li></ul>
        <table>
          <caption>关键指标</caption>
          <thead><tr><th scope="col">指标</th><th scope="col">值</th></tr></thead>
          <tbody><tr><th scope="row">提及率</th><td>68.4%</td></tr></tbody>
        </table>
        <p><a href="https://source.example/report">查看公开来源</a></p>
      </main>
    `);
    expect(projection).not.toBeNull();
    expect(JSON.stringify(projection)).not.toMatch(/remote-layout|tracker|style|class/i);

    render(<SafeHtmlDocument projection={projection!} label="安全在线报告" />);

    const article = screen.getByRole('article', { name: '安全在线报告' });
    expect(article.textContent).toContain('HTML 完整性与活动内容已校验');
    expect(screen.getByRole('heading', { name: '季度报告', level: 3 })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '执行摘要', level: 4 })).toBeTruthy();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByRole('table', { name: '关键指标' })).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: '指标' }).getAttribute('scope')).toBe('col');
    expect(screen.getByRole('rowheader', { name: '提及率' }).getAttribute('scope')).toBe('row');
    const link = screen.getByRole('link', { name: '查看公开来源' });
    expect(link.getAttribute('href')).toBe('https://source.example/report');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('rejects active, externally loading, event-bearing or secret-bearing HTML documents', () => {
    const parser = vi.spyOn(DOMParser.prototype, 'parseFromString');
    const activeDocuments = [
      '<title>报告</title><main><script>window.evil=true</script></main>',
      '<title>报告</title><main><p onclick="window.evil=true">正文</p></main>',
      '<title>报告</title><main><img src="https://tracker.invalid/pixel" alt="跟踪像素"></main>',
      '<title>报告</title><main><iframe srcdoc="<p>跟踪</p>"></iframe></main>',
      '<title>报告</title><main><svg><a xlink:href="https://tracker.invalid">跟踪</a></svg></main>',
      '<title>报告</title><main><p style="background:url(https://tracker.invalid)">正文</p></main>',
      '<title>报告</title><main><form><input name="code"></form></main>',
    ];
    for (const html of activeDocuments) expect(projectSafeHtmlDocument(html)).toBeNull();
    expect(parser).not.toHaveBeenCalled();

    expect(
      projectSafeHtmlDocument(
        '<title>报告</title><main><a href="javascript:alert(1)">危险链接</a></main>',
      ),
    ).toBeNull();
    expect(
      projectSafeHtmlDocument(
        '<title>报告</title><main><p>&#49;&#51;&#56;&#48;&#48;&#49;&#51;&#56;&#48;&#48;&#48;</p></main>',
      ),
    ).toBeNull();
  });

  it('bounds PDF.js pages, canvas allocation and built-in resource loading', () => {
    expect(
      projectSafePdfPageViewport({
        totalPages: 2,
        pageNumber: 1,
        width: 684.25,
        height: 968.5,
      }),
    ).toEqual({ canvasWidth: 685, canvasHeight: 969 });
    for (const candidate of [
      { totalPages: 0, pageNumber: 1, width: 600, height: 800 },
      { totalPages: safePdfDocumentLimits.pageCount + 1, pageNumber: 1, width: 600, height: 800 },
      { totalPages: 2, pageNumber: 3, width: 600, height: 800 },
      { totalPages: 2, pageNumber: 1, width: Number.NaN, height: 800 },
      {
        totalPages: 2,
        pageNumber: 1,
        width: safePdfDocumentLimits.canvasDimension + 1,
        height: 800,
      },
      { totalPages: 2, pageNumber: 1, width: 3_000, height: 3_000 },
    ]) {
      expect(projectSafePdfPageViewport(candidate)).toBeNull();
    }
    expect(safePdfDocumentOptions).toMatchObject({
      disableAutoFetch: true,
      disableFontFace: true,
      disableStream: true,
      enableXfa: false,
      maxImageSize: safePdfDocumentLimits.imagePixels,
      stopAtErrors: true,
      useSystemFonts: false,
      useWorkerFetch: false,
    });
    const canvas = { width: 685, height: 969 };
    clearSafePdfCanvas(canvas);
    expect(canvas).toEqual({ width: 0, height: 0 });
  });

  it('downloads only a verified non-empty Blob with a DLP-safe filename', async () => {
    const createObjectURL = vi.fn(() => 'blob:verified-report');
    const revokeObjectURL = vi.fn();
    const OriginalUrl = globalThis.URL;
    class DownloadUrl extends OriginalUrl {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    }
    vi.stubGlobal('URL', DownloadUrl);
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const load = vi.fn(async () => ({
      kind: 'ready' as const,
      blob: new Blob(['verified'], { type: 'application/pdf' }),
    }));

    render(
      <VerifiedBlobDownload load={load} fileName="报价单-盛邦安全-20260812.pdf" label="下载 PDF" />,
    );
    fireEvent.click(screen.getByRole('button', { name: '下载 PDF' }));

    expect((await screen.findByRole('status')).textContent).toBe('制品完整性校验通过并已下载');
    expect(load).toHaveBeenCalledOnce();
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(anchorClick).toHaveBeenCalledOnce();
  });

  it('serializes synchronous download activation and discards a prior resource result', async () => {
    const createObjectURL = vi.fn(() => 'blob:current-report');
    const OriginalUrl = globalThis.URL;
    class DownloadUrl extends OriginalUrl {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = vi.fn();
    }
    vi.stubGlobal('URL', DownloadUrl);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    let resolveFirst!: (value: VerifiedBlobDownloadResult) => void;
    let resolveSecond!: (value: VerifiedBlobDownloadResult) => void;
    const firstLoad = vi.fn(
      () =>
        new Promise<VerifiedBlobDownloadResult>((resolve) => {
          resolveFirst = resolve;
        }),
    );
    const secondLoad = vi.fn(
      () =>
        new Promise<VerifiedBlobDownloadResult>((resolve) => {
          resolveSecond = resolve;
        }),
    );
    const { rerender } = render(
      <VerifiedBlobDownload
        load={firstLoad}
        fileName="rpt_first-rpv_first.pdf"
        resourceKey="report-first-sha-safe"
        label="下载切换制品"
      />,
    );
    const activateTwice = () => {
      const button = screen.getByRole('button', { name: '下载切换制品' });
      button.click();
      button.click();
    };

    act(activateTwice);
    expect(firstLoad).toHaveBeenCalledOnce();
    rerender(
      <VerifiedBlobDownload
        load={secondLoad}
        fileName="rpt_second-rpv_second.pdf"
        resourceKey="report-second-sha-safe"
        label="下载切换制品"
      />,
    );
    await act(async () => {
      resolveFirst({
        kind: 'ready',
        blob: new Blob(['stale'], { type: 'application/pdf' }),
      });
      await Promise.resolve();
    });
    expect(createObjectURL).not.toHaveBeenCalled();

    act(activateTwice);
    expect(secondLoad).toHaveBeenCalledOnce();
    await act(async () => {
      resolveSecond({
        kind: 'ready',
        blob: new Blob(['current'], { type: 'application/pdf' }),
      });
      await Promise.resolve();
    });
    expect((await screen.findByRole('status')).textContent).toBe('制品完整性校验通过并已下载');
    expect(createObjectURL).toHaveBeenCalledOnce();
  });

  it('never creates a download URL for invalid results or secret-shaped filenames', async () => {
    const createObjectURL = vi.fn(() => 'blob:must-not-exist');
    const OriginalUrl = globalThis.URL;
    class DownloadUrl extends OriginalUrl {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = vi.fn();
    }
    vi.stubGlobal('URL', DownloadUrl);
    const load = vi.fn(async () => ({
      kind: 'ready' as const,
      blob: new Blob([], { type: 'application/pdf' }),
    }));
    const { rerender } = render(
      <VerifiedBlobDownload load={load} fileName="rpt_safe.pdf" label="下载空制品" />,
    );
    fireEvent.click(screen.getByRole('button', { name: '下载空制品' }));
    expect((await screen.findByRole('alert')).textContent).toBe('制品完整性校验失败');
    expect(createObjectURL).not.toHaveBeenCalled();

    rerender(
      <VerifiedBlobDownload
        load={load}
        fileName="Bearer report-download-canary.pdf"
        label="下载危险名称"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '下载危险名称' }));
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(load).toHaveBeenCalledOnce();
  });

  it('downloads bounded browser-generated JSON and CSV through the shared DLP boundary', () => {
    vi.useFakeTimers();
    const createObjectURL = vi.fn(() => 'blob:safe-generated-file');
    const revokeObjectURL = vi.fn();
    const OriginalUrl = globalThis.URL;
    class DownloadUrl extends OriginalUrl {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    }
    vi.stubGlobal('URL', DownloadUrl);
    const downloadedNames: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      downloadedNames.push(this.download);
    });

    expect(
      downloadSafeGeneratedFile({
        kind: 'json',
        fileName: 'case-safe-manifest.json',
        value: { case_id: 'CASE-safe', evidence_count: 4, public: false },
      }),
    ).toBe(true);
    expect(
      downloadSafeGeneratedFile({
        kind: 'csv',
        fileName: 'safe-metrics.csv',
        content: 'metric,value\nmention_rate,0.684\n',
      }),
    ).toBe(true);

    expect(downloadedNames).toEqual(['case-safe-manifest.json', 'safe-metrics.csv']);
    expect(createObjectURL).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(1_000);
    expect(revokeObjectURL).toHaveBeenCalledTimes(2);
  });

  it('creates no generated download for secret keys or values, numeric secrets or CSV formulas', () => {
    const createObjectURL = vi.fn(() => 'blob:must-not-exist');
    const OriginalUrl = globalThis.URL;
    class DownloadUrl extends OriginalUrl {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = vi.fn();
    }
    vi.stubGlobal('URL', DownloadUrl);
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    expect(
      downloadSafeGeneratedFile({
        kind: 'json',
        fileName: 'secret-key.json',
        value: { token: 'opaque-looking-value' },
      }),
    ).toBe(false);
    expect(
      downloadSafeGeneratedFile({
        kind: 'json',
        fileName: 'normalized-secret-key.json',
        value: {
          ｔｏｋｅｎ: 'fullwidth-key-canary',
          'co\u200bokie': 'zero-width-key-canary',
          'profile%255Fpath': 'encoded-key-canary',
        },
      }),
    ).toBe(false);
    expect(
      downloadSafeGeneratedFile({
        kind: 'json',
        fileName: 'secret-value.json',
        value: { note: 'Bearer generated-file-canary' },
      }),
    ).toBe(false);
    expect(
      downloadSafeGeneratedFile({
        kind: 'json',
        fileName: 'numeric-secret.json',
        value: { challenge: 824911 },
      }),
    ).toBe(false);
    expect(
      downloadSafeGeneratedFile({
        kind: 'csv',
        fileName: 'formula.csv',
        content: 'metric,value\nunsafe,=HYPERLINK("https://example.invalid")\n',
      }),
    ).toBe(false);
    expect(
      downloadSafeGeneratedFile({
        kind: 'csv',
        fileName: 'normalized-secret-header.csv',
        content: 'metric,ａｃｃｅｓｓ＿ｔｏｋｅｎ\nsafe,opaque-csv-key-canary\n',
      }),
    ).toBe(false);
    expect(
      downloadSafeGeneratedFile({
        kind: 'json',
        fileName: 'oversized.json',
        value: { rows: Array.from({ length: 300 }, () => 'x'.repeat(10_000)) },
      }),
    ).toBe(false);
    expect(
      downloadSafeGeneratedFile({
        kind: 'csv',
        fileName: 'Bearer unsafe.csv',
        content: 'metric,value\nsafe,1\n',
      }),
    ).toBe(false);

    expect(createObjectURL).not.toHaveBeenCalled();
    expect(anchorClick).not.toHaveBeenCalled();
  });

  it('does not invent notification facts for a validated live experience', async () => {
    render(
      <ExperienceProvider
        value={{
          tenantPubId: 'tnt_safe',
          tenantLabel: '安全租户',
          projectPubId: 'prj_safe',
          projectLabel: '安全项目',
          userPubId: 'usr_safe',
          userLabel: '安全用户',
          roles: ['customer'],
          source: 'live',
        }}
      >
        <ProductShell
          product="Customer Web"
          title="客户工作台"
          description="安全工作区"
          nav={[{ id: 'home', label: '首页' }]}
          probe={async () => ({ status: 'ok' })}
        >
          {() => <section>安全业务内容</section>}
        </ProductShell>
      </ExperienceProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '通知' }));
    const dialog = screen.getByRole('dialog', { name: '通知中心' });
    expect(dialog.textContent).toContain('当前安全投影未提供通知集合');
    expect(dialog.textContent).not.toContain('数据窗口已冻结');
    expect(dialog.textContent).not.toContain('有一项待人工确认');
  });

  it('lists every authorized project and preserves the active project across navigation', () => {
    render(
      <ExperienceProvider
        value={{
          tenantPubId: 'tnt_safe',
          tenantLabel: '安全租户',
          projectPubId: 'prj_security',
          projectLabel: '盛邦安全-GEO验证',
          userPubId: 'usr_safe',
          userLabel: '安全用户',
          roles: ['customer'],
          projects: [
            {
              projectPubId: 'prj_security',
              projectLabel: '盛邦安全-GEO验证',
              state: 'draft',
            },
            {
              projectPubId: 'prj_testdeep',
              projectLabel: 'testdeep',
              state: 'paused',
            },
          ],
          source: 'live',
        }}
      >
        <ProductShell
          product="Customer Web"
          title="客户工作台"
          description="安全工作区"
          nav={[
            { id: 'home', label: '首页' },
            {
              id: 'reports',
              label: '报告',
              href: '/platform/customer/reports#queue',
              projectAware: true,
            },
            { id: 'generic', label: '通用入口', href: '/platform/customer/help' },
          ]}
          probe={async () => ({ status: 'ok' })}
        >
          {() => <section>安全业务内容</section>}
        </ProductShell>
      </ExperienceProvider>,
    );

    expect(screen.getByRole('link', { name: '报告' }).getAttribute('href')).toBe(
      '/platform/customer/reports?project=prj_security#queue',
    );
    expect(screen.getByRole('link', { name: '通用入口' }).getAttribute('href')).toBe(
      '/platform/customer/help',
    );
    fireEvent.click(screen.getByRole('button', { name: /安全租户.*盛邦安全/u }));
    const dialog = screen.getByRole('dialog', { name: '当前项目上下文' });
    expect(dialog.textContent).toContain('盛邦安全-GEO验证');
    expect(dialog.textContent).toContain('testdeep');
    expect(screen.getByRole('link', { name: /testdeep/u }).getAttribute('href')).toBe(
      '?project=prj_testdeep',
    );
  });

  it('projects health status again in the shared shell and ignores superseded probes', async () => {
    let resolveFirst: ((value: { status: string }) => void) | undefined;
    const firstProbe = vi.fn(
      () =>
        new Promise<{ status: string }>((resolve) => {
          resolveFirst = resolve;
        }),
    );
    const secondProbe = vi.fn(async () => ({ status: 'ok' }));
    const renderShell = (probe: () => Promise<{ status: string }>) => (
      <ProductShell
        product="Customer Web"
        title="客户工作台"
        description="安全工作区"
        nav={[{ id: 'home', label: '首页' }]}
        probe={probe}
      >
        {() => <section>安全业务内容</section>}
      </ProductShell>
    );
    const { rerender } = render(renderShell(firstProbe));

    const healthStatus = screen.getByRole('status');
    expect(healthStatus.textContent).toContain('checking');
    rerender(renderShell(secondProbe));
    await waitFor(() => expect(healthStatus.textContent).toContain('ok'));

    resolveFirst?.({ status: 'Bearer health-probe-canary OTP 824911' });
    await Promise.resolve();
    expect(healthStatus.textContent).toContain('ok');
    expect(document.body.textContent).not.toMatch(/Bearer|health-probe-canary|824911/i);

    rerender(renderShell(async () => ({ status: 'Cookie=session-health-probe-canary' })));
    await waitFor(() => expect(healthStatus.textContent).toContain('unavailable'));
    expect(document.body.textContent).not.toMatch(/Cookie|session-health-probe-canary/i);
  });

  it('renders safe navigation groups once and rejects items with unsafe group labels', () => {
    render(
      <ProductShell
        product="Customer Web"
        title="客户工作台"
        description="分组导航"
        nav={[
          { id: 'home', label: '经营总览', group: '数据洞察' },
          { id: 'answers', label: '真实 AI 回答', group: '数据洞察' },
          { id: 'reports', label: '报告', group: '成果交付' },
          {
            id: 'unsafe-group',
            label: '不安全入口',
            group: 'Bearer navigation-group-canary',
          },
        ]}
        probe={async () => ({ status: 'ok' })}
      >
        {() => <section>安全业务内容</section>}
      </ProductShell>,
    );

    expect(screen.getAllByRole('heading', { name: '数据洞察' })).toHaveLength(1);
    expect(screen.getAllByRole('heading', { name: '成果交付' })).toHaveLength(1);
    expect(screen.getByRole('button', { name: '真实 AI 回答' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '不安全入口' })).toBeNull();
    expect(document.body.textContent).not.toContain('navigation-group-canary');
  });

  it('keeps internal href navigation outside section state and renders unsafe destinations disabled', () => {
    window.history.replaceState(null, '', '/platform/operations/?section=execution');
    render(
      <ProductShell
        product="Operations Web"
        title="运行总览"
        description="安全导航"
        nav={[
          { id: 'overview', label: '总览' },
          {
            id: 'execution',
            label: '执行任务',
            href: '/platform/operations/execution',
          },
          {
            id: 'sessions',
            label: '会话健康',
            href: '/platform/operations/?section=sessions',
          },
          {
            id: 'unsafe-link',
            label: '危险地址',
            href: '/platform/operations/execution?access_token=token-nav-canary',
          },
          { id: 'secret-label', label: 'Bearer navigation-label-canary' },
        ]}
        probe={async () => ({ status: 'ok' })}
      >
        {(active) => <section>{active}</section>}
      </ProductShell>,
    );

    expect(screen.getByRole('button', { name: '总览' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('link', { name: '执行任务' }).getAttribute('href')).toBe(
      '/platform/operations/execution',
    );
    expect(screen.getByRole('link', { name: '会话健康' }).getAttribute('href')).toBe(
      '/platform/operations/?section=sessions',
    );
    expect(screen.getByRole('button', { name: '危险地址' }).hasAttribute('disabled')).toBe(true);
    expect(screen.queryByText(/navigation-label-canary/)).toBeNull();
    expect(window.location.search).toBe('');
    expect(document.body.textContent).not.toMatch(/access_token|token-nav-canary|Bearer/);
  });

  it('allows safe hidden compatibility sections without rendering extra navigation controls', () => {
    window.history.replaceState(null, '', '/platform/customer/?section=monitoring');
    render(
      <ProductShell
        product="Customer Web"
        title="客户工作台"
        description="安全导航"
        nav={[{ id: 'home', label: '经营总览' }]}
        additionalSectionIds={['monitoring', 'Bearer hidden-section-canary']}
        probe={async () => ({ status: 'ok' })}
      >
        {(active) => <section>{active}</section>}
      </ProductShell>,
    );

    expect(screen.getByText('monitoring')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'monitoring' })).toBeNull();
    expect(document.body.textContent).not.toContain('hidden-section-canary');
  });

  it('marks the current standalone workspace link without changing section state', () => {
    render(
      <ProductShell
        product="Operations Web"
        title="媒体比价台"
        description="安全独立工作区"
        nav={[
          { id: 'overview', label: '总览', href: '/platform/operations/' },
          {
            id: 'media-prices',
            label: '媒体比价台',
            href: '/platform/operations/media-prices',
          },
        ]}
        currentNavId="media-prices"
        probe={async () => ({ status: 'ok' })}
      >
        {() => <section>媒体价格内容</section>}
      </ProductShell>,
    );

    expect(screen.getByRole('link', { name: '媒体比价台' }).getAttribute('aria-current')).toBe(
      'page',
    );
    expect(screen.getByRole('link', { name: '总览' }).hasAttribute('aria-current')).toBe(false);
  });

  it('closes dialogs with Escape and distinguishes assertive failure toasts', () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <Dialog title="授权确认" closeLabel="关闭授权确认" onClose={onClose}>
        <button>确认授权</button>
      </Dialog>,
    );
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();

    rerender(<Toast tone="negative">保存失败</Toast>);
    expect(screen.getByRole('alert').textContent).toBe('保存失败');
    rerender(<Toast>保存成功</Toast>);
    expect(screen.getByRole('status').textContent).toBe('保存成功');
  });
});

describe('platform session lifecycle UX', () => {
  it('offers a re-login entry on the forbidden state panel', () => {
    render(<StatePanel state="forbidden" />);

    const link = screen.getByRole('link', { name: '重新登录' });
    expect(link.getAttribute('href')).toBe(platformLoginHref);
    expect(platformLoginHref).toBe('/platform/operations/login');
  });

  it('keeps other data states free of the re-login entry', () => {
    render(<StatePanel state="failed" onRetry={() => undefined} />);

    expect(screen.queryByRole('link', { name: '重新登录' })).toBeNull();
    expect(screen.getByRole('button', { name: '重试此区域' })).toBeTruthy();
  });

  it('revokes the session, clears identity hints only and navigates to the login entry', async () => {
    const order: string[] = [];
    const revoke = vi.fn(async () => {
      order.push('revoke');
      return true;
    });
    const navigate = vi.fn((href: string) => {
      order.push(`navigate:${href}`);
    });
    window.localStorage.setItem('geo.session.tenant', 'tnt_hint');
    window.localStorage.setItem('geo.session.actor', 'usr_hint');
    window.localStorage.setItem('geo.session.role', 'customer');
    window.localStorage.setItem('geo.preference.panel', 'kept');

    await logoutPlatformSession(revoke, navigate);

    expect(revoke).toHaveBeenCalledOnce();
    for (const key of identitySessionHintStorageKeys) {
      expect(window.localStorage.getItem(key)).toBeNull();
    }
    expect(window.localStorage.getItem('geo.preference.panel')).toBe('kept');
    expect(order).toEqual(['revoke', `navigate:${platformLoginHref}`]);
  });

  it('still clears identity hints and navigates when revocation fails', async () => {
    const navigate = vi.fn();
    window.localStorage.setItem('geo.session.actor', 'usr_hint');

    await logoutPlatformSession(async () => {
      throw new Error('network down');
    }, navigate);

    expect(window.localStorage.getItem('geo.session.actor')).toBeNull();
    expect(navigate).toHaveBeenCalledWith(platformLoginHref);
  });

  it('signs out from the shared shell topbar through the audited logout boundary', async () => {
    logoutIdentitySessionMock.mockClear();
    logoutIdentitySessionMock.mockResolvedValue(true);
    const navigate = vi.fn();
    window.localStorage.setItem('geo.session.role', 'admin');
    render(
      <ProductShell
        product="Customer Web"
        title="客户工作台"
        description="安全工作区"
        nav={[{ id: 'home', label: '首页' }]}
        probe={async () => ({ status: 'ok' })}
        logout={() => logoutPlatformSession(undefined, navigate)}
      >
        {() => <section>安全业务内容</section>}
      </ProductShell>,
    );

    const button = screen.getByRole('button', { name: '退出登录' });
    fireEvent.click(button);

    await waitFor(() => expect(logoutIdentitySessionMock).toHaveBeenCalledOnce());
    await waitFor(() => expect(window.localStorage.getItem('geo.session.role')).toBeNull());
    expect(navigate).toHaveBeenCalledWith(platformLoginHref);
  });
});
