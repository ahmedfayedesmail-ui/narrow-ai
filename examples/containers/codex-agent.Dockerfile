# Build from the repository root:
# docker build -f examples/containers/codex-agent.Dockerfile \
#   --build-arg BASE_IMAGE='node:22-bookworm-slim@sha256:<verified-digest>' \
#   --build-arg CODEX_VERSION=0.166.0 \
#   -t ghcr.io/your-org/narrow-codex-agent:review .

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG CODEX_VERSION=0.166.0
RUN set -eu; \
    test -n "${CODEX_VERSION}"; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3 ca-certificates; \
    ln -sf /usr/bin/python3 /usr/local/bin/python; \
    npm install --global --omit=dev "@openai/codex@${CODEX_VERSION}"; \
    npm cache clean --force; \
    rm -rf /var/lib/apt/lists/*

COPY src /opt/narrow/src
ENV PYTHONPATH=/opt/narrow/src
ENV HOME=/home/narrow
WORKDIR /workspace

# No USER is hard-coded: NARROW starts the container with the invoking
# non-root POSIX UID/GID so bind-mounted task state stays private.
