import test from "node:test";
import assert from "node:assert/strict";

import {
  CHAT_SESSION_KEY,
  clearChatSession,
  loadChatSession,
  saveChatSession,
} from "../src/lib/chatSession.js";


function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}


test("persists safe chat state within the browser tab", () => {
  const storage = memoryStorage();
  saveChatSession(storage, {
    sessionId: "session-1",
    open: true,
    messages: [{ id: "safe", role: "assistant", kind: "text", text: "안내" }],
  });

  const restored = loadChatSession(storage);

  assert.equal(restored.sessionId, "session-1");
  assert.equal(restored.messages[0].text, "안내");
});


test("never persists personal result messages", () => {
  const storage = memoryStorage();
  saveChatSession(storage, {
    sessionId: "session-1",
    open: true,
    messages: [
      { id: "safe", kind: "text", text: "안내" },
      { id: "list", kind: "result_list", results: [{ result_id: "private" }] },
      { id: "detail", kind: "result_detail", sensitive: true, detail: { value: "private" } },
    ],
  });

  const raw = storage.getItem(CHAT_SESSION_KEY);
  assert.equal(raw.includes("private"), false);
  assert.deepEqual(loadChatSession(storage).messages.map((item) => item.id), ["safe"]);
});


test("clears the tab session when chat is ended", () => {
  const storage = memoryStorage();
  storage.setItem(CHAT_SESSION_KEY, "stored");
  clearChatSession(storage);
  assert.equal(storage.getItem(CHAT_SESSION_KEY), null);
});
