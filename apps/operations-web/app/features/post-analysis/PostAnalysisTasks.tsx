import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import {
  createPostAnalysisTask,
  listPostAnalysisTasks,
  type IdentitySessionHeaders,
  type PostAnalysisTaskSummary,
} from '@geo/api-client';
import {
  Badge,
  CursorPagination,
  FormField,
  StatePanel,
  TableRegion,
  Toast,
} from '@geo/design-system';
import { useCursorCollection, type CursorPage } from '../../pagination';
import { POST_ANALYSIS_TASKS_PAGE_SIZE } from './pagination-policy';
import { formatDateTime, taskStatusLabel, taskStatusTone } from './labels';
import './post-analysis.css';

const POLL_INTERVAL_MS = 4000;
const MAX_URLS_PER_TASK = 50;
const MAX_ALIASES = 20;

const isActiveTask = (status: string): boolean => status === 'queued' || status === 'running';

/** 每行一个 URL：去空、http/https 校验、客户端去重（保持首次出现顺序）。 */
export function parseUrlLines(text: string): { urls: string[]; invalid: string[] } {
  const seen = new Set<string>();
  const urls: string[] = [];
  const invalid: string[] = [];
  for (const line of text.split(/\r?\n/u)) {
    const candidate = line.trim();
    if (!candidate) continue;
    let parsed: URL | null = null;
    try {
      parsed = new URL(candidate);
    } catch {
      parsed = null;
    }
    if (!parsed || (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')) {
      invalid.push(candidate);
      continue;
    }
    if (!seen.has(candidate)) {
      seen.add(candidate);
      urls.push(candidate);
    }
  }
  return { urls, invalid };
}

export function PostAnalysisTasks({
  headers,
  canWrite,
}: {
  headers: IdentitySessionHeaders;
  canWrite: boolean;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [accessState, setAccessState] = useState<'ready' | 'forbidden' | 'failed'>('ready');
  const [notice, setNotice] = useState<{ tone: 'positive' | 'negative'; text: string } | null>(
    null,
  );
  const [form, setForm] = useState({
    targetBrand: '',
    aliases: '',
    urlsText: '',
    verifyFacts: true,
    annotate: true,
    openInvestigation: true,
  });

  const loadTasks = useCallback(
    async (cursor?: string): Promise<CursorPage<PostAnalysisTaskSummary>> => {
      const result = await listPostAnalysisTasks(headers, {
        ...(cursor ? { cursor } : {}),
        limit: POST_ANALYSIS_TASKS_PAGE_SIZE,
      });
      if (result.kind === 'ready') {
        setAccessState('ready');
        return result.data;
      }
      setAccessState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      throw new Error(result.kind);
    },
    [headers],
  );
  const tasksPage = useCursorCollection(
    loadTasks,
    `${headers['X-Tenant-Id']}:${headers['X-Actor-Id']}:${headers['X-Actor-Role']}`,
  );
  const hasActive = tasksPage.data.some((task) => isActiveTask(task.status));
  useEffect(() => {
    if (!hasActive) return;
    const timer = setInterval(() => void tasksPage.refresh(true), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [hasActive, tasksPage.refresh]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const targetBrand = form.targetBrand.trim();
    if (!targetBrand) {
      setNotice({ tone: 'negative', text: '请填写目标品牌。' });
      return;
    }
    const targetBrandAliases = [
      ...new Set(
        form.aliases
          .split(/[,，、]/u)
          .map((alias) => alias.trim())
          .filter(Boolean),
      ),
    ].slice(0, MAX_ALIASES);
    const parsed = parseUrlLines(form.urlsText);
    if (parsed.invalid.length > 0) {
      setNotice({
        tone: 'negative',
        text: `有 ${parsed.invalid.length} 行不是有效的 http/https URL，请修正后再提交。`,
      });
      return;
    }
    if (parsed.urls.length === 0) {
      setNotice({ tone: 'negative', text: '请至少填写一个帖子 URL（每行一个）。' });
      return;
    }
    if (parsed.urls.length > MAX_URLS_PER_TASK) {
      setNotice({
        tone: 'negative',
        text: `单次最多 ${MAX_URLS_PER_TASK} 个 URL，当前 ${parsed.urls.length} 个，请分批提交。`,
      });
      return;
    }
    setBusy(true);
    setNotice(null);
    void createPostAnalysisTask(
      headers,
      {
        targetBrand,
        targetBrandAliases,
        urls: parsed.urls,
        verifyFacts: form.verifyFacts,
        annotate: form.annotate,
        openInvestigation: form.openInvestigation,
      },
      `post-analysis-${globalThis.crypto.randomUUID()}`,
    ).then((result) => {
      setBusy(false);
      if (result.kind === 'ready') {
        void navigate(`/platform/operations/post-analysis/tasks/${result.data.pubId}`);
      } else {
        setNotice({
          tone: 'negative',
          text: result.kind === 'forbidden' ? '当前角色无权创建分析任务。' : '创建失败，请重试。',
        });
      }
    });
  };

  return (
    <main className="pa-page" aria-label="信源帖子取证分析">
      <section className="pa-hero">
        <div>
          <span className="overline">Post evidence analysis</span>
          <h2>信源帖子取证分析</h2>
          <p>提交帖子 URL，抓取留证后判定 GEO 帖、识别拉踩与不实信息，并生成标注截图。</p>
        </div>
        <Badge tone="info">1–50 URL / 任务</Badge>
      </section>

      <div className="pa-index-grid">
        <section className="pa-card" aria-label="分析任务列表">
          <div className="pa-section-head">
            <div>
              <span className="overline">监测页</span>
              <h3>分析任务</h3>
            </div>
            <button
              className="button button-secondary"
              type="button"
              onClick={() => void tasksPage.refresh()}
            >
              刷新
            </button>
          </div>
          {tasksPage.state === 'loading' ? (
            <StatePanel state="loading" />
          ) : accessState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : tasksPage.state === 'failed' || accessState === 'failed' ? (
            <StatePanel state="failed" onRetry={() => void tasksPage.refresh()} />
          ) : tasksPage.data.length === 0 ? (
            <StatePanel state="empty" />
          ) : (
            <>
              <TableRegion label="分析任务">
                <table className="data-table pa-table">
                  <thead>
                    <tr>
                      <th>目标品牌</th>
                      <th>状态</th>
                      <th>URL 数</th>
                      <th>创建时间</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tasksPage.data.map((task) => (
                      <tr key={task.pubId}>
                        <td>
                          <strong>{task.targetBrand}</strong>
                          <code>{task.pubId}</code>
                        </td>
                        <td>
                          <Badge tone={taskStatusTone(task.status)}>
                            {taskStatusLabel(task.status)}
                          </Badge>
                        </td>
                        <td>{task.urlCount}</td>
                        <td>{formatDateTime(task.createdAt)}</td>
                        <td>
                          <Link
                            className="button button-secondary"
                            to={`/platform/operations/post-analysis/tasks/${task.pubId}`}
                          >
                            查看详情
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableRegion>
              <CursorPagination
                page={tasksPage.pageNumber}
                hasPrevious={tasksPage.hasPrevious}
                hasNext={tasksPage.hasNext}
                onPrevious={tasksPage.previous}
                onNext={tasksPage.next}
                label="帖子分析任务分页"
              />
            </>
          )}
        </section>

        <section className="pa-card" aria-label="新建分析任务操作台">
          <div className="pa-section-head">
            <div>
              <span className="overline">操作台</span>
              <h3>新建分析任务</h3>
            </div>
            <Badge tone={canWrite ? 'positive' : 'neutral'}>{canWrite ? '可写' : '只读'}</Badge>
          </div>
          {!canWrite ? (
            <StatePanel state="forbidden" />
          ) : (
            <form className="pa-form" onSubmit={submit}>
              <FormField id="pa-target-brand" label="目标品牌">
                <input
                  id="pa-target-brand"
                  value={form.targetBrand}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setForm((current) => ({ ...current, targetBrand: value }));
                  }}
                />
              </FormField>
              <FormField id="pa-brand-aliases" label="品牌别名" hint="逗号分隔，可选，最多 20 个。">
                <input
                  id="pa-brand-aliases"
                  value={form.aliases}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setForm((current) => ({ ...current, aliases: value }));
                  }}
                />
              </FormField>
              <FormField
                id="pa-urls"
                label="帖子 URL 列表"
                hint="每行一个，仅 http/https，1–50 个，提交前自动去重。"
              >
                <textarea
                  id="pa-urls"
                  rows={8}
                  value={form.urlsText}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setForm((current) => ({ ...current, urlsText: value }));
                  }}
                />
              </FormField>
              <label className="pa-checkbox" htmlFor="pa-verify-facts">
                <input
                  id="pa-verify-facts"
                  type="checkbox"
                  checked={form.verifyFacts}
                  onChange={(event) => {
                    const value = event.currentTarget.checked;
                    setForm((current) => ({ ...current, verifyFacts: value }));
                  }}
                />
                事实核验（联网核查关键陈述）
              </label>
              <label className="pa-checkbox" htmlFor="pa-annotate">
                <input
                  id="pa-annotate"
                  type="checkbox"
                  checked={form.annotate}
                  onChange={(event) => {
                    const value = event.currentTarget.checked;
                    setForm((current) => ({ ...current, annotate: value }));
                  }}
                />
                截图标注（标注图与原截图留证）
              </label>
              <label className="pa-checkbox" htmlFor="pa-open-investigation">
                <input
                  id="pa-open-investigation"
                  type="checkbox"
                  checked={form.openInvestigation}
                  onChange={(event) => {
                    const value = event.currentTarget.checked;
                    setForm((current) => ({ ...current, openInvestigation: value }));
                  }}
                />
                <span>
                  命中后自动建立情报调查（反GEO）
                  <small className="pa-checkbox-hint">
                    命中（GEO帖/拉踩）后会在情报面自动建立调查案件；关闭则仅出分析。
                  </small>
                </span>
              </label>
              <button className="button button-primary" type="submit" disabled={busy}>
                {busy ? '正在创建…' : '创建分析任务'}
              </button>
            </form>
          )}
        </section>
      </div>
      {notice ? <Toast tone={notice.tone}>{notice.text}</Toast> : null}
    </main>
  );
}
