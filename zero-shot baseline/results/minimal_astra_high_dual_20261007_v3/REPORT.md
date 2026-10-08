# Minimal-prompt baseline

Run: `minimal_astra_high_dual_20261007_v3`; status: **stopped**.

Astra / high; approved minimal prompts; no domain skill, examples or feedback. Exposed development channels.
Existing controller scheduling, four Phase 1 images / three Phase 2 images; automatic size deletion disabled.
Manifest SHA256: `0c17400642c88101c0b8239f22f67c1dbf65ee18e13d96c5d7bd5fba090b786b`.
Calls: 1242; known standard estimate: $43.438431; conservative usage/reservations: $49.779133.

## Stepwise action evaluation

Given expert pre-action states and source/target objects; predictions never advance this trajectory. No KEEP/NOT_MERGE reference labels; MERGE positives only.
Reference actions: 295; eligible: 294; pre-registered exclusions: [{"dataset_id": "cM2-e004_011-015_CH20", "trajectory_step": 5, "expert_action_raw": "s 0", "state_before_sha256": "41f4e313220a346373e32cd097499ff444ff204daf2fa707bce36120fd923132", "state_after_sha256": "7c62bda79e773dac4bc9fefde014ead5ef31a0731373b58090d5d2b3c4f21993", "reason": "Noise-cluster recovery (s 0) is outside the active-cluster actor interface; replayed only to reconstruct subsequent expert states."}]

| Dataset | Valid/planned | Correct | Full accuracy | Valid-only accuracy |
|---|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 54/54 | 14 | 0.25925925925925924 | 0.25925925925925924 |
| cM2-e004_004-006_CH3 | 97/97 | 37 | 0.38144329896907214 | 0.38144329896907214 |
| cM2-e004_011-015_CH20 | 53/53 | 19 | 0.3584905660377358 | 0.3584905660377358 |
| cM2-e008_021-028_CH30 | 90/90 | 32 | 0.35555555555555557 | 0.35555555555555557 |

### phase1

Coverage: 284/284; accuracy: 0.33098591549295775

| Action | Evaluated support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| DISCARD | 158 | 1.0 | 0.03164556962025317 | 0.06134969325153374 |
| SPLIT | 126 | 0.7542372881355932 | 0.7063492063492064 | 0.7295081967213115 |
| MERGE | 0 | None | None | None |
| KEEP | 0 | 0.0 | None | None |
| NOT_MERGE | 0 | None | None | None |

Confusion matrix (expert rows, model columns):

| Expert / Model | DISCARD | SPLIT | MERGE | KEEP | NOT_MERGE |
|---|---:|---:|---:|---:|---:|
| DISCARD | 5 | 29 | 0 | 124 | 0 |
| SPLIT | 0 | 89 | 0 | 37 | 0 |
| MERGE | 0 | 0 | 0 | 0 | 0 |
| KEEP | 0 | 0 | 0 | 0 | 0 |
| NOT_MERGE | 0 | 0 | 0 | 0 | 0 |

### phase2

Coverage: 10/10; accuracy: 0.8

| Action | Evaluated support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| DISCARD | 0 | None | None | None |
| SPLIT | 0 | None | None | None |
| MERGE | 10 | 1.0 | 0.8 | 0.8888888888888888 |
| KEEP | 0 | None | None | None |
| NOT_MERGE | 0 | 0.0 | None | None |

Confusion matrix (expert rows, model columns):

| Expert / Model | DISCARD | SPLIT | MERGE | KEEP | NOT_MERGE |
|---|---:|---:|---:|---:|---:|
| DISCARD | 0 | 0 | 0 | 0 | 0 |
| SPLIT | 0 | 0 | 0 | 0 | 0 |
| MERGE | 0 | 0 | 8 | 0 | 2 |
| KEEP | 0 | 0 | 0 | 0 | 0 |
| NOT_MERGE | 0 | 0 | 0 | 0 | 0 |

## Autonomous rollout

| Dataset | Status | Units | Spikes | P | R | F1 | Initial F1 |
|---|---|---:|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | completed | 50 | 130051 | 0.3870 | 1.0000 | 0.5581 | 0.5048 |
| cM2-e004_004-006_CH3 | completed | 66 | 92030 | 0.1995 | 1.0000 | 0.3327 | 0.2440 |
| cM2-e004_011-015_CH20 | stopped | — | — | — | — | — | — |
| cM2-e008_021-028_CH30 | not-started | — | — | — | — | — | — |

Elapsed seconds: 14612.0. Stop/error: `{'type': 'ControllerStop', 'cause': 'BudgetStop'}`.
Stepwise accounting at completion/stop: `{'calls': 294, 'conservative_usd': 12.084431499999944, 'standard_usd': 10.425039000000002, 'tokens': {'input_tokens': 667876, 'output_tokens': 75667, 'cached_input_tokens': 4119}, 'seconds': 2664.7701776570175}`. Remaining accounting belongs to rollout; shared total budget.
Partial runs are not final scores. Matching is many-to-one; fragmentation can be under-penalized. Not a SOTA claim.
Local detailed records: `../../runs/minimal_astra_high_dual_20261007_v3/score.json` and per-step images, responses, checkpoints and cost ledger.
No comparison to old conditions is a prompt-only causal result. No automatic prompt update or retry.
