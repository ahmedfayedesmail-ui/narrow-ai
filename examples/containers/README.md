# Experimental Claude/Codex Agent and Egress Proxy Images

These Dockerfiles are starting points for the experimental `--container-agent`
profile for the native Claude and Codex adapters. They are not published or independently audited base images. Supply
platform-specific immutable base-image digests; do not build from mutable tags.

## Build

From the repository root, replace the two base-image digest placeholders with
the reviewed Linux/amd64 digests from the relevant official image manifests:

```bash
docker build -f examples/containers/codex-agent.Dockerfile \
  --build-arg BASE_IMAGE='node:22-bookworm-slim@sha256:<verified-digest>' \
  --build-arg CODEX_VERSION=0.166.0 \
  --build-arg CLAUDE_CODE_VERSION=2.1.311 \
  -t ghcr.io/your-org/narrow-agent:review .

docker build -f examples/containers/egress-proxy.Dockerfile \
  --build-arg BASE_IMAGE='python:3.12-slim-bookworm@sha256:<verified-digest>' \
  -t ghcr.io/your-org/narrow-egress-proxy:review .
```

Push both images to a registry you control, then resolve their immutable
registry digests. Put those `@sha256:` references into `.narrow/config.json`.
The shared agent image installs exact versions of the Codex and Claude Code CLIs; update either only through a reviewed image rebuild and a new digest. Claude Code version `2.1.311` is pinned to the npm stable tag at the time this image profile was prepared.

## Runtime requirements

- Linux/POSIX host, local default Docker context, non-root host UID/GID.
- Docker Engine with `docker network connect --gw-priority` support.
- Exactly one explicit provider credential: `OPENAI_API_KEY` for Codex or
  `ANTHROPIC_API_KEY` for Claude. NARROW never reads either provider's
  interactive login profile for this mode.
- An allowlist containing the exact provider API DNS endpoints required by
  the selected provider/account configuration.
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

The shared agent image contains both provider CLIs. The image build pins each CLI version independently; CI runs both `--version` commands as an image smoke test. Do not use mutable `latest` tags for either CLI or for the OCI image reference.

The same `agent_container.image` can be used for either native provider. For Claude, configure the egress allowlist to include `api.anthropic.com` where required by your account. For Codex, configure the required OpenAI API hostname. The selected API key is readable by the running provider process; the host provider home/login profile is never mounted.