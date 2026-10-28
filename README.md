# SpikeSorting

A multimodal agent for post-curation in spike sorting: starting from an initial
hierarchical clustering, a vision-language model inspects waveforms, ISI,
amplitude and hierarchy views and splits, merges or discards clusters, and the
final assignments are scored against expert curation.

**All project status, methods, results, known issues and plans are maintained in
one document: [项目总文档.md](项目总文档.md)** (Chinese). Earlier documents were
merged into it on 2026-10-26; the originals are kept unchanged in
[`docs/archive/superseded_20261026/`](docs/archive/superseded_20261026/).

## At a glance (2026-10-26)

- Real data only: four development channels (`cM2-e004_004-006_CH31`, `_CH3`,
  `cM2-e004_011-015_CH20`, `cM2-e008_021-028_CH30`); results are development
  results, not held-out generalization.
- Two rollout conditions, reported separately: a fixed Python controller where the
  model answers single decisions, and an **agentic-free** condition where the model
  chooses every inspection and operation itself (the purest zero-shot setting).
- Primary metric: one-to-one (IoU ≥ 0.5, Hungarian) terminal P/R/F1 over all
  output clusters, alongside expert units recovered, expert spikes deleted and
  reference noise retained.

## Repository layout

```text
src/                 # executor (cluster/), observations and request contracts (agent/), controllers (pipeline/)
configs/curation/    # frozen system prompt, skills, protocol and scoring rules
zero-shot baseline/  # minimal-prompt and agentic-free runners, prompts, per-run results
scripts/             # run, analysis and aggregation tools (see scripts/README.md)
mockups/curation-review/  # expert review website
tests/               # offline regression tests
docs/archive/        # historical reports (not current instructions)
```

Data (MAT files), generated images, run outputs, model weights and credentials are
local assets and are never committed.

## Platform

Linux and macOS only (the `neuron` dependency has no Windows wheel; use WSL2 on Windows).

## Upstream reference

Based on JianZhi Shen's original implementation,
[jiseshen/spike-sorting-agent](https://github.com/jiseshen/spike-sorting-agent).
This repository adds fixes, audits, real-data experiments and protocol changes, so
behavior should not be assumed identical to upstream.
