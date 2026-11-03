"""Minimal in-container entrypoint for consuming a mounted provider credential.

The host launcher mounts one task-scoped credential file read-only. This
entrypoint reads that one file, injects only the selected provider key into a
sanitized child environment, and replaces itself with the pinned provider CLI.
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path
from typing import Sequence

from .core import NarrowError
from .egress_proxy import ProxyPolicyError, read_token_file

_PROVIDER = {
    "claude": ("claude", "ANTHROPIC_API_KEY"),
    "codex": ("codex", "OPENAI_API_KEY"),
}
_MAX_CREDENTIAL_BYTES = 16 * 1024
_ALLOWED_ENV = {
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "XDG_CONFIG_HOME",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "NARROW_POLICY_MANIFEST", "NARROW_TASK_ID", "NARROW_EXECUTION_ID",
    "NARROW_CONTAINER_EVENT_SPOOL",
}


def read_mounted_credential(path: Path) -> str:
    """Read a single regular, unlinked, bounded, owner-only mounted secret."""
    source = Path(path)
    try:
        metadata = source.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_CREDENTIAL_BYTES
        ):
            raise NarrowError("Mounted provider credential is not a bounded regular file.")
        if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o400:
            raise NarrowError("Mounted provider credential must be owner-read-only (mode 0400).")
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_size != metadata.st_size
            ):
                raise NarrowError("Mounted provider credential changed during open.")
            payload = bytearray()
            while len(payload) <= _MAX_CREDENTIAL_BYTES:
                chunk = os.read(descriptor, min(4096, _MAX_CREDENTIAL_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) != opened.st_size:
                raise NarrowError("Mounted provider credential changed or exceeded its read limit.")
        finally:
            os.close(descriptor)
    except NarrowError:
        raise
    except OSError as exc:
        raise NarrowError("Could not safely read mounted provider credential.") from exc
    try:
        value = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NarrowError("Mounted provider credential is not valid UTF-8.") from exc
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise NarrowError("Mounted provider credential must be a non-empty single-line value.")
    return value


def build_provider_environment(
    provider: str,
    credential: str,
    inherited: dict[str, str] | None = None,
    *,
    proxy_token: str,
) -> dict[str, str]:
    """Keep only runtime allowlisted environment variables and one provider key."""
    if provider not in _PROVIDER:
        raise NarrowError("Container entrypoint supports only claude and codex.")
    if not isinstance(credential, str) or not credential or any(char in credential for char in "\x00\r\n"):
        raise NarrowError("Provider credential is invalid.")
    source = os.environ if inherited is None else inherited
    env = {
        key: value for key, value in source.items()
        if key in _ALLOWED_ENV and isinstance(value, str) and "\x00" not in value
    }
    # Do not forward either provider key from the parent environment.
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    if not isinstance(proxy_token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", proxy_token):
        raise NarrowError("Mounted egress proxy capability token is invalid.")
    proxy_url = f"http://narrow:{proxy_token}@narrow-egress-proxy:3128"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env[key] = proxy_url
    env["NO_PROXY"] = "localhost,127.0.0.1"
    env["no_proxy"] = "localhost,127.0.0.1"
    env.pop("ALL_PROXY", None)
    env.pop("all_proxy", None)
    env[_PROVIDER[provider][1]] = credential
    return env


def _route_table_has_default_route(ipv4_routes: str, ipv6_routes: str) -> bool:
    """Return True if either Linux routing table contains a default route."""
    for line in ipv4_routes.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 4 and fields[1] == "00000000":
            try:
                flags = int(fields[3], 16)
            except ValueError:
                raise NarrowError("Container IPv4 route table contains malformed route flags.")
            if flags & 0x1:
                return True
    for line in ipv6_routes.splitlines():
        fields = line.split()
        if len(fields) >= 10 and fields[0] == "0" * 32 and fields[1] == "00":
            try:
                flags = int(fields[8], 16)
            except ValueError:
                raise NarrowError("Container IPv6 route table contains malformed route flags.")
            if flags & 0x1:
                return True
    return False


def assert_no_default_route() -> None:
    """Fail closed unless the agent container has no IPv4 or IPv6 default route."""
    try:
        ipv4_routes = Path("/proc/net/route").read_text(encoding="ascii")
        ipv6_routes = Path("/proc/net/ipv6_route").read_text(encoding="ascii")
    except OSError as exc:
        raise NarrowError("Could not verify that the agent container has no default network route.") from exc
    if _route_table_has_default_route(ipv4_routes, ipv6_routes):
        raise NarrowError("Agent container has a default IPv4/IPv6 route; refusing to start outside the proxy-only network profile.")


def build_exec_argv(provider: str, command: Sequence[str]) -> list[str]:
    """Allow only the selected provider CLI as the child executable."""
    if provider not in _PROVIDER:
        raise NarrowError("Container entrypoint supports only claude and codex.")
    if not isinstance(command, (list, tuple)) or not command:
        raise NarrowError("A provider CLI command is required after --.")
    argv = list(command)
    if any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
        raise NarrowError("Provider CLI arguments must be non-empty strings without NUL bytes.")
    executable = argv[0]
    if executable != _PROVIDER[provider][0]:
        raise NarrowError("Provider entrypoint requires the exact selected CLI name; arbitrary executable paths are refused.")
    return argv


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NARROW provider credential entrypoint")
    parser.add_argument("--provider", required=True, choices=tuple(_PROVIDER))
    parser.add_argument("--credential-file", required=True, type=Path)
    parser.add_argument("--proxy-token-file", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="provider CLI argv after --")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    try:
        assert_no_default_route()
        exec_argv = build_exec_argv(args.provider, command)
        credential = read_mounted_credential(args.credential_file)
        proxy_token = read_token_file(args.proxy_token_file)
        child_env = build_provider_environment(args.provider, credential, proxy_token=proxy_token)
    except (NarrowError, ProxyPolicyError) as exc:
        print(f"NARROW agent entrypoint: {exc}", file=sys.stderr)
        return 2
    try:
        os.execvpe(exec_argv[0], exec_argv, child_env)
    except OSError as exc:
        print(f"NARROW agent entrypoint: provider CLI could not start ({type(exc).__name__}).", file=sys.stderr)
        return 127
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
