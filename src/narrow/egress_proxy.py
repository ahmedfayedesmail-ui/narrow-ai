"""Small, fail-closed HTTP CONNECT egress proxy for isolated NARROW agents.

This is a transport boundary primitive, not a complete agent runtime. The
container launcher must ensure agent containers have no route around this
proxy and must isolate each execution on its own network.
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import math
import os
import re
import select
import shlex
import socket
import socketserver
import stat
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

_MAX_HEADER_BYTES = 16 * 1024
_MAX_LINE_BYTES = 8 * 1024
_DEFAULT_PORT = 3128
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class ProxyPolicyError(ValueError):
    """A request or proxy configuration violates the egress policy."""


@dataclass(frozen=True)
class ConnectTarget:
    hostname: str
    port: int
    addresses: tuple[tuple[int, tuple], ...]


@dataclass(frozen=True)
class ProxyConfig:
    bind_host: str
    port: int
    allowed_hosts: frozenset[str]
    token: str
    connect_timeout: float = 8.0
    idle_timeout: float = 90.0
    tunnel_timeout: float = 900.0
    max_connections: int = 32


def normalize_hostname(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProxyPolicyError("Invalid hostname.")
    if any(char in value for char in "/\\@%?#") or value.endswith("."):
        raise ProxyPolicyError("Hostname is not in canonical DNS form.")
    try:
        normalized = value.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ProxyPolicyError("Hostname is not valid IDNA.") from exc
    if len(normalized) > 253:
        raise ProxyPolicyError("Hostname exceeds the DNS length limit.")
    labels = normalized.split(".")
    if len(labels) < 2 or any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise ProxyPolicyError("Hostname must be a fully-qualified DNS name.")
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        return normalized
    raise ProxyPolicyError("IP-literal destinations are not allowed.")


def normalize_allowlist(hosts: Iterable[str]) -> frozenset[str]:
    result = frozenset(normalize_hostname(host) for host in hosts)
    if not result:
        raise ProxyPolicyError("At least one exact provider hostname is required.")
    return result


def parse_connect_authority(authority: str) -> tuple[str, int]:
    """Parse host:port for CONNECT; only canonical DNS names on TCP/443 pass."""
    if not isinstance(authority, str) or authority.count(":") != 1:
        raise ProxyPolicyError("CONNECT authority must be hostname:443.")
    hostname_raw, port_raw = authority.rsplit(":", 1)
    if not port_raw.isascii() or not port_raw.isdecimal():
        raise ProxyPolicyError("CONNECT port is invalid.")
    port = int(port_raw)
    if port != 443:
        raise ProxyPolicyError("Only TLS port 443 is allowed.")
    return normalize_hostname(hostname_raw), port


def resolve_connect_target(
    authority: str,
    allowed_hosts: Iterable[str],
    *,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
) -> ConnectTarget:
    hostname, port = parse_connect_authority(authority)
    allowlist = normalize_allowlist(allowed_hosts)
    if hostname not in allowlist:
        raise ProxyPolicyError("Destination hostname is not on the exact provider allowlist.")

    try:
        records = resolver(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ProxyPolicyError("Provider hostname resolution failed.") from exc
    if not records:
        raise ProxyPolicyError("Provider hostname has no usable address records.")

    addresses: list[tuple[int, tuple]] = []
    seen: set[tuple[int, str]] = set()
    for family, socktype, proto, _canonname, sockaddr in records:
        if family not in (socket.AF_INET, socket.AF_INET6) or socktype not in (0, socket.SOCK_STREAM):
            continue
        try:
            resolved_ip = ipaddress.ip_address(sockaddr[0])
        except (ValueError, TypeError, IndexError) as exc:
            raise ProxyPolicyError("DNS returned a malformed address.") from exc
        # Reject the entire answer set if any record is non-public. This avoids
        # selecting a public record while retaining a rebinding/SSRF alternative.
        if not resolved_ip.is_global or resolved_ip.is_multicast or resolved_ip.is_unspecified:
            raise ProxyPolicyError("Provider DNS resolved to a non-public address.")
        key = (family, str(resolved_ip))
        if key in seen:
            continue
        seen.add(key)
        addresses.append((family, sockaddr))

    if not addresses:
        raise ProxyPolicyError("Provider hostname has no public IPv4/IPv6 address.")
    return ConnectTarget(hostname, port, tuple(addresses))


def validate_bind_host(bind_host: str) -> str:
    """Require an explicitly selected private/loopback interface, never wildcard."""
    try:
        address = ipaddress.ip_address(bind_host)
    except ValueError as exc:
        raise ProxyPolicyError("Proxy bind address must be a literal private IP.") from exc
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        raise ProxyPolicyError("Proxy must not bind to a wildcard, multicast, or reserved address.")
    if not (address.is_loopback or address.is_private or address.is_link_local):
        raise ProxyPolicyError("Proxy must bind only to a private or loopback interface.")
    return str(address)


def check_proxy_token(supplied: str | None, expected: str) -> bool:
    if not supplied or len(supplied) > 512:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def read_token_file(path: Path) -> str:
    source = Path(path)
    try:
        metadata = source.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ProxyPolicyError("Proxy token file must be a regular, unlinked file.")
        if metadata.st_size > 4096:
            raise ProxyPolicyError("Proxy token file is too large.")
        if os.name != "nt" and metadata.st_mode & 0o077:
            raise ProxyPolicyError("Proxy token file must be accessible only by its owner (chmod 600).")
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size > 4096:
                raise ProxyPolicyError("Proxy token file changed or is not a bounded regular file.")
            raw = os.read(descriptor, 4097)
        finally:
            os.close(descriptor)
    except ProxyPolicyError:
        raise
    except OSError as exc:
        raise ProxyPolicyError("Could not safely read proxy token file.") from exc
    try:
        token = raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ProxyPolicyError("Proxy token must contain ASCII text.") from exc
    if len(token) < 32 or len(token) > 512 or any(char.isspace() for char in token):
        raise ProxyPolicyError("Proxy token must be 32–512 non-whitespace ASCII characters.")
    return token


class _BoundedThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = True

    def __init__(self, address, handler, config: ProxyConfig):
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        self.config = config
        self._slots = threading.BoundedSemaphore(config.max_connections)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class _ConnectHandler(socketserver.StreamRequestHandler):
    server: _BoundedThreadingTCPServer

    def _reply(self, status: int, reason: str) -> None:
        message = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Connection: close\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode("ascii")
        try:
            self.wfile.write(message)
            self.wfile.flush()
        except OSError:
            pass

    def _read_request(self) -> tuple[str, dict[str, str]] | None:
        total = 0
        line = self.rfile.readline(_MAX_LINE_BYTES + 1)
        total += len(line)
        if not line or len(line) > _MAX_LINE_BYTES or not line.endswith(b"\r\n"):
            return None
        try:
            request_line = line[:-2].decode("ascii")
        except UnicodeDecodeError:
            return None
        parts = request_line.split(" ")
        if len(parts) != 3 or parts[0] != "CONNECT" or parts[2] not in {"HTTP/1.0", "HTTP/1.1"}:
            return None
        headers: dict[str, str] = {}
        while True:
            line = self.rfile.readline(_MAX_LINE_BYTES + 1)
            total += len(line)
            if not line or len(line) > _MAX_LINE_BYTES or total > _MAX_HEADER_BYTES or not line.endswith(b"\r\n"):
                return None
            if line == b"\r\n":
                break
            try:
                decoded = line[:-2].decode("latin-1")
                name, separator, value = decoded.partition(":")
                if not separator or not _HEADER_NAME.fullmatch(name):
                    return None
                key = name.lower()
                if key in headers:
                    return None
                headers[key] = value.strip()
            except (UnicodeDecodeError, ValueError):
                return None
        if "content-length" in headers or "transfer-encoding" in headers:
            return None
        return parts[1], headers

    def _tunnel(self, upstream: socket.socket) -> None:
        client = self.connection
        started = last_activity = time.monotonic()
        client.settimeout(self.server.config.idle_timeout)
        upstream.settimeout(self.server.config.idle_timeout)
        while True:
            now = time.monotonic()
            if now - started >= self.server.config.tunnel_timeout:
                return
            if now - last_activity >= self.server.config.idle_timeout:
                return
            readable, _, exceptional = select.select(
                [client, upstream], [], [client, upstream], 1.0
            )
            if exceptional:
                return
            for source in readable:
                destination = upstream if source is client else client
                try:
                    payload = source.recv(64 * 1024)
                    if not payload:
                        return
                    destination.sendall(payload)
                    last_activity = time.monotonic()
                except (OSError, TimeoutError):
                    return

    def handle(self) -> None:
        config = self.server.config
        self.connection.settimeout(10.0)
        request = self._read_request()
        if request is None:
            self._reply(400, "Bad Request")
            return
        authority, headers = request

        authorization = headers.get("proxy-authorization", "")
        scheme, separator, supplied_token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not check_proxy_token(supplied_token, config.token):
            self._reply(407, "Proxy Authentication Required")
            return
        if "host" not in headers:
            self._reply(400, "Bad Request")
            return
        try:
            request_host, request_port = parse_connect_authority(authority)
            header_host, header_port = parse_connect_authority(headers["host"])
            if (request_host, request_port) != (header_host, header_port):
                raise ProxyPolicyError("CONNECT Host header does not match the request target.")
            target = resolve_connect_target(authority, config.allowed_hosts)
        except ProxyPolicyError:
            self._reply(403, "Forbidden")
            return

        upstream: socket.socket | None = None
        for family, sockaddr in target.addresses:
            candidate = socket.socket(family, socket.SOCK_STREAM)
            candidate.settimeout(config.connect_timeout)
            try:
                # Connect to the already-vetted numeric address: do not resolve
                # the hostname a second time after the policy decision.
                candidate.connect(sockaddr)
                upstream = candidate
                break
            except OSError:
                candidate.close()
        if upstream is None:
            self._reply(502, "Bad Gateway")
            return

        try:
            self.connection.settimeout(config.idle_timeout)
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            self._tunnel(upstream)
        except OSError:
            pass
        finally:
            upstream.close()


class EgressProxy:
    """Bounded CONNECT proxy; bind only to an isolated private agent network."""

    def __init__(self, config: ProxyConfig):
        bind_host = validate_bind_host(config.bind_host)
        allowed = normalize_allowlist(config.allowed_hosts)
        if (
            not isinstance(config.port, int)
            or isinstance(config.port, bool)
            or not 1 <= config.port <= 65535
        ):
            raise ProxyPolicyError("Proxy port must be an integer between 1 and 65535.")
        if (
            not isinstance(config.max_connections, int)
            or isinstance(config.max_connections, bool)
            or config.max_connections < 1
            or config.max_connections > 256
        ):
            raise ProxyPolicyError("Proxy connection limit must be an integer between 1 and 256.")
        timeouts = (config.connect_timeout, config.idle_timeout, config.tunnel_timeout)
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
            for value in timeouts
        ):
            raise ProxyPolicyError("Proxy timeouts must be finite positive numbers.")
        if not isinstance(config.token, str) or len(config.token) < 32 or len(config.token) > 512:
            raise ProxyPolicyError("Proxy bearer token must contain 32–512 characters.")
        self.config = ProxyConfig(
            bind_host=bind_host,
            port=config.port,
            allowed_hosts=allowed,
            token=config.token,
            connect_timeout=config.connect_timeout,
            idle_timeout=config.idle_timeout,
            tunnel_timeout=config.tunnel_timeout,
            max_connections=config.max_connections,
        )
        self._server = _BoundedThreadingTCPServer(
            (bind_host, config.port), _ConnectHandler, self.config
        )

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def serve_forever(self) -> None:
        self._server.serve_forever(poll_interval=0.5)

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="NARROW fail-closed HTTPS CONNECT egress proxy."
    )
    parser.add_argument("--bind", required=True, help="Private/loopback interface IP; wildcard binds are refused.")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--allow-host", action="append", required=True, help="Exact provider DNS hostname; repeat as needed.")
    parser.add_argument("--token-file", required=True, type=Path, help="Owner-only file containing a random bearer token.")
    parser.add_argument("--max-connections", type=int, default=32)
    parser.add_argument("--idle-timeout", type=float, default=90.0)
    parser.add_argument("--tunnel-timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    try:
        token = read_token_file(args.token_file)
        proxy = EgressProxy(
            ProxyConfig(
                bind_host=args.bind,
                port=args.port,
                allowed_hosts=normalize_allowlist(args.allow_host),
                token=token,
                max_connections=args.max_connections,
                idle_timeout=args.idle_timeout,
                tunnel_timeout=args.tunnel_timeout,
            )
        )
    except (OSError, ProxyPolicyError) as exc:
        print(f"NARROW egress proxy: {exc}", file=sys.stderr)
        return 2

    print(
        f"NARROW egress proxy listening on {proxy.address[0]}:{proxy.address[1]} "
        f"for {', '.join(sorted(proxy.config.allowed_hosts))}",
        flush=True,
    )
    try:
        proxy.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        proxy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
