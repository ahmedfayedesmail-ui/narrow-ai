# External provider adapter plugins

NARROW has built-in adapters for Claude Code and OpenAI Codex CLI. Third-party
Python packages can register additional adapters using the narrow.adapters
entry-point group.

This is an extension point, not built-in LangChain, CrewAI, or AutoGen support.
A framework integration package must implement and test process launch,
session continuity, approval semantics, and event parsing for its supported
framework version.

## Adapter interface

A plugin exports a class derived from narrow.providers.ProviderAdapter and
implements build_command and parse_event. The command must use argv elements,
not a shell command string. It should run only inside the workspace passed by
NARROW, preserve provider approvals, and return sanitized metadata events.

Example registration in the plugin package's pyproject.toml:

[project.entry-points."narrow.adapters"]
my-agent = "my_agent_narrow:MyAgentAdapter"

Install the plugin into the same Python environment as NARROW. Its registered
adapter name can then be used with narrow providers and narrow drive --provider
my-agent.

NARROW imports installed plugins when provider discovery is used. Importing a
plugin executes its Python code in the user's environment, so install only
plugins you trust. Plugins must not store credentials, prompts, raw tool
arguments, or complete model responses in NARROW's event journal.

The existing configured generic command adapter remains available through
.narrow/config.json and narrow drive --agent NAME. It does not provide
structured provider events or native sandbox integration.
