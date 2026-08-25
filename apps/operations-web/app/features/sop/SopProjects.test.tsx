// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
        page: 1,
        pageSize: 4,
        totalCount: 1,
        totalPages: 1,
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

  it('shows server totals and jumps directly to project rows beyond 100', async () => {
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
          page: 1,
          pageSize: 4,
          totalCount: 105,
          totalPages: 27,
        },
      })
      .mockResolvedValueOnce({
        kind: 'ready',
        data: {
          data: [
            {
              pubId: 'spr_105',
              name: '第 105 条项目',
              brandStandardName: 'Acme',
              status: 'active',
              updatedAt: '2026-07-28T11:00:00Z',
            },
          ],
          page: 27,
          pageSize: 4,
          totalCount: 105,
          totalPages: 27,
        },
      })
      .mockResolvedValueOnce({
        kind: 'ready',
        data: {
          data: [
            {
              pubId: 'spr_101',
              name: '第 101 条项目',
              brandStandardName: 'Acme',
              status: 'active',
              updatedAt: '2026-07-28T10:00:00Z',
            },
          ],
          page: 26,
          pageSize: 4,
          totalCount: 105,
          totalPages: 27,
        },
      });

    render(
      <MemoryRouter initialEntries={['/platform/operations/sop']}>
        <SopProjects headers={headers} canWrite={false} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('第一页项目')).toBeTruthy();
    const pager = screen.getByRole('navigation', { name: 'SOP 项目分页' });
    expect(within(pager).getByText('共 105 条 ·')).toBeTruthy();
    expect(within(pager).getByText('第 1 / 27 页')).toBeTruthy();
    fireEvent.change(within(pager).getByRole('spinbutton', { name: '跳转页码' }), {
      target: { value: '27' },
    });
    fireEvent.click(within(pager).getByRole('button', { name: '跳转' }));
    expect(await screen.findByText('第 105 条项目')).toBeTruthy();
    expect(listSopProjects).toHaveBeenNthCalledWith(2, headers, 27);
    expect(screen.queryByText('第一页项目')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '上一页' }));
    expect(await screen.findByText('第 101 条项目')).toBeTruthy();
    expect(listSopProjects).toHaveBeenNthCalledWith(3, headers, 26);
  });
});
