const CHAT_API_URL = import.meta.env.VITE_CHAT_API_URL || "http://localhost:8000/api/chat";
const CHAT_HEALTH_URL = new URL("/health", CHAT_API_URL).toString();
const API_BASE_URL = new URL("/api/", CHAT_API_URL);

async function apiRequest(path, options = {}) {
  const response = await fetch(new URL(path, API_BASE_URL), {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let detail = `요청을 처리하지 못했습니다. (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch { /* keep the status-based message */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

export async function getChatHealth() {
  try {
    const response = await fetch(CHAT_HEALTH_URL, { signal: AbortSignal.timeout(3000) });
    if (!response.ok) throw new Error(`health returned ${response.status}`);
    return await response.json();
  } catch {
    return { status: "unavailable", mode: "unavailable", rag_enabled: false, live_chat_available: false };
  }
}

export async function sendChatMessage({ message, sessionId }) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 35000);

  try {
    const response = await fetch(CHAT_API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId, require_live: true }),
      signal: controller.signal,
    });

    if (!response.ok) {
      let detail = `실시간 답변을 생성하지 못했습니다. (${response.status})`;
      try {
        const body = await response.json();
        if (typeof body.detail === "string") detail = body.detail;
      } catch { /* keep the status-based message */ }
      throw new Error(detail);
    }

    const result = await response.json();
    return { ...result, transport: "api" };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("실시간 응답 시간이 초과되었습니다. 준비된 답변으로 대체하지 않았습니다.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export function endChatSession(sessionId) {
  if (!sessionId) return Promise.resolve();
  return apiRequest(`sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}

export function submitHandoff(payload) {
  return apiRequest("handoff", { method: "POST", body: JSON.stringify(payload) });
}

export function authenticateResults(payload) {
  return apiRequest("results/authenticate", { method: "POST", body: JSON.stringify(payload) });
}

export function listResults(sessionId) {
  return apiRequest(`results?session_id=${encodeURIComponent(sessionId)}`);
}

export function getResultDetail(sessionId, resultId) {
  return apiRequest(`results/${encodeURIComponent(resultId)}?session_id=${encodeURIComponent(sessionId)}`);
}

export function logoutResults(sessionId) {
  if (!sessionId) return Promise.resolve();
  return apiRequest(`results/session/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}
