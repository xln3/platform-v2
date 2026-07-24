import { useState } from 'react';
import { Badge, StatePanel } from '@geo/design-system';

export default function ExperienceStateMatrix() {
  const [retried, setRetried] = useState(false);
  return (
    <main className="state-matrix-page">
      <header>
        <span className="overline">Shared experience QA</span>
        <h1>数据状态语义矩阵</h1>
        <p>
          同一数据区域在不同事实状态下必须明确区分；“0”不是空，“不足”不是失败，“延迟”保留最后可用版本。
        </p>
      </header>
      <div className="state-matrix-grid">
        <section className="state-example">
          <Badge tone="positive">normal</Badge>
          <div className="state-panel state-ready" role="status">
            <span className="state-dot" aria-hidden="true" />
            <div>
              <strong>数据已就绪</strong>
              <p>38 个有效回答，口径版本 client-metrics-v2.4。</p>
            </div>
          </div>
        </section>
        <section className="state-example">
          <Badge tone="info">loading</Badge>
          <StatePanel state="loading" />
        </section>
        <section className="state-example">
          <Badge>empty</Badge>
          <StatePanel state="empty" />
        </section>
        <section className="state-example">
          <Badge>real-zero</Badge>
          <StatePanel state="real-zero" />
        </section>
        <section className="state-example">
          <Badge tone="warning">insufficient</Badge>
          <StatePanel state="insufficient" />
        </section>
        <section className="state-example">
          <Badge tone="danger">failed</Badge>
          {retried ? (
            <StatePanel state="loading" />
          ) : (
            <StatePanel state="failed" onRetry={() => setRetried(true)} />
          )}
        </section>
        <section className="state-example">
          <Badge tone="warning">delayed</Badge>
          <StatePanel state="delayed" />
        </section>
        <section className="state-example">
          <Badge tone="danger">forbidden</Badge>
          <StatePanel state="forbidden" />
        </section>
      </div>
    </main>
  );
}
