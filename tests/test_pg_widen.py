"""Postgres only (set CHRONOS_PG_TEST_URL to a throwaway database): the replay millisecond columns
created as INTEGER by the first release are widened to BIGINT in place, keeping existing rows."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "web"))
URL = os.environ.get("CHRONOS_PG_TEST_URL")


@unittest.skipUnless(URL, "set CHRONOS_PG_TEST_URL to run")
class WidenMs(unittest.TestCase):
    def test_widen_keeps_rows_and_is_idempotent(self):
        from sqlalchemy import create_engine, text
        import practice
        e = create_engine(URL, future=True)
        with e.begin() as c:
            c.execute(text("DROP TABLE IF EXISTS practice_trades"))
            c.execute(text("CREATE TABLE practice_trades (id serial primary key, placed_ms integer not null, fill_ms integer, exit_ms integer)"))
            c.execute(text("INSERT INTO practice_trades (placed_ms) VALUES (12345)"))
        practice.widen_ms_columns(e)
        practice.widen_ms_columns(e)
        with e.begin() as c:
            types = dict(c.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'practice_trades'")).all())
            self.assertEqual({types[k] for k in ("placed_ms", "fill_ms", "exit_ms")}, {"bigint"})
            c.execute(text("INSERT INTO practice_trades (placed_ms) VALUES (1786971600000)"))
            self.assertEqual(c.execute(text("SELECT placed_ms FROM practice_trades ORDER BY id")).scalars().all(), [12345, 1786971600000])
            c.execute(text("DROP TABLE practice_trades"))


if __name__ == "__main__":
    unittest.main()
