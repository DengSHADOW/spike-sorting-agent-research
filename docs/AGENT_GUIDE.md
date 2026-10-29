# Agent guide: how to read this project

Give this file to an AI coding agent (Claude Code, Codex, Cursor, etc.) before asking it about this
repository. To have it loaded automatically, copy it to `CLAUDE.md` or `AGENTS.md` in your own
clone. It is written for agents that **read and explain** the project. Changes and runs need the
project owner's agreement (see "Rules").

Last checked against the repository: 2026-10-29.

---

## 1. What the project is

A **post-curation agent for spike sorting**. Start from an initial hierarchical clustering of
detected spikes on one electrode channel. A vision-language model looks at rendered evidence
(waveforms, ISI / autocorrelograms, amplitude over time, the merge tree, cross-correlograms) and
decides cluster by cluster to KEEP, SPLIT, MERGE or DISCARD. The final clustering is scored
against an expert's curation.

It does **not** detect spikes and is not a new sorter. "CH31" is an electrode channel, not a
neuron.

## 2. Where the truth is

| Source | What it is | Trust |
|---|---|---|
| `项目总文档.md` (Chinese) | The single maintained document: status, methods, results, known issues, plans, change log. | Authoritative. Newest facts are in §10 (change log) and §3.7 (current design). |
| `README.md` | Short English overview and repository layout. | Accurate but brief; its "at a glance" date may be older than the main document. |
| `docs/LOCAL_MODEL_DEPLOYMENT.md` | How the local models (Qwen3.5-27B, Gemma-4-E4B-it) are served with vLLM on a RunPod GPU Pod. | Current. |
| `docs/archive/` | Superseded reports. | History only. Never present it as current. |
| Code and `configs/` | Ground truth for behaviour. | If code and document disagree, report the disagreement; do not silently pick one. |

The main document is in Chinese. Translate when answering, and keep identifiers (file names,
dataset IDs, field names) unchanged.

Suggested reading order in `项目总文档.md`:
1. §1 overview and research question;
2. §2 data, split and exposure (what may and may not be called held-out);
3. §3.1–3.6 task, actions, the two evaluations, scoring, lower/upper bounds;
4. §3.7 harness v2 (the current agent design);
5. §5 results and §6 known failure modes;
6. §10 change log, newest entries first within each date.

## 3. Key concepts

- **Dataset ID**: recording block + channel, e.g. `cM2-e004_004-006_CH31`. Always use the full ID.
  The four main development channels are CH3, CH20, CH30 and CH31.
- **Executor** (`src/cluster/manager.py`, `ClusterManager`): SPLIT undoes the cluster's *last*
  hierarchical merge (the model never chooses spike memberships); MERGE adds a tree row;
  DISCARD sets spikes to 0; KEEP / NOT_MERGE change nothing.
- **Two evaluations**: *stepwise* (replay expert actions, ask the model at each expert state,
  compare single actions) and *rollout* (the model drives the whole channel from the initial
  state; score the final clustering). A rollout cannot be aligned step by step with the expert.
- **Scoring**: the primary metric is one-to-one matching (Hungarian, IoU ≥ 0.5), with every
  predicted cluster counted in precision. Older results use many-to-one. **Never compare the two.**
  The lower bounds are "no curation" and random; the upper bound is a label-privileged oracle,
  which only shows that a path exists.
- **Three rollout conditions**, reported separately:
  1. *controller v1*: fixed Python phases (single judgements, then pair judgements). Zero-shot
     baselines. Finished.
  2. *agentic-free / B mode*: no scheduler; the model chooses what to inspect and when to stop.
     Finished.
  3. *harness v2* (current, §3.7): the round-synchronous controller, observation v2, a frozen
     contract, a **versioned skill** (the only part meant to be updated from expert feedback)
     and an "observe-then-act" output, in which the model labels each image before choosing an action.
- **Skill and gates**: `configs/controller_v2/skills/skill_v*.json` holds domain rules. Each rule
  records its sources (`sources`, `added_in`, `changed_in`). Gates are skill entries that the
  harness applies to the model's *own* observation labels. For example, G1 turns a MERGE into
  NOT_MERGE if the model itself labelled the cross-correlogram contaminated. Gated decisions log
  the model's original action.
- **Stopped state**: a run that hits a decision, time or round limit is scored separately as
  `stopped_state`, never mixed with completed runs. Runs that stop on an error are not scored.

## 4. Code map (harness v2)

| Part | File |
|---|---|
| Round-synchronous controller (`RoundsController`) | `src/pipeline/curation_controller_v2.py` |
| Observation v2 (images and measurements, dead-time-corrected contamination) | `src/agent/curation_observation_v2.py` |
| Decider: contract + skill + gates, request content, answer checks (`SkillDecider`) | `src/agent/curation_decider_v2.py` |
| vLLM provider (OpenAI-compatible chat completions) | `src/agent/vllm_provider.py` |
| Frozen contract / skills / model profiles | `configs/controller_v2/contract.txt`, `configs/controller_v2/skills/`, `configs/controller_v2/models.json` |
| One-channel rollout runner (`--scripted-offline` = no model) | `scripts/run/run_local_rollout.py` |
| GPU smoke test | `scripts/run/smoke_local_model.py` |
| Offline lower/upper bounds | `scripts/analysis/run_controller_v2_offline_references.py` |
| Model download / verify / serve | `scripts/deploy/local_models.py`, `configs/local_models.json` |
| Tests | `tests/test_curation_observation_v2.py`, `tests/test_curation_controller_v2_rounds.py`, `tests/test_skill_decider_v2.py`, `tests/test_vllm_provider_v2.py` |

Older parts (controller v1, zero-shot runners) are listed in `项目总文档.md` §4.1.
`mockups/curation-review/` is a separate expert-review website with its own README.

## 5. What a GitHub clone does and does not contain

- **Not in Git**: MAT data, rendered images, run outputs (`output/`), model weights, `.env`, keys.
  A fresh clone cannot reproduce results or render observations. Tests that need data are skipped.
- **Branches**: harness v2 code was pushed to the `harness-v2` branch on 2026-10-28 and may not
  be on `main` yet. Check `git log` on both branches before describing the current code.
- Results quoted in the main document point to `output/...` paths that exist only on the
  project's RunPod volume.

## 6. Current state (2026-10-29; check §10 for anything newer)

- Zero-shot baselines (controller v1, B mode) are finished. New work is harness v2.
- Harness v2 runs on a RunPod H100 with vLLM. Current design: `项目总文档.md` §3.7.13.
  - **Gemma-4-E4B-it** ran every rollout so far. It is a small model, about 4B effective
    parameters. Its weights were deleted on 2026-10-29.
  - Gemma will be **`google/gemma-4-31B-it`** (alias `gemma4_31b`), not downloaded yet: the disk
    needs about 45 GB more space first.
  - **Qwen3.5-27B** is prepared but has not run: no smoke test, no rollout.
- Four Gemma rounds on the four development channels (one-to-one F1, details in §5.7):

  | Version | CH3 | CH20 | CH30 | CH31 | Main failure |
  |---|---|---|---|---|---|
  | no curation (lower bound) | 0 | 0 | 0.195 | 0.425 | |
  | skill_v1 | 0 | 0 | 0 | 0 | over-merging |
  | skill_v2 (stopped at 400 decisions) | 0 | 0 | 0.198 | 0.436 | noise kept as small fragments |
  | skill_v3 | 0 | 0 | 0.705 | 0.864 | noise removed, but the model discarded the mixed root cluster, which held units |
  | skill_v4 | 0.529 | 0.531 | 0.445 | 0.515 | only 4/9 units found: mixtures labelled clean and kept unsplit; large noise clusters kept |

  Each round changed the observation, output labels or skill after an error analysis. The noise
  analysis showed that every initial leaf is pure (all noise or all one unit), and that waveform
  morphology (trough half-width, variability) separates noise from units, while refractoriness of
  small leaves does not.
- **Caveat**: from skill_v2 on, all four channels were used for error analysis, and the
  morphology thresholds in observation v2.3 were calibrated on their expert labels. No result here
  is held-out generalization, and the thresholds depart from the §3.7 rule that expert-derived
  rules belong only in the skill (§3.7.13).
- **Live jobs**: check §9 before acting. Runs write to `output/local_rollout_gemma4_*`. While a run
  is going, do not stop the GPU Pod, kill vLLM, or edit frozen files (the shared disk makes edits
  invalidate the run). The network volume is 98% full.
- **Not on GitHub yet**: changes after the first round (`harness-v2` branch, `cb2a921`) are only in
  the working tree on the RunPod volume.

## 7. Rules for an agent working on this repository

Default to read-only. Ask the project owner before doing any of the following.

- Do not modify MAT files or expert labels. Do not open the reserved block `cM2-e007_012-017`.
- Never put expert labels, expert reasons or scores into model inputs. They are for offline
  scoring only.
- Do not overwrite or delete historical outputs, ledgers, paid responses, frozen prompts or
  frozen configs. New runs go to new `output/` directories.
- No paid API calls without an explicit budget from the owner. Never print or copy keys.
- Do not commit data, images, outputs, weights, `.env`, private keys or personal documents.
  Do not use `git add -A`: the working tree contains private, untracked material.
- Do not edit the frozen harness files (listed in `FROZEN` in `scripts/run/run_local_rollout.py`)
  while a rollout is running. The runner checks their hashes at the end.
- Rules and skills must not mention channel names, cluster IDs or target cluster counts.

## 8. How to answer questions well

- Cite the section of `项目总文档.md` and the file path for every claim, e.g. "§5.3" or
  `src/agent/curation_decider_v2.py`.
- Say which condition (controller v1, B mode, harness v2), which scoring (one-to-one or
  many-to-one), and which skill / observation version a number comes from.
- Call results "development-set results", not generalization.
- Unfinished runs have no score. Do not report them as 0.
- To check behaviour without data, run the offline tests:
  `.venv/bin/python -m pytest -q tests/test_curation_controller_v2_rounds.py tests/test_skill_decider_v2.py tests/test_curation_observation_v2.py`.
  The full suite (`tests/`) has two long-standing failures caused by absolute paths from before
  the RunPod migration.
- If something is unclear or contradictory, say so and point to where; do not guess.
