"""Un diagnostic en cours reste visible et bloque un second lancement, quel que soit le processus."""

import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from services import diagnostic_reset_service as locks


class DiagnosticRunStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_dir = locks._LOCK_DIR
        locks._LOCK_DIR = Path(self.tmp.name)
        self.project = SimpleNamespace(id=987654)

    def tearDown(self):
        locks._LOCK_DIR = self.previous_dir
        self.tmp.cleanup()

    def test_status_is_running_only_while_the_diagnostic_runs(self):
        seen = {}

        @locks.exclusive_project_diagnostic
        def run(db, project):
            seen["during"] = locks.diagnostic_activity(project.id)
            return "done"

        self.assertFalse(locks.diagnostic_activity(self.project.id)["running"])
        self.assertEqual(run(None, self.project), "done")
        self.assertTrue(seen["during"]["running"])
        if locks.fcntl is not None:
            self.assertEqual(seen["during"]["operation"], "run")
            self.assertIn("started_at", seen["during"])
        self.assertFalse(locks.diagnostic_activity(self.project.id)["running"])

    def test_failed_diagnostic_releases_the_lock(self):
        @locks.exclusive_project_diagnostic
        def run(db, project):
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            run(None, self.project)
        self.assertFalse(locks.diagnostic_activity(self.project.id)["running"])

    @unittest.skipIf(locks.fcntl is None, "verrou entre processus disponible seulement sous Linux")
    def test_second_launch_from_another_process_is_refused(self):
        holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            from pathlib import Path
            from types import SimpleNamespace
            from services import diagnostic_reset_service as locks
            locks._LOCK_DIR = Path({self.tmp.name!r})

            @locks.exclusive_project_diagnostic
            def run(db, project):
                print("HOLDING", flush=True)
                time.sleep(30)

            run(None, SimpleNamespace(id={self.project.id}))
        """)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "HOLDING")
            status = locks.diagnostic_activity(self.project.id)
            self.assertTrue(status["running"])
            self.assertEqual(status["operation"], "run")

            @locks.exclusive_project_diagnostic
            def second(db, project):
                self.fail("un second diagnostic a démarré")

            with self.assertRaisesRegex(RuntimeError, "déjà en cours"):
                second(None, self.project)
        finally:
            holder.kill()
            holder.wait()
        # Le système libère le verrou d'un processus arrêté net.
        deadline = time.monotonic() + 5
        while locks.diagnostic_activity(self.project.id)["running"] and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(locks.diagnostic_activity(self.project.id)["running"])


if __name__ == "__main__":
    unittest.main()
