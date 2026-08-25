// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import {
  COLLECTION_PLATFORM_SLUGS,
  PlatformBadge,
  PLATFORM_REGISTRY,
  platformDisplayName,
} from './platforms';

afterEach(cleanup);

describe('platform display registry', () => {
  it('owns exactly the five local platform displays', () => {
    expect(COLLECTION_PLATFORM_SLUGS).toEqual(['doubao', 'deepseek', 'yiyan', 'tongyi', 'yuanbao']);
    for (const slug of COLLECTION_PLATFORM_SLUGS) {
      expect(PLATFORM_REGISTRY[slug].icon).toMatch(new RegExp(`/platform-icons/${slug}\\.png$`));
    }
    expect(platformDisplayName('unknown-model')).toBe('unknown-model');
  });

  it('uses decorative alt text beside a visible label and an accessible alt when icon-only', () => {
    const { rerender } = render(<PlatformBadge platform="yiyan" />);
    expect(screen.getByText('文心一言')).toBeTruthy();
    const decorativeIcon = document.querySelector('img') as HTMLImageElement;
    expect(decorativeIcon.getAttribute('alt')).toBe('');
    expect(decorativeIcon.getAttribute('aria-hidden')).toBe('true');
    expect(decorativeIcon.style.objectFit).toBe('contain');

    rerender(<PlatformBadge platform="doubao" iconOnly />);
    expect(screen.getByRole('img', { name: '豆包' })).toBeTruthy();
    expect(screen.queryByText('豆包')).toBeNull();
  });
});
