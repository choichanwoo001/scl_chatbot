import { welcomeMessage } from "../lib/chatSession.js";

export function createInitialChatState(restored) {
  return {
    open: restored?.open ?? true,
    query: "",
    sessionId: restored?.sessionId || null,
    connectionMode: "checking",
    isSending: false,
    messages: restored?.messages?.length ? restored.messages : [welcomeMessage],
  };
}

export function chatReducer(state, action) {
  switch (action.type) {
    case "query_changed":
      return { ...state, query: action.query };
    case "open_changed":
      return { ...state, open: action.open };
    case "health_received":
      return { ...state, connectionMode: action.mode };
    case "send_started":
      return { ...state, isSending: true, query: "" };
    case "send_succeeded":
      return {
        ...state,
        isSending: false,
        sessionId: action.sessionId || state.sessionId,
        connectionMode: action.mode,
        messages: [...state.messages, action.userMessage, action.assistantMessage],
      };
    case "send_failed":
      return {
        ...state,
        isSending: false,
        connectionMode: "unavailable",
        messages: [...state.messages, action.userMessage, action.errorMessage],
      };
    case "message_appended":
      return { ...state, messages: [...state.messages, action.message] };
    case "session_closed":
      return createInitialChatState({ open: false, messages: [welcomeMessage] });
    default:
      return state;
  }
}
