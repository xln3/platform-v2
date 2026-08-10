import { useEffect, useState } from 'react';
import { WindowPicker } from '../WindowPicker';
import {
  defaultWindow,
  servicesApi,
  type Project,
  type SessionContext,
  type SiteAuditSuggestions,
  type SourceAuditReport,
  type SourceAuditVerdicts,
} from '../api';

const VERDICT_LABELS: Record<string, string> = {
  accurate: '准确',
  inaccurate: '不准确',
  unsupported: '无依据',
  unverifiable: '无法核实',
};

const DIMENSION_LABELS: Record<string, string> = {
  transcript: '转述',
  factual: '事实',
};

const CATEGORY_LABELS: Record<string, string> = {
  content_coverage: '内容覆盖',
  citability: '可引用性',
  fact_consistency: '事实一致性',
  crawlability: '可抓取性',
  other: '其他',
};

const SEVERITY_LABELS: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
};

const SEVERITY_STATUS: Record<string, string> = {
  high: 'bad',
  medium: 'warn',
  low: 'ok',
};

function verdictTotal(bucket: SourceAuditVerdicts): number {
  return bucket.accurate + bucket.inaccurate + bucket.unsupported + bucket.unverifiable;
}

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: SourceAuditReport }
  | { kind: 'failed'; message: string };

type SuggestionsState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: SiteAuditSuggestions }
  | { kind: 'failed'; message: string };

export function SiteAuditWorkspace({
  session,
  project,
}: {
  session: SessionContext;
  project: Project;
}) {
  const [window_, setWindow] = useState(defaultWindow);
  const [report, setReport] = useState<LoadState>({ kind: 'loading' });
  const [suggestions, setSuggestions] = useState<SuggestionsState>({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setReport({ kind: 'loading' });
    servicesApi
      .sourceAudit(session, {
        projectPubId: project.pub_id,
        start: window_.start,
        end: window_.end,
      })
      .then((data) => {
        if (!cancelled) setReport({ kind: 'ready', data });
      })
      .catch((error: unknown) => {
        if (!cancelled)
          setReport({ kind: 'failed', message: error instanceof Error ? error.message : 'failed' });
      });
    return () => {
      cancelled = true;
    };
  }, [session, project.pub_id, window_.start, window_.end]);

  useEffect(() => {
    let cancelled = false;
    setSuggestions({ kind: 'loading' });
    servicesApi
      .siteAuditSuggestions(session, { projectPubId: project.pub_id })
      .then((data) => {
        if (!cancelled) setSuggestions({ kind: 'ready', data });
      })
      .catch((error: unknown) => {
        if (!cancelled)
          setSuggestions({
            kind: 'failed',
            message: error instanceof Error ? error.message : 'failed',
          });
      });
    return () => {
      cancelled = true;
    };
  }, [session, project.pub_id]);

  const data = report.kind === 'ready' ? report.data : null;
  const isEmpty =
    data !== null &&
    data.documents_total === 0 &&
    data.hosts.length === 0 &&
    data.items.length === 0;
  const itemUrlByPubId = new Map(data?.items.map((item) => [item.pub_id, item.url]) ?? []);

  const mainSection = (
    <section className="execution-card">
      <div className="section-title">
        <h2>官网引用能效</h2>
        <span>
          {window_.start} ~ {window_.end}
          {data?.own_site_host ? ` · 官网 ${data.own_site_host}` : ''}
        </span>
      </div>
      <WindowPicker start={window_.start} end={window_.end} onChange={setWindow} />
      {report.kind === 'loading' ? (
        <p className="empty">正在加载信源审计数据…</p>
      ) : report.kind === 'failed' ? (
        <p className="empty">信源审计数据暂不可用（{report.message}）。</p>
      ) : data && isEmpty ? (
        <p className="empty">尚无信源审计数据——采集 run 完成后自动生成。</p>
      ) : data ? (
        <>
          <div className="metric-cards">
            <article>
              <span>官网引用率</span>
              <strong>
                {data.own_site_share === null ? '—' : `${(data.own_site_share * 100).toFixed(1)}%`}
              </strong>
            </article>
            <article>
              <span>官网信源文档</span>
              <strong>
                {data.own_site_documents}/{data.documents_total}
              </strong>
            </article>
            <article>
              <span>官网内容采纳率</span>
              <strong>
                {data.own_site_adoption_rate === null
                  ? '数据不足'
                  : `${(data.own_site_adoption_rate * 100).toFixed(1)}%`}
              </strong>
              <span>
                官网转述准确 {data.own_site_transcript_accurate}/{data.own_site_transcript_total}
              </span>
            </article>
          </div>

          <h3>判定分布</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>维度</th>
                  {Object.values(VERDICT_LABELS).map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                  <th>合计</th>
                </tr>
              </thead>
              <tbody>
                {(['transcript', 'factual'] as const).map((dimension) => {
                  const bucket = data.verdicts[dimension];
                  return (
                    <tr key={dimension}>
                      <td>{DIMENSION_LABELS[dimension]}</td>
                      <td>{bucket.accurate}</td>
                      <td>{bucket.inaccurate}</td>
                      <td>{bucket.unsupported}</td>
                      <td>{bucket.unverifiable}</td>
                      <td>{verdictTotal(bucket)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <h3>信源宿主</h3>
          {data.hosts.length === 0 ? (
            <p className="empty">该时间窗内无信源宿主记录。</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Host</th>
                    <th>是否官网</th>
                    <th>文档数</th>
                    <th>转述准确/总数</th>
                  </tr>
                </thead>
                <tbody>
                  {data.hosts.map((host) => (
                    <tr key={host.host}>
                      <td>{host.host}</td>
                      <td>{host.is_own_site ? '官网' : '第三方'}</td>
                      <td>{host.documents}</td>
                      <td>
                        {host.transcript_accurate}/{host.transcript_total}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3>文档明细</h3>
          {data.items.length === 0 ? (
            <p className="empty">该时间窗内无信源文档明细。</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>URL</th>
                    <th>抓取</th>
                    <th>HTTP</th>
                    <th>维度判定</th>
                    <th>判定依据</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <tr key={item.pub_id}>
                      <td>
                        <a href={item.url} target="_blank" rel="noreferrer noopener">
                          {item.host}
                          {item.is_own_site ? '（官网）' : ''}
                        </a>
                      </td>
                      <td>{item.extract_status || '—'}</td>
                      <td>{item.http_status ?? '—'}</td>
                      <td>
                        {item.audits.length === 0
                          ? '—'
                          : item.audits
                              .map(
                                (audit) =>
                                  `${DIMENSION_LABELS[audit.dimension] ?? audit.dimension}:${
                                    VERDICT_LABELS[audit.verdict] ?? audit.verdict
                                  }`,
                              )
                              .join('；')}
                      </td>
                      <td>
                        {item.audits
                          .map((audit) => audit.rationale)
                          .filter(Boolean)
                          .join('；') || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}
    </section>
  );

  function renderSuggestions() {
    if (suggestions.kind === 'loading') return <p className="empty">正在加载优化建议…</p>;
    if (suggestions.kind === 'failed')
      return <p className="empty">优化建议暂不可用（{suggestions.message}）。</p>;
    if (suggestions.data.suggestions.length === 0)
      return <p className="empty">尚无官网优化建议——信源审计分析后自动生成。</p>;
    return (
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>类别</th>
              <th>严重度</th>
              <th>问题</th>
              <th>建议</th>
              <th>证据</th>
            </tr>
          </thead>
          <tbody>
            {suggestions.data.suggestions.map((suggestion, index) => {
              const evidenceUrl = suggestion.evidence_document_pub_id
                ? itemUrlByPubId.get(suggestion.evidence_document_pub_id)
                : undefined;
              return (
                <tr key={`${suggestion.category}-${index}`}>
                  <td>{CATEGORY_LABELS[suggestion.category] ?? suggestion.category}</td>
                  <td>
                    <span className={`status ${SEVERITY_STATUS[suggestion.severity] ?? 'warn'}`}>
                      {SEVERITY_LABELS[suggestion.severity] ?? suggestion.severity}
                    </span>
                  </td>
                  <td>{suggestion.title}</td>
                  <td>{suggestion.detail}</td>
                  <td>
                    {evidenceUrl ? (
                      <a href={evidenceUrl} target="_blank" rel="noreferrer noopener">
                        证据文档
                      </a>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <>
      {mainSection}
      <section className="execution-card">
        <div className="section-title">
          <h2>官网内容问题与优化建议</h2>
          <span>
            {suggestions.kind === 'ready' && suggestions.data.batch_pub_id
              ? `批次 ${suggestions.data.batch_pub_id}${
                  suggestions.data.generated_at
                    ? ` · ${suggestions.data.generated_at.slice(0, 10)}`
                    : ''
                }`
              : '最新批次'}
          </span>
        </div>
        {renderSuggestions()}
      </section>
    </>
  );
}
