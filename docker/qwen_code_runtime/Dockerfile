# Qwen Code terminal runtime — extends the analysis runtime with Node.js + Qwen CLI.
# Build:  ./scripts/build_qwen_code_runtime_image.sh
ARG BASE_IMAGE=gagent-python-runtime:latest
FROM ${BASE_IMAGE}

USER root

# tmux for future session-persistence; procps for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Writable home for arbitrary UID passed via --user at runtime
RUN mkdir -p /tmp/gagent_home && chmod 1777 /tmp/gagent_home

USER $MAMBA_USER

# Activate micromamba so RUN commands see the conda env
ARG MAMBA_DOCKERFILE_ACTIVATE=1

# Node.js 20 (required by the qwen CLI shebang: #!/usr/bin/env node)
RUN micromamba install -y -n base -c conda-forge nodejs=20 && \
    micromamba clean --all --yes

# Qwen Code CLI — global install into conda prefix
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm install -g @qwen-code/qwen-code --registry=${NPM_REGISTRY}

# Smoke-test: all key tools are on PATH
RUN node --version && npm --version && qwen --version && python --version && git --version

WORKDIR /workspace
