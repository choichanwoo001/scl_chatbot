# Design QA

**Source visual truth**

- Before-state reference: `C:\Users\jonad\AppData\Local\Temp\codex-clipboard-2c721550-e3f6-43a7-9513-c2ef62998e00.png`
- Source pixels: 320 × 86
- Requested target: replace each two-row test entry (title above, specimen/turnaround below) with one linked row formatted as `{test name} (검사코드 {code})`; keep additional explanation outside that row.

**Implementation evidence**

- Browser-rendered screenshot: current-turn Codex in-app Browser capture (displayed inline in the QA run; the browser surface does not expose a filesystem save path)
- Capture pixels: 698 × 888 at device pixel ratio 1
- App viewport: 698 × 888 CSS px
- State: chatbot open after submitting `25-(OH) Vit.D3`; ten related catalog tests visible
- Density normalization: both artifacts were reviewed at their native 1× density. The supplied image is a focused before-state crop, so comparison focused on the repeated test-entry region rather than full-frame coordinates.

**Findings**

- No actionable P0/P1/P2 issue remains.
- Fonts and typography: the existing Pretendard scale and weight are preserved. Every test entry is a single 33 px-high text row; the requested `25-(OH) Vit.D3 (RIA) (검사코드 51230)` label fits without wrapping or overflow.
- Spacing and layout rhythm: the former title-plus-detail pair is replaced by one compact linked row. Existing chatbot shell, summary card, composer, and surrounding page layout are unchanged.
- Colors and visual tokens: existing white surface, blue-gray text, subtle border, radius, hover, and focus tokens are reused.
- Image quality and asset fidelity: no new raster, vector, icon, or decorative asset was introduced.
- Copy and content: all ten visible catalog rows use `{test name} (검사코드 {code})`. The former `검체 / Serum · 소요일` rows and duplicate citation cards are removed from this result list; explanatory copy remains above the list.

**Full-view comparison evidence**

- The supplied before-state crop and the browser-rendered result were reviewed together. The requested structural change is visible: each catalog result now occupies one bordered linked row, with no paired specimen row.
- The chat panel remains scrollable and the composer remains visible and usable.

**Focused region comparison evidence**

- Browser inspection found ten `.test-title-link` elements, each 33 px high, each with a valid SCL detail URL, and none with horizontal overflow.
- Browser inspection found zero `.answer-facts` blocks and zero duplicate `.citation-list` blocks in the rendered catalog-result answer.
- Browser console warnings/errors checked: none.

**Comparison history**

1. Initial implementation path: structured live answers still rendered a test-title paragraph, a specimen/turnaround facts row, and a duplicate source link.
2. Fix: recognized catalog test entries in structured answers, moved the SCL detail URL onto the one-line test label, and removed only the paired facts and duplicate citation entries.
3. Post-fix evidence: all ten rows use the requested format and valid links, with no overflow and no console errors.

**Primary interactions tested**

- Submitted `25-(OH) Vit.D3` in the chatbot.
- Waited for the live catalog response and confirmed the requested `51230` entry and nine related results.
- Verified every displayed catalog row has a destination URL.
- Verified the chat composer remains usable.

**Implementation Checklist**

- [x] One-line test name/code format
- [x] Test detail URL attached to the row
- [x] Structured live-answer path normalized
- [x] Multi-choice path normalized
- [x] Single-test detail path normalized
- [x] Other chatbot and page UI preserved
- [x] Lint, unit tests, production build, and hosted worker tests pass

**Follow-up Polish**

- None required for the requested change.

final result: passed
