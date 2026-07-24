import { useEffect, useMemo, useRef, useState } from 'react';
import { Layer, Rect, Stage, Text } from 'react-konva';
import {
  Badge,
  containsClientSecret,
  MetricGrid,
  ProductShell,
  StatePanel,
  Toast,
  useOptionalExperienceContext,
} from '@geo/design-system';
import {
  commentOnReport,
  getHealth,
  getReport,
  listReports,
  publishReport,
  reviewReport,
  type ReportDetail,
  type ReportPage,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { WorkflowTimeline } from '@geo/workflow-ui';

const nav = [
  { id: 'window', label: '数据窗口' },
  { id: 'trace', label: 'KPI Trace' },
  { id: 'editor', label: '章节编辑' },
  { id: 'diff', label: '版本对比' },
  { id: 'evidence', label: '证据编排' },
  { id: 'preview', label: 'PDF 预览' },
  { id: 'review', label: '审核发布', badge: '3' },
  { id: 'outcomes', label: '效果复盘' },
];

type ReportState = 'draft' | 'frozen' | 'review' | 'approved' | 'published';
type LiveReportTarget = {
  reportPubId: string;
  versionPubId: string;
  versionNumber: number;
  status: string;
};
type Section = {
  id: string;
  title: string;
  body: string;
  provenance: 'ai' | 'human';
  modified: string;
};
type SectionVersion = {
  sectionId: string;
  version: number;
  title: string;
  body: string;
  savedBy: string;
  savedAt: string;
};
const initialSections: Section[] = [
  {
    id: 'summary',
    title: '执行摘要',
    body: '本窗口共获得 38 个有效回答，品牌提及率为 68.4%，较上一窗口提升 6.2 个百分点。',
    provenance: 'human',
    modified: '分析师 · 10:42',
  },
  {
    id: 'model',
    title: '模型差异分析',
    body: '豆包渠道的品牌提及率领先，但 DeepSeek 的独立来源覆盖更均衡。建议优先补齐制造业决策类内容。',
    provenance: 'ai',
    modified: 'AI 草稿 · 待确认',
  },
  {
    id: 'action',
    title: '优化建议',
    body: '补充私有化部署、权限审计与知识更新机制的权威材料，并在 30 天后复测。',
    provenance: 'human',
    modified: '项目经理 · 昨天',
  },
];
const initialVersions: SectionVersion[] = [
  {
    sectionId: 'published-summary',
    version: 1,
    title: '执行摘要',
    body: '本窗口共获得 36 个有效回答，品牌提及率为 62.2%，较上一窗口提升 3.1 个百分点。',
    savedBy: '分析师 · 林澈',
    savedAt: '2026-07-23 16:20',
  },
  {
    sectionId: 'published-summary',
    version: 2,
    title: '执行摘要',
    body: '本窗口共获得 38 个有效回答，品牌提及率为 68.4%，较上一窗口提升 6.2 个百分点。',
    savedBy: '分析师 · 林澈',
    savedAt: '2026-07-24 10:42',
  },
];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function projectLiveReportTarget(value: ReportDetail): LiveReportTarget | null {
  if (!isRecord(value) || !Array.isArray(value.versions)) return null;
  const reportPubId =
    typeof value.pub_id === 'string' &&
    value.pub_id.length <= 120 &&
    !containsClientSecret(value.pub_id)
      ? value.pub_id
      : '';
  const candidates = value.versions.flatMap((version) => {
    if (!isRecord(version)) return [];
    const versionPubId =
      typeof version.pub_id === 'string' &&
      version.pub_id.length <= 120 &&
      !containsClientSecret(version.pub_id)
        ? version.pub_id
        : '';
    const versionNumber =
      typeof version.version_number === 'number' && Number.isSafeInteger(version.version_number)
        ? version.version_number
        : 0;
    const status =
      typeof version.status === 'string' &&
      version.status.length <= 80 &&
      !containsClientSecret(version.status)
        ? version.status
        : 'unknown';
    return versionPubId ? [{ reportPubId, versionPubId, versionNumber, status }] : [];
  });
  return reportPubId && candidates.length
    ? candidates.sort((left, right) => right.versionNumber - left.versionNumber)[0]!
    : null;
}

function createPreviewPdf() {
  const streams = [
    'BT /F1 22 Tf 64 750 Td (GEO Monitoring Report) Tj /F1 12 Tf 0 -38 Td (Frozen window: 2026-07-01 to 2026-07-21) Tj 0 -26 Td (Brand mention rate: 68.4 percent) Tj 0 -22 Td (Evidence objects: 92) Tj ET',
    'BT /F1 22 Tf 64 750 Td (Optimization and Retest) Tj /F1 12 Tf 0 -38 Td (Priority: private deployment evidence) Tj 0 -26 Td (Retest window: 30 days) Tj 0 -22 Td (Human review required before release) Tj ET',
  ];
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${streams[0]!.length} >>\nstream\n${streams[0]}\nendstream`,
    `<< /Length ${streams[1]!.length} >>\nstream\n${streams[1]}\nendstream`,
  ];
  let pdf = '%PDF-1.4\n';
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(pdf.length);
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets
    .slice(1)
    .map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`)
    .join('');
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return new TextEncoder().encode(pdf);
}

function PdfCanvas({ page, zoom }: { page: number; zoom: 'fit' | '100' }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>('loading');
  useEffect(() => {
    let cancelled = false;
    let destroy: (() => Promise<void>) | undefined;
    setStatus('loading');
    void import('pdfjs-dist')
      .then(async ({ getDocument, GlobalWorkerOptions }) => {
        if (cancelled) return;
        GlobalWorkerOptions.workerSrc = new URL(
          'pdfjs-dist/build/pdf.worker.min.mjs',
          import.meta.url,
        ).toString();
        const task = getDocument({ data: createPreviewPdf() });
        destroy = () => task.destroy();
        const document = await task.promise;
        const pdfPage = await document.getPage(page);
        const viewport = pdfPage.getViewport({ scale: 1.25 });
        const canvas = canvasRef.current;
        if (!canvas || cancelled) return;
        const context = canvas.getContext('2d');
        if (!context) throw new Error('PDF canvas unavailable');
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        await pdfPage.render({ canvas, canvasContext: context, viewport }).promise;
        if (!cancelled) setStatus('ready');
      })
      .catch(() => {
        if (!cancelled) setStatus('failed');
      });
    return () => {
      cancelled = true;
      void destroy?.();
    };
  }, [page]);
  return (
    <div className="pdf-canvas-wrap" data-zoom={zoom}>
      <canvas ref={canvasRef} aria-hidden="true" />
      <span className="sr-only" role="status">
        {status === 'loading'
          ? 'PDF 页面加载中'
          : status === 'ready'
            ? `PDF.js 已渲染第 ${page} 页`
            : 'PDF 页面渲染失败'}
      </span>
    </div>
  );
}

function WindowWorkspace({
  state,
  onFreeze,
  livePage,
  liveState,
}: {
  state: ReportState;
  onFreeze: () => void;
  livePage: ReportPage | null;
  liveState: 'fixture' | 'loading' | 'ready' | 'failed';
}) {
  if (liveState === 'loading') return <StatePanel state="loading" />;
  if (liveState === 'failed') return <StatePanel state="failed" />;
  const liveReports = livePage?.data ?? [];
  return (
    <>
      <MetricGrid
        metrics={[
          {
            label: '冻结样本',
            value:
              liveState === 'ready'
                ? String(liveReports.length)
                : state === 'draft'
                  ? '—'
                  : '1,284',
            detail:
              liveState === 'ready' ? '真实 reports API' : '38 个 eligible 回答 · contract fixture',
          },
          { label: '数据窗口', value: '21 天', detail: '07/01–07/21' },
          { label: '口径版本', value: 'v2.4', detail: 'scorer geo-v4' },
          {
            label: '漂移检查',
            value: state === 'draft' ? '待冻结' : '通过',
            detail: 'input hash 已锁定',
          },
        ]}
      />
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Immutable snapshot</span>
            <h2>数据窗口与事实冻结</h2>
          </div>
          <Badge tone={state === 'draft' ? 'warning' : 'positive'}>
            {state === 'draft' ? '尚未冻结' : '事实已冻结'}
          </Badge>
        </div>
        <div className="freeze-grid">
          <label>
            开始日期
            <input type="date" value="2026-07-01" readOnly />
          </label>
          <label>
            结束日期
            <input type="date" value="2026-07-21" readOnly />
          </label>
          <label>
            Metric version
            <input value="client-metrics-v2.4" readOnly />
          </label>
          <label>
            Scorer version
            <input value="geo-scoring-v4" readOnly />
          </label>
        </div>
        <div className="freeze-summary">
          <div>
            <strong>1,284</strong>
            <span>分析事实</span>
          </div>
          <div>
            <strong>38</strong>
            <span>有效回答</span>
          </div>
          <div>
            <strong>92</strong>
            <span>证据对象</span>
          </div>
          <div>
            <strong>sha256: 7a3f…c91e</strong>
            <span>输入哈希</span>
          </div>
        </div>
        <div className="form-actions">
          <span>冻结后窗口和版本不可原地修改；需创建新报告版本。</span>
          <button className="button" disabled={state !== 'draft'} onClick={onFreeze}>
            {state === 'draft' ? '冻结事实并创建 v0.8' : '已冻结'}
          </button>
        </div>
      </section>
    </>
  );
}

function TraceWorkspace() {
  const [expanded, setExpanded] = useState<string | null>('mention');
  const [openedEvidence, setOpenedEvidence] = useState<string | null>(null);
  const metrics = [
    {
      id: 'mention',
      label: '品牌提及率',
      value: '68.4%',
      numerator: 26,
      denominator: 38,
      contributions: ['ans_01 · 豆包 · +1', 'ans_04 · 豆包 · +1', 'ans_08 · DeepSeek · +1'],
    },
    {
      id: 'top3',
      label: 'Top 3 占比',
      value: '73.7%',
      numerator: 28,
      denominator: 38,
      contributions: ['ans_01 · rank 2', 'ans_03 · rank 1', 'ans_11 · rank 3'],
    },
    {
      id: 'citation',
      label: '引用覆盖',
      value: '55.3%',
      numerator: 21,
      denominator: 38,
      contributions: ['evd_019 · 官网白皮书', 'evd_027 · 工信部指南'],
    },
  ];
  return (
    <section className="panel">
      <span className="overline">Reproducible metrics</span>
      <h2>KPI Trace</h2>
      <p className="panel-subtitle">
        任意数字可下钻到贡献回答、证据和版本；分子、分母及真实 0 分开表达。
      </p>
      <div className="trace-list">
        {metrics.map((metric) => (
          <article key={metric.id}>
            <button
              aria-expanded={expanded === metric.id}
              onClick={() => setExpanded(expanded === metric.id ? null : metric.id)}
            >
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
              <small>
                {metric.numerator} / {metric.denominator}
              </small>
            </button>
            {expanded === metric.id ? (
              <div className="trace-detail">
                <Badge tone="info">metric v2.4</Badge>
                <ul>
                  {metric.contributions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                <button
                  className="button button-secondary"
                  aria-expanded={openedEvidence === metric.id}
                  onClick={() => setOpenedEvidence(openedEvidence === metric.id ? null : metric.id)}
                >
                  {openedEvidence === metric.id ? '关闭贡献证据' : '打开贡献证据'}
                </button>
                {openedEvidence === metric.id ? (
                  <section className="confirmation" aria-label={`${metric.label}贡献证据`}>
                    <Badge tone="positive">证据版本已冻结</Badge>
                    <strong>{metric.contributions[0]}</strong>
                    <span>回答截图、文本锚点与窗口 hash 已绑定；当前只展示安全证据摘要。</span>
                  </section>
                ) : null}
              </div>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}

function EditorWorkspace({
  sections,
  onChange,
  savedVersions,
  onSaveVersion,
}: {
  sections: Section[];
  onChange: (sections: Section[]) => void;
  savedVersions: SectionVersion[];
  onSaveVersion: (version: SectionVersion) => void;
}) {
  const [selectedId, setSelectedId] = useState(sections[0]!.id);
  const selected = sections.find((section) => section.id === selectedId)!;
  const bodyContainsSecret = containsClientSecret(selected.body);
  const selectedVersions = savedVersions.filter((version) => version.sectionId === selectedId);
  const saveVersion = () => {
    if (bodyContainsSecret) return;
    onSaveVersion({
      sectionId: selected.id,
      version: Math.max(0, ...selectedVersions.map((version) => version.version)) + 1,
      title: selected.title,
      body: selected.body,
      savedBy: '当前分析师',
      savedAt: '刚刚',
    });
  };
  const updateBody = (body: string) =>
    onChange(
      sections.map((section) =>
        section.id === selectedId
          ? { ...section, body, provenance: 'human', modified: '当前分析师 · 刚刚' }
          : section,
      ),
    );
  return (
    <div className="editor-layout">
      <aside className="panel section-nav">
        <h2>报告章节</h2>
        {sections.map((section) => (
          <button
            className={section.id === selectedId ? 'selected' : ''}
            key={section.id}
            onClick={() => setSelectedId(section.id)}
          >
            <span>{section.title}</span>
            <Badge tone={section.provenance === 'ai' ? 'info' : 'positive'}>
              {section.provenance === 'ai' ? 'AI 草稿' : '人工修改'}
            </Badge>
          </button>
        ))}
      </aside>
      <section className="panel document-editor">
        <div className="account-head">
          <div>
            <span className="overline">Section editor</span>
            <h2>{selected.title}</h2>
          </div>
          <Badge tone={selected.provenance === 'ai' ? 'info' : 'positive'}>
            {selected.provenance === 'ai' ? 'AI 生成 · 未确认' : '人工内容'}
          </Badge>
        </div>
        <textarea
          aria-label="章节正文"
          value={selected.body}
          onChange={(event) => updateBody(event.target.value)}
        />
        {bodyContainsSecret ? (
          <span className="field-error" role="alert">
            请移除验证码、Cookie、token、密码、完整手机号或 profile 路径后再保存
          </span>
        ) : null}
        <div className="editor-meta">
          <span>{selected.modified}</span>
          <span>{selected.body.length} 字</span>
          <span>已绑定 3 条证据</span>
        </div>
        <div className="button-row">
          {selected.provenance === 'ai' ? (
            <button
              className="button button-secondary"
              disabled={bodyContainsSecret}
              onClick={() => updateBody(selected.body)}
            >
              接受草稿并标记人工确认
            </button>
          ) : null}
          <button className="button" disabled={bodyContainsSecret} onClick={saveVersion}>
            保存章节版本
          </button>
        </div>
        {selectedVersions[0] ? (
          <>
            <Toast>
              {selectedVersions[0].title} v{selectedVersions[0].version} 已保存，正文快照不可变
            </Toast>
            <ol className="version-list" aria-label={`${selected.title}章节版本历史`}>
              {selectedVersions.map((version) => (
                <li key={`${version.sectionId}-${version.version}`}>
                  v{version.version} · {version.body.length} 字 · 人工版本
                </li>
              ))}
            </ol>
          </>
        ) : null}
      </section>
    </div>
  );
}

type DiffChunk = { kind: 'equal' | 'removed' | 'added'; text: string };
function createVersionDiff(before: string, after: string) {
  const rows = Array.from({ length: before.length + 1 }, () => new Uint16Array(after.length + 1));
  for (let left = before.length - 1; left >= 0; left -= 1) {
    for (let right = after.length - 1; right >= 0; right -= 1) {
      rows[left]![right] =
        before[left] === after[right]
          ? rows[left + 1]![right + 1]! + 1
          : Math.max(rows[left + 1]![right]!, rows[left]![right + 1]!);
    }
  }
  const chunks: DiffChunk[] = [];
  const append = (kind: DiffChunk['kind'], text: string) => {
    const last = chunks.at(-1);
    if (last?.kind === kind) last.text += text;
    else chunks.push({ kind, text });
  };
  let left = 0;
  let right = 0;
  while (left < before.length || right < after.length) {
    if (left < before.length && right < after.length && before[left] === after[right]) {
      append('equal', before[left]!);
      left += 1;
      right += 1;
    } else if (
      left < before.length &&
      (right === after.length || rows[left + 1]![right]! >= rows[left]![right + 1]!)
    ) {
      append('removed', before[left]!);
      left += 1;
    } else {
      append('added', after[right]!);
      right += 1;
    }
  }
  return {
    chunks,
    removed: chunks
      .filter((chunk) => chunk.kind === 'removed')
      .reduce((total, chunk) => total + chunk.text.length, 0),
    added: chunks
      .filter((chunk) => chunk.kind === 'added')
      .reduce((total, chunk) => total + chunk.text.length, 0),
  };
}

function VersionDiffWorkspace({ versions }: { versions: SectionVersion[] }) {
  const sectionIds = [...new Set(versions.map((version) => version.sectionId))];
  const [sectionId, setSectionId] = useState(sectionIds[0] ?? '');
  const sectionVersions = versions
    .filter((version) => version.sectionId === sectionId)
    .sort((left, right) => left.version - right.version);
  const [beforeNumber, setBeforeNumber] = useState(sectionVersions[0]?.version ?? 0);
  const [afterNumber, setAfterNumber] = useState(sectionVersions.at(-1)?.version ?? 0);
  const before = sectionVersions.find((version) => version.version === beforeNumber);
  const after = sectionVersions.find((version) => version.version === afterNumber);
  const diff = before && after ? createVersionDiff(before.body, after.body) : null;
  const selectSection = (nextSectionId: string) => {
    const nextVersions = versions
      .filter((version) => version.sectionId === nextSectionId)
      .sort((left, right) => left.version - right.version);
    setSectionId(nextSectionId);
    setBeforeNumber(nextVersions[0]?.version ?? 0);
    setAfterNumber(nextVersions.at(-1)?.version ?? 0);
  };
  return (
    <section className="panel">
      <span className="overline">Immutable comparison</span>
      <h2>章节版本对比</h2>
      <p className="panel-subtitle">
        对比两个不可变正文快照；差异不会改写原版本，也不包含评论、账号或会话材料。
      </p>
      <div className="filter-bar" aria-label="版本对比筛选">
        <label>
          章节
          <select
            aria-label="对比章节"
            value={sectionId}
            onChange={(event) => selectSection(event.target.value)}
          >
            {sectionIds.map((id) => (
              <option key={id} value={id}>
                {versions.find((version) => version.sectionId === id)?.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          基准版本
          <select
            value={beforeNumber}
            onChange={(event) => setBeforeNumber(Number(event.target.value))}
          >
            {sectionVersions.map((version) => (
              <option key={version.version} value={version.version}>
                v{version.version}
              </option>
            ))}
          </select>
        </label>
        <label>
          对比版本
          <select
            value={afterNumber}
            onChange={(event) => setAfterNumber(Number(event.target.value))}
          >
            {sectionVersions.map((version) => (
              <option key={version.version} value={version.version}>
                v{version.version}
              </option>
            ))}
          </select>
        </label>
      </div>
      {before && after && diff ? (
        <>
          <div className="version-compare-meta">
            <article>
              <Badge tone="neutral">基准 v{before.version}</Badge>
              <strong>{before.savedBy}</strong>
              <span>{before.savedAt}</span>
            </article>
            <article>
              <Badge tone="info">对比 v{after.version}</Badge>
              <strong>{after.savedBy}</strong>
              <span>{after.savedAt}</span>
            </article>
          </div>
          <article
            className="version-diff"
            aria-label={`${before.title} v${before.version} 与 v${after.version} 正文差异`}
          >
            <p>
              {diff.chunks.map((chunk, index) =>
                chunk.kind === 'removed' ? (
                  <del key={`${chunk.kind}-${index}`}>{chunk.text}</del>
                ) : chunk.kind === 'added' ? (
                  <ins key={`${chunk.kind}-${index}`}>{chunk.text}</ins>
                ) : (
                  <span key={`${chunk.kind}-${index}`}>{chunk.text}</span>
                ),
              )}
            </p>
          </article>
          <p className="confirmation" role="status">
            {before.version === after.version
              ? '所选版本相同，正文无差异。'
              : `已对比 v${before.version} → v${after.version}；删除 ${diff.removed} 字，新增 ${diff.added} 字。`}
          </p>
        </>
      ) : (
        <StatePanel state="empty" />
      )}
    </section>
  );
}

type AnchorRect = { x: number; y: number; width: number; height: number };
function EvidenceCanvas({ anchor }: { anchor: AnchorRect }) {
  return (
    <div
      className="konva-wrap"
      role="img"
      tabIndex={0}
      aria-label={`回答截图证据，品牌提及锚点位于坐标 ${anchor.x},${anchor.y}，尺寸 ${anchor.width}×${anchor.height}`}
    >
      <Stage width={620} height={330}>
        <Layer>
          <Rect x={0} y={0} width={620} height={330} fill="#f4f7f5" />
          <Text
            x={34}
            y={34}
            width={540}
            text="企业知识库选型建议\n\n需要评估数据权限、检索质量和部署边界。\n云岫 AI 支持私有化部署与审计能力。"
            fontSize={18}
            lineHeight={1.7}
            fill="#24322d"
          />
          <Rect
            x={anchor.x}
            y={anchor.y}
            width={anchor.width}
            height={anchor.height}
            stroke="#d4573f"
            strokeWidth={3}
            fill="#fff4"
          />
          <Text
            x={anchor.x + 5}
            y={anchor.y + anchor.height + 6}
            text={`Anchor #A17 · bbox ${anchor.x},${anchor.y},${anchor.width},${anchor.height}`}
            fontSize={12}
            fill="#9b3929"
          />
        </Layer>
      </Stage>
    </div>
  );
}

function EvidenceWorkspace() {
  const [attached, setAttached] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [anchor, setAnchor] = useState<AnchorRect>({ x: 245, y: 118, width: 238, height: 52 });
  const moveAnchor = (dx: number, dy: number) =>
    setAnchor((current) => ({
      ...current,
      x: Math.max(0, Math.min(620 - current.width, current.x + dx)),
      y: Math.max(0, Math.min(330 - current.height, current.y + dy)),
    }));
  return (
    <div className="evidence-layout">
      <section className="panel">
        <span className="overline">Konva annotation</span>
        <h2>图表与证据编辑</h2>
        <p className="panel-subtitle">
          截图坐标、正文范围和内容哈希同时保存；画布不替代可访问文本说明。
        </p>
        <EvidenceCanvas anchor={anchor} />
        <div className="button-row">
          <button
            className="button button-secondary"
            aria-expanded={adjusting}
            onClick={() => setAdjusting((value) => !value)}
          >
            {adjusting ? '完成锚点调整' : '调整锚点'}
          </button>
          <button className="button" onClick={() => setAttached(true)}>
            绑定到“执行摘要”
          </button>
        </div>
        {adjusting ? (
          <div className="button-row" role="group" aria-label="锚点位置微调">
            <button onClick={() => moveAnchor(0, -8)}>上移</button>
            <button onClick={() => moveAnchor(-8, 0)}>左移</button>
            <button onClick={() => moveAnchor(8, 0)}>右移</button>
            <button onClick={() => moveAnchor(0, 8)}>下移</button>
            <span role="status">
              bbox {anchor.x},{anchor.y},{anchor.width},{anchor.height}
            </span>
          </div>
        ) : null}
      </section>
      <aside className="panel">
        <h2>证据属性</h2>
        <dl className="definition-grid evidence-dl">
          <div>
            <dt>资产</dt>
            <dd>evd_01K0…A17</dd>
          </div>
          <div>
            <dt>类型</dt>
            <dd>回答截图</dd>
          </div>
          <div>
            <dt>文本范围</dt>
            <dd>48–73</dd>
          </div>
          <div>
            <dt>采集时间</dt>
            <dd>2026-07-21 09:42</dd>
          </div>
          <div>
            <dt>完整性</dt>
            <dd>SHA-256 已校验</dd>
          </div>
          <div>
            <dt>章节绑定</dt>
            <dd>{attached ? '执行摘要' : '尚未绑定'}</dd>
          </div>
        </dl>
        {attached ? (
          <div className="confirmation" role="status">
            <Badge tone="positive">绑定成功</Badge>
            <span>章节引用已保存到当前草稿版本。</span>
          </div>
        ) : (
          <StatePanel state="empty" />
        )}
      </aside>
    </div>
  );
}

function PreviewWorkspace({ sections }: { sections: Section[] }) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState<'fit' | '100'>('fit');
  const pages = useMemo(() => [sections.slice(0, 2), sections.slice(2)], [sections]);
  return (
    <section className="preview-layout">
      <div className="preview-toolbar">
        <Badge tone="positive">PDF.js canvas</Badge>
        <button disabled={page === 1} onClick={() => setPage(page - 1)}>
          上一页
        </button>
        <span>
          {page} / {pages.length}
        </span>
        <button disabled={page === pages.length} onClick={() => setPage(page + 1)}>
          下一页
        </button>
        <button aria-pressed={zoom === 'fit'} onClick={() => setZoom('fit')}>
          适合页面
        </button>
        <button aria-pressed={zoom === '100'} onClick={() => setZoom('100')}>
          100%
        </button>
      </div>
      <PdfCanvas page={page} zoom={zoom} />
      <article className="pdf-page pdf-accessible-copy" aria-label={`报告预览第 ${page} 页`}>
        <header>
          <span>GEO Platform</span>
          <small>2026 Q3 监测报告 · v0.8</small>
        </header>
        <h1>{page === 1 ? 'GEO 监测与优化建议' : '优化行动与复测'}</h1>
        {pages[page - 1]!.map((section) => (
          <section key={section.id}>
            <h2>{section.title}</h2>
            <p>{section.body}</p>
            <div className="mini-chart">
              <span style={{ width: section.id === 'summary' ? '68%' : '55%' }} />
            </div>
          </section>
        ))}
        <footer>冻结窗口 2026-07-01—2026-07-21 · 第 {page} 页</footer>
      </article>
    </section>
  );
}

function ReviewWorkspace({
  state,
  onState,
  liveTarget,
}: {
  state: ReportState;
  onState: (state: ReportState) => void;
  liveTarget?: LiveReportTarget | null;
}) {
  const [comment, setComment] = useState('');
  const [comments, setComments] = useState(['请确认 Top 3 的分母是否排除了 degraded 样本。']);
  const [writeState, setWriteState] = useState<'idle' | 'saving' | 'failed'>('idle');
  const [receipt, setReceipt] = useState('');
  const commentContainsSecret = containsClientSecret(comment);
  const gates = {
    facts: true,
    evidence: true,
    aiReviewed: true,
    commentsResolved: comments.length === 0,
  };
  const allPassed = Object.values(gates).every(Boolean);
  const headers = liveTarget ? getValidatedIdentityHeaders() : null;
  const saveReview = async () => {
    if (!liveTarget || !headers) {
      onState('approved');
      return;
    }
    setWriteState('saving');
    const result = await reviewReport(
      liveTarget.reportPubId,
      liveTarget.versionPubId,
      { decision: 'approved', rationale: '事实、证据、AI 草稿与评论门均已人工核验。' },
      headers,
    );
    if (result.kind === 'ready') {
      onState('approved');
      setReceipt('真实审核决定已记录');
      setWriteState('idle');
    } else {
      setWriteState('failed');
    }
  };
  const publish = async () => {
    if (!liveTarget || !headers) {
      onState('published');
      return;
    }
    setWriteState('saving');
    const result = await publishReport(liveTarget.reportPubId, liveTarget.versionPubId, headers);
    if (result.kind === 'ready') {
      onState('published');
      setReceipt('真实发布操作已完成');
      setWriteState('idle');
    } else {
      setWriteState('failed');
    }
  };
  const addComment = async () => {
    const body = comment.trim();
    if (liveTarget && headers) {
      setWriteState('saving');
      const result = await commentOnReport(
        liveTarget.reportPubId,
        liveTarget.versionPubId,
        { body, parent_pub_id: null },
        headers,
      );
      if (result.kind !== 'ready') {
        setWriteState('failed');
        return;
      }
      setReceipt('真实审核评论已记录');
      setWriteState('idle');
    }
    setComments((current) => [...current, body]);
    setComment('');
  };
  return (
    <div className="review-layout">
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Release gates</span>
            <h2>审核与发布门</h2>
          </div>
          <Badge tone={state === 'published' ? 'positive' : 'warning'}>{state}</Badge>
          {liveTarget ? <Badge tone="positive">真实 reports API</Badge> : null}
        </div>
        <ul className="gate-list">
          <li data-pass={gates.facts}>事实窗口已冻结</li>
          <li data-pass={gates.evidence}>KPI 与章节证据齐全</li>
          <li data-pass={gates.aiReviewed}>AI 草稿已人工确认</li>
          <li data-pass={gates.commentsResolved}>审核评论已解决</li>
        </ul>
        <div className="button-row">
          <button
            className="button button-secondary"
            disabled={state !== 'frozen'}
            onClick={() => onState('review')}
          >
            提交审核
          </button>
          <button
            className="button button-secondary"
            disabled={state !== 'review' || !allPassed}
            onClick={() => void saveReview()}
          >
            批准发布
          </button>
          <button
            className="button"
            disabled={state !== 'approved' || writeState === 'saving'}
            onClick={() => void publish()}
          >
            发布 v1.0
          </button>
        </div>
        {state === 'published' ? (
          <div className="confirmation" role="status">
            <Badge tone="positive">已发布</Badge>
            <span>在线版与交付记录已生成，客户可见。</span>
          </div>
        ) : null}
        {receipt ? <Toast>{receipt}</Toast> : null}
        {writeState === 'failed' ? <StatePanel state="failed" /> : null}
      </section>
      <aside className="panel">
        <h2>审核评论</h2>
        <div className="comment-list">
          {comments.map((item) => (
            <article key={item}>
              <p>{item}</p>
              <button
                onClick={() => setComments((current) => current.filter((value) => value !== item))}
              >
                标记已解决
              </button>
            </article>
          ))}
        </div>
        <label className="form-field" htmlFor="review-comment">
          <span>新增评论</span>
          <textarea
            id="review-comment"
            rows={3}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
          {commentContainsSecret ? (
            <span className="field-error" role="alert">
              请勿在评论中粘贴验证码、Cookie、token、密码、完整手机号或 profile 路径
            </span>
          ) : null}
        </label>
        <button
          className="button button-secondary"
          disabled={comment.trim().length < 4 || commentContainsSecret || writeState === 'saving'}
          onClick={() => void addComment()}
        >
          添加评论
        </button>
      </aside>
    </div>
  );
}

function OutcomesWorkspace() {
  const [status, setStatus] = useState<'planned' | 'running' | 'reviewed'>('planned');
  return (
    <>
      <MetricGrid
        metrics={[
          { label: '建议', value: '6', detail: '高优先级 2' },
          { label: '执行中', value: status === 'running' ? '1' : '0', detail: '负责人已分配' },
          { label: '复测窗口', value: '30 天', detail: '最小到日' },
          {
            label: '效果 Delta',
            value: status === 'reviewed' ? '+6.2pp' : '—',
            detail: '复测后可用',
          },
        ]}
      />
      <section className="panel">
        <h2>优化建议与效果复盘</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>建议</th>
              <th>负责人</th>
              <th>状态</th>
              <th>复测</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>补齐私有化部署权威材料</td>
              <td>内容负责人 · 周岚</td>
              <td>
                <Badge tone={status === 'planned' ? 'warning' : 'positive'}>{status}</Badge>
              </td>
              <td>2026-08-21</td>
            </tr>
          </tbody>
        </table>
        <WorkflowTimeline
          label="建议执行与复测进度"
          steps={[
            { id: 'plan', label: '建议已确认', state: 'completed', detail: '2026-07-24' },
            {
              id: 'execute',
              label: '内容优化',
              state: status === 'planned' ? 'scheduled' : 'completed',
              detail: '负责人 周岚',
            },
            {
              id: 'retest',
              label: '30 天复测',
              state: status === 'reviewed' ? 'completed' : 'scheduled',
              detail: '2026-08-21',
            },
          ]}
        />
        <div className="button-row">
          <button className="button button-secondary" onClick={() => setStatus('running')}>
            开始执行
          </button>
          <button
            className="button"
            disabled={status !== 'running'}
            onClick={() => setStatus('reviewed')}
          >
            记录复测效果
          </button>
        </div>
      </section>
    </>
  );
}

export default function Shell() {
  const experience = useOptionalExperienceContext();
  const [state, setState] = useState<ReportState>('draft');
  const [sections, setSections] = useState(initialSections);
  const [savedVersions, setSavedVersions] = useState(initialVersions);
  const [livePage, setLivePage] = useState<ReportPage | null>(null);
  const [liveTarget, setLiveTarget] = useState<LiveReportTarget | null>(null);
  const [liveState, setLiveState] = useState<'fixture' | 'loading' | 'ready' | 'failed'>(
    experience?.source === 'live' ? 'loading' : 'fixture',
  );
  useEffect(() => {
    if (experience?.source !== 'live') {
      setLiveState('fixture');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveState('failed');
      return;
    }
    setLiveState('loading');
    void listReports(headers).then(async (result) => {
      if (result.kind === 'ready') {
        setLivePage(result.data);
        const reportPubId = result.data.data[0]?.pub_id;
        if (reportPubId) {
          const detail = await getReport(reportPubId, headers);
          if (detail.kind !== 'ready') {
            setLiveTarget(null);
            setLiveState('failed');
            return;
          }
          const target = projectLiveReportTarget(detail.data);
          setLiveTarget(target);
          if (target) {
            setState(
              target.status === 'published'
                ? 'published'
                : target.status === 'approved'
                  ? 'approved'
                  : 'frozen',
            );
          }
        }
        setLiveState('ready');
      } else {
        setLivePage(null);
        setLiveTarget(null);
        setLiveState('failed');
      }
    });
  }, [experience]);
  return (
    <ProductShell
      product="Report Studio"
      title="报告工作室"
      description="冻结事实窗口，编辑可追溯章节，并通过审核门发布。"
      probe={getHealth}
      nav={nav}
    >
      {(active) =>
        active === 'window' ? (
          <WindowWorkspace
            state={state}
            onFreeze={() => setState('frozen')}
            livePage={livePage}
            liveState={liveState}
          />
        ) : active === 'review' && experience?.source === 'live' ? (
          liveState === 'loading' ? (
            <StatePanel state="loading" />
          ) : liveState === 'failed' ? (
            <StatePanel state="failed" />
          ) : liveTarget ? (
            <ReviewWorkspace state={state} onState={setState} liveTarget={liveTarget} />
          ) : (
            <StatePanel state="empty" />
          )
        ) : experience?.source === 'live' ? (
          <StatePanel state="insufficient" />
        ) : active === 'trace' ? (
          <TraceWorkspace />
        ) : active === 'editor' ? (
          <EditorWorkspace
            sections={sections}
            onChange={setSections}
            savedVersions={savedVersions}
            onSaveVersion={(version) =>
              setSavedVersions((current) => [
                version,
                ...current.filter(
                  (item) =>
                    item.sectionId !== version.sectionId || item.version !== version.version,
                ),
              ])
            }
          />
        ) : active === 'diff' ? (
          <VersionDiffWorkspace versions={savedVersions} />
        ) : active === 'evidence' ? (
          <EvidenceWorkspace />
        ) : active === 'preview' ? (
          <PreviewWorkspace sections={sections} />
        ) : active === 'review' ? (
          <ReviewWorkspace state={state} onState={setState} />
        ) : (
          <OutcomesWorkspace />
        )
      }
    </ProductShell>
  );
}
