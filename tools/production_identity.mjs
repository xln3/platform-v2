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
