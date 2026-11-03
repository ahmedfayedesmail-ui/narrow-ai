from __future__ import annotations

import io
import os
import socket
import tempfile
import unittest
from pathlib import Path

from narrow.egress_proxy import (
    EgressProxy,
    ProxyConfig,
    _ConnectHandler,
    _extract_client_hello_sni,
    _read_tls_client_hello,
    ProxyPolicyError,
    check_proxy_token,
    check_proxy_authorization,
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


    @staticmethod
    def make_client_hello(
        hostname: str,
        split_after: int | None = None,
        extra_extensions: bytes = b"",
    ) -> bytes:
        host = hostname.encode("ascii")
        server_name = b"\x00" + len(host).to_bytes(2, "big") + host
        sni_list = len(server_name).to_bytes(2, "big") + server_name
        sni_extension = b"\x00\x00" + len(sni_list).to_bytes(2, "big") + sni_list
        extensions = sni_extension + extra_extensions
        body = (
            b"\x03\x03" + b"\x00" * 32
            + b"\x00"
            + b"\x00\x02\x13\x01"
            + b"\x01\x00"
            + len(extensions).to_bytes(2, "big") + extensions
        )
        handshake = b"\x01" + len(body).to_bytes(3, "big") + body
        chunks = [handshake] if split_after is None else [
            handshake[:split_after], handshake[split_after:]
        ]
        return b"".join(
            b"\x16\x03\x01" + len(chunk).to_bytes(2, "big") + chunk
            for chunk in chunks
        )

    def test_tls_client_hello_sni_is_extracted(self):
        record = self.make_client_hello("api.anthropic.com")
        # One TLS record header precedes the ClientHello handshake message.
        record_length = int.from_bytes(record[3:5], "big")
        message = record[5:5 + record_length]
        self.assertEqual(_extract_client_hello_sni(message), "api.anthropic.com")

    def test_tls_client_hello_rejects_trailing_handshake_bytes(self):
        record = self.make_client_hello("api.anthropic.com")
        record_length = int.from_bytes(record[3:5], "big")
        message = record[5:5 + record_length] + b"\x01\x00\x00\x00"
        with self.assertRaisesRegex(ProxyPolicyError, "length is inconsistent"):
            _extract_client_hello_sni(message)

    def test_tls_client_hello_sni_must_match_connect_authority(self):
        left, right = socket.socketpair()
        try:
            payload = self.make_client_hello("evil.example.com", split_after=7)
            left.sendall(payload)
            with self.assertRaisesRegex(ProxyPolicyError, "SNI does not match"):
                _read_tls_client_hello(
                    right, expected_hostname="api.anthropic.com", timeout=2.0
                )
        finally:
            left.close()
            right.close()

    def test_tls_client_hello_rejects_ech_even_with_allowlisted_outer_sni(self):
        # ECH can hide the inner SNI while presenting a public outer name.
        # The proxy must not treat that outer name as proof of the final route.
        record = self.make_client_hello(
            "api.anthropic.com",
            extra_extensions=b"\xfe\x0d\x00\x00",
        )
        record_length = int.from_bytes(record[3:5], "big")
        message = record[5:5 + record_length]
        with self.assertRaisesRegex(ProxyPolicyError, "Encrypted ClientHello"):
            _extract_client_hello_sni(message)

    def test_tls_client_hello_supports_fragmented_handshake_records(self):
        left, right = socket.socketpair()
        payload = self.make_client_hello("api.anthropic.com", split_after=7)
        try:
            left.sendall(payload)
            captured = _read_tls_client_hello(
                right, expected_hostname="api.anthropic.com", timeout=2.0
            )
            self.assertEqual(captured, payload)
        finally:
            left.close()
            right.close()


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

    def test_proxy_accepts_bearer_and_standard_basic_capability_auth(self):
        token = "a" * 48
        self.assertTrue(check_proxy_authorization(f"Bearer {token}", token))
        import base64
        basic = base64.b64encode(f"narrow:{token}".encode("utf-8")).decode("ascii")
        self.assertTrue(check_proxy_authorization(f"Basic {basic}", token))
        wrong_user = base64.b64encode(f"root:{token}".encode("utf-8")).decode("ascii")
        self.assertFalse(check_proxy_authorization(f"Basic {wrong_user}", token))
        self.assertFalse(check_proxy_authorization("Basic !!!", token))

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
