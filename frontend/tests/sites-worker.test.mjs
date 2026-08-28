import assert from "node:assert/strict";
import { access } from "node:fs/promises";
import test from "node:test";
import worker from "../worker/index.js";
import { catalog, publicItems } from "../worker/catalog-data.js";

function createDatabase() {
  const sessions = new Map();
  const handoffs = new Map();
  const geminiUsage = new Map();
  return {
    sessions,
    handoffs,
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
          return null;
        },
        async run() {
          if (sql.startsWith("INSERT INTO chat_sessions")) sessions.set(values[0], values[1]);
          if (sql.startsWith("DELETE FROM chat_sessions")) sessions.delete(values[0]);
          if (sql.startsWith("INSERT INTO handoff_requests")) handoffs.set(values[0], values);
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
      body: JSON.stringify({ message: target.code, require_live: true }),
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

test("uses approved-domain web search only after internal grounding abstains", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = String(url);
    calls.push(endpoint);
    if (endpoint.includes(":embedContent")) {
      return Response.json({ embedding: { values: Array(128).fill(0.1) } });
    }
    if (endpoint.includes(":generateContent")) {
      return Response.json({
        responseId: "gemini-web-fallback",
        candidates: [{ content: { parts: [{ text: JSON.stringify({
          answer: "모델 자체 답변", kind: "text", domain: "test",
          sub_intent: "get_test_detail", requested_action: "explain",
          matched_test_index: null, matched_public_index: null, needs_clarification: false,
          requires_authentication: false, needs_handoff: false, medical_review_required: false,
        }) }] }, finishReason: "STOP" }],
      });
    }
    if (endpoint === "https://api.openai.com/v1/responses") {
      const request = JSON.parse(options.body);
      assert.ok(request.tools[0].filters.allowed_domains.includes("pubmed.ncbi.nlm.nih.gov"));
      assert.ok(!request.tools[0].filters.allowed_domains.includes("scllab.co.kr"));
      return Response.json({
        output: [{ type: "message", content: [{
          type: "output_text",
          text: "PCR은 특정 DNA 구간을 증폭하는 방법입니다.",
          annotations: [{
            type: "url_citation", title: "PubMed", url: "https://pubmed.ncbi.nlm.nih.gov/12345",
          }],
        }] }],
      });
    }
    throw new Error(`Unexpected fetch: ${endpoint}`);
  };
  try {
    const response = await worker.fetch(new Request("https://example.test/api/chat", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: "베이즈 정리의 사전확률과 사후확률 차이를 설명해줘", require_live: true }),
    }), {
      DB: database,
      GEMINI_API_KEY: "test-key",
      GEMINI_EMBEDDING_DIMENSIONS: "128",
      OPENAI_API_KEY: "web-key",
      EXTERNAL_WEB_SEARCH_ENABLED: "true",
    });
    const body = await response.json();

    assert.equal(response.status, 200);
    assert.equal(body.reply.data_status, "approved_external");
    assert.equal(body.reply.citations[0].source_tier, "approved_external");
    assert.equal(body.reply.grounding_status, "grounded_external");
    assert.doesNotMatch(body.reply.text, /승인된/);
    assert.match(body.reply.text, /근거자료 링크/);
    assert.ok(calls.includes("https://api.openai.com/v1/responses"));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects operational web answers outside the SCL domain", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = String(url);
    if (endpoint.includes(":embedContent")) {
      return Response.json({ embedding: { values: Array(128).fill(0.1) } });
    }
    if (endpoint.includes(":generateContent")) {
      return Response.json({
        candidates: [{ content: { parts: [{ text: JSON.stringify({
          answer: "모델 자체 답변", kind: "text", domain: "test",
          sub_intent: "get_test_detail", requested_action: "explain",
          matched_test_index: null, matched_public_index: null, needs_clarification: false,
          requires_authentication: false, needs_handoff: false, medical_review_required: false,
        }) }] }, finishReason: "STOP" }],
      });
    }
    if (endpoint === "https://api.openai.com/v1/responses") {
      const request = JSON.parse(options.body);
      assert.deepEqual(request.tools[0].filters.allowed_domains, ["scllab.co.kr"]);
      return Response.json({
        output: [{ type: "message", content: [{
          type: "output_text",
          text: "검체는 혈청이고 소요일은 하루입니다.",
          annotations: [{
            type: "url_citation", title: "위조 출처", url: "https://scllab.co.kr.evil.example/claim",
          }],
        }] }],
      });
    }
    throw new Error(`Unexpected fetch: ${endpoint}`);
  };
  try {
    const response = await worker.fetch(new Request("https://example.test/api/chat", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: "SCL ZZZ-999 검사 용기와 소요일", require_live: true }),
    }), {
      DB: database,
      GEMINI_API_KEY: "test-key",
      GEMINI_EMBEDDING_DIMENSIONS: "128",
      OPENAI_API_KEY: "web-key",
      EXTERNAL_WEB_SEARCH_ENABLED: "true",
    });
    const body = await response.json();

    assert.equal(response.status, 200);
    assert.equal(body.reply.data_status, "no_source");
    assert.equal(body.reply.grounding_status, "abstained");
    assert.doesNotMatch(body.reply.text, /혈청|하루/);
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

test("emits the files required by Sites packaging", async () => {
  await access(new URL("../dist/client/index.html", import.meta.url));
  await access(new URL("../dist/server/index.js", import.meta.url));
  await access(new URL("../dist/server/catalog-data.js", import.meta.url));
  await access(new URL("../dist/.openai/hosting.json", import.meta.url));
  await access(new URL("../dist/.openai/drizzle/0000_real_thunderball.sql", import.meta.url));
});
