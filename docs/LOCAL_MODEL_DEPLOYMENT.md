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

| Alias | Model | Pinned revision | Precision | Suggested GPU |
|---|---|---|---|---|
| `qwen27b` | `Qwen/Qwen3.5-27B` | `fc05daec18b0a78c049392ed2e771dde82bdf654` | BF16 | 80 GB |
| `gemma4` | `google/gemma-4-E4B-it` | `ee0ef6023621cff504d758262d4e04895a5af4a2` | BF16 | 24 GB+ |

The download totals are 55,586,167,982 and 16,024,823,729 bytes. Keep the model
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
Defaults: BF16, context length 16,384 tokens, one concurrent sequence, four
images per request, and the model-specific reasoning parser. This context limit
includes input images/text plus generated reasoning and final output. There is
no automatic service startup, model switch, API fallback or experiment execution.

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
