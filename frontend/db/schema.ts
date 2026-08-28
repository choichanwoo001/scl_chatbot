import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const chatSessions = sqliteTable("chat_sessions", {
  sessionId: text("session_id").primaryKey(),
  lastTestJson: text("last_test_json"),
  createdAt: text("created_at").notNull(),
  updatedAt: text("updated_at").notNull(),
}, (table) => [index("idx_chat_sessions_updated_at").on(table.updatedAt)]);

export const handoffRequests = sqliteTable("handoff_requests", {
  publicId: text("public_id").primaryKey(),
  sessionHash: text("session_hash").notNull(),
  inquiryType: text("inquiry_type").notNull(),
  requesterNameEncrypted: text("requester_name_encrypted").notNull(),
  phoneEncrypted: text("phone_encrypted").notNull(),
  organizationEncrypted: text("organization_encrypted"),
  contentEncrypted: text("content_encrypted").notNull(),
  relatedRefsJson: text("related_refs_json").notNull(),
  status: text("status").notNull(),
  consentedAt: text("consented_at").notNull(),
  createdAt: text("created_at").notNull(),
}, (table) => [index("idx_handoff_requests_created_at").on(table.createdAt)]);

export const geminiDailyUsage = sqliteTable("gemini_daily_usage", {
  day: text("day").primaryKey(),
  calls: integer("calls").notNull().default(0),
});
