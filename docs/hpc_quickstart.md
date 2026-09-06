# TU Berlin HPC Quickstart — Shayma's Thesis Pipeline

Everything below was learned the hard way across several sessions. Follow it in order, don't freestyle from memory.

---

## 1. Connect (always two hops)

```bash
ssh Shayma27@sshgate.tu-berlin.de
# → enter password, then OTP

ssh gateway.hpc.tu-berlin.de
# → enter password
```

You're now on `frontend02`. Confirm with your prompt: `shayma27@frontend02:~$`

**If a connection drops mid-session** (`client_loop: send disconnect: Broken pipe`) — this is normal, happens often, not your fault. Any SLURM job you had running keeps running regardless; just reconnect with the two commands above.

**If sshgate rejects the connection outright** (`Connection closed` or repeated `Permission denied` after correct password+OTP) — don't rapid-retry, it can look like a brute-force attempt and extend a lockout. Wait 10-15 minutes, then try once, carefully, with a freshly generated OTP.

---

## 2. Set up the environment

```bash
source ~/thesis-venv/bin/activate
cd ~/thesis-scenario-pipeline
git pull
```

Always `git pull` at the start of a session — code may have changed from a different Claude Code session or local edits pushed earlier.

---

## 3. Submit the vLLM server — THE DUAL-PARTITION RACE TRICK

The cluster has multiple GPU partitions with different node pools (`h200_short` = H200 GPUs, `gpu` = A100 GPUs, `gpu_short` = smaller/older A100 pool — sometimes drained/unavailable for days). Waiting on just one partition can mean sitting `PD` (pending) for minutes to literally days if that partition's nodes are busy or drained.

**The trick: submit to two partitions at once, take whichever starts first, cancel the other.**

### Step 3a — submit to whatever the script currently targets
```bash
sbatch /scratch/shayma27/llm-api/jobs/serve-llama31.sbatch
squeue -u $USER
```
Note the JOBID and PARTITION column.

### Step 3b — toggle the script to the OTHER partition, submit again

**If the first job went to `h200_short`, switch to `gpu`/A100:**
```bash
sed -i 's/#SBATCH --partition=h200_short/#SBATCH --partition=gpu/' /scratch/shayma27/llm-api/jobs/serve-llama31.sbatch
sed -i 's/#SBATCH --gres=gpu:h200:1/#SBATCH --gres=gpu:a100:1/' /scratch/shayma27/llm-api/jobs/serve-llama31.sbatch
```

**If the first job went to `gpu`, switch to `h200_short`:**
```bash
sed -i 's/#SBATCH --partition=gpu/#SBATCH --partition=h200_short/' /scratch/shayma27/llm-api/jobs/serve-llama31.sbatch
sed -i 's/#SBATCH --gres=gpu:a100:1/#SBATCH --gres=gpu:h200:1/' /scratch/shayma27/llm-api/jobs/serve-llama31.sbatch
```

Then submit the second one:
```bash
sbatch /scratch/shayma27/llm-api/jobs/serve-llama31.sbatch
squeue -u $USER
```

### Step 3c — watch both, cancel the loser
```bash
watch -n 10 squeue -u $USER
```
(Ctrl+C to stop watching once one flips to `ST=R`)

Once one is `R`, **immediately cancel the other**, using its actual JOBID:
```bash
scancel <the_still_PD_jobid>
```

**Never submit more than one job per partition at a time** — if you're impatient and resubmit again on a partition that already has a pending job, you just end up with multiple duplicates all competing with yourself. One job per partition, two partitions max, that's the whole trick. If you lose track, clean up:
```bash
squeue -u $USER
scancel <jobid1> <jobid2> <jobid3>   # cancel all but the one that's actually running
```

---

## 4. Connect to the running vLLM server

**Do NOT hardcode a node name or port from memory or an old note — always read the live files.** The script uses a dynamic port (`8000 + SLURM_JOB_ID % 1000`) specifically so multiple jobs never collide on the same port.

```bash
cat /scratch/shayma27/llm-api/server-node.txt
cat /scratch/shayma27/llm-api/server-port.txt
```

```bash
export LLM_BASE_URL="http://$(cat /scratch/shayma27/llm-api/server-node.txt):$(cat /scratch/shayma27/llm-api/server-port.txt)/v1"
export LLM_API_KEY="$(cat ~/.secrets/vllm_api_key)"
export LLM_MODEL="llama31"
```

**Confirm it's actually serving before running anything real** — `ST=R` in squeue only means the SLURM job started, NOT that vLLM finished loading the 8B model into GPU memory (that takes 2-5 minutes: weight loading + torch.compile on first launch).

```bash
curl -s "$LLM_BASE_URL/models" -H "Authorization: Bearer $LLM_API_KEY" | python3 -m json.tool
```

Should print real JSON listing `llama31` / `meta-llama/Llama-3.1-8B-Instruct`.

**If it's empty or connection-refused**, don't assume something's broken — check the log first:
```bash
tail -f /scratch/shayma27/llm-api/logs/llama31-api-<jobid>.out
```
Wait for the line `Starting vLLM server on http://0.0.0.0:<port>`, then Ctrl+C and retry the `curl`.

---

## 5. Run the pipeline

```bash
cd thesis-scenario-pipeline     # repo root — the pipeline code lives directly here now
python run_batch_19.py          # or whatever the current entry point is — CHECK FIRST, see below
```

**Before running, if it's been a while since you last touched this repo, confirm the entry point still exists and hasn't changed:**
```bash
grep -n "^def \|^class " pipeline.py
```
Pipeline internals have changed multiple times (new modules like `speed_estimation.py`, `provenance.py`, `osm_audit_report.py` have been added in past sessions) — don't assume last week's script still matches this week's code without checking.

---

## 6. When you're done — stop the GPU job

Don't let it sit idle burning allocation:
```bash
squeue -u $USER
scancel <jobid>
```

---

## Common mistakes that wasted real time (don't repeat these)

- **Typing commands into the wrong terminal.** You have (at least) two terminals in play: local WSL (`chimo@ShaymaHichri`) and the HPC session (`shayma27@frontend02`). Commands like `/mnt/c/Users/...` only work locally; `/scratch/...` only works on HPC. Check your prompt before pasting.
- **Literal placeholder brackets.** `sbatch <your_script.sh>` and `scancel <jobid>` — the `< >` means "put the real value here," never type them literally. Bash reads `<` as file-redirect and throws a syntax error.
- **Path mixups from nested directories.** If you're already inside the repo root, don't prefix paths with the repo name again. Use `pwd` if unsure where you are.
- **git push with a password instead of a token.** GitHub disabled plain password auth years ago. Username: your GitHub username. Password field: a Personal Access Token (repo scope), generated at github.com → Settings → Developer settings → Personal access tokens. Set `git config --global credential.helper 'cache --timeout=31536000'` once so you're not regenerating this every session.
- **Assuming `R` means ready.** It means the SLURM job started. It does NOT mean vLLM finished loading the model. Always `curl` before trusting the connection.
