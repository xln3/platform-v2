// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  CustomerAnswerLoadError,
  CustomerAnswerExplorer,
  customerAnswerPaginationItems,
  type CustomerAnswerDetail,
  type CustomerAnswerLibraryAnswer,
  type CustomerAnswerLibraryMetaDetail,
  type CustomerAnswerLibraryPage,
  type CustomerAnswerLibraryRuns,
} from './customer-answer-explorer';

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: vi.fn(() => 'blob:verified-customer-share-image'),
  });
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: vi.fn(),
  });
});

const snapshotId = `als_${'1'.repeat(24)}`;
const metaQueryId = `amq_${'2'.repeat(24)}`;
const questionIds = [1, 2, 3, 4].map((value) => `aq_${String(value).repeat(24)}`);
const dimensions = [
  { label: 'DeepSeek', answer_count: 2 },
  { label: '豆包', answer_count: 1 },
];
const questionChoices = questionIds.map((questionId, index) => ({
  question_id: questionId,
  ordinal: index + 1,
  variant_label: ['原问题', '变体 A', '变体 B', '变体 C'][index]!,
  text: `第${index + 1}条具体问题`,
  answer_count: index === 0 ? 2 : 1,
}));

const libraryPage: CustomerAnswerLibraryPage = {
  schema_version: 'customer-answer-library-v1',
  project_pub_id: 'prj_answer_library_test',
  snapshot_id: snapshotId,
  snapshot_at: '2026-08-17T08:00:00Z',
  totals: {
    meta_query_count: 34,
    question_count: 136,
    answer_count: 1237,
    cited_answer_count: 988,
    citation_count: 3918,
    mentioned_answer_count: 842,
    unmapped_answer_count: 0,
  },
  models: dimensions,
  regions: [{ label: '华东', answer_count: 3 }],
  modes: [{ label: '深度回答', answer_count: 3 }],
  data: [
    {
      meta_query_id: metaQueryId,
      ordinal: 1,
      label: '测试关键词',
      question_count: 4,
      answer_count: 5,
      cited_answer_count: 4,
      citation_count: 12,
      mentioned_answer_count: 3,
      latest_capture_time: '2026-08-17T07:00:00Z',
      models: dimensions,
      regions: [{ label: '华东', answer_count: 3 }],
      modes: [{ label: '深度回答', answer_count: 3 }],
      questions: questionChoices,
    },
  ],
  page: { total: 34, offset: 0, limit: 8, has_more: true },
};

const metaDetail: CustomerAnswerLibraryMetaDetail = {
  schema_version: 'customer-answer-library-meta-v1',
  project_pub_id: libraryPage.project_pub_id,
  snapshot_id: snapshotId,
  snapshot_at: libraryPage.snapshot_at,
  meta_query_id: metaQueryId,
  ordinal: 1,
  label: '测试关键词',
  answer_count: 5,
  cited_answer_count: 4,
  citation_count: 12,
  mentioned_answer_count: 3,
  latest_capture_time: '2026-08-17T07:00:00Z',
  questions: questionChoices.map((question) => ({
    ...question,
    cited_answer_count: question.answer_count,
    citation_count: question.answer_count * 2,
    mentioned_answer_count: question.answer_count,
    latest_capture_time: '2026-08-17T07:00:00Z',
    models: dimensions,
    regions: [{ label: '华东', answer_count: question.answer_count }],
    modes: [{ label: '深度回答', answer_count: question.answer_count }],
  })),
};

const runsPage: CustomerAnswerLibraryRuns = {
  schema_version: 'customer-answer-library-runs-v1',
  project_pub_id: libraryPage.project_pub_id,
  snapshot_id: snapshotId,
  snapshot_at: libraryPage.snapshot_at,
  meta_query_id: metaQueryId,
  meta_query_ordinal: 1,
  meta_query_label: '测试关键词',
  question: metaDetail.questions[0]!,
  models: dimensions,
  regions: [{ label: '华东', answer_count: 2 }],
  modes: [{ label: '深度回答', answer_count: 2 }],
  data: [
    {
      answer_pub_id: 'ans_library_01',
      repeat_index: 2,
      model: 'DeepSeek',
      region: '华东',
      mode: '深度回答',
      capture_time: '2026-08-17T07:00:00Z',
      analysis_state: 'ready',
      mentioned: true,
      rank: 1,
      sentiment: 'positive',
      recommended: true,
      citation_count: 1,
    },
    {
      answer_pub_id: 'ans_library_02',
      repeat_index: 1,
      model: '豆包',
      region: '华东',
      mode: '快速回答',
      capture_time: '2026-08-16T07:00:00Z',
      analysis_state: 'pending',
      mentioned: null,
      rank: null,
      sentiment: null,
      recommended: null,
      citation_count: 0,
    },
  ],
  page: { total: 2, offset: 0, limit: 20, has_more: false },
};

const answerDetail: CustomerAnswerLibraryAnswer = {
  schema_version: 'customer-answer-library-detail-v1',
  project_pub_id: libraryPage.project_pub_id,
  snapshot_id: snapshotId,
  snapshot_at: libraryPage.snapshot_at,
  meta_query_id: metaQueryId,
  meta_query_ordinal: 1,
  meta_query_label: '测试关键词',
  question_id: questionIds[0]!,
  question_ordinal: 1,
  variant_label: '原问题',
  question_text: '第1条具体问题',
  answer: runsPage.data[0]!,
  response_text:
    '# 选型结论\n\n这段完整正文只应该在第四层出现。[citation:0]\n\n![不应显示](https://assets.example/image.png)\n\n[危险链接](javascript:alert(1))\n\n<script>window.leaked = true</script>',
};

const relationDetail: CustomerAnswerDetail = {
  citations: [
    {
      id: 'cit_detail_01',
      ordinal: 1,
      url: 'https://source.example/answer',
      host: 'source.example',
      title: '可核验来源',
      citedText: '权限边界需要独立验证。',
      ownSource: false,
      contentHash: 'a'.repeat(64),
      publishedAt: null,
      publishedAtSource: null,
    },
  ],
  evidence: [],
  shareImage: {
    id: 'evd_detail_share_image_01',
    relation: 'official_share_image',
    kind: 'share_image',
    mimeType: 'image/png',
    byteSize: 3,
    sha256: 'b'.repeat(64),
    sourceUrl: null,
    captureTime: '2026-08-17T08:00:00Z',
  },
  shareArtifact: {
    platform: 'deepseek',
    status: 'available',
    shareUrl: 'https://chat.deepseek.com/share/detail-safe',
    finalUrl: 'https://chat.deepseek.com/share/detail-safe',
    availabilityStatus: 'reachable',
    httpStatus: 200,
    checkedAt: '2026-08-17T08:00:00Z',
    lastAccessibleAt: '2026-08-17T08:00:00Z',
    embedStatus: 'allowed',
    embedReason: 'no_restrictive_frame_policy',
  },
  projectionComplete: true,
};

const renderLibrary = (overrides?: {
  loadLibraryPage?: () => Promise<CustomerAnswerLibraryPage>;
  loadAnswer?: () => Promise<CustomerAnswerLibraryAnswer>;
  loadDetail?: () => Promise<CustomerAnswerDetail>;
  loadEvidenceImage?: () => Promise<{ kind: 'ready'; blob: Blob }>;
}) => {
  const loaders = {
    loadLibraryPage: vi.fn(overrides?.loadLibraryPage ?? (async () => libraryPage)),
    loadMetaQuery: vi.fn(async () => metaDetail),
    loadQuestionRuns: vi.fn(async () => runsPage),
    loadAnswer: vi.fn(overrides?.loadAnswer ?? (async () => answerDetail)),
    loadDetail: vi.fn(overrides?.loadDetail ?? (async () => relationDetail)),
    loadEvidenceImage: vi.fn(
      overrides?.loadEvidenceImage ??
        (async () => ({ kind: 'ready' as const, blob: new Blob(['png'], { type: 'image/png' }) })),
    ),
  };
  render(<CustomerAnswerExplorer brandName="测试品牌" {...loaders} fixturePage={libraryPage} />);
  return loaders;
};

const enterFirstAnswer = async () => {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: /进入 4 条问题/u }));
  const questionHeading = await screen.findByRole('heading', { name: '第1条具体问题' });
  const questionCard = questionHeading.closest('article');
  if (!questionCard) throw new Error('question card unavailable');
  await user.click(within(questionCard).getByRole('button', { name: /选择采集条件/u }));
  await user.click((await screen.findAllByRole('button', { name: /查看完整答案/u }))[0]!);
  return user;
};

describe('CustomerAnswerExplorer four-level library', () => {
  it('reuses the first directory snapshot when loading another keyword page', async () => {
    const loadLibraryPage = vi.fn(async (query: { offset: number; limit: number }) => ({
      ...libraryPage,
      page: {
        ...libraryPage.page,
        offset: query.offset,
        limit: query.limit,
        has_more: query.offset + query.limit < libraryPage.page.total,
      },
    }));
    render(
      <CustomerAnswerExplorer
        brandName="测试品牌"
        loadLibraryPage={loadLibraryPage}
        loadMetaQuery={async () => metaDetail}
        loadQuestionRuns={async () => runsPage}
        loadAnswer={async () => answerDetail}
      />,
    );

    expect(await screen.findByRole('heading', { name: '测试关键词' })).toBeTruthy();
    expect(loadLibraryPage).toHaveBeenNthCalledWith(1, {
      search: '',
      offset: 0,
      limit: 8,
    });
    await userEvent.setup().click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() =>
      expect(loadLibraryPage).toHaveBeenNthCalledWith(
        2,
        expect.objectContaining({
          offset: 8,
          snapshotId,
          snapshotAt: libraryPage.snapshot_at,
        }),
      ),
    );
  });

  it('keeps the 34-group directory paged and loads body only after all three selectors', async () => {
    const loaders = renderLibrary();

    expect(await screen.findByRole('heading', { name: '测试关键词' })).toBeTruthy();
    expect(screen.getByText('1,237')).toBeTruthy();
    expect(screen.getByText('3,918')).toBeTruthy();
    expect(screen.getByRole('navigation', { name: '关键词分页' })).toBeTruthy();
    expect(screen.queryByText(/这段完整正文/u)).toBeNull();
    expect(loaders.loadAnswer).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /进入 4 条问题/u }));

    expect(await screen.findAllByRole('button', { name: /选择采集条件/u })).toHaveLength(4);
    expect(loaders.loadMetaQuery).toHaveBeenCalledWith(metaQueryId, {
      snapshotId,
      snapshotAt: libraryPage.snapshot_at,
    });
    expect(screen.queryByText(/这段完整正文/u)).toBeNull();

    const questionHeading = screen.getByRole('heading', { name: '第1条具体问题' });
    const questionCard = questionHeading.closest('article');
    if (!questionCard) throw new Error('question card unavailable');
    await user.click(within(questionCard).getByRole('button', { name: /选择采集条件/u }));

    expect(await screen.findByText('第 2 遍')).toBeTruthy();
    expect(screen.getByText('分析中')).toBeTruthy();
    expect(loaders.loadQuestionRuns).toHaveBeenCalledWith(
      questionIds[0],
      expect.objectContaining({
        snapshotId,
        snapshotAt: libraryPage.snapshot_at,
        offset: 0,
        limit: 20,
      }),
    );
    expect(loaders.loadAnswer).not.toHaveBeenCalled();
    expect(screen.queryByText(/这段完整正文/u)).toBeNull();

    await user.click(screen.getAllByRole('button', { name: /查看完整答案/u })[0]!);
    expect(await screen.findByText(/这段完整正文只应该在第四层出现/u)).toBeTruthy();
    expect(loaders.loadAnswer).toHaveBeenCalledWith('ans_library_01', {
      snapshotId,
      snapshotAt: libraryPage.snapshot_at,
    });
    expect(loaders.loadDetail).toHaveBeenCalledWith('ans_library_01', {
      snapshotId,
      snapshotAt: libraryPage.snapshot_at,
    });
    expect(screen.getByRole('navigation', { name: '答案库路径' }).textContent).toContain(
      '关键词/查询 01 · 测试关键词/问题 01 · 原问题/DeepSeek · 华东 · 第 2 遍 · 深度回答',
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renders the fourth layer safely and keeps the simplified citation evidence table', async () => {
    const loaders = renderLibrary();
    const user = await enterFirstAnswer();

    const modeTabs = screen.getByRole('tablist', { name: '答案展示方式' });
    expect(within(modeTabs).getByRole('tab', { name: '文本回答' })).toBeTruthy();
    expect(within(modeTabs).getByRole('tab', { name: '官方实时页' })).toBeTruthy();
    expect(within(modeTabs).getByRole('tab', { name: '分享图片' })).toBeTruthy();
    expect(screen.queryByTitle('DeepSeek 官方回答只读预览')).toBeNull();

    const fallback = screen.getByRole('region', { name: '历史采集答案退阶阅读版' });
    expect(within(fallback).getByText('文本存档')).toBeTruthy();
    expect(within(fallback).queryByText(/未保存官方分享链接/u)).toBeNull();
    expect(within(fallback).getByRole('heading', { name: '选型结论' })).toBeTruthy();
    expect(within(fallback).queryByText(/\[citation:0\]/u)).toBeNull();
    expect(within(fallback).getByRole('link', { name: '1' }).getAttribute('href')).toBe(
      '#citation-1',
    );
    expect(within(fallback).queryByRole('img')).toBeNull();
    expect(within(fallback).queryByText('不应显示')).toBeNull();
    expect(within(fallback).queryByRole('link', { name: '危险链接' })).toBeNull();
    expect(within(fallback).queryByText(/window\.leaked/u)).toBeNull();
    expect(within(fallback).queryByText(/采集于/u)).toBeNull();
    expect(within(fallback).queryByText('已保留采集证据')).toBeNull();
    expect(fallback.querySelector('footer')).toBeNull();

    await user.click(screen.getByRole('button', { name: '复制分享链接' }));
    expect(screen.getByRole('button', { name: '分享链接已复制' })).toBeTruthy();

    await user.click(within(modeTabs).getByRole('tab', { name: '官方实时页' }));

    const officialFrame = (await screen.findByTitle(
      'DeepSeek 官方回答只读预览',
    )) as HTMLIFrameElement;
    expect(officialFrame.tabIndex).toBe(-1);
    expect(officialFrame.getAttribute('sandbox')).toBe('allow-scripts allow-same-origin');
    expect(officialFrame.getAttribute('sandbox')).not.toContain('allow-forms');
    expect(officialFrame.getAttribute('sandbox')).not.toContain('allow-popups');
    expect(officialFrame.getAttribute('sandbox')).not.toContain('allow-top-navigation');
    expect(screen.getByRole('link', { name: '打开官方原页 ↗' }).getAttribute('href')).toBe(
      'https://chat.deepseek.com/share/detail-safe',
    );
    expect(officialFrame.getAttribute('aria-hidden')).toBe('true');

    await user.click(within(modeTabs).getByRole('tab', { name: '分享图片' }));
    expect(await screen.findByRole('img', { name: 'DeepSeek 官方分享图片' })).toBeTruthy();
    expect(loaders.loadEvidenceImage).toHaveBeenCalledWith(relationDetail.shareImage);

    await user.click(within(modeTabs).getByRole('tab', { name: '文本回答' }));
    expect(screen.getByRole('heading', { name: '选型结论' })).toBeTruthy();

    expect(
      within(screen.getByRole('region', { name: '引用信源分析表' })).getByRole('table'),
    ).toBeTruthy();
    expect(screen.getByText('引用片段：权限边界需要独立验证。')).toBeTruthy();
    expect(screen.queryByText('发布时间完整度')).toBeNull();
    expect(screen.queryByText(/AI 返回引用片段已登记/u)).toBeNull();
  });

  it('omits unavailable answer modality tabs instead of rendering empty states', async () => {
    renderLibrary({
      loadDetail: async () => ({
        citations: [],
        evidence: [],
        shareArtifact: null,
        projectionComplete: true,
      }),
    });
    await enterFirstAnswer();

    expect(screen.queryByRole('tablist', { name: '答案展示方式' })).toBeNull();
    expect(screen.queryByRole('tab', { name: '官方实时页' })).toBeNull();
    expect(screen.queryByRole('tab', { name: '分享图片' })).toBeNull();
    expect(screen.getByRole('region', { name: '历史采集答案退阶阅读版' })).toBeTruthy();
  });

  it('never promotes a generic evidence image into the official share-image mode', async () => {
    const genericLookalike = relationDetail.shareImage;
    if (!genericLookalike) throw new Error('share image fixture unavailable');
    const loaders = renderLibrary({
      loadDetail: async () => ({
        ...relationDetail,
        evidence: [genericLookalike],
        shareImage: null,
      }),
    });
    await enterFirstAnswer();

    const modeTabs = screen.getByRole('tablist', { name: '答案展示方式' });
    expect(within(modeTabs).getByRole('tab', { name: '文本回答' })).toBeTruthy();
    expect(within(modeTabs).getByRole('tab', { name: '官方实时页' })).toBeTruthy();
    expect(within(modeTabs).queryByRole('tab', { name: '分享图片' })).toBeNull();
    expect(loaders.loadEvidenceImage).not.toHaveBeenCalled();
  });

  it('uses the path controls for backward navigation instead of opening a modal', async () => {
    renderLibrary();
    const user = await enterFirstAnswer();

    await user.click(screen.getByRole('button', { name: '问题 01 · 原问题' }));
    expect(await screen.findAllByRole('button', { name: /查看完整答案/u })).toHaveLength(2);
    expect(screen.queryByText(/这段完整正文/u)).toBeNull();

    await user.click(screen.getByRole('button', { name: '关键词' }));
    expect(await screen.findByRole('heading', { name: '测试关键词' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('distinguishes an expired session from a retryable directory failure', async () => {
    render(
      <CustomerAnswerExplorer
        brandName="测试品牌"
        loadLibraryPage={async () => {
          throw new CustomerAnswerLoadError('forbidden');
        }}
        loadMetaQuery={async () => metaDetail}
        loadQuestionRuns={async () => runsPage}
        loadAnswer={async () => answerDetail}
      />,
    );

    expect(await screen.findByText('登录状态已失效')).toBeTruthy();
    expect(screen.getByRole('link', { name: '重新登录' }).getAttribute('href')).toBe(
      '/platform/operations/login',
    );
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull();
  });

  it('builds bounded page links without forcing users through every intermediate page', () => {
    expect(customerAnswerPaginationItems(30, 62)).toEqual([
      1,
      2,
      'gap-2',
      29,
      30,
      31,
      'gap-31',
      61,
      62,
    ]);
    expect(customerAnswerPaginationItems(1, 3)).toEqual([1, 2, 3]);
  });
});
