"""NFL Gold layer (stub) — builds the wide `nfl_matchup_features` table.

Same shape contract as MLB's `batter_matchup_features` (one row per
entity × event, a target, then form / opponent / context feature blocks, all
rolling windows obeying the `INTERVAL 1 DAY PRECEDING` leakage convention).
Only the block CONTENTS are sport-specific; the assembled shape and the
publish path (pipeline/publish.py) are reused unchanged.
"""
