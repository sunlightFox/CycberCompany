UPDATE brains
SET
  model_name = 'gpt-5.4-mini',
  privacy_policy_json = json_set(
    json_set(
      COALESCE(NULLIF(privacy_policy_json, ''), '{}'),
      '$.reasoning_effort',
      'medium'
    ),
    '$.text_verbosity',
    'medium'
  ),
  last_error_code = NULL,
  last_error_message = NULL,
  latency_ms = NULL,
  updated_at = CURRENT_TIMESTAMP
WHERE brain_id = 'brain_not_configured'
  AND display_name IN ('Codex Default Brain', 'Codex 默认大脑')
  AND model_name = 'gpt-5.5';
