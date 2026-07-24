// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { EvidenceViewer } from './index';

afterEach(cleanup);

describe('EvidenceViewer', () => {
  it('binds screenshot, text range, bbox and semantic history diff to one evidence asset', () => {
    render(
      <EvidenceViewer
        label="信源截图"
        anchor={{
          assetId: 'asset_safe_01',
          textStart: 112,
          textEnd: 168,
          bbox: [84, 176, 310, 42],
        }}
        previousText="旧页面表述"
        currentText="当前页面表述"
      >
        <span>受控截图内容</span>
      </EvidenceViewer>,
    );

    expect(
      screen.getByRole('img', { name: '信源截图，证据锚点 84,176,310,42' }).textContent,
    ).toContain('受控截图内容');
    expect(screen.getByText('asset_safe_01', { selector: 'dd' })).toBeTruthy();
    expect(screen.getByText('112–168')).toBeTruthy();
    expect(screen.getByText('84,176,310,42', { selector: 'dd' })).toBeTruthy();
    expect(screen.getByText('旧页面表述', { selector: 'del' })).toBeTruthy();
    expect(screen.getByText('当前页面表述', { selector: 'ins' })).toBeTruthy();
  });

  it('renders explicit missing-anchor values without inventing coordinates or a diff', () => {
    const { container } = render(
      <EvidenceViewer label="回答截图" anchor={{ assetId: 'asset_safe_02' }} />,
    );

    expect(screen.getByRole('img', { name: '回答截图，证据锚点 —' })).toBeTruthy();
    expect(
      [...container.querySelectorAll('dd')].map((definition) => definition.textContent),
    ).toEqual(['asset_safe_02', '—–—', '—']);
    expect(container.querySelector('.evidence-diff')).toBeNull();
  });
});
