#!/usr/bin/env python3
"""Offline tests for the trading journal: validation, derived R and R:R, ownership, filters, reports, calendar,
CSV export (with formula guard), evidence-gated observations (never shown below their minimum sample) and deletion.
Run: python3 tests/test_journal.py
"""
import csv, datetime as dt, importlib.util, io, os, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix="chronos-journal-")
os.environ.pop("DATABASE_URL", None)
os.environ["SQLITE_PATH"] = os.path.join(TMP, "j.db")
os.environ["SECRET_KEY"] = "test-secret-key-not-used-in-production"
os.environ["COOKIE_INSECURE"] = "1"
os.environ.pop("REQUIRE_LOGIN", None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("chronos_web_journal", os.path.join(ROOT, "web", "app.py"))
web = importlib.util.module_from_spec(spec)
sys.modules["chronos_web_journal"] = web
spec.loader.exec_module(web)
auth, journal = web.auth, web.journal
from sqlalchemy import select

PW = "correct horse battery"


def iso(days_ago=1, hour=9, minute=0):
    t = dt.datetime.now(dt.timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0) - dt.timedelta(days=days_ago)
    return t.isoformat()


def trade(**kw):
    d = dict(market="BTCUSDT", side="long", entry=100, sl=99, tp=103, exit_price=101, size=10, opened_at=iso(2, 9),
             closed_at=iso(2, 11), exit_reason="manual", strategy_name="Sweep")
    d.update(kw)
    return d


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
        c.ip = "192.0.2.%d" % (len(email) % 200 + 1)
        r = self.call(c, "post", "/api/auth/signup", {"email": email, "password": PW})
        self.assertIn(r.status_code, (200, 201), r.get_json())
        return c

    def call(self, c, method, path, body=None, csrf=True):
        headers = {"X-CSRF-Token": c.get("/api/auth/me").get_json()["csrf"]} if csrf else {}
        kw = dict(headers=headers, environ_base={"REMOTE_ADDR": getattr(c, "ip", "192.0.2.9")})
        if body is not None:
            kw["json"] = body
        return getattr(c, method)(path, **kw)

    def add(self, c, **kw):
        r = self.call(c, "post", "/api/journal/trades", trade(**kw))
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["trade"]


class TestCrud(Base):
    def test_derived_values(self):
        c = self.client("d@example.com")
        t = self.add(c)
        self.assertEqual(t["r_multiple"], 1.0)
        self.assertEqual(t["planned_rr"], 3.0)
        self.assertEqual(t["pnl"], 10.0)
        self.assertEqual(t["risk_amount"], 10.0)
        self.assertEqual(t["session"], "London")   # 09:00 UTC
        self.assertEqual(t["outcome"], "win")
        s = self.add(c, side="short", entry=100, sl=102, tp=96, exit_price=102, fees=1, opened_at=iso(1, 2), closed_at=iso(1, 4))
        self.assertAlmostEqual(s["r_multiple"], -1.05)
        self.assertEqual(s["session"], "Asia")
        self.assertEqual(s["pnl"], -21.0)

    def test_validation(self):
        c = self.client("v@example.com")
        for bad in (dict(sl=101), dict(tp=99), dict(side="up"), dict(entry=-1), dict(opened_at="yesterday"),
                    dict(closed_at=iso(5)), dict(chart_url="http://x.example/a.png"), dict(chart_url="javascript:alert(1)"),
                    dict(session="Moon"), dict(exit_reason="hope"), dict(opened_at=iso(-5))):
            r = self.call(c, "post", "/api/journal/trades", trade(**bad))
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.add(c, chart_url="https://www.tradingview.com/x/abc/")["chart_url"], "https://www.tradingview.com/x/abc/")

    def test_access(self):
        anon = web.app.test_client()
        self.assertEqual(anon.get("/api/journal/trades").status_code, 401)
        a, b = self.client("a@example.com"), self.client("bb@example.com")
        self.assertEqual(self.call(a, "post", "/api/journal/trades", trade(), csrf=False).status_code, 403)
        self.assertEqual(self.call(a, "get", "/api/journal/trades", csrf=False).status_code, 200)
        t = self.add(a)
        for m in ("get", "put", "delete"):
            self.assertEqual(self.call(b, m, f"/api/journal/trades/{t['id']}", trade() if m == "put" else None).status_code, 404)
        self.assertEqual(self.call(b, "get", "/api/journal/trades").get_json()["total"], 0)
        r = self.call(a, "put", f"/api/journal/trades/{t['id']}", trade(exit_price=99))
        self.assertEqual(r.get_json()["trade"]["outcome"], "loss")
        self.assertEqual(self.call(a, "delete", f"/api/journal/trades/{t['id']}").status_code, 200)

    def test_strategy_link_must_be_owned(self):
        a, b = self.client("sa@example.com"), self.client("sbb@example.com")
        spec = dict(name="Mine", setup_type="similar", side="long", markets=["binance|BTCUSDT"], tf="1h", htf_confirm="off",
                    stop_pct=1, rr=2, expiry="72h", max_hold="3d", days=90, slippage=1, slippage_unit="bps")
        sid = self.call(a, "post", "/api/library/strategies", spec).get_json()["strategy"]["id"]
        self.assertEqual(self.add(a, strategy_id=sid)["strategy_name"], "Mine")
        self.assertEqual(self.call(b, "post", "/api/journal/trades", trade(strategy_id=sid)).status_code, 400)

    def test_filters_calendar_report_csv(self):
        c = self.client("f@example.com")
        self.add(c, market="ETHUSDT", notes="=cmd|' /C calc'!A0", opened_at=iso(0, 1, 5), closed_at=iso(0, 2))
        self.add(c, exit_price=98.5)
        r = self.call(c, "get", "/api/journal/trades?market=ETHUSDT").get_json()
        self.assertEqual((r["shown"], r["total"]), (1, 2))
        self.assertEqual(self.call(c, "get", "/api/journal/trades?outcome=loss").get_json()["shown"], 1)
        self.assertEqual(self.call(c, "get", "/api/journal/trades?q=calc").get_json()["shown"], 1)
        month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
        cal = self.call(c, "get", f"/api/journal/calendar?month={month}").get_json()
        self.assertGreaterEqual(sum(d["trades"] for d in cal["days"].values()), 1)
        rep = self.call(c, "get", "/api/journal/report?period=month").get_json()["report"]
        self.assertIn("summary", rep)
        self.assertEqual(self.call(c, "get", "/api/journal/report?period=year").status_code, 400)
        body = self.call(c, "get", "/api/journal/export.csv").get_data(as_text=True)
        rows = list(csv.DictReader(io.StringIO(body)))
        self.assertEqual(len(rows), 2)
        self.assertTrue(any(r["notes"].startswith("'=") for r in rows))

    def test_delete_account_removes_journal(self):
        c = self.client("del@example.com")
        self.add(c)
        auth.delete_user(auth.get_user_by_email("del@example.com")["id"])
        with auth.engine().connect() as cn:
            self.assertEqual(cn.execute(select(journal.journal_trades)).fetchall(), [])


class TestObservations(unittest.TestCase):
    def items(self, rows):
        out = []
        for i, r in enumerate(rows):
            base = dict(id=i, market="BTCUSDT", side="long", entry=100.0, sl=99.0, tp=103.0, exit_price=101.0, size=1.0, fees=None,
                        pnl=None, risk_amount=1.0, strategy_id=None, strategy_name="A", session="London", exit_reason="manual",
                        sl_moved=False, notes="", tags="", chart_url="", created_at=None, updated_at=None,
                        opened_at=dt.datetime(2026, 10, 1 + i % 28, 9, tzinfo=dt.timezone.utc),
                        closed_at=dt.datetime(2026, 10, 1 + i % 28, 10, tzinfo=dt.timezone.utc))
            base.update(r)
            out.append(journal.public(base))
        return out

    def test_nothing_below_minimum_sample(self):
        o = journal.observations(self.items([{}] * 4))
        self.assertEqual(o["items"], [])
        self.assertTrue(o["small_sample"])
        self.assertTrue(all("need" in s and "have" in s for s in o["not_enough_data"]))

    def test_planned_vs_actual_with_counts(self):
        o = journal.observations(self.items([{}] * 12))
        pv = next(x for x in o["items"] if x["id"] == "planned_vs_actual")
        self.assertEqual(pv["counts"], {"trades": 12, "winners": 12})
        self.assertEqual((pv["planned_rr"], pv["realised_win_rr"]), (3.0, 1.0))
        self.assertIn("12 trades", pv["text"])
        early = next(x for x in o["items"] if x["id"] == "early_exits")
        self.assertEqual(early["counts"], {"early": 12, "trades": 12})

    def test_stop_moves_and_sessions(self):
        rows = [dict(sl_moved=True, exit_price=99.0)] * 6 + [dict(session="Asia", exit_price=103.0)] * 6
        o = journal.observations(self.items(rows))
        ids = {x["id"] for x in o["items"]}
        self.assertIn("stop_moves", ids)
        self.assertIn("sessions", ids)

    def test_after_loss(self):
        rows = []
        for i in range(6):
            day = dt.datetime(2026, 10, 1 + i, tzinfo=dt.timezone.utc)
            rows.append(dict(exit_price=99.0, opened_at=day.replace(hour=8), closed_at=day.replace(hour=9)))
            rows.append(dict(exit_price=99.0, opened_at=day.replace(hour=9, minute=30), closed_at=day.replace(hour=11)))
        o = journal.observations(self.items(rows))
        al = next(x for x in o["items"] if x["id"] == "after_loss")
        self.assertEqual(al["counts"]["after_loss"], 6)


if __name__ == "__main__":
    unittest.main()
