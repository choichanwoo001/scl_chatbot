import test from "node:test";
import assert from "node:assert/strict";

import { parseStructuredText } from "../src/chat/structuredText.js";


test("keeps a short answer as a single paragraph", () => {
  assert.deepEqual(parseStructuredText("검사 정보를 안내합니다."), [
    { type: "paragraph", text: "검사 정보를 안내합니다." },
  ]);
});


test("groups headings, bullets, facts, and callouts into readable blocks", () => {
  const blocks = parseStructuredText(`HPV 검사 정보를 확인했습니다.

## 핵심 정보
- 검체는 자궁경부 세포입니다.
- 전용 용기를 사용합니다.

검사일: 월~금
소요일: 3일

주의: 실제 의뢰 전 원문을 확인하세요.`);

  assert.deepEqual(blocks, [
    { type: "paragraph", text: "HPV 검사 정보를 확인했습니다." },
    { type: "heading", level: 2, text: "핵심 정보" },
    { type: "bullet-list", items: ["검체는 자궁경부 세포입니다.", "전용 용기를 사용합니다."] },
    { type: "facts", items: [
      { label: "검사일", value: "월~금" },
      { label: "소요일", value: "3일" },
    ] },
    { type: "callout", label: "주의", text: "실제 의뢰 전 원문을 확인하세요." },
  ]);
});


test("groups numbered steps without keeping their source numbers", () => {
  assert.deepEqual(parseStructuredText("1. 검사를 선택합니다.\n2) 상세 정보를 확인합니다."), [
    { type: "ordered-list", items: ["검사를 선택합니다.", "상세 정보를 확인합니다."] },
  ]);
});
