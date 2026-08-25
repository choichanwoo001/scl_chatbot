import { useState } from "react";
import { ShieldCheck } from "lucide-react";

export function ResultAuthForm({ onAuthenticate }) {
  const [values, setValues] = useState({ user_id: "", password: "", identity_value: "" });
  const [status, setStatus] = useState({ sending: false, error: null });
  const update = (field) => (event) => setValues((current) => ({ ...current, [field]: event.target.value }));
  const submit = async (event) => {
    event.preventDefault();
    setStatus({ sending: true, error: null });
    try {
      await onAuthenticate(values);
      setValues({ user_id: "", password: "", identity_value: "" });
      setStatus({ sending: false, error: null });
    } catch (error) {
      setValues((current) => ({ ...current, password: "" }));
      setStatus({ sending: false, error: error instanceof Error ? error.message : "인증에 실패했습니다." });
    }
  };
  return (
    <form className="chat-inline-form" onSubmit={submit} autoComplete="off">
      <p className="form-security-note"><ShieldCheck size={14} /> 인증정보는 AI와 대화 기록에 저장되지 않습니다.</p>
      <label>아이디<input value={values.user_id} onChange={update("user_id")} autoComplete="username" required /></label>
      <label>비밀번호<input type="password" value={values.password} onChange={update("password")} autoComplete="current-password" required /></label>
      <label>본인확인 정보<input type="password" value={values.identity_value} onChange={update("identity_value")} placeholder="연동기관 요구값" required /></label>
      {status.error && <p className="form-error" role="alert">{status.error}</p>}
      <button type="submit" disabled={status.sending}>{status.sending ? "인증 중…" : "결과 조회"}</button>
    </form>
  );
}

export function HandoffForm({ onSubmit }) {
  const [values, setValues] = useState({
    inquiry_type: "general", requester_name: "", phone: "", organization: "", content: "", consent: false,
  });
  const [status, setStatus] = useState({ sending: false, error: null, submitted: false });
  const update = (field) => (event) => setValues((current) => ({
    ...current,
    [field]: event.target.type === "checkbox" ? event.target.checked : event.target.value,
  }));
  const submit = async (event) => {
    event.preventDefault();
    setStatus({ sending: true, error: null, submitted: false });
    try {
      await onSubmit(values);
      setStatus({ sending: false, error: null, submitted: true });
    } catch (error) {
      setStatus({ sending: false, error: error instanceof Error ? error.message : "접수에 실패했습니다.", submitted: false });
    }
  };
  if (status.submitted) return <p className="form-success">상담 문의가 접수되었습니다.</p>;
  return (
    <form className="chat-inline-form" onSubmit={submit}>
      <label>문의 유형<select value={values.inquiry_type} onChange={update("inquiry_type")}>
        <option value="general">일반 상담</option><option value="test_request">검사의뢰</option>
        <option value="specimen_shipping">검체 배송</option><option value="result_issue">결과 오류</option>
        <option value="complaint">불편 접수</option><option value="business">사업 문의</option>
      </select></label>
      <label>이름<input value={values.requester_name} onChange={update("requester_name")} required /></label>
      <label>연락처<input type="tel" value={values.phone} onChange={update("phone")} placeholder="010-0000-0000" required /></label>
      <label>기관명 <small>선택</small><input value={values.organization} onChange={update("organization")} /></label>
      <label>문의 내용<textarea value={values.content} onChange={update("content")} maxLength={2000} required /></label>
      <label className="form-consent"><input type="checkbox" checked={values.consent} onChange={update("consent")} required /> 상담 접수를 위한 개인정보 수집에 동의합니다.</label>
      {status.error && <p className="form-error" role="alert">{status.error}</p>}
      <button type="submit" disabled={status.sending}>{status.sending ? "접수 중…" : "채팅에서 상담 접수"}</button>
    </form>
  );
}
