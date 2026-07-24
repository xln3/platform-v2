// @vitest-environment jsdom

import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { WorkflowTimeline } from './index';

afterEach(cleanup);

describe('WorkflowTimeline', () => {
  it('exposes ordered workflow state and optional detail without relying on marker color', () => {
    render(
      <WorkflowTimeline
        label="报告审核流程"
        steps={[
          { id: 'freeze', label: '事实冻结', state: 'completed', detail: '窗口 v0.8' },
          { id: 'review', label: '人工审核', state: 'running' },
          { id: 'publish', label: '发布', state: 'scheduled' },
        ]}
      />,
    );

    const timeline = screen.getByRole('list', { name: '报告审核流程' });
    const items = within(timeline).getAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(items[0]?.getAttribute('data-state')).toBe('completed');
    expect(items[0]?.textContent).toBe('事实冻结窗口 v0.8completed');
    expect(items[1]?.getAttribute('data-state')).toBe('running');
    expect(items[1]?.textContent).toBe('人工审核running');
    expect(items[2]?.getAttribute('data-state')).toBe('scheduled');
    expect(items[2]?.textContent).toBe('发布scheduled');
  });
});
