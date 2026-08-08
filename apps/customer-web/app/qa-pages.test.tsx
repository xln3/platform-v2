// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import ExperienceStateMatrix from './state-matrix';
import PerformanceMatrix from './performance-matrix';

afterEach(() => {
  cleanup();
});

describe('customer QA pages navigation loop', () => {
  it('wraps the state matrix in the shared shell with a way back to the workbench', () => {
    render(<ExperienceStateMatrix />);

    expect(screen.getByRole('link', { name: '客户工作台' }).getAttribute('href')).toBe(
      '/platform/customer/',
    );
    expect(screen.getByRole('link', { name: '性能矩阵' }).getAttribute('href')).toBe(
      '/platform/customer/experience-performance',
    );
    expect(screen.getByRole('link', { name: '状态语义矩阵' }).getAttribute('aria-current')).toBe(
      'page',
    );
    expect(screen.getByRole('heading', { name: '数据状态语义矩阵' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '退出登录' })).toBeTruthy();
    expect(screen.getByRole('link', { name: '重新登录' }).getAttribute('href')).toBe(
      '/platform/operations/login',
    );
  });

  it('wraps the performance matrix in the shared shell with the same navigation loop', () => {
    render(<PerformanceMatrix />);

    expect(screen.getByRole('heading', { name: '大表、大图与长文本矩阵' })).toBeTruthy();
    expect(screen.getByRole('link', { name: '客户工作台' }).getAttribute('href')).toBe(
      '/platform/customer/',
    );
    expect(screen.getByRole('link', { name: '状态语义矩阵' }).getAttribute('href')).toBe(
      '/platform/customer/experience-states',
    );
    expect(screen.getByRole('link', { name: '性能矩阵' }).getAttribute('aria-current')).toBe(
      'page',
    );
  });
});
