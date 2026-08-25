import test from "node:test";
import assert from "node:assert/strict";

import { chatReducer, createInitialChatState } from "../src/chat/chatState.js";

test("creates a live-chat state from a restored browser session", () => {
  const state = createInitialChatState({ sessionId: "saved", open: true, messages: [{ id: "saved" }] });
  assert.equal(state.sessionId, "saved");
  assert.deepEqual(state.messages, [{ id: "saved" }]);
  assert.equal(state.connectionMode, "checking");
});

test("shows the user message as soon as sending starts", () => {
  const initial = createInitialChatState(null);
  const state = chatReducer(initial, {
    type: "send_started", userMessage: { id: "user", text: "질문" },
  });
  assert.equal(state.isSending, true);
  assert.equal(state.query, "");
  assert.deepEqual(state.messages.slice(-1).map((message) => message.id), ["user"]);
});

test("keeps the user message when a live response succeeds", () => {
  const started = chatReducer(createInitialChatState(null), {
    type: "send_started", userMessage: { id: "user" },
  });
  const state = chatReducer(started, {
    type: "send_succeeded", sessionId: "server-session", mode: "openai",
    assistantMessage: { id: "assistant" },
  });
  assert.equal(state.sessionId, "server-session");
  assert.equal(state.connectionMode, "openai");
  assert.equal(state.isSending, false);
  assert.deepEqual(state.messages.slice(-2).map((message) => message.id), ["user", "assistant"]);
});

test("keeps an explicit error instead of synthesizing a fallback reply", () => {
  const started = chatReducer(createInitialChatState(null), {
    type: "send_started", userMessage: { id: "user" },
  });
  const state = chatReducer(started, {
    type: "send_failed", errorMessage: { id: "error", error: true },
  });
  assert.equal(state.connectionMode, "unavailable");
  assert.deepEqual(state.messages.slice(-2).map((message) => message.id), ["user", "error"]);
  assert.equal(state.messages.at(-1).error, true);
});
