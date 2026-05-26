UPDATE brains
SET
  model_name = 'gpt-5.4-mini',
  last_error_code = NULL,
  last_error_message = NULL,
  latency_ms = NULL,
  updated_at = CURRENT_TIMESTAMP
WHERE brain_id = 'brain_not_configured'
  AND (
    provider IN ('openai', 'openai_compatible', 'custom_openai_compatible')
    OR display_name IN ('Codex Default Brain', 'Codex 默认大脑')
  );
