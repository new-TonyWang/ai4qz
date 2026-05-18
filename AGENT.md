# ai4qz Agent Guide

This file tells AI agents how to use the `ai4qz` CLI to control remote qz notebooks.

## Environment Setup

Before running any ai4qz command, check and install the package:

```bash
# Step 1: Check if ai4qz is available
python3 -c "import ai4qz" 2>/dev/null && echo "ok" || echo "missing"

# Step 2: If missing, install from the project directory
pip install -e /path/to/ai4qz
```

After installation, all `ai4qz` commands are available directly.

## Notebook Topology

Use `ai4qz list` and `ai4qz check` to discover available notebooks and their status:

```bash
# List all configured notebooks
ai4qz list

# Check if a notebook is reachable (cookies, xsrf, terminals, contents)
ai4qz check <notebook_name>

# Deep check (also probes with a real command)
ai4qz check <notebook_name> --deep
```

Typical topology:

- **GPU notebook** (e.g. `h200_ncu`): Has GPU(s), no internet
- **Dev notebook** (e.g. `qz_dev`): No GPU, has internet

Both share the same filesystem. Install dependencies on the dev notebook, use them from the GPU notebook.

## Timeout — Critical

**Default command timeout is 60 seconds.** Any command that takes longer will be killed silently. You MUST set `--timeout` for long-running tasks.

`--timeout` is a **global** flag — it goes BEFORE the subcommand:

```bash
# WRONG — --timeout after subcommand will be swallowed by REMAINDER
ai4qz run h200_ncu --timeout 300 --cmd 'python3 train.py'

# CORRECT — --timeout before subcommand
ai4qz --timeout 300 run h200_ncu --cmd 'python3 train.py'
```

Timeout guide:

| Task type | Suggested `--timeout` |
|-----------|----------------------|
| Quick commands (ls, pwd, nvidia-smi) | Default 60s is fine, omit `--timeout` |
| pip install | `--timeout 300` |
| Training < 1 epoch | `--timeout 600` |
| Long training / benchmark | `--timeout 3600` or higher |
| Uncertain duration | Set generously — there is no penalty for a large timeout |

`--timeout` applies to `run`, `fanout`, and `session-run`. It **overrides** the value in `configs/notebooks.yaml`.

## Running Commands

```bash
# One-off command (creates temp terminal, runs, deletes)
ai4qz run h200_ncu --cmd 'nvidia-smi'

# Check GPU details
ai4qz run h200_ncu --cmd 'nvidia-smi --query-gpu=name,memory.total,utilization.gpu --format=csv,noheader'

# Install dependency on a notebook with internet
ai4qz --timeout 300 run qz_dev --cmd 'pip install some-package'

# Batch execute across notebooks by tag
ai4qz fanout --tag active --cmd 'hostname'
```

## File Transfer

```bash
# Upload local -> notebook
ai4qz upload h200_ncu ./local.txt /remote/path/local.txt

# Download notebook -> local (specify local path)
ai4qz download h200_ncu /remote/path/file.txt ./file.txt

# Download to current directory (omit local_path, uses remote filename)
ai4qz download h200_ncu /remote/path/file.txt
```

### Download fallback

The Jupyter contents API can only access files under the notebook's working directory. Files outside it will return 404. In that case, ai4qz automatically falls back to terminal base64 transfer.

For large files or paths known to be outside Jupyter root, use `--via-terminal` to skip the contents API attempt:

```bash
ai4qz download h200_ncu /outside/jupyter/root/file.txt --via-terminal
```

## Upload-then-Execute Pattern

```bash
# Upload script
ai4qz upload h200_ncu ./train.py /remote/workdir/train.py

# Execute it with a generous timeout
ai4qz --timeout 3600 run h200_ncu --cmd 'cd /remote/workdir && python3 train.py'
```

## Persistent Sessions

For running multiple commands in the same shell context:

```bash
# Open session
ai4qz session-open h200_ncu --tmux

# Run commands in it (--timeout applies)
ai4qz --timeout 300 session-run <id> --cmd 'conda activate myenv'
ai4qz session-run <id> --cmd 'python3 train.py'

# Close when done
ai4qz session-close <id>
```

## Common Mistakes

- **Timeout too short**: The #1 failure cause. Always set `--timeout` for anything beyond trivial commands.
- **`--timeout` after subcommand**: Won't work. Must be before: `ai4qz --timeout 300 run ...`
- **Installing on a notebook without internet**: Use a dev notebook with internet access instead.
- **Files outside Jupyter root**: If download 404s, ai4qz auto-falls back to terminal transfer; or use `--via-terminal` proactively.
