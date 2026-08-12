// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { EvidenceImageFrame, EvidenceViewer } from './index';

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

describe('EvidenceImageFrame', () => {
  it('scales a verified source-coordinate box over the real image without adding callout text', () => {
    render(
      <EvidenceImageFrame
        label="盛邦安全信源原文证据"
        overlayLabel="目标品牌提及位置"
        anchor={{ assetId: 'evd_source_01', bbox: [100, 50, 200, 80] }}
      >
        <img src="/source.png" alt="真实信源页面截图" />
      </EvidenceImageFrame>,
    );

    const image = screen.getByRole('img', { name: '真实信源页面截图' });
    Object.defineProperties(image, {
      naturalWidth: { configurable: true, value: 1_000 },
      naturalHeight: { configurable: true, value: 500 },
    });
    fireEvent.load(image);

    const overlay = screen.getByRole('img', {
      name: '目标品牌提及位置，原图坐标 100,50,200,80',
    });
    expect((overlay as HTMLElement).style.left).toBe('10%');
    expect((overlay as HTMLElement).style.top).toBe('10%');
    expect((overlay as HTMLElement).style.width).toBe('20%');
    expect((overlay as HTMLElement).style.height).toBe('16%');
    expect(screen.getByText(/已绑定真实页面截图与原图坐标/)).toBeTruthy();
  });

  it('does not draw or invent an annotation when a box is missing or outside the image', () => {
    const { rerender } = render(
      <EvidenceImageFrame label="信源页面概览">
        <img src="/source.png" alt="无定位截图" />
      </EvidenceImageFrame>,
    );
    expect(screen.queryByRole('img', { name: /原图坐标/ })).toBeNull();
    expect(screen.getByText(/不会在截图上虚构高亮或文本浮层/)).toBeTruthy();

    rerender(
      <EvidenceImageFrame
        label="信源页面概览"
        anchor={{ assetId: 'evd_source_02', bbox: [950, 450, 100, 80] }}
      >
        <img src="/source.png" alt="越界定位截图" />
      </EvidenceImageFrame>,
    );
    const image = screen.getByRole('img', { name: '越界定位截图' });
    Object.defineProperties(image, {
      naturalWidth: { configurable: true, value: 1_000 },
      naturalHeight: { configurable: true, value: 500 },
    });
    fireEvent.load(image);
    expect(screen.queryByRole('img', { name: /原图坐标/ })).toBeNull();
  });
});
