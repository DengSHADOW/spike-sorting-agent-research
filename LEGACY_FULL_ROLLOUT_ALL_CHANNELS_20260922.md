# Legacy GPT-5.1 full autonomous rollouts — all real channels (2026-09-22)

## Question

Can the retained historical GPT-5.1 real-data results be reproduced by rerunning the full legacy curation controller on all four available annotated channels?

## Frozen protocol

- Channels: CH3, CH20, CH30, CH31.
- Start state: each MAT file's original `hierarchy.assigns`.
- Model: requested `gpt-5.1`; actual served model `gpt-5.1-2025-11-13`.
- Input: artifact-recovered detailed domain prompt; Phase 1 waveform/ISI/tree images and Phase 2 small-waveform/large-waveform/merged-ISI images.
- Rendering: recoverable old unseeded random overlay of at most 5,000 waveforms. The original RNG states are unavailable.
- Controller thresholds: auto-discard `<500`, small/large boundary `4,000`, final discard `<5,000`.
- No total VLM-attempt limit. Per-state legacy JSON parsing still permits at most three attempts before `ABSTAIN`.
- Current safety semantics remain enabled: provider errors stop instead of falling back to mock; parse exhaustion becomes `ABSTAIN`; all-`NOT_MERGE` preserves a distinct cluster before the final filter.
- Runs use isolated output directories and never modify MAT files or retained historical results.

This is a complete best-effort reproduction of the recoverable controller, not exact source/RNG reproduction. The initial Git object contains source code that does not match its retained result artifacts.

## Results

| Channel | Calls | Tokens | Final units | Assigned spikes | Precision | Recall | F1 | Historical F1 | Exact assignments |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CH3 | 9 | 23,242 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.7508 | No |
| CH20 | 7 | 18,456 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.8951 | No |
| CH30 | 21 | 53,191 | 1 | 50,821 | 1.0000 | 0.5578 | 0.7162 | 0.7162 | Yes |
| CH31 | 58 | 144,781 | 2 | 58,043 | 0.7955 | 0.9173 | 0.8521 | 0.7251 | No |
| **Mean / total** | **95** | **239,670** | — | — | **0.4489** | **0.3688** | **0.3921** | **0.7718** | **1/4** |

The historical comparison column uses the currently retained `evaluation_report.json` files, not the older README aggregate. Across the retained reports, historical mean `P/R/F1=0.7735/0.8327/0.7718`.

## Channel-level interpretation

- **CH3:** Phase 0 removed 22 `<500` clusters. The VLM recursively split the dominant cluster and retained two small descendants, but Phase 3 discarded both because they had 658 and 388 spikes. The controller therefore returned no units.
- **CH20:** the only Phase-0 survivor was recursively split; every resulting branch was classified `DISCARD` in Phase 1. The controller returned no units before merge evaluation.
- **CH30:** the action path differed substantially from history, but the `<5,000` final filter removed all extra descendants and produced the exact historical final assignment: one unit with 50,821 spikes.
- **CH31:** the dominant cluster required 15 recursive split levels and generated many sibling decisions. Phase 2 produced two merges, then Phase 3 removed eight sub-5,000 clusters. Two units remained. This terminal result scored higher than the retained historical CH31 report but did not match its assignments.

## Main conclusions

1. The historical four-channel GPT-5.1 result is **not reproducible as a stable VLM policy**. Only one of four final assignment arrays matched exactly.
2. The four-channel mean F1 fell from retained historical `0.7718` to `0.3921`; CH3 and CH20 collapsed to zero units. Therefore the historical aggregate must remain preliminary legacy evidence, not a stable baseline or SOTA result.
3. The old 500/5,000 spike-count rules strongly determine outcomes. They can collapse divergent paths to the same endpoint (CH30) or erase every predicted unit (CH3). They should remain disabled in the current real-data main line unless independently calibrated.
4. Unlimited total calls do not solve policy instability. CH31 consumed 58 calls because early SPLIT decisions expanded the state tree, while CH20 terminated after seven calls by discarding everything.
5. Prompt detail alone is insufficient. Identical saved-state replay previously achieved only 7/10 action agreement, and these full rollouts show much larger closed-loop divergence.

## Evaluation bug discovered and fixed

CH3 exposed an evaluator boundary case: an empty curated sorting produced a DataFrame without `tp` columns and raised `KeyError`. The evaluator now treats an empty prediction as a valid result: `TP=0`, `FP=0`, `FN=all GT spikes`, with precision/recall/F1 equal to zero. CH3 was finalized offline from its saved `final_assigns.npy`; no API rerun was needed.

## Artifacts

- Unified machine-readable summary: `output/legacy_reproduction/full_rollout_summary_20260922/summary.json`
- Per-channel table: `output/legacy_reproduction/full_rollout_summary_20260922/per_channel.csv`
- CH3: `output/legacy_reproduction/gpt51_ch3_artifact_recovered_full_rollout_20260922/`
- CH20: `output/legacy_reproduction/gpt51_ch20_artifact_recovered_full_rollout_20260922/`
- CH30: `output/legacy_reproduction/gpt51_ch30_artifact_recovered_full_rollout_20260922/`
- CH31: `output/legacy_reproduction/gpt51_ch31_artifact_recovered_full_rollout_20260922/`

Total API accounting for the entire legacy reproduction investigation, including earlier stopped pilots and saved-input audits, is now 386,369 tokens. The four completed fresh rollouts themselves account for 239,670 tokens.
