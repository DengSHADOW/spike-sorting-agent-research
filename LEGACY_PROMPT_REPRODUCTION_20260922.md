# Legacy GPT-5.1 prompt/rollout reproduction — 2026-09-22

> **Scope correction:** this report is specifically a reproduction of the
> strict prompt retained in CH30 output artifacts. It must not be treated as the
> common prompt used for CH3/CH20/CH31 or as a four-channel protocol.

## Objective

Determine whether the historical GPT-5.1 real-data result can be reproduced with the detailed curation prompt used by the retained CH30 artifacts. This is separate from the current 90-sample `action-only-json-v2` fixed-state benchmark.

## Protocol provenance

- Historical result inputs: `output/main_gpt-5.1/CH30/vlm_inputs/`.
- Historical controller thresholds: Phase 0 `<500`, Phase 2 small/large boundary `4,000`, Phase 3 `<5,000`.
- Historical VLM input: three images in Phase 1 (waveform/ISI/tree), three in Phase 2 (small waveform/large waveform/merged ISI), detailed morphology/split/merge prompt, GPT-5.1 Responses API, reasoning effort `medium`, 1,000 output-token ceiling, no enforced JSON schema.
- Actual current API model: `gpt-5.1-2025-11-13`.
- Reproduction safety differences: provider failure stops instead of using mock; an exhausted parse retry becomes `ABSTAIN`; retained historical outputs are never overwritten.

The exact source revision that produced the strict CH30 artifacts is not recoverable. Git object `03f68e5` contains those artifacts, but its checked-in prompt is the more cautious shared version and its VLM logger uses unique call suffixes whereas the saved CH30 files overwrite repeated cluster IDs. The old waveform implementation used unseeded `numpy.random.choice` over at most 5,000 waveforms, so the original random subset is also unavailable. For CH30 specifically, the retained prompt/PNG bytes are therefore the strongest available run-level evidence.

The current fixed-state prompt was not simplified during this reproduction. It originated from the pre-existing `gemma4_train_reasoned` SFT-export profile. The repository contains no controlled evidence that simplifying the input prompt was intended to improve Gemma or that it improved accuracy. `action-only` output and simplified input rules must be treated as separate design choices.

## Controlled replay of retained historical states

Each retained CH30 prompt and PNG bundle was replayed byte-for-byte. This tests model stability at identical historical observations; it is not a new rollout because predictions are not applied to the environment.

| Saved state | Historical action | Current replay | Match |
|---|---|---|---:|
| Phase 1 cluster 1 | DISCARD | SPLIT | No |
| Phase 1 cluster 31 | KEEP | KEEP | Yes |
| Phase 1 cluster 211 | DISCARD | DISCARD | Yes |
| Phase 1 cluster 305 | DISCARD | DISCARD | Yes |
| Phase 1 cluster 353 | DISCARD | DISCARD | Yes |
| Phase 1 cluster 413 (last retained visit) | DISCARD | DISCARD | Yes |
| Phase 1 cluster 443 | KEEP | DISCARD after legacy-style retry | No |
| Phase 1 cluster 455 | KEEP | KEEP | Yes |
| Phase 2 443 into 31 | DISCARD | DISCARD | Yes |
| Phase 2 455 into 31 | DISCARD | NOT_MERGE | No |

Agreement is `7/10 = 70%`. The first cluster-443 response began with `{"action":"KEEP"}` but was truncated inside its rationale at the 1,000-token ceiling; the retry returned a complete `DISCARD`, so the strict legacy-style final result is counted as a mismatch. Cluster 413 was visited twice historically, but the old logger overwrote the first bundle; only its last retained state is auditable.

Controlled replay usage, including the cluster-443 retry: 11 API calls, 22,531 input tokens (1,920 cached), 6,182 output tokens (5,110 reasoning), 28,713 total tokens.

## Fresh CH30 autonomous rollout: capped pilot

The fresh run started from `hierarchy.assigns`, used the artifact-recovered detailed prompt, old unseeded 5,000-waveform rendering, historical thresholds, and a hard cap of 20 VLM attempts.

- Historical CH30 first action: cluster 1 `DISCARD`.
- Fresh first action: cluster 1 `SPLIT`; the next states became cluster 1 `DISCARD` and new child cluster 239 `KEEP`.
- Cluster 211 also changed from historical `DISCARD` to `SPLIT`, then produced two new child decisions.
- Cluster 305 changed from historical `DISCARD` to a multi-level split path.
- The run reached 20 calls during Phase 2 and stopped by budget before a terminal assignment.
- Usage: 41,999 input tokens (4,096 cached), 10,771 output tokens (8,879 reasoning), 52,770 total tokens.

Because the run did not terminate, it has no valid new cluster-level precision/recall/F1. It must not be compared numerically with historical CH30 `P/R/F1 = 1.0000/0.5578/0.7162` as though both were completed trials.

## User-requested complete CH30 autonomous rollout

After the capped pilot exposed the possible trajectory growth, the same artifact-recovered protocol was rerun from the original `hierarchy.assigns` with the safety cap raised from 20 to 100 attempts. The cap was only a local cost guard, not an OpenAI or algorithmic limit. This run terminated naturally after 21 successful provider calls.

| Quantity | Historical saved CH30 | New complete rollout |
|---|---:|---:|
| Logged actions, including automatic filters | 22 | 32 |
| Final clusters | 1 | 1 |
| Final assigned spikes | 50,821 | 50,821 |
| Precision | 1.0000 | 1.0000 |
| Recall | 0.5578 | 0.5578 |
| F1 | 0.7162 | 0.7162 |

The terminal assignment and cluster-level metrics are numerically reproduced. The decision trajectory is not:

- Historical cluster 1 was immediately `DISCARD`; the new run recursively `SPLIT` it three times before keeping/discarding descendants.
- Historical cluster 305 was immediately `DISCARD`; the new run `SPLIT` it and discarded two descendants.
- Historical cluster 353 was `DISCARD`; the new run first `KEEP` it, later reached `ABSTAIN` after three malformed/empty Phase-2 responses, preserved it safely, and finally removed it through the historical `<5,000` size filter.
- Historical cluster 413 was `SPLIT` and then resolved through descendants; the new run immediately returned `DISCARD`.

The matching endpoint therefore does not show that GPT-5.1 reproduced the historical step-by-step curation policy. It shows that, on this channel, divergent model decisions were collapsed to the same one-cluster endpoint by the old hard size filters. This makes the CH30 terminal result reproducible as an output of the whole legacy controller, but weak evidence for stable VLM reasoning.

Complete-run usage: 21 API calls, 41,873 input tokens (1,536 cached), 11,318 output tokens (9,417 reasoning), 53,191 total tokens. Actual served model: `gpt-5.1-2025-11-13`.

## Interpretation

1. The detailed prompt does not deterministically recover the historical policy. Even byte-identical prompt/PNG inputs agree on only 7/10 retained states.
2. Closed-loop differences are amplified: one early SPLIT creates new states and more API calls. In the completed rerun, later hard filters collapsed the different path back to the same terminal assignment.
3. The old 500/5,000 filters can dominate the endpoint and were not biologically calibrated in this repository. The exactly matched CH30 terminal F1 therefore cannot be attributed only to stable VLM reasoning.
4. The current simplified fixed-state accuracy and old rollout F1 are not a prompt A/B test: action-level versus terminal metrics, human versus model-visited states, three versus four views, and controller semantics differ.
5. Whether the simplified prompt lowers accuracy remains unmeasured. The required experiment is same states, same image bytes, same model/version/effort/output contract, with only `minimal-zero-shot-v1` versus a frozen detailed/domain-policy prompt changed.

## API accounting for this reproduction investigation

| Run | Status | Total tokens | Use in conclusions |
|---|---|---:|---|
| Current 500-waveform approximation | stopped after divergence | 55,434 | excluded from performance |
| Deterministic 5,000-waveform approximation | stopped after divergence | 9,782 | excluded from performance |
| Byte-identical saved-input audit + retry | complete | 28,713 | 7/10 action stability |
| Artifact-recovered fresh rollout | budget stop at 20 calls | 52,770 | trajectory instability only |
| Artifact-recovered complete rollout | complete at 21 calls | 53,191 | exact terminal metrics; divergent actions |
| **Total** |  | **199,890** | one new terminal CH30 trial |

## Decision

The requested CH30 complete reproduction attempt is finished. Its terminal output matches the saved CH30 result exactly, but its action policy does not. Do not generalize this one endpoint match into a stable-policy or SOTA claim. A later all-channel run mistakenly reused this CH30-specific prompt and is retained as a protocol-mismatch audit in `LEGACY_FULL_ROLLOUT_ALL_CHANNELS_20260922.md`; it is not a faithful reproduction. Return the main line to leakage-free supervised experiments after correcting the protocol record.

Artifacts:

- `scripts/run/run_legacy_prompt_rollout.py`
- `scripts/test/replay_saved_rollout_inputs.py`
- `scripts/test/replay_saved_vlm_input.py`
- `output/legacy_reproduction/gpt51_ch30_saved_input_replay_all_unique_retry_20260922/`
- `output/legacy_reproduction/gpt51_ch30_artifact_recovered_fresh_rollout_20260922/`
- `output/legacy_reproduction/gpt51_ch30_artifact_recovered_full_rollout_20260922/`
