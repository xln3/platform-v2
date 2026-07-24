import {
  AccountSummary,
  Badge,
  MetricGrid,
  ProductShell,
  RevocationReceipt,
  StatePanel,
  projectSafeAccountSummary,
  useOptionalExperienceContext,
} from '@geo/design-system';
import { getHealth } from '@geo/api-client';

const accounts = [
  projectSafeAccountSummary({
    accountMask: '豆包 · 尾号 4821',
    platformLabel: '豆包',
    ownerLabel: '客户管理员 · 林澄',
    custodyMode: 'hybrid',
    admissionLevel: 'read_verified',
    scopes: ['read', 'query'],
    expiresLabel: '2026-08-31 18:00 CST',
    regionLabel: '中国大陆 · 华东',
    sessionHealth: 'healthy',
    lastVerifiedLabel: '2026-07-24 16:42 CST',
    interventionStatus: 'none',
  }),
  projectSafeAccountSummary({
    accountMask: '元宝 · 企业席位',
    platformLabel: '元宝',
    ownerLabel: '内容负责人 · 周岚',
    custodyMode: 'customer-device',
    admissionLevel: 'adapter_ready',
    scopes: ['read'],
    expiresLabel: '2026-07-31 18:00 CST',
    regionLabel: '中国大陆 · 华南',
    sessionHealth: 'challenge_required',
    lastVerifiedLabel: '尚未 live 验证',
    interventionStatus: 'waiting',
  }),
  projectSafeAccountSummary({
    accountMask: '发布账号 · 尾号 0917',
    platformLabel: '内容平台',
    ownerLabel: '品牌负责人 · 赵宁',
    custodyMode: 'server',
    admissionLevel: 'suspended',
    scopes: [],
    expiresLabel: '已撤销',
    regionLabel: '中国大陆',
    sessionHealth: 'revoked',
    lastVerifiedLabel: '2026-07-23 09:18 CST',
    interventionStatus: 'completed',
  }),
];

function Overview() {
  return (
    <>
      <MetricGrid
        metrics={[
          { label: '运行中', value: '18', detail: '3 个项目' },
          { label: '待人工', value: '2', detail: 'OTP 1 · 扫码 1' },
          { label: '健康会话', value: '24/27', detail: '不包含秘密字段' },
          { label: '延迟任务', value: '3', detail: 'P95 8m 42s' },
        ]}
      />
      <section className="panel">
        <h2>运行时间线</h2>
        <p className="panel-subtitle">
          execution 业务文件由 S01 拥有；此处仅聚合其安全状态投影，不复制账号状态或 API 调用。
        </p>
        <table className="data-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>事件</th>
              <th>对象</th>
              <th>结果</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>16:42</td>
              <td>身份探针</td>
              <td>豆包 · 尾号 4821</td>
              <td>
                <Badge tone="positive">通过</Badge>
              </td>
            </tr>
            <tr>
              <td>16:38</td>
              <td>租约暂停</td>
              <td>元宝 · 企业席位</td>
              <td>
                <Badge tone="warning">等待客户</Badge>
              </td>
            </tr>
            <tr>
              <td>16:20</td>
              <td>授权撤销</td>
              <td>发布账号 · 尾号 0917</td>
              <td>
                <Badge tone="danger">已隔离</Badge>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </>
  );
}

function Sessions() {
  return (
    <section className="panel">
      <h2>授权、租约与会话健康</h2>
      <p className="panel-subtitle">
        仅展示 S01 安全投影；不提供 Cookie、token、profile 或秘密下载入口。
      </p>
      <div className="account-list">
        {accounts.map((account) => (
          <AccountSummary key={account.accountMask} account={account} />
        ))}
      </div>
    </section>
  );
}

function Interventions() {
  return (
    <>
      <section className="panel">
        <h2>人工接管队列</h2>
        <p className="panel-subtitle">
          验证必须在客户的目标平台原生页面或受控终端完成；运营人员看不到也不能代填秘密。
        </p>
        <table className="data-table">
          <thead>
            <tr>
              <th>待办</th>
              <th>安全状态</th>
              <th>租约</th>
              <th>到期</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>元宝 · 企业席位</td>
              <td>
                <Badge tone="warning">等待客户扫码</Badge>
              </td>
              <td>已暂停</td>
              <td>08:34</td>
            </tr>
            <tr>
              <td>Kimi · 尾号 6630</td>
              <td>
                <Badge tone="warning">Push MFA</Badge>
              </td>
              <td>未签发</td>
              <td>04:12</td>
            </tr>
          </tbody>
        </table>
      </section>
      <StatePanel state="delayed" />
    </>
  );
}

function Events() {
  return (
    <>
      <section className="panel">
        <h2>账号生命周期事件</h2>
        <p className="panel-subtitle">
          事件只含不透明标识与安全摘要。无权角色获得一致的 forbidden 结果，无法推断账号是否存在。
        </p>
        <ol className="timeline">
          <li>
            <strong>授权即将到期</strong>
            <span>元宝 · 企业席位 · 7 天内 · 16:40</span>
          </li>
          <li>
            <strong>租约因人工验证暂停</strong>
            <span>opaque account · 16:38</span>
          </li>
          <li>
            <strong>账号进入隔离</strong>
            <span>发布账号 · 尾号 0917 · 16:20</span>
          </li>
          <li>
            <strong>撤销完成</strong>
            <span>活动会话已关闭 · 秘密副本已清除 · 16:20</span>
          </li>
        </ol>
      </section>
      <RevocationReceipt
        receipt={{
          receiptId: 'rvr_01K0OPS9Y',
          revokedAtLabel: '2026-07-24 16:20 CST',
          actorLabel: '客户管理员 · 已验证',
          leasesStopped: true,
          sessionsClosed: true,
          secretCopiesPurged: true,
        }}
      />
    </>
  );
}

export default function Shell() {
  const experience = useOptionalExperienceContext();
  return (
    <ProductShell
      product="Operations Web"
      title="运行总览"
      description="可靠采集、会话生命周期、人工接管与数据新鲜度。"
      probe={getHealth}
      nav={[
        { id: 'overview', label: '总览' },
        {
          id: 'execution',
          label: '执行任务',
          href: '/platform/operations/execution',
        },
        { id: 'sessions', label: '会话健康', badge: '3' },
        { id: 'interventions', label: '待人工', badge: '2' },
        { id: 'events', label: '事件审计' },
      ]}
    >
      {(active) =>
        experience?.source === 'live' ? (
          <StatePanel state="insufficient" />
        ) : active === 'sessions' ? (
          <Sessions />
        ) : active === 'interventions' ? (
          <Interventions />
        ) : active === 'events' ? (
          <Events />
        ) : (
          <Overview />
        )
      }
    </ProductShell>
  );
}
