# SpikeSorting

A multimodal agent framework for iterative post-curation in spike sorting.

SpikeSorting studies whether VLM-based agents can assist or automate the expert curation process after an initial spike sorting pass. The project starts from initial clusters produced by hierarchical clustering or overclustering, then mimics how human experts inspect, split, merge, and discard clusters using waveform views, quality metrics, and iterative feedback.

The long-term goal is to build a scalable research pipeline for simulated and real extracellular recordings, supporting controlled MEArec-based benchmarking, expert-like action trajectory construction, teacher-student interaction, few-shot adaptation, memory-augmented curation, and future RL / continual learning across heterogeneous lab settings.

## Platform support

This project is currently supported on Linux and macOS only. Windows users should use WSL2 instead.
The main reason is that the `neuron` package does not provide a Windows wheel, so the environment is not reliable on native Windows setups.

---

## Overview

![SpikeSorting pipeline overview](figures/spikesorting_pipeline.png)

The full curation pipeline proceeds in three main phases:

**Phase 0: Neuronal filtering.**  
A Neuronal Agent scans all initial clusters once and removes clusters that fail extracellular spike-shape criteria.

**Phase 1: Recursive split refinement.**  
A DFS-style traversal applies a Split Agent to large neuronal clusters. Clusters with high internal variability are recursively partitioned until they satisfy quality and consistency criteria.

**Phase 2: Merge and discard.**  
Small clusters are compared against large clusters by a Merge Agent. Matched clusters are merged into their corresponding neuronal units, while unmatched or non-neuronal clusters are discarded.

This design turns spike sorting post-curation into a long-horizon multimodal decision process: the agent observes waveform plots and diagnostic metrics, proposes an action, receives feedback, updates the cluster state, and continues until the recording is curated.

---

## What This Project Does

SpikeSorting currently supports two complementary settings:

1. **Real-data VLM curation**
   - Load MATLAB-based spike sorting outputs.
   - Render cluster-level diagnostic figures.
   - Run VLM agents for split / merge / discard decisions.
   - Evaluate agent decisions against curated labels or expert-derived targets.
   - Extend to Memory and online RL for adaptation to different channel settings and lab needs.

2. **Simulated benchmark pipeline**
   - Generate controlled extracellular recordings with MEArec-style simulation.
   - Produce initial overclustered states.
   - Construct canonical ground-truth curation actions.
   - Run teacher-student interaction trajectories.
   - Evaluate step-level action alignment and reasoning alignment.
   - Study adaptation across different channel settings, noise regimes, drift patterns, and lab-specific curation requirements.

---

## Current Experimental Status

The repository contains both the original real-data experiments and a completed
Stage 1-6 end-to-end run of the simulated benchmark. The counts below describe
the artifacts currently retained in `output/`; they are intended to make the
state of the research reproducible and to distinguish pipeline verification
from model-quality claims.

### Simulated Stage 1-6 run

The completed end-to-end run uses `setting_001`: 20 simulated 120-second
recordings, low noise (10 uV RMS), no drift, no overlap, a Neuronexus-32 probe,
MountainSort5, and the strict teacher criteria.

| Stage | Retained record | Status / interpretation |
|---|---:|---|
| 1. Simulation and overclustering | 20-channel raw archive | Raw recordings and derived arrays were generated for all channels, then compressed to `output/archives/setting_001_raw_20260820.tar.gz`; live `raw/` directories are no longer retained. |
| 2. Canonical actions | 34 valid GT actions | The corrected rebuild contains 18 `KEEP` and 16 `SPLIT` actions. The older 234-action artifact was archived after 200 non-progressing `SPLIT` actions from `ch_017` were identified as invalid. |
| 3. Teacher-student trajectories | 82 current steps across 20 channels | The corrected mock rebuild verifies state transitions and RAG data flow; one channel produced zero post-filter steps. |
| 4. Adaptation data | 12 train channels, 5 eval channels, 8 training examples | The retained `n12_seed0_qwen35` run prepared JSONL and Hugging Face data. Only non-`KEEP` mistakes are sampled, so the current eight examples are all `SPLIT`; no trained checkpoint is retained. |
| 5. Alignment | 8 channel reports | Retained aggregate: action accuracy `0.8205`, mean edit distance `0.875`, reasoning similarity `0.5`. These are mock-path diagnostics; the high action accuracy is dominated by `KEEP`, while observed `SPLIT` accuracy is zero. |
| 6. Sweep aggregation | 1 setting | The Stage 6 orchestration and aggregation completed for `setting_001`. `setting_002` and `setting_003` are configured, but a complete three-setting sweep is not currently retained. |

The simulated run therefore verifies that all six stages connect and produce
their expected artifacts. It does **not** yet establish a strong learned
student baseline: the current alignment summary comes from mock predictions,
the reasoning score is a mock-path value, and the adaptation dataset is too
small and class-imbalanced for a meaningful SFT conclusion.

### RAG verification

Continual retrieval-augmented generation is integrated into both Phase 1
cluster decisions and Phase 2 merge decisions. The memory combines median
waveform-template similarity with numeric feature similarity (default weights
`0.7` and `0.3`) and injects the top-k past cases into the student prompt.

The retained standalone RAG verification produced:

- 84 persisted memory entries (`output/test_rag_memory_v2.jsonl`, about 4.53 MB);
- 84 trajectory steps, including per-step `rag_enabled`, `rag_hits`, and the
  retrieved examples;
- RAG hits in 19 of 20 channels (the first query starts with an empty memory);
- up to 3 retrieved examples per step with the default `top_k=3`;
- a separate three-entry real-API smoke-test memory (`output/rag_real_test.jsonl`).

This confirms the RAG storage, retrieval, prompt injection, persistence, and
trajectory-observability path. A controlled same-model `no_rag` versus `rag`
evaluation is still required before claiming an accuracy improvement. See
[`RAG_IMPLEMENTATION_COMPLETE.md`](RAG_IMPLEMENTATION_COMPLETE.md) for the
implementation record and verification command.

The later corrected Stage 3-5 mock rebuild contains 82 trajectory and memory
rows. The separate 84-row RAG artifact is retained as an earlier integration
test, not as the current canonical Stage 3 trajectory.

### Retained real-data results

The legacy MATLAB-data experiments cover CH3, CH20, CH30, and CH31. The table
reports the mean of each channel-level overall metric from the retained
aggregate reports.

| Method | Precision | Recall | F1 |
|---|---:|---:|---:|
| Before curation | 0.2247 | 0.8855 | 0.3536 |
| VLM + heuristic baseline | 0.2327 | 1.0000 | 0.3712 |
| GPT-4.1 curation | 0.5479 | 0.8606 | 0.5861 |
| GPT-5.1 curation | **0.7653** | 0.8327 | **0.7671** |
| GPT-5.1 without numeric metrics | 0.6315 | 0.8244 | 0.6624 |
| Human-curated reference | 1.0000 | 1.0000 | 1.0000 |

Within the retained VLM runs, GPT-5.1 has the strongest mean F1. Removing
numeric metrics reduces mean F1 from `0.7671` to `0.6624`, supporting the use of
waveform/quality measurements alongside images. The human row is the curated
reference target, not an independent blind human benchmark. The source MAT
files are not version-controlled. They are currently available only in the
local `Tianmin_Annotated_data/` and
`Jacob Bedke-Annotated_spike_sorting_data_w_chronux/` directories.

A read-only audit of the local real data on 2026-09-02 found 17 current-format
MAT files (4,469,063 spikes): 16 have final curation labels and executable
internal action logs, while one CH5 file has no curation target. The four MAT
files under `Tianmin_Annotated_data/` are byte-identical duplicates of four
Jacob files. CH3 and CH31 have Excel action sources that replay exactly to the
matching final labels; the other curated files remain valid as separate
action-level and cluster-level targets rather than a single unified trajectory.

### Safe real-data student path

Real datasets are identified by the complete recording-block/channel id (for
example, `cM2-e004_004-006_CH3`), not by `CH3` alone. The manifest batch runner
excludes unlabeled CH5 by default and writes one resumable directory per
dataset. The current real-data defaults also disable the uncalibrated Phase 0
and Phase 3 spike-count filters. Provider failures never fall back silently to
mock decisions, parse failures return `ABSTAIN`, and `NOT_MERGE` preserves a
distinct cluster rather than discarding it.

Training and runtime now share the same Phase 1 observation contract: waveform
overlay, ISI histogram, amplitude distribution, aggregation tree, and numeric
ISI/amplitude metrics. The first local-student target is action-only JSON; an
optional rationale remains compatible with runtime logging. Qwen/Gemma SFT
splits are defined by inferred recording block, with the CH3/CH31 block held
out from the default train/validation split.

The complete 2026-09-14 run record, data limitations, and open-model roadmap are
documented in [`REAL_DATA_OPEN_VLM_STATUS_20260914.md`](REAL_DATA_OPEN_VLM_STATUS_20260914.md).

---

## Repository Structure

```text
src/
  io/                 # Load MATLAB / MEArec data
  cluster/            # Cluster state management and split / merge operations
  agent/              # VLM API calls, prompts, runners, and teacher feedback
  eval/               # Metrics and evaluation utilities
  pipeline/           # Existing end-to-end pipeline utilities
  ablation/           # Ablation and comparison code

  simulate/           # Stage 1: MEArec generation and overclustering
  actions/            # Stage 2: Canonical GT action trajectory construction
  trajectories/       # Stage 3: Teacher-student interaction trajectories
  adapt/              # Stage 4: Few-shot / SFT adaptation
  alignment/          # Stage 5: Step-level action and reasoning alignment
  scale/              # Stage 6: Multi-setting sweep and aggregation

scripts/
  run/                # Existing run scripts
  finetune/           # Finetuning baselines
  aggregate/          # Result aggregation
  analysis/           # Analysis utilities
  plot/               # Plotting scripts
  demo/               # Demo scripts
  test/               # Unit-style evaluation scripts
```
