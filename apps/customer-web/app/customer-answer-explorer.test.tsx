// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  CustomerAnswerExplorer,
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

    expect(await screen.findByRole('heading', { name: 'DeepSeek' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '豆包' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'DeepSeek回答明细' })).toBeTruthy();
    expect(container.querySelector('.geo-answer-card')).toBeNull();

    await user.click(screen.getByRole('button', { name: '按回答模式' }));
    expect(screen.getByRole('heading', { name: '深度回答' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '快速回答' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '按回答模式' }).getAttribute('aria-pressed')).toBe(
      'true',
    );

    await user.click(screen.getByRole('button', { name: '按地域' }));
    expect(screen.getByRole('heading', { name: '华东' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '华北' })).toBeTruthy();
    expect(screen.getByText('2 个平台 · 2 种模式 · 2 个地域')).toBeTruthy();
  });
});
