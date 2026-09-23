# CH30 OpenAI API-VLM fixed-state evaluation（2026-09-22）

## Protocol

- Data: the current audited `cM2-e008_021-028_CH30` dataset, not a different legacy MAT file.
- Targets: 90 expert edits: 41 `DISCARD`, 43 `SPLIT`, and 6 `MERGE`.
- Evaluation: fixed expert pre-action states; predictions were scored but never executed.
- Inputs: four diagnostic images for split-stage states, three pairwise diagnostic images for merge-stage states, plus the exported numerical metrics.
- Output: strict `action-only-json-v2` with JSON schema enforcement; no rationale or schema fallback.
- Provider: official OpenAI API; temperature 0; no mock, RAG, MAT modification, or cluster-state update.
- GPT-4.1 used `max_tokens=32`; GPT-5.1 used medium reasoning and `max_output_tokens=2048`.

An exploratory GPT-5.1 run with a 512-token output budget was stopped and excluded after one response exhausted its reasoning budget and returned no final JSON. The formal run used 2,048 tokens as a ceiling and completed with 90/90 strict JSON responses. Per-sample checkpointing and `--resume` support were added before the formal rerun.

## Results

| Model | Actual API model | Accuracy | Macro-F1 | DISCARD P/R/F1 | SPLIT P/R/F1 | MERGE P/R/F1 |
|---|---|---:|---:|---|---|---|
| GPT-4.1 | `gpt-4.1-2025-04-14` | 35/90 = 0.389 | 0.575 | 0 / 0 / 0 | .784 / .674 / .725 | 1 / 1 / 1 |
| GPT-5.1 | `gpt-5.1-2025-11-13` | 33/90 = 0.367 | 0.564 | 0 / 0 / 0 | .771 / .628 / .692 | 1 / 1 / 1 |

Prediction distributions:

- GPT-4.1: 37 `SPLIT`, 47 `KEEP`, 6 `MERGE`.
- GPT-5.1: 35 `SPLIT`, 49 `KEEP`, 6 `MERGE`.
- The two models agreed on 88/90 predictions (97.8%). The only differences were samples 49 and 60, where GPT-4.1 predicted `SPLIT` and GPT-5.1 predicted `KEEP`.

Both models returned strict action-only JSON for 90/90 samples. Neither model predicted `DISCARD` once. The 6/6 `MERGE` result remains positive-only because CH30 contains no `NOT_MERGE` target.

Token usage recorded by the API:

- GPT-4.1: 221,485 input tokens, 4,864 cached input tokens, 530 output tokens, 222,015 total tokens.
- GPT-5.1: 184,225 input tokens, 85,120 cached input tokens, 18,438 output tokens, including 16,652 reasoning tokens; 202,663 total tokens.

## Interpretation

This rerun is useful because it places the API VLMs, open VLMs, and numeric baseline on the same current CH30 action-level task. GPT-4.1 and GPT-5.1 do not outperform Qwen3.5-9B (0.500) or Gemma-4-E4B-it (0.544), and all remain far below the supervised numeric Random Forest on learned `SPLIT/DISCARD` states (78/84 = 0.929).

The result does not invalidate the older GPT closed-loop cluster-level findings. It shows that the older endpoint F1 and the current expert-action accuracy measure different properties. Under the frozen action protocol, neither API model reproduces the expert `DISCARD` rule, so neither should be used as a ground-truth teacher for that action without calibration.

## Artifacts

- GPT-4.1: `output/openai_fixed_state/gpt41_ch30_action_v2_full90_20260922/`
- GPT-5.1: `output/openai_fixed_state/gpt51_ch30_action_v2_full90_20260922_v2/`
- Both directories contain per-sample predictions, strict-format analysis, run manifests, token usage, error cases, and incremental checkpoints.
