import assert from "node:assert/strict";
import { access } from "node:fs/promises";
import test from "node:test";
import worker from "../worker/index.js";
import { catalog, publicItems } from "../worker/catalog-data.js";
import { clearSupabaseCatalogCache } from "../worker/supabase-catalog.js";

function createDatabase() {
  const sessions = new Map();
  const handoffs = new Map();
  const feedback = new Map();
  const faqCandidates = new Map();
  const geminiUsage = new Map();
  return {
    sessions,
    handoffs,
    feedback,
    faqCandidates,
    geminiUsage,
    prepare(sql) {
      let values = [];
      return {
        bind(...bound) { values = bound; return this; },
        async first() {
          if (sql.startsWith("SELECT last_test_json")) {
            const lastTest = sessions.get(values[0]);
            return lastTest ? { last_test_json: lastTest } : null;
          }
          if (sql.startsWith("SELECT id, occurrence_count FROM faq_candidates")) {
            return faqCandidates.get(values[0]) || null;
          }
          return null;
        },
        async run() {
          if (sql.startsWith("INSERT INTO chat_sessions")) sessions.set(values[0], values[1]);
          if (sql.startsWith("DELETE FROM chat_sessions")) sessions.delete(values[0]);
          if (sql.startsWith("INSERT INTO handoff_requests")) handoffs.set(values[0], values);
          if (sql.startsWith("INSERT INTO faq_candidates")) {
            const existing = faqCandidates.get(values[0]);
            faqCandidates.set(values[0], existing
              ? { ...existing, occurrence_count: existing.occurrence_count + 1 }
              : { id: faqCandidates.size + 1, occurrence_count: 1 });
          }
          if (sql.startsWith("INSERT INTO chat_feedback")) {
            const id = feedback.size + 1;
            feedback.set(id, values);
            return { success: true, meta: { changes: 1, last_row_id: id } };
          }
          if (sql.startsWith("INSERT OR IGNORE INTO gemini_daily_usage") && !geminiUsage.has(values[0])) {
            geminiUsage.set(values[0], 0);
          }
          if (sql.startsWith("UPDATE gemini_daily_usage")) {
            const [day, limit] = values;
            const calls = geminiUsage.get(day) || 0;
            if (calls >= limit) return { success: true, meta: { changes: 0 } };
            geminiUsage.set(day, calls + 1);
          }
          return { success: true, meta: { changes: 1 } };
        },
      };
    },
    async batch(statements) {
      for (const statement of statements) await statement.run();
      return statements.map(() => ({ success: true }));
    },
  };
}

const database = createDatabase();
const apiEnv = { DB: database };

test("serves existing static assets without a fallback", async () => {
  const calls = [];
  const response = await worker.fetch(new Request("https://example.test/assets/app.js"), {
    ASSETS: {
      fetch: async (request) => {
        calls.push(new URL(request.url).pathname);
        return new Response("asset", { status: 200 });
      },
    },
  });

  assert.equal(response.status, 200);
  assert.deepEqual(calls, ["/assets/app.js"]);
});

test("falls back to index.html for an unknown app route", async () => {
  const calls = [];
  const response = await worker.fetch(
    new Request("https://example.test/flow/step-two?source=share", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async (request) => {
          const url = new URL(request.url);
          calls.push(url.pathname + url.search);
          return new Response(url.pathname === "/index.html" ? "app" : "missing", {
            status: url.pathname === "/index.html" ? 200 : 404,
          });
        },
      },
    },
  );

  assert.equal(response.status, 200);
  assert.deepEqual(calls, ["/flow/step-two?source=share", "/index.html"]);
});

test("does not turn missing API or write requests into the app shell", async () => {
  for (const [request, expectedAssetCalls] of [
    [new Request("https://example.test/api/missing", { headers: { accept: "application/json" } }), 0],
    [new Request("https://example.test/flow", { method: "POST", headers: { accept: "text/html" } }), 1],
  ]) {
    let calls = 0;
    const response = await worker.fetch(request, {
      ASSETS: {
        fetch: async () => {
          calls += 1;
          return new Response("missing", { status: 404 });
        },
      },
    });

    assert.equal(response.status, 404);
    assert.equal(calls, expectedAssetCalls);
  }
});

test("reports the hosted backend health and catalog snapshot", async () => {
  const health = await worker.fetch(new Request("https://example.test/health"), apiEnv);
  const healthBody = await health.json();
  assert.equal(health.status, 200);
  assert.equal(healthBody.mode, "demo_fallback");
  assert.equal(healthBody.rag_enabled, true);

  const status = await worker.fetch(new Request("https://example.test/api/catalog/status"), apiEnv);
  const statusBody = await status.json();
  assert.equal(statusBody.variants, catalog.length);
  assert.ok(statusBody.tests > 2000);
});

test("answers catalog questions and keeps the existing response contract", async () => {
  const target = catalog[0];
  const response = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message: target.code, require_live: false }),
  }), apiEnv);
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.mode, "demo_fallback");
  assert.equal(body.reply.kind, "test");
  assert.equal(body.reply.test.code, target.code);
  assert.ok(database.sessions.has(body.session_id));
});

test("uses the live Supabase catalog when the Worker credentials are configured", async () => {
  const originalFetch = globalThis.fetch;
  const liveTest = {
    code: "LIVE-001", variant_key: "live-row", name: "Supabase 실시간 검사", aliases: [],
    specimen: "혈청", container: "SST", method: "PCR", schedule: "월-금", tat: "1일",
    source_title: "SCL 검사항목조회", source_url: "https://www.scllab.co.kr/test/live",
    updated_at: "2026-09-16", demo: false, public_details: {}, score: 1000,
  };
  clearSupabaseCatalogCache();
  globalThis.fetch = async (url, options) => {
    assert.equal(String(url), "https://project.supabase.co/rest/v1/rpc/scl_search_catalog");
    assert.equal(options.method, "POST");
    assert.equal(options.headers.apikey, "publishable-key");
    assert.deepEqual(JSON.parse(options.body), { p_query: "LIVE-001", p_test_limit: 8, p_public_limit: 6 });
    return Response.json({ tests: [liveTest], public_items: [] });
  };
  try {
    const response = await worker.fetch(new Request("https://example.test/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: "LIVE-001", require_live: false }),
    }), { DB: database, SUPABASE_URL: "https://project.supabase.co", SUPABASE_PUBLISHABLE_KEY: "publishable-key" });
    const body = await response.json();

    assert.equal(response.status, 200);
    assert.equal(body.catalog_source, "supabase");
    assert.equal(body.reply.kind, "test");
    assert.equal(body.reply.test.code, "LIVE-001");
  } finally {
    globalThis.fetch = originalFetch;
    clearSupabaseCatalogCache();
  }
});

test("uses exact SCL-code matches before keyword ranking", async () => {
  const target = catalog[0];
  const response = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message: `${target.code} 코드를 가진 검사가 있나?`, require_live: false }),
  }), apiEnv);
  const body = await response.json();

  assert.equal(body.reply.kind, "test");
  assert.equal(body.reply.test.code, target.code);
});

test("computes requested-field coverage before returning a test card", async () => {
  const target = catalog.find((item) => !item.container && item.tat && item.method);
  assert.ok(target);
  const response = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message: `${target.code} 검사 용기와 소요일`, require_live: false }),
  }), apiEnv);
  const body = await response.json();

  assert.equal(body.reply.kind, "test");
  assert.match(body.reply.text, /요청하신 정보/);
  assert.match(body.reply.text, /확인하지 못한 항목/);
  assert.match(body.reply.text, /소요일:/);
  assert.equal(body.reply.answerability, "partial");
  assert.equal(body.reply.claim_coverage, 0.5);
});

test("recognizes mixed-format five-character SCL codes", async () => {
  const target = catalog.find((item) => item.code === "R0329");
  assert.ok(target);
  const response = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message: "R0329로 검사 찾아줘", require_live: false }),
  }), apiEnv);
  const body = await response.json();

  assert.equal(body.reply.kind, "test");
  assert.equal(body.reply.test.code, "R0329");
});

test("unknown identifiers never fall through to unrelated keyword candidates", async () => {
  const response = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message: "X999999ZZ 코드를 가진 검사가 있나?", require_live: false }),
  }), apiEnv);
  const body = await response.json();

  assert.equal(body.reply.kind, "text");
  assert.equal(body.reply.answerability, "none");
  assert.equal(body.reply.test, null);
  assert.deepEqual(body.reply.choices, []);
});

test("the packaged catalog resolves D185000HZ only to its ALT billing-code matches", async () => {
  const response = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message: "D185000HZ 코드를 가진 검사가 있나?", require_live: false }),
  }), apiEnv);
  const body = await response.json();

  assert.equal(body.reply.kind, "choices");
  assert.equal(body.reply.choices.length, 2);
  assert.ok(body.reply.choices.every((choice) => choice.includes("ALT")));
  assert.ok(body.reply.choices.some((choice) => choice.includes("검사코드 10130")));
  assert.ok(body.reply.choices.some((choice) => choice.includes("검사코드 10135")));
  assert.ok(body.reply.choices.every((choice) => !choice.includes("40500")));
});

test("filters remembered code candidates by field and carries the term into a short follow-up", async () => {
  const sessionId = "field-followup-session";
  const first = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: "11380 코드 검사 찾아줘", require_live: false }),
  }), apiEnv);
  assert.equal((await first.json()).reply.kind, "choices");

  const correction = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: "검사명에 random 들어간거?", require_live: false }),
  }), apiEnv);
  const correctionBody = await correction.json();
  assert.equal(correctionBody.reply.kind, "test");
  assert.equal(correctionBody.reply.test.variant_key, "11380:400");
  assert.match(correctionBody.reply.text, /검사명에는 'random'.*검체명에는 포함/);

  const followup = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: "검체명에?", require_live: false }),
  }), apiEnv);
  const followupBody = await followup.json();
  assert.equal(followupBody.reply.kind, "test");
  assert.equal(followupBody.reply.test.variant_key, "11380:400");
  assert.match(followupBody.reply.text, /검체명에 'random'.*포함/);
});

test("understands implicit specimen filters and ordinal references within remembered candidates", async () => {
  const sessionId = "implicit-followup-session";
  await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: "11380 코드 검사 찾아줘", require_live: false }),
  }), apiEnv);

  const serum = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: "serum인거", require_live: false }),
  }), apiEnv);
  assert.equal((await serum.json()).reply.test.variant_key, "11380:100");

  const second = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: "두 번째 보여줘", require_live: false }),
  }), apiEnv);
  assert.equal((await second.json()).reply.test.variant_key, "11380:100");
});

test("falls back from a missing SCL code to an exact billing-code match", async () => {
  const originalFetch = globalThis.fetch;
  const billingTest = {
    code: "10130", variant_key: "10130:100", name: "ALT", aliases: [], specimen: "Serum",
    container: null, method: "IFCC", schedule: "월~토", tat: "1일",
    source_title: "SCL 검사항목조회", source_url: "https://www.scllab.co.kr/test/10130",
    updated_at: "2026-09-16", demo: false, public_details: {}, billing_codes: ["D185000HZ"],
    matched_code: "D185000HZ", matched_code_type: "billing", score: 900,
  };
  clearSupabaseCatalogCache();
  globalThis.fetch = async () => Response.json({ tests: [billingTest], public_items: [], identifier_query: true });
  try {
    const response = await worker.fetch(new Request("https://example.test/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: "D185000HZ 코드를 가진 검사가 있나?", require_live: false }),
    }), { DB: database, SUPABASE_URL: "https://project.supabase.co", SUPABASE_PUBLISHABLE_KEY: "publishable-key" });
    const body = await response.json();

    assert.equal(body.reply.kind, "test");
    assert.equal(body.reply.test.code, "10130");
    assert.equal(body.reply.test.matched_code_type, "billing");
  } finally {
    globalThis.fetch = originalFetch;
    clearSupabaseCatalogCache();
  }
});

test("falls back to the packaged snapshot when Supabase is temporarily unavailable", async () => {
  const originalFetch = globalThis.fetch;
  const target = catalog[0];
  clearSupabaseCatalogCache();
  globalThis.fetch = async () => new Response("unavailable", { status: 503 });
  try {
    const response = await worker.fetch(new Request("https://example.test/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: target.code, require_live: false }),
    }), { DB: database, SUPABASE_URL: "https://project.supabase.co", SUPABASE_PUBLISHABLE_KEY: "publishable-key" });
    const body = await response.json();

    assert.equal(response.status, 200);
    assert.equal(body.catalog_source, "snapshot");
    assert.equal(body.reply.test.code, target.code);
  } finally {
    globalThis.fetch = originalFetch;
    clearSupabaseCatalogCache();
  }
});

test("uses Gemini structured generation while preserving the response contract", async () => {
  const target = catalog[0];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const endpoint = String(url);
    if (endpoint.includes(":embedContent")) {
      return Response.json({ embedding: { values: Array(128).fill(0.1) } });
    }
    if (endpoint.includes(":generateContent")) {
      return Response.json({
        responseId: "gemini-test-response",
        candidates: [{ content: { parts: [{ text: JSON.stringify({
          answer: "검사 정보를 안내합니다.",
          kind: "test",
          domain: "test",
          sub_intent: "get_test_detail",
          requested_action: "explain",
          matched_test_index: 0,
          matched_public_index: null,
          needs_clarification: false,
          requires_authentication: false,
          needs_handoff: false,
          medical_review_required: false,
        }) }] }, finishReason: "STOP" }],
      });
    }
    throw new Error(`Unexpected fetch: ${endpoint}`);
  };
  try {
    const response = await worker.fetch(new Request("https://example.test/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: target.name, require_live: true }),
    }), {
      DB: database,
      GEMINI_API_KEY: "test-key",
      GEMINI_MODEL: "gemini-3.1-flash-lite",
      GEMINI_EMBEDDING_MODEL: "gemini-embedding-2",
      GEMINI_EMBEDDING_DIMENSIONS: "128",
      GEMINI_DAILY_REQUEST_LIMIT: "100",
    });
    const body = await response.json();
    assert.equal(response.status, 200);
    assert.equal(body.mode, "gemini");
    assert.equal(body.response_id, "gemini-test-response");
    assert.equal(body.reply.test.code, target.code);
    assert.equal(body.reply.grounding_status, "grounded_internal");
    assert.notEqual(body.reply.text, "검사 정보를 안내합니다.");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("discards an ungrounded Gemini answer when no source was selected", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes(":embedContent")) {
      return Response.json({ embedding: { values: Array(128).fill(0.1) } });
    }
    return Response.json({
      candidates: [{ content: { parts: [{ text: JSON.stringify({
        answer: "근거 없이 특정 검체와 소요일을 만들어 냅니다.",
        kind: "text", domain: "test", sub_intent: "get_test_detail", requested_action: "explain",
        matched_test_index: null, matched_public_index: null, needs_clarification: false,
        requires_authentication: false, needs_handoff: false, medical_review_required: false,
      }) }] }, finishReason: "STOP" }],
    });
  };
  try {
    const response = await worker.fetch(new Request("https://example.test/api/chat", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: "SCL ZZZ-999 가상검사 상세", require_live: true }),
    }), { DB: database, GEMINI_API_KEY: "test-key", GEMINI_EMBEDDING_DIMENSIONS: "128" });
    const body = await response.json();

    assert.equal(response.status, 200);
    assert.equal(body.reply.data_status, "no_source");
    assert.equal(body.reply.answerability, "none");
    assert.doesNotMatch(body.reply.text, /특정 검체와 소요일/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("grounds a Gemini document answer even when the model omits the source index", async () => {
  const target = publicItems.find((item) => item.entity_type === "document" && item.vector);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes(":embedContent")) {
      return Response.json({ embedding: { values: Array(128).fill(0.1) } });
    }
    return Response.json({
      candidates: [{ content: { parts: [{ text: JSON.stringify({
        answer: "자료를 찾지 못했습니다.", kind: "text", domain: "support",
        sub_intent: "search_schedule_notice", requested_action: "search",
        matched_test_index: null, matched_public_index: null,
        needs_clarification: false, requires_authentication: false,
        needs_handoff: false, medical_review_required: false,
      }) }] }, finishReason: "STOP" }],
    });
  };
  try {
    const response = await worker.fetch(new Request("https://example.test/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: target.title, require_live: true }),
    }), { DB: database, GEMINI_API_KEY: "test-key", GEMINI_EMBEDDING_DIMENSIONS: "128" });
    const body = await response.json();
    assert.equal(response.status, 200);
    assert.equal(body.mode, "gemini");
    assert.ok(body.reply.citations.length > 0);
    assert.notEqual(body.reply.text, "자료를 찾지 못했습니다.");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("routes personal results without accepting credentials", async () => {
  const chat = await worker.fetch(new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message: "내 검사결과 조회해줘", require_live: false }),
  }), apiEnv);
  const chatBody = await chat.json();
  assert.equal(chatBody.reply.kind, "result_auth_form");

  const auth = await worker.fetch(new Request("https://example.test/api/results/authenticate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ user_id: "demo", password: "secret" }),
  }), apiEnv);
  assert.equal(auth.status, 503);
});

test("encrypts and persists consented handoff requests", async () => {
  const secret = Buffer.alloc(32, 7).toString("base64");
  const response = await worker.fetch(new Request("https://example.test/api/handoff", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      session_id: "session-test",
      inquiry_type: "general",
      requester_name: "홍길동",
      phone: "010-1234-5678",
      organization: "테스트 기관",
      content: "상담을 요청합니다.",
      related_refs: [],
      consent: true,
    }),
  }), { DB: database, HANDOFF_ENCRYPTION_KEY: secret });
  const body = await response.json();
  const stored = database.handoffs.get(body.public_id);

  assert.equal(response.status, 201);
  assert.equal(body.status, "submitted");
  assert.ok(stored);
  assert.notEqual(stored[3], "홍길동");
  assert.notEqual(stored[4], "010-1234-5678");
});

test("persists answer feedback and merges the FAQ candidate bucket", async () => {
  const payload = {
    session_id: "feedback-session",
    response_id: "gemini-response-1",
    rating: "not_helpful",
    reason: "missing_information",
    comment: "전화번호 010-9999-8888은 저장하지 마세요.",
    question: "HPV 검사 용기와 소요일",
    answer: "검사 정보를 안내합니다.",
    domain: "test",
    sub_intent: "get_test_detail",
    source_refs: ["test:16290:510", "https://unsafe.example"],
  };
  const first = await worker.fetch(new Request("https://example.test/api/feedback", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload),
  }), apiEnv);
  const second = await worker.fetch(new Request("https://example.test/api/feedback", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload),
  }), apiEnv);
  const secondBody = await second.json();

  assert.equal(first.status, 201);
  assert.equal(second.status, 201);
  assert.equal(secondBody.merged_occurrences, 2);
  assert.equal(database.feedback.size, 2);
  const latest = database.feedback.get(2);
  assert.doesNotMatch(latest[4], /010-9999-8888/);
  assert.deepEqual(JSON.parse(latest[10]), ["test:16290:510"]);
});

test("emits the files required by Sites packaging", async () => {
  await access(new URL("../dist/client/index.html", import.meta.url));
  await access(new URL("../dist/server/index.js", import.meta.url));
  await access(new URL("../dist/server/catalog-data.js", import.meta.url));
  await access(new URL("../dist/.openai/hosting.json", import.meta.url));
  await access(new URL("../dist/.openai/drizzle/0000_real_thunderball.sql", import.meta.url));
});
