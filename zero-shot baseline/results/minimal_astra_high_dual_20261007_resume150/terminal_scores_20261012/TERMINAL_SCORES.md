# Terminal scores — minimal_astra_high_dual_20261007_resume150

Read-only rescoring of saved terminal states; no model calls.
One-to-one rules: `configs/curation/one_to_one_scoring_v1.json` (SHA256 `dc4085582f1e92846afe63405e80beee12b568837bbe98f05f551364de3b79a5`).

## Primary: one-to-one (IoU >= 0.5, Hungarian), full output

Precision uses every predicted spike, including unmatched clusters.

| Dataset | P | R | F1 | Expert units matched | Expert-unit macro accuracy | Predicted clusters | Unmatched clusters | Pure-noise clusters | Retained noise spikes | Discarded expert spikes | No-curation F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 0.3311 | 0.8555 | 0.4774 | 2 / 3 | 0.6637 | 50 | 48 | 47 | 79,718 | 0 | 0.4253 |
| cM2-e004_004-006_CH3 | 0.1498 | 0.7505 | 0.2497 | 1 / 2 | 0.4586 | 66 | 65 | 64 | 73,666 | 0 | 0.0000 |
| cM2-e004_011-015_CH20 | 0.2205 | 1.0000 | 0.3613 | 1 / 1 | 0.9527 | 53 | 52 | 52 | 65,281 | 0 | 0.0000 |
| cM2-e008_021-028_CH30 | 0.1242 | 0.5578 | 0.2031 | 1 / 3 | 0.3333 | 67 | 66 | 64 | 318,201 | 0 | 0.1953 |

Per expert unit (unmatched units score 0):

| Dataset | Expert unit | Spikes | Matched cluster | IoU (accuracy) | Best candidate IoU | P | R | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 1 | 7,272 | — | 0.0000 | 0.3728 | 0.0000 | 0.0000 | 0.0000 |
| cM2-e004_004-006_CH31 | 56 | 4,162 | 56 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e004_004-006_CH31 | 63 | 38,899 | 63 | 0.9911 | 0.9911 | 0.9911 | 1.0000 | 0.9955 |
| cM2-e004_004-006_CH3 | 4 | 13,782 | 4 | 0.9173 | 0.9173 | 0.9173 | 1.0000 | 0.9569 |
| cM2-e004_004-006_CH3 | 15 | 4,582 | — | 0.0000 | 0.3501 | 0.0000 | 0.0000 | 0.0000 |
| cM2-e004_011-015_CH20 | 1 | 18,465 | 1 | 0.9527 | 0.9527 | 0.9527 | 1.0000 | 0.9758 |
| cM2-e008_021-028_CH30 | 2 | 28,823 | — | 0.0000 | 0.4680 | 0.0000 | 0.0000 | 0.0000 |
| cM2-e008_021-028_CH30 | 31 | 50,821 | 31 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| cM2-e008_021-028_CH30 | 38 | 11,461 | — | 0.0000 | 0.2292 | 0.0000 | 0.0000 | 0.0000 |

## Supplementary: historical many-to-one (not comparable with one-to-one)

| Dataset | Clusters | P | R | F1 | No-curation F1 |
|---|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 50 | 0.3870 | 1.0000 | 0.5581 | 0.5048 |
| cM2-e004_004-006_CH3 | 66 | 0.1995 | 1.0000 | 0.3327 | 0.2440 |
| cM2-e004_011-015_CH20 | 53 | 0.2205 | 1.0000 | 0.3613 | 0.3597 |
| cM2-e008_021-028_CH30 | 67 | 0.1946 | 0.8742 | 0.3183 | 0.3061 |

Do not quote expert-unit scores without the unmatched / pure-noise cluster counts in the same row, and do not average over matched units only.
