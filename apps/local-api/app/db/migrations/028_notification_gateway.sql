CREATE TABLE IF NOT EXISTS notification_channels (
  channel_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  asset_id TEXT,
  provider TEXT NOT NULL,
  display_name TEXT NOT NULL,
  channel_type TEXT NOT NULL,
  status TEXT NOT NULL,
  sensitivity TEXT NOT NULL DEFAULT 'medium',
  policy_json TEXT NOT NULL DEFAULT '{}',
  provider_config_json TEXT NOT NULL DEFAULT '{}',
  last_health_status TEXT,
  last_error TEXT,
  created_by_member_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(asset_id) REFERENCES assets(asset_id)
);

CREATE INDEX IF NOT EXISTS idx_notification_channels_status
ON notification_channels(status, provider);

CREATE INDEX IF NOT EXISTS idx_notification_channels_asset
ON notification_channels(asset_id);

CREATE TABLE IF NOT EXISTS notification_messages (
  notification_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  task_id TEXT,
  scheduled_task_id TEXT,
  scheduled_run_id TEXT,
  approval_id TEXT,
  message_type TEXT NOT NULL,
  recipient TEXT NOT NULL,
  status TEXT NOT NULL,
  subject_redacted TEXT,
  body_redacted TEXT NOT NULL,
  dlp_summary_json TEXT NOT NULL DEFAULT '{}',
  provider_message_id TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 3,
  next_retry_at TEXT,
  failure_reason TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sent_at TEXT,
  FOREIGN KEY(channel_id) REFERENCES notification_channels(channel_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(approval_id) REFERENCES approvals(approval_id),
  FOREIGN KEY(scheduled_task_id) REFERENCES scheduled_tasks(scheduled_task_id),
  FOREIGN KEY(scheduled_run_id) REFERENCES scheduled_task_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_notification_messages_channel_status
ON notification_messages(channel_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_notification_messages_task
ON notification_messages(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_notification_messages_approval
ON notification_messages(approval_id, created_at);

CREATE TABLE IF NOT EXISTS notification_delivery_attempts (
  attempt_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  notification_id TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  attempt_index INTEGER NOT NULL,
  status TEXT NOT NULL,
  request_summary_json TEXT NOT NULL DEFAULT '{}',
  response_summary_json TEXT NOT NULL DEFAULT '{}',
  error_code TEXT,
  error_summary TEXT,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(notification_id) REFERENCES notification_messages(notification_id),
  FOREIGN KEY(channel_id) REFERENCES notification_channels(channel_id)
);

CREATE INDEX IF NOT EXISTS idx_notification_attempts_message
ON notification_delivery_attempts(notification_id, created_at);

CREATE TABLE IF NOT EXISTS inbound_messages (
  inbound_message_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  sender_ref TEXT NOT NULL,
  provider_message_id TEXT,
  received_at TEXT NOT NULL,
  content_redacted TEXT NOT NULL,
  parsed_intent TEXT NOT NULL,
  binding_status TEXT NOT NULL,
  matched_approval_id TEXT,
  matched_task_id TEXT,
  action_result_json TEXT NOT NULL DEFAULT '{}',
  risk_summary_json TEXT NOT NULL DEFAULT '{}',
  untrusted_external_content INTEGER NOT NULL DEFAULT 1,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(channel_id) REFERENCES notification_channels(channel_id),
  FOREIGN KEY(matched_approval_id) REFERENCES approvals(approval_id),
  FOREIGN KEY(matched_task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_inbound_messages_channel
ON inbound_messages(channel_id, created_at);

CREATE INDEX IF NOT EXISTS idx_inbound_messages_approval
ON inbound_messages(matched_approval_id);

CREATE TABLE IF NOT EXISTS inbound_message_events (
  event_id TEXT PRIMARY KEY,
  inbound_message_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(inbound_message_id) REFERENCES inbound_messages(inbound_message_id)
);

CREATE INDEX IF NOT EXISTS idx_inbound_message_events_message
ON inbound_message_events(inbound_message_id, created_at);

CREATE TABLE IF NOT EXISTS notification_subscriptions (
  subscription_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT,
  event_types_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  policy_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(channel_id) REFERENCES notification_channels(channel_id)
);

CREATE INDEX IF NOT EXISTS idx_notification_subscriptions_channel
ON notification_subscriptions(channel_id, status);
