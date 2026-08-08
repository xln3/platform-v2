import { useState, type FormEvent } from 'react';
import { useOptionalExperienceContext } from '@geo/design-system';
import './login.css';

type LoginResult = 'ready' | 'invalid_input' | 'invalid_credentials' | 'unavailable';

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/u;
const OPERATIONS_HOME = '/platform/operations/';

export async function createOperationsSession(
  credentials: { email: string; password: string },
  request: typeof globalThis.fetch = globalThis.fetch,
): Promise<LoginResult> {
  const email = credentials.email.trim();
  if (
    email.length > 254 ||
    !EMAIL_PATTERN.test(email) ||
    credentials.password.length === 0 ||
    credentials.password.length > 256
  ) {
    return 'invalid_input';
  }
  try {
    const response = await request('/api/v2/identity/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ email, password: credentials.password }),
    });
    if (response.status === 200) return 'ready';
    if (response.status === 401) return 'invalid_credentials';
    return 'unavailable';
  } catch {
    return 'unavailable';
  }
}

const loginMessage: Record<Exclude<LoginResult, 'ready'>, string> = {
  invalid_input: '请输入有效邮箱和密码。',
  invalid_credentials: '邮箱或密码错误。',
  unavailable: '登录服务暂不可用，请稍后重试。',
};

export default function OperationsLoginRoute() {
  const experience = useOptionalExperienceContext();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setMessage(null);
    const result = await createOperationsSession({ email, password });
    setPassword('');
    if (result === 'ready') {
      window.location.replace(OPERATIONS_HOME);
      return;
    }
    setSubmitting(false);
    setMessage(loginMessage[result]);
  };

  return (
    <main className="operations-login-page">
      <a className="operations-login-brand" href={OPERATIONS_HOME}>
        <span aria-hidden="true">G</span>
        GEO Platform
      </a>
      <section className="operations-login-card" aria-labelledby="operations-login-title">
        <span className="operations-login-eyebrow">GEO Platform</span>
        <h1 id="operations-login-title">
          {experience?.source === 'live' ? '平台会话已建立' : 'GEO 平台登录'}
        </h1>
        {experience?.source === 'live' ? (
          <>
            <p>当前浏览器已经通过平台账号验证，可以进入运营工作台发起采集并处理执行任务。</p>
            <a className="operations-login-submit" href={OPERATIONS_HOME}>
              进入运营工作台
            </a>
          </>
        ) : (
          <>
            <p>
              使用平台账号邮箱登录，登录成功后进入运营工作台。账号密码只提交给同源认证接口，不写入浏览器存储。
            </p>
            <form onSubmit={(event) => void submit(event)}>
              <label htmlFor="operations-login-email">邮箱</label>
              <input
                id="operations-login-email"
                type="email"
                autoComplete="username"
                inputMode="email"
                maxLength={254}
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
              <label htmlFor="operations-login-password">密码</label>
              <input
                id="operations-login-password"
                type="password"
                autoComplete="current-password"
                maxLength={256}
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
              {message ? (
                <p className="operations-login-error" role="alert">
                  {message}
                </p>
              ) : null}
              <button className="operations-login-submit" type="submit" disabled={submitting}>
                {submitting ? '正在登录…' : '登录并进入运营工作台'}
              </button>
            </form>
          </>
        )}
        <a className="operations-login-public-link" href="/platform/operations/media-prices">
          无需登录，返回公开只读比价页
        </a>
      </section>
    </main>
  );
}
