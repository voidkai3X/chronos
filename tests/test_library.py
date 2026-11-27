#!/usr/bin/env python3
"""Offline tests for the strategy library: CRUD, sign-in + CSRF on every endpoint, strict ownership,
per-user limits, run snapshots (no candles or trades stored), account deletion, and additive migrations.

SQLite runs always. The Postgres migration test runs when CHRONOS_PG_TEST_URL points at a scratch database
(for example a throwaway local cluster); it creates and drops only its own tables.
Run: python3 tests/test_library.py
"""
import importlib.util, json, os, sqlite3, sys, tempfile, unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix="chronos-lib-")
os.environ.pop("DATABASE_URL", None)
os.environ["SQLITE_PATH"] = os.path.join(TMP, "lib.db")
os.environ["SECRET_KEY"] = "test-secret-key-not-used-in-production"
os.environ["COOKIE_INSECURE"] = "1"
os.environ.pop("REQUIRE_LOGIN", None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("chronos_web_lib", os.path.join(ROOT, "web", "app.py"))
web = importlib.util.module_from_spec(spec)
sys.modules["chronos_web_lib"] = web
spec.loader.exec_module(web)
auth, library = web.auth, web.library
from flask import jsonify

PW = "correct horse battery"
SPEC = dict(name="London sweep long", description="My morning idea", setup_type="liquidity_sweep",
            rule_params={"lookback": 24, "stop_atr": 0.3}, pattern_label="ICT liquidity sweep", side="long",
            markets=["binance|BTCUSDT", "binance|ETHUSDT"], tf="1h", htf_confirm="4h", trend_filter=False,
            entry_offset_pct=0, stop_pct=0.8, rr=2, expiry="72h", max_hold="3d", days=90, slippage=1,
            slippage_unit="bps", entry_conditions="Sweep of the Asia low, close back above",
            exit_conditions="TP at 2R, SL below the sweep wick, out after 3 days")

FAKE_OUT = dict(
    generated="2026-11-27T18:00:00Z", market=dict(market_id="binance|BTCUSDT"), setup=dict(side="long", rr=2.0),
    result=dict(closed=40, t_stat=1.4, verdict="possible edge, not proven (Edge Score 52/100: ...)"),
    stats=dict(setups_identified=120, net=dict(win_rate=0.4, expectancy_r=0.12, profit_factor=1.3,
                                               profit_factor_infinite=False, total_r=4.8, max_drawdown_r=-5.1)),
    edge=dict(score=52, band="promising", label="Promising"),
    analytics=dict(bootstrap=dict(expectancy=dict(low=-0.05, high=0.3)), monte_carlo=dict(p95=8.2)),
    data=dict(from_ms=1, to_ms=2, days=90, bars=8640, provenance_text="MFP feed",
              provenance=dict(segments=[dict(source="mfp", from_ms=1, to_ms=2)])),
    params=dict(mode="rule", setup_tf="1h", interval="15m", htf_confirm="4h", days=90, rule="liquidity_sweep"),
    costs=dict(slippage=dict(text="1 bps")), fees=dict(asset_class="crypto"),
    trades=[dict(placed=1, r_net=1.0)], candles=[[1, 2, 3, 4, 5]])


class Base(unittest.TestCase):
    def setUp(self):
        auth.reset_engine()
        if os.path.exists(os.environ["SQLITE_PATH"]):
            os.remove(os.environ["SQLITE_PATH"])
        auth.reset_auth_limits()
        web.reset_rate_limits()
        web.app.config["TESTING"] = True

    def client(self, email):
        c = web.app.test_client()
        c.ip = "203.0.113.%d" % (len(email) % 200 + 1)
        r = self.call(c, "post", "/api/auth/signup", {"email": email, "password": PW})
        self.assertIn(r.status_code, (200, 201), r.get_json())
        return c

    def call(self, c, method, path, body=None, csrf=True):
        headers = {}
        if csrf:
            headers["X-CSRF-Token"] = c.get("/api/auth/me").get_json()["csrf"]
        kw = dict(headers=headers, environ_base={"REMOTE_ADDR": getattr(c, "ip", "203.0.113.9")})
        if body is not None:
            kw["json"] = body
        return getattr(c, method)(path, **kw)


class TestAccess(Base):
    def test_signed_out(self):
        c = web.app.test_client()
        for m, p in (("get", "/api/library/strategies"), ("post", "/api/library/strategies"),
                     ("get", "/api/library/strategies/1"), ("put", "/api/library/strategies/1"),
                     ("delete", "/api/library/strategies/1"), ("post", "/api/library/strategies/1/duplicate"),
                     ("get", "/api/library/strategies/1/runs"), ("delete", "/api/library/runs/1"),
                     ("post", "/api/library/import")):
            r = self.call(c, m, p, SPEC if m in ("post", "put") else None)
            self.assertEqual(r.status_code, 401, (m, p))
            self.assertTrue(r.get_json().get("login_required"))

    def test_csrf_required_everywhere(self):
        a = self.client("a@example.com")
        sid = self.call(a, "post", "/api/library/strategies", SPEC).get_json()["strategy"]["id"]
        for m, p in (("get", "/api/library/strategies"), ("post", "/api/library/strategies"),
                     ("get", f"/api/library/strategies/{sid}"), ("put", f"/api/library/strategies/{sid}"),
                     ("delete", f"/api/library/strategies/{sid}"), ("post", f"/api/library/strategies/{sid}/duplicate"),
                     ("get", f"/api/library/strategies/{sid}/runs"), ("post", "/api/library/import")):
            r = self.call(a, m, p, SPEC if m in ("post", "put") else None, csrf=False)
            self.assertEqual(r.status_code, 403, (m, p))
        self.assertEqual(self.call(a, "get", f"/api/library/strategies/{sid}").status_code, 200)

    def test_ownership(self):
        a, b = self.client("a@example.com"), self.client("b@example.com")
        sid = self.call(a, "post", "/api/library/strategies", SPEC).get_json()["strategy"]["id"]
        library.save_run(auth.get_user_by_email("a@example.com")["id"], sid, FAKE_OUT, {})
        rid = self.call(a, "get", f"/api/library/strategies/{sid}/runs").get_json()["runs"][0]["id"]
        for m, p in (("get", f"/api/library/strategies/{sid}"), ("put", f"/api/library/strategies/{sid}"),
                     ("delete", f"/api/library/strategies/{sid}"), ("post", f"/api/library/strategies/{sid}/duplicate"),
                     ("get", f"/api/library/strategies/{sid}/runs"), ("delete", f"/api/library/runs/{rid}")):
            r = self.call(b, m, p, SPEC if m == "put" else None)
            self.assertEqual(r.status_code, 404, (m, p))
        self.assertEqual(self.call(b, "get", "/api/library/strategies").get_json()["strategies"], [])
        # untouched for the owner
        got = self.call(a, "get", f"/api/library/strategies/{sid}").get_json()["strategy"]
        self.assertEqual((got["name"], got["runs"]), (SPEC["name"], 1))
        bid = auth.get_user_by_email("b@example.com")["id"]
        self.assertIsNone(library.save_run(bid, sid, FAKE_OUT, {}), "cannot save a run on someone else's strategy")

    def test_run_endpoint_checks_owner_before_running(self):
        a, b = self.client("a@example.com"), self.client("b@example.com")
        sid = self.call(a, "post", "/api/library/strategies", SPEC).get_json()["strategy"]["id"]
        body = dict(market="BTC", side="long", entry=100, tp=102, sl=99, mode="rule", rule="liquidity_sweep", tf="1h",
                    strategy_id=sid)
        calls = []

        def fake_run(p, compare=False, timeout=None, cmd_fn=None):
            calls.append(p)
            return jsonify(FAKE_OUT)
        with mock.patch.object(web, "run_engine", fake_run), mock.patch.object(web, "resolve", lambda s: dict(market_id="binance|BTCUSDT")):
            r = self.call(b, "post", "/api/test", body)
            self.assertEqual(r.status_code, 404)
            r = self.call(a, "post", "/api/test", body, csrf=False)
            self.assertEqual(r.status_code, 403)
            self.assertEqual(calls, [], "engine never ran for a refused request")
            web.reset_rate_limits()
            r = self.call(a, "post", "/api/test", body)
            self.assertEqual(r.status_code, 200, r.get_json())
            j = r.get_json()
            self.assertTrue(j["library_run"]["saved"])
            self.assertEqual((calls[0]["mode"], calls[0]["rule"], calls[0]["max_hold"]), ("rule", "liquidity_sweep", "7d"))
            web.reset_rate_limits()
            r = self.call(web.app.test_client(), "post", "/api/test", body)
            self.assertEqual(r.status_code, 401, "signed out cannot attach a run to a strategy")
            web.reset_rate_limits()
            nb = dict(body)
            nb.pop("strategy_id")
            r = self.call(web.app.test_client(), "post", "/api/test", nb)
            self.assertEqual(r.status_code, 200, "tools still work signed out, just without saving")
            self.assertNotIn("library_run", r.get_json())


class TestCrud(Base):
    def test_create_list_get_update_duplicate_delete(self):
        a = self.client("a@example.com")
        r = self.call(a, "post", "/api/library/strategies", SPEC)
        self.assertEqual(r.status_code, 201, r.get_json())
        st = r.get_json()["strategy"]
        self.assertEqual(st["spec"]["rule_params"], {"lookback": 24, "stop_atr": 0.3})
        self.assertEqual(st["spec"]["markets"], SPEC["markets"])
        sid = st["id"]
        upd = dict(SPEC, name="Renamed", rr=3, tf="15m", htf_confirm="1h")
        r = self.call(a, "put", f"/api/library/strategies/{sid}", upd)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual((r.get_json()["strategy"]["name"], r.get_json()["strategy"]["spec"]["rr"]), ("Renamed", 3))
        r = self.call(a, "post", f"/api/library/strategies/{sid}/duplicate")
        self.assertEqual(r.status_code, 201)
        dup = r.get_json()["strategy"]
        self.assertEqual(dup["name"], "Renamed (copy)")
        self.assertEqual(dup["spec"], self.call(a, "get", f"/api/library/strategies/{sid}").get_json()["strategy"]["spec"])
        lst = self.call(a, "get", "/api/library/strategies").get_json()
        self.assertEqual(len(lst["strategies"]), 2)
        self.assertEqual((lst["limit"], lst["run_limit"]), (50, 200))
        self.assertEqual(self.call(a, "delete", f"/api/library/strategies/{sid}").status_code, 200)
        self.assertEqual(self.call(a, "get", f"/api/library/strategies/{sid}").status_code, 404)
        self.assertEqual(len(self.call(a, "get", "/api/library/strategies").get_json()["strategies"]), 1)

    def test_validation(self):
        a = self.client("a@example.com")
        bad = [dict(SPEC, name=""), dict(SPEC, name="x" * 81), dict(SPEC, tf="2h"), dict(SPEC, htf_confirm="1h"),
               dict(SPEC, setup_type="astrology"), dict(SPEC, rule_params={"lookback": 100000}),
               dict(SPEC, markets=[]), dict(SPEC, markets=["a", "b", "c", "d", "e", "f"]), dict(SPEC, side="up"),
               dict(SPEC, rr=0), dict(SPEC, stop_pct=None), dict(SPEC, expiry="20d"), dict(SPEC, max_hold="90d"),
               dict(SPEC, days=10), dict(SPEC, slippage=500), dict(SPEC, description="x" * 1001),
               dict(SPEC, setup_type="zone_retest", allow_overlap=True)]
        for b in bad:
            r = self.call(a, "post", "/api/library/strategies", b)
            self.assertEqual(r.status_code, 400, b)
        ok = dict(SPEC, setup_type="similar", rule_params={"ignored": 1}, max_hold="none")
        r = self.call(a, "post", "/api/library/strategies", ok)
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(r.get_json()["strategy"]["spec"]["rule_params"], {})

    def test_strategy_limit(self):
        a = self.client("a@example.com")
        with mock.patch.object(library, "MAX_STRATEGIES", 3):
            for i in range(3):
                self.assertEqual(self.call(a, "post", "/api/library/strategies", dict(SPEC, name=f"s{i}")).status_code, 201)
            self.assertEqual(self.call(a, "post", "/api/library/strategies", SPEC).status_code, 409)
            sid = self.call(a, "get", "/api/library/strategies").get_json()["strategies"][0]["id"]
            self.assertEqual(self.call(a, "post", f"/api/library/strategies/{sid}/duplicate").status_code, 409)
            self.assertEqual(self.call(a, "post", "/api/library/import", {"strategies": [SPEC]}).status_code, 409)

    def test_import(self):
        a = self.client("a@example.com")
        items = [dict(SPEC, name="EMA", setup_type="ema_cross", rule_params={"fast": 10, "slow": 30}),
                 dict(SPEC, name="Levels", setup_type="similar", rule_params={})]
        r = self.call(a, "post", "/api/library/import", {"strategies": items})
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(r.get_json()["imported"], 2)
        self.assertEqual(self.call(a, "post", "/api/library/import", {"strategies": [SPEC] * 7}).status_code, 400)
        self.assertEqual(self.call(a, "post", "/api/library/import", {"strategies": [dict(SPEC, tf="9h")]}).status_code, 400)
        self.assertEqual(len(self.call(a, "get", "/api/library/strategies").get_json()["strategies"]), 2)


class TestRuns(Base):
    def test_snapshot_contents_history_and_limit(self):
        a = self.client("a@example.com")
        uid = auth.get_user_by_email("a@example.com")["id"]
        sid = self.call(a, "post", "/api/library/strategies", SPEC).get_json()["strategy"]["id"]
        with mock.patch.object(library, "MAX_RUNS", 3):
            for i in range(5):
                out = json.loads(json.dumps(FAKE_OUT))
                out["edge"]["score"] = 40 + i
                library.save_run(uid, sid, out, dict(entry=100, tp=102, sl=99))
            runs = self.call(a, "get", f"/api/library/strategies/{sid}/runs").get_json()["runs"]
        self.assertEqual([r["metrics"]["edge_score"] for r in runs], [44, 43, 42], "newest first, oldest pruned")
        r0 = runs[0]
        self.assertEqual(r0["metrics"]["expectancy_r"], 0.12)
        self.assertEqual(r0["params"]["entry"], 100)
        self.assertEqual(r0["provenance"]["sources"], ["mfp"])
        self.assertTrue(r0["created_at"])
        flat = json.dumps(r0)
        self.assertNotIn("candles", flat)
        self.assertNotIn("trades", flat)
        lst = self.call(a, "get", "/api/library/strategies").get_json()["strategies"][0]
        self.assertEqual((lst["runs"], lst["last_run"]["metrics"]["edge_score"]), (3, 44))
        self.assertEqual(self.call(a, "delete", f"/api/library/runs/{runs[0]['id']}").status_code, 200)
        self.assertEqual(len(self.call(a, "get", f"/api/library/strategies/{sid}/runs").get_json()["runs"]), 2)

    def test_account_delete_removes_library(self):
        a = self.client("a@example.com")
        uid = auth.get_user_by_email("a@example.com")["id"]
        sid = self.call(a, "post", "/api/library/strategies", SPEC).get_json()["strategy"]["id"]
        library.save_run(uid, sid, FAKE_OUT, {})
        r = self.call(a, "post", "/api/auth/delete", {"password": PW})
        self.assertEqual(r.status_code, 200, r.get_json())
        with auth.engine().connect() as c:
            from sqlalchemy import func, select
            self.assertEqual(c.execute(select(func.count()).select_from(library.strategies)).scalar_one(), 0)
            self.assertEqual(c.execute(select(func.count()).select_from(library.strategy_runs)).scalar_one(), 0)


OLD_USERS_SQL = """CREATE TABLE users (id INTEGER NOT NULL PRIMARY KEY, email VARCHAR(254) NOT NULL UNIQUE,
password_hash VARCHAR(255) NOT NULL, plan VARCHAR(32) DEFAULT 'free' NOT NULL, created_at DATETIME NOT NULL,
free_until DATETIME, is_admin BOOLEAN DEFAULT 'false' NOT NULL)"""


class TestMigration(unittest.TestCase):
    def test_sqlite_adds_tables_and_keeps_accounts(self):
        path = os.path.join(TMP, "old.db")
        if os.path.exists(path):
            os.remove(path)
        con = sqlite3.connect(path)
        con.execute(OLD_USERS_SQL)
        con.execute("INSERT INTO users (email, password_hash, plan, created_at, is_admin) VALUES "
                    "('old@example.com', 'scrypt:hash', 'free', '2026-11-01 10:00:00', 0)")
        con.commit()
        con.close()
        with mock.patch.dict(os.environ, {"SQLITE_PATH": path}):
            auth.reset_engine()
            try:
                auth.engine()
                auth.engine()  # a second start is a no-op
                con = sqlite3.connect(path)
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"users", "strategies", "strategy_runs"} <= tables)
                self.assertEqual(con.execute("SELECT email, password_hash FROM users").fetchall(),
                                 [("old@example.com", "scrypt:hash")])
                con.close()
            finally:
                auth.reset_engine()

    @unittest.skipUnless(os.environ.get("CHRONOS_PG_TEST_URL"), "set CHRONOS_PG_TEST_URL to a scratch Postgres database")
    def test_postgres_adds_tables_and_keeps_accounts(self):
        from sqlalchemy import create_engine, inspect, text
        url = os.environ["CHRONOS_PG_TEST_URL"]
        raw = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1), future=True)
        with raw.begin() as c:
            c.execute(text("DROP TABLE IF EXISTS strategy_runs, strategies, users CASCADE"))
            c.execute(text("CREATE TABLE users (id SERIAL PRIMARY KEY, email VARCHAR(254) NOT NULL UNIQUE, "
                           "password_hash VARCHAR(255) NOT NULL, plan VARCHAR(32) NOT NULL DEFAULT 'free', "
                           "created_at TIMESTAMPTZ NOT NULL, free_until TIMESTAMPTZ, is_admin BOOLEAN NOT NULL DEFAULT false)"))
            c.execute(text("INSERT INTO users (email, password_hash, created_at) VALUES ('old@example.com', 'scrypt:hash', now())"))
        with mock.patch.dict(os.environ, {"DATABASE_URL": url}):
            auth.reset_engine()
            try:
                self.assertEqual(auth.db_kind(), "postgres")
                auth.engine()
                auth.engine()
                insp = inspect(raw)
                self.assertTrue({"users", "strategies", "strategy_runs"} <= set(insp.get_table_names()))
                fks = insp.get_foreign_keys("strategies")
                self.assertEqual((fks[0]["referred_table"], fks[0]["options"].get("ondelete")), ("users", "CASCADE"))
                with raw.connect() as c:
                    self.assertEqual(c.execute(text("SELECT email, password_hash FROM users")).all(),
                                     [("old@example.com", "scrypt:hash")])
                # library works on Postgres too
                uid = auth.get_user_by_email("old@example.com")["id"]
                with auth.engine().begin() as c:
                    from sqlalchemy import insert
                    sid = c.execute(insert(library.strategies).values(
                        user_id=uid, name="pg", description="", spec=json.dumps(SPEC),
                        created_at=library._now(), updated_at=library._now())).inserted_primary_key[0]
                self.assertTrue(library.save_run(uid, sid, FAKE_OUT, {}))
                auth.delete_user(uid)
                with raw.connect() as c:
                    self.assertEqual(c.execute(text("SELECT count(*) FROM strategies")).scalar(), 0)
            finally:
                auth.reset_engine()
                with raw.begin() as c:
                    c.execute(text("DROP TABLE IF EXISTS strategy_runs, strategies, users CASCADE"))
                raw.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=1)
