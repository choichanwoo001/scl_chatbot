import { ExternalLink } from "lucide-react";
import { HandoffForm, ResultAuthForm } from "./ChatForms.jsx";
import { parseStructuredText } from "./structuredText.js";

const SOURCE_LABELS = {
  internal_scl: "SCL 공개 자료",
};

function sourceHostname(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function GroundingStatus({ message }) {
  if (message.error || message.role !== "assistant") return null;
  if (message.grounding_status === "abstained" && message.answerability === "none") {
    return <p className="grounding-note is-abstained">확인 가능한 근거 없음 · 추측 답변 차단</p>;
  }
  return null;
}

function InlineText({ children }) {
  return String(children).split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={`${part}-${index}`}>{part.slice(1, -1)}</code>;
    }
    return part;
  });
}

function StructuredText({ text, emphasizeFirst = false }) {
  const blocks = parseStructuredText(text);
  const hasStructure = blocks.length > 1 || blocks.some((block) => block.type !== "paragraph");

  return (
    <div className="structured-answer">
      {blocks.map((block, index) => {
        if (block.type === "heading") {
          return <h3 className="answer-heading" key={`${block.type}-${index}`}><InlineText>{block.text}</InlineText></h3>;
        }
        if (block.type === "bullet-list" || block.type === "ordered-list") {
          const List = block.type === "ordered-list" ? "ol" : "ul";
          return <List className="answer-list" key={`${block.type}-${index}`}>
            {block.items.map((item, itemIndex) => <li key={`${item}-${itemIndex}`}><InlineText>{item}</InlineText></li>)}
          </List>;
        }
        if (block.type === "facts") {
          return <dl className="answer-facts" key={`${block.type}-${index}`}>
            {block.items.map((item, itemIndex) => <div key={`${item.label}-${item.value}-${itemIndex}`}>
              <dt>{item.label}</dt><dd><InlineText>{item.value}</InlineText></dd>
            </div>)}
          </dl>;
        }
        if (block.type === "callout") {
          return <aside className={`answer-callout ${block.label === "주의" || block.label === "중요" ? "is-warning" : ""}`} key={`${block.type}-${index}`}>
            <strong>{block.label}</strong><p><InlineText>{block.text}</InlineText></p>
          </aside>;
        }

        if (emphasizeFirst && index === 0 && hasStructure) {
          return <div className="answer-summary" key={`${block.type}-${index}`}>
            <span>답변 요약</span><p><InlineText>{block.text}</InlineText></p>
          </div>;
        }
        return <p className="answer-paragraph" key={`${block.type}-${index}`}><InlineText>{block.text}</InlineText></p>;
      })}
    </div>
  );
}

function splitSentences(text) {
  const paragraphs = String(text).split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean);
  if (paragraphs.length > 1) return paragraphs;

  const sentences = String(text).match(/[^.!?。！？]+[.!?。！？]?/g)
    ?.map((sentence) => sentence.trim())
    .filter(Boolean);
  return sentences?.length ? sentences : [String(text)];
}

function TestDescription({ text, testName }) {
  const sentences = splitSentences(text);
  const emphasizedTerm = testName?.replace(/^\([^)]*\)\s*/, "").trim();
  const guidanceIndex = sentences.findIndex((sentence, index) => (
    index > 0 && /(결과 확인|상담|문의|의뢰|폼)/.test(sentence)
  ));
  const summary = guidanceIndex > 0 ? sentences.slice(0, guidanceIndex) : sentences;
  const guidance = guidanceIndex > 0 ? sentences.slice(guidanceIndex) : [];

  const renderText = (sentence) => {
    if (!emphasizedTerm || !sentence.includes(emphasizedTerm)) return sentence;
    return sentence.split(emphasizedTerm).map((part, index) => (
      index === 0
        ? part
        : <span key={`${part}-${index}`}><strong className="test-term">{emphasizedTerm}</strong>{part}</span>
    ));
  };

  return (
    <div className="test-description">
      <div className="test-description-summary">
        {summary.map((sentence, index) => <p key={`${sentence}-${index}`}>{renderText(sentence)}</p>)}
      </div>
      {guidance.length ? (
        <aside className="test-description-guidance">
          <strong>결과 확인 안내</strong>
          <p>{guidance.map((sentence, index) => (
            <span key={`${sentence}-${index}`}>{index > 0 ? " " : ""}{renderText(sentence)}</span>
          ))}</p>
        </aside>
      ) : null}
    </div>
  );
}

function TextReply({ message }) {
  return (
    <div>
      {message.role === "assistant"
        ? <StructuredText text={message.text} emphasizeFirst={message.liveGenerated && !message.error} />
        : <p>{message.text}</p>}
      <GroundingStatus message={message} />
      {message.citations?.length ? (
        <div className="citation-list">
          {message.citations.map((citation) => citation.url ? (
            <a key={`${citation.title}-${citation.url}`} href={citation.url} target="_blank" rel="noreferrer">
              <span>{SOURCE_LABELS[citation.source_tier] || "출처"}</span>
              <strong>{citation.title}</strong>
              {sourceHostname(citation.url) ? <small>{sourceHostname(citation.url)}</small> : null}
              <ExternalLink size={12} />
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
      <TestDescription text={message.text} testName={message.test.name} />
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
  return <div><StructuredText text={message.text} /><div className="result-list">{message.results.map((item) => (
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
      return <div><StructuredText text={message.text} /><ResultAuthForm onAuthenticate={onAuthenticate} /></div>;
    case "handoff_form":
      return <div><StructuredText text={message.text} /><HandoffForm onSubmit={onHandoff} /></div>;
    case "result_list":
      return <ResultListReply message={message} onResultSelect={onResultSelect} />;
    case "result_detail":
      return <ResultDetailReply message={message} />;
    case "test":
      return <TestReply message={message} />;
    case "choices":
      return <div><StructuredText text={message.text} /><div className="choice-list">
        {message.choices.map((choice) => {
          const label = typeof choice === "string" ? choice : choice.label;
          const url = typeof choice === "string" ? null : choice.url;
          return url
            ? <a key={`${label}-${url}`} href={url} target="_blank" rel="noreferrer">{label} <ExternalLink size={13} /></a>
            : <button type="button" key={label} onClick={() => onQuickQuestion(label)}>{label}</button>;
        })}
      </div></div>;
    default:
      return <TextReply message={message} />;
  }
}
