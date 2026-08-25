const HEADING_PATTERN = /^(#{1,3})\s+(.+)$/;
const BULLET_PATTERN = /^[-*•]\s+(.+)$/;
const ORDERED_PATTERN = /^\d+[.)]\s+(.+)$/;
const CALLOUT_PATTERN = /^(주의|중요|참고|안내)\s*[:：]\s*(.+)$/;
const FACT_PATTERN = /^([^:：]{1,18})\s*[:：]\s+(.+)$/;

function appendGrouped(blocks, type, item) {
  const previous = blocks.at(-1);
  if (previous?.type === type) {
    previous.items.push(item);
    return;
  }
  blocks.push({ type, items: [item] });
}

export function parseStructuredText(value = "") {
  const blocks = [];
  let paragraph = [];

  const flushParagraph = () => {
    const text = paragraph.join(" ").trim();
    if (text) blocks.push({ type: "paragraph", text });
    paragraph = [];
  };

  for (const rawLine of String(value).replace(/\r\n?/g, "\n").split("\n")) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      continue;
    }

    const heading = line.match(HEADING_PATTERN);
    if (heading) {
      flushParagraph();
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2].trim() });
      continue;
    }

    const bullet = line.match(BULLET_PATTERN);
    if (bullet) {
      flushParagraph();
      appendGrouped(blocks, "bullet-list", bullet[1].trim());
      continue;
    }

    const ordered = line.match(ORDERED_PATTERN);
    if (ordered) {
      flushParagraph();
      appendGrouped(blocks, "ordered-list", ordered[1].trim());
      continue;
    }

    const callout = line.match(CALLOUT_PATTERN);
    if (callout) {
      flushParagraph();
      blocks.push({ type: "callout", label: callout[1], text: callout[2].trim() });
      continue;
    }

    const fact = line.match(FACT_PATTERN);
    if (fact) {
      flushParagraph();
      appendGrouped(blocks, "facts", { label: fact[1].trim(), value: fact[2].trim() });
      continue;
    }

    paragraph.push(line);
  }

  flushParagraph();
  return blocks;
}
