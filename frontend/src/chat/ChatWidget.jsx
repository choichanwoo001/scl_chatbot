import { useEffect, useRef } from "react";
import { Bot, MessageCircleMore, Minus, Send, ShieldCheck, X } from "lucide-react";
import { FeedbackControls } from "./ChatForms.jsx";
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
        <div><strong>SCL 챗봇</strong><span><i className={state.connectionMode === "openai" ? "is-live" : ""} /> {state.connectionMode === "openai" ? "OpenAI 실시간 전용" : state.connectionMode === "checking" ? "연결 확인 중" : "실시간 연결 오류"} · 시연용</span></div>
        <div className="chat-header-actions">
          <button type="button" onClick={() => actions.setOpen(false)} aria-label="챗봇 최소화"><Minus size={20} /></button>
          <button type="button" onClick={actions.closeSession} aria-label="채팅 세션 종료"><X size={20} /></button>
        </div>
      </header>
      <div className="safety-strip"><ShieldCheck size={15} /> OpenAI 실시간 생성 · SCL 공개 RDB 근거 · fallback 미사용</div>
      <div className="message-list" ref={listRef} aria-live="polite">
        {state.messages.map((message) => (
          <div className={`message ${message.role}`} key={message.id}>
            {message.role === "assistant" && <span className="message-avatar"><Bot size={17} /></span>}
            <div className="message-bubble">
              <MessageBody message={message} onQuickQuestion={actions.sendMessage} onAuthenticate={actions.authenticate}
                onHandoff={actions.handoff} onResultSelect={actions.selectResult} />
              {message.liveGenerated && (
                <div className="answer-provenance">
                  실시간 OpenAI · {message.dataStatus === "public_document" ? "문서 RDB" : message.dataStatus === "public_database" ? "검사 RDB" : "정책 응답"}
                  {message.sourceRefs?.length ? ` · 근거 ${message.sourceRefs.length}건` : ""}
                </div>
              )}
              {message.feedbackEligible && <FeedbackControls message={message} onFeedback={actions.feedback} />}
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
