# Experimental Codex Agent and Egress Proxy Images

These Dockerfiles are starting points for the experimental `--container-agent`
profile. They are not published or independently audited base images. Supply
platform-specific immutable base-image digests; do not build from mutable tags.

## Build

From the repository root, replace the two base-image digest placeholders with
the reviewed Linux/amd64 digests from the relevant official image manifests:

```bash
docker build -f examples/containers/codex-agent.Dockerfile \
  --build-arg BASE_IMAGE='node:22-bookworm-slim@sha256:<verified-digest>' \
  --build-arg CODEX_VERSION=0.166.0 \
  -t ghcr.io/your-org/narrow-codex-agent:review .

docker build -f examples/containers/egress-proxy.Dockerfile \
  --build-arg BASE_IMAGE='python:3.12-slim-bookworm@sha256:<verified-digest>' \
  -t ghcr.io/your-org/narrow-egress-proxy:review .
```

Push both images to a registry you control, then resolve their immutable
registry digests. Put those `@sha256:` references into `.narrow/config.json`.
The Codex image installs an exact npm package version; update it only through
a reviewed image rebuild and a new digest. Version `0.166.0` is the npm package
version recorded by the upstream registry when this example was written.

## Runtime requirements

- Linux/POSIX host, local default Docker context, non-root host UID/GID.
- Docker Engine with `docker network connect --gw-priority` support.
- `OPENAI_API_KEY` explicitly present in the host environment. NARROW never
  reads Codex's interactive login profile for this mode.
- An allowlist containing the exact provider API DNS endpoints required by
  your Codex CLI/account configuration.
- A Codex CLI build and Python runtime compatible with the `narrow` source
  snapshot embedded in both images.

The proxy image must start `python -m narrow.egress_proxy` successfully. The
agent image must start `python -m narrow.agent_entrypoint` and expose `codex`
on PATH. NARROW starts both images with the invoking UID/GID.

Before using this profile on valuable code, test denied destinations,
allowlisted provider connectivity, route absence in the agent, uplink routing
in the proxy, process cleanup, daemon interruption, and host-gateway access.
A Docker bridge and daemon are still trusted infrastructure; this is not a
microVM and does not protect against the host or Docker administrator.
