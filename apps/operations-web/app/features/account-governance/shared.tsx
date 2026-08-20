import { useEffect, useState, type ReactNode } from 'react';

/** 相对时间展示 + 绝对时间悬停（无效时间戳诚实显示「—」）。 */
export function RelativeTime({ iso, now }: { iso: string | null | undefined; now?: number }) {
  if (!iso) return <span className="acct-gov-muted">—</span>;
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return <span className="acct-gov-muted">—</span>;
  const absolute = new Date(ts).toLocaleString('zh-CN', { hour12: false });
  return (
    <time dateTime={iso} title={absolute}>
      {formatRelative(ts, now ?? Date.now())}
    </time>
  );
}

export function formatRelative(ts: number, now: number): string {
  const diffMs = ts - now;
  const abs = Math.abs(diffMs);
  const suffix = diffMs >= 0 ? '后' : '前';
  if (abs < 60_000) return diffMs >= 0 ? '1 分钟内' : '刚刚';
  if (abs < 3_600_000) return `${Math.round(abs / 60_000)} 分钟${suffix}`;
  if (abs < 86_400_000) return `${Math.round(abs / 3_600_000)} 小时${suffix}`;
  return `${Math.round(abs / 86_400_000)} 天${suffix}`;
}

/** 未来时刻倒计时（如 muted_until / quota_resume_at）；已过或无效返回 null。 */
export function formatCountdown(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null;
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return null;
  const remain = ts - now;
  if (remain <= 0) return null;
  const totalMinutes = Math.ceil(remain / 60_000);
  if (totalMinutes >= 60 * 48) return `剩余 ${Math.round(totalMinutes / (60 * 24))} 天`;
  if (totalMinutes >= 60)
    return `剩余 ${Math.floor(totalMinutes / 60)} 小时 ${totalMinutes % 60} 分`;
  return `剩余 ${totalMinutes} 分钟`;
}

/** 秒数人性化（浏览器开启时长）。 */
export function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—';
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  return `${minutes} 分`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes >= 1 << 30) return `${(bytes / (1 << 30)).toFixed(1)} GB`;
  if (bytes >= 1 << 20) return `${Math.round(bytes / (1 << 20))} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

export const GB = 1 << 30;

/** 链路状态灯：ok=绿 / down=红 / untested（及其他）=灰。 */
export function LinkLight({ state, label }: { state: string; label: string }) {
  const tone = state === 'ok' ? 'ok' : state === 'down' ? 'bad' : 'untested';
  const text = state === 'ok' ? '通' : state === 'down' ? '断' : '未测';
  return (
    <span
      className={`acct-gov-light ${tone}`}
      role="img"
      aria-label={`${label}${text}`}
      title={`${label}${text}`}
    />
  );
}

/** 页面可见时轮询；隐藏标签页跳过（避免后台空转）。 */
export function useVisiblePolling(callback: () => void, intervalMs: number) {
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
      callback();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [callback, intervalMs]);
}

/** 低频时钟（状态倒计时等粗粒度刷新用）。 */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export type ToastMessage = { id: number; tone: 'positive' | 'warning' | 'negative'; text: string };

export function ToastStack({ toasts }: { toasts: ToastMessage[] }) {
  if (toasts.length === 0) return null;
  return (
    <div className="acct-gov-toasts">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`acct-gov-toast ${toast.tone}`}
          role={toast.tone === 'negative' ? 'alert' : 'status'}
        >
          {toast.text}
        </div>
      ))}
    </div>
  );
}

export function FieldRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="acct-gov-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

/** 已知错误码中文化；未知码如实透传（不掩饰后端口径）。 */
export function describeApiError(cause: unknown): string {
  const code = cause instanceof Error ? cause.message : String(cause);
  const known: Record<string, string> = {
    region_change_requires_confirmation: '地域变更需要二次确认',
    region_ip_mismatch: '地域IP不匹配：账号地域绑定与浏览器出口地域不一致',
    region_not_available: '地域不可用（未登记或状态异常）',
    phone_already_exists: '该手机号已存在',
    region_already_exists: '该地域已存在',
    push_channel_not_configured: '接管通道未配置',
    browser_instances_not_configured: '浏览器实例清单未配置（GEO_BROWSER_INSTANCES）',
    permission_denied: '权限不足（写操作需要 account:operate）',
  };
  return known[code] ? `${known[code]}（${code}）` : `操作失败：${code}`;
}
