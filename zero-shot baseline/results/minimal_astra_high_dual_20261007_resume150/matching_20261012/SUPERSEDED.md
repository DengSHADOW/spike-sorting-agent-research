# Superseded — do not quote

This folder was produced on 2026-10-12 by an earlier version of `scripts/score_terminal_matching.py` that:

- used its own one-to-one implementation instead of the project's pre-registered core
  (`scripts/analysis/curation_one_to_one.py`, `configs/curation/one_to_one_scoring_v1.json`);
- listed many-to-one as the primary metric, contrary to the README (one-to-one primary, many-to-one supplementary);
- looked up MAT files and terminal states across all runs instead of only the scored run's lineage.

Use `../terminal_scores_20261012/TERMINAL_SCORES.md` instead. Kept for audit only.
