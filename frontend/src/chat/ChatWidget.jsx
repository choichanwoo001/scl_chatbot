import { useEffect, useRef } from "react";
import { Bot, MessageCircleMore, Minus, Send, ShieldCheck, X } from "lucide-react";
import { MessageBody } from "./MessageBody.jsx";
import { useChatController } from "./useChatController.js";

const QUICK_QUESTIONS = [
  "갑상선 관련 검사 알려줘",
  "HPV 검사 용기와 소요일",
  "2026년 8월 연휴 검사일정 공문",
];

export function ChatWidget() {
  const { state, actions } = useChatController();
  const listRef = useRef(null);

  useEffect(() => {
    const element = listRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [state.messages]);

  const submit = (event) => {
    event.preventDefault();
    actions.sendMessage(state.query);
  };

  if (!state.open) {
    return (
      <button className="chat-launcher" type="button" onClick={actions.reopen} aria-label="SCL AI 챗봇 열기">
        <MessageCircleMore size={29} /><span>챗봇</span>
      </button>
    );
  }

  return (
    <aside className="chat-panel" aria-label="SCL AI 챗봇" data-testid="chat-panel">
      <header className="chat-header">
        <div className="bot-mark"><img src="/assets/scl/scl-logo.svg" alt="" /></div>
        <div><strong>SCL 챗봇</strong><span><i className={state.connectionMode === "gemini" ? "is-live" : ""} /> {state.connectionMode === "gemini" ? "Gemini 실시간" : ["demo_fallback", "deterministic"].includes(state.connectionMode) ? "서버 검색 모드" : state.connectionMode === "checking" ? "연결 확인 중" : "서버 연결 오류"} · 시연용</span></div>
        <div className="chat-header-actions">
          <button type="button" onClick={() => actions.setOpen(false)} aria-label="챗봇 최소화"><Minus size={20} /></button>
          <button type="button" onClick={actions.closeSession} aria-label="채팅 세션 종료"><X size={20} /></button>
        </div>
      </header>
      <div className="safety-strip"><ShieldCheck size={15} /> 검증된 SCL 자료 · 부족 시 도메인 제한 검색과 근거 링크 제공</div>
      <div className="message-list" ref={listRef} aria-live="polite">
        {state.messages.map((message) => (
          <div className={`message ${message.role}`} key={message.id}>
            {message.role === "assistant" && <span className="message-avatar"><Bot size={17} /></span>}
            <div className="message-bubble">
              <MessageBody message={message} onQuickQuestion={actions.sendMessage} onAuthenticate={actions.authenticate}
                onHandoff={(values) => actions.handoff(values, message)} onResultSelect={actions.selectResult} />
            </div>
          </div>
        ))}
        {state.isSending && (
          <div className="message assistant" aria-label="답변 생성 중">
            <span className="message-avatar"><Bot size={17} /></span>
            <div className="message-bubble typing-indicator"><i /><i /><i /></div>
          </div>
        )}
        {state.messages.length === 1 && <div className="quick-questions">{QUICK_QUESTIONS.map((question) => (
          <button type="button" key={question} onClick={() => actions.sendMessage(question)} disabled={state.isSending}>{question}</button>
        ))}</div>}
      </div>
      <form className="chat-composer" onSubmit={submit}>
        <label htmlFor="chat-input" className="sr-only">질문 입력</label>
        <textarea
          id="chat-input"
          value={state.query}
          disabled={state.isSending}
          onChange={(event) => actions.setQuery(event.target.value.slice(0, 300))}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit(event);
            }
          }}
          placeholder="검사명, 검체, 소요일 등을 자유롭게 물어보세요"
          rows="2"
        />
        <button type="submit" aria-label="질문 보내기" disabled={!state.query.trim() || state.isSending}><Send size={19} /></button>
        <small>{state.query.length}/300 · 개인정보와 검사결과 원문은 입력하지 마세요.</small>
      </form>
    </aside>
  );
}
