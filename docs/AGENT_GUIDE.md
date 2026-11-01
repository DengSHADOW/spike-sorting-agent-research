# Handoff guide for people and AI agents

For the next person working on this project and for their AI coding agent (Claude Code, Codex,
Cursor, etc.). Claude Code loads it automatically through `CLAUDE.md` in the repository root. For
Codex, copy it to `AGENTS.md` in your own clone; there is no `AGENTS.md` in the shared RunPod copy,
because a Codex session works on the website there. Read sections 1 and 2 first.

Last checked against the repository: 2026-10-29; the run plan note in section 1 was added
2026-10-31. If anything here disagrees with `项目总文档.md`
§10 (the change log), the change log is newer.

---

## 1. State in five minutes

> **From 2026-10-31, every new run follows the plan from the 2026-10-31 meeting
> (`项目总文档.md` §3.8; the owner decided this).** Each channel runs on its own as a strict
> expert replay. The result is a within-channel learning curve: decision index against
> sliding-window accuracy, with 3–5 seeds per configuration. The methods are base (no learning),
> ACE (`ace_only` only) and in-context. CH31 and CH3 are used for transfer, KEEP is left out for
> now, and questions come in the expert's order. Do not run the older designs for new results.
> These are the CH31→CH3→CH30 chain with a frozen CH20 test, and the split into training and
> evaluation channels. Items the meeting left open (§3.8.4) must be settled before a run starts.
> The bullets below are as of 2026-10-29. The replay code lives only in the `/workspace/replay-*`
> copies (§5.8). GPU jobs now go through the `gpuq` queue on the H200 Pod (`docs/GPU_QUEUE.md`),
> which forbids the `setsid nohup` pattern in section 5.

- **The project** is a post-curation agent for spike sorting. A vision-language model looks at
  rendered evidence for one cluster, or one pair of clusters, at a time and chooses KEEP, SPLIT,
  MERGE or DISCARD. The final clustering is scored against an expert curation (section 3).
- **Zero-shot baselines are finished.** They are controller v1 with Opus 5.5, Astra and Qwen, and
  "B mode" with Opus 5.5. The four-channel mean F1 of the best one is 0.444.
- **Current work is harness v2.** The model is served on a RunPod H100 by vLLM. Four rounds were run
  with Gemma-4-E4B-it, each followed by an error analysis and a harness or skill change:

  | Version | CH3 | CH20 | CH30 | CH31 | Mean | Main failure |
  |---|---|---|---|---|---|---|
  | no curation (lower bound) | 0 | 0 | 0.195 | 0.425 | 0.155 | |
  | skill_v1 | 0 | 0 | 0 | 0 | 0 | over-merging |
  | skill_v2 (stopped at 400 decisions) | 0 | 0 | 0.198 | 0.436 | 0.159 | noise kept as small fragments |
  | skill_v3 | 0 | 0 | 0.705 | 0.864 | 0.392 | the model deleted the mixed root clusters, and their units with them |
  | **skill_v4 (current)** | 0.529 | 0.531 | 0.445 | 0.515 | **0.505** | 4/9 units: mixtures labelled clean and kept unsplit; large noise clusters kept |

  These are one-to-one F1 scores on development data, not held-out results (section 9, item 1).
- **Models.** Gemma-4-E4B-it (about 4B effective parameters) was retired and its weights deleted.
  Gemma will be `google/gemma-4-31B-it`, which is not downloaded yet: the disk needs space first.
  Qwen3.5-27B is downloaded, verified and load-tested. Nothing has been run with it.
- **Nothing is running.** The GPU Pod still bills $4/hour while it is on.
- **Decisions waiting for the owner** (`项目总文档.md` §7):
  1. Run Qwen3.5-27B on skill_v4 as it is.
  2. Or first add a harness check that rejects labels contradicted by the measurements.
  3. Free disk space for Gemma 31B.
  4. Raise vLLM concurrency (now 4; memory allows about 7 for Qwen) and the 400-decision limit.

## 2. Where the truth is

| Source | What it is | Trust |
|---|---|---|
| `项目总文档.md` (Chinese) | The single maintained document: status, methods, results, known issues, plans, change log. | Authoritative. Current harness design: §3.7.13. Harness results: §5.7. Pods and live runs: §9. Pending decisions: §7. Change log: §10, newest first. |
| `docs/AGENT_GUIDE.md` (this file) | English orientation and runbook. | A summary. The main document wins on conflict. |
| `docs/LOCAL_MODEL_DEPLOYMENT.md` | Model downloads, vLLM serving, measured GPU memory. | Current. |
| `output/harness_v2_build_20261028/PROGRESS.md` (RunPod volume only) | Timestamped log of every harness v2 step, in Chinese. | Detailed history; not in Git. |
| `output/<run>/REPORT.md`, `result.json`, `rollout/events.jsonl` | Evidence for each run. | Evidence, not documentation. |
| `README.md` | Short English overview and repository layout. | Brief. |
| `docs/archive/` | Superseded reports. | History only; never present it as current. |
| Code and `configs/` | Ground truth for behaviour. | If code and document disagree, report it; do not silently pick one. |

The main document is in Chinese. Translate when answering, and keep identifiers (file names,
dataset IDs, field names) unchanged.

## 3. Key concepts

- **Dataset ID**: recording block + channel, e.g. `cM2-e004_004-006_CH31`. Always use the full ID.
  The four development channels are CH3, CH20, CH30 and CH31. "CH31" is an electrode channel, not
  a neuron. The project does not detect spikes and is not a new sorter.
- **Executor** (`src/cluster/manager.py`, `ClusterManager`): SPLIT undoes the cluster's *last*
  hierarchical merge, so the model never chooses spike memberships. MERGE adds a tree row, DISCARD
  sets spikes to 0, and KEEP / NOT_MERGE change nothing. Initial leaves are 100% pure on all four
  channels: each is all noise or all one expert unit.
- **Evaluations**: *stepwise* replays the expert's actions and asks the model at each expert state;
  *rollout* lets the model drive the whole channel and scores the final clustering. All harness v2
  results so far are rollouts.
- **Scoring**: the primary metric is one-to-one matching (Hungarian, IoU ≥ 0.5), with every
  predicted cluster counted in precision. Older results use many-to-one. **Never compare the two.**
  One-to-one F1 can reward deleting a mixed cluster once its largest unit is already separate,
  which is why skill_v3 scored higher than skill_v4 on CH30 and CH31. Always report units found
  and expert spikes deleted next to F1.
- **Rollout conditions**, reported separately: controller v1 (finished), B mode (finished) and
  harness v2 (current).
- **Harness v2 layers**:
  - a round-synchronous controller;
  - observation v2.3: images plus measurements;
  - a frozen contract;
  - a versioned **skill** of domain rules, each with its sources;
  - **gates**: skill entries that the harness applies to the model's *own* labels, in order, each
    acting on the action left by the previous one;
  - "observe-then-act" output: the model labels each image before it chooses an action.

  Gated decisions log the model's original action.
- **Stopped state**: a run that hits a decision, time or round limit is scored separately as
  `stopped_state`. Runs that end on an error are not scored.

## 4. Environment

| Thing | Where / what |
|---|---|
| Network volume | RunPod `spike-sorting-data`, mounted at `/workspace` on both pods; 140 GB, about 87% full. Only `/workspace` survives a pod reset. |
| CPU Pod ("SpikeCPU") | Development, tests, analysis and the expert-review website (backend on port 8765, private gateway on 8888, both run by a Codex session). No GPU. |
| GPU Pod | 1× H100 80 GB (US-CA-2, $4.00/h). Serves vLLM on `127.0.0.1:8000` and runs the rollouts. The CPU Pod cannot reach its TCP port; use the SSH proxy (`scripts/deploy/gpu_exec.sh`). Pod ID and SSH details: `项目总文档.md` §9. |
| Python environments | `.venv` (harness, tests, scoring; Python 3.10), `.venv-vllm` (vLLM 0.32.0, torch 2.13.0+cu130), `.venv-model-tools` (downloads and the launcher), `.venv-interface` (website). Do not modify anything outside the project. |
| Model cache | `/workspace/.cache/huggingface/hub/`: only Qwen3.5-27B (55.6 GB) now. |
| Other folders in `/workspace` | `START_HERE.md`; `spike-sorting-agent-harness-v2` (a clean checkout of an older commit); `spike-sorting-agent-tianmin` (a collaborator's clone with uncommitted work; leave it alone); `spike-history-archive-20261029` (archived old backups, see its README); `claude-sessions` (copies of Claude Code session logs). |
| People | Project owner: Tengyang Deng (GitHub `DengSHADOW`). The expert curation, including 286 action reasons, is by Jacob Bedke (`Jacob Bedke-Annotated_spike_sorting_data_w_chronux/`). The website is maintained separately (`mockups/curation-review/`). |

## 5. Runbook

All commands run from `/workspace/spike-sorting-agent`.

**Offline checks (CPU, no model, no network)**

```bash
.venv/bin/python -m pytest -q tests/test_curation_observation_v2.py tests/test_skill_decider_v2.py \
    tests/test_curation_controller_v2_rounds.py tests/test_vllm_provider_v2.py tests/test_local_models_deployment.py
.venv/bin/python -m pytest -q tests/ --continue-on-collection-errors   # full suite, see below
.venv/bin/python scripts/run/run_local_rollout.py --model qwen27b --scripted-offline --parallel 4 \
    --output-root output/scripted_e2e_<channel>_<date>                  # end-to-end without a model
```

The full suite gives 543 passed with 2 long-standing failures, caused by absolute paths from before
the RunPod migration. 3 files do not collect, because `.venv` lacks yaml and joblib.

**On the GPU Pod** (start the pod in the RunPod console first)

```bash
export GPU_SSH=<pod-id>-<suffix>@ssh.runpod.io GPU_KEY=~/.ssh/<your key>
G=scripts/deploy/gpu_exec.sh
$G 'nvidia-smi --query-gpu=name,memory.used --format=csv,noheader'

# 1. start one model server, detached (one model fits at a time); ready after about 5 minutes for Qwen
$G 'setsid nohup .venv-model-tools/bin/python scripts/deploy/local_models.py serve qwen27b \
      > output/harness_v2_build_20261028/gpu_logs/vllm_qwen27b.log 2>&1 < /dev/null & disown'
grep "Application startup complete" output/harness_v2_build_20261028/gpu_logs/vllm_qwen27b.log

# 2. smoke test: a few real requests, timing, truncation, field order
$G '.venv/bin/python scripts/run/smoke_local_model.py --model qwen27b --output-root output/smoke_qwen27b_<date>'

# 3. four channels at once, same settings as the Gemma rounds
$G 'for d in cM2-e004_004-006_CH3 cM2-e004_011-015_CH20 cM2-e008_021-028_CH30 cM2-e004_004-006_CH31; do
      c=$(echo ${d##*_} | tr A-Z a-z)
      setsid nohup .venv/bin/python scripts/run/run_local_rollout.py --model qwen27b --dataset $d --parallel 4 \
        --max-decisions 400 --max-seconds 5400 --output-root output/local_rollout_qwen27b_${c}_<skill>_<date> \
        > output/harness_v2_build_20261028/gpu_logs/rollout_qwen27b_${c}.log 2>&1 < /dev/null & disown; done'

# 4. monitor (the volume is shared, so this works from the CPU Pod too)
grep -c '"event": "decision"' output/local_rollout_qwen27b_*/rollout/events.jsonl
cat output/local_rollout_qwen27b_*/REPORT.md      # appears when a run ends

# 5. audit every decision against the expert composition (offline, reads expert labels)
.venv/bin/python scripts/analysis/audit_harness_v2_decisions.py output/<run directory>

# 6. stop the server, then stop the pod in the RunPod console
$G 'kill $(pgrep -f "vllm serve" | head -1)'
```

The launcher reads `configs/local_models.json`. It refuses models marked `retired` and those not
yet downloaded and verified (`local_models.py download <alias>`; it checks every file against the
official checksums). Gemma 31B is `gemma4_31b`; it needs about 68 GB free before download.

**Git**

- **Push to `main`.** The harness v2 code is on GitHub `main` (`f1ca871`, 2026-10-29). The
  temporary `harness-v2` branch was deleted the same day; do not create branches. Ask the owner
  whenever the target is unclear.
- The working tree on the RunPod volume has many uncommitted changes from several people. The
  local `main` ref may lag behind GitHub `main`, so fetch first.
- Harness commits were built with a temporary index (`GIT_INDEX_FILE`) on top of GitHub `main`, so
  that only harness files went in. Website files, other people's tests, the private backup and
  `项目总文档.md` hunks about the website were left out.
- Never `git add -A`. `spike-sorting-private-backup-20261019/` is not ignored and holds keys and
  personal documents.

## 6. Code map (harness v2)

| Part | File |
|---|---|
| Round-synchronous controller (`RoundsController`) | `src/pipeline/curation_controller_v2.py` |
| Observation v2.3 (images, measurements, morphology, spike-like fraction) | `src/agent/curation_observation_v2.py` |
| Decider: contract + skill + gates, labels, answer checks (`SkillDecider`) | `src/agent/curation_decider_v2.py` |
| vLLM provider (chat completions, json_schema, retries) | `src/agent/vllm_provider.py` |
| Contract / skills v1–v4 / model profiles | `configs/controller_v2/contract.txt`, `configs/controller_v2/skills/`, `configs/controller_v2/models.json` |
| Model downloads, checksums, serving settings | `configs/local_models.json`, `scripts/deploy/local_models.py` |
| GPU command helper (SSH proxy) | `scripts/deploy/gpu_exec.sh` |
| One-channel rollout runner (`--scripted-offline` = no model) | `scripts/run/run_local_rollout.py` |
| GPU smoke test | `scripts/run/smoke_local_model.py` |
| Decision audit against expert labels | `scripts/analysis/audit_harness_v2_decisions.py` |
| Offline lower / upper bounds | `scripts/analysis/run_controller_v2_offline_references.py` |
| Noise analysis behind skill_v3 (volume only) | `output/harness_v2_build_20261028/noise_analysis_20261029/` |
| Tests | `tests/test_curation_observation_v2.py`, `tests/test_curation_controller_v2_rounds.py`, `tests/test_skill_decider_v2.py`, `tests/test_vllm_provider_v2.py`, `tests/test_local_models_deployment.py` |

Older parts (controller v1, zero-shot runners) are listed in `项目总文档.md` §4.1.

## 7. What a GitHub clone does and does not contain

- **Not in Git**: MAT data, rendered images, run outputs (`output/`), model weights, `.env`, keys
  and the progress log. A fresh clone cannot reproduce results or render observations; tests that
  need data are skipped.
- Results quoted in the documents point to `output/...` paths that exist only on the RunPod volume.

## 8. Rules for an agent working on this repository

Default to read-only. Ask the project owner before doing any of the following.

- Do not modify MAT files or expert labels. Do not open the reserved block `cM2-e007_012-017`.
- Never put expert labels, expert reasons or scores into model inputs. They are for offline
  scoring and analysis only.
- Do not overwrite or delete historical outputs, ledgers, paid responses, frozen prompts or
  frozen configs. New runs go to new `output/` directories. A new skill is a new file
  (`skill_v5.json`, with `parent` and per-rule `sources`); old skills stay unchanged.
- No paid API calls without an explicit budget from the owner. Never print or copy keys.
- Do not start, stop or create billable resources (pods, volume size, downloads that need more
  disk) without the owner's approval.
- Do not commit data, images, outputs, weights, `.env`, private keys or personal documents.
- **While a rollout runs, do not edit its frozen files** (`FROZEN` in
  `scripts/run/run_local_rollout.py`, plus the skill in use). Both pods share the disk. The runner
  checks the hashes at the end and marks the run `changed_during_run` and unscored.
- Do not touch `mockups/curation-review/` (the website) unless asked; another session maintains it.
- Rules and skills must not mention channel names, cluster IDs or target cluster counts.

## 9. Lessons and pitfalls

1. **Everything after skill_v1 is fitted to the four development channels.** The skills were
   written from their errors, and the observation v2.3 thresholds (half-width 0.2 ms, variability
   0.15) were calibrated on their expert labels. This departs from the design rule that
   expert-derived rules live only in the skill (§3.7.13). Report every number as a
   development-set result.
2. **The small model misreads both images and numbers.** It labelled clusters with 10% ISI
   violations and saturated contamination as "clean". Gates only check the model's labels against
   each other, so they cannot fix a wrong label.
3. **Waveform morphology, not refractoriness, separates noise here**: trough half-width AUC 0.96,
   variability 0.93, refractoriness of small leaves 0.66 (§5.7).
4. **Splitting noise makes noise look clean.** Small fragments have sparse autocorrelograms.
   The expert discards contaminated noise branches whole.
5. **vLLM on this pod** needs `VLLM_USE_FLASHINFER_SAMPLER=0` (no `ninja`, nvcc mismatch); the
   launcher sets it. Gemma's thinking must be switched on explicitly in the chat template kwargs.
6. **The SSH proxy needs a PTY**, and jobs must be detached, or they die with the connection.
   The helper script handles the first; use `setsid nohup ... & disown` for the second.
7. **Run time** is set by the number of rounds, not by the number of requests: deep splits
   proceed one tree level per round. Four channels on one server took 7–65 minutes per round of
   experiments.
8. **The disk filled up once** (98%). Check `df -h /workspace` before downloads and long runs.
9. **The website's Agent_2 slot** points at the retired `gemma4` alias
   (`mockups/curation-review/backend/server_agents.py`). It needs updating when Gemma 31B arrives.

## 10. How to answer questions well

- Cite the section of `项目总文档.md` and the file path for every claim, e.g. "§5.7" or
  `src/agent/curation_decider_v2.py`.
- Say which condition (controller v1, B mode, harness v2), which model, which skill / observation
  version and which scoring (one-to-one or many-to-one) a number comes from.
- Unfinished runs have no score; do not report them as 0.
- If something is unclear or contradictory, say so and point to where; do not guess.
