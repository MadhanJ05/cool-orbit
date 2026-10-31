# Sandbox image for comparison arms A and B (coolorbit/eval/sandbox.py builds and runs it).
#
# Build ONLY through `python -m coolorbit.eval.sandbox build`: it stages a minimal build context
# (this file, the ALCHEMI skills, the board tool) so the repo -- tasks.json, CLAUDE.md, .env, our
# referee code -- is never in the context and can't end up in the image.
#
# The GB10 is ARM64 + Blackwell (CUDA 13): torch/nvalchemi come as cu130 wheels; the host driver
# is provided by `docker run --gpus all` (NVIDIA Container Toolkit).
ARG BASE=python:3.12-slim-bookworm
FROM ${BASE}

ARG WITH_ALCHEMI=1
ARG NVALCHEMI_VERSION=0.2.0
ARG CLAUDE_CODE_VERSION=latest

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp/home \
    DISABLE_AUTOUPDATER=1

# build-essential: MACE's torch.compile/Inductor kernels need a C compiler (GB10 build: "InductorError: Failed to
# find C compiler").
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates curl git nodejs npm \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" && npm cache clean --force

RUN pip install "pydantic>=2.11" "plotext<6"

# WITH_ALCHEMI=0 skips the multi-GB CUDA stack (laptop plumbing tests only; never for real runs).
RUN if [ "$WITH_ALCHEMI" = "1" ]; then \
      pip install --extra-index-url https://download.pytorch.org/whl/cu130 \
                  --extra-index-url https://pypi.nvidia.com \
                  "nvalchemi-toolkit[cu13,mace,ase]==${NVALCHEMI_VERSION}"; \
    fi

COPY skills/ /opt/alchemi-skills/
COPY tool/ /opt/coolorbit-tool/
RUN chmod 0755 /opt/coolorbit-tool/entrypoint.sh

LABEL org.coolorbit.with_alchemi="${WITH_ALCHEMI}" \
      org.coolorbit.nvalchemi="${NVALCHEMI_VERSION}" \
      org.coolorbit.claude_code="${CLAUDE_CODE_VERSION}"

WORKDIR /work
ENTRYPOINT ["/opt/coolorbit-tool/entrypoint.sh"]
