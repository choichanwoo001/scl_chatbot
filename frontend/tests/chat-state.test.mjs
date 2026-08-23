import test from "node:test";
import assert from "node:assert/strict";

import { chatReducer, createInitialChatState } from "../src/chat/chatState.js";

test("creates a live-chat state from a restored browser session", () => {
  const state = createInitialChatState({ sessionId: "saved", open: true, messages: [{ id: "saved" }] });
  assert.equal(state.sessionId, "saved");
  assert.deepEqual(state.messages, [{ id: "saved" }]);
  assert.equal(state.connectionMode, "checking");
});

test("records a successful live response atomically", () => {
  const initial = { ...createInitialChatState(null), isSending: true };
  const state = chatReducer(initial, {
    type: "send_succeeded", sessionId: "server-session", mode: "openai",
    userMessage: { id: "user" }, assistantMessage: { id: "assistant" },
  });
  assert.equal(state.sessionId, "server-session");
  assert.equal(state.connectionMode, "openai");
  assert.equal(state.isSending, false);
  assert.deepEqual(state.messages.slice(-2).map((message) => message.id), ["user", "assistant"]);
});

test("keeps an explicit error instead of synthesizing a fallback reply", () => {
  const initial = { ...createInitialChatState(null), isSending: true };
  const state = chatReducer(initial, {
    type: "send_failed", userMessage: { id: "user" }, errorMessage: { id: "error", error: true },
  });
  assert.equal(state.connectionMode, "unavailable");
  assert.equal(state.messages.at(-1).error, true);
});
