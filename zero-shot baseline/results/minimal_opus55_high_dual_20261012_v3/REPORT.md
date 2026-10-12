# Minimal-prompt baseline — Claude Opus 5.5

Run: `minimal_opus55_high_dual_20261012_v3`; status: **pilot-stopped**.

Claude Opus 5.5 / effort high / adaptive thinking; same minimal prompts, observations, images, schema and controller as the Astra run; no domain skill, examples, feedback or refusal fallback. Exposed development channels.
Manifest SHA256: `057b2e3816686a303936713026e7cf1285d45307eabadde5ae45bd03761c7070`.
Calls: 20; known standard estimate: $0.379752; conservative usage/reservations: $0.437595.
Output limit 8000 tokens (thinking included). Truncations: 0 (retried automatically once: 0; stopped after retry: 0). Live calls 20, replayed from a source run 0.

## Stepwise action evaluation

Given expert pre-action states and source/target objects; predictions never advance this trajectory. No KEEP/NOT_MERGE reference labels; MERGE positives only.

| Dataset | Valid/planned | Correct | Full accuracy |
|---|---:|---:|---:|
| cM2-e004_004-006_CH31 | 20/54 | 10 | None |
| cM2-e004_004-006_CH3 | 0/97 | 0 | None |
| cM2-e004_011-015_CH20 | 0/53 | 0 | None |
| cM2-e008_021-028_CH30 | 0/90 | 0 | None |

### phase1

| Action | Evaluated support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| DISCARD | 16 | 1.0 | 0.375 | 0.5454545454545454 |
| SPLIT | 4 | 0.4 | 1.0 | 0.5714285714285714 |
| MERGE | 0 | None | None | None |
| KEEP | 0 | 0.0 | None | None |
| NOT_MERGE | 0 | None | None | None |

### phase2

| Action | Evaluated support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| DISCARD | 0 | None | None | None |
| SPLIT | 0 | None | None | None |
| MERGE | 0 | None | None | None |
| KEEP | 0 | None | None | None |
| NOT_MERGE | 0 | None | None | None |

## Autonomous rollout

Dataset status: cM2-e004_004-006_CH31 not-started, cM2-e004_004-006_CH3 not-started, cM2-e004_011-015_CH20 not-started, cM2-e008_021-028_CH30 not-started. Only completed datasets are scored.

No completed rollout to score.

Partial runs are not final scores. Not a SOTA claim. No automatic prompt update or retry.
Stop/error: `None`. Local records: `../../runs/minimal_opus55_high_dual_20261012_v3/`.
