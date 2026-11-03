from __future__ import annotations

import io
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from narrow.egress_proxy import (
    EgressProxy,
    ProxyConfig,
    _ConnectHandler,
    ProxyPolicyError,
    check_proxy_token,
    normalize_allowlist,
    normalize_hostname,
    parse_connect_authority,
    read_token_file,
    resolve_connect_target,
    validate_bind_host,
)


class EgressProxyPolicyTests(unittest.TestCase):
    def test_exact_hostname_allowlist_only(self):
        self.assertEqual(
            normalize_allowlist(["API.ANTHROPIC.COM"]),
            frozenset({"api.anthropic.com"}),
        )
        with self.assertRaises(ProxyPolicyError):
            resolve_connect_target(
                "evil.api.anthropic.com:443",
                ["api.anthropic.com"],
                resolver=lambda *args, **kwargs: [],
            )

    def test_rejects_wildcards_ip_literals_and_noncanonical_hosts(self):
        for host in ("*.anthropic.com", "127.0.0.1", "api.anthropic.com.", "api/anthropic.com"):
            with self.subTest(host=host), self.assertRaises(ProxyPolicyError):
                normalize_hostname(host)

    def test_only_tls_443_is_accepted(self):
        self.assertEqual(parse_connect_authority("api.anthropic.com:443"), ("api.anthropic.com", 443))
        for authority in ("api.anthropic.com:80", "api.anthropic.com:8443", "127.0.0.1:443", "[::1]:443", "api.anthropic.com"):
            with self.subTest(authority=authority), self.assertRaises(ProxyPolicyError):
                parse_connect_authority(authority)

    def test_dns_answers_are_pinned_and_all_must_be_public(self):
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ]
        target = resolve_connect_target(
            "api.anthropic.com:443",
            ["api.anthropic.com"],
            resolver=lambda *args, **kwargs: records,
        )
        self.assertEqual(target.addresses[0][1][0], "93.184.216.34")

        mixed = records + [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with self.assertRaisesRegex(ProxyPolicyError, "non-public"):
            resolve_connect_target(
                "api.anthropic.com:443",
                ["api.anthropic.com"],
                resolver=lambda *args, **kwargs: mixed,
            )

    def test_connect_request_accepts_standard_header_spacing(self):
        handler = object.__new__(_ConnectHandler)
        handler.rfile = io.BytesIO(
            b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n"
            b"Host: api.anthropic.com:443\r\n"
            b"Proxy-Authorization: Bearer " + b"x" * 48 + b"\r\n"
            b"\r\n"
        )
        request = handler._read_request()
        self.assertIsNotNone(request)
        authority, headers = request
        self.assertEqual(authority, "api.anthropic.com:443")
        self.assertEqual(headers["host"], "api.anthropic.com:443")

    def test_connect_request_rejects_duplicate_auth_headers(self):
        handler = object.__new__(_ConnectHandler)
        handler.rfile = io.BytesIO(
            b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n"
            b"Host: api.anthropic.com:443\r\n"
            b"Proxy-Authorization: Bearer " + b"x" * 48 + b"\r\n"
            b"Proxy-Authorization: Bearer " + b"x" * 48 + b"\r\n"
            b"\r\n"
        )
        self.assertIsNone(handler._read_request())

    def test_proxy_rejects_nonfinite_timeouts(self):
        config = ProxyConfig(
            bind_host="127.0.0.1",
            port=3128,
            allowed_hosts=frozenset({"api.anthropic.com"}),
            token="x" * 48,
            idle_timeout=float("nan"),
        )
        with self.assertRaises(ProxyPolicyError):
            EgressProxy(config)

    def test_dns_failure_and_empty_results_fail_closed(self):
        with self.assertRaises(ProxyPolicyError):
            resolve_connect_target(
                "api.anthropic.com:443",
                ["api.anthropic.com"],
                resolver=lambda *args, **kwargs: [],
            )
        def fail(*args, **kwargs):
            raise OSError("DNS unavailable")
        with self.assertRaisesRegex(ProxyPolicyError, "resolution failed"):
            resolve_connect_target("api.anthropic.com:443", ["api.anthropic.com"], resolver=fail)

    def test_bind_address_must_be_private_and_explicit(self):
        self.assertEqual(validate_bind_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(validate_bind_host("172.20.0.1"), "172.20.0.1")
        for host in ("0.0.0.0", "::", "8.8.8.8", "proxy.local"):
            with self.subTest(host=host), self.assertRaises(ProxyPolicyError):
                validate_bind_host(host)

    def test_proxy_authentication_uses_constant_time_comparison(self):
        self.assertTrue(check_proxy_token("a" * 48, "a" * 48))
        self.assertFalse(check_proxy_token("a" * 47, "a" * 48))
        self.assertFalse(check_proxy_token(None, "a" * 48))

    def test_token_file_requires_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "token"
            path.write_text("a" * 48 + "\n", encoding="ascii")
            if os.name != "nt":
                path.chmod(0o600)
            self.assertEqual(read_token_file(path), "a" * 48)
            if os.name != "nt":
                path.chmod(0o644)
                with self.assertRaisesRegex(ProxyPolicyError, "chmod 600"):
                    read_token_file(path)

    def test_proxy_rejects_wildcard_bind_before_opening_socket(self):
        config = ProxyConfig(
            bind_host="0.0.0.0",
            port=0,
            allowed_hosts=frozenset({"api.anthropic.com"}),
            token="x" * 48,
        )
        with self.assertRaises(ProxyPolicyError):
            EgressProxy(config)


if __name__ == "__main__":
    unittest.main()
