// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_REGIONS, REGION_OPTIONS, RegionMultiSelect } from './RegionMultiSelect';

describe('RegionMultiSelect', () => {
  afterEach(() => {
    cleanup();
  });

  it('selects 北京 and 上海 by default and never offers 全国', () => {
    render(<RegionMultiSelect />);
    expect(screen.getByRole('button', { name: '北京' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: '上海' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: '天津' }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.queryByRole('button', { name: '全国' })).toBeNull();
    expect(screen.getAllByRole('button')).toHaveLength(REGION_OPTIONS.length);
    expect(DEFAULT_REGIONS).toEqual(['北京', '上海']);
  });

  it('toggles chips and reports the full selection', () => {
    const onChange = vi.fn();
    render(<RegionMultiSelect onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: '深圳' }));
    expect(onChange).toHaveBeenLastCalledWith(['北京', '上海', '深圳']);
    expect(screen.getByRole('button', { name: '深圳' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: '北京' }));
    expect(onChange).toHaveBeenLastCalledWith(['上海', '深圳']);
    expect(screen.getByRole('button', { name: '北京' }).getAttribute('aria-pressed')).toBe('false');
  });

  it('honours the controlled value prop', () => {
    const onChange = vi.fn();
    render(<RegionMultiSelect value={['成都']} onChange={onChange} />);
    expect(screen.getByRole('button', { name: '成都' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: '北京' }).getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: '杭州' }));
    expect(onChange).toHaveBeenLastCalledWith(['成都', '杭州']);
  });
});
