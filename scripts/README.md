# Script Entry Points

## New numbered entrypoints (MEArec simulation pipeline)

Run these in order for the full research pipeline:

```bash
# Stage 1: Generate MEArec recordings + overclustering
uv run python scripts/01_simulate.py --config configs/settings/setting_001.yaml

# Resume one channel without changing the setting's configured n_channels
uv run python scripts/01_simulate.py --config configs/settings/setting_002.yaml \
  --channel-id ch_007

# Stage 2: Build ground-truth action trajectories
uv run python scripts/02_build_actions.py --config configs/settings/setting_001.yaml --all-channels

# Stage 3: Run teacher-student interaction
uv run python scripts/03_run_trajectories.py --config configs/settings/setting_001.yaml \
  --student-model gpt-4o --teacher-model gpt-4o --all-channels

# Stage 4: Few-shot adaptation
uv run python scripts/04_adapt.py --config configs/settings/setting_001.yaml

# Stage 5: Evaluate alignment
uv run python scripts/05_evaluate_alignment.py --config configs/settings/setting_001.yaml --all-channels

# Stage 6: Sweep over all settings
uv run python scripts/06_sweep.py --settings-dir configs/settings/ --jobs 4

# Audit action-class coverage before adaptation/evaluation
uv run python scripts/07_audit_action_coverage.py \
  --config configs/settings/setting_001.yaml
```

## Legacy scripts (real MATLAB data)

### `run/`
End-to-end pipeline runs on real CH3/CH20/CH30/CH31 data:
- `run_single_channel.py` — pure VLM pipeline on one channel (configurable via CLI arg)
- `run_all_channels.py` — pure VLM on all channels (multiprocessing)
- `run_baseline_pipeline.py` — VLM + heuristics baseline on all channels
- `run_ablation_no_metrics.py` — VLM without numerical metrics
- `run_single_channel_qwen35.py` — single channel with finetuned Qwen3.5 backbone
- `run_real_manifest.py` — preflight or sequentially run every labeled real MAT by full dataset id; unlabeled CH5 is excluded by default
- `run_qwen35_full_cycle.sh` — sequential: base eval → finetune → finetuned eval
- `run_gemma4_full_cycle.sh` — Gemma-4 full cycle
- `run_rag_backbone_unit_compare.sh` — vLLM replay unit test (`no_rag` vs `rag`) for Qwen3.5-4B + Gemma4-E4B, with auto plotting

```bash
uv run python scripts/run/run_single_channel.py CH3
uv run python scripts/run/run_all_channels.py
```

### `finetune/`
Dataset construction and model training:
- `build_finetune_dataset.py` — build JSONL + image assets from MATLAB action sheets
- `export_real_manifest_actions.py` — ordered, provenance-preserving export for all labeled current-format real MATs
- `prepare_hf_dataset.py` — convert to HuggingFace datasets format
- `split_finetune_dataset_by_channel.py` — train/eval split by channel
- `train_qwen35_unsloth.py` — Unsloth Vision SFT for Qwen3.5-4B
- `train_gemma4_unsloth.py` — Unsloth Vision SFT for Gemma-4-E4B

Real-data safe defaults:

```bash
# Load/hash/shape/label preflight for all 16 labeled MATs; no model calls.
uv run python scripts/run/run_real_manifest.py --mode preflight

# Export all ordered expert actions and images; resumes per dataset.
uv run python scripts/finetune/export_real_manifest_actions.py --resume

# Run one local-vLLM dataset after the server is ready.
uv run python scripts/run/run_single_channel_qwen35.py \
  --mat-path "path/to/CH_spikes.mat" \
  --dataset-id cM2-e004_001-003_CH1 \
  --recording-block cM2-e004_001-003 \
  --provider vllm --model Qwen/Qwen3.5-4B
```

The real-data runner disables the Phase 0 and Phase 3 size rules by default.
Set their thresholds explicitly only for a documented ablation. Parse/provider
failures produce `ABSTAIN` or stop the run; they never silently become mock or
destructive decisions. Batch subprocess output is streamed to one log per
dataset so long runs remain resumable without accumulating console output in
memory.

### `aggregate/`
- `aggregate_results.py` — cross-channel metrics aggregation
- `aggregate_baseline.py` — aggregate baseline pipeline results
- `collect_runpod_experiment.py` — collect compact Runpod results, provenance, environment metadata, logs, and SHA-256 checksums into one transfer archive

After a Runpod evaluation finishes, collect its outputs before stopping the Pod:

```bash
uv run python scripts/aggregate/collect_runpod_experiment.py \
  --run-dir output/qwen35_ch30_smoke \
  --extra-file output/vllm_logs/qwen35_base.log \
  --run-name qwen35_ch30_smoke_20260921
```

The resulting directory and `.tar.gz` contain predictions, metrics, raw model
responses, text logs, the Git commit, GPU/software metadata, file sizes, and
SHA-256 checksums. The collector deliberately does not recursively copy the run
directory, so MAT data, diagnostic images, model weights, caches, and `.env`
files are excluded. Verify a downloaded bundle from inside its directory with
`sha256sum -c checksums.sha256`.

### `analysis/`
- `compute_human_curation.py` — analyze human curation patterns
- `evaluate_baseline.py` — evaluate baseline pipeline
- `generate_curation_stats_table.py` — statistics tables
- `visualize_clusters.py` — visualize cluster waveforms
- `audit_real_action_replay.py` — replay legacy CSV actions without calling a VLM
- `audit_real_action_sources.py` — compare MAT-internal, CSV, and Excel action sources
- `audit_real_mat_internal_actions.py` — audit all current MAT action logs against final assignments

### `plot/`
- `plot_ablation.py` — ablation test results
- `plot_comparison.py` — pipeline performance comparison
- `plot_f1_scores.py` — F1 score plots
- `plot_results.py` — overall results
- `plot_sft_accuracy_grouped.py` — grouped before/after accuracy bars
- `plot_rag_backbone_unit_compare.py` — RAG/no-RAG bars + overall compare + step learning curves (Qwen/Gemma)

### `test/`
- `eval_unit_actions_from_dataset.py` — unit-test style action accuracy
- `test_vlm_unit_decisions.py` — VLM decision unit tests

### `demo/`
- `demo_simulated_teacher_feedback.py` — one-shot teacher feedback demo
- `run_channel_teacher_budget.py` — limited teacher interaction budget demo
