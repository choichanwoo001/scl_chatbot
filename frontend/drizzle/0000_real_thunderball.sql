CREATE TABLE `chat_sessions` (
	`session_id` text PRIMARY KEY NOT NULL,
	`last_test_json` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_chat_sessions_updated_at` ON `chat_sessions` (`updated_at`);--> statement-breakpoint
CREATE TABLE `handoff_requests` (
	`public_id` text PRIMARY KEY NOT NULL,
	`session_hash` text NOT NULL,
	`inquiry_type` text NOT NULL,
	`requester_name_encrypted` text NOT NULL,
	`phone_encrypted` text NOT NULL,
	`organization_encrypted` text,
	`content_encrypted` text NOT NULL,
	`related_refs_json` text NOT NULL,
	`status` text NOT NULL,
	`consented_at` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_handoff_requests_created_at` ON `handoff_requests` (`created_at`);