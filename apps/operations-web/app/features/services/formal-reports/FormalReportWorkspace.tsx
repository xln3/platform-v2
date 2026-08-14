import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  defaultWindow,
  servicesApi,
  type FormalReportArtifact,
  type FormalReportCreatableDocumentStatus,
  type FormalReportOutput,
  type FormalReportProduction,
  type FormalReportReviewDecision,
  type FormalReportService,
  type FormalReportWindow,
  type Project,
  type SessionContext,
} from '../api';

const SERVICE_OPTIONS: ReadonlyArray<{
  value: FormalReportService;
  title: string;
  description: string;
}> = [
  { value: 1, title: '品牌 GEO 推荐结果评测', description: '提及、推荐位次、竞品与信源结构' },
  { value: 2, title: '内容生态风险核查', description: 'AI 回答与公开信源的拉踩风险证据链' },
  { value: 3, title: '官网引用能效评估', description: '官网引用、内容复用下界与逐回答证据链' },
  { value: 4, title: 'GEO 试点与效果验证', description: '试点方案及同矩阵 before / after 对比' },
];

const STATUS_LABELS: Record<FormalReportProduction['status'], string> = {
  queued: '排队中',
  running: '生成中',
  failed: '失败',
  awaiting_review: '待审阅',
  signed: '已签发',
};

const DOCUMENT_STATUS_LABELS: Record<FormalReportProduction['document_status'], string> = {
  pre_formal: '历史预正式稿',
  formal: '历史正式候选稿',
  internal_review: '内部审核稿',
  delivery_candidate: '客户交付候选稿',
  approved_signed: '已批准签发版',
};

const TERMINAL_STATUSES = new Set<FormalReportProduction['status']>([
  'failed',
  'awaiting_review',
  'signed',
]);

type LoadState = 'loading' | 'ready' | 'failed';

function isoDateOffset(value: string, days: number): string {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function initialPilotWindows(): { before: FormalReportWindow; after: FormalReportWindow } {
  const after = defaultWindow(30);
  return {
    before: { start: isoDateOffset(after.start, -30), end: isoDateOffset(after.end, -30) },
    after,
  };
}

function idempotencyKey(): string {
  const suffix =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `formal-report-${suffix}`;
}

function currentCstDate(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
}

function artifactLabel(artifact: FormalReportArtifact): string {
  const format = artifact.format.toLowerCase();
  if (format.includes('docx')) return 'DOCX';
  if (format.includes('pdf')) return 'PDF';
  if (format.includes('manifest')) return '审计清单';
  if (format.includes('facts') || format.includes('json')) return '事实快照';
  return artifact.format.toUpperCase();
}

function artifactHref(value: string): string | null {
  return value.startsWith('/api/v2/reports/formal-productions/') && !value.includes('\\')
    ? value
    : null;
}

function readableBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return '—';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function outputArtifacts(production: FormalReportProduction): Array<{
  output: FormalReportOutput;
  artifact: FormalReportArtifact;
}> {
  return production.outputs.flatMap((output) =>
    output.artifacts.map((artifact) => ({ output, artifact })),
  );
}

export function FormalReportWorkspace({
  session,
  project,
}: {
  session: SessionContext;
  project: Project;
}) {
  const [services, setServices] = useState<FormalReportService[]>([1, 2, 3, 4]);
  const [window_, setWindow] = useState<FormalReportWindow>(() => defaultWindow(30));
  const [pilotWindows, setPilotWindows] = useState(initialPilotWindows);
  const [documentStatus, setDocumentStatus] =
    useState<FormalReportCreatableDocumentStatus>('internal_review');
  const [version, setVersion] = useState('V1.0');
  const [preparedBy, setPreparedBy] = useState(session.actorId);
  const [preparedDate, setPreparedDate] = useState(currentCstDate);
  const [reviewedBy, setReviewedBy] = useState('');
  const [reviewedDate, setReviewedDate] = useState('');
  const [productions, setProductions] = useState<FormalReportProduction[]>([]);
  const [state, setState] = useState<LoadState>('loading');
  const [busy, setBusy] = useState(false);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [reviewRationales, setReviewRationales] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);

  const includesService4 = services.includes(4);
  const hasActiveProduction = productions.some((item) => !TERMINAL_STATUSES.has(item.status));
  const canProduce =
    session.role === 'operator' || session.role === 'analyst' || session.role === 'admin';
  const canReview = session.role === 'reviewer' || session.role === 'admin';

  const validationError = useMemo(() => {
    if (services.length === 0) return '请至少选择一项服务。';
    if (!/^V[1-9]\d*\.\d+$/.test(version)) return '版本号须使用 V1.0 形式。';
    if (!preparedBy.trim() || !preparedDate) return '请填写编制人和编制日期。';
    if (documentStatus === 'delivery_candidate' && (!reviewedBy.trim() || !reviewedDate))
      return '客户交付候选稿必须先填写复核人和复核日期。';
    if (!window_.start || !window_.end || window_.start > window_.end)
      return '请选择有效的事实冻结窗口。';
    if (!includesService4) return null;
    const { before, after } = pilotWindows;
    if (!before.start || !before.end || before.start > before.end)
      return '服务 4 的优化前窗口无效。';
    if (!after.start || !after.end || after.start > after.end) return '服务 4 的优化后窗口无效。';
    if (before.end >= after.start) return '服务 4 的优化前、优化后窗口必须按时间分离。';
    return null;
  }, [
    documentStatus,
    includesService4,
    pilotWindows,
    preparedBy,
    preparedDate,
    reviewedBy,
    reviewedDate,
    services.length,
    version,
    window_,
  ]);

  const refresh = useCallback(async () => {
    try {
      const items = await servicesApi.formalReportProductions(session, {
        projectPubId: project.pub_id,
        limit: 50,
      });
      setProductions(items);
      setState('ready');
    } catch (error) {
      setState('failed');
      setNotice(error instanceof Error ? error.message : 'formal_report_list_failed');
    }
  }, [project.pub_id, session]);

  useEffect(() => {
    setState('loading');
    setProductions([]);
    setNotice(null);
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!hasActiveProduction) return;
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => window.clearInterval(timer);
  }, [hasActiveProduction, refresh]);

  function toggleService(service: FormalReportService, checked: boolean) {
    setServices((current) =>
      checked
        ? [...new Set([...current, service])].sort()
        : current.filter((value) => value !== service),
    );
  }

  async function createProduction() {
    if (validationError || busy || !canProduce) return;
    setBusy(true);
    setNotice(null);
    try {
      const created = await servicesApi.createFormalReportProduction(session, {
        projectPubId: project.pub_id,
        services,
        window: window_,
        documentStatus,
        version,
        preparedBy: preparedBy.trim(),
        preparedDate,
        ...(reviewedBy.trim() ? { reviewedBy: reviewedBy.trim() } : {}),
        ...(reviewedDate ? { reviewedDate } : {}),
        ...(includesService4
          ? { beforeWindow: pilotWindows.before, afterWindow: pilotWindows.after }
          : {}),
        idempotencyKey: idempotencyKey(),
      });
      setProductions((current) => [
        created,
        ...current.filter((item) => item.pub_id !== created.pub_id),
      ]);
      setNotice(`生产请求 ${created.pub_id} 已进入${STATUS_LABELS[created.status]}状态。`);
      await refresh();
    } catch (error) {
      setNotice(`启动失败：${error instanceof Error ? error.message : 'unknown_error'}`);
    } finally {
      setBusy(false);
    }
  }

  async function reviewProduction(
    production: FormalReportProduction,
    decision: FormalReportReviewDecision,
  ) {
    const rationale = (reviewRationales[production.pub_id] ?? '').trim();
    if (!canReview || reviewing || rationale.length === 0) return;
    setReviewing(production.pub_id);
    setNotice(null);
    try {
      await servicesApi.reviewFormalReportProduction(session, {
        productionPubId: production.pub_id,
        decision,
        rationale,
        idempotencyKey: idempotencyKey(),
      });
      setNotice(
        decision === 'approved'
          ? '审阅批准已提交，Temporal 正在执行签发。'
          : '修改意见已提交，Temporal 正在关闭本次生产。',
      );
      window.setTimeout(() => void refresh(), 1_000);
    } catch (error) {
      setNotice(`审阅提交失败：${error instanceof Error ? error.message : 'unknown_error'}`);
    } finally {
      setReviewing(null);
    }
  }

  return (
    <>
      <section className="execution-card formal-report-launcher">
        <div className="section-title">
          <h2>启动受治理报告生产</h2>
          <span>冻结事实 → 内部审核/交付候选 → 人工批准 → 签发</span>
        </div>
        <p className="service-note">
          报告从平台冻结事实动态构建，并把主报告、样本索引、证据包与 manifest
          保存到同一生产记录。内部审核稿不可签发；客户交付候选稿仍须人工批准后才会重渲染为签发版。
        </p>

        <fieldset className="formal-service-picker">
          <legend>1. 选择服务</legend>
          {SERVICE_OPTIONS.map((option) => (
            <label key={option.value}>
              <input
                type="checkbox"
                checked={services.includes(option.value)}
                onChange={(event) => toggleService(option.value, event.target.checked)}
              />
              <span>
                <strong>
                  服务 {option.value} · {option.title}
                </strong>
                <small>{option.description}</small>
              </span>
            </label>
          ))}
        </fieldset>

        <div className="formal-window-grid">
          <label>
            2. 事实窗口开始
            <input
              type="date"
              value={window_.start}
              onChange={(event) =>
                setWindow((current) => ({ ...current, start: event.target.value }))
              }
            />
          </label>
          <label>
            事实窗口结束
            <input
              type="date"
              value={window_.end}
              onChange={(event) =>
                setWindow((current) => ({ ...current, end: event.target.value }))
              }
            />
          </label>
          <label>
            3. 文档状态
            <select
              value={documentStatus}
              onChange={(event) =>
                setDocumentStatus(event.target.value as FormalReportCreatableDocumentStatus)
              }
            >
              <option value="internal_review">内部审核稿 · 不可签发</option>
              <option value="delivery_candidate">客户交付候选稿 · 待人工批准</option>
            </select>
          </label>
          <label>
            4. 版本
            <input value={version} onChange={(event) => setVersion(event.target.value)} />
          </label>
          <label>
            编制人
            <input value={preparedBy} onChange={(event) => setPreparedBy(event.target.value)} />
          </label>
          <label>
            编制日期（中国标准时间）
            <input
              type="date"
              value={preparedDate}
              onChange={(event) => setPreparedDate(event.target.value)}
            />
          </label>
          {documentStatus === 'delivery_candidate' ? (
            <>
              <label>
                复核人
                <input value={reviewedBy} onChange={(event) => setReviewedBy(event.target.value)} />
              </label>
              <label>
                复核日期（中国标准时间）
                <input
                  type="date"
                  value={reviewedDate}
                  onChange={(event) => setReviewedDate(event.target.value)}
                />
              </label>
            </>
          ) : null}
        </div>

        {includesService4 ? (
          <fieldset className="formal-pilot-windows">
            <legend>5. 服务 4 同矩阵对比窗口</legend>
            <label>
              优化前开始
              <input
                type="date"
                value={pilotWindows.before.start}
                onChange={(event) =>
                  setPilotWindows((current) => ({
                    ...current,
                    before: { ...current.before, start: event.target.value },
                  }))
                }
              />
            </label>
            <label>
              优化前结束
              <input
                type="date"
                value={pilotWindows.before.end}
                onChange={(event) =>
                  setPilotWindows((current) => ({
                    ...current,
                    before: { ...current.before, end: event.target.value },
                  }))
                }
              />
            </label>
            <label>
              优化后开始
              <input
                type="date"
                value={pilotWindows.after.start}
                onChange={(event) =>
                  setPilotWindows((current) => ({
                    ...current,
                    after: { ...current.after, start: event.target.value },
                  }))
                }
              />
            </label>
            <label>
              优化后结束
              <input
                type="date"
                value={pilotWindows.after.end}
                onChange={(event) =>
                  setPilotWindows((current) => ({
                    ...current,
                    after: { ...current.after, end: event.target.value },
                  }))
                }
              />
            </label>
            <p>
              两臂必须使用相同问题矩阵、平台、模式、地域、账号策略、重复次数、指标与抽取版本；不一致时报告会显著标记为不可直接归因。
            </p>
          </fieldset>
        ) : null}

        {validationError ? (
          <p className="launcher-error" role="alert">
            {validationError}
          </p>
        ) : null}
        <div className="actions">
          <button
            type="button"
            disabled={!canProduce || busy || validationError !== null}
            onClick={() => void createProduction()}
          >
            {!canProduce ? '当前角色仅可查看/审阅' : busy ? '正在创建…' : '冻结事实并启动生成'}
          </button>
          <a href="/platform/reports/">前往报告工作室审阅与签发</a>
        </div>
        {notice ? (
          <p className={notice.startsWith('启动失败') ? 'launcher-error' : 'receipt'} role="status">
            {notice}
          </p>
        ) : null}
      </section>

      <section className="execution-card formal-production-list">
        <div className="section-title">
          <h2>生产记录与下载</h2>
          <span>
            {hasActiveProduction ? '存在进行中任务，每 15 秒自动刷新' : '已显示最近 50 条'}
          </span>
        </div>
        {state === 'loading' ? (
          <p className="empty">正在加载生产记录…</p>
        ) : state === 'failed' ? (
          <p className="empty">
            生产记录加载失败。<button onClick={() => void refresh()}>重试</button>
          </p>
        ) : productions.length === 0 ? (
          <p className="empty">当前项目还没有正式报告生产记录。</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>创建时间</th>
                  <th>服务</th>
                  <th>窗口</th>
                  <th>状态</th>
                  <th>产物</th>
                  <th>事实哈希</th>
                </tr>
              </thead>
              <tbody>
                {productions.map((production) => (
                  <tr key={production.pub_id}>
                    <td data-label="创建时间">
                      {new Date(production.created_at).toLocaleString('zh-CN', { hour12: false })}
                      <small>{production.pub_id}</small>
                    </td>
                    <td data-label="服务">
                      {production.services.map((service) => `服务 ${service}`).join('、')}
                    </td>
                    <td data-label="窗口">
                      {production.window_start} ~ {production.window_end}
                      {production.before_window && production.after_window ? (
                        <small>
                          before {production.before_window.start} ~ {production.before_window.end}
                          <br />
                          after {production.after_window.start} ~ {production.after_window.end}
                        </small>
                      ) : null}
                    </td>
                    <td data-label="状态">
                      <span
                        className={`status ${
                          production.status === 'failed'
                            ? 'bad'
                            : production.status === 'signed'
                              ? 'ok'
                              : 'warn'
                        }`}
                      >
                        {STATUS_LABELS[production.status]}
                      </span>
                      <small>{DOCUMENT_STATUS_LABELS[production.document_status]}</small>
                      {production.error_code ? <small>错误：{production.error_code}</small> : null}
                      {canReview && production.status === 'awaiting_review' ? (
                        <div className="formal-review-actions">
                          <label>
                            审阅意见
                            <input
                              aria-label={`审阅意见 ${production.pub_id}`}
                              value={reviewRationales[production.pub_id] ?? ''}
                              maxLength={1000}
                              onChange={(event) =>
                                setReviewRationales((current) => ({
                                  ...current,
                                  [production.pub_id]: event.target.value,
                                }))
                              }
                            />
                          </label>
                          {production.document_status === 'delivery_candidate' ? (
                            <button
                              type="button"
                              disabled={
                                reviewing !== null ||
                                !(reviewRationales[production.pub_id] ?? '').trim()
                              }
                              onClick={() => void reviewProduction(production, 'approved')}
                            >
                              批准签发
                            </button>
                          ) : (
                            <small>只有客户交付候选稿可提交人工批准；内部审核稿不可签发。</small>
                          )}
                          <button
                            type="button"
                            className="secondary"
                            disabled={
                              reviewing !== null ||
                              !(reviewRationales[production.pub_id] ?? '').trim()
                            }
                            onClick={() => void reviewProduction(production, 'changes_requested')}
                          >
                            退回修改
                          </button>
                        </div>
                      ) : null}
                    </td>
                    <td data-label="产物">
                      {outputArtifacts(production).length === 0 ? (
                        '—'
                      ) : (
                        <div className="formal-artifacts">
                          {outputArtifacts(production).map(({ output, artifact }) => {
                            const href = artifactHref(artifact.download_url);
                            return href ? (
                              <a key={`${output.service_number}-${artifact.format}`} href={href}>
                                服务 {output.service_number} {artifactLabel(artifact)}
                                <small>
                                  {readableBytes(artifact.byte_size)} ·{' '}
                                  {artifact.sha256.slice(0, 12)}…
                                </small>
                              </a>
                            ) : (
                              <span key={`${output.service_number}-${artifact.format}`}>
                                服务 {output.service_number} {artifactLabel(artifact)}
                                （下载地址无效）
                              </span>
                            );
                          })}
                        </div>
                      )}
                    </td>
                    <td data-label="事实哈希">
                      {production.fact_snapshot_hash
                        ? `${production.fact_snapshot_hash.slice(0, 16)}…`
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
