# Round2 100 Case Real Model Final Summary

- Initial full run: 100 cases, 70 pass / 26 warn / 4 fail.
- Focused rerun 1: 30 problematic cases, 12 pass / 17 warn / 1 fail.
- Focused rerun 2: 18 remaining cases, 16 pass / 2 warn / 0 fail.
- Focused rerun 3: 2 remaining cases, 2 pass / 0 warn / 0 fail.
- Final effective result: 100 pass / 0 warn / 0 fail.

## Generic Fixes

- Narrowed natural communication repair so friend, group, thanks, and boundary requests do not collapse into apology templates.
- Split fact-check repair by topic: screenshot, sample bias, official/media conflict, investment, privacy, and health-product boundaries.
- Strengthened visible replies for reading re-entry, compact summaries, webpage summary, and research extraction.
- Added semantic aliases and short-form scoring rules to avoid false warnings on equivalent natural wording.

## Verification

- Unit tests: `pytest apps/local-api/tests/test_feishu_broad_repairs.py -q`, 67 passed.
- Real model endpoint: `http://127.0.0.1:8317/v1`.
- Rerun policy: after each generic fix, only warn/fail cases were rerun.