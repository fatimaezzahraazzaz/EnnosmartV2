"""Un diagnostic en cours reste visible pour la page, quel que soit le processus de l'API."""

import os
import unittest
from types import SimpleNamespace

from services import diagnostic_reset_service as locks

FAKE_PROJECT = 987654  # aucun projet réel : le verrou consultatif ne touche aucune donnée
SQLITE_DB = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))


class RunStatusWithoutDatabaseTests(unittest.TestCase):
    def test_status_follows_the_process_lock(self):
        project = SimpleNamespace(id=FAKE_PROJECT)
        seen = {}

        @locks.exclusive_project_diagnostic
        def run(db, project):
            seen["during"] = locks.diagnostic_activity(db, project.id)
            return "done"

        self.assertEqual(locks.diagnostic_activity(SQLITE_DB, FAKE_PROJECT), {"running": False})
        self.assertEqual(run(SQLITE_DB, project), "done")
        self.assertTrue(seen["during"]["running"])
        self.assertEqual(locks.diagnostic_activity(SQLITE_DB, FAKE_PROJECT), {"running": False})

    def test_failed_diagnostic_releases_the_lock(self):
        @locks.exclusive_project_diagnostic
        def run(db, project):
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            run(SQLITE_DB, SimpleNamespace(id=FAKE_PROJECT))
        self.assertFalse(locks.diagnostic_activity(SQLITE_DB, FAKE_PROJECT)["running"])


@unittest.skipUnless(
    os.getenv("ENNOSMART_RUN_STATUS_PG_TEST") == "1"
    and os.getenv("DATABASE_URL", "").startswith("postgresql"),
    "nécessite la base PostgreSQL de l'API",
)
class RunStatusAcrossProcessesTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        self.engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        self.sessions = [Session(self.engine) for _ in range(3)]

    def tearDown(self):
        for session in self.sessions:
            session.close()
        self.engine.dispose()

    def test_lock_held_by_another_connection_is_reported_and_blocks_a_second_launch(self):
        holder, reader, second = self.sessions
        self.assertFalse(locks.diagnostic_activity(reader, FAKE_PROJECT)["running"])
        with locks._database_project_lock(holder, FAKE_PROJECT) as acquired:
            self.assertTrue(acquired)
            status = locks.diagnostic_activity(reader, FAKE_PROJECT)
            self.assertTrue(status["running"])
            self.assertIn("started_at", status)
            self.assertLess(status["elapsed_seconds"], 60)
            with locks._database_project_lock(second, FAKE_PROJECT) as second_acquired:
                self.assertFalse(second_acquired)
        self.assertFalse(locks.diagnostic_activity(reader, FAKE_PROJECT)["running"])


if __name__ == "__main__":
    unittest.main()
