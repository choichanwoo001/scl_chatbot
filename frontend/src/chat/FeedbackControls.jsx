import { useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";

const REASONS = [
  ["missing_information", "필요한 정보가 빠졌어요"],
  ["wrong_result", "결과가 맞지 않아요"],
  ["hard_to_understand", "이해하기 어려워요"],
  ["source_problem", "출처가 부족해요"],
  ["other", "기타"],
];

export function FeedbackControls({ message, onSubmit }) {
  const [status, setStatus] = useState("idle");
  const [rating, setRating] = useState(null);
  const [reason, setReason] = useState(REASONS[0][0]);
  const [comment, setComment] = useState("");

  if (!message.feedbackContext) return null;
  if (status === "sent") {
    return <p className="feedback-thanks" role="status">의견을 반영할 수 있도록 저장했습니다.</p>;
  }

  const send = async (nextRating, details = {}) => {
    setStatus("sending");
    try {
      await onSubmit(message, { rating: nextRating, ...details });
      setStatus("sent");
    } catch {
      setStatus("error");
    }
  };

  return (
    <div className="feedback-controls">
      <div className="feedback-actions" aria-label="답변 평가">
        <span>이 답변이 도움됐나요?</span>
        <button type="button" onClick={() => send("helpful")} disabled={status === "sending"} aria-label="도움됨">
          <ThumbsUp size={14} /> 도움됨
        </button>
        <button type="button" onClick={() => { setRating("not_helpful"); setStatus("idle"); }} aria-label="아쉬움">
          <ThumbsDown size={14} /> 아쉬움
        </button>
      </div>
      {rating === "not_helpful" ? (
        <form className="feedback-detail" onSubmit={(event) => {
          event.preventDefault();
          void send("not_helpful", { reason, comment: comment.trim() || null });
        }}>
          <label>아쉬운 이유
            <select value={reason} onChange={(event) => setReason(event.target.value)}>
              {REASONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </label>
          <label>추가 의견 <small>선택</small>
            <textarea value={comment} onChange={(event) => setComment(event.target.value.slice(0, 500))} maxLength={500} />
          </label>
          {status === "error" ? <p className="form-error" role="alert">의견을 저장하지 못했습니다. 다시 시도해 주세요.</p> : null}
          <button type="submit" disabled={status === "sending"}>{status === "sending" ? "저장 중…" : "의견 보내기"}</button>
        </form>
      ) : null}
    </div>
  );
}
