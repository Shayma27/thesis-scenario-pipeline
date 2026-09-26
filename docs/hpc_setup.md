# HPC Setup and Connection Guide

This document describes the language-model inference environment used by
this thesis on the TU Berlin HPC cluster: how it was built, and how to
reconnect to it and run it. It is written for a reader with no prior HPC
experience. No credentials appear in this document; all values requiring
authentication are represented by placeholders (`YOUR_HPC_USERNAME`, etc.).

The pipeline requires a large language model (Llama-3.1-8B-Instruct) to
extract structured information from accident reports. Running this model at
usable speed requires a GPU, which is provided by the TU Berlin HPC cluster.
The model is served through vLLM, exposing an OpenAI-compatible API that the
pipeline queries as it would any hosted LLM provider.

The document has two parts: **Part A** covers the one-time setup of this
environment; **Part B** covers reconnecting to it and running the pipeline in
a new session. A reader setting this up for the first time follows both
parts in order; a reader who already has the environment built can skip to
Part B.

---

## Part A — One-time setup

### A.1 Cluster access

Access to TU Berlin's HPC cluster is granted per chair/institute and is not
self-service. Access requires:

- A TU Berlin account with HPC/GPU access, arranged through the supervising
  chair.
- A designated Slurm account for billing compute time (this project uses
  `iit`).
- SSH access to the cluster gateway, typically included with the HPC grant.

Reference: TU Berlin, "High-Performance Computing (HPC)" — <https://www.tu.berlin/campusmanagement/angebot/high-performance-computing-hpc>;
HPC-Cluster-Dokumentation — <https://hpc.wiki.tu-berlin.de/doku.php?id=start>

Connection path from a local machine (for example Ubuntu/WSL):

```bash
ssh YOUR_TU_USERNAME@sshgate.tu-berlin.de
ssh gateway.hpc.tu-berlin.de
```

The first hop requires password and, depending on account configuration,
one-time-password authentication. The second lands on an HPC frontend node
(e.g. `frontend02`). All commands below run on the frontend node. The model
itself is never run on the frontend node directly — it only runs inside a
scheduled Slurm job with an allocated GPU.

Note the shell prompt changes at each hop:

```
you@your-laptop:~$              ← your own computer
YOUR_TU_USERNAME@sshgate-XX:~$  ← TU Berlin SSH gateway
YOUR_HPC_USERNAME@frontend02:~$ ← HPC frontend node
```

### A.2 Working-directory structure

Home directories on the cluster are not intended for large files. Model
weights and container images are stored under scratch storage:

```bash
mkdir -p /scratch/YOUR_HPC_USERNAME/llm-api/{containers,hf-cache,logs,jobs,tmp,singularity-cache}
export LLM_SERVING_DIR="/scratch/YOUR_HPC_USERNAME/llm-api"
```

Directory purpose:

| Path | Contents |
|---|---|
| `containers/` | Singularity image file |
| `hf-cache/` | Downloaded model weights |
| `logs/` | Job stdout/stderr |
| `jobs/` | Slurm batch scripts |
| `tmp/` | Scratch temporary space |
| `singularity-cache/` | Singularity's internal cache |

The pipeline repository itself is cloned separately, into the home
directory:

```bash
cd "$HOME"
git clone <pipeline-repository-URL>
```

### A.3 Model-weight access

`meta-llama/Llama-3.1-8B-Instruct` is a gated model on Hugging Face;
authentication is required before the weights can be downloaded.

1. Request access to the model on its Hugging Face model page.
2. Once granted, generate a read-only access token under Hugging Face
   account settings.
3. Store the token on the cluster, outside the project directory, readable
   only by the owning user:

```bash
mkdir -p "$HOME/.secrets" && chmod 700 "$HOME/.secrets"
echo "<hf_token>" > "$HOME/.secrets/hf_token"
chmod 600 "$HOME/.secrets/hf_token"
```

The token is never committed to the repository, referenced in scripts by
literal value, or written to logs.

Reference: Hugging Face documentation, "Gated models" — <https://huggingface.co/docs/hub/models-gated>;
"User access tokens" — <https://huggingface.co/docs/hub/en/security-tokens>

### A.4 Container build

The cluster runs containers through SingularityCE (version 4.4.0 in this
setup), not Docker. vLLM's OpenAI-compatible server is distributed as a
Docker/OCI image and converted directly by SingularityCE:

```bash
cd "$LLM_SERVING_DIR/containers"
singularity pull vllm-openai.sif docker://vllm/vllm-openai:latest
sha256sum vllm-openai.sif
```

The checksum should be recorded for reproducibility: the `latest` tag is
mutable, and the checksum is the only way to later confirm which build was
actually used for a given experiment. In this project's deployment, the
resulting image was 7.6 GB with checksum
`050a58c4d02fe4ab81b4cd3916476cdf44b2a0f63949cfa12e998d6063338ce4`.

Reference: SingularityCE, "singularity pull" — Singularity User Guide 3.7 — <https://docs.sylabs.io/guides/3.7/user-guide/cli/singularity_pull.html>

### A.5 API authentication

Independently of the Hugging Face token, the vLLM server requires an API
key to prevent unauthenticated access from elsewhere on the cluster's
internal network:

```bash
echo "$(openssl rand -hex 32)" > "$HOME/.secrets/vllm_api_key"
chmod 600 "$HOME/.secrets/vllm_api_key"
```

### A.6 Slurm batch script

The batch script requests a GPU allocation and starts the containerized
vLLM server on whichever node Slurm assigns. Saved at
`$LLM_SERVING_DIR/jobs/serve-llama31.sbatch`:

```bash
#!/bin/bash
#SBATCH --job-name=llama31-api
#SBATCH --account=iit
#SBATCH --partition=gpu_short
#SBATCH --gres=gpu:a100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/YOUR_HPC_USERNAME/llm-api/logs/llama31-api-%j.out

set -euo pipefail

LLM_SERVING_DIR="/scratch/YOUR_HPC_USERNAME/llm-api"
HF_TOKEN="$(cat "$HOME/.secrets/hf_token")"
VLLM_API_KEY="$(cat "$HOME/.secrets/vllm_api_key")"

# Port derived from the Slurm job ID to avoid collisions between
# concurrently running jobs.
PORT=$((8000 + SLURM_JOB_ID % 1000))

echo "$SLURMD_NODENAME" > "$LLM_SERVING_DIR/server-node.txt"
echo "$PORT" > "$LLM_SERVING_DIR/server-port.txt"

singularity exec --nv \
  --bind "$LLM_SERVING_DIR/hf-cache:/root/.cache/huggingface" \
  --env HF_TOKEN="$HF_TOKEN" \
  --env VLLM_API_KEY="$VLLM_API_KEY" \
  "$LLM_SERVING_DIR/containers/vllm-openai.sif" \
  python3 -m vllm.entrypoints.openai.api_server \
    --model meta-llama/Llama-3.1-8B-Instruct \
    --served-model-name llama31 \
    --port "$PORT" \
    --enable-auto-tool-choice \
    --tool-call-parser llama3_json
```

Notes on script parameters:

- `--partition` / `--gres` must match resources actually available to the
  Slurm account in use. This project used `gpu_short` (A100) and
  `h200_short` (H200) at different points; the partition and GPU type are
  coupled and must be changed together.
- `--enable-auto-tool-choice --tool-call-parser llama3_json` enables vLLM's
  Llama-compatible structured tool-calling output, required by the
  pipeline's extraction agents.
- `server-node.txt` / `server-port.txt` record the runtime location of the
  server so it can be located later without inspecting the job log — this
  is what Part B's reconnection steps read from.

Reference: vLLM documentation, "Tool Calling" — <https://docs.vllm.ai/en/latest/features/tool_calling/>

### A.7 First submission and verification

```bash
sbatch "$LLM_SERVING_DIR/jobs/serve-llama31.sbatch"
squeue -u "$USER"
```

`PD` indicates the job is queued; `R` indicates it has started (model
loading may still be in progress). Confirm the server is responding:

```bash
export LLM_BASE_URL="http://$(cat "$LLM_SERVING_DIR/server-node.txt"):$(cat "$LLM_SERVING_DIR/server-port.txt")/v1"
export LLM_API_KEY="$(cat "$HOME/.secrets/vllm_api_key")"

curl --fail --silent --show-error "$LLM_BASE_URL/models" \
  -H "Authorization: Bearer $LLM_API_KEY" | python3 -m json.tool
```

A response listing `llama31` confirms the container, model, and API key are
all functioning correctly.

### A.8 Pipeline environment

```bash
cd "$HOME/thesis-scenario-pipeline"
python3 -m venv thesis-venv
source thesis-venv/bin/activate
pip install -r requirements.txt

export LLM_MODEL="llama31"
python3 scripts/extract_all.py
```

Successful output under `data/` confirms the deployment is functioning end
to end. Setup is now complete; subsequent sessions follow Part B.

---

## Part B — Reconnecting and running the pipeline

Part A is one-time. This part covers what changes every session: a new SSH
login, restarting the model server, and connecting the pipeline to it.

### B.1 Connect from your computer

```bash
ssh YOUR_TU_USERNAME@sshgate.tu-berlin.de
ssh gateway.hpc.tu-berlin.de
```

Complete the requested authentication, then continue on the frontend node.

### B.2 Select the environment and repository state

```bash
source "$HOME/thesis-venv/bin/activate"
export PIPELINE_DIR="$HOME/thesis-scenario-pipeline"
export LLM_SERVING_DIR="/scratch/YOUR_HPC_USERNAME/llm-api"
cd "$PIPELINE_DIR"
git checkout main
git branch --show-current
git status --short
```

Use the intended project revision (`main` for this guide, the repository's
sole active branch) — `git branch --show-current` only reports which branch
you're on; it does not switch to it, so the `git checkout` line is required
if a fresh checkout or a different branch is currently active. Check local
changes (`git status --short`) before updating the checkout. Some
installations keep the repository under scratch storage instead of the home
directory.

**Pushing changes:** GitHub no longer accepts your account password for
`git push` over HTTPS. Use a Personal Access Token (classic, `repo` scope,
created under GitHub → Settings → Developer settings → Personal access
tokens) as the password when prompted. To avoid re-entering it every
session:

```bash
git config --global credential.helper 'cache --timeout=31536000'
```

### B.3 Start the model-serving job

Check whether a server is already running before submitting another job:

```bash
squeue -u "$USER"
sbatch "$LLM_SERVING_DIR/jobs/serve-llama31.sbatch"
squeue -u "$USER"
```

Record the submitted job ID. `PD` means pending, `R` means the job has
started; the model may still be loading after the job starts (see B.4).

**Speeding up the queue wait.** GPU partitions can sit backed up for hours
to days while another partition is free. Rather than waiting on one queue,
the same job can be submitted to two partitions at once, keeping whichever
starts first and cancelling the other:

```bash
sed -e 's/--partition=h200_short/--partition=gpu/' \
    -e 's/--gres=gpu:h200:1/--gres=gpu:a100:1/' \
    "$LLM_SERVING_DIR/jobs/serve-llama31.sbatch" > /tmp/serve-llama31-alt.sbatch
sbatch /tmp/serve-llama31-alt.sbatch
```

(Reverse the substitutions to go the other direction. Match the exact
`--partition=`/`--gres=` lines actually in `serve-llama31.sbatch` first —
check with `grep -E '^#SBATCH.*(partition|gres)'
"$LLM_SERVING_DIR/jobs/serve-llama31.sbatch"`.)

```bash
watch -n 10 squeue -u "$USER"
```

Once one job shows `R`, cancel the still-pending one:

```bash
scancel <job_id_of_the_pending_one>
```

Because the port is derived from the Slurm job ID (A.6), running two
submissions briefly in parallel does not cause a port collision.

### B.4 Connect the pipeline to the current server

The serving script records its current node and port. Old files may remain
from an earlier job; do not reuse a node address from a previous session.

```bash
cat "$LLM_SERVING_DIR/server-node.txt"
cat "$LLM_SERVING_DIR/server-port.txt"
export LLM_BASE_URL="http://$(cat "$LLM_SERVING_DIR/server-node.txt"):$(cat "$LLM_SERVING_DIR/server-port.txt")/v1"
export LLM_API_KEY="$(cat "$HOME/.secrets/vllm_api_key")"
export LLM_MODEL="llama31"
```

**These exports live only in the current shell.** They do not persist
across a new SSH login, a new terminal tab, or a reconnect after a dropped
connection. If `Connection error` appears partway through a session — even
though the server was working minutes earlier — re-run the four lines
above (re-reading `server-node.txt`/`server-port.txt` fresh, since the
node/port can change between job submissions) before assuming something
else is broken. This is the most common cause of connection failures in
practice.

Check the connection without running extraction:

```bash
curl --fail --silent --show-error "$LLM_BASE_URL/models" \
  -H "Authorization: Bearer $LLM_API_KEY" | python3 -m json.tool
```

The response should list the served model `llama31`.

If the connection fails, inspect the current job's log (replace `1234567`
with its actual ID):

```bash
export LLM_JOB_ID=1234567
tail -n 80 "$LLM_SERVING_DIR/logs/llama31-api-${LLM_JOB_ID}.out"
```

Check the queue, loading progress, node/port files, and credentials. Run
these checks within the HPC network; the internal compute-node URL is not a
public endpoint accessible directly from a laptop.

### B.5 Run the pipeline

```bash
cd "$PIPELINE_DIR"
python3 scripts/extract_all.py
```

This runs Stage 1 over the report collection and writes extraction outputs;
use it when regenerating results, and keep existing results if an earlier
experiment needs to be preserved.

For interactive generation and subsequent esmini review, see
[scripts/README.md](../scripts/README.md). Visual playback requires an
appropriate display environment and an esmini installation.

`scripts/hpc_live_llm_verification.py` provides broader diagnostics,
including model calls and pipeline runs; use `/models` (B.4) if only
checking server availability.

### B.6 Release the GPU

```bash
squeue -u "$USER"
scancel 1234567
```

Disconnecting SSH does not stop a submitted batch job. If the connection
drops, reconnect and check the queue before submitting another. If the
dual-partition trick (B.3) was used, confirm no leftover job remains before
ending the session.

---

## Recorded thesis configuration

The values below identify the final extraction run, not every development
session. The commands in this document were reviewed against the repository
and setup notes but have not been rerun on HPC as part of this documentation
change.

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
specific file used; downloading the tag again does not guarantee the same
file. A cached model snapshot also needs to be matched to the revision
actually served when recording a new experiment.

## References

- TU Berlin, "High-Performance Computing (HPC)" — <https://www.tu.berlin/campusmanagement/angebot/high-performance-computing-hpc>
- HPC-Cluster-Dokumentation — <https://hpc.wiki.tu-berlin.de/doku.php?id=start>
- SingularityCE, "singularity pull" — Singularity User Guide 3.7 — <https://docs.sylabs.io/guides/3.7/user-guide/cli/singularity_pull.html>
- Hugging Face documentation, "Gated models" — <https://huggingface.co/docs/hub/models-gated>
- Hugging Face documentation, "User access tokens" — <https://huggingface.co/docs/hub/en/security-tokens>
- vLLM documentation, "Tool Calling" — <https://docs.vllm.ai/en/latest/features/tool_calling/>
