import { CursorPagination } from '@geo/design-system';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { PAGE_SIZE, useCursorCollection } from '../../../pagination';
import {
  defaultWindow,
  servicesApi,
  type FormalReportArtifact,
  type FormalReportCreatableDocumentStatus,
  type FormalReportOutput,
  type FormalReportProduction,
  type FormalReportReviewDecision,
  type FormalReportService,
  type FormalReportServiceCatalogVersion,
  type FormalReportWindow,
  type Project,
  type SessionContext,
} from '../api';

const SERVICE_OPTIONS: ReadonlyArray<{
  value: FormalReportService;
  title: string;
  description: string;
}> = [
  {
    value: 1,
    title: '测试',
    description: '排名效果与实际留证采集渠道之间的同题观测差异',
  },
  {
    value: 2,
    title: '找拉踩帖',
    description: '核验冻结范围内全部 U 信源帖的实体方向、逐字证据与独立归属',
  },
  {
    value: 3,
    title: '找被拉踩帖',
    description: '核验公开信源中针对己方品牌的拉踩证据链',
  },
  {
    value: 4,
    title: '官网分析',
    description: '官网引用 URL、内容复用下界与逐回答证据链',
  },
  {
    value: 5,
    title: '发帖提排名',
    description: '发布试点与同矩阵 before / after 复测，不预设提升',
  },
];

const LEGACY_SERVICE_TITLES: Readonly<Record<1 | 2 | 3 | 4, string>> = {
  1: '品牌 GEO 推荐结果评测',
  2: '内容生态风险核查',
  3: '官网引用能效评估',
  4: 'GEO 试点与效果验证',
};

function serviceLabel(
  catalogVersion: FormalReportServiceCatalogVersion,
  service: FormalReportService,
): string {
  if (catalogVersion === 'legacy_report_services_v1') {
    const title = service <= 4 ? LEGACY_SERVICE_TITLES[service as 1 | 2 | 3 | 4] : undefined;
    return title ? `服务 ${service} · ${title}` : `服务 ${service}`;
  }
  const title = SERVICE_OPTIONS.find((option) => option.value === service)?.title;
  return title ? `服务 ${service} · ${title}` : `服务 ${service}`;
}

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
  const [services, setServices] = useState<FormalReportService[]>([1, 2, 3, 4, 5]);
  const [window_, setWindow] = useState<FormalReportWindow>(() => defaultWindow(30));
  const [pilotWindows, setPilotWindows] = useState(initialPilotWindows);
  const [sopProjectPubId, setSopProjectPubId] = useState('');
  const [documentStatus, setDocumentStatus] =
    useState<FormalReportCreatableDocumentStatus>('internal_review');
  const [version, setVersion] = useState('V1.0');
  const [preparedBy, setPreparedBy] = useState(session.actorId);
  const [preparedDate, setPreparedDate] = useState(currentCstDate);
  const [reviewedBy, setReviewedBy] = useState('');
  const [reviewedDate, setReviewedDate] = useState('');
  const [productionVersion, setProductionVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [reviewRationales, setReviewRationales] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);

  const includesService5 = services.includes(5);
  const includesSopBoundService = includesService5;
  const loadProductions = useCallback(
    (cursor?: string) =>
      servicesApi.formalReportProductions(session, {
        projectPubId: project.pub_id,
        ...(cursor ? { cursor } : {}),
        limit: PAGE_SIZE,
      }),
    [project.pub_id, session],
  );
  const productionsPage = useCursorCollection(
    loadProductions,
    `${project.pub_id}:${productionVersion}`,
  );
  const productions = productionsPage.data;
  const hasActiveProduction = productions.some((item) => !TERMINAL_STATUSES.has(item.status));
  const canProduce =
    session.role === 'operator' || session.role === 'analyst' || session.role === 'admin';
  const canReview = session.role === 'reviewer' || session.role === 'admin';

  const validationError = useMemo(() => {
    if (services.length === 0) return '请至少选择一项服务。';
    if (!/^V[1-9]\d*\.\d+$/.test(version)) return '版本号须使用 V1.0 形式。';
    if (!preparedBy.trim() || !preparedDate) return '请填写编制人和编制日期。';
    if (includesSopBoundService && !sopProjectPubId.trim())
      return '服务 5 需要填写用于审批与发布证据核验的 SOP 项目 ID。';
    if (documentStatus === 'delivery_candidate' && (!reviewedBy.trim() || !reviewedDate))
      return '客户交付候选稿必须先填写复核人和复核日期。';
    if (!window_.start || !window_.end || window_.start > window_.end)
      return '请选择有效的事实冻结窗口。';
    if (!includesService5) return null;
    const { before, after } = pilotWindows;
    if (!before.start || !before.end || before.start > before.end)
      return '服务 5 的发布前窗口无效。';
    if (!after.start || !after.end || after.start > after.end) return '服务 5 的发布后窗口无效。';
    if (before.end >= after.start) return '服务 5 的发布前、发布后窗口必须按时间分离。';
    return null;
  }, [
    documentStatus,
    includesService5,
    includesSopBoundService,
    pilotWindows,
    preparedBy,
    preparedDate,
    reviewedBy,
    reviewedDate,
    services.length,
    sopProjectPubId,
    version,
    window_,
  ]);

  useEffect(() => {
    if (!hasActiveProduction) return;
    const timer = window.setInterval(() => void productionsPage.refresh(true), 15_000);
    return () => window.clearInterval(timer);
  }, [hasActiveProduction, productionsPage.refresh]);

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
        serviceCatalogVersion: 'quotation_services_v2',
        ...(includesSopBoundService ? { sopProjectPubId: sopProjectPubId.trim() } : {}),
        window: window_,
        documentStatus,
        version,
        preparedBy: preparedBy.trim(),
        preparedDate,
        ...(reviewedBy.trim() ? { reviewedBy: reviewedBy.trim() } : {}),
        ...(reviewedDate ? { reviewedDate } : {}),
        ...(includesService5
          ? { beforeWindow: pilotWindows.before, afterWindow: pilotWindows.after }
          : {}),
        idempotencyKey: idempotencyKey(),
      });
      setNotice(`生产请求 ${created.pub_id} 已进入${STATUS_LABELS[created.status]}状态。`);
      setProductionVersion((current) => current + 1);
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
      window.setTimeout(() => void productionsPage.refresh(true), 1_000);
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
          所选服务共用一份冻结事实，但每项服务独立生成自己的 DOCX、样本索引、证据包与
          manifest，不拼成一份跨服务报告。内部审核稿不可签发；客户交付候选稿仍须人工批准后才会重渲染为签发版。
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

        {includesSopBoundService ? (
          <label className="formal-sop-project">
            服务 5 内容 SOP 项目 ID
            <input
              aria-label="服务 5 内容 SOP 项目 ID"
              value={sopProjectPubId}
              placeholder="spr_…"
              onChange={(event) => setSopProjectPubId(event.target.value)}
            />
            <small>
              仅服务 5 用它核验稿件审批与公开发布记录；服务 2 从冻结的全 U corpus manifest
              读取事实。
            </small>
          </label>
        ) : null}

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

        {includesService5 ? (
          <fieldset className="formal-pilot-windows">
            <legend>5. 服务 5 同矩阵对比窗口</legend>
            <label>
              发布前开始
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
              发布前结束
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
              发布后开始
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
              发布后结束
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
            {hasActiveProduction
              ? '当前页存在进行中任务，每 15 秒自动刷新'
              : `每页最多 ${PAGE_SIZE} 条`}
          </span>
        </div>
        {productionsPage.state === 'loading' ? (
          <p className="empty">正在加载生产记录…</p>
        ) : productionsPage.state === 'failed' ? (
          <p className="empty">
            生产记录加载失败。
            <button onClick={() => void productionsPage.refresh()}>重试</button>
          </p>
        ) : productions.length === 0 ? (
          <p className="empty">当前项目还没有正式报告生产记录。</p>
        ) : (
          <>
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
                        {production.services
                          .map((service) =>
                            serviceLabel(production.service_catalog_version, service),
                          )
                          .join('、')}
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
                        {production.error_code ? (
                          <small>错误：{production.error_code}</small>
                        ) : null}
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
                                  {serviceLabel(
                                    production.service_catalog_version,
                                    output.service_number,
                                  )}{' '}
                                  {artifactLabel(artifact)}
                                  <small>
                                    {readableBytes(artifact.byte_size)} ·{' '}
                                    {artifact.sha256.slice(0, 12)}…
                                  </small>
                                </a>
                              ) : (
                                <span key={`${output.service_number}-${artifact.format}`}>
                                  {serviceLabel(
                                    production.service_catalog_version,
                                    output.service_number,
                                  )}{' '}
                                  {artifactLabel(artifact)}
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
            <CursorPagination
              page={productionsPage.pageNumber}
              hasPrevious={productionsPage.hasPrevious}
              hasNext={productionsPage.hasNext}
              onPrevious={productionsPage.previous}
              onNext={productionsPage.next}
              label="正式报告生产记录分页"
            />
          </>
        )}
      </section>
    </>
  );
}
