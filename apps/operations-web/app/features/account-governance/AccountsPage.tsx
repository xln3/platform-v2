import { useCallback, useEffect, useRef, useState } from 'react';
import { Dialog } from '@geo/design-system';
import type { SessionContext } from '../execution/api';
import {
  accountPhoneLabel,
  accountGovApi,
  COLLECTION_PLATFORMS,
  PLATFORM_LABELS,
  type AccountQuotaObservation,
  type CollectionAccountEvent,
  type CollectionAccountRow,
  type CollectionPlatform,
  type CollectionRegionRow,
  type LinkTestResult,
  type PlatformAccountCell,
} from './api';
import {
  describeApiError,
  formatCountdown,
  LinkLight,
  RelativeTime,
  ToastStack,
  useNow,
  useVisiblePolling,
  type ToastMessage,
} from './shared';

const TOTAL_COLS = 3 + COLLECTION_PLATFORMS.length * 3 + 1;
const POLL_MS = 15_000;

type RegionChangeRequest = {
  row: CollectionAccountRow;
  platform: CollectionPlatform;
  cell: PlatformAccountCell;
  to: string | null;
};

type QuotaEditRequest = {
  row: CollectionAccountRow;
  platform: CollectionPlatform;
  cell: PlatformAccountCell;
};

export function AccountsPage({ session }: { session: SessionContext }) {
  const [accounts, setAccounts] = useState<CollectionAccountRow[]>([]);
  const [regions, setRegions] = useState<CollectionRegionRow[]>([]);
  const [quotaObservations, setQuotaObservations] = useState<AccountQuotaObservation[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [eventsOpenId, setEventsOpenId] = useState<string | null>(null);
  const [regionChange, setRegionChange] = useState<RegionChangeRequest | null>(null);
  const [quotaEdit, setQuotaEdit] = useState<QuotaEditRequest | null>(null);
  const [smsTestRow, setSmsTestRow] = useState<CollectionAccountRow | null>(null);
  const [addAccountOpen, setAddAccountOpen] = useState(false);
  const [addRegionOpen, setAddRegionOpen] = useState(false);
  const [syncingNumbers, setSyncingNumbers] = useState(false);
  const toastSeq = useRef(0);
  const now = useNow();

  const pushToast = useCallback((tone: ToastMessage['tone'], text: string) => {
    toastSeq.current += 1;
    const id = toastSeq.current;
    setToasts((current) => [...current, { id, tone, text }]);
    window.setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 8_000);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [accountRows, regionRows, quotaRows] = await Promise.all([
        accountGovApi.listAccounts(session),
        accountGovApi.listRegions(session),
        accountGovApi.listQuotaObservations(session),
      ]);
      setAccounts(accountRows);
      setRegions(regionRows);
      setQuotaObservations(quotaRows);
      setState('ready');
    } catch {
      setState('failed');
    }
  }, [session]);

  useEffect(() => {
    setState('loading');
    void refresh();
  }, [refresh]);
  useVisiblePolling(() => void refresh(), POLL_MS);

  // 浏览器管理页「绑定账号」跳转锚点：数据就绪后滚到目标行。
  useEffect(() => {
    if (state !== 'ready' || typeof window === 'undefined') return;
    const hash = window.location.hash;
    if (!hash) return;
    document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView({ block: 'center' });
  }, [state]);

  async function runPushTest(row: CollectionAccountRow) {
    try {
      const result = await accountGovApi.linkTest(session, row.phone_account_pub_id, 'push');
      if (result.ok) {
        pushToast('positive', `接管测试推送已发出（${accountPhoneLabel(row)}），请留意手机回执。`);
      } else {
        pushToast('negative', `接管测试未通过：${result.detail ?? '未知原因'}`);
      }
    } catch (cause) {
      pushToast('negative', describeApiError(cause));
    }
    void refresh();
  }

  async function syncOtpNumbers() {
    setSyncingNumbers(true);
    try {
      const result = await accountGovApi.syncOtpRegistry(session);
      pushToast(
        'positive',
        `号码刷新完成：新增 ${result.created}、更新 ${result.updated}、无变化 ${result.unchanged}`,
      );
      await refresh();
    } catch (cause) {
      pushToast('negative', describeApiError(cause));
    } finally {
      setSyncingNumbers(false);
    }
  }

  return (
    <main className="acct-gov-page">
      <header className="acct-gov-heading">
        <div>
          <h1>采集账号管理</h1>
          <p>行 = 手机号；五平台格分别维护地域绑定、采集额度与运行状态。每 15 秒自动刷新。</p>
        </div>
        <div className="acct-gov-actions">
          {session.role === 'operator' || session.role === 'admin' ? (
            <button onClick={() => void syncOtpNumbers()} disabled={syncingNumbers}>
              {syncingNumbers ? '刷新中…' : '刷新号码'}
            </button>
          ) : null}
          <button onClick={() => setAddAccountOpen(true)}>添加帐号</button>
          <button onClick={() => setAddRegionOpen(true)}>添加地域</button>
        </div>
      </header>
      <QuotaObservationPanel
        observations={quotaObservations}
        accounts={accounts}
        regions={regions}
        now={now}
      />
      {state === 'loading' ? (
        <p className="acct-gov-empty">正在加载账号列表…</p>
      ) : state === 'failed' ? (
        <p className="acct-gov-empty">
          账号列表加载失败。<button onClick={() => void refresh()}>重试</button>
        </p>
      ) : accounts.length === 0 ? (
        <p className="acct-gov-empty">尚无采集账号。点击右上「添加帐号」登记第一个手机号。</p>
      ) : (
        <div className="acct-gov-table-scroll">
          <table aria-label="采集账号列表" className="acct-gov-table">
            <thead>
              <tr>
                <th rowSpan={2}>手机号</th>
                <th rowSpan={2}>转码</th>
                <th rowSpan={2}>接管</th>
                {COLLECTION_PLATFORMS.map((platform) => (
                  <th key={platform} colSpan={3}>
                    {PLATFORM_LABELS[platform]}
                  </th>
                ))}
                <th rowSpan={2}>操作</th>
              </tr>
              <tr>
                {COLLECTION_PLATFORMS.map((platform) => (
                  <PlatformSubHead key={platform} />
                ))}
              </tr>
            </thead>
            <tbody>
              {accounts.map((row) => (
                <AccountRow
                  key={row.phone_account_pub_id}
                  row={row}
                  regions={regions}
                  now={now}
                  eventsOpen={eventsOpenId === row.phone_account_pub_id}
                  session={session}
                  onToggleEvents={() =>
                    setEventsOpenId((current) =>
                      current === row.phone_account_pub_id ? null : row.phone_account_pub_id,
                    )
                  }
                  onRegionChange={(platform, cell, to) =>
                    setRegionChange({ row, platform, cell, to })
                  }
                  onQuotaEdit={(platform, cell) => setQuotaEdit({ row, platform, cell })}
                  onSmsTest={() => setSmsTestRow(row)}
                  onPushTest={() => void runPushTest(row)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {regionChange ? (
        <RegionConfirmDialog
          request={regionChange}
          session={session}
          onClose={() => setRegionChange(null)}
          onDone={(text) => {
            setRegionChange(null);
            if (text) pushToast('positive', text);
            void refresh();
          }}
        />
      ) : null}
      {quotaEdit ? (
        <QuotaEditDialog
          key={quotaEdit.cell.platform_account_pub_id}
          request={quotaEdit}
          session={session}
          onClose={() => setQuotaEdit(null)}
          onDone={(text) => {
            setQuotaEdit(null);
            if (text) pushToast('positive', text);
            void refresh();
          }}
        />
      ) : null}
      {smsTestRow ? (
        <SmsTestDialog
          key={smsTestRow.phone_account_pub_id}
          row={smsTestRow}
          session={session}
          onClose={() => {
            setSmsTestRow(null);
            void refresh();
          }}
        />
      ) : null}
      {addAccountOpen ? (
        <AddAccountDialog
          session={session}
          onClose={() => setAddAccountOpen(false)}
          onDone={(text) => {
            setAddAccountOpen(false);
            if (text) pushToast('positive', text);
            void refresh();
          }}
        />
      ) : null}
      {addRegionOpen ? (
        <AddRegionDialog
          session={session}
          onClose={() => setAddRegionOpen(false)}
          onDone={(text) => {
            setAddRegionOpen(false);
            if (text) pushToast('positive', text);
            void refresh();
          }}
        />
      ) : null}
      <ToastStack toasts={toasts} />
    </main>
  );
}

const QUOTA_MODE_LABELS: Record<AccountQuotaObservation['mode'], string> = {
  normal: '快速模式',
  deep_think: '专家模式',
  unknown: '模式未知',
};

const QUOTA_TIER_LABELS: Record<AccountQuotaObservation['account_tier'], string> = {
  free: '免费版',
  subscriber: '专业版',
  unknown: '档位未知',
};

const QUOTA_SOURCE_LABELS: Record<AccountQuotaObservation['source'], string> = {
  platform: '平台响应',
  platform_and_logs: '平台响应 + 采集日志',
  manual: '人工记录',
  unknown: '来源未知',
};

function QuotaObservationPanel({
  observations,
  accounts,
  regions,
  now,
}: {
  observations: AccountQuotaObservation[];
  accounts: CollectionAccountRow[];
  regions: CollectionRegionRow[];
  now: number;
}) {
  if (observations.length === 0) return null;
  const regionNames = new Map(regions.map((region) => [region.region_gb, region.name]));
  const phoneLabels = new Map(
    accounts.map((account) => [account.phone_account_pub_id, accountPhoneLabel(account)]),
  );
  return (
    <section className="acct-gov-quota-overview" aria-labelledby="account-quota-heading">
      <div className="acct-gov-section-heading">
        <div>
          <h2 id="account-quota-heading">平台账号额度（按手机号）</h2>
          <p>额度按“手机号 × 平台 × 模式”归集；地域只表示最近一次观测出口。</p>
        </div>
      </div>
      <div className="acct-gov-quota-cards">
        {observations.map((observation) => {
          const observedRegionName = observation.observed_region_gb
            ? (regionNames.get(observation.observed_region_gb) ?? observation.observed_region_gb)
            : '地域未登记';
          const roundedDaily =
            observation.daily_equivalent === null
              ? null
              : Math.max(0, Math.round(observation.daily_equivalent));
          const windowLabel =
            observation.window_days === null
              ? '周期未知'
              : `${observation.window_type === 'rolling' ? '滚动' : '自然'} ${observation.window_days} 天`;
          const resetCountdown = formatCountdown(observation.reset_at, now);
          return (
            <article
              className="acct-gov-quota-card"
              key={`${observation.phone_account_pub_id}-${observation.platform}-${observation.mode}`}
            >
              <header>
                <div>
                  <strong>
                    <a href={`#acct-${observation.phone_account_pub_id}`}>
                      {phoneLabels.get(observation.phone_account_pub_id) ??
                        observation.phone_masked}
                    </a>{' '}
                    · {PLATFORM_LABELS[observation.platform]}
                  </strong>
                  <small>
                    最近观测：{observedRegionName} · {observation.observed_browser_instance_key}
                  </small>
                </div>
                <span
                  className={`acct-gov-badge ${
                    observation.quota_state === 'exhausted'
                      ? 'quota'
                      : observation.quota_state === 'available'
                        ? 'idle'
                        : 'neutral'
                  }`}
                >
                  {observation.quota_state === 'exhausted'
                    ? '额度已用尽'
                    : observation.quota_state === 'available'
                      ? '额度可用'
                      : '额度未知'}
                </span>
              </header>
              <dl>
                <div>
                  <dt>档位 / 模式</dt>
                  <dd>
                    {QUOTA_TIER_LABELS[observation.account_tier]} ·{' '}
                    {QUOTA_MODE_LABELS[observation.mode]}
                  </dd>
                </div>
                <div>
                  <dt>额度周期</dt>
                  <dd>{windowLabel}</dd>
                </div>
                <div>
                  <dt>日志折算</dt>
                  <dd>
                    {roundedDaily === null ? (
                      '平台未公开固定条数'
                    ) : (
                      <>
                        <strong>约 {roundedDaily} 条/天</strong>
                        {observation.observed_window_count !== null &&
                        observation.window_days !== null ? (
                          <small>
                            已确认 {observation.observed_window_count} 条 ÷{' '}
                            {observation.window_days} 天 = {observation.daily_equivalent}；
                            {observation.count_kind === 'lower_bound' ? '下限估算' : '估算值'}
                          </small>
                        ) : null}
                      </>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>预计恢复</dt>
                  <dd>
                    {observation.reset_at ? (
                      <>
                        {new Date(observation.reset_at).toLocaleString('zh-CN', { hour12: false })}
                        {resetCountdown ? <small>{resetCountdown}</small> : null}
                      </>
                    ) : (
                      '未返回'
                    )}
                  </dd>
                </div>
              </dl>
              <footer>
                来源：{QUOTA_SOURCE_LABELS[observation.source]} · 观测于{' '}
                <RelativeTime iso={observation.observed_at} now={now} />
              </footer>
            </article>
          );
        })}
      </div>
      <p className="acct-gov-note">
        “约 N
        条/天”由该手机号滚动窗口内已确认发送数折算；复杂任务可能加权。同一手机号切换地域不会新增额度；豆包快速模式不占用这里展示的专家模式额度。
      </p>
    </section>
  );
}

function PlatformSubHead() {
  return (
    <>
      <th>地域</th>
      <th>额度</th>
      <th>状态</th>
    </>
  );
}

function AccountRow({
  row,
  regions,
  now,
  eventsOpen,
  session,
  onToggleEvents,
  onRegionChange,
  onQuotaEdit,
  onSmsTest,
  onPushTest,
}: {
  row: CollectionAccountRow;
  regions: CollectionRegionRow[];
  now: number;
  eventsOpen: boolean;
  session: SessionContext;
  onToggleEvents: () => void;
  onRegionChange: (
    platform: CollectionPlatform,
    cell: PlatformAccountCell,
    to: string | null,
  ) => void;
  onQuotaEdit: (platform: CollectionPlatform, cell: PlatformAccountCell) => void;
  onSmsTest: () => void;
  onPushTest: () => void;
}) {
  return (
    <>
      <tr id={`acct-${row.phone_account_pub_id}`}>
        <td data-label="手机号">
          <span title={row.owner_note ?? undefined}>{accountPhoneLabel(row)}</span>
          {row.state !== 'active' ? (
            <span className="acct-gov-badge neutral">{row.state}</span>
          ) : null}
        </td>
        <td data-label="转码">
          <div className="acct-gov-link">
            <LinkLight state={row.sms_link_state} label="转码链路" />
            <RelativeTime iso={row.last_sms_at} now={now} />
            <button aria-label="测试转码链路" onClick={onSmsTest}>
              测试
            </button>
          </div>
        </td>
        <td data-label="接管">
          <div className="acct-gov-link">
            <LinkLight state={row.push_link_state} label="接管通道" />
            <RelativeTime iso={row.last_push_test_at} now={now} />
            <button aria-label="测试接管通道" onClick={onPushTest}>
              测试
            </button>
          </div>
        </td>
        {COLLECTION_PLATFORMS.map((platform) => {
          const cell = row.platforms[platform] ?? null;
          if (!cell) {
            return (
              <td key={platform} colSpan={3} className="acct-gov-null">
                —
              </td>
            );
          }
          return (
            <PlatformCell
              key={platform}
              platform={platform}
              cell={cell}
              regions={regions}
              now={now}
              onRegionChange={onRegionChange}
              onQuotaEdit={onQuotaEdit}
            />
          );
        })}
        <td data-label="操作">
          <button aria-expanded={eventsOpen} onClick={onToggleEvents}>
            {eventsOpen ? '收起' : '事件'}
          </button>
        </td>
      </tr>
      {eventsOpen ? (
        <tr className="acct-gov-events-row">
          <td colSpan={TOTAL_COLS}>
            <AccountEventsPanel session={session} phoneAccountPubId={row.phone_account_pub_id} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function PlatformCell({
  platform,
  cell,
  regions,
  now,
  onRegionChange,
  onQuotaEdit,
}: {
  platform: CollectionPlatform;
  cell: PlatformAccountCell;
  regions: CollectionRegionRow[];
  now: number;
  onRegionChange: (
    platform: CollectionPlatform,
    cell: PlatformAccountCell,
    to: string | null,
  ) => void;
  onQuotaEdit: (platform: CollectionPlatform, cell: PlatformAccountCell) => void;
}) {
  const okRegions = regions.filter((region) => region.state === 'ok');
  const currentMissing =
    cell.region_gb !== null && !okRegions.some((region) => region.region_gb === cell.region_gb);
  const quotaTitle = `今日 ${cell.used_today}/${cell.quota_day ?? '不限'} · 本周 ${cell.used_week}/${cell.quota_week ?? '不限'} · 今年 ${cell.used_year}/${cell.quota_year ?? '不限'}`;
  const resumeCountdown =
    cell.runtime_state === 'quota_exhausted' ? formatCountdown(cell.quota_resume_at, now) : null;
  return (
    <>
      <td data-label={`${PLATFORM_LABELS[platform]}地域`}>
        <select
          aria-label={`${PLATFORM_LABELS[platform]}地域`}
          value={cell.region_gb ?? ''}
          onChange={(event) =>
            onRegionChange(platform, cell, event.target.value === '' ? null : event.target.value)
          }
        >
          <option value="">未分配</option>
          {okRegions.map((region) => (
            <option key={region.region_pub_id} value={region.region_gb}>
              {region.region_gb} {region.name}
            </option>
          ))}
          {currentMissing && cell.region_gb !== null ? (
            <option value={cell.region_gb}>{cell.region_gb}（当前，不可用）</option>
          ) : null}
        </select>
      </td>
      <td data-label={`${PLATFORM_LABELS[platform]}额度`}>
        <button
          className="acct-gov-quota"
          title={`${quotaTitle}（点击编辑）`}
          onClick={() => onQuotaEdit(platform, cell)}
        >
          {cell.used_today}/{cell.quota_day ?? '不限'}
        </button>
        {resumeCountdown ? <small className="acct-gov-countdown">{resumeCountdown}</small> : null}
      </td>
      <td data-label={`${PLATFORM_LABELS[platform]}状态`}>
        <RuntimeStateBadge cell={cell} now={now} />
      </td>
    </>
  );
}

export function RuntimeStateBadge({ cell, now }: { cell: PlatformAccountCell; now: number }) {
  switch (cell.runtime_state) {
    case 'idle':
      return <span className="acct-gov-badge idle">空闲</span>;
    case 'running':
      return (
        <span className="acct-gov-badge running">
          运行中
          {cell.current_run_pub_id ? (
            <a href="/platform/operations/execution" title="前往「执行与账号」查看该 run">
              {cell.current_run_pub_id}
            </a>
          ) : null}
        </span>
      );
    case 'muted': {
      const countdown = formatCountdown(cell.muted_until, now);
      return (
        <span className="acct-gov-badge muted" title={cell.state_reason ?? undefined}>
          禁{countdown ? <small>{countdown}</small> : null}
        </span>
      );
    }
    case 'quota_exhausted': {
      const countdown = formatCountdown(cell.quota_resume_at, now);
      return (
        <span className="acct-gov-badge quota">
          额度尽{countdown ? <small>{countdown}</small> : null}
        </span>
      );
    }
    case 'captcha':
      return <span className="acct-gov-badge captcha">验证码中</span>;
    case 'error':
      return (
        <span className="acct-gov-badge error" title={cell.state_reason ?? undefined}>
          异常
        </span>
      );
    default:
      return <span className="acct-gov-badge neutral">{cell.runtime_state}</span>;
  }
}

function RegionConfirmDialog({
  request,
  session,
  onClose,
  onDone,
}: {
  request: RegionChangeRequest;
  session: SessionContext;
  onClose: () => void;
  onDone: (message?: string) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fromLabel = request.cell.region_gb ?? '未分配';
  const toLabel = request.to ?? '未分配';

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await accountGovApi.patchPlatformAccount(session, request.cell.platform_account_pub_id, {
        region_gb: request.to,
        confirm: true,
      });
      onDone(`地域已变更：${fromLabel} → ${toLabel}`);
    } catch (cause) {
      setError(describeApiError(cause));
      setBusy(false);
    }
  }

  return (
    <Dialog title="确认地域变更" closeLabel="关闭" onClose={onClose}>
      <div className="acct-gov-dialog-body">
        <p>
          该手机号在该平台的地域绑定变更：
          <strong>
            {accountPhoneLabel(request.row)} · {PLATFORM_LABELS[request.platform]}
          </strong>
        </p>
        <p>
          旧地域：<strong>{fromLabel}</strong> → 新地域：<strong>{toLabel}</strong>
        </p>
        <p className="acct-gov-note">
          该平台会话将长期固定使用新地域出口；若与所绑浏览器实例的出口地域不一致，提交会被拒绝（region_ip_mismatch）。
        </p>
        {error ? (
          <p className="acct-gov-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="acct-gov-actions">
          <button onClick={onClose} disabled={busy}>
            取消
          </button>
          <button className="primary" onClick={() => void confirm()} disabled={busy}>
            {busy ? '提交中…' : '确认变更'}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

function QuotaEditDialog({
  request,
  session,
  onClose,
  onDone,
}: {
  request: QuotaEditRequest;
  session: SessionContext;
  onClose: () => void;
  onDone: (message?: string) => void;
}) {
  const [day, setDay] = useState(request.cell.quota_day?.toString() ?? '');
  const [week, setWeek] = useState(request.cell.quota_week?.toString() ?? '');
  const [year, setYear] = useState(request.cell.quota_year?.toString() ?? '');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function parseQuota(value: string): number | null {
    const trimmed = value.trim();
    if (trimmed === '') return null;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await accountGovApi.patchPlatformAccount(session, request.cell.platform_account_pub_id, {
        quota_day: parseQuota(day),
        quota_week: parseQuota(week),
        quota_year: parseQuota(year),
      });
      onDone('额度预算已更新');
    } catch (cause) {
      setError(describeApiError(cause));
      setBusy(false);
    }
  }

  return (
    <Dialog title="编辑额度预算" closeLabel="关闭" onClose={onClose}>
      <div className="acct-gov-dialog-body">
        <p>
          {accountPhoneLabel(request.row)} · {PLATFORM_LABELS[request.platform]}
          （今日已用 {request.cell.used_today} / 本周 {request.cell.used_week} / 今年{' '}
          {request.cell.used_year}）
        </p>
        <div className="acct-gov-quota-grid">
          <label>
            日额度
            <input
              type="number"
              min={0}
              value={day}
              placeholder="不限"
              onChange={(event) => setDay(event.target.value)}
            />
          </label>
          <label>
            周额度
            <input
              type="number"
              min={0}
              value={week}
              placeholder="不限"
              onChange={(event) => setWeek(event.target.value)}
            />
          </label>
          <label>
            年额度
            <input
              type="number"
              min={0}
              value={year}
              placeholder="不限"
              onChange={(event) => setYear(event.target.value)}
            />
          </label>
        </div>
        <p className="acct-gov-note">留空 = 不限。额度用尽后系统停发并显示重置倒计时。</p>
        {error ? (
          <p className="acct-gov-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="acct-gov-actions">
          <button onClick={onClose} disabled={busy}>
            取消
          </button>
          <button className="primary" onClick={() => void save()} disabled={busy}>
            {busy ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

function SmsTestDialog({
  row,
  session,
  onClose,
}: {
  row: CollectionAccountRow;
  session: SessionContext;
  onClose: () => void;
}) {
  const [result, setResult] = useState<LinkTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const now = useNow(1_000);

  useEffect(() => {
    let cancelled = false;
    accountGovApi
      .linkTest(session, row.phone_account_pub_id, 'sms')
      .then((payload) => {
        if (cancelled) return;
        setResult(payload);
        setStartedAt(Date.now());
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(describeApiError(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [session, row.phone_account_pub_id]);

  const waitSeconds = result?.wait_window_s ?? null;
  const remaining =
    waitSeconds !== null && startedAt !== null
      ? Math.max(0, waitSeconds - Math.floor((now - startedAt) / 1_000))
      : null;

  return (
    <Dialog title="转码链路测试" closeLabel="关闭" onClose={onClose}>
      <div className="acct-gov-dialog-body">
        <p>
          手机号：<strong>{accountPhoneLabel(row)}</strong>（smsforwarder 自动转发验证码链路）
        </p>
        {error ? (
          <p className="acct-gov-error" role="alert">
            {error}
          </p>
        ) : !result ? (
          <p>正在发起测试…</p>
        ) : (
          <>
            {result.guidance ? <p className="acct-gov-note">{result.guidance}</p> : null}
            {remaining !== null ? (
              <p aria-live="polite">
                {remaining > 0
                  ? `等待回执：剩余 ${remaining} 秒`
                  : '等待窗口已结束，可关闭后重试。'}
              </p>
            ) : null}
            {result.detail ? <p className="acct-gov-note">{result.detail}</p> : null}
          </>
        )}
        <div className="acct-gov-actions">
          <button className="primary" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </Dialog>
  );
}

function AccountEventsPanel({
  session,
  phoneAccountPubId,
}: {
  session: SessionContext;
  phoneAccountPubId: string;
}) {
  const [events, setEvents] = useState<CollectionAccountEvent[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');

  useEffect(() => {
    let cancelled = false;
    accountGovApi
      .listAccountEvents(session, phoneAccountPubId)
      .then((rows) => {
        if (cancelled) return;
        setEvents(rows);
        setState('ready');
      })
      .catch(() => {
        if (cancelled) return;
        setState('failed');
      });
    return () => {
      cancelled = true;
    };
  }, [session, phoneAccountPubId]);

  if (state === 'loading') return <p className="acct-gov-empty">正在加载事件…</p>;
  if (state === 'failed') return <p className="acct-gov-empty">事件加载失败。</p>;
  if (events.length === 0) return <p className="acct-gov-empty">暂无事件。</p>;
  return (
    <ol className="acct-gov-events" aria-label="账号事件时间线">
      {events.map((event) => (
        <li key={event.event_pub_id}>
          <time dateTime={event.created_at}>
            {new Date(event.created_at).toLocaleString('zh-CN', { hour12: false })}
          </time>
          <strong>{event.event_type}</strong>
          <span>{event.actor}</span>
          {event.old_value !== null || event.new_value !== null ? (
            <span>
              {event.old_value ?? '—'} → {event.new_value ?? '—'}
            </span>
          ) : null}
          {event.evidence !== null ? (
            <small>
              {typeof event.evidence === 'string' ? event.evidence : JSON.stringify(event.evidence)}
            </small>
          ) : null}
          {event.run_pub_id ? <small>{event.run_pub_id}</small> : null}
        </li>
      ))}
    </ol>
  );
}

function AddAccountDialog({
  session,
  onClose,
  onDone,
}: {
  session: SessionContext;
  onClose: () => void;
  onDone: (message?: string) => void;
}) {
  const [phone, setPhone] = useState('');
  const [ownerNote, setOwnerNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!phone.trim()) {
      setError('请填写手机号');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await accountGovApi.createAccount(session, {
        phone: phone.trim(),
        ...(ownerNote.trim() ? { owner_note: ownerNote.trim() } : {}),
      });
      onDone(`账号 ${phone.trim()} 已登记`);
    } catch (cause) {
      setError(describeApiError(cause));
      setBusy(false);
    }
  }

  return (
    <Dialog title="添加帐号" closeLabel="关闭" onClose={onClose}>
      <div className="acct-gov-dialog-body">
        <label className="acct-gov-field">
          <span>手机号</span>
          <input
            value={phone}
            placeholder="11 位手机号"
            onChange={(event) => setPhone(event.target.value)}
          />
        </label>
        <label className="acct-gov-field">
          <span>号主备注（可选）</span>
          <input
            value={ownerNote}
            placeholder="号主 / 众包来源备注"
            onChange={(event) => setOwnerNote(event.target.value)}
          />
        </label>
        <p className="acct-gov-note">
          登记后请配置 smsforwarder 转发与方糖接管通道，再逐平台登记账号。
        </p>
        {error ? (
          <p className="acct-gov-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="acct-gov-actions">
          <button onClick={onClose} disabled={busy}>
            取消
          </button>
          <button className="primary" onClick={() => void submit()} disabled={busy}>
            {busy ? '提交中…' : '提交'}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

function AddRegionDialog({
  session,
  onClose,
  onDone,
}: {
  session: SessionContext;
  onClose: () => void;
  onDone: (message?: string) => void;
}) {
  const [regionGb, setRegionGb] = useState('');
  const [name, setName] = useState('');
  const [proxyEnvKey, setProxyEnvKey] = useState('');
  const [relayUnit, setRelayUnit] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!/^\d{6}$/.test(regionGb.trim())) {
      setError('region_gb 须为 6 位数字（GB/T 2260 行政区划代码）');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await accountGovApi.createRegion(session, {
        region_gb: regionGb.trim(),
        ...(name.trim() ? { name: name.trim() } : {}),
        ...(proxyEnvKey.trim() ? { proxy_env_key: proxyEnvKey.trim() } : {}),
        ...(relayUnit.trim() ? { relay_unit: relayUnit.trim() } : {}),
      });
      onDone(`地域 ${regionGb.trim()} 已登记，待运维配置 relay 后生效`);
    } catch (cause) {
      setError(describeApiError(cause));
      setBusy(false);
    }
  }

  return (
    <Dialog title="添加地域" closeLabel="关闭" onClose={onClose}>
      <div className="acct-gov-dialog-body">
        <p className="acct-gov-note">
          新地域 = 悟空代理新购。提交后需运维配置 relay（proxy-relay@&lt;region&gt; 单元与代理凭证
          env），巡检通过后才会出现在地域下拉框。
        </p>
        <label className="acct-gov-field">
          <span>地域代码（region_gb，6 位数字）</span>
          <input
            value={regionGb}
            placeholder="如 120000"
            onChange={(event) => setRegionGb(event.target.value)}
          />
        </label>
        <label className="acct-gov-field">
          <span>名称（可选）</span>
          <input
            value={name}
            placeholder="如 天津"
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="acct-gov-field">
          <span>代理凭证 env 键名（可选）</span>
          <input
            value={proxyEnvKey}
            placeholder="如 GEO_PROXY_TJ_URL（不存明文凭证）"
            onChange={(event) => setProxyEnvKey(event.target.value)}
          />
        </label>
        <label className="acct-gov-field">
          <span>relay 单元名（可选）</span>
          <input
            value={relayUnit}
            placeholder="如 proxy-relay@tj.service"
            onChange={(event) => setRelayUnit(event.target.value)}
          />
        </label>
        {error ? (
          <p className="acct-gov-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="acct-gov-actions">
          <button onClick={onClose} disabled={busy}>
            取消
          </button>
          <button className="primary" onClick={() => void submit()} disabled={busy}>
            {busy ? '提交中…' : '提交'}
          </button>
        </div>
      </div>
    </Dialog>
  );
}
