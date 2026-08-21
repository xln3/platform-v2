import { useCallback, useEffect, useRef, useState } from 'react';
import type { SessionContext } from '../execution/api';
import {
  accountPhoneLabel,
  accountGovApi,
  COLLECTION_PLATFORMS,
  PLATFORM_LABELS,
  type BrowserSyncResult,
  type CollectionAccountRow,
  type CollectionBrowserRow,
  type CollectionRegionRow,
} from './api';
import {
  describeApiError,
  formatBytes,
  formatUptime,
  GB,
  ToastStack,
  useNow,
  useVisiblePolling,
  type ToastMessage,
} from './shared';

const POLL_MS = 30_000;
const UPTIME_WARN_S = 3 * 86_400;
const RSS_WARN_BYTES = 1.5 * GB;
const RSS_BAD_BYTES = 2 * GB;

export function BrowsersPage({ session }: { session: SessionContext }) {
  const [browsers, setBrowsers] = useState<CollectionBrowserRow[]>([]);
  const [accounts, setAccounts] = useState<CollectionAccountRow[]>([]);
  const [regions, setRegions] = useState<CollectionRegionRow[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [syncResult, setSyncResult] = useState<BrowserSyncResult | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
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
      const [browserRows, accountRows, regionRows] = await Promise.all([
        accountGovApi.listBrowsers(session),
        accountGovApi.listAccounts(session),
        accountGovApi.listRegions(session),
      ]);
      setBrowsers(browserRows);
      setAccounts(accountRows);
      setRegions(regionRows);
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

  const phoneByPubId = new Map(accounts.map((row) => [row.phone_account_pub_id, row]));
  const regionNameByGb = new Map(regions.map((row) => [row.region_gb, row.name]));

  async function restart(row: CollectionBrowserRow) {
    setBusyKey(row.instance_key);
    try {
      const result = await accountGovApi.restartBrowser(session, row.instance_key);
      const detail =
        result.detail === 'manual_restart_window_required'
          ? '已登记，需运维窗口执行'
          : result.detail;
      pushToast(result.ok ? 'warning' : 'negative', `重启 ${row.instance_key}：${detail}`);
    } catch (cause) {
      pushToast('negative', describeApiError(cause));
    }
    setBusyKey(null);
    void refresh();
  }

  async function releaseLock(row: CollectionBrowserRow) {
    setBusyKey(row.instance_key);
    try {
      const result = await accountGovApi.releaseBrowserLock(session, row.instance_key);
      pushToast(
        result.released ? 'positive' : 'warning',
        `释放锁 ${row.instance_key}：${result.detail}`,
      );
    } catch (cause) {
      pushToast('negative', describeApiError(cause));
    }
    setBusyKey(null);
    void refresh();
  }

  async function syncInstances() {
    setSyncing(true);
    setSyncResult(null);
    try {
      const result = await accountGovApi.syncBrowsers(session);
      setSyncResult(result);
      const errorCount = Array.isArray(result.errors) ? result.errors.length : result.errors;
      pushToast(
        errorCount > 0 ? 'warning' : 'positive',
        `同步完成：新增 ${result.created}、更新 ${result.updated}、错误 ${errorCount}`,
      );
    } catch (cause) {
      pushToast('negative', describeApiError(cause));
    }
    setSyncing(false);
    void refresh();
  }

  return (
    <main className="acct-gov-page">
      <header className="acct-gov-heading">
        <div>
          <h1>采集浏览器管理</h1>
          <p>行 = 常驻浏览器实例（平台 × 地域粒度）。实况每 30 秒自动刷新。</p>
        </div>
        <div className="acct-gov-actions">
          <button onClick={() => void syncInstances()} disabled={syncing}>
            {syncing ? '同步中…' : '同步实例清单'}
          </button>
        </div>
      </header>
      {syncResult && Array.isArray(syncResult.errors) && syncResult.errors.length > 0 ? (
        <p className="acct-gov-error" role="alert">
          同步错误：{syncResult.errors.join('；')}
        </p>
      ) : null}
      {state === 'loading' ? (
        <p className="acct-gov-empty">正在加载浏览器实例…</p>
      ) : state === 'failed' ? (
        <p className="acct-gov-empty">
          浏览器实例加载失败。<button onClick={() => void refresh()}>重试</button>
        </p>
      ) : browsers.length === 0 ? (
        <p className="acct-gov-empty">
          尚无浏览器实例记录。点击「同步实例清单」从 GEO_BROWSER_INSTANCES 同步。
        </p>
      ) : (
        <div className="acct-gov-table-scroll">
          <table aria-label="采集浏览器列表" className="acct-gov-table">
            <thead>
              <tr>
                <th>实例</th>
                <th>开启时长</th>
                <th>内存占用</th>
                <th>地域</th>
                <th>IP 地址</th>
                {COLLECTION_PLATFORMS.map((platform) => (
                  <th key={platform}>{PLATFORM_LABELS[platform]}</th>
                ))}
                <th>活动</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {browsers.map((row) => (
                <BrowserRow
                  key={row.browser_pub_id}
                  row={row}
                  now={now}
                  busy={busyKey === row.instance_key}
                  phoneByPubId={phoneByPubId}
                  regionNameByGb={regionNameByGb}
                  onRestart={() => void restart(row)}
                  onReleaseLock={() => void releaseLock(row)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ToastStack toasts={toasts} />
    </main>
  );
}

function BrowserRow({
  row,
  now,
  busy,
  phoneByPubId,
  regionNameByGb,
  onRestart,
  onReleaseLock,
}: {
  row: CollectionBrowserRow;
  now: number;
  busy: boolean;
  phoneByPubId: Map<string, CollectionAccountRow>;
  regionNameByGb: Map<string, string>;
  onRestart: () => void;
  onReleaseLock: () => void;
}) {
  const uptimeTone = row.uptime_s !== null && row.uptime_s > UPTIME_WARN_S ? 'warn' : undefined;
  const rssTone =
    row.rss_bytes !== null && row.rss_bytes > RSS_BAD_BYTES
      ? 'bad'
      : row.rss_bytes !== null && row.rss_bytes > RSS_WARN_BYTES
        ? 'warn'
        : undefined;
  const regionName = row.region_gb ? regionNameByGb.get(row.region_gb) : undefined;
  return (
    <tr>
      <td data-label="实例">
        <span title={row.systemd_unit ?? undefined}>{row.instance_key}</span>
      </td>
      <td data-label="开启时长" className={uptimeTone ? `acct-gov-${uptimeTone}` : undefined}>
        {formatUptime(row.uptime_s)}
      </td>
      <td data-label="内存占用" className={rssTone ? `acct-gov-${rssTone}` : undefined}>
        {formatBytes(row.rss_bytes)}
      </td>
      <td data-label="地域">
        {row.region_gb ? `${row.region_gb}${regionName ? ` ${regionName}` : ''}` : '—'}
      </td>
      <td data-label="IP 地址">{row.exit_ip ?? <span className="acct-gov-muted">未探测</span>}</td>
      {COLLECTION_PLATFORMS.map((platform) => {
        const boundPubId = row.bindings[platform];
        if (!boundPubId) {
          return (
            <td key={platform} className="acct-gov-null">
              —
            </td>
          );
        }
        const phone = phoneByPubId.get(boundPubId);
        return (
          <td key={platform} data-label={PLATFORM_LABELS[platform]}>
            <a
              href={`/platform/operations/accounts#acct-${boundPubId}`}
              title="跳转到账号管理页查看该手机号"
            >
              {phone ? accountPhoneLabel(phone) : boundPubId}
            </a>
          </td>
        );
      })}
      <td data-label="活动">
        <ActivityBadge row={row} now={now} />
      </td>
      <td data-label="操作">
        <div className="acct-gov-actions">
          <button onClick={onRestart} disabled={busy}>
            重启
          </button>
          <button onClick={onReleaseLock} disabled={busy}>
            释放锁
          </button>
        </div>
      </td>
    </tr>
  );
}

export function ActivityBadge({ row, now }: { row: CollectionBrowserRow; now: number }) {
  const abnormal: string[] = [];
  if (row.error_streak > 0) abnormal.push(`连续失败 ${row.error_streak} 次`);
  if (row.breaker_until && Date.parse(row.breaker_until) > now) {
    abnormal.push(
      `熔断至 ${new Date(row.breaker_until).toLocaleString('zh-CN', { hour12: false })}`,
    );
  }
  if (row.muted_until && Date.parse(row.muted_until) > now) {
    abnormal.push(`禁言至 ${new Date(row.muted_until).toLocaleString('zh-CN', { hour12: false })}`);
  }
  const base =
    row.activity === 'idle'
      ? '空闲'
      : row.activity === 'busy'
        ? '行动中'
        : row.activity === 'captcha'
          ? '验证码中'
          : row.activity;
  if (abnormal.length > 0) {
    return (
      <span className="acct-gov-badge error" title={abnormal.join('；')}>
        异常
      </span>
    );
  }
  const tone =
    row.activity === 'idle' ? 'idle' : row.activity === 'captcha' ? 'captcha' : 'running';
  return <span className={`acct-gov-badge ${tone}`}>{base}</span>;
}
