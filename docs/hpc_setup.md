# HPC setup and connection guide

This guide explains how to reconnect to the existing TU Berlin HPC deployment,
start its Llama model server, and connect the pipeline to it. The model runs on
a GPU allocated by Slurm; vLLM receives the pipeline's requests and returns
the extracted information.

## Before you start

You need a TU Berlin account with HPC/GPU access, SSH access, and the project's
prepared serving environment. This is a reconnection guide, not a complete
first-time cluster installation guide. The following are prepared separately
on the HPC and are not distributed in this repository:

- A Singularity container containing vLLM, and access to the Llama model weights.
- A Slurm script named `serve-llama31.sbatch` in the serving directory's `jobs/` folder.
- The script's node/port output files, model cache, and logs.
- Your access credentials and the pipeline's Python environment.

Examples below use `YOUR_TU_USERNAME` and `YOUR_HPC_USERNAME`: replace them
with your accounts. Adjust paths if your installation differs. Never put
access tokens or API keys in this repository.

## 1. Connect from your computer

In your local terminal (for example Ubuntu/WSL):

```bash
ssh YOUR_TU_USERNAME@sshgate.tu-berlin.de
```

Complete the requested authentication, then connect from the gateway:

```bash
ssh gateway.hpc.tu-berlin.de
```

The remaining commands run in the HPC terminal. The frontend is used to
prepare and submit the GPU job; the model itself runs on the allocated node.

## 2. Select your environment and paths

These paths reflect the existing project setup; change them if needed:

```bash
source "$HOME/thesis-venv/bin/activate"
export PIPELINE_DIR="$HOME/thesis-scenario-pipeline"
export LLM_SERVING_DIR="/scratch/YOUR_HPC_USERNAME/llm-api"
cd "$PIPELINE_DIR"
git branch --show-current
git status --short
```

Use the intended project revision (`Refacturing` for this guide). Check local
changes before updating the checkout. Some installations keep the repository
under scratch storage instead of the home directory.

## 3. Start the existing model-serving job

Check whether your server is already running before submitting another job:

```bash
squeue -u "$USER"
sbatch "$LLM_SERVING_DIR/jobs/serve-llama31.sbatch"
squeue -u "$USER"
```

Record the submitted job ID. The prepared script specifies the GPU resources
and starts vLLM inside its container. Development used A100 and H200 queues;
the partition and GPU request must match the resources available to your account.

`PD` means pending, and `R` means the job has started. The model may still be
loading after the job starts. Wait for the connection check below to succeed.

## 4. Connect the pipeline to the current server

The project's serving script records its current node and port in these files.
Check that they belong to the job you just started; old files may remain after
an earlier job stops. Do not reuse a node address from a previous session.

```bash
cat "$LLM_SERVING_DIR/server-node.txt"
cat "$LLM_SERVING_DIR/server-port.txt"
export LLM_BASE_URL="http://$(cat "$LLM_SERVING_DIR/server-node.txt"):$(cat "$LLM_SERVING_DIR/server-port.txt")/v1"
export LLM_API_KEY="$(cat "$HOME/.secrets/vllm_api_key")"
export LLM_MODEL="llama31"
```

The key file must already contain the credential configured for this server.
Keep its contents private. Check the connection without running extraction:

```bash
curl --fail --silent --show-error "$LLM_BASE_URL/models" \
  -H "Authorization: Bearer $LLM_API_KEY" | python3 -m json.tool
```

The response should list the served model `llama31`. An OpenAI-compatible
interface describes the request format; this deployment runs the model on HPC.

If the connection fails, inspect the current job's log (replace `1234567`
with its actual ID):

```bash
export LLM_JOB_ID=1234567
tail -n 80 "$LLM_SERVING_DIR/logs/llama31-api-${LLM_JOB_ID}.out"
```

Check the queue, loading progress, node/port files, and credentials. Run these
checks within the HPC network; the internal compute-node URL is not a public
endpoint accessible directly from a laptop.

## 5. Run the pipeline

From the repository root in the configured HPC environment:

```bash
cd "$PIPELINE_DIR"
python3 scripts/extract_all.py
```

This runs Stage 1 over the report collection and writes extraction outputs;
use it when you intend to regenerate those results. Keep existing results if
you need to preserve an earlier experiment.

For interactive generation and subsequent esmini review, see
[scripts/README.md](../scripts/README.md). Visual playback requires an
appropriate display environment and an esmini installation.

`scripts/hpc_live_llm_verification.py` provides broader diagnostics, including
model calls and pipeline runs. It is more than a simple connection check;
use `/models` above if you only want to check server availability.

## 6. Release the GPU

When processing has finished, find your serving job and stop it. Replace the
example number with the correct ID:

```bash
squeue -u "$USER"
scancel 1234567
```

Disconnecting SSH does not necessarily stop a submitted batch job. If the
connection drops, reconnect and check the queue before submitting another.

## Recorded thesis configuration

The thesis author reported the following after checking the HPC container and
job history. These identify the final extraction run, not every development
session. The guide's commands were reviewed against the repository and setup
notes, but have not been rerun on HPC as part of this documentation change.

| Component | Recorded value |
|---|---|
| Model | `meta-llama/Llama-3.1-8B-Instruct` |
| Model revision | `0e9e39f249a16976918f6564b8830bc894c89659` |
| Served alias | `llama31` |
| vLLM | 0.24.0 |
| Python inside serving container | 3.12.13 |
| Container runtime | SingularityCE 4.4.0 |
| Source image tag | `vllm/vllm-openai:latest` |
| Saved container SHA-256 | `050a58c4d02fe4ab81b4cd3916476cdf44b2a0f63949cfa12e998d6063338ce4` |
| Final extraction GPU | One NVIDIA H200 |
| Node / Slurm job | `gpu069` / `1879432` |
| Extraction date | 2026-08-17 |

The `latest` tag can change. The saved container checksum identifies the
specific file used; downloading the tag again does not guarantee the same file.
A cached model snapshot also needs to be matched to the revision actually
served when recording a new experiment.
