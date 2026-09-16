// Schema is owned by the checked-in Drizzle migrations, applied before deployment.
export function ensureDatabase(env) {
  if (!env.DB) throw new Error("D1 binding DB is not configured");
}

function sessionCutoff(env) {
  const ttl = Number(env.SESSION_TTL_SECONDS || 1800);
  if (!Number.isInteger(ttl) || ttl < 1) throw new Error("Invalid session TTL");
  return new Date(Date.now() - ttl * 1000).toISOString();
}

export async function readSession(env, sessionId) {
  await ensureDatabase(env);
  const row = await env.DB.prepare("SELECT last_test_json FROM chat_sessions WHERE session_id = ? AND updated_at > ?").bind(sessionId, sessionCutoff(env)).first();
  if (!row?.last_test_json) return null;
  try { return JSON.parse(row.last_test_json); } catch { return null; }
}

export async function saveSession(env, sessionId, lastTest) {
  await ensureDatabase(env);
  const now = new Date().toISOString();
  await env.DB.prepare("DELETE FROM chat_sessions WHERE updated_at <= ?").bind(sessionCutoff(env)).run();
  await env.DB.prepare(`INSERT INTO chat_sessions (session_id, last_test_json, created_at, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(session_id) DO UPDATE SET last_test_json = excluded.last_test_json, updated_at = excluded.updated_at`)
    .bind(sessionId, lastTest ? JSON.stringify(lastTest) : null, now, now).run();
  const capacity = Number(env.SESSION_MAX_ENTRIES || 10000);
  if (!Number.isInteger(capacity) || capacity < 1) throw new Error("Invalid session capacity");
  await env.DB.prepare(`DELETE FROM chat_sessions WHERE session_id IN (
    SELECT session_id FROM chat_sessions ORDER BY updated_at DESC, session_id LIMIT -1 OFFSET ?
  )`).bind(capacity).run();
}

