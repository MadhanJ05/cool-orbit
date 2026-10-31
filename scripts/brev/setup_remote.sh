#!/usr/bin/env bash
# Runs ON a Brev GPU instance (x86 or ARM), never on the GB10:
#     brev exec <instance> @scripts/brev/setup_remote.sh
# Installs the verified ALCHEMI environment, clones Cool Orbit (public), and runs the GPU smoke test incl. a
# torch.compile measurement. Idempotent: safe to re-run. Full log: ~/cool-orbit/runs/remote_setup.log
# No sudo needed: uses uv-managed Python (ships its own headers, which Triton/torch.compile need).
set -euo pipefail

REPO_URL="https://github.com/MadhanJ05/cool-orbit.git"
DIR="$HOME/cool-orbit"
export PATH="$HOME/.local/bin:$PATH"
export UV_PYTHON_PREFERENCE=only-managed

echo "== host: $(uname -m), $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1)"
CUDA_MAJOR=$(nvidia-smi | sed -n 's/.*CUDA Version: \([0-9]*\)\..*/\1/p' | head -1)
if [ "${CUDA_MAJOR:-0}" -ge 13 ]; then EXTRA=cu13; BACKEND=cu130; else EXTRA=cu12; BACKEND=cu126; fi
echo "== driver supports CUDA ${CUDA_MAJOR}: using ${EXTRA} / torch backend ${BACKEND}"

command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
if [ -d "$DIR/.git" ]; then git -C "$DIR" fetch -q origin && git -C "$DIR" checkout -q --detach origin/main
else git clone -q "$REPO_URL" "$DIR"; fi
cd "$DIR"
mkdir -p runs
exec > >(tee -a runs/remote_setup.log) 2>&1

[ -d .venv ] || uv venv --seed --python 3.12
uv pip install --torch-backend "$BACKEND" --index-strategy unsafe-best-match \
  "nvalchemi-toolkit[${EXTRA},mace,ase]==0.2.0" 'plotext<6' numpy scipy

.venv/bin/python - <<'EOF'
import torch, nvalchemi
x = torch.randn(1024, 1024, device="cuda")
print("== nvalchemi", nvalchemi.__version__, "| torch", torch.__version__, "| CUDA", torch.version.cuda,
      "|", torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0), "| matmul ok:", bool((x @ x).isfinite().all()))
EOF

echo "== smoke test (cuEquivariance), then the same with torch.compile"
.venv/bin/python scripts/smoke_throughput.py --kernels cueq --out runs/smoke_remote
.venv/bin/python scripts/smoke_throughput.py --kernels cueq --compile --out runs/smoke_remote
echo "== setup done at commit $(git rev-parse --short HEAD)"
