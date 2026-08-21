// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { SourceIntelligenceWorkspace } from './source-intelligence-workspace';

afterEach(cleanup);

describe('SourceIntelligenceWorkspace', () => {
  it('drills site to URL, histories, all occurrences and reverse answer UVW', async () => {
    const user = userEvent.setup();
    render(<SourceIntelligenceWorkspace />);

    expect(screen.getByText('默认按 distinct URL、U occurrence、最近出现时间排序。')).toBeTruthy();
    expect(screen.getAllByText(/V 不可观察/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/U 不可观察/).length).toBeGreaterThan(0);

    await user.click(screen.getAllByRole('button', { name: '查看 URL' })[0]!);
    expect(screen.getByText('默认按 U occurrence 次数倒序。')).toBeTruthy();
    await user.click(screen.getAllByRole('button', { name: '查看详情' })[0]!);

    expect(screen.getByText('页面快照、历史版本与风险体检')).toBeTruthy();
    expect(screen.getByText('W 内容片段、版本与人工复核')).toBeTruthy();
    expect(screen.getByText(/source exact \[20, 36\)/)).toBeTruthy();
    expect(screen.getAllByText(/目标品牌评测/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: '查看问答' })).toHaveLength(2);

    await user.type(screen.getByLabelText(/复核依据 wch_fixture_article/), '逐字证据复核不通过');
    await user.click(screen.getByRole('button', { name: '复核驳回' }));
    expect(screen.getByRole('status').textContent).toContain('W 片段已复核驳回');
    expect(screen.getByText(/最近复核：rejected · 逐字证据复核不通过/)).toBeTruthy();

    await user.click(screen.getByRole('button', { name: '查看逐字证据' }));
    expect(screen.getByText('风险发现与逐字证据')).toBeTruthy();
    expect(screen.getAllByText(/目标品牌在该项对比中排名靠后/).length).toBeGreaterThan(0);

    await user.click(screen.getAllByRole('button', { name: '查看问答' })[1]!);
    expect(screen.getByText('V 可观察性')).toBeTruthy();
    expect(screen.getAllByText('不可观察').length).toBeGreaterThan(0);
    expect(screen.getByRole('table', { name: '回答完整 UVW 滚动区域' })).toBeTruthy();
  });
});
