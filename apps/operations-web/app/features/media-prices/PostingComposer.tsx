import { useEffect, useMemo, useRef, useState } from 'react';
import {
  backfillPostingTarget,
  completePrfabuLogin,
  createPostingBatch,
  getPrfabuSessionStatus,
  getPostingBatch,
  listPostingBatches,
  refreshPostingBatch,
  startPrfabuLogin,
  submitPostingBatch,
  type IdentitySessionHeaders,
  type MediaPricesPlatform,
  type PostingBatch,
  type PostingBatchStatus,
  type PostingBatchSummary,
  type PostingTargetStatus,
  type PrfabuCaptchaChallenge,
  type PrfabuSessionStatus,
} from '@geo/api-client';

export type PostingSelection = {
  key: string;
  catalogType: 'news' | 'wemedia';
  mediaName: string;
  mediaPlatform: string;
  prices: Partial<Record<MediaPricesPlatform, number>>;
  provider: MediaPricesPlatform;
};

export function postingSelectionKey(
  catalogType: 'news' | 'wemedia',
  mediaName: string,
  mediaPlatform = '',
): string {
  return `${catalogType}\u0000${mediaPlatform}\u0000${mediaName}`;
}

const PROVIDER_LABELS: Record<MediaPricesPlatform, string> = {
  prfabu: 'prfabu',
  toumeiw: '投媒网',
  mtpfw: '媒体批发网',
  meititejia: '媒体特价网',
  meijiehezi: '媒介盒子',
  pinda: '品达发稿',
};

const BATCH_STATUS_LABELS: Record<PostingBatchStatus, string> = {
  draft: '待确认',
  queued: '已排队',
  processing: '提交中',
  partially_submitted: '部分已提交',
  submitted: '已提交',
  published: '已发出',
  blocked: '受阻',
  failed: '失败',
  canceled: '已取消',
};

const TARGET_STATUS_LABELS: Record<PostingTargetStatus, string> = {
  selected: '已选择',
  queued: '已排队',
  submitting: '提交中',
  submitted: '已提交',
  reviewing: '审核中',
  published: '已发出',
  balance_insufficient: '余额不足',
  provider_session_expired: '供应商会话失效',
  provider_confirmation_required: '需要人工确认',
  unsupported_provider: '供应商尚未接入',
  rejected: '已拒绝 / 退稿',
  failed: '提交失败',
  canceled: '已取消',
};

const ACTIVE_BATCH_STATUSES = new Set<PostingBatchStatus>(['queued', 'processing']);
const ACTIVE_TARGET_STATUSES = new Set<PostingTargetStatus>(['queued', 'submitting']);
const RETRYABLE_TARGET_STATUSES = new Set<PostingTargetStatus>([
  'balance_insufficient',
  'provider_session_expired',
  'unsupported_provider',
]);
const POLL_INTERVAL_MS = 4_000;

type Session = {
  tenantId: string;
  actorId: string;
  role: 'operator' | 'reviewer' | 'admin';
  headers: IdentitySessionHeaders;
};

function selectedPrice(selection: PostingSelection): number {
  return selection.prices[selection.provider] ?? 0;
}

function newIdempotencyKey(): string {
  const random =
    typeof globalThis.crypto?.randomUUID === 'function'
      ? globalThis.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `posting-${random}`;
}

export function statusTone(
  status: PostingBatchStatus | PostingTargetStatus,
): 'neutral' | 'progress' | 'success' | 'warn' | 'error' {
  if (['queued', 'processing', 'submitting', 'reviewing'].includes(status)) return 'progress';
  if (['submitted', 'published'].includes(status)) return 'success';
  if (
    [
      'partially_submitted',
      'blocked',
      'balance_insufficient',
      'provider_session_expired',
      'provider_confirmation_required',
      'unsupported_provider',
    ].includes(status)
  ) {
    return 'warn';
  }
  if (['failed', 'rejected', 'canceled'].includes(status)) return 'error';
  return 'neutral';
}

export function PostingComposer({
  session,
  selections,
  onProviderChange,
  onRemove,
  onClear,
}: {
  session: Session;
  selections: PostingSelection[];
  onProviderChange: (key: string, provider: MediaPricesPlatform) => void;
  onRemove: (key: string) => void;
  onClear: () => void;
}) {
  const requestHeaders = useMemo<IdentitySessionHeaders>(
    () => ({ ...session.headers }),
    [session.headers],
  );
  const canOperate = session.role === 'operator' || session.role === 'admin';
  const [document, setDocument] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [customerName, setCustomerName] = useState('');
  const [releaseTime, setReleaseTime] = useState('');
  const [note, setNote] = useState('');
  const [sopProjectPubId, setSopProjectPubId] = useState('');
  const [articleVersionPubId, setArticleVersionPubId] = useState('');
  const [autoSubmit, setAutoSubmit] = useState(true);
  const [confirmSpend, setConfirmSpend] = useState(false);
  const quotedTotal = useMemo(
    () => selections.reduce((total, selection) => total + selectedPrice(selection), 0),
    [selections],
  );
  const [maxTotalAmount, setMaxTotalAmount] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<{
    tone: 'info' | 'warn' | 'error';
    text: string;
  } | null>(null);
  const [recent, setRecent] = useState<PostingBatchSummary[]>([]);
  const [currentBatch, setCurrentBatch] = useState<PostingBatch | null>(null);
  const [backfillUrls, setBackfillUrls] = useState<Record<string, string>>({});
  const [providerSession, setProviderSession] = useState<PrfabuSessionStatus | null>(null);
  const [providerSessionLoading, setProviderSessionLoading] = useState(canOperate);
  const [providerLoginOpen, setProviderLoginOpen] = useState(false);
  const [providerChallenge, setProviderChallenge] = useState<PrfabuCaptchaChallenge | null>(null);
  const [providerAccount, setProviderAccount] = useState('');
  const [providerPassword, setProviderPassword] = useState('');
  const [providerCaptcha, setProviderCaptcha] = useState('');
  const [providerLoginSubmitting, setProviderLoginSubmitting] = useState(false);
  const [recentState, setRecentState] = useState<'loading' | 'ready' | 'forbidden' | 'failed'>(
    'loading',
  );
  const idempotencyKeyRef = useRef(newIdempotencyKey());

  useEffect(() => {
    setMaxTotalAmount((current) => {
      if (quotedTotal <= 0) return '';
      const numeric = Number(current);
      return current === '' || !Number.isFinite(numeric) || numeric < quotedTotal
        ? quotedTotal.toFixed(2)
        : current;
    });
  }, [quotedTotal]);

  const reloadRecent = async () => {
    const result = await listPostingBatches(requestHeaders);
    if (result.kind === 'ready') {
      setRecent(result.data);
      setRecentState('ready');
    } else {
      setRecentState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
    }
  };

  useEffect(() => {
    void reloadRecent();
  }, [requestHeaders]);

  useEffect(() => {
    let cancelled = false;
    if (!canOperate) {
      setProviderSessionLoading(false);
      return;
    }
    setProviderSessionLoading(true);
    void getPrfabuSessionStatus(requestHeaders).then((result) => {
      if (cancelled) return;
      setProviderSessionLoading(false);
      setProviderSession(
        result.kind === 'ready'
          ? result.data
          : { status: 'unavailable', message: '供应商会话状态暂不可用', balance: null },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [canOperate, requestHeaders]);

  useEffect(() => {
    if (
      !currentBatch ||
      (!ACTIVE_BATCH_STATUSES.has(currentBatch.status) &&
        !currentBatch.targets.some((target) => ACTIVE_TARGET_STATUSES.has(target.status)))
    ) {
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void getPostingBatch(currentBatch.pubId, requestHeaders).then((result) => {
        if (cancelled || result.kind !== 'ready') return;
        setCurrentBatch(result.data);
        void reloadRecent();
      });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [currentBatch, requestHeaders]);

  const beginProviderLogin = async () => {
    setProviderLoginOpen(true);
    setProviderLoginSubmitting(true);
    setNotice({ tone: 'info', text: '正在获取 prfabu 图形验证码…' });
    const result = await startPrfabuLogin(requestHeaders);
    setProviderLoginSubmitting(false);
    if (result.kind === 'ready') {
      setProviderChallenge(result.data);
      setProviderCaptcha('');
      setNotice({ tone: 'info', text: '请输入账号、密码和图片中的验证码。' });
      return;
    }
    setProviderChallenge(null);
    setNotice({ tone: 'error', text: '验证码获取失败，请稍后重试。' });
  };

  const completeProviderLogin = async () => {
    if (!providerChallenge || !providerAccount.trim() || !providerPassword || !providerCaptcha) {
      setNotice({ tone: 'error', text: '请完整填写 prfabu 账号、密码和验证码。' });
      return;
    }
    setProviderLoginSubmitting(true);
    setNotice({ tone: 'info', text: '正在验证 prfabu 登录并安全保存会话…' });
    const result = await completePrfabuLogin(
      {
        challengeId: providerChallenge.challengeId,
        account: providerAccount.trim(),
        password: providerPassword,
        captcha: providerCaptcha.trim(),
      },
      requestHeaders,
    );
    setProviderPassword('');
    setProviderCaptcha('');
    setProviderChallenge(null);
    setProviderLoginSubmitting(false);
    if (result.kind === 'ready' && result.data.status === 'ready') {
      setProviderSession(result.data);
      setProviderLoginOpen(false);
      setNotice({ tone: 'info', text: 'prfabu 登录成功，现在可以直接发帖。' });
      return;
    }
    if (result.kind === 'ready' && result.data.status === 'rejected') {
      setProviderSession(result.data);
      setNotice({ tone: 'error', text: `${result.data.message}，请获取新验证码后重试。` });
      return;
    }
    setNotice({ tone: 'error', text: '登录请求失败或验证码已过期，请重新获取验证码。' });
  };

  const createBatch = async () => {
    const maximum = Number(maxTotalAmount);
    if (!document) {
      setNotice({ tone: 'error', text: '请选择上游产出的 .docx 图文文档。' });
      return;
    }
    if (selections.length === 0) {
      setNotice({ tone: 'error', text: '请先在比价表里选择至少一个媒体。' });
      return;
    }
    if (!Number.isFinite(maximum) || maximum < quotedTotal || maximum <= 0) {
      setNotice({
        tone: 'error',
        text: `预算上限不能低于当前报价合计 ¥${quotedTotal.toFixed(2)}。`,
      });
      return;
    }
    if (autoSubmit && !confirmSpend) {
      setNotice({ tone: 'error', text: '自动发帖会产生真实费用，请先确认扣费授权。' });
      return;
    }
    if (
      autoSubmit &&
      selections.some((selection) => selection.provider === 'prfabu') &&
      providerSession?.status !== 'ready'
    ) {
      setProviderLoginOpen(true);
      setNotice({ tone: 'error', text: '请先在本页登录 prfabu，再开始自动发帖。' });
      return;
    }
    setSubmitting(true);
    setNotice({ tone: 'info', text: '正在解析 DOCX、核对服务端报价并创建发帖批次…' });
    const result = await createPostingBatch(
      {
        document,
        targets: selections.map((selection) => ({
          catalogType: selection.catalogType,
          provider: selection.provider,
          mediaName: selection.mediaName,
          mediaPlatform: selection.mediaPlatform,
        })),
        title: title.trim(),
        customerName: customerName.trim(),
        ...(releaseTime ? { releaseTime } : {}),
        autoSubmit,
        confirmSpend: autoSubmit && confirmSpend,
        maxTotalAmount: maximum,
        note: note.trim(),
        ...(sopProjectPubId.trim() ? { sopProjectPubId: sopProjectPubId.trim() } : {}),
        ...(articleVersionPubId.trim() ? { articleVersionPubId: articleVersionPubId.trim() } : {}),
        idempotencyKey: idempotencyKeyRef.current,
      },
      requestHeaders,
    );
    setSubmitting(false);
    if (result.kind === 'ready') {
      setCurrentBatch(result.data);
      setNotice({
        tone: 'info',
        text: autoSubmit
          ? '预算已确认，发帖任务已直接进入队列。'
          : '发帖批次草稿已保存，可稍后确认预算并开始发帖。',
      });
      idempotencyKeyRef.current = newIdempotencyKey();
      setConfirmSpend(false);
      void reloadRecent();
      return;
    }
    if (result.kind === 'conflict') {
      setNotice({
        tone: 'error',
        text: '当前媒体报价已变化、媒体已下架，或预算低于服务端最新报价，请刷新比价数据后重试。',
      });
    } else if (result.kind === 'invalid') {
      setNotice({ tone: 'error', text: 'DOCX 无法解析或配置字段不合法，请检查文档后重试。' });
    } else if (result.kind === 'forbidden') {
      setNotice({ tone: 'error', text: '当前账号没有创建自动发帖批次的权限。' });
    } else {
      setNotice({
        tone: 'error',
        text: '创建请求暂不可用；可用同一配置重试，幂等键会避免重复下单。',
      });
    }
  };

  const openBatch = async (batchPubId: string) => {
    const result = await getPostingBatch(batchPubId, requestHeaders);
    if (result.kind === 'ready') {
      setCurrentBatch(result.data);
      setNotice(null);
    } else {
      setNotice({ tone: 'error', text: '发帖批次详情暂时无法读取。' });
    }
  };

  const refreshBatch = async () => {
    if (!currentBatch) return;
    setNotice({ tone: 'info', text: '正在向供应商同步订单与回链状态…' });
    const result = await refreshPostingBatch(currentBatch.pubId, requestHeaders);
    if (result.kind === 'ready') {
      setCurrentBatch(result.data);
      setNotice({ tone: 'info', text: '供应商状态同步完成。' });
      void reloadRecent();
    } else {
      setNotice({ tone: 'error', text: '供应商状态暂时无法同步，请稍后重试。' });
    }
  };

  const startBatch = async () => {
    if (!currentBatch) return;
    const retrying = currentBatch.targets.some((target) =>
      RETRYABLE_TARGET_STATUSES.has(target.status),
    );
    const maximum = Number(
      maxTotalAmount || currentBatch.maxTotalAmount || currentBatch.quotedTotalAmount,
    );
    const result = await submitPostingBatch(currentBatch.pubId, maximum, requestHeaders);
    if (result.kind === 'ready') {
      setCurrentBatch(result.data);
      setNotice({
        tone: 'info',
        text: retrying ? '受阻目标已重新排队。' : '预算已确认，发帖任务已直接进入队列。',
      });
      void reloadRecent();
    } else {
      setNotice({ tone: 'error', text: '开始发帖失败，请核对预算、登录态和批次状态。' });
    }
  };

  const backfillTarget = async (targetPubId: string) => {
    if (!currentBatch) return;
    const publicUrl = (backfillUrls[targetPubId] ?? '').trim();
    const result = await backfillPostingTarget(
      currentBatch.pubId,
      targetPubId,
      {
        status: 'published',
        publicUrl,
        providerMessage: '运营已核验公开回链并完成人工回填。',
      },
      requestHeaders,
    );
    if (result.kind === 'ready') {
      setCurrentBatch(result.data);
      setNotice({ tone: 'info', text: '公开回链与发布状态已回填。' });
      void reloadRecent();
    } else {
      setNotice({ tone: 'error', text: '回填失败，请确认使用有效的 http(s) 公开链接。' });
    }
  };

  return (
    <section className="posting-composer" aria-labelledby="posting-composer-title">
      <header className="posting-composer-heading">
        <div>
          <span className="eyebrow">paid distribution</span>
          <h3 id="posting-composer-title">自动发帖配置</h3>
          <p>选择媒体后上传上游图文 DOCX；系统冻结报价并逐媒体记录下单与发帖状态。</p>
        </div>
        {selections.length > 0 ? (
          <button type="button" onClick={onClear}>
            清空已选
          </button>
        ) : null}
      </header>

      <section className="posting-provider-login" aria-label="prfabu 供应商登录">
        <div className="posting-provider-login-summary">
          <div>
            <strong>prfabu 登录态</strong>
            <p>
              {providerSessionLoading
                ? '正在验证…'
                : (providerSession?.message ?? '尚未验证供应商会话')}
              {providerSession?.status === 'ready' && providerSession.balance !== null
                ? ` · 可用余额 ¥${providerSession.balance.toFixed(2)}`
                : ''}
            </p>
          </div>
          <span
            className={`posting-status ${providerSession?.status === 'ready' ? 'success' : 'warn'}`}
          >
            {providerSession?.status === 'ready' ? '可发帖' : '需要登录'}
          </span>
          {canOperate ? (
            <button
              type="button"
              disabled={providerLoginSubmitting}
              onClick={() => void beginProviderLogin()}
            >
              {providerChallenge ? '换一张验证码' : '网页登录 / 更新会话'}
            </button>
          ) : null}
        </div>
        {providerLoginOpen ? (
          <form
            className="posting-provider-login-form"
            onSubmit={(event) => {
              event.preventDefault();
              void completeProviderLogin();
            }}
          >
            <p>账号和密码仅用于本次供应商登录请求，不会写入数据库或会话文件。</p>
            <label>
              prfabu 账号
              <input
                type="text"
                autoComplete="username"
                maxLength={120}
                value={providerAccount}
                onChange={(event) => setProviderAccount(event.target.value)}
              />
            </label>
            <label>
              prfabu 密码
              <input
                type="password"
                autoComplete="current-password"
                maxLength={256}
                value={providerPassword}
                onChange={(event) => setProviderPassword(event.target.value)}
              />
            </label>
            {providerChallenge ? (
              <div className="posting-provider-captcha">
                <img
                  src={`data:image/png;base64,${providerChallenge.imageBase64}`}
                  alt="prfabu 图形验证码"
                />
                <label>
                  图片验证码
                  <input
                    type="text"
                    inputMode="numeric"
                    autoComplete="off"
                    maxLength={12}
                    value={providerCaptcha}
                    onChange={(event) => setProviderCaptcha(event.target.value)}
                  />
                </label>
                <small>
                  验证码 {providerChallenge.expiresInSeconds / 60} 分钟内有效且只能使用一次。
                </small>
              </div>
            ) : (
              <p>请点击“网页登录 / 更新会话”获取验证码。</p>
            )}
            <div className="posting-confirmation">
              <button
                type="submit"
                className="primary"
                disabled={!providerChallenge || providerLoginSubmitting}
              >
                {providerLoginSubmitting ? '正在登录…' : '登录并保存会话'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setProviderLoginOpen(false);
                  setProviderChallenge(null);
                  setProviderPassword('');
                  setProviderCaptcha('');
                }}
              >
                取消
              </button>
            </div>
          </form>
        ) : null}
      </section>

      {selections.length === 0 ? (
        <div className="posting-empty">在新闻媒体或自媒体表格首列勾选媒体后，可继续配置。</div>
      ) : (
        <div className="posting-selection-list" aria-label="已选媒体">
          {selections.map((selection) => {
            const providers = Object.entries(selection.prices).filter(
              (entry): entry is [MediaPricesPlatform, number] => entry[1] != null,
            );
            return (
              <article key={selection.key}>
                <div>
                  <strong>{selection.mediaName}</strong>
                  <small>
                    {selection.catalogType === 'wemedia'
                      ? `自媒体 · ${selection.mediaPlatform}`
                      : '新闻媒体'}
                  </small>
                </div>
                <label>
                  采购服务
                  <select
                    aria-label={`${selection.mediaName}采购服务`}
                    value={selection.provider}
                    onChange={(event) =>
                      onProviderChange(selection.key, event.target.value as MediaPricesPlatform)
                    }
                  >
                    {providers.map(([provider, price]) => (
                      <option key={provider} value={provider}>
                        {PROVIDER_LABELS[provider]} · ¥{price}
                        {provider === 'prfabu' ? ' · 可自动' : ' · 待接入'}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  aria-label={`移除${selection.mediaName}`}
                  onClick={() => onRemove(selection.key)}
                >
                  移除
                </button>
              </article>
            );
          })}
        </div>
      )}

      <div className="posting-form-grid">
        <label>
          图文 DOCX
          <input
            type="file"
            accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(event) => setDocument(event.target.files?.[0] ?? null)}
          />
        </label>
        <label>
          标题覆盖（可选）
          <input
            type="text"
            value={title}
            maxLength={300}
            placeholder="留空则取 DOCX 首个标题"
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          客户 / 品牌（可选）
          <input
            type="text"
            value={customerName}
            maxLength={300}
            onChange={(event) => setCustomerName(event.target.value)}
          />
        </label>
        <label>
          期望发布日期（可选）
          <input
            type="date"
            value={releaseTime}
            onChange={(event) => setReleaseTime(event.target.value)}
          />
        </label>
        <label>
          报价合计
          <output>¥{quotedTotal.toFixed(2)}</output>
        </label>
        <label>
          扣费预算上限
          <input
            type="number"
            min={quotedTotal || 0.01}
            step="0.01"
            value={maxTotalAmount}
            onChange={(event) => setMaxTotalAmount(event.target.value)}
          />
        </label>
        <label className="posting-note-field">
          发稿备注（可选）
          <textarea
            value={note}
            maxLength={1_000}
            rows={3}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
        <label>
          SOP 项目标识（可选）
          <input
            value={sopProjectPubId}
            maxLength={120}
            placeholder="sop_…"
            onChange={(event) => setSopProjectPubId(event.target.value)}
          />
        </label>
        <label>
          文章版本标识（可选）
          <input
            value={articleVersionPubId}
            maxLength={120}
            placeholder="artv_…"
            onChange={(event) => setArticleVersionPubId(event.target.value)}
          />
        </label>
      </div>

      <div className="posting-confirmation">
        <label>
          <input
            type="checkbox"
            checked={autoSubmit}
            onChange={(event) => {
              setAutoSubmit(event.target.checked);
              if (!event.target.checked) setConfirmSpend(false);
            }}
          />
          确认后自动提交供应商
        </label>
        {autoSubmit ? (
          <label className="spend-confirm">
            <input
              type="checkbox"
              checked={confirmSpend}
              onChange={(event) => setConfirmSpend(event.target.checked)}
            />
            我确认按服务端最新报价扣费，且总额不超过 ¥{Number(maxTotalAmount || 0).toFixed(2)}
          </label>
        ) : null}
        <button
          type="button"
          className="primary"
          disabled={!canOperate || submitting || selections.length === 0}
          onClick={() => void createBatch()}
        >
          {!canOperate
            ? '仅可查看'
            : submitting
              ? '正在创建…'
              : autoSubmit
                ? '确认预算并开始发帖'
                : '保存发帖草稿'}
        </button>
      </div>
      <p className="posting-adapter-note">
        当前 prfabu
        已接自动下单与状态同步；其他供应商会保留所选服务和报价，但状态明确显示“供应商尚未接入”。
      </p>
      {notice ? (
        <div className={`media-prices-notice ${notice.tone}`} role="status">
          {notice.text}
        </div>
      ) : null}

      {currentBatch ? (
        <section className="posting-status-panel" aria-label="当前发帖批次">
          <header>
            <div>
              <span className={`posting-status ${statusTone(currentBatch.status)}`}>
                {BATCH_STATUS_LABELS[currentBatch.status]}
              </span>
              <span className={`posting-status ${currentBatch.approvalState}`}>
                扣费确认：{currentBatch.approvalState === 'approved' ? '已确认' : '未确认'}
              </span>
              <h4>{currentBatch.title}</h4>
              <p>
                {currentBatch.sourceFilename} · {currentBatch.imageCount} 张图 · 报价 ¥
                {currentBatch.quotedTotalAmount.toFixed(2)}
              </p>
            </div>
            <button type="button" disabled={!canOperate} onClick={() => void refreshBatch()}>
              同步供应商状态
            </button>
          </header>
          <div className="posting-confirmation" aria-label="发帖提交控制">
            {canOperate && ['draft', 'rejected'].includes(currentBatch.approvalState) ? (
              <button type="button" className="primary" onClick={() => void startBatch()}>
                确认预算并开始发帖
              </button>
            ) : null}
            {canOperate &&
            currentBatch.approvalState === 'approved' &&
            currentBatch.targets.some((target) => RETRYABLE_TARGET_STATUSES.has(target.status)) ? (
              <button type="button" className="primary" onClick={() => void startBatch()}>
                会话或余额恢复后重试
              </button>
            ) : null}
          </div>
          <details>
            <summary>帖子内容</summary>
            <pre>{currentBatch.contentText}</pre>
          </details>
          <div className="posting-target-statuses">
            {currentBatch.targets.map((target) => (
              <article key={target.pubId}>
                <div>
                  <strong>{target.mediaName}</strong>
                  <small>
                    {PROVIDER_LABELS[target.provider]} · ¥{target.quotedPrice.toFixed(2)}
                    {target.externalOrderId ? ` · 订单 ${target.externalOrderId}` : ''}
                  </small>
                </div>
                <span className={`posting-status ${statusTone(target.status)}`}>
                  {TARGET_STATUS_LABELS[target.status]}
                </span>
                <p>{target.providerMessage || '等待状态更新'}</p>
                {target.publicUrl ? (
                  <a href={target.publicUrl} target="_blank" rel="noopener noreferrer">
                    查看已发帖子
                  </a>
                ) : null}
                {canOperate && target.status !== 'published' ? (
                  <div className="posting-confirmation">
                    <label>
                      公开回链
                      <input
                        type="url"
                        placeholder="https://…"
                        value={backfillUrls[target.pubId] ?? ''}
                        onChange={(event) =>
                          setBackfillUrls((current) => ({
                            ...current,
                            [target.pubId]: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <button type="button" onClick={() => void backfillTarget(target.pubId)}>
                      回填已发布
                    </button>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        </section>
      ) : null}

      <section className="posting-history" aria-label="最近发帖批次">
        <h4>最近发帖批次</h4>
        {recentState === 'loading' ? <p>正在读取…</p> : null}
        {recentState === 'forbidden' ? <p>无权查看发帖记录。</p> : null}
        {recentState === 'failed' ? (
          <p>
            发帖记录暂不可用。{' '}
            <button type="button" onClick={() => void reloadRecent()}>
              重试
            </button>
          </p>
        ) : null}
        {recentState === 'ready' && recent.length === 0 ? <p>尚无发帖批次。</p> : null}
        {recent.length > 0 ? (
          <div className="posting-history-list">
            {recent.map((batch) => (
              <button type="button" key={batch.pubId} onClick={() => void openBatch(batch.pubId)}>
                <span>
                  <strong>{batch.title}</strong>
                  <small>
                    {batch.targetCount} 个媒体 · {batch.submittedCount} 已提交 ·{' '}
                    {batch.publishedCount} 已发出
                  </small>
                </span>
                <span className={`posting-status ${statusTone(batch.status)}`}>
                  {BATCH_STATUS_LABELS[batch.status]}
                </span>
              </button>
            ))}
          </div>
        ) : null}
      </section>
    </section>
  );
}
