ALTER TABLE chat_turn_queue
ADD COLUMN steering_diagnostics_json TEXT NOT NULL DEFAULT '{}';
