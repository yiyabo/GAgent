# Qwen Code terminal runtime — extends the analysis runtime with Node.js + Qwen CLI.
# Build:  ./scripts/build_qwen_code_runtime_image.sh
ARG BASE_IMAGE=gagent-python-runtime:latest
FROM ${BASE_IMAGE}

USER root

# tmux for future session-persistence; procps for healthchecks; node/npm for Qwen CLI.
# The base analysis image on this server is a regular Python runtime, not a
# micromamba runtime, so install Node through apt instead of micromamba.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    nodejs \
    npm \
    tmux \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Writable home for arbitrary UID passed via --user at runtime.
# /opt/conda/bin is kept as a compatibility path because the qwen container
# driver invokes /opt/conda/bin/qwen directly.
RUN mkdir -p /tmp/gagent_home /opt/conda/bin && chmod 1777 /tmp/gagent_home

# Qwen Code CLI — global install into /usr/local, then expose the legacy
# /opt/conda/bin/qwen path expected by DockerPTYBackend/QwenSessionDriver.
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm install -g @qwen-code/qwen-code --registry=${NPM_REGISTRY} && \
    ln -sf /usr/local/bin/qwen /opt/conda/bin/qwen && \
    ln -sf /usr/bin/node /opt/conda/bin/node

# Smoke-test: all key tools are on PATH and the compatibility qwen path works.
RUN node --version && npm --version && qwen --version && /opt/conda/bin/qwen --version && python --version && git --version

WORKDIR /workspace
