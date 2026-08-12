import { useState, type ReactEventHandler, type ReactNode } from 'react';

export type EvidenceAnchor = {
  assetId: string;
  textStart?: number;
  textEnd?: number;
  bbox?: [number, number, number, number];
};

type RenderedImageSize = {
  width: number;
  height: number;
};

const validAnchorBox = (
  bbox: EvidenceAnchor['bbox'],
  image: RenderedImageSize | null,
): bbox is [number, number, number, number] => {
  if (!bbox || !image) return false;
  const [x, y, width, height] = bbox;
  return (
    [x, y, width, height].every(Number.isFinite) &&
    x >= 0 &&
    y >= 0 &&
    width > 0 &&
    height > 0 &&
    image.width > 0 &&
    image.height > 0 &&
    x + width <= image.width &&
    y + height <= image.height
  );
};

/**
 * Places a real evidence image and its source-coordinate bounding box in one
 * scaling context. The rectangle is rendered only after the image's intrinsic
 * dimensions prove that the server-provided box is inside the captured page.
 * Text callouts deliberately stay outside the screenshot surface.
 */
export function EvidenceImageFrame({
  label,
  anchor,
  overlayLabel = '证据原文位置',
  children,
}: {
  label: string;
  anchor?: EvidenceAnchor;
  overlayLabel?: string;
  children: ReactNode;
}) {
  const [imageSize, setImageSize] = useState<RenderedImageSize | null>(null);
  const bbox = anchor?.bbox;
  const showOverlay = validAnchorBox(bbox, imageSize);
  const captureImageSize: ReactEventHandler<HTMLDivElement> = (event) => {
    const target = event.target;
    if (!(target instanceof HTMLImageElement)) return;
    const next = { width: target.naturalWidth, height: target.naturalHeight };
    setImageSize(next.width > 0 && next.height > 0 ? next : null);
  };

  return (
    <div className="evidence-image-frame" role="group" aria-label={label}>
      <div className="evidence-image-frame-surface" onLoadCapture={captureImageSize}>
        {children}
        {showOverlay && imageSize ? (
          <span
            className="evidence-image-anchor-box"
            role="img"
            aria-label={`${overlayLabel}，原图坐标 ${bbox.join(',')}`}
            style={{
              left: `${(bbox[0] / imageSize.width) * 100}%`,
              top: `${(bbox[1] / imageSize.height) * 100}%`,
              width: `${(bbox[2] / imageSize.width) * 100}%`,
              height: `${(bbox[3] / imageSize.height) * 100}%`,
            }}
          />
        ) : null}
      </div>
      <p className="evidence-image-frame-status">
        {showOverlay
          ? `${overlayLabel}：已绑定真实页面截图与原图坐标。`
          : '未提供可核验的页面坐标；不会在截图上虚构高亮或文本浮层。'}
      </p>
    </div>
  );
}

export function EvidenceViewer({
  label,
  anchor,
  children,
  previousText,
  currentText,
}: {
  label: string;
  anchor: EvidenceAnchor;
  children?: ReactNode;
  previousText?: string;
  currentText?: string;
}) {
  const bbox = anchor.bbox?.join(',') ?? '—';
  return (
    <div className="evidence-viewer">
      <div className="screenshot-placeholder" role="img" aria-label={`${label}，证据锚点 ${bbox}`}>
        {children}
        <span className="anchor-box">锚点 · {anchor.assetId}</span>
      </div>
      <aside>
        <h3>证据锚点</h3>
        <dl className="definition-grid evidence-dl">
          <div>
            <dt>资产</dt>
            <dd>{anchor.assetId}</dd>
          </div>
          <div>
            <dt>文本范围</dt>
            <dd>
              {anchor.textStart ?? '—'}–{anchor.textEnd ?? '—'}
            </dd>
          </div>
          <div>
            <dt>截图 bbox</dt>
            <dd>{bbox}</dd>
          </div>
        </dl>
        {previousText || currentText ? (
          <div className="evidence-diff">
            <h3>历史 diff</h3>
            {previousText ? <del>{previousText}</del> : null}
            {currentText ? <ins>{currentText}</ins> : null}
          </div>
        ) : null}
      </aside>
    </div>
  );
}
