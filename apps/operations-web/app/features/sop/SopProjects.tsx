import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import {
  createSopProject,
  listSopProjects,
  type IdentitySessionHeaders,
  type SopProjectSummary,
} from '@geo/api-client';
import { Badge, FormField, Pagination, StatePanel, TableRegion, Toast } from '@geo/design-system';
import './sop.css';

type ProjectState =
  | { kind: 'loading' }
  | {
      kind: 'ready';
      data: SopProjectSummary[];
      page: number;
      pageSize: number;
      totalCount: number;
      totalPages: number;
    }
  | { kind: 'forbidden' }
  | { kind: 'failed' };

export function SopProjects({
  headers,
  canWrite,
}: {
  headers: IdentitySessionHeaders;
  canWrite: boolean;
}) {
  const navigate = useNavigate();
  const [state, setState] = useState<ProjectState>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'positive' | 'negative'; text: string } | null>(
    null,
  );
  const [form, setForm] = useState({
    name: '',
    brandStandardName: '',
    targetPlatform: '',
    successMetric: '',
  });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: 'loading' });
    void listSopProjects(headers, page).then((result) => {
      if (cancelled) return;
      if (result.kind === 'ready') setPage(result.data.page);
      setState(
        result.kind === 'ready'
          ? { kind: 'ready', ...result.data }
          : result.kind === 'forbidden'
            ? { kind: 'forbidden' }
            : { kind: 'failed' },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [headers, attempt, page]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.name.trim() || !form.brandStandardName.trim()) {
      setNotice({ tone: 'negative', text: '请填写项目名称和标准品牌名。' });
      return;
    }
    setBusy(true);
    setNotice(null);
    void createSopProject(headers, form, `sop-${globalThis.crypto.randomUUID()}`).then((result) => {
      setBusy(false);
      if (result.kind === 'ready') {
        setNotice({ tone: 'positive', text: result.data.message });
        void navigate(`/platform/operations/sop/projects/${result.data.pubId}`);
      } else {
        setNotice({
          tone: 'negative',
          text: result.kind === 'forbidden' ? '当前角色无权创建 SOP 项目。' : '创建失败，请重试。',
        });
      }
    });
  };

  return (
    <main className="sop-page" aria-label="信源 SOP 项目">
      <section className="sop-hero">
        <div>
          <span className="overline">GEO source workflow</span>
          <h2>信源型文章闭环</h2>
          <p>从冻结问题集和发布前基线出发，追踪到文章级检索、引用与正确归因。</p>
        </div>
        <Badge tone="info">阶段 0–15</Badge>
      </section>

      <div className="sop-index-grid">
        <section className="sop-card" aria-label="SOP 项目列表">
          <div className="sop-section-head">
            <div>
              <span className="overline">监测页</span>
              <h3>项目组合</h3>
            </div>
            <button
              className="button button-secondary"
              onClick={() => setAttempt((value) => value + 1)}
            >
              刷新
            </button>
          </div>
          {state.kind === 'loading' ? (
            <StatePanel state="loading" />
          ) : state.kind === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : state.kind === 'failed' ? (
            <StatePanel state="failed" onRetry={() => setAttempt((value) => value + 1)} />
          ) : state.data.length === 0 ? (
            <StatePanel state="empty" />
          ) : (
            <>
              <TableRegion label="SOP 项目">
                <table className="data-table sop-table">
                  <thead>
                    <tr>
                      <th>项目</th>
                      <th>品牌</th>
                      <th>状态</th>
                      <th>更新时间</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {state.data.map((project) => (
                      <tr key={project.pubId}>
                        <td>
                          <strong>{project.name}</strong>
                          <code>{project.pubId}</code>
                        </td>
                        <td>{project.brandStandardName}</td>
                        <td>
                          <Badge tone={project.status === 'active' ? 'positive' : 'neutral'}>
                            {project.status}
                          </Badge>
                        </td>
                        <td>{project.updatedAt}</td>
                        <td>
                          <Link
                            className="button button-secondary"
                            to={`/platform/operations/sop/projects/${project.pubId}`}
                          >
                            进入工作区
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableRegion>
              <Pagination
                page={state.page}
                pageCount={state.totalPages}
                totalItems={state.totalCount}
                onPageChange={setPage}
                label="SOP 项目分页"
              />
            </>
          )}
        </section>

        <section className="sop-card" aria-label="新建 SOP 项目操作台">
          <div className="sop-section-head">
            <div>
              <span className="overline">操作台</span>
              <h3>新建项目</h3>
            </div>
            <Badge tone={canWrite ? 'positive' : 'neutral'}>{canWrite ? '可写' : '只读'}</Badge>
          </div>
          {!canWrite ? (
            <StatePanel state="forbidden" />
          ) : (
            <form className="sop-form" onSubmit={submit}>
              <FormField id="sop-project-name" label="项目名称">
                <input
                  id="sop-project-name"
                  value={form.name}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setForm((current) => ({ ...current, name: value }));
                  }}
                />
              </FormField>
              <FormField id="sop-brand-name" label="标准品牌名">
                <input
                  id="sop-brand-name"
                  value={form.brandStandardName}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setForm((current) => ({
                      ...current,
                      brandStandardName: value,
                    }));
                  }}
                />
              </FormField>
              <FormField id="sop-target-platform" label="目标 AI / 模式">
                <input
                  id="sop-target-platform"
                  value={form.targetPlatform}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setForm((current) => ({
                      ...current,
                      targetPlatform: value,
                    }));
                  }}
                />
              </FormField>
              <FormField id="sop-success-metric" label="成功定义">
                <textarea
                  id="sop-success-metric"
                  rows={4}
                  value={form.successMetric}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setForm((current) => ({
                      ...current,
                      successMetric: value,
                    }));
                  }}
                />
              </FormField>
              <button className="button button-primary" type="submit" disabled={busy}>
                {busy ? '正在创建…' : '创建 SOP 项目'}
              </button>
            </form>
          )}
        </section>
      </div>
      {notice ? <Toast tone={notice.tone}>{notice.text}</Toast> : null}
    </main>
  );
}
