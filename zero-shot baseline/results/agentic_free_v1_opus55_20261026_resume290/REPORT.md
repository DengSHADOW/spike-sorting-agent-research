# Agentic-free v1 rollout — claude-opus-5-5

Run: `agentic_free_v1_opus55_20261026_resume290`; campaign status: **completed**.

No scheduler: the model chose every INSPECT / COMPARE / KEEP / SPLIT / MERGE / DISCARD / FINISH. Renderer, executor and scoring are those of the engineering_v1 minimal rollouts, but this is a different condition: report it separately. No skill, examples, expert information or refusal fallback. Exposed development channels.
Manifest SHA256: `6e200b9225afb4e71a24edcf821737ca9a5ded0851fd5382642ed29e3b6dddb9`.
Calls: 1258; known standard estimate: $221.282544; conservative usage/reservations: $274.448550.
Resumed from `agentic_free_v1_opus55_20261026_resume270` (stopped: budget): cM2-e004_004-006_CH31, cM2-e004_004-006_CH3, cM2-e004_011-015_CH20 carried over unchanged; 1239 calls of cM2-e008_021-028_CH30 replayed byte-identically without billing (1239 consumed). Costs above are cumulative including the source run; calls are this run's (replayed + live). A resumed channel's own summary counts only its new live cost.

| Dataset | Status | Turns / limit | Actions | Not executed |
|---|---|---:|---|---:|
| cM2-e004_004-006_CH31 | completed | 543 / 3200 | DISCARD 34, FINISH 1, INSPECT 271, KEEP 112, SPLIT 125 | 0 |
| cM2-e004_004-006_CH3 | completed | 343 / 3200 | COMPARE 3, DISCARD 27, FINISH 1, INSPECT 169, KEEP 67, MERGE 3, SPLIT 72 | 1 |
| cM2-e004_011-015_CH20 | completed | 269 / 3200 | DISCARD 12, FINISH 1, INSPECT 134, KEEP 62, SPLIT 60 | 0 |
| cM2-e008_021-028_CH30 | completed | 1258 / 3200 | DISCARD 75, FINISH 1, INSPECT 629, KEEP 253, SPLIT 300 | 0 |

One-to-one rules: `configs/curation/one_to_one_scoring_v1.json` (SHA256 `dc4085582f1e92846afe63405e80beee12b568837bbe98f05f551364de3b79a5`).

## Primary: one-to-one (IoU >= 0.5, Hungarian), full output

Precision uses every predicted spike, including unmatched clusters.

| Dataset | P | R | F1 | Expert units matched | Expert-unit macro accuracy | Predicted clusters | Unmatched clusters | Pure-noise clusters | Retained noise spikes | Discarded expert spikes | No-curation F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 0.4051 | 1.0000 | 0.5766 | 3 / 3 | 0.9034 | 108 | 105 | 105 | 73,913 | 0 | 0.4253 |
| cM2-e004_004-006_CH3 | 0.2364 | 1.0000 | 0.3824 | 2 / 2 | 1.0000 | 67 | 65 | 65 | 59,330 | 0 | 0.0000 |
| cM2-e004_011-015_CH20 | 0.2559 | 0.9671 | 0.4048 | 1 / 1 | 0.9213 | 60 | 59 | 57 | 51,305 | 0 | 0.0000 |
| cM2-e008_021-028_CH30 | 0.2127 | 0.8873 | 0.3432 | 3 / 3 | 0.8471 | 243 | 240 | 234 | 288,955 | 0 | 0.1953 |

Per expert unit (unmatched units score 0):

| Dataset | Expert unit | Spikes | Matched cluster | IoU (accuracy) | Best candidate IoU | P | R | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 1 | 7,272 | 1 | 0.7103 | 0.7103 | 0.7103 | 1.0000 | 0.8306 |
| cM2-e004_004-006_CH31 | 56 | 4,162 | 56 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e004_004-006_CH31 | 63 | 38,899 | 63 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e004_004-006_CH3 | 4 | 13,782 | 4 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e004_004-006_CH3 | 15 | 4,582 | 15 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e004_011-015_CH20 | 1 | 18,465 | 1 | 0.9213 | 0.9213 | 0.9512 | 0.9671 | 0.9590 |
| cM2-e008_021-028_CH30 | 2 | 28,823 | 2 | 0.7117 | 0.7117 | 1.0000 | 0.7117 | 0.8316 |
| cM2-e008_021-028_CH30 | 31 | 50,821 | 31 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e008_021-028_CH30 | 38 | 11,461 | 41 | 0.8295 | 0.8295 | 1.0000 | 0.8295 | 0.9068 |

## Supplementary: historical many-to-one (not comparable with one-to-one)

| Dataset | Clusters | P | R | F1 | No-curation F1 |
|---|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 108 | 0.4051 | 1.0000 | 0.5766 | 0.5048 |
| cM2-e004_004-006_CH3 | 67 | 0.2364 | 1.0000 | 0.3824 | 0.2440 |
| cM2-e004_011-015_CH20 | 60 | 0.2647 | 1.0000 | 0.4185 | 0.3597 |
| cM2-e008_021-028_CH30 | 243 | 0.2397 | 1.0000 | 0.3867 | 0.3061 |

Do not quote expert-unit scores without the unmatched / pure-noise cluster counts in the same row, and do not average over matched units only.

Incomplete channels are not final scores. No automatic retry or prompt update.
Stop/error: `None`. Local records: `../../runs/agentic_free_v1_opus55_20261026_resume290/`.
