export const CHAT_SESSION_KEY = "scl-chat-session-v1";

export const welcomeMessage = {
  id: "welcome",
  role: "assistant",
  kind: "text",
  text: "안녕하세요. SCL 챗봇입니다. 검사명을 몰라도 증상·검체·소요일 같은 조건으로 찾아드릴 수 있어요.",
};

export function loadChatSession(storage = window.sessionStorage) {
  try {
    const raw = storage.getItem(CHAT_SESSION_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw);
    if (!value || !Array.isArray(value.messages)) return null;
    return {
      sessionId: typeof value.sessionId === "string" ? value.sessionId : null,
      open: value.open !== false,
      messages: value.messages.filter((message) => !message.sensitive),
    };
  } catch {
    return null;
  }
}

export function saveChatSession(storage, state) {
  const messages = state.messages.filter((message) => (
    !message.sensitive && !["result_list", "result_detail"].includes(message.kind)
  ));
  storage.setItem(CHAT_SESSION_KEY, JSON.stringify({
    sessionId: state.sessionId,
    open: state.open,
    messages,
  }));
}

export function clearChatSession(storage = window.sessionStorage) {
  storage.removeItem(CHAT_SESSION_KEY);
}
