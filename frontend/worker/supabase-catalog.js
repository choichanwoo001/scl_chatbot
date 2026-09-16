const DEFAULT_TIMEOUT_MS = 5000;
const DEFAULT_CACHE_TTL_MS = 60_000;
const MAX_CACHE_ENTRIES = 200;

const searchCache = new Map();

function configured(env) {
  return Boolean(env.SUPABASE_URL && (env.SUPABASE_PUBLISHABLE_KEY || env.SUPABASE_ANON_KEY));
}

function endpoint(env, functionName) {
  const url = new URL(env.SUPABASE_URL);
  if (url.protocol !== "https:") throw new Error("SUPABASE_URL must use HTTPS");
  url.pathname = `/rest/v1/rpc/${functionName}`;
  url.search = "";
  return url;
}

function headers(env) {
  const key = env.SUPABASE_PUBLISHABLE_KEY || env.SUPABASE_ANON_KEY;
  return {
    apikey: key,
    authorization: `Bearer ${key}`,
    "content-type": "application/json",
  };
}

async function callRpc(env, functionName, body) {
  const timeout = Math.max(500, Number(env.SUPABASE_TIMEOUT_MS || DEFAULT_TIMEOUT_MS));
  const response = await fetch(endpoint(env, functionName), {
    method: "POST",
    headers: headers(env),
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeout),
  });
  if (!response.ok) {
    const requestId = response.headers.get("x-request-id");
    throw new Error(`Supabase RPC ${functionName} failed (${response.status}${requestId ? `, ${requestId}` : ""})`);
  }
  return response.json();
}

function boundedArray(value, maxLength) {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object").slice(0, maxLength) : [];
}

function remember(key, value, expiresAt) {
  if (searchCache.size >= MAX_CACHE_ENTRIES) searchCache.delete(searchCache.keys().next().value);
  searchCache.set(key, { value, expiresAt });
}

export function hasSupabaseCatalog(env) {
  return configured(env);
}

export async function searchSupabaseCatalog(env, query, testLimit = 8, publicLimit = 6) {
  if (!configured(env)) return null;
  const normalizedQuery = String(query || "").normalize("NFKC").replace(/\s+/g, " ").trim();
  if (!normalizedQuery) return { tests: [], publicItems: [], source: "supabase" };

  const safeTestLimit = Math.min(12, Math.max(1, Number(testLimit) || 8));
  const safePublicLimit = Math.min(12, Math.max(1, Number(publicLimit) || 6));
  const cacheKey = `${normalizedQuery}\u0000${safeTestLimit}\u0000${safePublicLimit}`;
  const cached = searchCache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  if (cached) searchCache.delete(cacheKey);

  const payload = await callRpc(env, "scl_search_catalog", {
    p_query: normalizedQuery,
    p_test_limit: safeTestLimit,
    p_public_limit: safePublicLimit,
  });
  const result = {
    tests: boundedArray(payload?.tests, safeTestLimit).map((item) => ({ item, score: Number(item.score || 0), lexicalScore: Number(item.score || 0) })),
    publicItems: boundedArray(payload?.public_items, safePublicLimit).map((item) => ({ item, score: Number(item.score || 0), lexicalScore: Number(item.score || 0) })),
    source: "supabase",
  };
  const ttl = Math.max(0, Number(env.SUPABASE_CACHE_TTL_SECONDS ?? DEFAULT_CACHE_TTL_MS / 1000)) * 1000;
  if (ttl > 0) remember(cacheKey, result, Date.now() + ttl);
  return result;
}

export async function readSupabaseCatalogStatus(env) {
  if (!configured(env)) return null;
  const payload = await callRpc(env, "scl_catalog_status", {});
  return payload && typeof payload === "object" ? { ...payload, source: "SUPABASE_LIVE" } : null;
}

export function clearSupabaseCatalogCache() {
  searchCache.clear();
}
