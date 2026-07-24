import { useEffect, useMemo, useState } from 'react';
import { Background, Controls, ReactFlow, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Badge,
  containsClientSecret,
  Dialog,
  MetricGrid,
  ProductShell,
  StatePanel,
  useOptionalExperienceContext,
} from '@geo/design-system';
import {
  createInvestigationAppeal,
  createInvestigationVerdict,
  getHealth,
  getInvestigation,
  listInvestigations,
  type InvestigationDetail,
  type InvestigationPage,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';

const nav = [
  { id: 'cases', label: '案件' },
  { id: 'claims', label: 'Claim 矩阵' },
  { id: 'sources', label: '多源证据' },
  { id: 'graph', label: '传播关系' },
  { id: 'history', label: '页面历史' },
  { id: 'verdict', label: '裁决与申诉', badge: '1' },
  { id: 'package', label: '证据包' },
];

type Verdict = 'pending' | 'confirmed' | 'rejected' | 'appealed' | 'reviewed';
type LiveInvestigationTarget = {
  investigationPubId: string;
  probability: number | null;
  evidenceSufficiency: number | null;
  uncertainty: number | null;
};
type Evidence = {
  id: string;
  source: string;
  kind: string;
  cluster: string;
  stance: '支持' | '反驳' | '背景';
  independent: boolean;
};

const evidence: Evidence[] = [
  {
    id: 'E-019',
    source: '国家认证信息平台',
    kind: '一手登记',
    cluster: 'C-01',
    stance: '反驳',
    independent: true,
  },
  {
    id: 'E-027',
    source: '品牌官网 / awards',
    kind: '自有页面',
    cluster: 'C-02',
    stance: '支持',
    independent: true,
  },
  {
    id: 'E-031',
    source: '行业观察转载',
    kind: '媒体转载',
    cluster: 'C-07',
    stance: '支持',
    independent: false,
  },
  {
    id: 'E-044',
    source: '区域代理商文章',
    kind: '近重复',
    cluster: 'C-07',
    stance: '支持',
    independent: false,
  },
];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function projectLiveInvestigation(value: InvestigationDetail): LiveInvestigationTarget | null {
  if (!isRecord(value)) return null;
  const investigationPubId =
    typeof value.pub_id === 'string' &&
    value.pub_id.length <= 120 &&
    !containsClientSecret(value.pub_id)
      ? value.pub_id
      : '';
  const scores = Array.isArray(value.scores) ? value.scores : [];
  const latestScore = [...scores].reverse().find(isRecord);
  const safeRatio = (candidate: unknown): number | null =>
    typeof candidate === 'number' && Number.isFinite(candidate) && candidate >= 0 && candidate <= 1
      ? candidate
      : null;
  return investigationPubId
    ? {
        investigationPubId,
        probability: safeRatio(latestScore?.probability),
        evidenceSufficiency: safeRatio(latestScore?.evidence_sufficiency),
        uncertainty: safeRatio(latestScore?.uncertainty),
      }
    : null;
}

function CasesWorkspace({
  livePage,
  liveState,
}: {
  livePage: InvestigationPage | null;
  liveState: 'fixture' | 'loading' | 'ready' | 'failed';
}) {
  const [selected, setSelected] = useState('CASE-2407');
  const liveCases = livePage?.data ?? [];
  if (liveState === 'loading') {
    return <StatePanel state="loading" />;
  }
  if (liveState === 'failed') {
    return <StatePanel state="failed" />;
  }
  if (liveState === 'ready' && liveCases.length === 0) {
    return <StatePanel state="empty" />;
  }
  const caseRows =
    liveState === 'ready'
      ? liveCases.map((item) => [
          item.pub_id,
          item.title,
          item.latest_verdict ?? item.state,
          item.probability ?? '—',
        ])
      : [
          ['CASE-2407', '认证表述跨页面传播', '人工复核', '高'],
          ['CASE-2406', '市场份额口径冲突', '证据补充中', '中'],
          ['CASE-2398', '产品发布日期历史变更', '已裁决', '低'],
        ];
  return (
    <>
      <MetricGrid
        metrics={[
          { label: '开放案件', value: '4', detail: '1 个需复核' },
          { label: '原子 Claim', value: '24', detail: '已验证 17' },
          { label: '独立来源', value: '8', detail: '3 个同源簇' },
          { label: '申诉时限', value: '6 天', detail: 'CASE-2407' },
        ]}
      />
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Investigation queue</span>
            <h2>调查案件</h2>
          </div>
          <Badge tone={liveState === 'ready' ? 'positive' : 'warning'}>
            {liveState === 'ready' ? 'live API' : 'contract fixture'}
          </Badge>
        </div>
        <div className="case-list" role="list">
          {caseRows.map(([id, title, status, risk]) => (
            <button
              key={id}
              role="listitem"
              className={selected === id ? 'selected' : ''}
              onClick={() => setSelected(id!)}
            >
              <strong>{id}</strong>
              <span>{title}</span>
              <Badge tone={status === '已裁决' ? 'positive' : 'warning'}>{status}</Badge>
              <small>风险 {risk}</small>
            </button>
          ))}
        </div>
        <div className="case-summary">
          <div>
            <span className="overline">Selected case</span>
            <h3>{selected} · 认证表述跨页面传播</h3>
            <p>
              调查目标是判断“国家级认证”表述是否有可核验的一手依据，并追踪近重复内容的传播路径。概率只用于排序，不直接构成指控。
            </p>
          </div>
          <dl className="definition-grid">
            <div>
              <dt>案件 owner</dt>
              <dd>调查员 · 林岚</dd>
            </div>
            <div>
              <dt>规则版本</dt>
              <dd>intelligence-v2.3</dd>
            </div>
            <div>
              <dt>证据窗口</dt>
              <dd>2026-05-01—07-21</dd>
            </div>
          </dl>
        </div>
      </section>
    </>
  );
}

function ClaimsWorkspace() {
  const [expanded, setExpanded] = useState('CL-01');
  const claims = [
    {
      id: 'CL-01',
      text: '产品已通过国家级认证',
      support: 3,
      oppose: 1,
      sufficiency: '不足',
      uncertainty: '登记主体名称可能存在别名',
    },
    {
      id: 'CL-02',
      text: '市场份额连续三年第一',
      support: 2,
      oppose: 1,
      sufficiency: '不足',
      uncertainty: '未披露市场范围与统计机构',
    },
    {
      id: 'CL-03',
      text: '支持私有化部署',
      support: 4,
      oppose: 0,
      sufficiency: '充分',
      uncertainty: '版本适用范围待确认',
    },
  ];
  return (
    <section className="panel">
      <span className="overline">Atomic claims</span>
      <h2>Claim × Evidence 矩阵</h2>
      <p className="panel-subtitle">
        复合表述已拆成可单独支持或反驳的原子 Claim；真实 0 与未采集严格区分。
      </p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Claim</th>
              <th>支持</th>
              <th>反驳</th>
              <th>充分度</th>
              <th>解释</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((claim) => (
              <tr key={claim.id}>
                <td>
                  <button
                    className="link-button"
                    aria-expanded={expanded === claim.id}
                    onClick={() => setExpanded(expanded === claim.id ? '' : claim.id)}
                  >
                    {claim.id} · {claim.text}
                  </button>
                </td>
                <td>{claim.support}</td>
                <td>{claim.oppose}</td>
                <td>
                  <Badge tone={claim.sufficiency === '充分' ? 'positive' : 'warning'}>
                    {claim.sufficiency}
                  </Badge>
                </td>
                <td>{claim.uncertainty}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {expanded ? (
        <div className="rule-explanation">
          <Badge tone="info">规则解释</Badge>
          <p>
            <strong>{expanded}</strong> 的独立一手来源不足 2 个；同源转载只计一次，反证 E-019
            权重更高。当前概率 0.61，区间 0.43–0.76。
          </p>
        </div>
      ) : null}
    </section>
  );
}

function SourcesWorkspace() {
  const [filter, setFilter] = useState('all');
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [verifiedEvidence, setVerifiedEvidence] = useState<Set<string>>(() => new Set());
  const rows = evidence.filter((item) => filter === 'all' || item.cluster === filter);
  const verifySelected = () => {
    if (!selectedEvidence) return;
    setVerifiedEvidence((current) => new Set(current).add(selectedEvidence.id));
  };
  return (
    <>
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Source independence</span>
            <h2>多源证据与同源簇</h2>
          </div>
          <Badge tone="info">{rows.length} 条</Badge>
        </div>
        <div className="filter-bar">
          <label>
            同源簇
            <select
              aria-label="筛选同源簇"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            >
              <option value="all">全部</option>
              <option value="C-01">C-01</option>
              <option value="C-02">C-02</option>
              <option value="C-07">C-07</option>
            </select>
          </label>
        </div>
        <div className="source-grid">
          {rows.map((item) => (
            <article key={item.id}>
              <div>
                <Badge
                  tone={
                    item.stance === '反驳'
                      ? 'danger'
                      : item.stance === '支持'
                        ? 'positive'
                        : 'neutral'
                  }
                >
                  {item.stance}
                </Badge>
                <Badge tone={item.independent ? 'info' : 'warning'}>
                  {item.independent ? '独立来源' : '同源传播'}
                </Badge>
                {verifiedEvidence.has(item.id) ? <Badge tone="positive">锚点已核验</Badge> : null}
              </div>
              <h3>{item.source}</h3>
              <p>
                {item.kind} · cluster {item.cluster}
              </p>
              <dl>
                <div>
                  <dt>采集</dt>
                  <dd>2026-07-21 09:42</dd>
                </div>
                <div>
                  <dt>快照</dt>
                  <dd>sha256: 8bc…19f</dd>
                </div>
              </dl>
              <button className="button button-secondary" onClick={() => setSelectedEvidence(item)}>
                检查证据锚点
              </button>
            </article>
          ))}
        </div>
        {rows.length === 0 ? <StatePanel state="empty" /> : null}
      </section>
      {selectedEvidence ? (
        <Dialog
          title={`${selectedEvidence.id} 证据锚点`}
          eyebrow="Frozen evidence"
          closeLabel="关闭对话框"
          onClose={() => setSelectedEvidence(null)}
        >
          <div
            className="screenshot-placeholder"
            role="img"
            aria-label={`${selectedEvidence.source}页面快照，锚点 bbox 84,176,310,42`}
          >
            <span>目标表述位于 bbox 84,176,310,42</span>
          </div>
          <dl className="definition-grid">
            <div>
              <dt>文本锚点</dt>
              <dd>字符 112–168</dd>
            </div>
            <div>
              <dt>页面版本</dt>
              <dd>sha256: 8bc…19f</dd>
            </div>
            <div>
              <dt>来源判定</dt>
              <dd>
                {selectedEvidence.independent ? '独立来源' : `同源簇 ${selectedEvidence.cluster}`}
              </dd>
            </div>
            <div>
              <dt>历史差异</dt>
              <dd>当前版本新增目标 claim；前版无此段落</dd>
            </div>
          </dl>
          {verifiedEvidence.has(selectedEvidence.id) ? (
            <div className="confirmation" role="status">
              <Badge tone="positive">锚点已核验</Badge>
              <span>核验事件绑定页面 hash，未改写原证据。</span>
            </div>
          ) : (
            <button className="button" onClick={verifySelected}>
              标记锚点已核验
            </button>
          )}
        </Dialog>
      ) : null}
    </>
  );
}

const graphNodes: Node[] = [
  { id: 'origin', position: { x: 20, y: 90 }, data: { label: '品牌页面 E-027' }, type: 'input' },
  { id: 'media', position: { x: 260, y: 20 }, data: { label: '行业观察 E-031' } },
  { id: 'agent', position: { x: 260, y: 165 }, data: { label: '代理商 E-044' } },
  { id: 'answer', position: { x: 520, y: 90 }, data: { label: 'AI 回答 A-108' }, type: 'output' },
];
const graphEdges: Edge[] = [
  { id: 'e1', source: 'origin', target: 'media', label: '近重复 0.91' },
  { id: 'e2', source: 'origin', target: 'agent', label: '近重复 0.87' },
  { id: 'e3', source: 'media', target: 'answer', label: '被引用' },
  { id: 'e4', source: 'agent', target: 'answer', label: '语义匹配' },
];

function GraphWorkspace() {
  return (
    <div className="graph-layout">
      <section className="panel">
        <span className="overline">Propagation graph</span>
        <h2>内容传播关系</h2>
        <p className="panel-subtitle">边表示可解释的相似或引用关系，不代表主体之间存在组织关系。</p>
        <div className="flow-canvas">
          <ReactFlow
            nodes={graphNodes}
            edges={graphEdges}
            fitView
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </section>
      <section className="panel">
        <h2>可访问表格替代</h2>
        <p className="panel-subtitle">与图中节点和边完全等价，可通过键盘和读屏访问。</p>
        <table className="data-table">
          <caption className="sr-only">传播图节点与关系</caption>
          <thead>
            <tr>
              <th>起点</th>
              <th>关系</th>
              <th>终点</th>
              <th>依据</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>品牌页面 E-027</td>
              <td>近重复</td>
              <td>行业观察 E-031</td>
              <td>相似度 0.91</td>
            </tr>
            <tr>
              <td>品牌页面 E-027</td>
              <td>近重复</td>
              <td>代理商 E-044</td>
              <td>相似度 0.87</td>
            </tr>
            <tr>
              <td>行业观察 E-031</td>
              <td>被引用</td>
              <td>AI 回答 A-108</td>
              <td>citation #2</td>
            </tr>
            <tr>
              <td>代理商 E-044</td>
              <td>语义匹配</td>
              <td>AI 回答 A-108</td>
              <td>锚点 44–78</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  );
}

function HistoryWorkspace() {
  const [version, setVersion] = useState<'current' | 'previous'>('current');
  return (
    <section className="panel">
      <div className="account-head">
        <div>
          <span className="overline">Immutable history</span>
          <h2>页面历史与视觉 Diff</h2>
        </div>
        <div className="segmented">
          <button aria-pressed={version === 'previous'} onClick={() => setVersion('previous')}>
            07/02
          </button>
          <button aria-pressed={version === 'current'} onClick={() => setVersion('current')}>
            07/21
          </button>
        </div>
      </div>
      <div className="visual-diff">
        <article>
          <header>历史快照 · 2026-07-02</header>
          <div className="page-mock">
            <h3>资质与荣誉</h3>
            <p>荣获行业创新产品奖。</p>
          </div>
        </article>
        <article>
          <header>当前快照 · 2026-07-21</header>
          <div className="page-mock">
            <h3>资质与荣誉</h3>
            <p>荣获行业创新产品奖。</p>
            <mark>已通过国家级认证</mark>
          </div>
        </article>
      </div>
      <div className="diff-summary">
        <Badge tone="warning">新增区域</Badge>
        <span>bbox 84,176,310,42 · OCR “已通过国家级认证” · 感知哈希变化 18%</span>
      </div>
    </section>
  );
}

function VerdictWorkspace({
  verdict,
  setVerdict,
  liveTarget,
}: {
  verdict: Verdict;
  setVerdict: (value: Verdict) => void;
  liveTarget?: LiveInvestigationTarget | null;
}) {
  const [reason, setReason] = useState('');
  const [writeState, setWriteState] = useState<'idle' | 'saving' | 'failed'>('idle');
  const [receipt, setReceipt] = useState('');
  const reasonContainsSecret = containsClientSecret(reason);
  const headers = liveTarget ? getValidatedIdentityHeaders() : null;
  const decide = async (next: 'confirmed' | 'rejected') => {
    if (liveTarget && headers) {
      setWriteState('saving');
      const result = await createInvestigationVerdict(
        liveTarget.investigationPubId,
        {
          verdict: next,
          rationale:
            next === 'confirmed'
              ? '人工复核确认当前证据支持高风险表述。'
              : '人工复核认为当前证据不足以支持该结论。',
          workflow_operation_id: null,
        },
        headers,
      );
      if (result.kind !== 'ready') {
        setWriteState('failed');
        return;
      }
      setReceipt('真实人工裁决已记录');
      setWriteState('idle');
    }
    setVerdict(next);
  };
  const appeal = async () => {
    if (liveTarget && headers) {
      setWriteState('saving');
      const result = await createInvestigationAppeal(
        liveTarget.investigationPubId,
        { reason: reason.trim() },
        headers,
      );
      if (result.kind !== 'ready') {
        setWriteState('failed');
        return;
      }
      setReceipt('真实申诉已登记');
      setWriteState('idle');
    }
    setVerdict('appealed');
  };
  const probability = liveTarget?.probability ?? 0.61;
  const sufficiency = liveTarget?.evidenceSufficiency ?? 0.72;
  return (
    <div className="verdict-layout">
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Human decision</span>
            <h2>人工裁决</h2>
          </div>
          <Badge
            tone={
              verdict === 'confirmed' || verdict === 'reviewed'
                ? 'danger'
                : verdict === 'rejected'
                  ? 'positive'
                  : 'warning'
            }
          >
            {verdict}
          </Badge>
          {liveTarget ? <Badge tone="positive">真实 intelligence API</Badge> : null}
        </div>
        <div className="probability">
          <span>GEO 可能性</span>
          <strong>{probability.toFixed(2)}</strong>
          <div>
            <i style={{ width: `${probability * 100}%` }} />
          </div>
          <small>
            {liveTarget?.uncertainty === null || liveTarget?.uncertainty === undefined
              ? '可信区间等待真实评分'
              : `不确定性 ${(liveTarget.uncertainty * 100).toFixed(0)}%`}{' '}
            · 证据充分度 {(sufficiency * 100).toFixed(0)}%
          </small>
        </div>
        <ul className="reason-list">
          <li>同源簇 C-07 的 6 个页面只计作 1 个来源</li>
          <li>一手登记 E-019 与目标表述冲突，权重 1.5</li>
          <li>传播时间先于 AI 回答，但不能单独证明操纵意图</li>
        </ul>
        <div className="button-row">
          <button
            className="button button-secondary"
            disabled={writeState === 'saving'}
            onClick={() => void decide('rejected')}
          >
            证据不足，不成立
          </button>
          <button
            className="button"
            disabled={writeState === 'saving'}
            onClick={() => void decide('confirmed')}
          >
            确认高风险表述
          </button>
        </div>
        {receipt ? <div role="status">{receipt}</div> : null}
        {writeState === 'failed' ? <StatePanel state="failed" /> : null}
      </section>
      <aside className="panel">
        <h2>复核与申诉</h2>
        <p className="panel-subtitle">申诉不会覆盖原裁决；新事实会创建独立版本和审计事件。</p>
        <label className="form-field">
          <span>申诉理由</span>
          <textarea
            aria-label="申诉理由"
            rows={5}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
          {reasonContainsSecret ? (
            <span className="field-error" role="alert">
              请勿在申诉中粘贴验证码、Cookie、token、密码、完整手机号或 profile 路径
            </span>
          ) : null}
        </label>
        <button
          className="button button-secondary"
          disabled={
            reason.trim().length < 8 ||
            reasonContainsSecret ||
            verdict === 'appealed' ||
            writeState === 'saving'
          }
          onClick={() => void appeal()}
        >
          提交申诉
        </button>
        {verdict === 'appealed' ? (
          <div className="confirmation" role="status">
            <Badge tone="info">申诉已登记</Badge>
            <span>AP-2407 · 原裁决保持可追溯，等待另一名复核员。</span>
            <button className="button" onClick={() => setVerdict('reviewed')}>
              记录二次复核
            </button>
          </div>
        ) : null}
      </aside>
    </div>
  );
}

function PackageWorkspace({ verdict }: { verdict: Verdict }) {
  const [prepared, setPrepared] = useState(false);
  const manifest = useMemo(
    () => ({
      case_id: 'CASE-2407',
      verdict,
      rule_version: 'intelligence-v2.3',
      evidence_count: 4,
      generated_at: '2026-07-24T16:00:00+08:00',
    }),
    [verdict],
  );
  const download = () => {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' }),
    );
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'CASE-2407-evidence-manifest.json';
    anchor.click();
    URL.revokeObjectURL(url);
    setPrepared(true);
  };
  return (
    <section className="panel">
      <span className="overline">Portable evidence</span>
      <h2>证据包</h2>
      <p className="panel-subtitle">
        包内包含快照、锚点、哈希、规则解释、人工裁决和版本链；不包含账号秘密或会话材料。
      </p>
      <div className="package-grid">
        <article>
          <strong>01</strong>
          <h3>manifest.json</h3>
          <p>对象清单、内容哈希和 MIME。</p>
        </article>
        <article>
          <strong>02</strong>
          <h3>evidence/</h3>
          <p>4 个不可变快照与锚点。</p>
        </article>
        <article>
          <strong>03</strong>
          <h3>decision.json</h3>
          <p>规则版本、概率区间和人工理由。</p>
        </article>
        <article>
          <strong>04</strong>
          <h3>history.json</h3>
          <p>页面版本链与视觉 diff。</p>
        </article>
      </div>
      <div className="button-row">
        <button className="button" onClick={download}>
          生成并下载 manifest
        </button>
      </div>
      {prepared ? (
        <div className="confirmation" role="status">
          <Badge tone="positive">证据包清单已生成</Badge>
          <span>4 项完整性检查通过；二进制归档等待真实 API。</span>
        </div>
      ) : null}
    </section>
  );
}

export default function Shell() {
  const experience = useOptionalExperienceContext();
  const [verdict, setVerdict] = useState<Verdict>('pending');
  const [livePage, setLivePage] = useState<InvestigationPage | null>(null);
  const [liveTarget, setLiveTarget] = useState<LiveInvestigationTarget | null>(null);
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
    void listInvestigations(headers).then(async (result) => {
      if (result.kind === 'ready') {
        setLivePage(result.data);
        const investigationPubId = result.data.data[0]?.pub_id;
        if (investigationPubId) {
          const detail = await getInvestigation(investigationPubId, headers);
          if (detail.kind !== 'ready') {
            setLiveTarget(null);
            setLiveState('failed');
            return;
          }
          setLiveTarget(projectLiveInvestigation(detail.data));
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
      product="Intelligence Web"
      title="证据调查台"
      description="从原子 Claim、多源证据与传播关系形成可解释的人工裁决。"
      probe={getHealth}
      nav={nav}
    >
      {(active) =>
        active === 'cases' ? (
          <CasesWorkspace livePage={livePage} liveState={liveState} />
        ) : active === 'verdict' && experience?.source === 'live' ? (
          liveState === 'loading' ? (
            <StatePanel state="loading" />
          ) : liveState === 'failed' ? (
            <StatePanel state="failed" />
          ) : liveTarget ? (
            <VerdictWorkspace verdict={verdict} setVerdict={setVerdict} liveTarget={liveTarget} />
          ) : (
            <StatePanel state="empty" />
          )
        ) : experience?.source === 'live' ? (
          <StatePanel state="insufficient" />
        ) : active === 'claims' ? (
          <ClaimsWorkspace />
        ) : active === 'sources' ? (
          <SourcesWorkspace />
        ) : active === 'graph' ? (
          <GraphWorkspace />
        ) : active === 'history' ? (
          <HistoryWorkspace />
        ) : active === 'verdict' ? (
          <VerdictWorkspace verdict={verdict} setVerdict={setVerdict} />
        ) : (
          <PackageWorkspace verdict={verdict} />
        )
      }
    </ProductShell>
  );
}
