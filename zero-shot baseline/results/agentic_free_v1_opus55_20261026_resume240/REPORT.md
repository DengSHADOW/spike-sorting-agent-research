# Agentic-free v1 rollout — claude-opus-5-5

Run: `agentic_free_v1_opus55_20261026_resume240`; campaign status: **stopped**.

No scheduler: the model chose every INSPECT / COMPARE / KEEP / SPLIT / MERGE / DISCARD / FINISH. Renderer, executor and scoring are those of the engineering_v1 minimal rollouts, but this is a different condition: report it separately. No skill, examples, expert information or refusal fallback. Exposed development channels.
Manifest SHA256: `be537f9940a3a18ac980f066f4a6840f45d18c695016862422da2723c9c92081`.
Calls: 1114; known standard estimate: $193.372848; conservative usage/reservations: $239.733390.
Resumed from `agentic_free_v1_opus55_20261026_resume200` (stopped: budget): cM2-e004_004-006_CH31, cM2-e004_004-006_CH3, cM2-e004_011-015_CH20 carried over unchanged; 934 calls of cM2-e008_021-028_CH30 replayed byte-identically without billing (934 consumed). Costs above are cumulative including the source run; calls are this run's (replayed + live). A resumed channel's own summary counts only its new live cost.

| Dataset | Status | Turns / limit | Actions | Not executed |
|---|---|---:|---|---:|
| cM2-e004_004-006_CH31 | completed | 543 / 3200 | DISCARD 34, FINISH 1, INSPECT 271, KEEP 112, SPLIT 125 | 0 |
| cM2-e004_004-006_CH3 | completed | 343 / 3200 | COMPARE 3, DISCARD 27, FINISH 1, INSPECT 169, KEEP 67, MERGE 3, SPLIT 72 | 1 |
| cM2-e004_011-015_CH20 | completed | 269 / 3200 | DISCARD 12, FINISH 1, INSPECT 134, KEEP 62, SPLIT 60 | 0 |
| cM2-e008_021-028_CH30 | stopped | 1114 / 3200 | DISCARD 47, INSPECT 558, KEEP 229, SPLIT 280 | 0 |

One-to-one rules: `configs/curation/one_to_one_scoring_v1.json` (SHA256 `dc4085582f1e92846afe63405e80beee12b568837bbe98f05f551364de3b79a5`).

## Primary: one-to-one (IoU >= 0.5, Hungarian), full output

Precision uses every predicted spike, including unmatched clusters.

| Dataset | P | R | F1 | Expert units matched | Expert-unit macro accuracy | Predicted clusters | Unmatched clusters | Pure-noise clusters | Retained noise spikes | Discarded expert spikes | No-curation F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 0.4051 | 1.0000 | 0.5766 | 3 / 3 | 0.9034 | 108 | 105 | 105 | 73,913 | 0 | 0.4253 |
| cM2-e004_004-006_CH3 | 0.2364 | 1.0000 | 0.3824 | 2 / 2 | 1.0000 | 67 | 65 | 65 | 59,330 | 0 | 0.0000 |
| cM2-e004_011-015_CH20 | 0.2559 | 0.9671 | 0.4048 | 1 / 1 | 0.9213 | 60 | 59 | 57 | 51,305 | 0 | 0.0000 |

Per expert unit (unmatched units score 0):

| Dataset | Expert unit | Spikes | Matched cluster | IoU (accuracy) | Best candidate IoU | P | R | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 1 | 7,272 | 1 | 0.7103 | 0.7103 | 0.7103 | 1.0000 | 0.8306 |
| cM2-e004_004-006_CH31 | 56 | 4,162 | 56 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e004_004-006_CH31 | 63 | 38,899 | 63 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e004_004-006_CH3 | 4 | 13,782 | 4 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e004_004-006_CH3 | 15 | 4,582 | 15 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e004_011-015_CH20 | 1 | 18,465 | 1 | 0.9213 | 0.9213 | 0.9512 | 0.9671 | 0.9590 |

## Supplementary: historical many-to-one (not comparable with one-to-one)

| Dataset | Clusters | P | R | F1 | No-curation F1 |
|---|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 108 | 0.4051 | 1.0000 | 0.5766 | 0.5048 |
| cM2-e004_004-006_CH3 | 67 | 0.2364 | 1.0000 | 0.3824 | 0.2440 |
| cM2-e004_011-015_CH20 | 60 | 0.2647 | 1.0000 | 0.4185 | 0.3597 |

Do not quote expert-unit scores without the unmatched / pure-noise cluster counts in the same row, and do not average over matched units only.

Incomplete channels are not final scores. No automatic retry or prompt update.
Stop/error: `{'type': 'ClaudeStop', 'cause': None, 'message': "Remaining budget cannot cover next request's reserved maximum"}`. Local records: `../../runs/agentic_free_v1_opus55_20261026_resume240/`.
