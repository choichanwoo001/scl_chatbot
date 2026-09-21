import { ensureDatabase, readSession, saveSession } from "./session-store.js";
import { relayBackend } from "./backend-proxy.js";
import { catalog, publicItems } from "./catalog-data.js";
import { hasSupabaseCatalog, readSupabaseCatalogStatus, searchSupabaseCatalog } from "./supabase-catalog.js";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const INQUIRY_TYPES = new Set(["test_request", "specimen_shipping", "result_issue", "complaint", "business", "general"]);
const SAFE_REF = /^(?:test:[A-Za-z0-9:_-]+|document:\d+|container:\d+|preservative:\d+|location:\d+|route:\d+|taxonomy:\d+|attachment:\d+|faq:\d+)$/;
const PHONE = /^(?:\+?82[- ]?)?0?1(?:0|1|6|7|8|9)[- ]?\d{3,4}[- ]?\d{4}$/;
const PROMPT_INJECTION = /((이전|앞의).*(지시|규칙).*(무시|잊어)|ignore\s+(all\s+)?previous|system\s*prompt|시스템\s*프롬프트|내부\s*(지침|규칙)|개발자\s*메시지|jailbreak)/i;
const RESIDENT_ID = /(?<!\d)\d{6}\s*-\s*[1-4]\d{6}(?!\d)/g;
const PHONE_IN_TEXT = /(?<!\d)01[016789][\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)/g;
const EMAIL = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi;
const RESULT_INTENT = /(내|개인|본인).{0,12}(검사)?결과|검사결과.{0,10}(조회|확인|보여)/i;
const HANDOFF_INTENT = /상담(원)?.{0,10}(연결|신청|접수)|사람.{0,8}연결/i;
const MEDICAL_REVIEW_INTENT = /(진단|치료|복약|약물|정상|비정상|수치.{0,8}해석|결과.{0,8}해석|위험도)/i;
const FOLLOWUP_INTENT = /(그거|그 검사|그 항목|해당 검사|앞의 검사).*(용기|검체|소요일|방법|언제|일정|며칠)/i;
const STOP_WORDS = new Set(["검사", "알려", "주세요", "궁금", "대한", "관련", "정보"]);
const CODE_PATTERN = /(?<![A-Za-z0-9])(?:[A-Za-z]\d{6}[A-Za-z]{2}|[A-Za-z]\d{4}|\d[A-Za-z]\d{3}|\d{5})(?![A-Za-z0-9])/gi;
const FILTER_TOKEN_STOP_WORDS = new Set(["scl", "code", "test", "name", "specimen", "method"]);
class GeminiDailyLimitError extends Error {}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function detail(message, status) {
  return json({ detail: message }, status);
}

function normalize(value) {
  return String(value || "").normalize("NFKC").replace(/[^0-9a-zA-Z가-힣]+/g, " ").trim().toLocaleLowerCase("ko-KR");
}

function inspectInput(value) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim().slice(0, 500);
  const displayed = normalized
    .replace(RESIDENT_ID, "[주민등록번호 가림]")
    .replace(PHONE_IN_TEXT, "[전화번호 가림]")
    .replace(EMAIL, "[이메일 가림]");
  if (PROMPT_INJECTION.test(normalized)) return { action: "block", displayed, modelInput: displayed };
  return { action: displayed === normalized ? "allow" : "redact", displayed, modelInput: displayed };
}

function safeUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && (url.hostname === "scllab.co.kr" || url.hostname.endsWith(".scllab.co.kr"))
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

function termsFor(query) {
  return normalize(query).split(" ").filter((term) => term.length > 1 && !STOP_WORDS.has(term));
}

function codeCandidates(query) {
  return [...new Set((String(query || "").normalize("NFKC").match(CODE_PATTERN) || []).map((code) => code.toUpperCase()))];
}

function candidateFilter(query, previousFilter = null) {
  const text = String(query || "").normalize("NFKC").trim();
  let field = null;
  if (/(?:검사\s*명|검사\s*이름)/i.test(text)) field = "name";
  else if (/(?:검체\s*명|검체)/i.test(text)) field = "specimen";
  else if (/(?:용기\s*명|용기)/i.test(text)) field = "container";
  else if (/(?:검사\s*방법|방법)/i.test(text)) field = "method";
  else if (/(?:검사\s*코드|SCL\s*코드)/i.test(text)) field = "code";
  const quoted = text.match(/["'“”‘’]([^"'“”‘’]{1,60})["'“”‘’]/)?.[1]?.trim();
  const latin = (text.match(/[A-Za-z][A-Za-z0-9._-]*/g) || [])
    .find((token) => !FILTER_TOKEN_STOP_WORDS.has(token.toLowerCase()));
  const korean = text.match(/(?:^|\s)([가-힣]{2,20})(?:인\s*(?:거|것)|들어|포함)/)?.[1]
    ?.replace(/^(?:검사명|검체명|검체|용기|방법)/, "");
  const term = quoted || latin || korean || (field ? previousFilter?.term : null) || null;
  return term ? { field, term } : null;
}

const CANDIDATE_FIELDS = {
  name: { label: "검사명", value: (item) => item.name },
  specimen: { label: "검체명", value: (item) => item.specimen },
  container: { label: "용기", value: (item) => item.container },
  method: { label: "검사방법", value: (item) => item.method },
  code: { label: "검사코드", value: (item) => item.code },
};

function candidateFilterReply(items, filter) {
  const requested = filter.field ? CANDIDATE_FIELDS[filter.field] : null;
  const aliases = { 혈청: "serum", 소변: "urine", 전혈: "whole blood", 혈장: "plasma", 대변: "stool" };
  const term = normalize(aliases[filter.term] || filter.term);
  const matchingFields = (item) => Object.entries(CANDIDATE_FIELDS)
    .filter(([field, definition]) => (!filter.field || field === filter.field)
      && normalize(definition.value(item)).includes(term));
  const matches = items.filter((item) => matchingFields(item).length);
  if (matches.length === 1) {
    const matchedLabel = requested?.label || matchingFields(matches[0])[0][1].label;
    return testReply(
      matches[0],
      `${matchedLabel}에 '${filter.term}'이(가) 포함된 항목은 이 검사입니다.`,
    );
  }
  if (matches.length > 1) {
    return {
      kind: "choices",
      text: `${requested?.label || "앞의 후보 정보"}에 '${filter.term}'이(가) 포함된 검사가 여러 개예요. 확인할 항목을 선택해 주세요.`,
      test: null,
      choices: matches.slice(0, 4).map((item) => testChoice(item)),
      citations: [],
      data_status: "public_database",
      grounding_status: "grounded_internal",
      answerability: "partial",
      claim_coverage: 1,
      missing_information: [],
    };
  }

  const alternate = requested && Object.entries(CANDIDATE_FIELDS)
    .filter(([field]) => field !== filter.field)
    .map(([field, definition]) => ({
      field,
      label: definition.label,
      matches: items.filter((item) => normalize(definition.value(item)).includes(term)),
    }))
    .find((entry) => entry.matches.length);
  if (alternate?.matches.length === 1) {
    return testReply(
      alternate.matches[0],
      `${requested.label}에는 '${filter.term}'이(가) 없습니다. ${alternate.label}에는 포함되어 있으며, 해당 항목은 이 검사입니다.`,
    );
  }
  if (alternate?.matches.length > 1) {
    return {
      kind: "choices",
      text: `${requested.label}에는 '${filter.term}'이(가) 없습니다. ${alternate.label}에는 포함된 항목이 여러 개예요.`,
      test: null,
      choices: alternate.matches.slice(0, 4).map((item) => testChoice(item)),
      citations: [],
      data_status: "public_database",
      grounding_status: "grounded_internal",
      answerability: "partial",
      claim_coverage: 1,
      missing_information: [],
    };
  }
  return formReply(
    "text",
    `앞에서 찾은 검사 후보의 ${requested?.label || "검사 정보"}에는 '${filter.term}'이(가) 없습니다.`,
  );
}

function turnaroundUpperDays(value) {
  const numbers = String(value || "").match(/\d+(?:\.\d+)?/g)?.map(Number) || [];
  return numbers.length ? Math.max(...numbers) : null;
}

function contextualCandidateReply(query, items, previousFilter) {
  if (/(?:두\s*(?:개|검사)|둘|2\s*개).{0,12}(?:차이|비교|다른)|(?:차이|비교).{0,12}(?:두\s*(?:개|검사)|둘|2\s*개)/i.test(query)) {
    const compared = uniqueTests(items).slice(0, 2);
    if (compared.length === 2) {
      return { reply: comparisonReply(compared), filter: previousFilter };
    }
  }
  const ordinalMatch = String(query).match(/(?:^|\s)(첫|두|세|네|1|2|3|4)\s*번째/);
  if (ordinalMatch) {
    const indexes = { 첫: 0, 두: 1, 세: 2, 네: 3, 1: 0, 2: 1, 3: 2, 4: 3 };
    const item = items[indexes[ordinalMatch[1]]];
    if (item) return { reply: testReply(item, "앞의 후보에서 선택한 검사입니다."), filter: previousFilter };
  }
  if (/(?:가장\s*(?:빠른|짧은)|소요일.{0,8}(?:짧|빠)|제일\s*(?:빠른|짧은))/i.test(query)) {
    const ranked = items
      .map((item) => ({ item, days: turnaroundUpperDays(item.tat) }))
      .filter(({ days }) => days !== null)
      .sort((left, right) => left.days - right.days);
    if (ranked.length) {
      const best = ranked.filter(({ days }) => days === ranked[0].days).map(({ item }) => item);
      if (best.length === 1) {
        return { reply: testReply(best[0], "앞의 후보 중 소요일이 가장 짧은 검사입니다."), filter: previousFilter };
      }
      return {
        reply: {
          kind: "choices",
          text: "앞의 후보 중 소요일이 가장 짧은 검사가 여러 개예요.",
          test: null,
          choices: best.slice(0, 4).map((item) => testChoice(item)),
          citations: [],
          data_status: "public_database",
          grounding_status: "grounded_internal",
          answerability: "partial",
          claim_coverage: 1,
          missing_information: [],
        },
        filter: previousFilter,
      };
    }
  }
  const filter = candidateFilter(query, previousFilter);
  return filter ? { reply: candidateFilterReply(items, filter), filter } : null;
}

function normalizedBillingCodes(item) {
  return (Array.isArray(item.billing_codes) ? item.billing_codes : [])
    .map((code) => String(code).replace(/etc$/i, "").toUpperCase());
}

function exactCodeMatches(query, items = catalog) {
  const codes = codeCandidates(query);
  if (!codes.length) return null;
  const resolved = new Map();
  for (const code of codes) {
    const sclMatches = items.filter((item) => String(item.code || "").toUpperCase() === code);
    const matches = sclMatches.length
      ? sclMatches.map((item) => ({ item, matchedCode: code, matchedCodeType: "scl" }))
      : items
        .filter((item) => normalizedBillingCodes(item).includes(code))
        .map((item) => ({ item, matchedCode: code, matchedCodeType: "billing" }));
    for (const match of matches) resolved.set(match.item.variant_key, match);
  }
  return [...resolved.values()].map(({ item, matchedCode, matchedCodeType }) => ({
    item: { ...item, matched_code: matchedCode, matched_code_type: matchedCodeType },
    score: matchedCodeType === "scl" ? 1000 : 900,
    lexicalScore: matchedCodeType === "scl" ? 1000 : 900,
  }));
}

function rankTests(query, limit = 8) {
  const exactMatches = exactCodeMatches(query);
  if (exactMatches) return exactMatches.slice(0, limit);
  const normalized = normalize(query);
  const terms = termsFor(query);
  if (!normalized) return [];
  return catalog
    .map((item) => {
      const name = normalize(item.name);
      const code = String(item.code || "").toLocaleLowerCase();
      const aliases = item.aliases.map(normalize);
      let score = 0;
      if (normalized === code) score += 1000;
      else if (code && normalized.includes(code)) score += 250;
      if (normalized === name || aliases.includes(normalized)) score += 800;
      else if (name.includes(normalized) || normalized.includes(name)) score += 300;
      const matched = terms.filter((term) => item.search.includes(term));
      score += matched.length * 35;
      if (terms.length && matched.length === terms.length) score += 120;
      return { item, score, lexicalScore: score };
    })
    .filter(({ score }) => score > 0)
    .sort((left, right) => right.score - left.score || left.item.name.localeCompare(right.item.name, "ko"))
    .slice(0, limit);
}

function rankPublic(query, limit = 6) {
  const normalized = normalize(query);
  const terms = termsFor(query);
  if (!normalized || !terms.length) return [];
  return publicItems
    .map((item) => {
      const title = normalize(item.title);
      let score = title === normalized ? 700 : title.includes(normalized) ? 260 : 0;
      const matched = terms.filter((term) => item.search.includes(term));
      score += matched.length * 24;
      if (matched.length === terms.length) score += 90;
      return { item, score, lexicalScore: score };
    })
    .filter(({ score }) => score > 0)
    .sort((left, right) => right.score - left.score || left.item.title.localeCompare(right.item.title, "ko"))
    .slice(0, limit);
}

function decodeVector(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function reserveGeminiCall(env) {
  await ensureDatabase(env);
  const day = new Date().toISOString().slice(0, 10);
  const limit = Math.max(1, Number(env.GEMINI_DAILY_REQUEST_LIMIT || 20));
  await env.DB.prepare("INSERT OR IGNORE INTO gemini_daily_usage (day, calls) VALUES (?, 0)").bind(day).run();
  const result = await env.DB.prepare("UPDATE gemini_daily_usage SET calls = calls + 1 WHERE day = ? AND calls < ?")
    .bind(day, limit).run();
  if (result?.meta?.changes === 0) throw new GeminiDailyLimitError("Gemini daily request limit reached");
}

async function rankPublicWithVectors(env, query, lexicalHits, limit = 6) {
  if (!env.GEMINI_API_KEY || !publicItems.some((item) => item.vector)) return lexicalHits;
  await reserveGeminiCall(env);
  const model = env.GEMINI_EMBEDDING_MODEL || "gemini-embedding-2";
  const dimensions = Math.max(1, Number(env.GEMINI_EMBEDDING_DIMENSIONS || 128));
  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:embedContent`, {
    method: "POST",
    headers: { "x-goog-api-key": env.GEMINI_API_KEY, "content-type": "application/json" },
    body: JSON.stringify({
      content: { parts: [{ text: `task: search result | query: ${query}` }] },
      outputDimensionality: dimensions,
    }),
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error(`Gemini Embeddings API ${response.status}`);
  const queryVector = (await response.json()).embedding?.values || [];
  if (queryVector.length !== dimensions) throw new Error("Gemini embedding dimensions did not match the index");
  const queryNorm = Math.sqrt(queryVector.reduce((sum, value) => sum + Number(value) ** 2, 0)) || 1;
  const scores = new Map(lexicalHits.map((hit) => [hit.item.ref, { score: hit.score, lexicalScore: hit.lexicalScore ?? hit.score }]));
  for (const item of publicItems) {
    if (!item.vector || !["document", "attachment"].includes(item.entity_type)) continue;
    const vector = decodeVector(item.vector);
    if (vector.length !== dimensions) continue;
    let dot = 0;
    let vectorNormSquared = 0;
    for (let index = 0; index < dimensions; index += 1) {
      const signed = vector[index] < 128 ? vector[index] : vector[index] - 256;
      dot += signed * Number(queryVector[index]);
      vectorNormSquared += signed * signed;
    }
    const cosine = dot / ((Math.sqrt(vectorNormSquared) || 1) * queryNorm);
    if (cosine >= 0.55) {
      const existing = scores.get(item.ref) || { score: 0, lexicalScore: 0 };
      scores.set(item.ref, { ...existing, score: existing.score + cosine * 420 });
    }
  }
  if (lexicalHits[0]?.lexicalScore >= 90) {
    const existing = scores.get(lexicalHits[0].item.ref);
    scores.set(lexicalHits[0].item.ref, { ...existing, score: existing.score + 1000 });
  }
  return [...scores.entries()]
    .map(([ref, values]) => ({ item: publicItems.find((item) => item.ref === ref), ...values }))
    .filter(({ item }) => item)
    .sort((left, right) => right.score - left.score || left.item.title.localeCompare(right.item.title, "ko"))
    .slice(0, limit);
}

function publicTest(item) {
  const { search: _search, ...test } = item;
  return test;
}

function testChoice(item) {
  return {
    label: `${item.name} · ${item.specimen} · 검사코드 ${item.code}`,
    name: item.name,
    code: item.code,
    specimen: item.specimen,
    tat: item.tat,
    url: safeUrl(item.source_url),
  };
}

function testReply(item, text = "확인된 SCL 공개 검사 항목입니다. 검사 조건은 아래 공개 데이터 카드에서 확인해 주세요.") {
  const test = publicTest(item);
  const resolvedText = test.matched_code_type === "billing" && test.matched_code
    ? `급여코드 ${test.matched_code}에 정확히 일치하는 SCL 공개 검사 항목입니다. 검사 조건은 아래 공개 데이터 카드에서 확인해 주세요.`
    : text;
  return {
    kind: "test",
    text: resolvedText,
    test,
    choices: [],
    citations: [{ title: test.source_title, ref: `test:${test.variant_key || test.code}`, url: safeUrl(test.source_url), updated_at: test.updated_at, source_tier: "internal_scl", claim_ids: ["verified-test"] }],
    data_status: "public_database",
    grounding_status: "grounded_internal",
    answerability: "full",
    claim_coverage: 1,
    missing_information: [],
  };
}

const CONCEPT_TEST_NAMES = {
  thyroid: ["TSH", "Free T4", "Free T3", "T3", "T4"],
  liver: ["ALT", "AST", "γ-GTP", "ALP", "Bilirubin,total", "Bilirubin total"],
};

function canonicalTestName(item) {
  return String(item.name || "").replace(/^\([^)]*\)\s*/, "").trim();
}

function testCitation(item) {
  return {
    title: item.name,
    ref: `test:${item.variant_key || item.code}`,
    url: safeUrl(item.source_url),
    updated_at: item.updated_at,
    source_tier: "internal_scl",
    claim_ids: ["verified-test"],
  };
}

function uniqueTests(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = item.variant_key || `${item.code}:${item.specimen}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function fieldValue(item, field) {
  const details = item.public_details || {};
  const direct = {
    billing: details["급여코드"] || item.billing_codes?.join(", "),
    specimen: item.specimen,
    container: item.container,
    method: item.method || details["검사방법"],
    schedule: item.schedule,
    tat: item.tat,
    precautions: details["채취방법 및 주의사항"],
    clinical_significance: details["임상적 의의"],
    storage: details["보존방법"],
    price: details["검사수가"],
  }[field];
  if (field !== "container" || direct) return direct;
  const values = [];
  if (details["용기 첨가제"]) values.push(`첨가제: ${details["용기 첨가제"]}`);
  if (details["용기 주요검사항목"]) values.push(`주요 항목: ${details["용기 주요검사항목"]}`);
  if (!values.length && details["용기 주의사항/참고"]) values.push(details["용기 주의사항/참고"]);
  return values.join(" · ") || null;
}

const FIELD_LABELS = {
  billing: "급여코드",
  specimen: "검체",
  container: "용기",
  method: "검사방법",
  schedule: "검사요일",
  tat: "소요일",
  precautions: "주의사항",
  clinical_significance: "임상적 의의",
  storage: "검체 보존방법",
  price: "공개 검사수가",
};

function missingFieldText(field) {
  if (field === "container") return "SCL 공개 페이지에서 용기 정보를 확인하지 못함";
  if (field === "precautions") return "SCL 공개 페이지에 별도 주의사항 미기재";
  if (field === "clinical_significance") return "SCL 공개 페이지에 별도 설명 미기재";
  return "SCL 공개 페이지에 별도 기재되지 않음";
}

function compactPublicText(value, maxChars = 240) {
  const text = String(value || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  return text.length <= maxChars ? text : `${text.slice(0, maxChars - 2).trim()} …`;
}

function textReply(text, items, { answerability = "full", missing = [] } = {}) {
  return {
    kind: "text",
    text,
    test: null,
    choices: [],
    citations: uniqueTests(items).map(testCitation),
    data_status: "public_database",
    grounding_status: "grounded_internal",
    answerability,
    claim_coverage: answerability === "full" ? 1 : 0.75,
    missing_information: missing,
  };
}

function fieldAnswerReply(items, slots, query) {
  const fields = [...new Set(slots.map(([field]) => field))];
  const missing = [];
  const displayed = (item, field) => {
    const value = fieldValue(item, field);
    if (value && String(value).trim() !== "-") return String(value).trim();
    missing.push(`${item.name}: ${FIELD_LABELS[field]}`);
    return missingFieldText(field);
  };
  if (items.length === 1) {
    const item = items[0];
    const facts = fields.map((field) => `- ${FIELD_LABELS[field]}: ${compactPublicText(displayed(item, field))}`);
    const reply = testReply(item, `${item.name} (검사코드 ${item.code})의 요청하신 정보입니다.\n${facts.join("\n")}`);
    return { ...reply, answerability: missing.length ? "partial" : "full", missing_information: missing };
  }

  const labels = fields.map((field) => FIELD_LABELS[field]).join("·");
  const lines = [
    fields.length === 1 && fields[0] === "container" && /hpv/i.test(query)
      ? "HPV 검사는 검사 방식과 검체에 따라 사용하는 용기가 다릅니다."
      : `조회된 검사들의 ${labels} 정보를 공통 값별로 정리했습니다.`,
  ];
  const cited = [];
  if (fields.includes("specimen")) {
    const groups = new Map();
    for (const item of items) {
      const specimen = displayed(item, "specimen");
      if (!groups.has(specimen)) groups.set(specimen, []);
      groups.get(specimen).push(item);
    }
    for (const [specimen, group] of groups) {
      const parts = fields.filter((field) => field !== "specimen").map((field) => {
        const values = [...new Set(group.map((item) => compactPublicText(displayed(item, field))))];
        return `${FIELD_LABELS[field]}: ${values.join(", ")}`;
      });
      lines.push(`- ${specimen} 검체${parts.length ? `: ${parts.join(" · ")}` : ""}`);
      cited.push(...group.slice(0, Math.max(1, parts.length)));
    }
  } else {
    const groups = new Map();
    for (const item of items) {
      const values = fields.map((field) => compactPublicText(displayed(item, field)));
      const key = JSON.stringify(values);
      if (!groups.has(key)) groups.set(key, { values, items: [] });
      groups.get(key).items.push(item);
    }
    for (const { values, items: groupedItems } of groups.values()) {
      const specimens = [...new Set(groupedItems.map((item) => item.specimen).filter(Boolean))];
      let context = groupedItems.slice(0, 3).map((item) => item.name).join(", ");
      if (fields.length === 1 && fields[0] === "container" && specimens.length === 1) {
        context = specimens[0] === "Cervix cell" ? "액상 HPV 검사" : "자궁경부·질 면봉을 이용한 HPV PCR";
      }
      const detail = fields.length === 1
        ? values[0]
        : fields.map((field, index) => `${FIELD_LABELS[field]}: ${values[index]}`).join(" · ");
      lines.push(`- ${context}: ${detail}`);
      cited.push(groupedItems[0]);
    }
  }
  return textReply(lines.join("\n"), cited, {
    answerability: missing.length ? "partial" : "full",
    missing: [...new Set(missing)],
  });
}

function listReply(items, intro, fields = []) {
  const shown = uniqueTests(items).slice(0, items.length > 10 ? 8 : 20);
  const displayedFields = fields.length ? fields : ["specimen", "tat"];
  const lines = [`조건에 해당하는 검사 ${items.length}건입니다.`];
  if (intro) lines.push(intro);
  for (const item of shown) {
    lines.push("", `${item.name} (검사코드 ${item.code})`);
    const details = displayedFields.map((field) => {
      const value = fieldValue(item, field);
      return `${FIELD_LABELS[field]}: ${value || missingFieldText(field)}`;
    });
    lines.push(details.join(" · "));
  }
  if (items.length > shown.length) lines.push("", `결과가 많아 대표 ${shown.length}건만 표시했습니다.`);
  return textReply(lines.join("\n"), shown);
}

function comparisonReply(items) {
  const compared = items.slice(0, 2);
  const fields = ["billing", "specimen", "method", "schedule", "tat", "precautions", "storage", "price"];
  const common = [];
  const differences = [];
  for (const field of fields) {
    const values = compared.map((item) => {
      const raw = fieldValue(item, field);
      if (!raw) return missingFieldText(field);
      return field === "precautions" ? "상세 안내 있음" : compactPublicText(raw);
    });
    const line = `${FIELD_LABELS[field]}: ${values[0]}`;
    if (values[0] === values[1]) common.push(line);
    else differences.push(`- ${FIELD_LABELS[field]}: ${compared.map((item, index) => `${item.name}(${item.code}) ${values[index]}`).join(" · ")}`);
  }
  const lines = ["두 검사의 핵심 차이입니다."];
  if (differences.length) lines.push("", "## 차이점", ...differences);
  if (common.length) lines.push("", "## 공통점", `- ${common.join(" · ")}`);
  return textReply(lines.join("\n"), compared);
}

function requestedFieldSlots(query) {
  const text = String(query || "");
  return [
    ["billing", "급여코드", /(?:급여|보험)\s*코드/i],
    ["specimen", "검체", /검[체채]|피로\s*검사/i],
    ["container", "용기", /용기|튜브|어떤\s*통|무슨\s*통|통에\s*담/i],
    ["method", "검사방법", /검사\s*방법|방법/i],
    ["schedule", "검사요일", /검사\s*(?:요일|일정)|(?<!소)요일/i],
    ["tat", "소요일", /소요\s*(?:일|기간)|며칠|얼마나\s*걸|결과.{0,8}(?:언제|빨리|빠르게)/i],
    ["precautions", "주의사항", /주의\s*사항|주의할\s*점/i],
    ["clinical_significance", "임상적 의의", /임상적\s*의의/i],
    ["storage", "검체 보존방법", /보존\s*방법|어떻게\s*보관/i],
    ["price", "공개 검사수가", /검사\s*수가|가격|비용/i],
  ].filter(([, , pattern]) => pattern.test(text));
}

function applyAnswerCoverage(reply, query) {
  const slots = requestedFieldSlots(query);
  if (!slots.length || reply.kind !== "test" || !reply.test) return reply;
  const item = reply.test;
  const values = {
    billing: item.public_details?.["급여코드"] || item.billing_codes?.join(", "),
    specimen: item.specimen,
    container: item.container,
    method: item.method,
    schedule: item.schedule,
    tat: item.tat,
  };
  const facts = [];
  const missing = [];
  for (const [field, label] of slots) {
    const value = values[field];
    if (value && String(value).trim() !== "-") facts.push(`- ${label}: ${String(value).trim()}`);
    else missing.push(`${item.name}: ${label}`);
  }
  let prefix = `요청하신 정보\n${facts.join("\n")}`;
  if (missing.length) prefix += `\n\n확인하지 못한 항목\n- ${missing.join("\n- ")}`;
  return {
    ...reply,
    text: `${prefix}\n\n${reply.text}`,
    answerability: missing.length ? (facts.length ? "partial" : "none") : "full",
    claim_coverage: facts.length / slots.length,
    missing_information: missing,
  };
}

function publicReply(item) {
  return {
    kind: "text",
    text: ["document", "attachment", "faq"].includes(item.entity_type)
      ? `관련 공개 문서를 찾았습니다. ‘${item.title}’에서 세부 내용을 확인해 주세요.`
      : `${item.title}: ${item.snippet || "상세 내용은 출처에서 확인해 주세요."}`,
    test: null,
    choices: [],
    citations: [{ title: item.title, ref: item.ref, url: safeUrl(item.source_url), updated_at: item.updated_at, source_tier: "internal_scl", claim_ids: ["verified-public-record"] }],
    data_status: ["document", "attachment", "faq"].includes(item.entity_type) ? "public_document" : "public_database",
    grounding_status: "grounded_internal",
    answerability: "full",
    claim_coverage: 1,
    missing_information: [],
  };
}

function formReply(kind, text) {
  return { kind, text, test: null, choices: [], citations: [], data_status: "no_source", grounding_status: "abstained", answerability: "none", claim_coverage: 0, missing_information: [] };
}

function noSourceReply() {
  return formReply("text", "확인된 공개 자료에서 답변 근거를 찾지 못했습니다. 확인되지 않은 내용을 추측하지 않았습니다. 검사명·검사코드나 찾으시는 문서를 더 구체적으로 알려주세요.");
}

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function createGeminiPlan(env, query, sessionId, tests, publicHits) {
  void sessionId;
  const schema = {
    type: "object",
    additionalProperties: false,
    properties: {
      answer: { type: "string" },
      kind: { type: "string", enum: ["text", "test", "choices", "result_auth_form", "handoff_form"] },
      domain: { type: "string" },
      sub_intent: { type: "string", nullable: true },
      requested_action: { type: "string", nullable: true },
      matched_test_index: { type: "integer", nullable: true },
      matched_public_index: { type: "integer", nullable: true },
      needs_clarification: { type: "boolean" },
      requires_authentication: { type: "boolean" },
      needs_handoff: { type: "boolean" },
      medical_review_required: { type: "boolean" },
    },
    required: ["answer", "kind", "domain", "sub_intent", "requested_action", "matched_test_index", "matched_public_index", "needs_clarification", "requires_authentication", "needs_handoff", "medical_review_required"],
  };
  const candidates = tests.map(({ item }, index) => ({ index, ...publicTest(item) }));
  const sources = publicHits.map(({ item }, index) => ({ index, ...item, search: undefined, vector: undefined }));
  await reserveGeminiCall(env);
  const model = env.GEMINI_MODEL || "gemini-3.1-flash-lite";
  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
    method: "POST",
    headers: { "x-goog-api-key": env.GEMINI_API_KEY, "content-type": "application/json" },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: "당신은 SCL 공개 검사정보 안내 챗봇이다. 제공된 후보와 공개자료만 사실 근거로 사용한다. 개인 검사결과는 해석하지 말고 인증 폼으로, 의료 판단과 상담 요청은 상담 폼으로 연결한다. 주민번호·연락처 등 개인정보를 답변에 복원하지 않는다. 답변은 간결한 한국어로 작성하고 후보 인덱스는 제공된 배열 범위에서만 고른다." }] },
      contents: [{ role: "user", parts: [{ text: `사용자 질문: ${query}\n\n검사 후보: ${JSON.stringify(candidates)}\n\n공개자료 후보: ${JSON.stringify(sources)}` }] }],
      generationConfig: {
        responseMimeType: "application/json",
        responseJsonSchema: schema,
        maxOutputTokens: 900,
        temperature: 0.1,
      },
    }),
    signal: AbortSignal.timeout(28000),
  });
  if (!response.ok) {
    throw new Error(`Gemini GenerateContent API ${response.status}`);
  }
  const payload = await response.json();
  const candidate = payload.candidates?.[0];
  if (!candidate || ["SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT"].includes(candidate.finishReason)) {
    throw new Error("Gemini response was unavailable or blocked");
  }
  const outputText = (candidate.content?.parts || []).map((part) => part.text || "").join("");
  if (!outputText) throw new Error("Gemini response did not contain text");
  return { plan: JSON.parse(outputText), responseId: payload.responseId || `gemini-${crypto.randomUUID()}` };
}

function resolvePlan(plan, tests, publicHits, query) {
  if (plan.requires_authentication || plan.domain === "result") {
    return formReply("result_auth_form", "개인 검사결과는 보안 인증 후 조회할 수 있습니다. 인증정보는 AI에 전달하거나 저장하지 않습니다.");
  }
  if (
    (plan.needs_handoff || plan.kind === "handoff_form") && HANDOFF_INTENT.test(query)
    || plan.medical_review_required && MEDICAL_REVIEW_INTENT.test(query)
  ) {
    return formReply("handoff_form", "이 문의는 상담 접수가 필요합니다. 아래 정보를 입력하면 채팅 안에서 바로 접수됩니다.");
  }
  if (Number.isInteger(plan.matched_test_index) && tests[plan.matched_test_index]?.score >= 120) {
    return testReply(tests[plan.matched_test_index].item);
  }
  if (Number.isInteger(plan.matched_public_index) && publicHits[plan.matched_public_index]?.lexicalScore >= 90) {
    return publicReply(publicHits[plan.matched_public_index].item);
  }
  if (publicHits[0]?.lexicalScore >= 90) {
    return publicReply(publicHits[0].item);
  }
  if (plan.needs_clarification && tests.length) {
    return {
      kind: "choices",
      text: "관련 검사 후보가 여러 개예요. 확인할 항목을 선택해 주세요.",
      test: null,
      choices: tests.slice(0, 4).map(({ item }) => testChoice(item)),
      citations: [],
      data_status: "public_database",
      grounding_status: "grounded_internal",
      answerability: "partial",
      claim_coverage: 1,
      missing_information: [],
    };
  }
  return noSourceReply();
}

function scheduleIncludes(schedule, day) {
  const value = String(schedule || "").replace(/\s+/g, "");
  if (value.includes(day)) return true;
  const order = ["월", "화", "수", "목", "금", "토", "일"];
  const range = value.match(/([월화수목금토일])[~-]([월화수목금토일])/);
  return range && order.indexOf(day) >= order.indexOf(range[1]) && order.indexOf(day) <= order.indexOf(range[2]);
}

function deterministicTestQuestion(query, rankedTests) {
  const text = String(query || "");
  const codes = codeCandidates(text);
  const slots = requestedFieldSlots(text);
  const comparesCodes = codes.length >= 2 && /(차이|비교|달라)/.test(text);
  const explanation = /(무슨\s*검사|어떤\s*검사야|뭐(?:야|하는\s*검사)|무슨\s*의미|왜\s*검사)/.test(text);

  if (comparesCodes) {
    const items = uniqueTests(rankedTests.map(({ item }) => item));
    return items.length >= 2 ? comparisonReply(items) : null;
  }
  if (codes.length === 1 && explanation && rankedTests.length === 1 && !slots.length) {
    const item = rankedTests[0].item;
    const significance = compactPublicText(fieldValue(item, "clinical_significance"), 320);
    const description = significance
      ? `${item.name} (검사코드 ${item.code})은 SCL 공개 자료에서 다음과 같이 설명합니다.\n${significance}`
      : `${item.code}는 ${item.name} 검사입니다. SCL 공개 정보상 검체는 ${item.specimen}, 검사방법은 ${item.method}, 소요일은 ${item.tat}입니다. 구체적인 임상적 의미는 SCL 공개 페이지에 별도로 기재되어 있지 않습니다.`;
    return testReply(item, description);
  }
  if (codes.length) return null;

  let items = [];
  let intro = "";
  if (/갑상선/.test(text)) {
    const wanted = new Set(CONCEPT_TEST_NAMES.thyroid);
    const order = new Map(CONCEPT_TEST_NAMES.thyroid.map((name, index) => [name, index]));
    items = catalog
      .filter((item) => wanted.has(canonicalTestName(item)))
      .sort((left, right) => (
        order.get(canonicalTestName(left)) - order.get(canonicalTestName(right))
        || Number(left.name.startsWith("(")) - Number(right.name.startsWith("("))
      ));
    intro = "아래 항목은 갑상선 기능을 확인할 때 함께 참고하는 검사입니다. TSH는 갑상선자극호르몬, T3·T4와 Free T3·Free T4는 갑상선호르몬 관련 항목입니다.";
  } else if (/간\s*(?:기능|수치|검사)|간기능/.test(text)) {
    const wanted = new Set(CONCEPT_TEST_NAMES.liver);
    const order = new Map(CONCEPT_TEST_NAMES.liver.map((name, index) => [name, index]));
    items = catalog
      .filter((item) => wanted.has(canonicalTestName(item)))
      .sort((left, right) => (
        order.get(canonicalTestName(left)) - order.get(canonicalTestName(right))
        || Number(left.name.startsWith("(")) - Number(right.name.startsWith("("))
      ));
    intro = "아래 항목은 간 상태를 확인할 때 함께 참고하는 검사입니다. ALT·AST 등은 검사 목적과 임상 상황에 따라 함께 확인할 수 있습니다.";
  } else if (/hpv/i.test(text)) {
    items = catalog.filter((item) => /hpv/i.test(item.name) || item.aliases?.some((alias) => /hpv/i.test(alias)));
  } else if (/(?<![A-Za-z0-9])ALT(?![A-Za-z0-9])|에이엘티/i.test(text)) {
    items = catalog.filter((item) => canonicalTestName(item).toUpperCase() === "ALT");
    if (!/관련|특검/.test(text)) items = items.filter((item) => item.name === "ALT");
  } else if (/소변|요검체|urine/i.test(text)) {
    items = catalog.filter((item) => /urine|소변|요\b/i.test(item.specimen));
  } else {
    return null;
  }

  if (/serum|혈청|피\s*뽑/i.test(text)) items = items.filter((item) => /serum|혈청/i.test(item.specimen));
  else if (/혈장|plasma/i.test(text)) items = items.filter((item) => /plasma|혈장/i.test(item.specimen));
  else if (/전혈|whole\s*blood/i.test(text)) items = items.filter((item) => /whole\s*blood|전혈/i.test(item.specimen));
  else if (/대변|분변|stool/i.test(text)) items = items.filter((item) => /stool|대변|분변/i.test(item.specimen));

  const requestedDay = Object.entries({ 월요일: "월", 화요일: "화", 수요일: "수", 목요일: "목", 금요일: "금", 토요일: "토", 일요일: "일" })
    .find(([label]) => text.includes(label))?.[1];
  if (requestedDay) items = items.filter((item) => scheduleIncludes(item.schedule, requestedDay));
  if (/(당일|하루\s*(?:안|이내)|1일\s*이내)/.test(text)) {
    items = items.filter((item) => {
      const days = turnaroundUpperDays(item.tat);
      return days !== null && days <= 1;
    });
  }
  const shortest = /(가장|제일).{0,6}(빠른|빨리|짧은)|(?:결과\s*)?(?:빨리|빠르게)\s*나오|소요일.{0,6}짧/.test(text);
  if (shortest && items.length) {
    const known = items.map((item) => ({ item, days: turnaroundUpperDays(item.tat) })).filter(({ days }) => days !== null);
    if (known.length) {
      const best = Math.min(...known.map(({ days }) => days));
      items = known.filter(({ days }) => days === best).map(({ item }) => item);
    }
  }
  items = uniqueTests(items);
  if (!items.length) return noSourceReply();

  const listRequest = /(찾아\s*줘|목록|후보|어떤\s*검사(?:가|들|를)?|검사\s*중|골라\s*줘|보여\s*줘|해당하는\s*검사)/.test(text)
    || requestedDay || shortest || /(당일|하루\s*(?:안|이내)|1일\s*이내)/.test(text);
  if (slots.length && !listRequest) return fieldAnswerReply(items, slots, text);
  if (listRequest) {
    const fields = slots.map(([field]) => field);
    if (requestedDay && !fields.includes("schedule")) fields.push("schedule");
    if (shortest && !fields.includes("tat")) fields.push("tat");
    return listReply(items, intro, fields);
  }
  if (items.length === 1) return testReply(items[0]);
  return listReply(items, intro);
}

function deterministicReply(query, lastTest, tests, publicHits) {
  if (RESULT_INTENT.test(query)) {
    return { domain: "result", reply: formReply("result_auth_form", "개인 검사결과는 보안 인증 후 조회할 수 있습니다. 인증정보는 AI에 전달하거나 저장하지 않습니다.") };
  }
  if (HANDOFF_INTENT.test(query)) {
    return { domain: "support", reply: formReply("handoff_form", "아래 정보를 입력하면 다른 창으로 이동하지 않고 상담 문의를 접수할 수 있습니다.") };
  }
  if (FOLLOWUP_INTENT.test(query) && lastTest) {
    return { domain: "test", reply: testReply(lastTest, "앞에서 확인한 검사의 정보를 다시 정리했어요.") };
  }
  if (codeCandidates(query).length) {
    if (tests.length === 1) {
      return { domain: "test", reply: testReply(tests[0].item) };
    }
    if (tests.length > 1) {
      return { domain: "test", reply: {
        kind: "choices",
        text: "코드에 정확히 일치하는 검사가 여러 개예요. 확인할 항목을 선택해 주세요.",
        test: null,
        choices: tests.slice(0, 4).map(({ item }) => testChoice(item)),
        citations: [],
        data_status: "public_database",
        grounding_status: "grounded_internal",
        answerability: "partial",
        claim_coverage: 1,
        missing_information: [],
      } };
    }
    return { domain: "test", reply: noSourceReply() };
  }
  const topTest = tests[0];
  const secondTest = tests[1];
  const topPublic = publicHits[0];
  if (topPublic?.lexicalScore >= 90 && (!topTest || topPublic.score > topTest.score + 80)) {
    return { domain: topPublic.item.entity_type === "document" ? "document" : "support", reply: publicReply(topPublic.item) };
  }
  if (topTest && (!secondTest || topTest.score >= secondTest.score + 120 || topTest.score >= 700)) {
    return { domain: "test", reply: testReply(topTest.item, "요청 조건과 가장 가까운 SCL 공개 검사 항목입니다.") };
  }
  if (tests.length) {
    return { domain: "test", reply: {
      kind: "choices",
      text: "관련 검사 후보가 여러 개예요. 확인할 항목을 선택해 주세요.",
      test: null,
      choices: tests.slice(0, 4).map(({ item }) => testChoice(item)),
      citations: [],
      data_status: "public_database",
      grounding_status: "grounded_internal",
      answerability: "partial",
      claim_coverage: 1,
      missing_information: [],
    } };
  }
  return { domain: "unsupported", reply: noSourceReply() };
}

async function handleChat(request, env) {
  let body;
  try { body = await request.json(); } catch { return detail("JSON 요청 형식을 확인해 주세요.", 400); }
  if (typeof body.message !== "string" || !body.message.trim() || body.message.length > 500) {
    return detail("메시지는 1자 이상 500자 이하로 입력해 주세요.", 422);
  }
  const sessionId = typeof body.session_id === "string" && body.session_id.length <= 80 ? body.session_id : crypto.randomUUID();
  const started = Date.now();
  const inspection = inspectInput(body.message);
  if (inspection.action === "block") {
    return json({
      session_id: sessionId,
      displayed_input: inspection.displayed,
      reply: formReply("text", "해당 요청은 처리할 수 없습니다. SCL 검사·검체·결과 조회 절차에 관해 질문해 주세요."),
      mode: env.GEMINI_API_KEY ? "gemini" : "demo_fallback",
      safety_action: "block",
      domain: "unsupported",
      sub_intent: null,
      requested_action: null,
      requires_authentication: false,
      needs_handoff: false,
      medical_review_required: false,
      response_id: null,
      timings_ms: { total: Date.now() - started },
    });
  }

  if (body.require_live && !env.GEMINI_API_KEY) {
    return detail("실시간 AI 연결이 구성되지 않았습니다.", 503);
  }
  let sessionState = null;
  try { sessionState = await readSession(env, sessionId); } catch (error) { console.error("D1 session read failed", error); }
  const lastTest = sessionState?.last_test || (sessionState?.code ? sessionState : null);
  const previousTests = Array.isArray(sessionState?.previous_tests) ? sessionState.previous_tests : [];
  const requestedCodes = codeCandidates(inspection.modelInput);
  const contextual = previousTests.length && !requestedCodes.length
    ? contextualCandidateReply(inspection.modelInput, previousTests, sessionState?.last_filter)
    : null;
  if (contextual) {
    const { filter } = contextual;
    const reply = applyAnswerCoverage(contextual.reply, inspection.modelInput);
    const selectedTest = reply.kind === "test" ? reply.test : lastTest;
    try {
      await saveSession(env, sessionId, {
        last_test: selectedTest,
        previous_tests: previousTests,
        last_filter: filter || null,
      });
    } catch (error) { console.error("D1 session save failed", error); }
    return json({
      session_id: sessionId,
      displayed_input: inspection.displayed,
      reply,
      mode: "deterministic",
      safety_action: inspection.action,
      domain: "test",
      sub_intent: "filter_previous_candidates",
      requested_action: "filter",
      requires_authentication: false,
      needs_handoff: false,
      medical_review_required: false,
      response_id: null,
      catalog_source: "session",
      timings_ms: { total: Date.now() - started },
    });
  }
  let tests = rankTests(inspection.modelInput);
  let publicHits = rankPublic(inspection.modelInput);
  if (requestedCodes.length) publicHits = [];
  let catalogSource = "snapshot";
  try {
    const remote = await searchSupabaseCatalog(env, inspection.modelInput);
    if (remote) {
      // During a rolling deployment, an older RPC can still return keyword
      // candidates for an identifier query. Keep the packaged exact-match
      // result until the RPC explicitly confirms the new identifier contract.
      if (!requestedCodes.length || remote.identifierQueryHandled) tests = remote.tests;
      publicHits = requestedCodes.length ? [] : remote.publicItems;
      catalogSource = remote.source;
    }
  } catch (error) {
    console.error("Supabase catalog search failed; using snapshot", error);
  }
  let reply;
  let domain;
  let mode = "demo_fallback";
  let responseId = null;
  let plan = null;

  const deterministicTest = deterministicTestQuestion(inspection.modelInput, tests);
  if (deterministicTest) {
    reply = deterministicTest;
    domain = "test";
    mode = "deterministic";
  }

  if (
    !reply
    &&
    env.GEMINI_API_KEY
    && !requestedCodes.length
    && !RESULT_INTENT.test(inspection.modelInput)
    && !HANDOFF_INTENT.test(inspection.modelInput)
  ) {
    try {
      try {
        if (catalogSource === "snapshot") publicHits = await rankPublicWithVectors(env, inspection.modelInput, publicHits);
      } catch (error) {
        console.error("Gemini vector search failed", error);
      }
      const generated = await createGeminiPlan(env, inspection.modelInput, sessionId, tests, publicHits);
      plan = generated.plan;
      responseId = generated.responseId;
      reply = resolvePlan(plan, tests, publicHits, inspection.modelInput);
      domain = plan.domain;
      mode = "gemini";
    } catch (error) {
      console.error("Gemini generation failed", error);
      if (body.require_live) return detail("실시간 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.", 503);
    }
  }
  if (!reply) {
    const fallback = deterministicReply(inspection.modelInput, lastTest, tests, publicHits);
    reply = fallback.reply;
    domain = fallback.domain;
  }
  reply = applyAnswerCoverage(reply, inspection.modelInput);

  const selectedTest = reply.kind === "test" ? reply.test : lastTest;
  const rememberedTests = requestedCodes.length || tests.length
    ? tests.map(({ item }) => publicTest(item)).slice(0, 12)
    : previousTests;
  try {
    await saveSession(env, sessionId, {
      last_test: selectedTest,
      previous_tests: rememberedTests,
      last_filter: null,
    });
  } catch (error) { console.error("D1 session save failed", error); }
  const requiresAuthentication = reply.kind === "result_auth_form" || Boolean(plan?.requires_authentication);
  const needsHandoff = reply.kind === "handoff_form" || Boolean(plan?.needs_handoff);
  return json({
    session_id: sessionId,
    displayed_input: inspection.displayed,
    reply,
    mode,
    safety_action: inspection.action,
    domain: domain || "unknown",
    sub_intent: plan?.sub_intent || null,
    requested_action: plan?.requested_action || null,
    requires_authentication: requiresAuthentication,
    needs_handoff: needsHandoff,
    medical_review_required: Boolean(plan?.medical_review_required),
    response_id: responseId,
    catalog_source: catalogSource,
    timings_ms: { total: Date.now() - started },
  });
}

function decodeSecret(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function encrypt(value, secret) {
  const key = await crypto.subtle.importKey("raw", decodeSecret(secret), "AES-GCM", false, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(value)));
  const payload = new Uint8Array(iv.length + encrypted.length);
  payload.set(iv);
  payload.set(encrypted, iv.length);
  let binary = "";
  for (const byte of payload) binary += String.fromCharCode(byte);
  return btoa(binary);
}

async function handleHandoff(request, env) {
  if (!env.HANDOFF_ENCRYPTION_KEY) return detail("상담 접수 보안키가 아직 구성되지 않았습니다.", 503);
  let body;
  try { body = await request.json(); } catch { return detail("JSON 요청 형식을 확인해 주세요.", 400); }
  if (!body.consent) return detail("개인정보 수집 동의가 필요합니다.", 422);
  if (!INQUIRY_TYPES.has(body.inquiry_type)) return detail("문의 유형을 확인해 주세요.", 422);
  if (typeof body.session_id !== "string" || !body.session_id || body.session_id.length > 80) return detail("채팅 세션을 먼저 시작해 주세요.", 422);
  if (typeof body.requester_name !== "string" || !body.requester_name.trim() || body.requester_name.length > 100) return detail("이름을 확인해 주세요.", 422);
  const normalizedPhone = String(body.phone || "").replace(/\s+/g, "");
  if (!PHONE.test(normalizedPhone)) return detail("연락처 형식을 확인해 주세요.", 422);
  if (typeof body.content !== "string" || body.content.trim().length < 2 || body.content.length > 2000) return detail("문의 내용을 2자 이상 입력해 주세요.", 422);
  if (body.organization && (typeof body.organization !== "string" || body.organization.length > 200)) return detail("기관명을 확인해 주세요.", 422);

  await ensureDatabase(env);
  const now = new Date();
  const publicId = `SCL-${now.toISOString().slice(0, 10).replaceAll("-", "")}-${crypto.randomUUID().replaceAll("-", "").slice(0, 8).toUpperCase()}`;
  const refs = Array.isArray(body.related_refs) ? [...new Set(body.related_refs.filter((value) => typeof value === "string" && SAFE_REF.test(value)))].slice(0, 10) : [];
  await env.DB.prepare(`INSERT INTO handoff_requests (
    public_id, session_hash, inquiry_type, requester_name_encrypted, phone_encrypted,
    organization_encrypted, content_encrypted, related_refs_json, status, consented_at, created_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?)`)
    .bind(
      publicId,
      await sha256(body.session_id),
      body.inquiry_type,
      await encrypt(body.requester_name.trim(), env.HANDOFF_ENCRYPTION_KEY),
      await encrypt(normalizedPhone, env.HANDOFF_ENCRYPTION_KEY),
      body.organization ? await encrypt(body.organization.trim(), env.HANDOFF_ENCRYPTION_KEY) : null,
      await encrypt(body.content.trim(), env.HANDOFF_ENCRYPTION_KEY),
      JSON.stringify(refs),
      now.toISOString(),
      now.toISOString(),
    ).run();
  return json({ public_id: publicId, status: "submitted", created_at: now.toISOString() }, 201);
}

function redactFeedbackText(value, limit) {
  return String(value || "").slice(0, limit)
    .replace(RESIDENT_ID, "[주민등록번호 가림]")
    .replace(PHONE_IN_TEXT, "[전화번호 가림]")
    .replace(EMAIL, "[이메일 가림]")
    .trim();
}

async function handleFeedback(request, env) {
  let body;
  try { body = await request.json(); } catch { return detail("JSON 요청 형식을 확인해 주세요.", 400); }
  if (typeof body.session_id !== "string" || !body.session_id || body.session_id.length > 80) return detail("채팅 세션을 먼저 시작해 주세요.", 422);
  if (!new Set(["helpful", "not_helpful"]).has(body.rating)) return detail("답변 평가 값을 확인해 주세요.", 422);
  if (body.response_id != null && (typeof body.response_id !== "string" || body.response_id.length > 120)) return detail("응답 식별자를 확인해 주세요.", 422);
  if (body.reason != null && (typeof body.reason !== "string" || body.reason.length > 80)) return detail("평가 사유를 확인해 주세요.", 422);
  if (typeof body.question !== "string" || !body.question.trim() || body.question.length > 500) return detail("평가할 질문을 확인해 주세요.", 422);
  if (typeof body.answer !== "string" || !body.answer.trim() || body.answer.length > 3000) return detail("평가할 답변을 확인해 주세요.", 422);
  if (body.comment != null && (typeof body.comment !== "string" || body.comment.length > 500)) return detail("추가 의견을 확인해 주세요.", 422);

  await ensureDatabase(env);
  const question = redactFeedbackText(body.question, 500);
  const answer = redactFeedbackText(body.answer, 3000);
  const comment = redactFeedbackText(body.comment, 500) || null;
  const normalizedQuestion = normalize(question);
  if (!normalizedQuestion) return detail("FAQ 후보로 저장할 질문이 없습니다.", 422);
  const refs = Array.isArray(body.source_refs)
    ? [...new Set(body.source_refs.filter((value) => typeof value === "string" && SAFE_REF.test(value)))].slice(0, 10)
    : [];
  const domain = typeof body.domain === "string" ? body.domain.slice(0, 50) : null;
  const subIntent = typeof body.sub_intent === "string" ? body.sub_intent.slice(0, 80) : null;
  const fingerprint = await sha256([domain || "", subIntent || "", normalizedQuestion, ...refs.sort()].join("|"));
  const now = new Date().toISOString();
  const positive = body.rating === "helpful" ? 1 : 0;
  const negative = body.rating === "not_helpful" ? 1 : 0;

  await env.DB.prepare(`INSERT INTO faq_candidates (
    fingerprint, canonical_question, normalized_question, canonical_answer, domain, sub_intent,
    source_refs_json, occurrence_count, positive_count, negative_count, status, created_at, updated_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'draft', ?, ?)
  ON CONFLICT(fingerprint) DO UPDATE SET
    occurrence_count = occurrence_count + 1,
    positive_count = positive_count + excluded.positive_count,
    negative_count = negative_count + excluded.negative_count,
    canonical_answer = excluded.canonical_answer,
    updated_at = excluded.updated_at`)
    .bind(fingerprint, question, normalizedQuestion, answer, domain, subIntent, JSON.stringify(refs), positive, negative, now, now).run();
  const candidate = await env.DB.prepare("SELECT id, occurrence_count FROM faq_candidates WHERE fingerprint = ?")
    .bind(fingerprint).first();
  const feedbackResult = await env.DB.prepare(`INSERT INTO chat_feedback (
    response_id, session_hash, rating, reason, comment, redacted_question, normalized_question,
    answer_text, domain, sub_intent, source_refs_json, created_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(
      body.response_id || null,
      await sha256(body.session_id),
      body.rating,
      body.reason || null,
      comment,
      question,
      normalizedQuestion,
      answer,
      domain,
      subIntent,
      JSON.stringify(refs),
      now,
    ).run();
  return json({
    feedback_id: Number(feedbackResult?.meta?.last_row_id || 0),
    faq_candidate_id: Number(candidate?.id || 0),
    merged_occurrences: Number(candidate?.occurrence_count || 1),
  }, 201);
}

async function handleApi(request, env, url) {
  if (url.pathname === "/health" && request.method === "GET") {
    return json({
      status: "ok",
      mode: env.GEMINI_API_KEY ? "gemini" : "demo_fallback",
      model: env.GEMINI_MODEL || "gemini-3.1-flash-lite",
      rag_enabled: true,
      live_chat_available: Boolean(env.GEMINI_API_KEY),
      result_provider: "unconfigured",
      vector_search_enabled: true,
      vector_search_configured: Boolean(env.GEMINI_API_KEY && publicItems.some((item) => item.vector)),
      vector_search_shadow_mode: false,
      vector_index_completed: publicItems.filter((item) => item.vector).length,
      vector_index_failed: 0,
      vector_index_items_with_errors: 0,
      vector_index_last_synced_at: null,
      catalog_source: hasSupabaseCatalog(env) ? "supabase" : "snapshot",
    });
  }
  if (url.pathname === "/api/chat" && request.method === "POST") return handleChat(request, env);
  if (url.pathname === "/api/handoff" && request.method === "POST") return handleHandoff(request, env);
  if (url.pathname === "/api/feedback" && request.method === "POST") return handleFeedback(request, env);
  if (url.pathname === "/api/catalog/status" && request.method === "GET") {
    try {
      const remote = await readSupabaseCatalogStatus(env);
      if (remote) return json(remote);
    } catch (error) {
      console.error("Supabase catalog status failed; using snapshot", error);
    }
    return json({ source: "SCL_PUBLIC_SNAPSHOT", tests: new Set(catalog.map((item) => item.code)).size, variants: catalog.length, details: catalog.filter((item) => Object.keys(item.public_details).length).length });
  }
  if (url.pathname === "/api/public-data/status" && request.method === "GET") {
    return json({ documents: publicItems.filter((item) => item.entity_type === "document").length, attachments: publicItems.filter((item) => item.entity_type === "attachment").length, locations: publicItems.filter((item) => item.entity_type === "location").length, routes: publicItems.filter((item) => item.entity_type === "route").length });
  }
  const sessionMatch = url.pathname.match(/^\/api\/sessions\/([^/]+)$/);
  if (sessionMatch && request.method === "DELETE") {
    await ensureDatabase(env);
    await env.DB.prepare("DELETE FROM chat_sessions WHERE session_id = ?").bind(decodeURIComponent(sessionMatch[1])).run();
    return new Response(null, { status: 204 });
  }
  if (url.pathname.startsWith("/api/results")) {
    return detail("시연 배포에는 실제 검사결과 제공기관이 연결되어 있지 않습니다. 인증정보는 저장하지 않았습니다.", 503);
  }
  return detail("API 경로를 찾을 수 없습니다.", 404);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health" || url.pathname.startsWith("/api/")) {
      try {
        if (env.BACKEND_API_URL) return await relayBackend(request, env);
        return await handleApi(request, env, url);
      } catch (error) {
        console.error("Unhandled API error", error);
        return detail("서버 요청을 처리하지 못했습니다.", 500);
      }
    }

    const response = await env.ASSETS.fetch(request);
    const acceptsHtml = request.headers.get("accept")?.includes("text/html");
    if (response.status !== 404 || !acceptsHtml || !["GET", "HEAD"].includes(request.method)) return response;

    const indexUrl = new URL(request.url);
    indexUrl.pathname = "/index.html";
    indexUrl.search = "";
    return env.ASSETS.fetch(new Request(indexUrl, request));
  },
};
