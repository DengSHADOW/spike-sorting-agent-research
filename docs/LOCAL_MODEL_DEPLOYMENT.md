# RunPod local Qwen27B and Gemma4 deployment

Prepared on 2026-10-26 at the user's request. Download and installation are CPU
operations. No model inference or experiment is started during preparation.
The completion evidence is in `output/local_model_setup_20261026/`.

CPU preparation completed: both model snapshots passed all file checksums;
vLLM 0.32.0, PyTorch 2.13.0 (CUDA 13.0 build), and Transformers 5.19.0 are
installed in `.venv-vllm`; the 198-package dependency check passed. Both models'
configuration and processor objects loaded successfully in offline mode without
loading any weights. Four offline integrity/startup-guard tests passed. GPU
loading and real image inference remain untested, and no service is running.
The GPU vLLM CLI's `serve --help` also tries to infer a device and fails on this
CPU-only Pod with `Failed to infer device type`; CLI startup must be verified
on the GPU Pod. The deployment script's `--dry-run` works on CPU without
invoking vLLM, and its startup options are present in the installed vLLM source.

| Alias | Model | Pinned revision | Precision | Suggested GPU | Status (2026-10-29) |
|---|---|---|---|---|---|
| `qwen27b` | `Qwen/Qwen3.5-27B` | `fc05daec18b0a78c049392ed2e771dde82bdf654` | BF16 | 80 GB | downloaded and verified |
| `gemma4_31b` | `google/gemma-4-31B-it` | `842da3794eaa0b77d5f08bae87a17459d91ff475` | BF16 | 80 GB | planned, not downloaded |
| `gemma4` | `google/gemma-4-E4B-it` | `ee0ef6023621cff504d758262d4e04895a5af4a2` | BF16 | 24 GB+ | **retired**: weights deleted |

The download totals are 55,586,167,982 bytes (Qwen) and 62,578,686,074 bytes (Gemma 31B); the deleted
E4B was 16,024,823,729 bytes.

Gemma-4-E4B-it ("E4B" = about 4B effective parameters, 8.0B stored including per-layer embeddings and
the vision/audio encoders) ran all harness v2 rollouts of 2026-10-28/29. At the user's request it is
replaced by the 31B dense model of the same family. Gemma-4-31B-it has 31.27B BF16 parameters, the same
architecture (`Gemma4ForConditionalGeneration`) and reasoning parser, Apache-2.0, and is not gated
(Hugging Face API, checked 2026-10-29). The E4B weights were deleted on 2026-10-29. Its config entries
are kept with a `retired` note, so the historical runs stay interpretable, and the launcher refuses to
download or serve it.

**Before downloading the 31B model, free disk space.** It needs 62.6 GB plus a 5 GB margin, and the
volume had about 19 GB free after the E4B deletion. Options are deleting the Qwen cache after the Qwen
runs, or enlarging the network volume, which costs money and needs the owner's approval. The KV-cache
capacity of the 31B on one H100 80 GB has not been measured: 62.6 GB of weights leave little room at
`gpu_memory_utilization` 0.85. Keep the model
cache at `/workspace/.cache/huggingface/hub` on the persistent network volume.
The user authorized removal of the old Qwen3.5-9B cache; the deletion inventory
is `output/local_model_setup_20261026/qwen9b_deletion.json`. Historical model
responses, datasets and experiment results are retained.

## Environments and downloads

The existing project `.venv` is separate from both new environments:

- `.venv-model-tools`: Python 3.12 and `huggingface_hub==2.3.0` for downloads.
- `.venv-vllm`: Python 3.12 and `vllm==0.32.0` for later GPU serving.

To recreate the environments with `uv`:

```bash
uv venv .venv-model-tools --python 3.12
uv pip install --python .venv-model-tools/bin/python huggingface_hub==2.3.0
uv venv .venv-vllm --python 3.12
uv pip install --python .venv-vllm/bin/python --no-cache vllm==0.32.0
```

Download/resume and verify the exact revisions in `configs/local_models.json`:

```bash
.venv-model-tools/bin/python scripts/deploy/local_models.py download all
.venv-model-tools/bin/python scripts/deploy/local_models.py status
```

Every downloaded file is checked against its official SHA-256 or Git blob SHA-1.
The Qwen weight index is also checked for missing shards. Failed checks stop the
command; no inference happens. To repeat the complete checksum verification:

```bash
.venv-model-tools/bin/python scripts/deploy/local_models.py verify all
```

## Start later on a GPU Pod

Attach the same network volume. Verify the actual NVIDIA driver, GPU memory and
CUDA compatibility with the installed PyTorch/vLLM build. CPU installation does
not establish GPU compatibility or successful model loading. Virtualenvs also
need compatible Python binaries and system libraries after changing Pod images;
use the pinned installation commands above if the new image differs.

Start one model at a time in the foreground:

```bash
.venv-model-tools/bin/python scripts/deploy/local_models.py serve qwen27b
# Stop with Ctrl+C before switching models.
.venv-model-tools/bin/python scripts/deploy/local_models.py serve gemma4
```

Append `--dry-run` to print a command without starting anything. The launcher
refuses to start without a visible NVIDIA runtime, uses the verified local
snapshot with Hugging Face offline mode, and binds to `127.0.0.1:8000`.
Defaults (changed 2026-10-28 for harness v2, 项目总文档 §3.7.5): BF16, context
length 24,576 tokens, up to four concurrent sequences, six images per request,
and the model-specific reasoning parser. The number of sequences that actually
fit in GPU memory must be measured in the first smoke test. This context limit
includes input images/text plus generated reasoning and final output. There is
no automatic service startup, model switch, API fallback or experiment execution.

Measured on 2026-10-28 (H100 SXM 80 GB, driver 595.91, CUDA 13.2, image
`runpod/pytorch:1.4.1-cu1281-torch280-ubuntu2404`): the existing `.venv-vllm`
(torch 2.13.0+cu130, vLLM 0.32.0) loads without reinstalling. The first start
failed during warm-up because FlashInfer JIT-compiles its top-k/top-p sampler and
`ninja` is not on `PATH` (the system `nvcc` is 12.8, which does not match
cu130 either). The launcher now sets `VLLM_USE_FLASHINFER_SAMPLER=0` unless the
variable is already set, so vLLM uses the PyTorch sampler; the sampling
parameters are unchanged.

Qwen3.5-27B load test (2026-10-29, same settings, no request sent): 51.0 GiB for weights, 14.0 GiB
KV cache (189,493 tokens, about 7.7 concurrent 24,576-token requests), ready in about 5 minutes.
The server was stopped afterwards; smoke and rollouts have not been run.

With these settings Gemma-4-E4B-it used 14.6 GiB for weights and left KV cache
for about 50 concurrent 24,576-token requests. The CH31 smoke test took 7–20 s
per request with thinking on, and four concurrent requests ran 3.98× faster
than serial (`output/smoke_gemma4_20261028/smoke.json`).

The CPU Pod could not reach the GPU Pod's exposed TCP port. Commands were sent
through the RunPod SSH proxy (`ssh -tt <pod>@ssh.runpod.io`, PTY only), and
the server and runner both ran on the GPU Pod against the shared volume.

The OpenAI-compatible client settings are:

```bash
export VLLM_BASE_URL=http://127.0.0.1:8000/v1
# Use --provider vllm and the corresponding served name:
# Qwen/Qwen3.5-27B or google/gemma-4-E4B-it
```

The generic `src/agent/api.py` vLLM provider can use this endpoint. Historical
OpenRouter minimal/harness runners are separate and still require a local
transport adaptation; setting this variable does not redirect those runners.
No historical prompts, controllers or scores were changed by this deployment.
Thinking and sampling settings must be explicitly frozen by the future caller;
the launcher does not establish a new experiment protocol. These BF16 weights
also differ from the historical Qwen27B FP8 API deployment.

References: [Qwen model](https://huggingface.co/Qwen/Qwen3.5-27B),
[Gemma model](https://huggingface.co/google/gemma-4-E4B-it),
[Gemma vLLM guide](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html).

## Harness v2 single-channel rollout (2026-10-28)

The harness v2 runner talks to the server above through `src/agent/vllm_provider.py`
(`/v1/chat/completions`, `json_schema` strict output, thinking enabled, sampling
from `configs/controller_v2/models.json`). Check the whole pipeline on CPU first,
with no model and no network:

```bash
.venv/bin/python scripts/run/run_local_rollout.py --model gemma4 --scripted-offline \
    --output-root output/<new folder>
```

Then, with one model served on the GPU Pod:

```bash
.venv/bin/python scripts/run/run_local_rollout.py --model gemma4 --parallel 4 \
    --output-root output/<new folder>
```

`--parallel` only changes timing; questions, answers and the final state do not
depend on it. A run that hits a limit is reported as a stopped state.
