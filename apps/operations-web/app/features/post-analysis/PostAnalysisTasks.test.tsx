// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router';
import { createPostAnalysisTask, listPostAnalysisTasks } from '@geo/api-client';
import { parseUrlLines, PostAnalysisTasks } from './PostAnalysisTasks';

vi.mock('@geo/api-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@geo/api-client')>();
  return {
    ...original,
    listPostAnalysisTasks: vi.fn(),
    createPostAnalysisTask: vi.fn(),
  };
});

const headers = {
  'X-Tenant-Id': 'tnt_pa_test',
  'X-Actor-Id': 'operator-test',
  'X-Actor-Role': 'operator',
};

const existingTask = {
  pubId: 'pat_existing',
  targetBrand: 'Acme',
  targetBrandAliases: ['Acme中国'],
  status: 'completed' as const,
  urlCount: 3,
  error: null,
  createdAt: '2026-08-05T10:00:00Z',
  updatedAt: '2026-08-05T10:30:00Z',
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/platform/operations/post-analysis']}>
      <Routes>
        <Route
          path="/platform/operations/post-analysis"
          element={<PostAnalysisTasks headers={headers} canWrite />}
        />
        <Route
          path="/platform/operations/post-analysis/tasks/:taskPubId"
          element={<div>任务详情已打开</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('parseUrlLines', () => {
  it('trims, validates http/https and dedupes client-side', () => {
    const parsed = parseUrlLines(
      [
        ' https://example.com/a ',
        '',
        'https://example.com/a',
        'ftp://example.com/b',
        'not-a-url',
        'https://example.com/c',
      ].join('\n'),
    );
    expect(parsed.urls).toEqual(['https://example.com/a', 'https://example.com/c']);
    expect(parsed.invalid).toEqual(['ftp://example.com/b', 'not-a-url']);
  });
});

describe('PostAnalysisTasks', () => {
  it('renders the task list and creates a task from the operation console', async () => {
    vi.mocked(listPostAnalysisTasks).mockResolvedValue({
      kind: 'ready',
      data: { data: [existingTask], nextCursor: null, hasMore: false },
    });
    vi.mocked(createPostAnalysisTask).mockResolvedValue({
      kind: 'ready',
      data: { pubId: 'pat_created' },
    });

    renderPage();

    expect(await screen.findByText('Acme')).toBeTruthy();
    expect(screen.getByText('已完成')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('目标品牌'), { target: { value: ' 新品牌 ' } });
    fireEvent.change(screen.getByLabelText('品牌别名'), { target: { value: '别名A，别名B' } });
    fireEvent.change(screen.getByLabelText('帖子 URL 列表'), {
      target: { value: 'https://example.com/p1\nhttps://example.com/p1\nhttps://example.com/p2' },
    });
    fireEvent.click(screen.getByRole('button', { name: '创建分析任务' }));

    await waitFor(() => {
      expect(createPostAnalysisTask).toHaveBeenCalledWith(
        headers,
        {
          targetBrand: '新品牌',
          targetBrandAliases: ['别名A', '别名B'],
          urls: ['https://example.com/p1', 'https://example.com/p2'],
          verifyFacts: true,
          annotate: true,
          openInvestigation: true,
        },
        expect.stringMatching(/^post-analysis-/u),
      );
    });
    expect(await screen.findByText('任务详情已打开')).toBeTruthy();
  });

  it('rejects non-http/https URLs without calling the API', async () => {
    vi.mocked(listPostAnalysisTasks).mockResolvedValue({
      kind: 'ready',
      data: { data: [], nextCursor: null, hasMore: false },
    });

    renderPage();

    expect(await screen.findByText('暂无数据')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('目标品牌'), { target: { value: '新品牌' } });
    fireEvent.change(screen.getByLabelText('帖子 URL 列表'), {
      target: { value: 'not-a-url' },
    });
    fireEvent.click(screen.getByRole('button', { name: '创建分析任务' }));

    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(createPostAnalysisTask).not.toHaveBeenCalled();
  });
});
