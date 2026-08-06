// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router';
import { createSopProject, listSopProjects } from '@geo/api-client';
import { SopProjects } from './SopProjects';

vi.mock('@geo/api-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@geo/api-client')>();
  return {
    ...original,
    listSopProjects: vi.fn(),
    createSopProject: vi.fn(),
  };
});

const headers = {
  'X-Tenant-Id': 'tnt_sop_test',
  'X-Actor-Id': 'operator-test',
  'X-Actor-Role': 'operator',
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('SopProjects', () => {
  it('renders the project monitor and creates a project from the operation console', async () => {
    vi.mocked(listSopProjects).mockResolvedValue({
      kind: 'ready',
      data: {
        data: [
          {
            pubId: 'spr_existing',
            name: '现有项目',
            brandStandardName: 'Acme',
            status: 'active',
            updatedAt: '2026-07-28T10:00:00Z',
          },
        ],
        nextCursor: null,
        hasMore: false,
      },
    });
    vi.mocked(createSopProject).mockResolvedValue({
      kind: 'ready',
      data: {
        pubId: 'spr_created',
        relatedPubId: null,
        message: 'SOP 项目已创建',
      },
    });

    render(
      <MemoryRouter initialEntries={['/platform/operations/sop']}>
        <Routes>
          <Route
            path="/platform/operations/sop"
            element={<SopProjects headers={headers} canWrite />}
          />
          <Route
            path="/platform/operations/sop/projects/:projectPubId"
            element={<div>项目工作区已打开</div>}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('现有项目')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('项目名称'), { target: { value: '新项目' } });
    fireEvent.change(screen.getByLabelText('标准品牌名'), { target: { value: 'NewCo' } });
    fireEvent.change(screen.getByLabelText('目标 AI / 模式'), {
      target: { value: 'doubao/search' },
    });
    fireEvent.change(screen.getByLabelText('成功定义'), {
      target: { value: '引用率达到 50%' },
    });
    fireEvent.click(screen.getByRole('button', { name: '创建 SOP 项目' }));

    await waitFor(() =>
      expect(createSopProject).toHaveBeenCalledWith(
        headers,
        {
          name: '新项目',
          brandStandardName: 'NewCo',
          targetPlatform: 'doubao/search',
          successMetric: '引用率达到 50%',
        },
        expect.stringMatching(/^sop-/),
      ),
    );
    expect(await screen.findByText('项目工作区已打开')).toBeTruthy();
  });

  it('loads subsequent cursor pages without hiding projects after the first page', async () => {
    vi.mocked(listSopProjects)
      .mockResolvedValueOnce({
        kind: 'ready',
        data: {
          data: [
            {
              pubId: 'spr_first',
              name: '第一页项目',
              brandStandardName: 'Acme',
              status: 'active',
              updatedAt: '2026-07-28T10:00:00Z',
            },
          ],
          nextCursor: 'spr_first',
          hasMore: true,
        },
      })
      .mockResolvedValueOnce({
        kind: 'ready',
        data: {
          data: [
            {
              pubId: 'spr_second',
              name: '第二页项目',
              brandStandardName: 'Acme',
              status: 'active',
              updatedAt: '2026-07-28T11:00:00Z',
            },
          ],
          nextCursor: null,
          hasMore: false,
        },
      });

    render(
      <MemoryRouter initialEntries={['/platform/operations/sop']}>
        <SopProjects headers={headers} canWrite={false} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('第一页项目')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '加载更多项目' }));
    expect(await screen.findByText('第二页项目')).toBeTruthy();
    expect(listSopProjects).toHaveBeenNthCalledWith(2, headers, 'spr_first');
    expect(screen.queryByRole('button', { name: '加载更多项目' })).toBeNull();
  });
});
