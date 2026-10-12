# Minimal-prompt baseline — Claude Opus 5.5

Run: `minimal_opus55_high_dual_20261012_v3_full`; status: **completed**.

Claude Opus 5.5 / effort high / adaptive thinking; same minimal prompts, observations, images, schema and controller as the Astra run; no domain skill, examples, feedback or refusal fallback. Exposed development channels.
Manifest SHA256: `057b2e3816686a303936713026e7cf1285d45307eabadde5ae45bd03761c7070`.
Calls: 1091; known standard estimate: $20.946076; conservative usage/reservations: $23.956375.
Output limit 8000 tokens (thinking included). Truncations: 0 (retried automatically once: 0; stopped after retry: 0). Live calls 1071, replayed from a source run 20; resumed from `minimal_opus55_high_dual_20261012_v3`.

## Stepwise action evaluation

Given expert pre-action states and source/target objects; predictions never advance this trajectory. No KEEP/NOT_MERGE reference labels; MERGE positives only.

| Dataset | Valid/planned | Correct | Full accuracy |
|---|---:|---:|---:|
| cM2-e004_004-006_CH31 | 54/54 | 24 | 0.4444444444444444 |
| cM2-e004_004-006_CH3 | 97/97 | 55 | 0.5670103092783505 |
| cM2-e004_011-015_CH20 | 53/53 | 28 | 0.5283018867924528 |
| cM2-e008_021-028_CH30 | 90/90 | 48 | 0.5333333333333333 |

### phase1

| Action | Evaluated support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| DISCARD | 158 | 0.925 | 0.23417721518987342 | 0.37373737373737376 |
| SPLIT | 126 | 0.6569767441860465 | 0.8968253968253969 | 0.7583892617449665 |
| MERGE | 0 | None | None | None |
| KEEP | 0 | 0.0 | None | None |
| NOT_MERGE | 0 | None | None | None |

### phase2

| Action | Evaluated support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| DISCARD | 0 | None | None | None |
| SPLIT | 0 | None | None | None |
| MERGE | 10 | 1.0 | 0.5 | 0.6666666666666666 |
| KEEP | 0 | None | None | None |
| NOT_MERGE | 0 | 0.0 | None | None |

## Autonomous rollout

Dataset status: cM2-e004_004-006_CH31 completed, cM2-e004_004-006_CH3 completed, cM2-e004_011-015_CH20 completed, cM2-e008_021-028_CH30 completed. Only completed datasets are scored.

One-to-one rules: `configs/curation/one_to_one_scoring_v1.json` (SHA256 `dc4085582f1e92846afe63405e80beee12b568837bbe98f05f551364de3b79a5`).

## Primary: one-to-one (IoU >= 0.5, Hungarian), full output

Precision uses every predicted spike, including unmatched clusters.

| Dataset | P | R | F1 | Expert units matched | Expert-unit macro accuracy | Predicted clusters | Unmatched clusters | Pure-noise clusters | Retained noise spikes | Discarded expert spikes | No-curation F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 0.5587 | 1.0000 | 0.7169 | 3 / 3 | 0.8755 | 28 | 25 | 25 | 39,754 | 0 | 0.4253 |
| cM2-e004_004-006_CH3 | 0.2628 | 1.0000 | 0.4162 | 2 / 2 | 0.7805 | 54 | 52 | 52 | 51,508 | 0 | 0.0000 |
| cM2-e004_011-015_CH20 | 0.0000 | 0.0000 | 0.0000 | 0 / 1 | 0.0000 | 7 | 7 | 5 | 509 | 17,857 | 0.0000 |
| cM2-e008_021-028_CH30 | 0.7602 | 0.5578 | 0.6435 | 1 / 3 | 0.3333 | 19 | 18 | 18 | 16,028 | 40,284 | 0.1953 |

Per expert unit (unmatched units score 0):

| Dataset | Expert unit | Spikes | Matched cluster | IoU (accuracy) | Best candidate IoU | P | R | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 1 | 7,272 | 1 | 0.6376 | 0.6376 | 0.6376 | 1.0000 | 0.7787 |
| cM2-e004_004-006_CH31 | 56 | 4,162 | 56 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e004_004-006_CH31 | 63 | 38,899 | 63 | 0.9889 | 0.9889 | 0.9889 | 1.0000 | 0.9944 |
| cM2-e004_004-006_CH3 | 4 | 13,782 | 4 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e004_004-006_CH3 | 15 | 4,582 | 15 | 0.5610 | 0.5610 | 0.5610 | 1.0000 | 0.7187 |
| cM2-e004_011-015_CH20 | 1 | 18,465 | — | 0.0000 | 0.0186 | 0.0000 | 0.0000 | 0.0000 |
| cM2-e008_021-028_CH30 | 2 | 28,823 | — | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| cM2-e008_021-028_CH30 | 31 | 50,821 | 31 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e008_021-028_CH30 | 38 | 11,461 | — | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

## Supplementary: historical many-to-one (not comparable with one-to-one)

| Dataset | Clusters | P | R | F1 | No-curation F1 |
|---|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 28 | 0.5587 | 1.0000 | 0.7169 | 0.5048 |
| cM2-e004_004-006_CH3 | 54 | 0.2628 | 1.0000 | 0.4162 | 0.2440 |
| cM2-e004_011-015_CH20 | 7 | 0.5443 | 0.0329 | 0.0621 | 0.3597 |
| cM2-e008_021-028_CH30 | 19 | 0.7602 | 0.5578 | 0.6435 | 0.3061 |

Do not quote expert-unit scores without the unmatched / pure-noise cluster counts in the same row, and do not average over matched units only.

Partial runs are not final scores. Not a SOTA claim. No automatic prompt update or retry.
Stop/error: `None`. Local records: `../../runs/minimal_opus55_high_dual_20261012_v3_full/`.
