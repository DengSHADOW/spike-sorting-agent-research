# GPT-5.1 cautious-prompt full rollout — 2026-09-23

> 2026-10-06 归档：以下保留当时的实验条件、结果和判断，不是当前执行计划。旧文中的“下一步”“必须先 SFT”“未使用 final test”等不代表现状；四主通道已反复分析，fixed-state accuracy 不等于 rollout 终态质量。当前主线见 [方法与实验状态](../../项目方法与实验状态.md)。代码和 `output/` 路径均相对于仓库根目录。

## Protocol

- Scope: CH3, CH20, CH30, and CH31 full autonomous curation rollouts from
  `hierarchy.assigns`; each action updates the state used by the next call.
- Model: `gpt-5.1-2025-11-13`, OpenAI Responses API, reasoning effort
  `medium`, temperature `0`.
- Prompt: JianZhi's shared cautious Phase 1/2 source prompt from upstream
  commit `03f68e5`; this is the skill-rich zero-shot expert harness, not the
  student/SFT harness.
- Legacy controller thresholds retained: Phase 0 `<500`, small/large split at
  `4,000`, final discard `<5,000` spikes.
- No total VLM-call cap. Provider failures stop the run; parse exhaustion
  returns `ABSTAIN`; `NOT_MERGE` preserves state until the final filter.
- No mock, RAG, SFT, or MAT modification. Outputs were written to new local
  directories and did not overwrite historical runs.

## Results

| Channel | Calls | Tokens | Cost estimate | Final units | Assigned spikes | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CH3 | 14 | 36,993 | $0.1001 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 |
| CH20 | 3 | 7,694 | $0.0210 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 |
| CH30 | 27 | 70,051 | $0.2016 | 1 | 51,447 | 0.9878 | 0.5578 | 0.7130 |
| CH31 | 24 | 59,868 | $0.1634 | 2 | 57,230 | 0.6797 | 0.7728 | 0.7233 |
| **Mean / total** | **68** | **174,606** | **$0.4861** | — | **108,677** | **0.4169** | **0.3327** | **0.3591** |

The cost estimate uses $1.25/M uncached input tokens, $0.125/M cached input
tokens, and $10/M output tokens (including reasoning tokens). Actual billing
is determined by the provider account.

## Historical comparison

| Channel | Fresh cautious F1 | Retained historical F1 | Exact final assignment |
|---|---:|---:|---:|
| CH3 | 0.0000 | 0.7508 | No |
| CH20 | 0.0000 | 0.8951 | No |
| CH30 | 0.7130 | 0.7162 | No |
| CH31 | 0.7233 | 0.7251 | No |
| **Mean** | **0.3591** | **0.7718** | **0/4** |

CH30 and CH31 reproduced nearly the same cluster-level scores but not exact
assignments. CH30 merged 626 additional spikes into the retained unit; CH31
merged 266 additional spikes into one retained unit. CH3 and CH20 diverged
early and ended empty.

## Interpretation

- This run used the corrected cautious source prompt. For CH3 and CH20, the
  first generated prompt is byte-identical to the corresponding retained
  historical prompt; the same check also holds for the available CH31 call-1
  artifact.
- The old visualization path samples up to 5,000 waveforms with unseeded
  `numpy.random.choice`. Therefore fresh diagnostic images are not frozen.
  GPT reasoning output is also not guaranteed to be deterministic at
  temperature zero. An early action change alters every later cluster state.
- CH3 split its large cluster into five small retained branches, then the
  legacy `<5,000` final filter deleted all five. CH20 discarded both branches
  immediately after its first split. These are different failure modes.
- The retained historical aggregate is therefore not reproducible as a stable
  policy under the original protocol. The fresh mean F1 is an observed single
  stochastic rollout, not a model capability estimate or SOTA result.
- Before another reproduction study, freeze the sampled spike indices/image
  bytes, prompt hash, controller semantics, model snapshot, and evaluator;
  then run repeated seeds to report variance. Further unfrozen one-off API
  runs have low scientific value.

## Artifacts

Machine-readable local summary:
`output/legacy_reproduction/cautious_source_full_rollout_summary_20260923/`.
Each channel directory contains the manifest, prompt/images, decisions,
action log, final assignments, and evaluation report.
