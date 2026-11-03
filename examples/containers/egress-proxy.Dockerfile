# Build from the repository root:
# docker build -f examples/containers/egress-proxy.Dockerfile \
#   --build-arg BASE_IMAGE='python:3.12-slim-bookworm@sha256:<verified-digest>' \
#   -t ghcr.io/your-org/narrow-egress-proxy:review .

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

COPY src /opt/narrow/src
ENV PYTHONPATH=/opt/narrow/src
WORKDIR /
