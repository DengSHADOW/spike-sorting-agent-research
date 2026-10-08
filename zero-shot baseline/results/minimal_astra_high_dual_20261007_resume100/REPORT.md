# Minimal prompt baseline continuation

Status: stopped; source: minimal_astra_high_dual_20261007_v3; cumulative cap $100.
Same Astra/high, minimal prompts, observations and controller. Local replay of 44 saved CH20 decisions is not new inference or new expense.
Exposed development data. Stepwise objects are supplied; no KEEP/NOT_MERGE reference labels. Terminal matching is many-to-one and can under-penalize fragmentation.

| Evaluation | API calls | Conservative USD | Standard estimate USD |
|---|---:|---:|---:|
| stepwise | 294 | 12.084431 | 10.425039 |
| full_rollout | 959 | 38.398902 | 33.416222 |

| Dataset | Stepwise correct / eligible | Rollout status | Precision | Recall | F1 |
|---|---:|---|---:|---:|---:|
| cM2-e004_004-006_CH31 | 14/54 | completed | 0.3870 | 1.0000 | 0.5581 |
| cM2-e004_004-006_CH3 | 37/97 | completed | 0.1995 | 1.0000 | 0.3327 |
| cM2-e004_011-015_CH20 | 19/53 | stopped | — | — | — |
| cM2-e008_021-028_CH30 | 32/90 | not-started | — | — | — |

Stop/error: {'type': 'ControllerStop', 'cause': 'InternalServerError'}. Partial trajectories are not terminal scores. Costs are estimates, not invoices.
Original reports, data and frozen code are unchanged. Detailed tokens, checkpoint provenance and failure cases are in score.json and provider_ledger.jsonl.
