// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  CustomerAnswerExplorer,
  type CustomerAnswerDetail,
  type CustomerAnswerExplorerPage,
} from './customer-answer-explorer';

afterEach(() => {
  cleanup();
});

const answerPage: CustomerAnswerExplorerPage = {
  schema_version: 'customer-answer-page-v1',
  project_pub_id: 'prj_answer_group_test',
  data: [
    {
      answer_pub_id: 'ans_group_01',
      query_pub_id: 'qry_group_01',
      query_text: '第一条测试问题',
      response_text: '第一条测试回答原文。',
      model: 'DeepSeek',
      region: '华东',
      mode: '深度回答',
      capture_time: '2026-08-17T08:00:00Z',
      mentioned: true,
      rank: 1,
      sentiment: 'positive',
      recommended: true,
      citation_count: 2,
    },
    {
      answer_pub_id: 'ans_group_02',
      query_pub_id: 'qry_group_02',
      query_text: '第二条测试问题',
      response_text: '第二条测试回答原文。',
      model: '豆包',
      region: '华北',
      mode: '快速回答',
      capture_time: '2026-08-17T07:00:00Z',
      mentioned: false,
      rank: null,
      sentiment: 'neutral',
      recommended: false,
      citation_count: 0,
    },
  ],
  page: { total: 2, offset: 0, limit: 20, has_more: false },
};

describe('CustomerAnswerExplorer classification', () => {
  it('groups answers by platform by default and can regroup by mode or region', async () => {
    const user = userEvent.setup();
    const loadPage = vi.fn(async () => answerPage);
    const { container } = render(
      <CustomerAnswerExplorer brandName="测试品牌" loadPage={loadPage} fixturePage={answerPage} />,
    );

    const directory = await screen.findByRole('complementary', { name: '回答分类导航' });
    await user.click(within(directory).getByRole('button', { name: /DeepSeek/u }));
    expect(screen.getByRole('heading', { name: 'DeepSeek' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '豆包' })).toBeNull();
    expect(screen.getByRole('region', { name: 'DeepSeek回答明细' })).toBeTruthy();
    expect(container.querySelector('.geo-answer-card')).toBeNull();

    await user.click(screen.getByRole('button', { name: '按回答模式' }));
    await user.click(within(directory).getByRole('button', { name: /深度回答/u }));
    expect(screen.getByRole('heading', { name: '深度回答' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '快速回答' })).toBeNull();
    expect(screen.getByRole('button', { name: '按回答模式' }).getAttribute('aria-pressed')).toBe(
      'true',
    );

    await user.click(screen.getByRole('button', { name: '按地域' }));
    await user.click(within(directory).getByRole('button', { name: /华东/u }));
    expect(screen.getByRole('heading', { name: '华东' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '华北' })).toBeNull();
    expect(screen.getByText('2 个平台 · 2 种模式 · 2 个地域')).toBeTruthy();
  });

  it('opens the official live page with a structured citation table and copies its share URL', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    const markdownPage: CustomerAnswerExplorerPage = {
      ...answerPage,
      data: [
        {
          ...answerPage.data[0]!,
          response_text:
            '# 选型结论\n\n- 核验权限边界 [citation:0]\n- 保存完整证据\n\n| 项目 | 结果 |\n| --- | --- |\n| 引用 | 可追溯 |',
          citation_count: 1,
        },
      ],
      page: { total: 1, offset: 0, limit: 20, has_more: false },
    };
    const answerDetail: CustomerAnswerDetail = {
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
      evidence: [
        {
          id: 'evd_detail_link_01',
          relation: 'official_share_link',
          kind: 'share_link',
          mimeType: 'application/json',
          byteSize: 120,
          sha256: 'b'.repeat(64),
          sourceUrl: 'https://chat.deepseek.com/share/detail-safe',
          captureTime: '2026-08-17T08:00:00Z',
        },
      ],
      projectionComplete: true,
    };
    render(
      <CustomerAnswerExplorer
        brandName="测试品牌"
        loadPage={async () => markdownPage}
        loadDetail={async () => answerDetail}
        fixturePage={markdownPage}
      />,
    );

    await user.click(
      within(await screen.findByRole('region', { name: 'DeepSeek回答明细' })).getByRole('button', {
        name: /查看官方回答与信源/,
      }),
    );

    const officialFrame = (await screen.findByTitle(
      'DeepSeek 官方回答只读预览',
    )) as HTMLIFrameElement;
    expect(officialFrame.tabIndex).toBe(-1);
    expect(officialFrame.getAttribute('sandbox')).toBe('allow-scripts allow-same-origin');
    expect(officialFrame.getAttribute('sandbox')).not.toContain('allow-forms');
    expect(officialFrame.getAttribute('sandbox')).not.toContain('allow-popups');
    expect(officialFrame.getAttribute('sandbox')).not.toContain('allow-top-navigation');
    expect(screen.getByRole('region', { name: '官方回答只读预览' })).toBeTruthy();
    expect(screen.getByText(/已裁掉平台底部/u)).toBeTruthy();
    expect(
      within(screen.getByRole('region', { name: '引用信源分析表' })).getByRole('table'),
    ).toBeTruthy();
    expect(screen.queryByText('# 选型结论')).toBeNull();
    expect(screen.queryByRole('heading', { name: '选型结论' })).toBeNull();
    expect(screen.getByText('待采集')).toBeTruthy();
    expect(screen.getByRole('link', { name: '可核验来源' }).getAttribute('href')).toBe(
      'https://source.example/answer',
    );
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.queryByText(/分享图片|采集现场/u)).toBeNull();
    await user.click(screen.getByRole('button', { name: '复制分享链接' }));
    expect(writeText).toHaveBeenCalledWith('https://chat.deepseek.com/share/detail-safe');
    expect(screen.getByRole('button', { name: '分享链接已复制' })).toBeTruthy();
  });

  it('switches between same-question platform runs inside the dossier', async () => {
    const user = userEvent.setup();
    const comparisonPage: CustomerAnswerExplorerPage = {
      ...answerPage,
      data: [
        answerPage.data[0]!,
        {
          ...answerPage.data[1]!,
          query_pub_id: answerPage.data[0]!.query_pub_id,
          query_text: answerPage.data[0]!.query_text,
        },
      ],
    };
    const loadDetail = vi.fn(
      async (): Promise<CustomerAnswerDetail> => ({
        citations: [],
        evidence: [],
        projectionComplete: true,
      }),
    );
    render(
      <CustomerAnswerExplorer
        brandName="测试品牌"
        loadPage={async () => comparisonPage}
        loadDetail={loadDetail}
        fixturePage={comparisonPage}
      />,
    );

    const directory = await screen.findByRole('complementary', { name: '回答分类导航' });
    await user.click(within(directory).getByRole('button', { name: /DeepSeek/u }));
    await user.click(
      within(screen.getByRole('region', { name: 'DeepSeek回答明细' })).getByRole('button', {
        name: /查看官方回答与信源/,
      }),
    );
    const doubaoRun = await screen.findByRole('button', {
      name: /豆包，快速回答/u,
    });
    expect(doubaoRun.getAttribute('aria-pressed')).toBe('false');
    await user.click(doubaoRun);

    expect(loadDetail).toHaveBeenLastCalledWith('ans_group_02');
    expect(
      screen.getByRole('button', { name: /豆包，快速回答/u }).getAttribute('aria-pressed'),
    ).toBe('true');
    expect(screen.getByText('本次采集没有官方分享链接')).toBeTruthy();
    expect(screen.queryByText('第二条测试回答原文。')).toBeNull();
  });
});
