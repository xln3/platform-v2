import { useEffect, useMemo, useState } from 'react';
import {
  completeProviderAccountLogin,
  deleteProviderAccount,
  listProviderAccounts,
  saveProviderAccount,
  startProviderAccountLogin,
  type IdentitySessionHeaders,
  type MediaPricesPlatform,
  type ProviderAccountStatus,
  type ProviderCaptchaChallenge,
} from '@geo/api-client';

type Session = {
  tenantId: string;
  actorId: string;
  role: 'operator' | 'reviewer' | 'admin';
  headers: IdentitySessionHeaders;
};

type CredentialDraft = { account: string; password: string; captcha: string };

const EMPTY_DRAFT: CredentialDraft = { account: '', password: '', captcha: '' };

const STATUS_LABELS: Record<ProviderAccountStatus['sessionStatus'], string> = {
  not_configured: '未配置',
  needs_login: '待登录',
  ready: '会话有效',
  expired: '会话失效',
  rejected: '登录失败',
  verification_required: '需要二次验证',
  interactive_required: '需要交互验证',
  unavailable: '状态未知',
};

function statusTone(status: ProviderAccountStatus['sessionStatus']): string {
  if (status === 'ready') return 'success';
  if (status === 'rejected' || status === 'unavailable') return 'error';
  return 'warn';
}

export function ProviderAccountManager({
  session,
  onAccountsChange,
}: {
  session: Session;
  onAccountsChange: (accounts: ProviderAccountStatus[]) => void;
}) {
  const requestHeaders = useMemo<IdentitySessionHeaders>(
    () => ({ ...session.headers }),
    [session.headers],
  );
  const [accounts, setAccounts] = useState<ProviderAccountStatus[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [editing, setEditing] = useState<MediaPricesPlatform | null>(null);
  const [drafts, setDrafts] = useState<Partial<Record<MediaPricesPlatform, CredentialDraft>>>({});
  const [challenges, setChallenges] = useState<
    Partial<Record<MediaPricesPlatform, ProviderCaptchaChallenge>>
  >({});
  const [busy, setBusy] = useState<MediaPricesPlatform | null>(null);
  const [notice, setNotice] = useState<{ tone: 'info' | 'error'; text: string } | null>(null);

  const replaceAccount = (next: ProviderAccountStatus) => {
    const replaced = accounts.map((item) => (item.provider === next.provider ? next : item));
    setAccounts(replaced);
    onAccountsChange(replaced);
  };

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    void listProviderAccounts(requestHeaders).then((result) => {
      if (cancelled) return;
      if (result.kind === 'ready') {
        setAccounts(result.data);
        onAccountsChange(result.data);
        setState('ready');
      } else {
        setAccounts([]);
        onAccountsChange([]);
        setState('failed');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [onAccountsChange, requestHeaders]);

  const draftFor = (provider: MediaPricesPlatform): CredentialDraft =>
    drafts[provider] ?? EMPTY_DRAFT;

  const updateDraft = (provider: MediaPricesPlatform, patch: Partial<CredentialDraft>) => {
    setDrafts((current) => ({
      ...current,
      [provider]: { ...(current[provider] ?? EMPTY_DRAFT), ...patch },
    }));
  };

  const toggleEditing = (provider: MediaPricesPlatform) => {
    setDrafts((current) =>
      Object.fromEntries(
        Object.entries(current).map(([key, value]) => [key, { ...value, password: '' }]),
      ),
    );
    setEditing((current) => (current === provider ? null : provider));
  };

  const beginLogin = async (provider: MediaPricesPlatform) => {
    setBusy(provider);
    setNotice({ tone: 'info', text: `正在获取 ${provider} 登录验证码…` });
    const result = await startProviderAccountLogin(provider, requestHeaders);
    setBusy(null);
    if (result.kind === 'ready') {
      setChallenges((current) => ({ ...current, [provider]: result.data }));
      updateDraft(provider, { captcha: '' });
      setEditing(provider);
      setNotice({ tone: 'info', text: '验证码已获取；输入图片文字即可完成网页登录。' });
      return;
    }
    setNotice({ tone: 'error', text: '验证码获取失败，请确认凭据已保存后重试。' });
  };

  const saveCredentials = async (account: ProviderAccountStatus) => {
    const draft = draftFor(account.provider);
    if (!draft.account.trim() || !draft.password) {
      setNotice({ tone: 'error', text: '请填写账号和密码。' });
      return;
    }
    setBusy(account.provider);
    setNotice({ tone: 'info', text: `正在加密保存 ${account.label} 凭据…` });
    const result = await saveProviderAccount(
      account.provider,
      { account: draft.account.trim(), password: draft.password },
      requestHeaders,
    );
    updateDraft(account.provider, { password: '' });
    if (result.kind !== 'ready') {
      setBusy(null);
      setNotice({ tone: 'error', text: '凭据保存失败；密码未保留在页面中，请重新输入。' });
      return;
    }
    replaceAccount(result.data);
    if (result.data.loginMode === 'interactive') {
      setBusy(null);
      setNotice({
        tone: 'info',
        text: `${account.label} 凭据已加密保存；该平台使用供应商交互验证码，暂不能由本页自动接管会话。`,
      });
      return;
    }
    setBusy(null);
    await beginLogin(account.provider);
  };

  const completeLogin = async (account: ProviderAccountStatus) => {
    const challenge = challenges[account.provider];
    const captcha = draftFor(account.provider).captcha.trim();
    if (!challenge || !captcha) {
      setNotice({ tone: 'error', text: '请输入图片中的验证码。' });
      return;
    }
    setBusy(account.provider);
    setNotice({ tone: 'info', text: `正在验证 ${account.label} 登录…` });
    const result = await completeProviderAccountLogin(
      account.provider,
      { challengeId: challenge.challengeId, captcha },
      requestHeaders,
    );
    setBusy(null);
    setChallenges((current) => ({ ...current, [account.provider]: undefined }));
    updateDraft(account.provider, { captcha: '' });
    if (result.kind !== 'ready') {
      setNotice({ tone: 'error', text: '登录请求失败或验证码已过期，请获取新验证码。' });
      return;
    }
    replaceAccount(result.data);
    setNotice({
      tone: result.data.sessionStatus === 'ready' ? 'info' : 'error',
      text: result.data.sessionMessage,
    });
    if (result.data.sessionStatus === 'ready') setEditing(null);
  };

  const removeCredentials = async (account: ProviderAccountStatus) => {
    if (!window.confirm(`删除 ${account.label} 已保存的账号、密码和登录会话？`)) return;
    setBusy(account.provider);
    const result = await deleteProviderAccount(account.provider, requestHeaders);
    setBusy(null);
    if (result.kind !== 'ready') {
      setNotice({ tone: 'error', text: '删除凭据失败，请稍后重试。' });
      return;
    }
    const next: ProviderAccountStatus = {
      ...account,
      configured: false,
      accountMask: '',
      sessionStatus: 'not_configured',
      sessionMessage: '尚未配置账号凭据',
      balance: null,
      updatedAt: null,
    };
    replaceAccount(next);
    setChallenges((current) => ({ ...current, [account.provider]: undefined }));
    setDrafts((current) => ({ ...current, [account.provider]: EMPTY_DRAFT }));
    setNotice({ tone: 'info', text: `${account.label} 凭据和会话已删除。` });
  };

  return (
    <section className="posting-accounts" aria-labelledby="posting-accounts-title">
      <header className="posting-composer-heading">
        <div>
          <span className="eyebrow">encrypted provider vault</span>
          <h3 id="posting-accounts-title">平台账号与自动登录</h3>
          <p>账号、密码和会话按租户加密保存；页面不回显密码，也不再需要登录服务器终端。</p>
        </div>
      </header>
      {state === 'loading' ? <div className="posting-empty">正在读取平台账号…</div> : null}
      {state === 'failed' ? (
        <div className="media-prices-notice error" role="alert">
          加密凭据库暂不可用，已禁止自动发帖。
        </div>
      ) : null}
      {state === 'ready' ? (
        <div className="posting-account-grid">
          {accounts.map((account) => {
            const draft = draftFor(account.provider);
            const challenge = challenges[account.provider];
            const isEditing = editing === account.provider;
            return (
              <article key={account.provider} className="posting-account-card">
                <header>
                  <div>
                    <strong>{account.label}</strong>
                    <small>{account.configured ? account.accountMask : '未保存账号'}</small>
                  </div>
                  <span className={`posting-status ${statusTone(account.sessionStatus)}`}>
                    {STATUS_LABELS[account.sessionStatus]}
                  </span>
                </header>
                <p>{account.sessionMessage}</p>
                {account.balance !== null ? <p>可用余额 ¥{account.balance.toFixed(2)}</p> : null}
                <small>
                  {account.postingSupported ? '已接入自动下单' : '已预留账号与会话；下单适配待接入'}
                </small>
                {account.loginMode === 'interactive' ? (
                  <p className="posting-account-caveat">
                    供应商要求腾讯交互验证码，无法安全转成图片验证码；当前只保存加密凭据，不冒充登录成功。
                  </p>
                ) : null}
                {isEditing ? (
                  <form
                    className="posting-account-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void saveCredentials(account);
                    }}
                  >
                    <label>
                      账号
                      <input
                        type="text"
                        autoComplete="username"
                        maxLength={120}
                        value={draft.account}
                        onChange={(event) =>
                          updateDraft(account.provider, { account: event.target.value })
                        }
                      />
                    </label>
                    <label>
                      密码
                      <input
                        type="password"
                        autoComplete="current-password"
                        maxLength={256}
                        value={draft.password}
                        onChange={(event) =>
                          updateDraft(account.provider, { password: event.target.value })
                        }
                      />
                    </label>
                    <button type="submit" className="primary" disabled={busy !== null}>
                      保存凭据{account.loginMode === 'image_captcha' ? '并登录' : ''}
                    </button>
                  </form>
                ) : null}
                {challenge ? (
                  <form
                    className="posting-provider-captcha"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void completeLogin(account);
                    }}
                  >
                    <img
                      src={`data:${challenge.imageMimeType};base64,${challenge.imageBase64}`}
                      alt={`${account.label} 图形验证码`}
                    />
                    <label>
                      图片验证码
                      <input
                        type="text"
                        autoComplete="off"
                        maxLength={32}
                        value={draft.captcha}
                        onChange={(event) =>
                          updateDraft(account.provider, { captcha: event.target.value })
                        }
                      />
                    </label>
                    <button type="submit" className="primary" disabled={busy !== null}>
                      验证并保存会话
                    </button>
                  </form>
                ) : null}
                <div className="posting-account-actions">
                  <button type="button" onClick={() => toggleEditing(account.provider)}>
                    {isEditing ? '收起' : account.configured ? '更新凭据' : '配置账号'}
                  </button>
                  {account.configured && account.loginMode === 'image_captcha' ? (
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => void beginLogin(account.provider)}
                    >
                      {account.sessionStatus === 'ready' ? '重新登录' : '获取验证码'}
                    </button>
                  ) : null}
                  {account.configured ? (
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => void removeCredentials(account)}
                    >
                      删除凭据
                    </button>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
      {notice ? (
        <div className={`media-prices-notice ${notice.tone}`} role="status">
          {notice.text}
        </div>
      ) : null}
    </section>
  );
}
