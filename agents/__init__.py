"""Phase 3 agent package (docs/phase3-agent-prompts.md) -- prompt assembly,
player-pool selection, and run ingestion/validation for the five analyst
agents. Agent runs execute inside Claude Code sessions (D-006, zero API
spend); this package is everything AROUND the LLM run: the exact prompt
artifact under test, the bounded per-position pool, output validation, and
conversion into the [gsis_id, position, positional_rank, overall_rank]
candidate shape backtest/run_backtest.py scores.
"""
