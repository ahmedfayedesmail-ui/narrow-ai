from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest import skipUnless

from narrow.windows_job import assign_process_to_job


class WindowsJobTests(unittest.TestCase):
    @skipUnless(os.name == "nt", "Windows Job Objects are available only on Windows.")
    def test_closing_job_terminates_assigned_process(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        job = None
        try:
            job = assign_process_to_job(child)
            self.assertIsNotNone(job)
            job.close()
            return_code = child.wait(timeout=5)
            self.assertNotEqual(return_code, 0, "Closing the Job Object should terminate the child.")
        finally:
            if job is not None:
                job.close()
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    @skipUnless(os.name != "nt", "Non-Windows compatibility path.")
    def test_non_windows_does_not_create_job_object(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        self.assertIsNone(assign_process_to_job(child))
        child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
