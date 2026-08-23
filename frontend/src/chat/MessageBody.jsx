import { ExternalLink } from "lucide-react";
import { HandoffForm, ResultAuthForm } from "./ChatForms.jsx";

function TextReply({ message }) {
  return (
    <div>
      <p>{message.text}</p>
      {message.citations?.length ? (
        <div className="citation-list">
          {message.citations.map((citation) => citation.url ? (
            <a key={`${citation.title}-${citation.url}`} href={citation.url} target="_blank" rel="noreferrer">
              {citation.title} <ExternalLink size={12} />
            </a>
          ) : <span key={citation.title}>{citation.title}</span>)}
        </div>
      ) : null}
    </div>
  );
}

function TestReply({ message }) {
  return (
    <div className="test-result-card">
      <p>{message.text}</p>
      <div className="test-card-heading"><span>검사코드 {message.test.code}</span><strong>{message.test.name}</strong></div>
      <dl>
        <div><dt>검체/용기</dt><dd>{message.test.specimen}{message.test.container ? ` / ${message.test.container}` : ""}</dd></div>
        <div><dt>검사방법</dt><dd>{message.test.method}</dd></div>
        <div><dt>검사일</dt><dd>{message.test.schedule}</dd></div>
        <div><dt>소요일</dt><dd>{message.test.tat}</dd></div>
      </dl>
      {message.test.source_url ? (
        <a className="source-link" href={message.test.source_url} target="_blank" rel="noreferrer">검사 상세 보기 <ExternalLink size={14} /></a>
      ) : null}
      <p className="source-note">출처: {message.test.source_title || "SCL 검사항목 조회"} · {message.test.demo === false ? "공개 문서" : "데모 데이터"} {message.test.updated_at || "2026-08-15"}</p>
    </div>
  );
}

function ResultListReply({ message, onResultSelect }) {
  return <div><p>{message.text}</p><div className="result-list">{message.results.map((item) => (
    <button type="button" key={item.result_id} onClick={() => onResultSelect(item.result_id)}>
      <strong>{item.test_name}</strong><span>{item.requested_at} · {item.status}</span>
    </button>
  ))}</div></div>;
}

function ResultDetailReply({ message }) {
  return <div className="result-detail-card"><p>{message.detail.notice}</p><strong>{message.detail.test_name}</strong>
    <span>{message.detail.reported_at || message.detail.requested_at}</span>
    <dl>{message.detail.fields.map((field) => <div key={field.name}><dt>{field.name}</dt><dd>{field.value}</dd></div>)}</dl>
    <small>개인 결과에 대한 의학적 해석은 의료진 또는 상담 채널을 이용해 주세요.</small>
  </div>;
}

export function MessageBody({ message, onQuickQuestion, onAuthenticate, onHandoff, onResultSelect }) {
  switch (message.kind) {
    case "result_auth_form":
      return <div><p>{message.text}</p><ResultAuthForm onAuthenticate={onAuthenticate} /></div>;
    case "handoff_form":
      return <div><p>{message.text}</p><HandoffForm onSubmit={onHandoff} /></div>;
    case "result_list":
      return <ResultListReply message={message} onResultSelect={onResultSelect} />;
    case "result_detail":
      return <ResultDetailReply message={message} />;
    case "test":
      return <TestReply message={message} />;
    case "choices":
      return <div><p>{message.text}</p><div className="choice-list">
        {message.choices.map((choice) => <button type="button" key={choice} onClick={() => onQuickQuestion(choice)}>{choice}</button>)}
      </div></div>;
    default:
      return <TextReply message={message} />;
  }
}
