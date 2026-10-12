# Terminal matching report — minimal_astra_high_dual_20261007_resume150

Read-only rescoring of saved terminal states; no model calls. Metric definitions are in `zero-shot baseline/scripts/score_terminal_matching.py`.

## 1. Primary: many-to-one, spike-pooled (repository evaluator)

Every spike in a cluster with no expert overlap counts as a false positive.

| Dataset | Clusters | Precision | Recall | F1 | No-curation F1 | Matches run's own F1 |
|---|---:|---:|---:|---:|---:|---|
| cM2-e004_004-006_CH31 | 50 | 0.3870 | 1.0000 | 0.5581 | 0.5048 | yes |
| cM2-e004_004-006_CH3 | 66 | 0.1995 | 1.0000 | 0.3327 | 0.2440 | yes |
| cM2-e004_011-015_CH20 | 53 | 0.2205 | 1.0000 | 0.3613 | 0.3597 | yes |
| cM2-e008_021-028_CH30 | 67 | 0.1946 | 0.8742 | 0.3183 | 0.3061 | yes |

False-positive spikes by source (all counted in the primary score):

| Dataset | Pure-noise clusters | Their spikes | Noise in matched clusters | Other expert units in matched clusters |
|---|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 47 | 67,131 | 12,587 | 0 |
| cM2-e004_004-006_CH3 | 64 | 63,919 | 9,747 | 0 |
| cM2-e004_011-015_CH20 | 52 | 64,364 | 917 | 0 |
| cM2-e008_021-028_CH30 | 64 | 304,783 | 13,418 | 11,461 |

## 2. Secondary: one-to-one (SpikeInterface 0.103.1 rules)

Scores are per expert unit; an unmatched expert unit scores 0. Unmatched predicted clusters are not in these scores, so their counts are shown in the same row and must be quoted together.

| Dataset | Expert units found / total | Mean accuracy (all units) | Well-detected | Unmatched clusters (bad) | False-positive clusters | Redundant | Overmerged | Spikes in unmatched clusters | No-curation: found / FP clusters |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| cM2-e004_004-006_CH31 | 2 / 3 | 0.664 | 2 | 48 | 47 | 0 | 0 | 86,640 | 1 / 16 |
| cM2-e004_004-006_CH3 | 1 / 2 | 0.459 | 1 | 65 | 64 | 0 | 0 | 77,005 | 0 / 25 |
| cM2-e004_011-015_CH20 | 1 / 1 | 0.953 | 1 | 52 | 52 | 0 | 0 | 64,364 | 0 / 11 |
| cM2-e008_021-028_CH30 | 1 / 3 | 0.333 | 1 | 66 | 65 | 0 | 1 | 358,485 | 1 / 17 |

Per expert unit:

| Dataset | Expert unit | Spikes | Matched cluster | Agreement | Accuracy | Recall | Precision |
|---|---:|---:|---:|---:|---:|---:|---:|
| cM2-e004_004-006_CH31 | 1 | 7,272 | — | none (best 0.373) | 0.000 | 0.000 | 0.000 |
| cM2-e004_004-006_CH31 | 56 | 4,162 | 56 | 1.000 | 1.000 | 1.000 | 1.000 |
| cM2-e004_004-006_CH31 | 63 | 38,899 | 63 | 0.991 | 0.991 | 1.000 | 0.991 |
| cM2-e004_004-006_CH3 | 4 | 13,782 | 4 | 0.917 | 0.917 | 1.000 | 0.917 |
| cM2-e004_004-006_CH3 | 15 | 4,582 | — | none (best 0.350) | 0.000 | 0.000 | 0.000 |
| cM2-e004_011-015_CH20 | 1 | 18,465 | 1 | 0.953 | 0.953 | 1.000 | 0.953 |
| cM2-e008_021-028_CH30 | 2 | 28,823 | — | none (best 0.468) | 0.000 | 0.000 | 0.000 |
| cM2-e008_021-028_CH30 | 31 | 50,821 | 31 | 1.000 | 1.000 | 1.000 | 1.000 |
| cM2-e008_021-028_CH30 | 38 | 11,461 | — | none (best 0.229) | 0.000 | 0.000 | 0.000 |

## 3. Checks

- cM2-e004_004-006_CH31: terminal `zero-shot baseline/runs/minimal_astra_high_dual_20261007_v3/datasets/cM2-e004_004-006_CH31/terminal.npz` (final_sha256 verified); MAT SHA256 verified; official SpikeInterface cross-check: match.
- cM2-e004_004-006_CH3: terminal `zero-shot baseline/runs/minimal_astra_high_dual_20261007_v3/datasets/cM2-e004_004-006_CH3/terminal.npz` (final_sha256 verified); MAT SHA256 verified; official SpikeInterface cross-check: match.
- cM2-e004_011-015_CH20: terminal `zero-shot baseline/runs/minimal_astra_high_dual_20261007_retry503/datasets/cM2-e004_011-015_CH20/terminal.npz` (final_sha256 verified); MAT SHA256 verified; official SpikeInterface cross-check: match.
- cM2-e008_021-028_CH30: terminal `zero-shot baseline/runs/minimal_astra_high_dual_20261007_resume150/datasets/cM2-e008_021-028_CH30/terminal.npz` (final_sha256 verified); MAT SHA256 verified; official SpikeInterface cross-check: match.

## 4. How to quote these numbers

- Lead with the spike-pooled many-to-one F1; it is the score that penalises retained noise.
- Quote one-to-one results only together with the unmatched / false-positive cluster counts.
- Do not report any precision or F1 computed over matched pairs only.
