import { DatabaseSync } from 'node:sqlite';

export function loadLegacySessionCookie(databasePath, baseURL) {
  if (!databasePath) throw new Error('S04_LEGACY_SESSION_DB is required');
  const database = new DatabaseSync(databasePath, { readOnly: true });
  let row;
  try {
    row = database
      .prepare(
        `SELECT s.token,m.role
         FROM session s
         JOIN membership m ON m.user_id=s.user_id AND m.tenant_id=s.tenant_id
         WHERE s.expires_at >= datetime('now')
         ORDER BY s.expires_at DESC,s.id DESC
         LIMIT 1`,
      )
      .get();
  } finally {
    database.close();
  }
  if (!row || typeof row.token !== 'string') {
    throw new Error('No active legacy production session is available');
  }
  return {
    cookie: {
      name: 'session',
      value: row.token,
      domain: new URL(baseURL).hostname,
      path: '/',
      httpOnly: true,
      secure: true,
      sameSite: 'Lax',
    },
    legacyRole: String(row.role),
  };
}

// native_session 模式（2026-08-03 起生产启用）下，legacy `session` cookie 不再被
// 前端 bootstrap 识别。验收方需改为注入 `__Host-geo_session`（平台 browser_session
// 表里的 sha256(token_hash) 会话）。token 由带库权限的部署脚本铸好后经
// S04_NATIVE_SESSION_TOKEN 传入；`__Host-` 前缀要求 secure + path=/ + 无 domain，
// 用 url 形态声明 host-only cookie。
export function loadNativeSessionCookie(token, baseURL, role = 'operator') {
  if (typeof token !== 'string' || token.length < 32 || token.length > 256) {
    throw new Error('S04_NATIVE_SESSION_TOKEN is invalid');
  }
  return {
    cookie: {
      name: '__Host-geo_session',
      value: token,
      url: baseURL,
      httpOnly: true,
      secure: true,
      sameSite: 'Lax',
    },
    legacyRole: String(role),
  };
}
