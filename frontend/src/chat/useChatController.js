import { useEffect, useReducer, useRef } from "react";
import {
  authenticateResults,
  endChatSession,
  getChatHealth,
  getResultDetail,
  listResults,
  logoutResults,
  sendChatMessage,
  submitHandoff,
} from "../lib/chatApi.js";
import {
  clearChatSession,
  loadChatSession,
  saveChatSession,
} from "../lib/chatSession.js";
import { chatReducer, createInitialChatState } from "./chatState.js";

function messageId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function assistantMessage(result) {
  return {
    id: messageId("assistant"), role: "assistant", ...result.reply,
    liveGenerated: result.mode === "gemini",
  };
}

export function useChatController() {
  const endingSession = useRef(false);
  const [state, dispatch] = useReducer(
    chatReducer,
    undefined,
    () => createInitialChatState(loadChatSession()),
  );

  useEffect(() => {
    let active = true;
    getChatHealth().then((health) => {
      if (active) dispatch({ type: "health_received", mode: health.mode });
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!endingSession.current) {
      saveChatSession(window.sessionStorage, {
        sessionId: state.sessionId,
        open: state.open,
        messages: state.messages,
      });
    }
  }, [state.sessionId, state.open, state.messages]);

  const sendMessage = async (value) => {
    const trimmed = value.trim();
    if (!trimmed || state.isSending) return;
    dispatch({
      type: "send_started",
      userMessage: { id: messageId("user"), role: "user", kind: "text", text: trimmed },
    });
    try {
      const result = await sendChatMessage({ message: trimmed, sessionId: state.sessionId });
      dispatch({
        type: "send_succeeded",
        sessionId: result.session_id,
        mode: result.mode,
        assistantMessage: assistantMessage(result),
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : "실시간 응답을 생성하지 못했습니다.";
      dispatch({
        type: "send_failed",
        errorMessage: { id: messageId("error"), role: "assistant", kind: "text", text: detail, error: true },
      });
    }
  };

  const authenticate = async (credentials) => {
    if (!state.sessionId) throw new Error("채팅 세션을 먼저 시작해 주세요.");
    await authenticateResults({ session_id: state.sessionId, ...credentials });
    const results = await listResults(state.sessionId);
    dispatch({ type: "message_appended", message: {
      id: messageId("results"), role: "assistant", kind: "result_list", sensitive: true,
      text: results.length ? "신청한 검사결과입니다." : "조회 가능한 검사결과가 없습니다.", results,
    } });
  };

  const selectResult = async (resultId) => {
    const detail = await getResultDetail(state.sessionId, resultId);
    dispatch({ type: "message_appended", message: {
      id: messageId("result"), role: "assistant", kind: "result_detail", sensitive: true, detail,
    } });
  };

  const handoff = async (values) => {
    if (!state.sessionId) throw new Error("채팅 세션을 먼저 시작해 주세요.");
    const receipt = await submitHandoff({ session_id: state.sessionId, related_refs: [], ...values });
    dispatch({ type: "message_appended", message: {
      id: messageId("handoff"), role: "assistant", kind: "text",
      text: `상담 문의가 접수되었습니다. 접수번호는 ${receipt.public_id}입니다.`,
    } });
  };

  const closeSession = () => {
    endingSession.current = true;
    clearChatSession();
    void Promise.allSettled([endChatSession(state.sessionId), logoutResults(state.sessionId)]);
    dispatch({ type: "session_closed" });
  };

  const reopen = () => {
    endingSession.current = false;
    dispatch({ type: "open_changed", open: true });
  };

  return {
    state,
    actions: {
      setQuery: (query) => dispatch({ type: "query_changed", query }),
      setOpen: (open) => dispatch({ type: "open_changed", open }),
      sendMessage,
      authenticate,
      selectResult,
      handoff,
      closeSession,
      reopen,
    },
  };
}
