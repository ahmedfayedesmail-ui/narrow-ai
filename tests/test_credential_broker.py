from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from narrow.core import NarrowError, Store
from narrow.credential_broker import issue_credential_lease, revoke_credential_lease

TASK = "task_credential_1"
EXECUTION = "12345678-1234-4234-8234-123456789abc"


class CredentialBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        os.system(f'git -C "{self.root}" init -q')
        os.system(f'git -C "{self.root}" config user.email "test@example.invalid"')
        os.system(f'git -C "{self.root}" config user.name "NARROW tests"')
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
        if os.name != "nt":
            self.assertEqual(lease.path.stat().st_mode & 0o777, 0o400)
            self.assertEqual(lease.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn(self.environment["ANTHROPIC_API_KEY"], repr(lease))
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

    def test_revoke_refuses_path_with_wrong_execution_identity(self):
        lease = issue_credential_lease(
            self.store, "claude", TASK, EXECUTION, environment=self.environment
        )
        from dataclasses import replace
        with self.assertRaisesRegex(NarrowError, "does not match"):
            revoke_credential_lease(replace(lease, execution_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
        revoke_credential_lease(lease)


if __name__ == "__main__":
    unittest.main()
