from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from narrow.core import NarrowError, Store
from narrow.credential_broker import (
    issue_credential_lease,
    revoke_credential_lease,
    validate_credential_lease,
)

TASK = "task_credential_1"
EXECUTION = "12345678-1234-4234-8234-123456789abc"


@unittest.skipIf(os.name == "nt", "credential file leases fail closed on Windows until restricted DACL support exists")
class CredentialBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "NARROW tests"], check=True)
        self.store = Store(self.root)
        self.environment = {"ANTHROPIC_API_KEY": "test-secret-value-123456"}

    def tearDown(self):
        self.temp.cleanup()

    def test_issues_owner_only_task_scoped_lease_without_returning_secret(self):
        lease = issue_credential_lease(
            self.store, "claude", TASK, EXECUTION, environment=self.environment
        )
        self.assertEqual(lease.environment_name, "ANTHROPIC_API_KEY")
        self.assertEqual(lease.task_id, TASK)
        self.assertEqual(lease.execution_id, EXECUTION)
        self.assertEqual(lease.path.read_text(encoding="utf-8"), self.environment["ANTHROPIC_API_KEY"])
        self.assertEqual(lease.path.stat().st_nlink, 1)
        self.assertEqual(lease.path.stat().st_mode & 0o777, 0o400)
        self.assertEqual(lease.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn(self.environment["ANTHROPIC_API_KEY"], repr(lease))
        self.assertEqual(validate_credential_lease(lease), lease.path)
        revoke_credential_lease(lease)
        self.assertFalse(lease.path.exists())

    def test_credential_missing_fails_closed(self):
        with self.assertRaisesRegex(NarrowError, "No explicit claude API key"):
            issue_credential_lease(self.store, "claude", TASK, EXECUTION, environment={})

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(NarrowError, "only the native"):
            issue_credential_lease(self.store, "generic", TASK, EXECUTION, environment={})

    def test_ttl_is_bounded(self):
        for value in (0, -1, 3601, True):
            with self.subTest(value=value), self.assertRaises(NarrowError):
                issue_credential_lease(
                    self.store, "claude", TASK, EXECUTION,
                    ttl_seconds=value, environment=self.environment,
                )

    def test_multiline_and_oversized_credentials_are_rejected(self):
        for value in ("line1\nline2", "x" * (16 * 1024 + 1)):
            with self.subTest(length=len(value)), self.assertRaises(NarrowError):
                issue_credential_lease(
                    self.store, "claude", TASK, EXECUTION,
                    environment={"ANTHROPIC_API_KEY": value},
                )

    def test_expired_lease_is_revoked(self):
        lease = issue_credential_lease(
            self.store, "claude", TASK, EXECUTION, environment=self.environment
        )
        with self.assertRaisesRegex(NarrowError, "expired and was revoked"):
            validate_credential_lease(lease, now=lease.expires_at + 1)
        self.assertFalse(lease.path.exists())

    def test_tampered_lease_is_rejected(self):
        lease = issue_credential_lease(
            self.store, "claude", TASK, EXECUTION, environment=self.environment
        )
        lease.path.chmod(0o600)
        lease.path.write_text("tampered", encoding="utf-8")
        lease.path.chmod(0o400)
        with self.assertRaisesRegex(NarrowError, "content does not match"):
            validate_credential_lease(lease)
        revoke_credential_lease(lease)

    def test_revoke_refuses_path_with_wrong_execution_identity(self):
        lease = issue_credential_lease(
            self.store, "claude", TASK, EXECUTION, environment=self.environment
        )
        with self.assertRaisesRegex(NarrowError, "does not match"):
            revoke_credential_lease(replace(
                lease, execution_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            ))
        revoke_credential_lease(lease)

    def test_revoke_refuses_a_path_outside_the_narrow_state_root(self):
        lease = issue_credential_lease(
            self.store, "claude", TASK, EXECUTION, environment=self.environment
        )
        unrelated = self.root / "unrelated.secret"
        unrelated.write_text("do-not-delete", encoding="utf-8")
        with self.assertRaisesRegex(NarrowError, "path does not match"):
            revoke_credential_lease(replace(lease, path=unrelated))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "do-not-delete")
        revoke_credential_lease(lease)

    def test_revoke_is_idempotent(self):
        lease = issue_credential_lease(
            self.store, "claude", TASK, EXECUTION, environment=self.environment
        )
        revoke_credential_lease(lease)
        revoke_credential_lease(lease)
        self.assertFalse(lease.path.exists())


if __name__ == "__main__":
    unittest.main()
