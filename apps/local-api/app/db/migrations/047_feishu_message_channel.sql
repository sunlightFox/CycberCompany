CREATE TABLE IF NOT EXISTS feishu_connections (
  feishu_connection_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  channel_account_id TEXT NOT NULL,
  channel_id TEXT,
  app_id_redacted TEXT NOT NULL,
  tenant_key_redacted TEXT,
  bot_open_id_redacted TEXT,
  transport_mode TEXT NOT NULL DEFAULT 'websocket',
  status TEXT NOT NULL DEFAULT 'configured',
  connection_state TEXT NOT NULL DEFAULT 'disconnected',
  permission_snapshot_json TEXT NOT NULL DEFAULT '{}',
  capability_snapshot_json TEXT NOT NULL DEFAULT '{}',
  last_event_id_redacted TEXT,
  last_heartbeat_at TEXT,
  last_connected_at TEXT,
  last_disconnected_at TEXT,
  last_error_code TEXT,
  last_error_summary TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(channel_account_id) REFERENCES channel_accounts(channel_account_id),
  FOREIGN KEY(channel_id) REFERENCES notification_channels(channel_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_feishu_connections_account
ON feishu_connections(channel_account_id);

CREATE INDEX IF NOT EXISTS idx_feishu_connections_state
ON feishu_connections(status, connection_state, updated_at);

CREATE TABLE IF NOT EXISTS feishu_event_records (
  feishu_event_record_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  channel_account_id TEXT NOT NULL,
  channel_event_id TEXT,
  provider_event_id_redacted TEXT NOT NULL,
  event_type TEXT NOT NULL,
  message_type TEXT,
  chat_id_redacted TEXT,
  sender_id_redacted TEXT,
  message_id_redacted TEXT,
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  normalized_event_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  trace_id TEXT,
  received_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(channel_account_id) REFERENCES channel_accounts(channel_account_id),
  FOREIGN KEY(channel_event_id) REFERENCES channel_events(channel_event_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_feishu_event_records_dedupe
ON feishu_event_records(channel_account_id, provider_event_id_redacted);

CREATE INDEX IF NOT EXISTS idx_feishu_event_records_type
ON feishu_event_records(event_type, status, created_at);

CREATE TABLE IF NOT EXISTS feishu_message_operations (
  feishu_operation_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  channel_account_id TEXT NOT NULL,
  channel_id TEXT,
  provider_message_id_redacted TEXT,
  operation TEXT NOT NULL,
  request_summary_json TEXT NOT NULL DEFAULT '{}',
  response_summary_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  error_code TEXT,
  error_summary TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(channel_account_id) REFERENCES channel_accounts(channel_account_id),
  FOREIGN KEY(channel_id) REFERENCES notification_channels(channel_id)
);

CREATE INDEX IF NOT EXISTS idx_feishu_message_operations_account
ON feishu_message_operations(channel_account_id, operation, created_at);
