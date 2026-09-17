CREATE TABLE `chat_feedback` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`response_id` text,
	`session_hash` text NOT NULL,
	`rating` text NOT NULL,
	`reason` text,
	`comment` text,
	`redacted_question` text NOT NULL,
	`normalized_question` text NOT NULL,
	`answer_text` text NOT NULL,
	`domain` text,
	`sub_intent` text,
	`source_refs_json` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_chat_feedback_created_at` ON `chat_feedback` (`created_at`);--> statement-breakpoint
CREATE TABLE `faq_candidates` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`fingerprint` text NOT NULL,
	`canonical_question` text NOT NULL,
	`normalized_question` text NOT NULL,
	`canonical_answer` text NOT NULL,
	`domain` text,
	`sub_intent` text,
	`source_refs_json` text NOT NULL,
	`occurrence_count` integer DEFAULT 1 NOT NULL,
	`positive_count` integer DEFAULT 0 NOT NULL,
	`negative_count` integer DEFAULT 0 NOT NULL,
	`status` text DEFAULT 'draft' NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `faq_candidates_fingerprint_unique` ON `faq_candidates` (`fingerprint`);--> statement-breakpoint
CREATE INDEX `idx_faq_candidates_status` ON `faq_candidates` (`status`);