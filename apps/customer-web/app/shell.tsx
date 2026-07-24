import {
  AccountSummary,
  Badge,
  containsClientSecret,
  Dialog,
  FilterBar,
  FormField as Field,
  InterventionStatus,
  MetricGrid,
  Pagination,
  ProductShell,
  projectSafeAccountSummary,
  RevocationReceipt,
  StatePanel,
  TableRegion,
  Toast,
  type AccountSummaryProjection,
  useOptionalExperienceContext,
} from '@geo/design-system';
import {
  authorizeCustomerAccount,
  createCustomerPairing,
  createProjectResource,
  getAnalyticsOverview,
  getHealth,
  listAnalyticsAnswers,
  listCustomerAccountEvents,
  listCustomerAccounts,
  listCustomerPairings,
  listReports,
  registerCustomerAccount,
  revokeCustomerAccount,
  type ReportPage,
  type CustomerAccountView,
  type CustomerEventView,
  type AnalyticsOverviewResponse,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { GeoBarChart } from '@geo/charts';
import { EvidenceViewer } from '@geo/evidence-viewer';
import { useEffect, useState } from 'react';
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import { useSearchParams } from 'react-router';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';

const nav = [
  { id: 'home', label: '首页' },
  { id: 'profile', label: '资料' },
  { id: 'assets', label: '品牌产品' },
  { id: 'questions', label: '问题目标' },
  { id: 'monitoring', label: '监测表现' },
  { id: 'evidence', label: '回答证据' },
  { id: 'reports', label: '报告' },
  { id: 'members', label: '成员' },
  { id: 'accounts', label: '平台账号', badge: '2' },
];
const noClientSecret = (value: string): boolean => !containsClientSecret(value);
const noClientSecretMessage =
  '请勿在普通表单粘贴验证码、Cookie、token、密码、完整手机号或 profile 路径';
const navigateCustomerSection = (section: string) => {
  const url = new URL(window.location.href);
  url.searchParams.set('section', section);
  window.history.pushState({}, '', url);
  window.dispatchEvent(new PopStateEvent('popstate'));
};

const account: AccountSummaryProjection = {
  accountMask: '尾号 · 4821',
  platformLabel: '豆包',
  ownerLabel: '客户管理员 · 林澄',
  custodyMode: 'hybrid',
  admissionLevel: 'read_verified',
  scopes: ['read', 'query'],
  expiresLabel: '2026-09-30',
  regionLabel: '中国大陆 · 华东',
  sessionHealth: 'healthy',
  lastVerifiedLabel: '今天 09:42',
  interventionStatus: 'none',
};

const authorizationSchema = z.object({
  platformSlug: z.enum(['doubao']),
  accountMask: z
    .string()
    .trim()
    .min(3, '请填写至少 3 个字符的账号掩码')
    .max(120)
    .refine(noClientSecret, noClientSecretMessage),
  owner: z.string().trim().min(2, '请填写账号 owner').refine(noClientSecret, noClientSecretMessage),
  responsible: z
    .string()
    .trim()
    .min(2, '请填写运营责任人')
    .refine(noClientSecret, noClientSecretMessage),
  custodyMode: z.enum(['server', 'customer-device', 'hybrid']),
  expiresOn: z.string().date('请选择授权到期日'),
  region: z.string().trim().min(2, '请填写授权地域').refine(noClientSecret, noClientSecretMessage),
  scopes: z.array(z.enum(['read', 'query', 'draft', 'publish'])).min(1, '至少选择一个授权动作'),
});

type AuthorizationFields = z.infer<typeof authorizationSchema>;

const safeOpaqueId = (value: unknown, prefix: string): string =>
  typeof value === 'string' &&
  value.startsWith(prefix) &&
  value.length <= 120 &&
  !containsClientSecret(value)
    ? value
    : '';

function projectCustomerAccount(value: CustomerAccountView): AccountSummaryProjection {
  return projectSafeAccountSummary({
    accountMask: value.account_mask,
    platformLabel: value.platform_label,
    ownerLabel: value.owner_label,
    custodyMode: value.custody_mode === 'customer_device' ? 'customer-device' : value.custody_mode,
    admissionLevel: value.admission_level,
    scopes: value.scopes,
    expiresLabel: value.authorization_expires_at?.slice(0, 10) ?? '—',
    regionLabel: value.region_label,
    sessionHealth: value.session_health,
    lastVerifiedLabel: value.last_verified_at?.slice(0, 16).replace('T', ' ') ?? '尚未验证',
    interventionStatus: value.intervention_status,
  });
}

function projectCustomerEvents(
  values: CustomerEventView[],
): { type: string; occurredAt: string }[] {
  return values.flatMap((value) => {
    const type =
      typeof value.event_type === 'string' &&
      value.event_type.length <= 120 &&
      !containsClientSecret(value.event_type)
        ? value.event_type
        : '';
    const occurredAt =
      typeof value.occurred_at === 'string' && !containsClientSecret(value.occurred_at)
        ? value.occurred_at.slice(0, 16).replace('T', ' ')
        : '';
    return type && occurredAt ? [{ type, occurredAt }] : [];
  });
}

type QuestionRow = {
  question: string;
  prompts: number;
  mention: string;
  rank: string;
  evidence: string;
  evidenceTone: 'positive' | 'info' | 'neutral';
};
const questionRows: QuestionRow[] = [
  {
    question: '企业知识库如何选择？',
    prompts: 12,
    mention: '75%',
    rank: '2.1',
    evidence: '9 条可追溯',
    evidenceTone: 'positive',
  },
  {
    question: '适合制造业的 AI 平台',
    prompts: 14,
    mention: '64%',
    rank: '2.7',
    evidence: '8 条可追溯',
    evidenceTone: 'info',
  },
  {
    question: '私有化大模型方案对比',
    prompts: 12,
    mention: '0%',
    rank: '—',
    evidence: '真实 0',
    evidenceTone: 'neutral',
  },
];

function QuestionTable() {
  const columns: ColumnDef<QuestionRow>[] = [
    { accessorKey: 'question', header: '问题' },
    { accessorKey: 'prompts', header: '提问' },
    { accessorKey: 'mention', header: '提及率' },
    { accessorKey: 'rank', header: '平均排名' },
    {
      id: 'evidence',
      header: '证据',
      cell: ({ row }) => <Badge tone={row.original.evidenceTone}>{row.original.evidence}</Badge>,
    },
  ];
  const table = useReactTable({ data: questionRows, columns, getCoreRowModel: getCoreRowModel() });
  return (
    <table className="data-table">
      <thead>
        {table.getHeaderGroups().map((group) => (
          <tr key={group.id}>
            {group.headers.map((header) => (
              <th key={header.id}>
                {flexRender(header.column.columnDef.header, header.getContext())}
              </th>
            ))}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => (
          <tr key={row.id}>
            {row.getVisibleCells().map((cell) => (
              <td key={cell.id}>
                {flexRender(
                  cell.column.columnDef.cell ?? ((context) => String(context.getValue() ?? '')),
                  cell.getContext(),
                )}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Monitoring() {
  const experience = useOptionalExperienceContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const [liveMetrics, setLiveMetrics] = useState<AnalyticsOverviewResponse | null>(null);
  const [liveState, setLiveState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const safeParam = <T extends string>(key: string, allowed: readonly T[], fallback: T): T => {
    const value = searchParams.get(key);
    return value && allowed.includes(value as T) ? (value as T) : fallback;
  };
  const windowValue = safeParam('window', ['7d', '30d', '90d'] as const, '30d');
  const model = safeParam('model', ['all', 'doubao', 'deepseek', 'yuanbao'] as const, 'all');
  const mode = safeParam('mode', ['all', 'quick', 'deep'] as const, 'all');
  const region = safeParam('region', ['all', 'east', 'north', 'south'] as const, 'all');
  useEffect(() => {
    if (experience?.source !== 'live' || !experience.projectPubId) return;
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveState('failed');
      return;
    }
    setLiveState('loading');
    const end = new Date();
    const days = Number.parseInt(windowValue, 10);
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - days + 1);
    void getAnalyticsOverview(
      experience.projectPubId,
      start.toISOString().slice(0, 10),
      end.toISOString().slice(0, 10),
      {
        ...(model !== 'all' ? { model } : {}),
        ...(region !== 'all' ? { region } : {}),
        ...(mode !== 'all' ? { mode } : {}),
      },
      headers,
    ).then((result) => {
      if (result.kind === 'ready') {
        setLiveMetrics(result.data);
        setLiveState('ready');
      } else {
        setLiveMetrics(null);
        setLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
  }, [experience, model, mode, region, windowValue]);
  const metricValue = (name: string, percent = false): string => {
    const metric = liveMetrics?.find((item) => item.metric === name);
    if (!metric || metric.value === null) return '—';
    return percent ? `${(metric.value * 100).toFixed(1)}%` : metric.value.toFixed(2);
  };
  const metricDetail = (name: string): string => {
    const metric = liveMetrics?.find((item) => item.metric === name);
    return metric
      ? `${metric.numerator ?? '—'} / ${metric.denominator} · ${
          containsClientSecret(metric.state) ? '状态已隐藏' : metric.state
        }`
      : '暂无可用事实';
  };
  const updateFilter = (
    key: 'window' | 'model' | 'mode' | 'region',
    value: string,
    fallback: string,
    replace = false,
  ) => {
    const next = new URLSearchParams(searchParams);
    if (value === fallback) next.delete(key);
    else next.set(key, value);
    void setSearchParams(next, { replace });
  };
  return (
    <>
      <FilterBar label="监测筛选">
        <label>
          时间窗口
          <select
            aria-label="时间窗口"
            value={windowValue}
            onChange={(event) => updateFilter('window', event.target.value, '30d')}
          >
            <option value="7d">近 7 天</option>
            <option value="30d">近 30 天</option>
            <option value="90d">近 90 天</option>
          </select>
        </label>
        <label>
          模型
          <select
            aria-label="模型"
            value={model}
            onChange={(event) => updateFilter('model', event.target.value, 'all')}
          >
            <option value="all">全部模型</option>
            <option value="doubao">豆包</option>
            <option value="deepseek">DeepSeek</option>
            <option value="yuanbao">元宝</option>
          </select>
        </label>
        <label>
          回答模式
          <select
            aria-label="回答模式"
            value={mode}
            onChange={(event) => updateFilter('mode', event.target.value, 'all')}
          >
            <option value="all">全部模式</option>
            <option value="quick">快速</option>
            <option value="deep">深度思考</option>
          </select>
        </label>
        <label>
          地域
          <select
            aria-label="监测地域"
            value={region}
            onChange={(event) => updateFilter('region', event.target.value, 'all')}
          >
            <option value="all">全部地域</option>
            <option value="east">华东</option>
            <option value="north">华北</option>
            <option value="south">华南</option>
          </select>
        </label>
        <button
          className="button button-secondary"
          onClick={() => {
            const next = new URLSearchParams(searchParams);
            next.delete('window');
            next.delete('model');
            next.delete('mode');
            next.delete('region');
            void setSearchParams(next, { replace: true });
          }}
        >
          重置筛选
        </button>
      </FilterBar>
      {liveState === 'loading' ? <StatePanel state="loading" /> : null}
      {liveState === 'failed' ? <StatePanel state="failed" /> : null}
      {liveState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
      {liveState === 'ready' && liveMetrics?.length === 0 ? <StatePanel state="empty" /> : null}
      <MetricGrid
        metrics={
          liveState === 'ready'
            ? [
                {
                  label: '品牌提及率',
                  value: metricValue('mention_rate', true),
                  detail: metricDetail('mention_rate'),
                },
                {
                  label: '平均排名',
                  value: metricValue('average_rank'),
                  detail: metricDetail('average_rank'),
                },
                {
                  label: 'Top 3 占比',
                  value: metricValue('top3_rate', true),
                  detail: metricDetail('top3_rate'),
                },
                {
                  label: '引用覆盖',
                  value: metricValue('citation_coverage', true),
                  detail: metricDetail('citation_coverage'),
                },
              ]
            : [
                {
                  label: '品牌提及率',
                  value: '68.4%',
                  detail: 'Contract fixture · 26 / 38',
                },
                { label: '平均排名', value: '2.4', detail: 'Contract fixture · 38 样本' },
                { label: 'Top 3 占比', value: '73.7%', detail: 'Contract fixture · 28 / 38' },
                { label: '引用覆盖', value: '55.3%', detail: 'Contract fixture · 21 / 38' },
              ]
        }
      />
      <div className="two-column">
        <section className="panel">
          <h2>模型表现</h2>
          <p className="panel-subtitle">同一冻结窗口，不把未准入平台混入比较。</p>
          <GeoBarChart
            title="各模型品牌提及率"
            valueSuffix="%"
            data={[
              { label: '豆包', value: 82, state: 'ready' },
              { label: 'DeepSeek', value: 71, state: 'ready' },
              { label: '元宝', value: 64, state: 'ready' },
              { label: 'Kimi', value: 57, state: 'ready' },
            ]}
          />
        </section>
        <section className="panel">
          <h2>数据诚实状态</h2>
          <p className="panel-subtitle">每种状态有独立语义。</p>
          <StatePanel state="insufficient" />
        </section>
      </div>
      <div className="two-column">
        <section className="panel">
          <h2>趋势</h2>
          <p className="panel-subtitle">按冻结日展示品牌提及率，不用累计值冒充单日表现。</p>
          <GeoBarChart
            title="近五个冻结日品牌提及率趋势"
            valueSuffix="%"
            data={[
              { label: '07-17', value: 61, state: 'ready' },
              { label: '07-19', value: 63, state: 'ready' },
              { label: '07-21', value: 65, state: 'ready' },
              { label: '07-23', value: 67, state: 'ready' },
              { label: '07-24', value: 68.4, state: 'ready' },
            ]}
          />
        </section>
        <section className="panel">
          <h2>竞品表现</h2>
          <p className="panel-subtitle">仅比较客户确认的竞品集合。</p>
          <GeoBarChart
            title="品牌与确认竞品提及率"
            valueSuffix="%"
            data={[
              { label: '澄明云', value: 68.4, state: 'ready' },
              { label: '北辰智库', value: 52.6, state: 'ready' },
              { label: '知川平台', value: 41.2, state: 'ready' },
            ]}
          />
        </section>
      </div>
      <section className="panel">
        <h2>地域与回答模式</h2>
        <p className="panel-subtitle">
          地域和模式使用同一指标版本；“深度思考”不是更高质量结论的替代口径。
        </p>
        <TableRegion label="地域与回答模式表现">
          <table className="data-table">
            <thead>
              <tr>
                <th>地域</th>
                <th>模式</th>
                <th>有效回答</th>
                <th>品牌提及率</th>
                <th>平均排名</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>华东</td>
                <td>深度思考</td>
                <td>14</td>
                <td>71.4%</td>
                <td>2.1</td>
              </tr>
              <tr>
                <td>华北</td>
                <td>快速</td>
                <td>12</td>
                <td>66.7%</td>
                <td>2.5</td>
              </tr>
              <tr>
                <td>华南</td>
                <td>深度思考</td>
                <td>12</td>
                <td>66.7%</td>
                <td>2.7</td>
              </tr>
            </tbody>
          </table>
        </TableRegion>
      </section>
      <section className="panel">
        <h2>问题级表现</h2>
        <p className="panel-subtitle">可从指标下钻到贡献回答与证据。</p>
        <QuestionTable />
      </section>
    </>
  );
}

function Accounts() {
  const experience = useOptionalExperienceContext();
  const live = experience?.source === 'live';
  const [safeAccount, setSafeAccount] = useState(account);
  const [liveAccountPubId, setLiveAccountPubId] = useState('');
  const [livePairingPubId, setLivePairingPubId] = useState('');
  const [integrationState, setIntegrationState] = useState<
    'fixture' | 'loading' | 'ready' | 'empty' | 'failed' | 'forbidden'
  >(live ? 'loading' : 'fixture');
  const [safeEvents, setSafeEvents] = useState<{ type: string; occurredAt: string }[]>([]);
  const [liveActionMessage, setLiveActionMessage] = useState('');
  const [authorizationSaved, setAuthorizationSaved] = useState(false);
  const [showRevocationGuide, setShowRevocationGuide] = useState(false);
  const [stage, setStage] = useState<
    | 'registered'
    | 'pairing'
    | 'paired'
    | 'waiting'
    | 'completed'
    | 'refused'
    | 'timed_out'
    | 'revoked'
  >('registered');
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<AuthorizationFields>({
    resolver: zodResolver(authorizationSchema),
    defaultValues: {
      platformSlug: 'doubao',
      accountMask: '尾号 · 4821',
      owner: '林澄',
      responsible: '周岚',
      custodyMode: 'hybrid',
      expiresOn: '2026-09-30',
      region: '中国大陆 · 华东',
      scopes: ['read', 'query'],
    },
  });
  const applyLiveAccount = (value: CustomerAccountView): string => {
    const pubId = safeOpaqueId(value.pub_id, 'pac_');
    if (!pubId) return '';
    setSafeAccount(projectCustomerAccount(value));
    setLiveAccountPubId(pubId);
    setIntegrationState('ready');
    return pubId;
  };
  const refreshEvents = async (accountPubId: string) => {
    const headers = getValidatedIdentityHeaders();
    if (!headers) return;
    const result = await listCustomerAccountEvents(accountPubId, headers);
    if (result.kind === 'ready') setSafeEvents(projectCustomerEvents(result.data));
  };
  useEffect(() => {
    if (!live) {
      setIntegrationState('fixture');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setIntegrationState('forbidden');
      return;
    }
    let active = true;
    setIntegrationState('loading');
    void listCustomerAccounts(headers).then((result) => {
      if (!active) return;
      if (result.kind === 'forbidden') {
        setIntegrationState('forbidden');
        return;
      }
      if (result.kind === 'unavailable') {
        setIntegrationState('failed');
        return;
      }
      const first = result.data[0];
      if (!first) {
        setIntegrationState('empty');
        return;
      }
      const pubId = applyLiveAccount(first);
      if (!pubId) {
        setIntegrationState('failed');
        return;
      }
      void refreshEvents(pubId);
    });
    return () => {
      active = false;
    };
  }, [live]);
  const saveAuthorization = async (fields: AuthorizationFields) => {
    setLiveActionMessage('');
    if (live) {
      const headers = getValidatedIdentityHeaders();
      if (!headers) {
        setIntegrationState('forbidden');
        return;
      }
      let accountPubId = liveAccountPubId;
      if (!accountPubId) {
        const registration = await registerCustomerAccount(
          {
            platform_slug: fields.platformSlug,
            platform_name: fields.platformSlug === 'doubao' ? '豆包' : fields.platformSlug,
            account_mask: fields.accountMask,
            custody_mode:
              fields.custodyMode === 'customer-device' ? 'customer_device' : fields.custodyMode,
            region: fields.region,
          },
          headers,
        );
        if (registration.kind !== 'ready') {
          setIntegrationState(registration.kind === 'forbidden' ? 'forbidden' : 'failed');
          return;
        }
        accountPubId = applyLiveAccount(registration.data);
        if (!accountPubId) {
          setIntegrationState('failed');
          return;
        }
      }
      const authorization = await authorizeCustomerAccount(
        accountPubId,
        {
          scopes: fields.scopes,
          forbidden_actions: ['delete', 'pay', 'direct_message', 'security_settings'],
          regions: [fields.region],
          valid_until: new Date(`${fields.expiresOn}T23:59:59+08:00`).toISOString(),
        },
        headers,
      );
      if (authorization.kind !== 'ready') {
        setIntegrationState(authorization.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      applyLiveAccount(authorization.data);
      await refreshEvents(accountPubId);
      setLiveActionMessage('真实 API 已登记；owner 与责任人由已验证客户身份绑定。');
    } else {
      setSafeAccount({
        ...safeAccount,
        accountMask: fields.accountMask,
        platformLabel: fields.platformSlug === 'doubao' ? '豆包' : fields.platformSlug,
        ownerLabel: `账号 owner · ${fields.owner} / 责任人 · ${fields.responsible}`,
        custodyMode: fields.custodyMode,
        scopes: fields.scopes,
        expiresLabel: fields.expiresOn,
        regionLabel: fields.region,
      });
    }
    setAuthorizationSaved(true);
  };
  const confirmPairing = async () => {
    if (!live) {
      setStage('paired');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers || !liveAccountPubId) {
      setIntegrationState('forbidden');
      return;
    }
    const action = safeAccount.scopes.includes('read') ? 'read' : safeAccount.scopes[0];
    if (!action) {
      setLiveActionMessage('当前没有可用于配对的授权动作。');
      return;
    }
    const result = await createCustomerPairing(
      liveAccountPubId,
      { allowed_domain: 'doubao.com', action, challenge_type: 'qr' },
      headers,
    );
    if (result.kind !== 'ready') {
      setIntegrationState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      return;
    }
    const pairingPubId = safeOpaqueId(result.data.pub_id, 'int_');
    if (!pairingPubId) {
      setIntegrationState('failed');
      return;
    }
    setLivePairingPubId(pairingPubId);
    setLiveActionMessage('真实 API 已创建待处理配对；一次性 payload 仅由受控终端生成。');
    setStage('paired');
    await refreshEvents(liveAccountPubId);
  };
  const refreshPairing = async () => {
    const headers = getValidatedIdentityHeaders();
    if (!headers || !liveAccountPubId || !livePairingPubId) {
      setIntegrationState('forbidden');
      return;
    }
    const result = await listCustomerPairings(liveAccountPubId, headers);
    if (result.kind !== 'ready') {
      setIntegrationState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      return;
    }
    const current = result.data.find((item) => item.pub_id === livePairingPubId);
    const next =
      current?.state === 'completed'
        ? 'completed'
        : current?.state === 'refused'
          ? 'refused'
          : current?.state === 'timed_out' || current?.state === 'expired'
            ? 'timed_out'
            : 'waiting';
    setStage(next);
    setLiveActionMessage(
      next === 'waiting'
        ? '受控终端仍在处理；客户页面未接收任何挑战秘密。'
        : `真实配对状态已更新为 ${next}。`,
    );
    await refreshEvents(liveAccountPubId);
  };
  const revokeAuthorization = async () => {
    if (live) {
      const headers = getValidatedIdentityHeaders();
      if (!headers || !liveAccountPubId) {
        setIntegrationState('forbidden');
        return;
      }
      const result = await revokeCustomerAccount(liveAccountPubId, headers);
      if (result.kind !== 'ready') {
        setIntegrationState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      setLiveActionMessage('真实撤销工作流已受理；回执将在工作流完成后更新。');
    }
    setStage('revoked');
  };
  const custodyLabel = {
    server: '服务器托管',
    'customer-device': '客户终端托管',
    hybrid: '混合托管',
  }[safeAccount.custodyMode];
  const scopeLabel = safeAccount.scopes.join(' / ') || '无';
  const intervention =
    stage === 'pairing' || stage === 'waiting'
      ? 'waiting'
      : stage === 'paired'
        ? 'paired'
        : stage === 'completed'
          ? 'completed'
          : stage === 'refused'
            ? 'refused'
            : stage === 'timed_out'
              ? 'timed_out'
              : 'none';
  if (live && integrationState === 'loading') {
    return (
      <section className="panel">
        <h2>平台账号与授权</h2>
        <StatePanel state="loading" />
      </section>
    );
  }
  if (live && integrationState === 'forbidden') {
    return (
      <section className="panel">
        <h2>平台账号与授权</h2>
        <StatePanel state="forbidden" />
      </section>
    );
  }
  return (
    <>
      <section className="panel">
        <h2>平台账号与授权</h2>
        <p className="panel-subtitle">
          账号所有权、最小动作范围、期限、地域、责任人与撤销权均可审计。
        </p>
        <div className="button-row">
          <Badge tone={live ? 'positive' : 'warning'}>
            {live ? '客户安全投影 · 真实 API' : 'Contract fixture'}
          </Badge>
        </div>
        {live && integrationState === 'failed' ? (
          <StatePanel state="failed" onRetry={() => location.reload()} />
        ) : null}
        {live && integrationState === 'empty' ? (
          <StatePanel state="empty" />
        ) : (
          <AccountSummary account={safeAccount} />
        )}
        <form className="form-grid" onSubmit={handleSubmit(saveAuthorization)} noValidate>
          <Field id="account-platform" label="目标平台" error={errors.platformSlug}>
            <select id="account-platform" {...register('platformSlug')}>
              <option value="doubao">豆包</option>
            </select>
          </Field>
          <Field id="account-mask" label="账号掩码" error={errors.accountMask}>
            <input id="account-mask" autoComplete="off" {...register('accountMask')} />
          </Field>
          <Field id="account-owner" label="账号 owner" error={errors.owner}>
            <input id="account-owner" autoComplete="off" {...register('owner')} />
          </Field>
          <Field id="account-responsible" label="运营责任人" error={errors.responsible}>
            <input id="account-responsible" autoComplete="off" {...register('responsible')} />
          </Field>
          <Field id="account-custody" label="托管模式" error={errors.custodyMode}>
            <select id="account-custody" {...register('custodyMode')}>
              <option value="customer-device">客户终端托管</option>
              <option value="hybrid">混合托管</option>
              <option value="server">服务器托管</option>
            </select>
          </Field>
          <Field id="account-expiry" label="授权到期日" error={errors.expiresOn}>
            <input id="account-expiry" type="date" {...register('expiresOn')} />
          </Field>
          <Field id="account-region" label="授权地域" error={errors.region}>
            <input id="account-region" autoComplete="off" {...register('region')} />
          </Field>
          <fieldset className="field">
            <legend>允许动作</legend>
            <div className="scope-row">
              {(['read', 'query', 'draft', 'publish'] as const).map((scope) => (
                <label className="checkbox-line" key={scope}>
                  <input type="checkbox" value={scope} {...register('scopes')} />
                  {scope}
                </label>
              ))}
            </div>
            {errors.scopes ? <span className="field-error">{errors.scopes.message}</span> : null}
          </fieldset>
          <div className="form-actions">
            <button className="button" type="submit">
              登记授权
            </button>
            {authorizationSaved ? (
              <Toast>
                授权登记已更新；配对范围将采用当前安全投影。
                {liveActionMessage ? ` ${liveActionMessage}` : ''}
              </Toast>
            ) : null}
          </div>
        </form>
      </section>
      <section className="panel" aria-labelledby="pairing-title">
        <div className="account-head">
          <div>
            <span className="overline">Customer terminal</span>
            <h2 id="pairing-title">客户终端安全配对</h2>
          </div>
          <InterventionStatus value={intervention} />
        </div>
        <ol className="flow-steps" aria-label="配对进度" tabIndex={0}>
          {['登记授权', '选择托管', '安全配对', '原生验证', '健康确认'].map((label, index) => (
            <li
              key={label}
              aria-current={
                (stage === 'registered' && index === 1) ||
                (['pairing', 'paired'].includes(stage) && index === 2) ||
                (stage === 'waiting' && index === 3) ||
                (stage === 'completed' && index === 4)
                  ? 'step'
                  : undefined
              }
            >
              {label}
            </li>
          ))}
        </ol>
        {stage === 'registered' ? (
          <div className="pairing-body">
            <div>
              <h3>
                {custodyLabel} · {scopeLabel}
              </h3>
              <p>
                敏感验证留在客户终端，日常获授权查询可由隔离 Runner 执行。配对令牌单次使用，10
                分钟过期。
              </p>
            </div>
            <button className="button" onClick={() => setStage('pairing')}>
              创建一次性配对
            </button>
          </div>
        ) : null}
        {stage === 'pairing' ? (
          <div className="pairing-confirm">
            <h3>请二次确认本次任务</h3>
            <dl className="definition-grid">
              <div>
                <dt>账号掩码</dt>
                <dd>{safeAccount.accountMask}</dd>
              </div>
              <div>
                <dt>目标平台</dt>
                <dd>{safeAccount.platformLabel}</dd>
              </div>
              <div>
                <dt>允许动作</dt>
                <dd>{scopeLabel}</dd>
              </div>
              <div>
                <dt>允许域名</dt>
                <dd>doubao.com</dd>
              </div>
              <div>
                <dt>到期时间</dt>
                <dd>10 分钟后</dd>
              </div>
              <div>
                <dt>目标地域</dt>
                <dd>{safeAccount.regionLabel}</dd>
              </div>
              <div>
                <dt>秘密传输</dt>
                <dd>禁止</dd>
              </div>
            </dl>
            <p className="security-note">
              请勿在聊天或普通表单粘贴验证码、Cookie 或
              token。后续操作只在目标平台原生页面或受控终端完成。
            </p>
            <div className="button-row">
              <button className="button button-secondary" onClick={() => setStage('refused')}>
                拒绝
              </button>
              <button className="button" onClick={() => void confirmPairing()}>
                确认并生成配对码
              </button>
            </div>
          </div>
        ) : null}
        {stage === 'paired' ? (
          <div className="pairing-body">
            <div className="pairing-code">
              <div
                className="safe-pairing-qr"
                role="img"
                aria-label="一次性安全配对二维码；实际 payload 仅在受控终端通道中提供，不进入页面文本、URL、截图或日志"
              >
                <span aria-hidden="true" />
              </div>
              <div>
                <Badge tone="info">{live ? '真实配对待受控终端处理' : '一次性链接 · 09:59'}</Badge>
                <h3>在已登记终端打开安全链接</h3>
                <p>
                  终端通道仅允许 {safeAccount.platformLabel}、doubao.com 和 {scopeLabel}
                  动作。二维码视觉不包含可提取的配对 payload；真实内容只进入受控终端通道。
                </p>
              </div>
            </div>
            <div className="button-row">
              {live ? (
                <button className="button button-secondary" onClick={() => void refreshPairing()}>
                  刷新真实配对状态
                </button>
              ) : (
                <button className="button button-secondary" onClick={() => setStage('timed_out')}>
                  模拟超时
                </button>
              )}
              <button className="button" onClick={() => setStage('waiting')}>
                终端已连接
              </button>
            </div>
          </div>
        ) : null}
        {stage === 'waiting' ? (
          <div className="pairing-confirm">
            <Badge tone="warning">等待目标平台</Badge>
            <h3>请在豆包原生页面完成验证</h3>
            <p>
              支持 OTP、官方 App 扫码、Push MFA、passkey、人脸/活体跳转和图形
              challenge。平台页面完成后只返回成功、失败或过期状态，不上传验证码或生物材料。
            </p>
            <div className="button-row">
              {live ? (
                <span className="security-note">
                  拒绝和挑战输入只在受控终端完成；客户页面通过刷新读取真实结果。
                </span>
              ) : (
                <button className="button button-secondary" onClick={() => setStage('refused')}>
                  拒绝本次操作
                </button>
              )}
              <button
                className="button"
                onClick={() => (live ? void refreshPairing() : setStage('completed'))}
              >
                {live ? '刷新真实配对状态' : '模拟平台确认完成'}
              </button>
            </div>
          </div>
        ) : null}
        {stage === 'completed' ? (
          <div className="pairing-body">
            <div>
              <Badge tone="positive">身份探针通过</Badge>
              <h3>配对与验证已完成</h3>
              <p>
                账号 opaque identity 匹配；本次仅验证登录与 read，准入保持 read_verified。授权范围为{' '}
                {scopeLabel}，draft/publish 不会因登记授权被描述为已完成 live 验证。
              </p>
            </div>
            <button className="button button-secondary" onClick={() => void revokeAuthorization()}>
              撤销授权
            </button>
          </div>
        ) : null}
        {stage === 'refused' || stage === 'timed_out' ? (
          <div className="pairing-body">
            <div>
              <InterventionStatus value={stage} />
              <h3>{stage === 'refused' ? '本次配对已拒绝' : '一次性配对已超时'}</h3>
              <p>通道和一次性令牌已销毁，没有改变现有授权或会话。</p>
            </div>
            <button className="button" onClick={() => setStage('registered')}>
              重新开始
            </button>
          </div>
        ) : null}
        {stage === 'revoked' && !live ? (
          <RevocationReceipt
            receipt={{
              receiptId: 'rvr_01K0SAFE9Y',
              revokedAtLabel: '刚刚',
              actorLabel: '客户管理员 · 林澄',
              leasesStopped: true,
              sessionsClosed: true,
              secretCopiesPurged: true,
            }}
          />
        ) : null}
        {stage === 'revoked' && live ? (
          <div className="pairing-body">
            <div>
              <Badge tone="warning">撤销工作流处理中</Badge>
              <h3>等待真实撤销回执</h3>
              <p>新租约已停止受理；只有后端完成会话关闭和秘密副本删除验证后才展示回执。</p>
            </div>
          </div>
        ) : null}
      </section>
      <section className="panel">
        <h2>账号安全事件</h2>
        <p className="panel-subtitle">
          {live
            ? '来自客户安全事件投影；不包含账号秘密。'
            : 'Contract fixture：真实 API 会话可用后替换。'}
        </p>
        {safeEvents.length ? (
          <ol className="workflow-list" aria-label="账号安全事件">
            {safeEvents.map((event) => (
              <li key={`${event.type}-${event.occurredAt}`}>
                <strong>{event.type}</strong> · {event.occurredAt}
              </li>
            ))}
          </ol>
        ) : (
          <StatePanel state="empty" />
        )}
      </section>
      <div className="card-grid">
        <article className="action-card">
          <span className="overline">安全配对</span>
          <h3>一次性、限域、限动作</h3>
          <p>
            核对账号掩码、目标平台、允许动作、允许域名和到期时间后，在目标平台原生页面完成验证。
          </p>
        </article>
        <article className="action-card">
          <span className="overline">禁止动作</span>
          <h3>默认拒绝高风险操作</h3>
          <p>支付、删除、私信、修改安全设置、绑定手机和未审批发布均不在授权范围。</p>
          <Badge tone="warning">最小权限</Badge>
        </article>
        <article className="action-card">
          <span className="overline">撤销权</span>
          <h3>随时终止托管</h3>
          <p>撤销会停止新租约、关闭活动会话并生成不含秘密的撤销回执。</p>
          <button className="button button-secondary" onClick={() => setShowRevocationGuide(true)}>
            查看撤销流程
          </button>
        </article>
      </div>
      {showRevocationGuide ? (
        <Dialog
          title="客户撤销权与执行顺序"
          eyebrow="Revocation guide"
          closeLabel="关闭撤销流程"
          onClose={() => setShowRevocationGuide(false)}
        >
          <ol className="workflow-list">
            <li>立即拒绝新租约与新动作</li>
            <li>关闭活动会话并终止待处理配对</li>
            <li>删除托管秘密副本；客户设备上的平台凭据仍由客户在原生页面管理</li>
            <li>生成不含 Cookie、token、profile 或生物材料的撤销回执</li>
          </ol>
          <p className="security-note">撤销不要求客户再次提交 OTP、Cookie 或 token。</p>
        </Dialog>
      ) : null}
    </>
  );
}

const profileSchema = z.object({
  companyName: z
    .string()
    .trim()
    .min(2, '请输入至少 2 个字的企业名称')
    .max(80)
    .refine(noClientSecret, noClientSecretMessage),
  contactRole: z
    .string()
    .trim()
    .min(2, '请填写联系人角色')
    .max(40)
    .refine(noClientSecret, noClientSecretMessage),
  audience: z
    .string()
    .trim()
    .min(10, '请用至少 10 个字描述目标客户')
    .max(500)
    .refine(noClientSecret, noClientSecretMessage),
  publicStatement: z
    .string()
    .trim()
    .min(10, '请填写可公开核验的企业说明')
    .max(800)
    .refine(noClientSecret, noClientSecretMessage),
  truthConfirmed: z.boolean().refine((value) => value, '提交前必须确认资料真实性'),
});
type ProfileFormValue = z.infer<typeof profileSchema>;

function ProfileWorkspace() {
  const [savedAt, setSavedAt] = useState('尚未保存');
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<ProfileFormValue>({
    resolver: zodResolver(profileSchema),
    defaultValues: {
      companyName: '云岫智能科技有限公司',
      contactRole: '品牌负责人',
      audience: '需要安全部署企业知识库与智能问答的制造业数字化团队',
      publicStatement: '云岫智能提供企业知识检索、问答与治理服务，支持私有化部署。',
      truthConfirmed: false,
    },
  });
  const submit = handleSubmit(async () => {
    await Promise.resolve();
    setSavedAt(
      `客户声明 v3 · ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`,
    );
  });
  return (
    <div className="workspace-grid">
      <form className="panel form-panel" onSubmit={(event) => void submit(event)} noValidate>
        <span className="overline">Client declaration</span>
        <h2>甲方资料</h2>
        <p className="panel-subtitle">
          客户声明、AI 草稿和 GEO 规范化结果分别保存，任何一方都不能静默覆盖另一方。
        </p>
        <div className="form-grid">
          <Field id="companyName" label="企业全称" error={errors.companyName}>
            <input
              id="companyName"
              aria-invalid={Boolean(errors.companyName)}
              aria-describedby={errors.companyName ? 'companyName-error' : undefined}
              {...register('companyName')}
            />
          </Field>
          <Field id="contactRole" label="责任人角色" error={errors.contactRole}>
            <input id="contactRole" {...register('contactRole')} />
          </Field>
          <Field id="audience" label="目标客户" error={errors.audience}>
            <textarea id="audience" rows={4} {...register('audience')} />
          </Field>
          <Field
            id="publicStatement"
            label="可公开核验说明"
            error={errors.publicStatement}
            hint="仅填写可由官网、资质或公开材料证明的事实。"
          >
            <textarea id="publicStatement" rows={4} {...register('publicStatement')} />
          </Field>
        </div>
        <label className="check-field">
          <input type="checkbox" {...register('truthConfirmed')} />
          我确认上述客户声明真实、可核验，并理解修改会生成新版本。
        </label>
        {errors.truthConfirmed ? (
          <span className="field-error" role="alert">
            {errors.truthConfirmed.message}
          </span>
        ) : null}
        <div className="form-actions">
          <span aria-live="polite">
            {savedAt !== '尚未保存' ? savedAt : isDirty ? '有未保存修改' : savedAt}
          </span>
          <button className="button" disabled={isSubmitting}>
            {isSubmitting ? '正在提交' : '保存并生成版本'}
          </button>
        </div>
      </form>
      <aside className="panel timeline-panel">
        <h2>字段历史</h2>
        <ol className="timeline">
          <li>
            <strong>客户声明 v2</strong>
            <span>林澄 · 今天 09:18</span>
          </li>
          <li>
            <strong>AI 调研草稿</strong>
            <span>仅建议，未覆盖客户值</span>
          </li>
          <li>
            <strong>客户声明 v1</strong>
            <span>项目创建时</span>
          </li>
        </ol>
      </aside>
    </div>
  );
}

const assetSchema = z.object({
  brandName: z
    .string()
    .trim()
    .min(2, '请输入品牌名称')
    .max(60)
    .refine(noClientSecret, noClientSecretMessage),
  website: z
    .url('请输入完整 HTTPS 官网地址')
    .refine((value) => value.startsWith('https://'), '官网必须使用 HTTPS')
    .refine(noClientSecret, noClientSecretMessage),
  productName: z
    .string()
    .trim()
    .min(2, '请输入产品或服务名称')
    .max(80)
    .refine(noClientSecret, noClientSecretMessage),
  competitor: z
    .string()
    .trim()
    .min(2, '请输入客户确认的竞品')
    .max(80)
    .refine(noClientSecret, noClientSecretMessage),
  forbiddenClaim: z.string().trim().max(300).refine(noClientSecret, noClientSecretMessage),
});
type AssetFormValue = z.infer<typeof assetSchema>;

function AssetsWorkspace() {
  const [brands, setBrands] = useState([
    { brand: '云岫 AI', product: '企业知识中枢', competitor: '星河智库' },
  ]);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<AssetFormValue>({
    resolver: zodResolver(assetSchema),
    defaultValues: {
      brandName: '',
      website: 'https://',
      productName: '',
      competitor: '',
      forbiddenClaim: '',
    },
  });
  const submit = handleSubmit((value) => {
    setBrands((current) => [
      ...current,
      { brand: value.brandName, product: value.productName, competitor: value.competitor },
    ]);
    reset({
      brandName: '',
      website: 'https://',
      productName: '',
      competitor: '',
      forbiddenClaim: '',
    });
  });
  return (
    <>
      <section className="panel">
        <span className="overline">Brand registry</span>
        <h2>品牌、产品与竞品</h2>
        <p className="panel-subtitle">
          仅展示客户确认的资产；潜在别名、隐性竞品和内部消歧置信度不会暴露。
        </p>
        <div className="asset-list">
          {brands.map((item) => (
            <article className="asset-row" key={`${item.brand}-${item.product}`}>
              <div>
                <strong>{item.brand}</strong>
                <span>{item.product}</span>
              </div>
              <div>
                <small>客户指定竞品</small>
                <b>{item.competitor}</b>
              </div>
              <Badge tone="positive">已确认</Badge>
            </article>
          ))}
        </div>
      </section>
      <form className="panel form-panel" onSubmit={(event) => void submit(event)} noValidate>
        <h2>登记品牌资产</h2>
        <div className="form-grid form-grid-three">
          <Field id="brandName" label="品牌名称" error={errors.brandName}>
            <input id="brandName" {...register('brandName')} />
          </Field>
          <Field id="website" label="官方 HTTPS 网站" error={errors.website}>
            <input id="website" inputMode="url" {...register('website')} />
          </Field>
          <Field id="productName" label="产品或服务" error={errors.productName}>
            <input id="productName" {...register('productName')} />
          </Field>
          <Field id="competitor" label="客户指定竞品" error={errors.competitor}>
            <input id="competitor" {...register('competitor')} />
          </Field>
          <Field id="forbiddenClaim" label="禁止使用的表述" error={errors.forbiddenClaim}>
            <input
              id="forbiddenClaim"
              placeholder="例如：未经证明的“行业第一”"
              {...register('forbiddenClaim')}
            />
          </Field>
        </div>
        <div className="form-actions">
          <span>提交后进入客户确认版本，不自动改变监测配置。</span>
          <button className="button">登记资产</button>
        </div>
      </form>
    </>
  );
}

const requestSchema = z.object({
  question: z
    .string()
    .trim()
    .min(8, '问题至少需要 8 个字')
    .max(200)
    .refine(noClientSecret, noClientSecretMessage),
  priority: z.enum(['high', 'medium', 'low']),
  goalMetric: z.enum(['mention_rate', 'top3_rate', 'citation_coverage']),
  target: z.number().min(0, '目标不能小于 0').max(100, '百分比目标不能超过 100'),
  changeType: z.enum(['add_query', 'pause', 'resume', 'backfill']),
  reason: z
    .string()
    .trim()
    .min(10, '请说明至少 10 个字的业务原因')
    .max(500)
    .refine(noClientSecret, noClientSecretMessage),
});
type RequestFormValue = z.infer<typeof requestSchema>;

function QuestionsWorkspace() {
  const experience = useOptionalExperienceContext();
  const [submitted, setSubmitted] = useState<RequestFormValue[]>([]);
  const [submissionState, setSubmissionState] = useState<
    'idle' | 'submitting' | 'saved' | 'failed' | 'forbidden'
  >('idle');
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<RequestFormValue>({
    resolver: zodResolver(requestSchema),
    defaultValues: {
      question: '',
      priority: 'medium',
      goalMetric: 'mention_rate',
      target: 70,
      changeType: 'add_query',
      reason: '',
    },
  });
  const submit = handleSubmit(async (value) => {
    setSubmissionState('submitting');
    if (experience?.source === 'live') {
      const headers = getValidatedIdentityHeaders();
      if (!headers || !experience.projectPubId) {
        setSubmissionState('forbidden');
        return;
      }
      const result = await createProjectResource(
        experience.projectPubId,
        'change-requests',
        {
          kind: value.changeType,
          state: 'pending',
          payload: {
            question: value.question,
            priority: value.priority,
            goal_metric: value.goalMetric,
            target_percent: value.target,
            reason: value.reason,
          },
        },
        headers,
        `customer-change-${crypto.randomUUID()}`,
      );
      if (result.kind !== 'ready') {
        setSubmissionState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
    }
    setSubmitted((current) => [value, ...current]);
    setSubmissionState('saved');
    reset({
      question: '',
      priority: 'medium',
      goalMetric: 'mention_rate',
      target: 70,
      changeType: 'add_query',
      reason: '',
    });
  });
  return (
    <div className="workspace-grid">
      <form className="panel form-panel" onSubmit={(event) => void submit(event)} noValidate>
        <span className="overline">Change request</span>
        <h2>问题、目标与配置申请</h2>
        <p className="panel-subtitle">
          客户提交的是待审核申请，不直接修改调度真源。运营审批、生效版本和审计事件分别记录。
        </p>
        <Field id="question" label="关注问题" error={errors.question}>
          <textarea
            id="question"
            rows={3}
            placeholder="例如：制造企业如何选择可私有化部署的知识库？"
            {...register('question')}
          />
        </Field>
        <div className="form-grid form-grid-three">
          <Field id="priority" label="优先级" error={errors.priority}>
            <select id="priority" {...register('priority')}>
              <option value="high">高</option>
              <option value="medium">中</option>
              <option value="low">低</option>
            </select>
          </Field>
          <Field id="goalMetric" label="目标指标" error={errors.goalMetric}>
            <select id="goalMetric" {...register('goalMetric')}>
              <option value="mention_rate">品牌提及率</option>
              <option value="top3_rate">Top 3 占比</option>
              <option value="citation_coverage">引用覆盖</option>
            </select>
          </Field>
          <Field id="target" label="目标值（%）" error={errors.target}>
            <input
              id="target"
              type="number"
              min="0"
              max="100"
              {...register('target', { valueAsNumber: true })}
            />
          </Field>
          <Field id="changeType" label="申请动作" error={errors.changeType}>
            <select id="changeType" {...register('changeType')}>
              <option value="add_query">新增问题</option>
              <option value="pause">申请暂停</option>
              <option value="resume">申请恢复</option>
              <option value="backfill">申请补采</option>
            </select>
          </Field>
        </div>
        <Field id="reason" label="业务原因" error={errors.reason}>
          <textarea id="reason" rows={3} {...register('reason')} />
        </Field>
        <div className="form-actions">
          <span>
            {experience?.source === 'live'
              ? '提交将通过生成的 OpenAPI client 写入幂等申请与审计记录。'
              : 'Contract fixture：提交仅保存在当前演示会话。'}
          </span>
          <button className="button" disabled={submissionState === 'submitting'}>
            {submissionState === 'submitting' ? '正在提交…' : '提交审核'}
          </button>
        </div>
        {submissionState === 'saved' ? <Toast>申请已进入待运营审核队列</Toast> : null}
        {submissionState === 'failed' ? (
          <Toast tone="negative">申请服务暂不可用；内容仍保留在表单中，请稍后重试。</Toast>
        ) : null}
        {submissionState === 'forbidden' ? (
          <Toast tone="negative">无权提交此项目申请，且不会探测或显示项目是否存在。</Toast>
        ) : null}
      </form>
      <aside className="panel">
        <h2>申请队列</h2>
        {submitted.length ? (
          <div className="request-list">
            {submitted.map((request, index) => (
              <article key={`${request.question}-${index}`}>
                <Badge tone="warning">待运营审核</Badge>
                <strong>{request.question}</strong>
                <span>
                  目标 {request.target}% · {request.changeType}
                </span>
              </article>
            ))}
          </div>
        ) : (
          <StatePanel state="empty" />
        )}
      </aside>
    </div>
  );
}

function HomeWorkspace() {
  return (
    <>
      <section className="hero-panel">
        <div>
          <span className="overline">Project stage</span>
          <h2>监测运行中</h2>
          <p>最近一次数据窗口已于今天 10:20 冻结，下一次采集预计今晚 22:00 开始。</p>
        </div>
        <div
          className="stage-progress"
          role="progressbar"
          aria-label="项目进度"
          aria-valuemin={0}
          aria-valuemax={6}
          aria-valuenow={4}
        >
          <span style={{ width: '67%' }} />
        </div>
        <ol className="stage-list">
          <li data-done="true">资料确认</li>
          <li data-done="true">品牌档案</li>
          <li data-done="true">问题确认</li>
          <li data-current="true">监测运行</li>
          <li>报告审核</li>
          <li>优化复测</li>
        </ol>
      </section>
      <MetricGrid
        metrics={[
          { label: '客户待办', value: '2', detail: '资料确认 1 · 报告问题 1' },
          { label: '今日任务', value: '38/40', detail: '2 条延迟', trend: '95%' },
          { label: '最新提及率', value: '68.4%', detail: '26 / 38 个有效回答', trend: '↑ 6.2%' },
          { label: '证据完整率', value: '92%', detail: '回答与信源截图' },
        ]}
      />
      <div className="two-column">
        <section className="panel">
          <h2>下一步</h2>
          <p className="panel-subtitle">系统只推荐一个最需要完成的客户动作。</p>
          <article className="next-action">
            <Badge tone="warning">今天到期</Badge>
            <h3>确认 Q3 报告中的目标口径</h3>
            <p>报告审核人对“Top 3 目标值”提出一个澄清问题，确认后才能发布。</p>
            <button className="button" onClick={() => navigateCustomerSection('reports')}>
              前往报告
            </button>
          </article>
        </section>
        <section className="panel">
          <h2>数据新鲜度</h2>
          <p className="panel-subtitle">真实状态与最后可用版本分开显示。</p>
          <StatePanel state="delayed" />
        </section>
      </div>
    </>
  );
}

type AnswerFixture = {
  id: string;
  question: string;
  model: string;
  mode: string;
  region: string;
  answer: string;
  cited: string[];
  mention: boolean;
  capturedAt: string;
};
const answerFixtures: AnswerFixture[] = [
  {
    id: 'ans_01',
    question: '企业知识库如何选择？',
    model: '豆包',
    mode: 'deep',
    region: '上海',
    answer:
      '选择企业知识库时，需要同时评估数据权限、检索质量、更新机制与部署边界。云岫 AI 提供私有化知识治理与可追溯问答能力。[1]',
    cited: ['云岫智能产品白皮书', '工信部数据安全指南'],
    mention: true,
    capturedAt: '今天 09:42',
  },
  {
    id: 'ans_02',
    question: '适合制造业的 AI 平台有哪些？',
    model: 'DeepSeek',
    mode: 'quick',
    region: '江苏',
    answer:
      '制造业通常需要兼顾现场网络条件、知识更新频率和权限隔离，可优先评估具备本地部署与审计能力的平台。[1][2]',
    cited: ['制造业数字化转型指南', '企业知识工程实践'],
    mention: false,
    capturedAt: '今天 09:36',
  },
  {
    id: 'ans_03',
    question: '私有化大模型方案对比',
    model: '元宝',
    mode: 'quick',
    region: '北京',
    answer:
      '方案比较应覆盖基础模型、知识检索、应用编排、运维与安全治理五个层次。当前证据中没有足够信息形成品牌推荐。',
    cited: [],
    mention: false,
    capturedAt: '今天 09:28',
  },
  {
    id: 'ans_04',
    question: '知识库如何验证回答来源？',
    model: '豆包',
    mode: 'deep',
    region: '广东',
    answer:
      '可追溯系统应保存答案、引用规范化地址、页面快照、文本锚点和采集时间，并允许回看历史差异。[1]',
    cited: ['可信 AI 系统工程规范'],
    mention: true,
    capturedAt: '昨天 22:18',
  },
];

function downloadTextFile(name: string, mime: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

function EvidenceWorkspace() {
  const experience = useOptionalExperienceContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const [liveAnswers, setLiveAnswers] = useState<AnswerFixture[] | null>(null);
  const [liveAnswerState, setLiveAnswerState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
  const model = ['all', 'doubao', 'deepseek', 'yuanbao'].includes(
    searchParams.get('answer_model') ?? '',
  )
    ? searchParams.get('answer_model')!
    : 'all';
  const mode = ['all', 'quick', 'deep'].includes(searchParams.get('answer_mode') ?? '')
    ? searchParams.get('answer_mode')!
    : 'all';
  const region = ['all', '上海', '江苏', '北京', '广东'].includes(
    searchParams.get('answer_region') ?? '',
  )
    ? searchParams.get('answer_region')!
    : 'all';
  const rawQuery = searchParams.get('answer_query') ?? '';
  const containsSecret = containsClientSecret(rawQuery);
  const query = containsSecret ? '' : rawQuery.slice(0, 80);
  const requestedPage = searchParams.get('answer_page') === '2' ? 2 : 1;
  const [selected, setSelected] = useState<AnswerFixture | null>(null);
  const openEvidence = (_trigger: HTMLElement, answer: AnswerFixture) => setSelected(answer);
  const closeEvidence = () => setSelected(null);
  useEffect(() => {
    if (rawQuery === query) return;
    const next = new URLSearchParams(searchParams);
    if (query) next.set('answer_query', query);
    else next.delete('answer_query');
    void setSearchParams(next, { replace: true });
  }, [query, rawQuery, searchParams, setSearchParams]);
  const update = (key: string, value: string, fallback: string) => {
    const next = new URLSearchParams(searchParams);
    const safeValue =
      key === 'answer_query' && containsClientSecret(value) ? '' : value.slice(0, 80);
    if (safeValue === fallback || !safeValue) next.delete(key);
    else next.set(key, safeValue);
    void setSearchParams(next);
  };
  useEffect(() => {
    if (experience?.source !== 'live' || !experience.projectPubId) {
      setLiveAnswerState('fixture');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers) {
      setLiveAnswers(null);
      setLiveAnswerState('failed');
      return;
    }
    setLiveAnswerState('loading');
    void listAnalyticsAnswers(
      experience.projectPubId,
      {
        ...(model !== 'all' ? { model } : {}),
        ...(mode !== 'all' ? { mode } : {}),
        ...(region !== 'all' ? { region } : {}),
        limit: 100,
      },
      headers,
    ).then((result) => {
      if (result.kind !== 'ready') {
        setLiveAnswers(null);
        setLiveAnswerState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
        return;
      }
      setLiveAnswers(
        result.data.data.flatMap((answer) => {
          const id = safeOpaqueId(answer.pub_id, 'ans_');
          if (!id) return [];
          const safeText = (value: string | null | undefined, fallback: string, max = 4000) =>
            value && value.length <= max && !containsClientSecret(value) ? value : fallback;
          return [
            {
              id,
              question: safeText(
                answer.query_text ?? answer.query_pub_id,
                '未关联问题',
                500,
              ),
              model: safeText(answer.model, '未知模型', 120),
              mode: safeText(answer.mode, 'unknown', 80),
              region: safeText(answer.region, '未标注地域', 120),
              answer: safeText(answer.response_text, '内容因安全策略隐藏'),
              cited:
                answer.citation_count > 0 ? [`${answer.citation_count} 条规范化引用`] : [],
              mention: answer.mentioned ?? false,
              capturedAt: safeText(
                answer.capture_time.slice(0, 16).replace('T', ' '),
                '时间已隐藏',
                40,
              ),
            },
          ];
        }),
      );
      setLiveAnswerState('ready');
    });
  }, [experience, model, mode, region]);
  const sourceAnswers = experience?.source === 'live' ? (liveAnswers ?? []) : answerFixtures;
  const filtered = sourceAnswers.filter(
    (answer) =>
      (model === 'all' || answer.model.toLowerCase() === model) &&
      (mode === 'all' || answer.mode === mode) &&
      (region === 'all' || answer.region === region) &&
      (!query || answer.question.includes(query)),
  );
  const pageCount = Math.max(1, Math.ceil(filtered.length / 2));
  const page = Math.min(requestedPage, pageCount);
  useEffect(() => {
    if (page === requestedPage) return;
    const next = new URLSearchParams(searchParams);
    if (page === 1) next.delete('answer_page');
    else next.set('answer_page', String(page));
    void setSearchParams(next, { replace: true });
  }, [page, requestedPage, searchParams, setSearchParams]);
  const rows = filtered.slice((page - 1) * 2, page * 2);
  return (
    <>
      <FilterBar label="回答筛选" className="filter-wrap">
        <label>
          搜索问题
          <input
            aria-label="搜索问题"
            value={query}
            onChange={(event) => update('answer_query', event.target.value, '')}
          />
        </label>
        <label>
          模型
          <select
            aria-label="回答模型"
            value={model}
            onChange={(event) => {
              update('answer_model', event.target.value, 'all');
              update('answer_page', '1', '1');
            }}
          >
            <option value="all">全部模型</option>
            <option value="doubao">豆包</option>
            <option value="deepseek">DeepSeek</option>
            <option value="yuanbao">元宝</option>
          </select>
        </label>
        <label>
          模式
          <select
            aria-label="回答模式筛选"
            value={mode}
            onChange={(event) => update('answer_mode', event.target.value, 'all')}
          >
            <option value="all">全部模式</option>
            <option value="quick">快速</option>
            <option value="deep">深度思考</option>
          </select>
        </label>
        <label>
          地域
          <select
            aria-label="回答地域"
            value={region}
            onChange={(event) => update('answer_region', event.target.value, 'all')}
          >
            <option value="all">全部地域</option>
            <option>上海</option>
            <option>江苏</option>
            <option>北京</option>
            <option>广东</option>
          </select>
        </label>
        <span className="filter-summary">共 {filtered.length} 条回答 · 每页 2 条</span>
        <button
          className="button button-secondary"
          onClick={() =>
            downloadTextFile(
              'evidence-package-manifest.json',
              'application/json',
              JSON.stringify(
                {
                  version: '1.0',
                  answers: filtered.map(({ id, question, model: answerModel, capturedAt }) => ({
                    id,
                    question,
                    model: answerModel,
                    capturedAt,
                  })),
                },
                null,
                2,
              ),
            )
          }
        >
          生成证据包
        </button>
      </FilterBar>
      {liveAnswerState === 'loading' ? <StatePanel state="loading" /> : null}
      {liveAnswerState === 'failed' ? <StatePanel state="failed" /> : null}
      {liveAnswerState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
      <div className="answer-list">
        {liveAnswerState === 'loading' ||
        liveAnswerState === 'failed' ||
        liveAnswerState === 'forbidden' ? null : rows.length ? (
          rows.map((answer) => (
            <article className="answer-card" key={answer.id}>
              <div className="answer-meta">
                <Badge tone={answer.mention ? 'positive' : 'neutral'}>
                  {answer.mention ? '品牌已出现' : '未出现'}
                </Badge>
                <span>
                  {answer.model} · {answer.mode === 'deep' ? '深度思考' : '快速'} · {answer.region}
                </span>
                <time>{answer.capturedAt}</time>
              </div>
              <h2>{answer.question}</h2>
              <p>{answer.answer}</p>
              <div className="source-chips">
                {answer.cited.length ? (
                  answer.cited.map((source, index) => (
                    <button
                      key={source}
                      onClick={(event) => openEvidence(event.currentTarget, answer)}
                    >
                      [{index + 1}] {source}
                    </button>
                  ))
                ) : (
                  <Badge tone="warning">无引用来源</Badge>
                )}
              </div>
              <div className="answer-actions">
                <button
                  className="button button-secondary"
                  onClick={(event) => openEvidence(event.currentTarget, answer)}
                >
                  查看回答截图
                </button>
                <button
                  className="button button-secondary"
                  onClick={(event) => openEvidence(event.currentTarget, answer)}
                >
                  历史 diff
                </button>
                <button
                  className="button"
                  onClick={(event) => openEvidence(event.currentTarget, answer)}
                >
                  打开证据中心
                </button>
              </div>
            </article>
          ))
        ) : (
          <StatePanel state="empty" />
        )}
      </div>
      <Pagination
        label="回答分页"
        page={page}
        pageCount={pageCount}
        onPageChange={(nextPage) => update('answer_page', String(nextPage), '1')}
      />
      {selected ? (
        <Dialog
          title="证据与历史差异"
          eyebrow="Evidence viewer"
          closeLabel="关闭证据弹窗"
          onClose={closeEvidence}
        >
          <EvidenceViewer
            label={`${selected.model} 回答截图，锚点高亮品牌提及`}
            anchor={{
              assetId: 'evd_01K0…A17',
              textStart: 48,
              textEnd: 73,
              bbox: [312, 184, 220, 46],
            }}
            previousText="仅支持云端部署"
            currentText="支持私有化部署与审计"
          >
            <Badge tone="positive">SHA-256 已校验</Badge>
          </EvidenceViewer>
        </Dialog>
      ) : null}
    </>
  );
}

function ReportsWorkspace() {
  const experience = useOptionalExperienceContext();
  const [question, setQuestion] = useState('');
  const [questions, setQuestions] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [livePage, setLivePage] = useState<ReportPage | null>(null);
  const [liveState, setLiveState] = useState<
    'fixture' | 'loading' | 'ready' | 'failed' | 'forbidden'
  >(experience?.source === 'live' ? 'loading' : 'fixture');
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
    void listReports(headers).then((result) => {
      if (result.kind === 'ready') {
        setLivePage(result.data);
        setLiveState('ready');
      } else {
        setLivePage(null);
        setLiveState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
  }, [experience]);
  if (liveState === 'loading') return <StatePanel state="loading" />;
  if (liveState === 'failed') return <StatePanel state="failed" />;
  if (liveState === 'forbidden') return <StatePanel state="forbidden" />;
  if (liveState === 'ready' && livePage?.data.length === 0) return <StatePanel state="empty" />;
  const liveReport = liveState === 'ready' ? livePage?.data[0] : undefined;
  const safeTitle =
    liveReport && liveReport.title.length <= 240 && !containsClientSecret(liveReport.title)
      ? liveReport.title
      : '未命名报告';
  const safeState =
    liveReport && liveReport.state.length <= 80 && !containsClientSecret(liveReport.state)
      ? liveReport.state
      : 'unknown';
  const safeReportId =
    liveReport && liveReport.pub_id.length <= 120 && !containsClientSecret(liveReport.pub_id)
      ? liveReport.pub_id
      : '报告标识已隐藏';
  const questionContainsSecret = containsClientSecret(question);
  return (
    <>
      <section className="panel report-feature">
        <div>
          <span className="overline">Published report</span>
          <h2>{liveReport ? safeTitle : '2026 Q3 GEO 监测与优化建议'}</h2>
          <p>
            {liveReport
              ? `${safeReportId} · 更新于 ${liveReport.updated_at.slice(0, 16).replace('T', ' ')}`
              : '覆盖窗口 2026-07-01—2026-07-21 · 发布版本 v1.2 · 文件 hash 已记录'}
          </p>
          <div className="scope-row">
            <Badge tone={safeState === 'published' ? 'positive' : 'warning'}>
              {liveReport ? safeState : '已发布'}
            </Badge>
            {liveReport ? <Badge tone="positive">真实 reports API</Badge> : null}
            <Badge>PDF</Badge>
            <Badge>在线版</Badge>
          </div>
        </div>
        <div className="report-actions">
          <button className="button button-secondary" onClick={() => setPreviewOpen(true)}>
            在线预览
          </button>
          <button className="button button-secondary" disabled title="真实 PDF API 就绪后开放">
            PDF 正在生成
          </button>
          <button
            className="button"
            onClick={() =>
              downloadTextFile(
                'geo-report-data.csv',
                'text/csv;charset=utf-8',
                'metric,value,numerator,denominator\nmention_rate,0.684,26,38\ntop3_rate,0.737,28,38\n',
              )
            }
          >
            导出筛选数据
          </button>
        </div>
      </section>
      <div className="two-column">
        <section className="panel">
          <h2>向报告提问</h2>
          <p className="panel-subtitle">问题与报告版本绑定，回答不会静默改写已发布报告。</p>
          <label className="form-field" htmlFor="report-question">
            <span>问题</span>
            <textarea
              id="report-question"
              rows={4}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
            />
            {questionContainsSecret ? (
              <span className="field-error" role="alert">
                {noClientSecretMessage}
              </span>
            ) : null}
          </label>
          <div className="form-actions">
            <span>{questions.length} 个问题</span>
            <button
              className="button"
              disabled={question.trim().length < 6 || questionContainsSecret}
              onClick={() => {
                setQuestions((current) => [question.trim(), ...current]);
                setQuestion('');
              }}
            >
              提交问题
            </button>
          </div>
          {questions.map((item) => (
            <article className="question-thread" key={item}>
              <Badge tone="warning">等待报告团队</Badge>
              <p>{item}</p>
            </article>
          ))}
        </section>
        <section className="panel">
          <h2>客户确认</h2>
          <p className="panel-subtitle">确认仅表示已接收此版本，不代表认可所有建议。</p>
          {confirmed ? (
            <div className="confirmation" role="status">
              <Badge tone="positive">已确认接收 v1.2</Badge>
              <span>确认事件已写入审计</span>
            </div>
          ) : (
            <button className="button" onClick={() => setConfirmed(true)}>
              确认收到 v1.2
            </button>
          )}
          <h3>历史版本</h3>
          <ul className="version-list">
            <li>v1.2 · 当前发布</li>
            <li>v1.1 · 已撤回并保留审计</li>
            <li>v1.0 · 首次发布</li>
          </ul>
        </section>
      </div>
      {previewOpen ? (
        <Dialog
          title="2026 Q3 GEO 监测与优化建议"
          eyebrow="Published online report · v1.2"
          closeLabel="关闭在线报告预览"
          onClose={() => setPreviewOpen(false)}
        >
          <article className="report-preview-copy">
            <Badge tone="positive">发布 hash 已核验</Badge>
            <h3>执行摘要</h3>
            <p>品牌提及率 68.4%，有效回答 38 条；所有数字绑定冻结窗口与贡献证据。</p>
            <h3>优化建议</h3>
            <p>优先补齐可公开核验的部署边界说明，并在下一冻结窗口进行复测。</p>
          </article>
        </Dialog>
      ) : null}
    </>
  );
}

const memberSchema = z.object({
  name: z.string().trim().min(2, '请输入成员姓名').refine(noClientSecret, noClientSecretMessage),
  email: z.email('请输入有效邮箱').refine(noClientSecret, noClientSecretMessage),
  role: z.enum(['member', 'admin']),
});
type MemberValue = z.infer<typeof memberSchema>;
type CustomerMember = { name: string; email: string; role: '客户管理员' | '客户成员' };
function MembersWorkspace() {
  const [members, setMembers] = useState<CustomerMember[]>([
    { name: '林澄', email: 'l***@yunxiu.example', role: '客户管理员' },
  ]);
  const [selectedEmail, setSelectedEmail] = useState<string | null>(null);
  const [memberReceipt, setMemberReceipt] = useState('');
  const selectedMember = members.find((member) => member.email === selectedEmail) ?? null;
  const adminCount = members.filter((member) => member.role === '客户管理员').length;
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<MemberValue>({
    resolver: zodResolver(memberSchema),
    defaultValues: { name: '', email: '', role: 'member' },
  });
  const submit = handleSubmit((value) => {
    setMembers((current) => [
      ...current,
      {
        name: value.name,
        email: value.email.replace(/(^.).*(@.*$)/, '$1***$2'),
        role: value.role === 'admin' ? '客户管理员' : '客户成员',
      },
    ]);
    setMemberReceipt(`${value.name} 的邀请已发送，邮箱仅以掩码保存`);
    reset();
  });
  const changeSelectedRole = () => {
    if (!selectedMember) return;
    const nextRole = selectedMember.role === '客户管理员' ? '客户成员' : '客户管理员';
    if (selectedMember.role === '客户管理员' && adminCount === 1) return;
    setMembers((current) =>
      current.map((member) =>
        member.email === selectedMember.email ? { ...member, role: nextRole } : member,
      ),
    );
    setMemberReceipt(`${selectedMember.name} 已变更为${nextRole}，审计事件已记录`);
  };
  const removeSelected = () => {
    if (!selectedMember) return;
    if (selectedMember.role === '客户管理员' && adminCount === 1) return;
    setMembers((current) => current.filter((member) => member.email !== selectedMember.email));
    setMemberReceipt(`${selectedMember.name} 已移出项目，历史审计仍保留`);
    setSelectedEmail(null);
  };
  return (
    <div className="workspace-grid">
      <section className="panel">
        <h2>项目成员</h2>
        <p className="panel-subtitle">客户管理员可以管理本租户成员；邮箱在列表和审计中保持掩码。</p>
        <div className="member-list">
          {members.map((member) => (
            <article key={`${member.name}-${member.email}`}>
              <div className="avatar">{member.name.slice(0, 1)}</div>
              <div>
                <strong>{member.name}</strong>
                <span>{member.email}</span>
              </div>
              <Badge tone={member.role === '客户管理员' ? 'info' : 'neutral'}>{member.role}</Badge>
              <button
                className="button button-secondary"
                aria-label={`管理 ${member.name}`}
                onClick={() => setSelectedEmail(member.email)}
              >
                管理
              </button>
            </article>
          ))}
        </div>
      </section>
      <form className="panel form-panel" onSubmit={(event) => void submit(event)} noValidate>
        <h2>邀请成员</h2>
        <Field id="memberName" label="姓名" error={errors.name}>
          <input id="memberName" {...register('name')} />
        </Field>
        <Field id="memberEmail" label="工作邮箱" error={errors.email}>
          <input id="memberEmail" type="email" {...register('email')} />
        </Field>
        <Field id="memberRole" label="项目角色" error={errors.role}>
          <select id="memberRole" {...register('role')}>
            <option value="member">客户成员</option>
            <option value="admin">客户管理员</option>
          </select>
        </Field>
        <button className="button">发送邀请</button>
      </form>
      {memberReceipt ? <Toast>{memberReceipt}</Toast> : null}
      {selectedMember ? (
        <Dialog
          title={`管理 ${selectedMember.name}`}
          eyebrow="Project membership"
          closeLabel="关闭成员管理"
          onClose={() => setSelectedEmail(null)}
        >
          <dl className="definition-grid">
            <div>
              <dt>邮箱</dt>
              <dd>{selectedMember.email}</dd>
            </div>
            <div>
              <dt>当前角色</dt>
              <dd>{selectedMember.role}</dd>
            </div>
          </dl>
          {selectedMember.role === '客户管理员' && adminCount === 1 ? (
            <p className="security-note">必须至少保留一名客户管理员，当前成员不可降级或移除。</p>
          ) : null}
          <div className="button-row">
            <button
              className="button button-secondary"
              disabled={selectedMember.role === '客户管理员' && adminCount === 1}
              onClick={changeSelectedRole}
            >
              {selectedMember.role === '客户管理员' ? '改为客户成员' : '提升为客户管理员'}
            </button>
            <button
              className="button button-danger"
              disabled={selectedMember.role === '客户管理员' && adminCount === 1}
              onClick={removeSelected}
            >
              移出项目
            </button>
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}

function Placeholder({ active }: { active: string }) {
  const labels: Record<string, string> = {
    home: '项目总览',
    profile: '甲方资料',
    assets: '品牌、产品与竞品',
    questions: '问题、目标与配置申请',
    evidence: 'AI 回答与证据中心',
    reports: '报告、确认与导出',
  };
  return (
    <section className="panel">
      <h2>{labels[active] ?? '工作区'}</h2>
      <p className="panel-subtitle">
        此纵向页面已接入统一权限、状态和响应式外壳，详细交互正在持续补齐。
      </p>
      <div className="card-grid">
        <article className="action-card">
          <Badge tone="positive">已同步</Badge>
          <h3>项目数据</h3>
          <p>来自 OpenAPI contract fixture；真实接口可用后保持同一投影替换。</p>
        </article>
        <article className="action-card">
          <Badge tone="info">可追溯</Badge>
          <h3>证据与版本</h3>
          <p>所有结论保留冻结窗口、版本和贡献证据入口。</p>
        </article>
        <article className="action-card">
          <Badge tone="warning">待处理 2</Badge>
          <h3>客户待办</h3>
          <p>资料确认与报告问题将在这里完成闭环。</p>
        </article>
      </div>
    </section>
  );
}

export default function Shell() {
  const experience = useOptionalExperienceContext();
  return (
    <ProductShell
      product="Customer Web"
      title="客户工作台"
      description="从项目资料到监测、证据、报告与平台账号授权的安全协作入口。"
      nav={nav}
      probe={getHealth}
    >
      {(active) =>
        experience?.source === 'live' &&
        !['monitoring', 'accounts', 'questions', 'evidence', 'reports'].includes(active) ? (
          <StatePanel state="insufficient" />
        ) : active === 'home' ? (
          <HomeWorkspace />
        ) : active === 'monitoring' ? (
          <Monitoring />
        ) : active === 'accounts' ? (
          <Accounts />
        ) : active === 'profile' ? (
          <ProfileWorkspace />
        ) : active === 'assets' ? (
          <AssetsWorkspace />
        ) : active === 'questions' ? (
          <QuestionsWorkspace />
        ) : active === 'evidence' ? (
          <EvidenceWorkspace />
        ) : active === 'reports' ? (
          <ReportsWorkspace />
        ) : active === 'members' ? (
          <MembersWorkspace />
        ) : (
          <Placeholder active={active} />
        )
      }
    </ProductShell>
  );
}
