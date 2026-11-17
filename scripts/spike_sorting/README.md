# Spike sorting pilot

The task is to curate one existing extracellular recording channel. The policy
chooses the next cluster and operation from a numeric shortlist rebuilt after
each edit. The engine supports `KEEP`, `SPLIT`, `MERGE`, `DISCARD`, and `FINISH`.
It reuses the spike sorting project's executor and terminal scorer. The expert
trace never sets the action order. The shortlist is heuristic and the pilot is
text-only.

## Run on CH3

Use a Python environment with NumPy, SciPy, h5py, Matplotlib, SpikeInterface,
PyYAML, and the dependencies of the spike sorting checkout. The adapter was
developed against `spike-sorting-agent-tianmin` at
`638d60e1da3b7fc3a94ec0ca64dc1837ede10228`. Provide the existing CH3 MAT
separately. MAT files, copied rationales, and outputs stay local.

```bash
python scripts/spike_sorting/run.py \
  --spike-repo /path/to/spike-sorting-agent-tianmin \
  --mat /path/to/CH3_spikes.mat \
  --dataset-id cM2-e004_004-006_CH3 \
  --mode selective --max-queries 120 --max-turns 220 \
  --output runs/ch3-rule-selective
```

| Mode | Review behavior |
|---|---|
| `none` | Run the same policy without guidance. Discard is permitted in this offline control. |
| `discard` | Seek approval before discard. |
| `selective` | Seek approval before discard and let the policy ask about other choices. |
| `always` | Seek approval before each proposed operation, including keep. |

For required reviews, an unavailable answer or exhausted budget preserves the
current assignments. The adviser checks current spike membership against an
independently replayed expert trace. Exact matches can include a recorded
rationale, derived answers are labelled, and unsupported current states get an
abstention. The pilot requires the trace to replay to the same partition as the
scoring labels. CH3 satisfies this check. The local CH20 and CH31 MAT logs
yield different partitions from their recorded scoring labels.

By default, the numeric rule policy picks the highest-scoring available option
and uses a fixed uncertainty rule. To use a local language model, add
`--local-model /path/to/hf/checkpoint --model-python /path/to/model-env/bin/python`.
The worker was built for a local Qwen2.5-Instruct checkpoint with PyTorch and
Transformers 5.3. It uses logits over the offered choices and can select whether
to ask and predict the reply. It reads model weights from disk. The adapter calls
CoResearch's `HumanHarness` directly after the policy requests review. The pilot
uses a fixed harness and stores the reply and prediction outcome. It does not
exercise the Pi or Claude Code transport, memory updates, or cross-channel carryover.

Read `summary.json` for the initial and terminal full-output spike F1, units
matched by one-to-one IoU at or above 0.5, expert spikes discarded, and number
of queries. If the turn limit is reached, the score is reported under
`stopped_state_score`. `decisions.jsonl` records the shortlist, review response,
and executed choice on each turn. `human_history/` keeps the CoResearch
question packets and answers. The number of queries measures requests to the
simulated adviser. Human review time requires a separate measurement.
