// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  completeProviderAccountLogin,
  createPostingBatch,
  listPostingBatches,
  listProviderAccounts,
  saveProviderAccount,
  startProviderAccountLogin,
  type PostingBatchSummary,
  type ProviderAccountStatus,
} from '@geo/api-client';
import { PostingWorkspace } from './PostingWorkspace';
import { createPostingHandoff, type ComparisonPostingSelection } from './selection-handoff';

vi.mock('@geo/api-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@geo/api-client')>();
  return {
    ...original,
    completeProviderAccountLogin: vi.fn(),
    createPostingBatch: vi.fn(),
    listPostingBatches: vi.fn(),
    listProviderAccounts: vi.fn(),
    saveProviderAccount: vi.fn(),
    startProviderAccountLogin: vi.fn(),
  };
});

const session = {
  tenantId: 'tnt_alpha',
  actorId: 'usr_operator',
  role: 'operator' as const,
  headers: {
    'X-Tenant-Id': 'tnt_alpha',
    'X-Actor-Id': 'operator-subject',
    'X-Actor-Role': 'operator',
  },
};

const selection: ComparisonPostingSelection = {
  key: 'news\u0000\u0000人民网',
  catalogType: 'news',
  catalogSha256: 'a'.repeat(64),
  mediaName: '人民网',
  mediaPlatform: '',
  provider: 'prfabu',
  options: { prfabu: { providerMediaId: 'pr_1001', quotedPrice: 88 } },
};

const providerNames = [
  ['prfabu', 'prfabu'],
  ['toumeiw', '投媒网'],
  ['mtpfw', '媒体批发网'],
  ['meititejia', '媒体特价网'],
  ['meijiehezi', '媒介盒子'],
  ['pinda', '品达发稿'],
] as const;

function accounts(prfabuStatus: ProviderAccountStatus['sessionStatus'] = 'not_configured') {
  return providerNames.map(
    ([provider, label]): ProviderAccountStatus => ({
      provider,
      label,
      configured: provider === 'prfabu' && prfabuStatus !== 'not_configured',
      accountMask: provider === 'prfabu' && prfabuStatus !== 'not_configured' ? 'su***nt' : '',
      sessionStatus:
        provider === 'meijiehezi'
          ? 'interactive_required'
          : provider === 'prfabu'
            ? prfabuStatus
            : 'not_configured',
      sessionMessage:
        provider === 'meijiehezi'
          ? '需要供应商交互验证码'
          : provider === 'prfabu' && prfabuStatus === 'ready'
            ? '会话有效，由系统自动维护'
            : '尚未配置账号凭据',
      loginMode: provider === 'meijiehezi' ? 'interactive' : 'image_captcha',
      postingSupported: provider === 'prfabu',
      balance: provider === 'prfabu' && prfabuStatus === 'ready' ? 128.5 : null,
      updatedAt: null,
    }),
  );
}

function batchSummary(index: number): PostingBatchSummary {
  return {
    pubId: `pbt_${index}`,
    sourceFilename: `article-${index}.docx`,
    sourceSha256: String(index).padStart(64, '0'),
    title: `发帖批次 ${index}`,
    imageCount: 1,
    customerName: '测试品牌',
    releaseTime: null,
    autoSubmit: false,
    spendConfirmedAt: null,
    maxTotalAmount: 88,
    quotedTotalAmount: 88,
    status: 'draft',
    note: '',
    sopProjectPubId: null,
    articleVersionPubId: null,
    approvalState: 'draft',
    approvalRequestedByPubId: null,
    approvedByPubId: null,
    approvedAt: null,
    createdAt: `2026-08-24T00:00:0${index}Z`,
    updatedAt: `2026-08-24T00:00:0${index}Z`,
    contentExcerpt: `正文 ${index}`,
    targetCount: index,
    submittedCount: 0,
    publishedCount: 0,
  };
}

function renderWorkspace() {
  const created = createPostingHandoff({
    tenantId: session.tenantId,
    actorId: session.actorId,
    selections: [selection],
  })!;
  return render(
    <MemoryRouter initialEntries={[created.href]}>
      <PostingWorkspace session={session} />
    </MemoryRouter>,
  );
}

describe('PostingWorkspace', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.mocked(listPostingBatches).mockResolvedValue({
      kind: 'ready',
      data: { data: [], nextCursor: null, hasMore: false, totalCount: 0 },
    });
    vi.mocked(listProviderAccounts).mockResolvedValue({ kind: 'ready', data: accounts() });
    vi.mocked(createPostingBatch).mockResolvedValue({ kind: 'unavailable' });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    window.sessionStorage.clear();
  });

  it('renders a separate posting page with exact target identity and Douyin placeholder', async () => {
    renderWorkspace();

    await screen.findByText('平台账号与自动登录');
    expect(screen.getByRole('tab', { name: /媒体发稿/u }).getAttribute('aria-selected')).toBe(
      'true',
    );
    expect(screen.getByRole('tab', { name: /抖音图文/u })).toHaveProperty('disabled', true);
    expect(screen.getByText(/媒体 ID pr_1001/u)).toBeTruthy();
    expect(screen.getByText('自动发帖配置')).toBeTruthy();
    expect(screen.getByRole('link', { name: '返回比价台修改目标' }).getAttribute('href')).toBe(
      '/platform/operations/media-prices',
    );
  });

  it('shows exactly four recent batches and navigates independent opaque cursor pages', async () => {
    vi.mocked(listPostingBatches).mockImplementation(async (_headers, input = {}) => {
      const page = input.cursor === 'cursor-2' ? 2 : input.cursor === 'cursor-3' ? 3 : 1;
      const rows = page === 1 ? [1, 2, 3, 4] : page === 2 ? [5, 6, 7, 8] : [9];
      return {
        kind: 'ready',
        data: {
          data: rows.map(batchSummary),
          nextCursor: page === 1 ? 'cursor-2' : page === 2 ? 'cursor-3' : null,
          hasMore: page < 3,
          totalCount: 9,
        },
      };
    });
    renderWorkspace();

    await screen.findByText('发帖批次 1');
    expect(screen.getByText('发帖批次 4')).toBeTruthy();
    expect(screen.queryByText('发帖批次 5')).toBeNull();
    const pager = screen.getByRole('navigation', { name: '最近发帖批次分页' });
    fireEvent.click(within(pager).getByRole('button', { name: '下一页' }));
    await screen.findByText('发帖批次 5');
    expect(screen.getByText('发帖批次 8')).toBeTruthy();
    expect(screen.queryByText('发帖批次 1')).toBeNull();
    fireEvent.click(within(pager).getByRole('button', { name: '下一页' }));
    await screen.findByText('发帖批次 9');
    expect(screen.queryByText('发帖批次 8')).toBeNull();
    expect(listPostingBatches).toHaveBeenLastCalledWith(session.headers, {
      cursor: 'cursor-3',
      limit: 4,
    });
  });

  it('saves credentials, clears the browser password, and completes captcha login in-page', async () => {
    const saved = accounts('needs_login')[0]!;
    const ready = accounts('ready')[0]!;
    vi.mocked(saveProviderAccount).mockResolvedValue({ kind: 'ready', data: saved });
    vi.mocked(startProviderAccountLogin).mockResolvedValue({
      kind: 'ready',
      data: {
        provider: 'prfabu',
        challengeId: 'A'.repeat(32),
        imageBase64: 'iVBORw0KGgo=',
        imageMimeType: 'image/png',
        expiresInSeconds: 300,
      },
    });
    vi.mocked(completeProviderAccountLogin).mockResolvedValue({ kind: 'ready', data: ready });
    renderWorkspace();

    await screen.findByText('平台账号与自动登录');
    const card = screen
      .getAllByText('prfabu')
      .map((item) => item.closest('article'))
      .find((item) => item?.classList.contains('posting-account-card'))!;
    fireEvent.click(within(card).getByRole('button', { name: '配置账号' }));
    fireEvent.change(within(card).getByLabelText('账号'), {
      target: { value: 'supplier-account' },
    });
    const password = within(card).getByLabelText('密码') as HTMLInputElement;
    fireEvent.change(password, { target: { value: 'supplier-password' } });
    fireEvent.click(within(card).getByRole('button', { name: '保存凭据并登录' }));

    expect(await within(card).findByAltText('prfabu 图形验证码')).toBeTruthy();
    expect(password.value).toBe('');
    expect(saveProviderAccount).toHaveBeenCalledWith(
      'prfabu',
      { account: 'supplier-account', password: 'supplier-password' },
      session.headers,
    );

    fireEvent.change(within(card).getByLabelText('图片验证码'), {
      target: { value: '4821' },
    });
    fireEvent.click(within(card).getByRole('button', { name: '验证并保存会话' }));
    expect((await screen.findAllByText('会话有效，由系统自动维护')).length).toBeGreaterThan(0);
    expect(completeProviderAccountLogin).toHaveBeenCalledWith(
      'prfabu',
      { challengeId: 'A'.repeat(32), captcha: '4821' },
      session.headers,
    );
  });

  it('submits the frozen snapshot and provider media ID to batch creation', async () => {
    vi.mocked(listProviderAccounts).mockResolvedValue({
      kind: 'ready',
      data: accounts('ready'),
    });
    renderWorkspace();
    await screen.findByText('会话有效，由系统自动维护');
    fireEvent.change(screen.getByLabelText('图文 DOCX'), {
      target: {
        files: [
          new File(['docx'], 'article.docx', {
            type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          }),
        ],
      },
    });
    fireEvent.click(screen.getByLabelText(/我确认按服务端最新报价扣费/u));
    fireEvent.click(screen.getByRole('button', { name: '确认预算并开始发帖' }));

    await waitFor(() => expect(createPostingBatch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createPostingBatch).mock.calls[0]?.[0].targets).toEqual([
      {
        catalogType: 'news',
        catalogSha256: 'a'.repeat(64),
        provider: 'prfabu',
        providerMediaId: 'pr_1001',
        mediaName: '人民网',
        mediaPlatform: '',
      },
    ]);
  });
});
