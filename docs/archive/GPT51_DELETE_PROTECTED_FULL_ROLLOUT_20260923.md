# GPT-5.1 deletion-protected full rollout — 2026-09-23

> 2026-10-06 归档：以下保留当时的实验条件、结果和判断，不是当前执行计划。旧文中的“下一步”“必须先 SFT”“未使用 final test”等不代表现状；四主通道已反复分析，fixed-state accuracy 不等于 rollout 终态质量。当前主线见 [方法与实验状态](../../项目方法与实验状态.md)。代码和 `output/` 路径均相对于仓库根目录。

## Question and protocol

This experiment tests whether JianZhi's shared cautious domain-rule prompt can
avoid catastrophic empty outputs when destructive actions are made
recoverable. It is a full autonomous rollout: every prediction changes the
cluster state seen by later calls. It is not the CH30 fixed-state action test
and is not a trained student model.

- Data: CH3, CH20, CH30, and CH31, starting from each MAT file's
  `hierarchy.assigns`.
- Model: `gpt-5.1-2025-11-13`, OpenAI Responses API, reasoning effort
  `medium`, temperature `0`, no total call cap.
- Prompt: the common cautious Phase 1/2 prompt from upstream commit
  `03f68e5`; the prompt itself was retained.
- Automatic `<500` Phase-0 deletion and `<5,000` Phase-3 deletion were
  disabled. The `4,000` threshold was retained only to partition small and
  large clusters for Phase 2.
- If Phase 1 proposed `DISCARD` for a cluster with at least 4,000 spikes, the
  controller used `SPLIT` when the hierarchy allowed it, otherwise
  `ABSTAIN`. Phase-2 `DISCARD` became `ABSTAIN` and preserved the small
  cluster. Parse exhaustion also returned `ABSTAIN`; provider failure stopped
  the run instead of falling back to mock.
- NumPy seed was fixed to `0`, but OpenAI reasoning is not guaranteed to be
  deterministic. Raw data, images, prompts, and per-call outputs remain local
  under ignored `output/` directories.

## Results

| Channel | Calls | Tokens | Runtime | Final units | Assigned spikes | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CH3 | 258 | 638,383 | 43m49s | 55 | 82,700 | 0.2221 | 1.0000 | 0.3634 |
| CH20 | 207 | 517,326 | 36m09s | 38 | 70,354 | 0.2625 | 1.0000 | 0.4158 |
| CH30 | 869 | 2,101,133 | 3h15m39s | 50 | 376,317 | 0.2116 | 0.8742 | 0.3408 |
| CH31 | 213 | 523,542 | 43m32s | 31 | 120,026 | 0.4194 | 1.0000 | 0.5909 |
| **Mean / total** | **1,547** | **3,780,384** | **5h19m** | — | **649,397** | **0.2789** | **0.9686** | **0.4277** |

The macro row averages the four channel-level precision, recall, and F1
values. Pooling spike counts across channels gives micro
`P/R/F1=0.2569/0.9357/0.4031` (`TP=166,806`, `FP=482,591`, `FN=11,461`).
At the documented rate assumptions of $1.25/M uncached input tokens,
$0.125/M cached input tokens, and $10/M output tokens including reasoning,
the estimated API cost is **$11.85**. Provider billing is authoritative.

## Safety activity and comparison

| Channel | Phase-1 large-DISCARD guards | Phase-2 DISCARD guards | Unprotected cautious F1 | Protected F1 |
|---|---:|---:|---:|---:|
| CH3 | 13 | 0 | 0.0000 | 0.3634 |
| CH20 | 18 | 2 | 0.0000 | 0.4158 |
| CH30 | 49 | 13 | 0.7130 | 0.3408 |
| CH31 | 15 | 4 | 0.7233 | 0.5909 |

Across the four runs, the controller recorded 95 protected Phase-1
large-cluster deletion proposals and 19 protected Phase-2 deletion proposals.
These counts are guard events, not independent human actions. None of the
protected final assignments exactly matches the retained historical endpoint.

The guard solved the narrow safety failure: CH3 and CH20 no longer ended with
zero units, and total recall rose to nearly one. It did not solve curation
quality. Excess preservation produced hundreds of thousands of false-positive
spikes, reduced precision to 0.21–0.42, and made the run 23 times more
expensive in calls than the unprotected cautious run (`1,547` versus `68`).

## New failure evidence

Phase 2 asks the model to judge small-cluster validity again for every possible
merge target. The same small cluster can therefore receive incompatible
validity judgments solely because the comparison target changed. In CH30,
cluster 344 was repeatedly described as a valid neuronal waveform for some
targets and as invalid/non-neuronal for other targets. CH31 showed the same
pattern for clusters including 79 and 226. A single pairwise `DISCARD` is
therefore not a reliable cluster-level verdict.

The prompt also mixes ratio and percentage language around the ISI threshold.
Some rationales interpreted `0.006` as 0.006%, while others used 0.6%. This is
additional evidence that free-text rationale should not implement numerical
policy semantics.

## Conclusion and next protocol

Deletion protection is appropriate as a fail-safe but not as the final
curation policy. The next controller should separate three decisions:

1. Judge cluster validity once using a dedicated, frozen input and cache that
   result; do not rejudge validity inside every merge pair.
2. Rank or screen merge candidates numerically, then ask only a binary
   `MERGE`/`NOT_MERGE` question for selected pairs.
3. Route proposed deletions to `QUARANTINE`/`ABSTAIN` and require calibrated
   evidence or expert confirmation before irreversible removal.

This expensive four-channel run is sufficient to reject the naive policy
"protect every deletion and otherwise keep the legacy controller." It should
not be repeated across more channels. The main research line remains the
leakage-free student harness: train images-only and images+numeric
action-only SFT on train recordings, compare against the locked numeric RF on
CH30 validation, and enter autonomous rollout only after fixed-state safety
metrics are acceptable.

## Local artifacts

Machine-readable aggregate:
`output/legacy_reproduction/gpt51_cautious_delete_protected_v2_summary_20260923/`.
The successful CH20 run has the suffix `_retry1`; the earlier directory is a
zero-call provider-connection failure retained for audit. No experiment output
or source data is intended for Git.
