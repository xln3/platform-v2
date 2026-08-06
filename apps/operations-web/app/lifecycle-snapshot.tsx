import {
  AccountSummary,
  Badge,
  containsClientSecret,
  InterventionStatus,
  MetricGrid,
  RevocationReceipt,
  StatePanel,
  projectSafeAccountSummary,
  type AccountSummaryProjection,
  type RevocationReceiptProjection,
} from '@geo/design-system';

type SafeTone = 'positive' | 'warning' | 'danger' | 'neutral';
type InterventionState = NonNullable<AccountSummaryProjection['interventionStatus']>;

export type OperationsLifecycleSnapshot = {
  metrics: {
    runningRuns: number;
    projectCount: number;
    pendingInterventions: number;
    healthySessions: number;
    totalSessions: number;
    delayedRuns: number;
    p95DelayLabel: string;
  };
  activity: {
    pubId: string;
    occurredAtLabel: string;
    eventLabel: string;
    objectLabel: string;
    resultLabel: string;
    tone: SafeTone;
  }[];
  accounts: AccountSummaryProjection[];
  interventions: {
    pubId: string;
    accountMask: string;
    challengeType: 'otp' | 'qr' | 'push' | 'passkey' | 'face' | 'graphical';
    state: InterventionState;
    leaseLabel: string;
    expiresLabel: string;
  }[];
  events: {
    pubId: string;
    eventLabel: string;
    detailLabel: string;
    occurredAtLabel: string;
  }[];
  revocationReceipt: RevocationReceiptProjection | null;
  projectionTruncated: boolean;
};

const challengeTypes = new Set(['otp', 'qr', 'push', 'passkey', 'face', 'graphical']);
const interventionStates = new Set([
  'none',
  'waiting',
  'paired',
  'refused',
  'timed_out',
  'failed',
  'completed',
]);
const tones = new Set(['positive', 'warning', 'danger', 'neutral']);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const safeText = (value: unknown, maximum = 120): string | null =>
  typeof value === 'string' &&
  value.trim().length > 0 &&
  value.length <= maximum &&
  !containsClientSecret(value)
    ? value.trim()
    : null;

const safeId = (value: unknown): string | null => {
  const projected = safeText(value);
  return projected && /^[a-z]{2,12}_[A-Za-z0-9_-]{1,108}$/.test(projected) ? projected : null;
};

const safeCount = (value: unknown): number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= 1_000_000
    ? value
    : 0;

const projectRecords = <T,>(
  value: unknown,
  projector: (source: Record<string, unknown>) => T | null,
  maximum: number,
): T[] =>
  Array.isArray(value)
    ? value.slice(0, maximum).flatMap((item) => {
        if (!isRecord(item)) return [];
        const projected = projector(item);
        return projected ? [projected] : [];
      })
    : [];

/**
 * S03's only Operations read boundary. S01 may supply its existing lifecycle state here, but this
 * projector never accepts raw credentials, profile metadata, free-form challenge payloads or
 * unknown fields.
 */
export function projectSafeOperationsLifecycleSnapshot(
  value: unknown,
): OperationsLifecycleSnapshot | null {
  if (!isRecord(value)) return null;
  const metrics = isRecord(value.metrics) ? value.metrics : {};
  const accounts = projectRecords(
    value.accounts,
    (account) => {
      if (
        !safeText(account.accountMask) ||
        !safeText(account.platformLabel) ||
        !safeText(account.ownerLabel)
      ) {
        return null;
      }
      return projectSafeAccountSummary(account);
    },
    100,
  );
  const activity = projectRecords(
    value.activity,
    (item) => {
      const pubId = safeId(item.pubId);
      const occurredAtLabel = safeText(item.occurredAtLabel);
      const eventLabel = safeText(item.eventLabel);
      const objectLabel = safeText(item.objectLabel);
      const resultLabel = safeText(item.resultLabel);
      const tone =
        typeof item.tone === 'string' && tones.has(item.tone) ? (item.tone as SafeTone) : 'neutral';
      return pubId && occurredAtLabel && eventLabel && objectLabel && resultLabel
        ? { pubId, occurredAtLabel, eventLabel, objectLabel, resultLabel, tone }
        : null;
    },
    50,
  );
  const interventions = projectRecords(
    value.interventions,
    (item) => {
      const pubId = safeId(item.pubId);
      const accountMask = safeText(item.accountMask);
      const leaseLabel = safeText(item.leaseLabel);
      const expiresLabel = safeText(item.expiresLabel);
      const challengeType =
        typeof item.challengeType === 'string' && challengeTypes.has(item.challengeType)
          ? (item.challengeType as OperationsLifecycleSnapshot['interventions'][number]['challengeType'])
          : null;
      const state =
        typeof item.state === 'string' && interventionStates.has(item.state)
          ? (item.state as InterventionState)
          : null;
      return pubId && accountMask && leaseLabel && expiresLabel && challengeType && state
        ? { pubId, accountMask, leaseLabel, expiresLabel, challengeType, state }
        : null;
    },
    100,
  );
  const events = projectRecords(
    value.events,
    (item) => {
      const pubId = safeId(item.pubId);
      const eventLabel = safeText(item.eventLabel);
      const detailLabel = safeText(item.detailLabel);
      const occurredAtLabel = safeText(item.occurredAtLabel);
      return pubId && eventLabel && detailLabel && occurredAtLabel
        ? { pubId, eventLabel, detailLabel, occurredAtLabel }
        : null;
    },
    100,
  );
  const receiptSource = isRecord(value.revocationReceipt) ? value.revocationReceipt : null;
  const receiptId = safeId(receiptSource?.receiptId);
  const revokedAtLabel = safeText(receiptSource?.revokedAtLabel);
  const actorLabel = safeText(receiptSource?.actorLabel);
  const revocationReceipt =
    receiptId && revokedAtLabel && actorLabel && receiptSource
      ? {
          receiptId,
          revokedAtLabel,
          actorLabel,
          leasesStopped: receiptSource.leasesStopped === true,
          sessionsClosed: receiptSource.sessionsClosed === true,
          secretCopiesPurged: receiptSource.secretCopiesPurged === true,
        }
      : null;

  return {
    metrics: {
      runningRuns: safeCount(metrics.runningRuns),
      projectCount: safeCount(metrics.projectCount),
      pendingInterventions: safeCount(metrics.pendingInterventions),
      healthySessions: safeCount(metrics.healthySessions),
      totalSessions: safeCount(metrics.totalSessions),
      delayedRuns: safeCount(metrics.delayedRuns),
      p95DelayLabel: safeText(metrics.p95DelayLabel) ?? '—',
    },
    activity,
    accounts,
    interventions,
    events,
    revocationReceipt,
    projectionTruncated: value.projectionTruncated === true,
  };
}

const challengeLabels: Record<
  OperationsLifecycleSnapshot['interventions'][number]['challengeType'],
  string
> = {
  otp: 'OTP',
  qr: '扫码',
  push: 'Push MFA',
  passkey: 'Passkey',
  face: '人脸/活体跳转',
  graphical: '图形 Challenge',
};

export function OperationsLifecycleWorkspace({
  section,
  snapshot,
  unavailableState = 'insufficient',
}: {
  section: 'overview' | 'sessions' | 'interventions' | 'events';
  snapshot: OperationsLifecycleSnapshot | null;
  unavailableState?: 'loading' | 'insufficient' | 'failed' | 'forbidden';
}) {
  if (!snapshot) return <StatePanel state={unavailableState} />;
  const content =
    section === 'sessions' ? (
      <Sessions snapshot={snapshot} />
    ) : section === 'interventions' ? (
      <Interventions snapshot={snapshot} />
    ) : section === 'events' ? (
      <Events snapshot={snapshot} />
    ) : (
      <Overview snapshot={snapshot} />
    );
  return (
    <>
      {snapshot.projectionTruncated ? <StatePanel state="delayed" /> : null}
      {content}
    </>
  );
}

function Overview({ snapshot }: { snapshot: OperationsLifecycleSnapshot }) {
  return (
    <>
      <MetricGrid
        metrics={[
          {
            label: '运行中',
            value: String(snapshot.metrics.runningRuns),
            detail: `${snapshot.metrics.projectCount} 个项目`,
          },
          {
            label: '待人工',
            value: String(snapshot.metrics.pendingInterventions),
            detail: '仅显示安全状态摘要',
          },
          {
            label: '健康会话',
            value: `${snapshot.metrics.healthySessions}/${snapshot.metrics.totalSessions}`,
            detail: '不包含秘密字段',
          },
          {
            label: '延迟任务',
            value: String(snapshot.metrics.delayedRuns),
            detail: `P95 ${snapshot.metrics.p95DelayLabel}`,
          },
        ]}
      />
      <section className="panel">
        <h2>运行时间线</h2>
        <p className="panel-subtitle">
          execution 业务文件由 S01 拥有；此处仅消费其单一安全只读快照，不复制账号状态或 API 调用。
        </p>
        {snapshot.activity.length === 0 ? (
          <StatePanel state="empty" />
        ) : (
          <div
            className="table-scroll"
            role="region"
            aria-label="可横向滚动的运行时间线"
            tabIndex={0}
          >
            <table className="data-table">
              <caption className="sr-only">运行时间线安全事件</caption>
              <thead>
                <tr>
                  <th scope="col">时间</th>
                  <th scope="col">事件</th>
                  <th scope="col">对象</th>
                  <th scope="col">结果</th>
                </tr>
              </thead>
              <tbody>
                {snapshot.activity.map((item) => (
                  <tr key={item.pubId}>
                    <td>{item.occurredAtLabel}</td>
                    <td>{item.eventLabel}</td>
                    <td>{item.objectLabel}</td>
                    <td>
                      <Badge tone={item.tone}>{item.resultLabel}</Badge>
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

function Sessions({ snapshot }: { snapshot: OperationsLifecycleSnapshot }) {
  return (
    <section className="panel">
      <h2>授权、租约与会话健康</h2>
      <p className="panel-subtitle">
        仅展示 S01 安全投影；不提供 Cookie、token、profile 或秘密下载入口。
      </p>
      {snapshot.accounts.length === 0 ? (
        <StatePanel state="empty" />
      ) : (
        <div className="account-list">
          {snapshot.accounts.map((account) => (
            <AccountSummary
              key={`${account.platformLabel}:${account.accountMask}`}
              account={account}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function Interventions({ snapshot }: { snapshot: OperationsLifecycleSnapshot }) {
  return (
    <>
      <section className="panel">
        <h2>人工接管队列</h2>
        <p className="panel-subtitle">
          验证必须在客户的目标平台原生页面或受控终端完成；运营人员看不到也不能代填秘密。
        </p>
        {snapshot.interventions.length === 0 ? (
          <StatePanel state="empty" />
        ) : (
          <div
            className="table-scroll"
            role="region"
            aria-label="可横向滚动的人工接管队列"
            tabIndex={0}
          >
            <table className="data-table">
              <caption className="sr-only">人工接管安全状态</caption>
              <thead>
                <tr>
                  <th scope="col">待办</th>
                  <th scope="col">挑战</th>
                  <th scope="col">安全状态</th>
                  <th scope="col">租约</th>
                  <th scope="col">到期</th>
                </tr>
              </thead>
              <tbody>
                {snapshot.interventions.map((item) => (
                  <tr key={item.pubId}>
                    <td>{item.accountMask}</td>
                    <td>{challengeLabels[item.challengeType]}</td>
                    <td>
                      <InterventionStatus value={item.state} />
                    </td>
                    <td>{item.leaseLabel}</td>
                    <td>{item.expiresLabel}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {snapshot.interventions.some((item) => item.state === 'waiting') ? (
        <StatePanel state="delayed" />
      ) : null}
    </>
  );
}

function Events({ snapshot }: { snapshot: OperationsLifecycleSnapshot }) {
  return (
    <>
      <section className="panel">
        <h2>账号生命周期事件</h2>
        <p className="panel-subtitle">
          事件只含不透明标识与安全摘要。无权角色获得一致的 forbidden 结果，无法推断账号是否存在。
        </p>
        {snapshot.events.length === 0 ? (
          <StatePanel state="empty" />
        ) : (
          <ol className="timeline">
            {snapshot.events.map((event) => (
              <li key={event.pubId}>
                <strong>{event.eventLabel}</strong>
                <span>
                  {event.detailLabel} · {event.occurredAtLabel}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>
      {snapshot.revocationReceipt ? (
        <RevocationReceipt receipt={snapshot.revocationReceipt} />
      ) : null}
    </>
  );
}

export const fixtureOperationsLifecycleSnapshot = projectSafeOperationsLifecycleSnapshot({
  metrics: {
    runningRuns: 18,
    projectCount: 3,
    pendingInterventions: 2,
    healthySessions: 24,
    totalSessions: 27,
    delayedRuns: 3,
    p95DelayLabel: '8m 42s',
  },
  activity: [
    {
      pubId: 'evt_fixture_health',
      occurredAtLabel: '16:42',
      eventLabel: '身份探针',
      objectLabel: '豆包 · 尾号 4821',
      resultLabel: '通过',
      tone: 'positive',
    },
    {
      pubId: 'evt_fixture_lease',
      occurredAtLabel: '16:38',
      eventLabel: '租约暂停',
      objectLabel: '元宝 · 企业席位',
      resultLabel: '等待客户',
      tone: 'warning',
    },
    {
      pubId: 'evt_fixture_revocation',
      occurredAtLabel: '16:20',
      eventLabel: '授权撤销',
      objectLabel: '发布账号 · 尾号 0917',
      resultLabel: '已隔离',
      tone: 'danger',
    },
  ],
  accounts: [
    {
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
    },
    {
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
    },
    {
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
    },
  ],
  interventions: [
    {
      pubId: 'int_fixture_qr',
      accountMask: '元宝 · 企业席位',
      challengeType: 'qr',
      state: 'waiting',
      leaseLabel: '已暂停',
      expiresLabel: '08:34',
    },
    {
      pubId: 'int_fixture_push',
      accountMask: 'Kimi · 尾号 6630',
      challengeType: 'push',
      state: 'waiting',
      leaseLabel: '未签发',
      expiresLabel: '04:12',
    },
  ],
  events: [
    {
      pubId: 'evt_fixture_expiry',
      eventLabel: '授权即将到期',
      detailLabel: '元宝 · 企业席位 · 7 天内',
      occurredAtLabel: '16:40',
    },
    {
      pubId: 'evt_fixture_challenge',
      eventLabel: '租约因人工验证暂停',
      detailLabel: 'opaque account',
      occurredAtLabel: '16:38',
    },
    {
      pubId: 'evt_fixture_quarantine',
      eventLabel: '账号进入隔离',
      detailLabel: '发布账号 · 尾号 0917',
      occurredAtLabel: '16:20',
    },
    {
      pubId: 'evt_fixture_revoked',
      eventLabel: '撤销完成',
      detailLabel: '活动会话已关闭 · 秘密副本已清除',
      occurredAtLabel: '16:20',
    },
  ],
  revocationReceipt: {
    receiptId: 'rvr_01K0OPS9Y',
    revokedAtLabel: '2026-07-24 16:20 CST',
    actorLabel: '客户管理员 · 已验证',
    leasesStopped: true,
    sessionsClosed: true,
    secretCopiesPurged: true,
  },
});
