# Minimal prompt baseline continuation

Status: completed; source: collector_inputs; cumulative cap $150.
Same Astra/high, minimal prompts, observations and controller. Local replay of 1060 saved CH30 decisions is not new inference or new expense.
Exposed development data. Stepwise objects are supplied; no KEEP/NOT_MERGE reference labels. Terminal matching is many-to-one and can under-penalize fragmentation.

| Evaluation | API calls | Conservative USD | Standard estimate USD |
|---|---:|---:|---:|
| stepwise | 294 | 12.084431 | 10.425039 |
| full_rollout | 2754 | 107.866573 | 94.284763 |

| Dataset | Stepwise correct / eligible | Rollout status | Precision | Recall | F1 |
|---|---:|---|---:|---:|---:|
| cM2-e004_004-006_CH31 | 14/54 | completed | 0.3870 | 1.0000 | 0.5581 |
| cM2-e004_004-006_CH3 | 37/97 | completed | 0.1995 | 1.0000 | 0.3327 |
| cM2-e004_011-015_CH20 | 19/53 | completed | 0.2205 | 1.0000 | 0.3613 |
| cM2-e008_021-028_CH30 | 32/90 | completed | 0.1946 | 0.8742 | 0.3183 |

Stop/error: None. Partial trajectories are not terminal scores. Costs are estimates, not invoices.
Original reports, data and frozen code are unchanged. Detailed tokens, checkpoint provenance and failure cases are in score.json and provider_ledger.jsonl.

Prior completed results: minimal_astra_high_dual_20261007_retry503. Inherited unresolved 503 reserve $0.2441875 is included, not confirmed spend. Calls count attempts.
