import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import worker from "../worker/index.js";
import { readSession, saveSession } from "../worker/session-store.js";

function migratedDatabase() {
  const db = new DatabaseSync(":memory:");
  for (const file of ["0000_real_thunderball.sql", "0001_dapper_ben_grimm.sql"]) {
    db.exec(readFileSync(new URL(`../drizzle/${file}`, import.meta.url), "utf8"));
  }
  return { db, prepare(sql) {
    const statement = db.prepare(sql);
    let parameters = [];
    return {
      bind(...values) { parameters = values; return this; },
      async first() { return statement.get(...parameters); },
      async run() { const result = statement.run(...parameters); return { meta: { changes: result.changes } }; },
    };
  } };
}

test("sessions use migrated schema and expire without runtime DDL", async () => {
  const database = migratedDatabase();
  try {
    const env = { DB: database, SESSION_TTL_SECONDS: "60" };
    await saveSession(env, "active", { code: "12345" });
    assert.deepEqual(await readSession(env, "active"), { code: "12345" });
    database.db.prepare("UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?")
      .run("2000-01-01T00:00:00.000Z", "active");
    assert.equal(await readSession(env, "active"), null);
    await saveSession(env, "new", { code: "67890" });
    assert.equal(database.db.prepare("SELECT count(*) AS count FROM chat_sessions").get().count, 1);
  } finally { database.db.close(); }
});

test("configured backend owns API responses and receives the exact payload", async () => {
  const original = globalThis.fetch;
  const body = JSON.stringify({ message: "HPV", require_live: true });
  globalThis.fetch = async (url, options) => {
    assert.equal(String(url), "https://backend.example/api/chat");
    assert.equal(new TextDecoder().decode(options.body), body);
    assert.equal(options.headers.get("cookie"), null);
    return Response.json({ detail: "Live model unavailable" }, { status: 503 });
  };
  try {
    const response = await worker.fetch(new Request("https://site.example/api/chat", {
      method: "POST", headers: { "content-type": "application/json", cookie: "private=value" }, body,
    }), { BACKEND_API_URL: "https://backend.example" });
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { detail: "Live model unavailable" });
    assert.equal(response.headers.get("cache-control"), "no-store");
  } finally { globalThis.fetch = original; }
});

test("backend errors do not silently switch to snapshot answers", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("offline"); };
  try {
    const response = await worker.fetch(new Request("https://site.example/api/chat", {
      method: "POST", body: JSON.stringify({ message: "HPV" }),
    }), { BACKEND_API_URL: "https://backend.example" });
    assert.equal(response.status, 503);
    assert.equal((await response.json()).reply, undefined);
  } finally { globalThis.fetch = original; }
});

const contracts = JSON.parse(readFileSync(new URL("../../data/evals/api_contracts.json", import.meta.url), "utf8"));
for (const fixture of contracts) {
  test(`standalone API contract: ${fixture.name}`, async () => {
    const database = migratedDatabase();
    try {
      const response = await worker.fetch(new Request("https://site.example/api/chat", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(fixture.request),
      }), { DB: database });
      assert.equal(response.status, fixture.status);
      const body = await response.json();
      for (const [path, expected] of Object.entries(fixture.fields)) {
        assert.equal(path.split(".").reduce((value, key) => value[key], body), expected);
      }
    } finally { database.db.close(); }
  });
}
