CREATE TABLE IF NOT EXISTS dialogue_states (
  dialogue_state_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL UNIQUE,
  member_id TEXT NOT NULL,
  active_topic TEXT,
  user_goal TEXT,
  goal_status TEXT NOT NULL DEFAULT 'active',
  goal_history_json TEXT NOT NULL DEFAULT '[]',
  known_constraints_json TEXT NOT NULL DEFAULT '[]',
  soft_preferences_json TEXT NOT NULL DEFAULT '[]',
  hard_constraints_json TEXT NOT NULL DEFAULT '[]',
  decisions_made_json TEXT NOT NULL DEFAULT '[]',
  open_questions_json TEXT NOT NULL DEFAULT '[]',
  pending_confirmation_json TEXT NOT NULL DEFAULT '{}',
  topic_shift INTEGER NOT NULL DEFAULT 0,
  last_user_action TEXT,
  candidate_next_actions_json TEXT NOT NULL DEFAULT '[]',
  referenced_memories_json TEXT NOT NULL DEFAULT '[]',
  referenced_artifacts_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 0.0,
  source_turn_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(source_turn_id) REFERENCES chat_turns(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_dialogue_states_member_status
ON dialogue_states(member_id, goal_status, updated_at);

CREATE INDEX IF NOT EXISTS idx_dialogue_states_source_turn
ON dialogue_states(source_turn_id);

CREATE TABLE IF NOT EXISTS semantic_intent_candidates (
  semantic_candidate_id TEXT PRIMARY KEY,
  brain_decision_id TEXT,
  turn_id TEXT,
  conversation_id TEXT,
  member_id TEXT NOT NULL,
  primary_intent TEXT NOT NULL,
  secondary_intents_json TEXT NOT NULL DEFAULT '[]',
  actionable_intents_json TEXT NOT NULL DEFAULT '[]',
  non_actionable_intents_json TEXT NOT NULL DEFAULT '[]',
  risk_intents_json TEXT NOT NULL DEFAULT '[]',
  memory_intents_json TEXT NOT NULL DEFAULT '[]',
  tool_intents_json TEXT NOT NULL DEFAULT '[]',
  skill_intents_json TEXT NOT NULL DEFAULT '[]',
  mcp_intents_json TEXT NOT NULL DEFAULT '[]',
  conversation_intents_json TEXT NOT NULL DEFAULT '[]',
  conflicts_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 0.0,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  model_hint_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(brain_decision_id) REFERENCES brain_decision_logs(brain_decision_id),
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_intent_candidates_turn
ON semantic_intent_candidates(turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_semantic_intent_candidates_decision
ON semantic_intent_candidates(brain_decision_id);

CREATE INDEX IF NOT EXISTS idx_semantic_intent_candidates_conversation
ON semantic_intent_candidates(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS low_confidence_decision_reviews (
  review_id TEXT PRIMARY KEY,
  brain_decision_id TEXT,
  turn_id TEXT,
  conversation_id TEXT,
  member_id TEXT NOT NULL,
  trigger_reasons_json TEXT NOT NULL DEFAULT '[]',
  rule_decision_json TEXT NOT NULL DEFAULT '{}',
  verifier_suggestion_json TEXT NOT NULL DEFAULT '{}',
  clarification_candidates_json TEXT NOT NULL DEFAULT '[]',
  fallback_used INTEGER NOT NULL DEFAULT 1,
  model_assist_enabled INTEGER NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0.0,
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(brain_decision_id) REFERENCES brain_decision_logs(brain_decision_id),
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_low_confidence_reviews_turn
ON low_confidence_decision_reviews(turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_low_confidence_reviews_decision
ON low_confidence_decision_reviews(brain_decision_id);

CREATE INDEX IF NOT EXISTS idx_low_confidence_reviews_status
ON low_confidence_decision_reviews(status, created_at);
