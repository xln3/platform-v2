import type { ReactNode } from 'react';

export type EvidenceAnchor = {
  assetId: string;
  textStart?: number;
  textEnd?: number;
  bbox?: [number, number, number, number];
};

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
