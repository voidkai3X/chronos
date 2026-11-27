#!/usr/bin/env python3
"""Offline tests for CHRONOS accounts (SQLite in a temp dir, no network, no engine run)."""
import datetime as dt, importlib.util, io, logging, os, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix="chronos-auth-")
os.environ.pop("DATABASE_URL", None)
os.environ["SQLITE_PATH"] = os.path.join(TMP, "test.db")
os.environ["SECRET_KEY"] = "test-secret-key-not-used-in-production"
os.environ.pop("REQUIRE_LOGIN", None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("chronos_web_auth", os.path.join(ROOT, "web", "app.py"))
web = importlib.util.module_from_spec(spec)
sys.modules["chronos_web_auth"] = web
spec.loader.exec_module(web)
auth = web.auth

PW = "correct horse battery"


class TestAuth(unittest.TestCase):
    def setUp(self):
        auth.reset_engine()
        if os.path.exists(os.environ["SQLITE_PATH"]):
            os.remove(os.environ["SQLITE_PATH"])
        auth.reset_auth_limits()
        web.reset_rate_limits()
        os.environ.pop("REQUIRE_LOGIN", None)
        web.app.config["TESTING"] = True
        self.c = web.app.test_client()
        self.ip = "203.0.113.50"

    def tearDown(self):
        os.environ.pop("REQUIRE_LOGIN", None)
        web.app.config["TESTING"] = False

    # helpers
    def me(self, c=None):
        return (c or self.c).get("/api/auth/me", environ_base={"REMOTE_ADDR": self.ip}).get_json()

    def post(self, path, body=None, csrf=True, c=None, ip=None):
        c = c or self.c
        headers = {}
        if csrf:
            headers["X-CSRF-Token"] = self.me(c)["csrf"]
        return c.post(path, json=body or {}, headers=headers, environ_base={"REMOTE_ADDR": ip or self.ip})

    def signup(self, email="Trader@Example.com", pw=PW, **kw):
        return self.post("/api/auth/signup", {"email": email, "password": pw}, **kw)

    # tests
    def test_cookie_flags_and_secret(self):
        self.assertEqual(web.app.secret_key, "test-secret-key-not-used-in-production")
        self.assertTrue(web.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertTrue(web.app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(web.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        r = self.c.get("/api/auth/me")
        cookie = r.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_signup_creates_free_user_and_signs_in(self):
        r = self.signup()
        self.assertEqual(r.status_code, 201, r.get_json())
        u = r.get_json()["user"]
        self.assertEqual(u["email"], "trader@example.com")
        self.assertEqual(u["plan"], "free")
        self.assertFalse(u["is_admin"])
        self.assertTrue(u["has_access"])
        created = dt.datetime.fromisoformat(u["created_at"])
        until = dt.datetime.fromisoformat(u["free_until"])
        self.assertAlmostEqual((until - created).days, 365, delta=1)
        self.assertEqual(self.me()["user"]["email"], "trader@example.com")
        row = auth.get_user_by_email("trader@example.com")
        self.assertNotIn(PW, row["password_hash"])
        self.assertTrue(row["password_hash"].startswith(("scrypt:", "pbkdf2:")))

    def test_validation(self):
        self.assertEqual(self.signup(email="not-an-email").status_code, 400)
        self.assertEqual(self.signup(email="a@b").status_code, 400)
        r = self.signup(pw="short")
        self.assertEqual(r.status_code, 400)
        self.assertIn("8", r.get_json()["error"])
        self.assertEqual(self.signup().status_code, 201)
        c2 = web.app.test_client()
        r = self.signup(email="trader@example.com", c=c2, ip="203.0.113.51")
        self.assertEqual(r.status_code, 409)

    def test_csrf_required(self):
        r = self.signup(csrf=False)
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/api/auth/signin", json={"email": "x@y.com", "password": PW},
                        headers={"X-CSRF-Token": "wrong"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.post("/api/auth/signout", csrf=False).status_code, 403)

    def test_signin_signout_and_generic_error(self):
        self.assertEqual(self.signup().status_code, 201)
        self.assertEqual(self.post("/api/auth/signout").status_code, 200)
        self.assertIsNone(self.me()["user"])
        bad_pw = self.post("/api/auth/signin", {"email": "trader@example.com", "password": "wrong password"})
        no_user = self.post("/api/auth/signin", {"email": "nobody@example.com", "password": "wrong password"})
        self.assertEqual(bad_pw.status_code, 401)
        self.assertEqual(no_user.status_code, 401)
        self.assertEqual(bad_pw.get_json()["error"], no_user.get_json()["error"])
        ok = self.post("/api/auth/signin", {"email": "  TRADER@example.com ", "password": PW})
        self.assertEqual(ok.status_code, 200, ok.get_json())
        self.assertEqual(self.me()["user"]["email"], "trader@example.com")

    def test_signin_rate_limit_per_email(self):
        self.signup()
        self.post("/api/auth/signout")
        for i in range(auth.SIGNIN_FAIL_PER_EMAIL):
            r = self.post("/api/auth/signin", {"email": "trader@example.com", "password": "nope nope nope"},
                          ip=f"198.51.100.{i}")
            self.assertEqual(r.status_code, 401)
        # even the right password is refused while locked, from any IP
        r = self.post("/api/auth/signin", {"email": "trader@example.com", "password": PW}, ip="198.51.100.200")
        self.assertEqual(r.status_code, 429)

    def test_signin_rate_limit_per_ip(self):
        for i in range(auth.SIGNIN_PER_IP):
            r = self.post("/api/auth/signin", {"email": f"u{i}@example.com", "password": "whatever123"})
            self.assertEqual(r.status_code, 401)
        r = self.post("/api/auth/signin", {"email": "z@example.com", "password": "whatever123"})
        self.assertEqual(r.status_code, 429)

    def test_signup_rate_limit_per_ip(self):
        for i in range(auth.SIGNUP_PER_IP):
            c = web.app.test_client()
            self.assertEqual(self.signup(email=f"n{i}@example.com", c=c).status_code, 201)
        r = self.signup(email="late@example.com", c=web.app.test_client())
        self.assertEqual(r.status_code, 429)

    def test_password_never_logged(self):
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        logging.getLogger().addHandler(h)
        old_err = sys.stderr
        sys.stderr = buf
        try:
            self.signup()
            self.post("/api/auth/signin", {"email": "trader@example.com", "password": "wrong-" + PW})
        finally:
            sys.stderr = old_err
            logging.getLogger().removeHandler(h)
        self.assertNotIn(PW, buf.getvalue())

    def test_copy_trading_needs_login(self):
        r = self.c.get("/api/copy-trading")
        self.assertEqual(r.status_code, 401)
        self.assertTrue(r.get_json()["login_required"])
        self.signup()
        r = self.c.get("/api/copy-trading")
        self.assertEqual(r.status_code, 200)
        self.assertIn("coming soon", r.get_json()["message"])

    def test_expired_free_access(self):
        self.signup()
        with auth.engine().begin() as c:
            c.execute(auth.users.update().values(free_until=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)))
        self.assertEqual(self.c.get("/api/copy-trading").status_code, 402)
        with auth.engine().begin() as c:
            c.execute(auth.users.update().values(plan="pro"))
        self.assertEqual(self.c.get("/api/copy-trading").status_code, 200)

    def test_delete_account(self):
        self.signup()
        self.assertEqual(self.post("/api/auth/delete", {"password": "wrong"}).status_code, 401)
        r = self.post("/api/auth/delete", {"password": PW})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(auth.get_user_by_email("trader@example.com"))
        self.assertIsNone(self.me()["user"])

    def test_session_dies_when_account_deleted_elsewhere(self):
        self.signup()
        uid = auth.get_user_by_email("trader@example.com")["id"]
        auth.delete_user(uid)
        self.assertIsNone(self.me()["user"])

    def test_forgot_is_stub(self):
        r = self.post("/api/auth/forgot", {"email": "trader@example.com"})
        self.assertEqual(r.status_code, 501)
        self.assertTrue(r.get_json()["coming_soon"])

    def test_tools_open_by_default(self):
        r = self.c.post("/api/test", json={}, environ_base={"REMOTE_ADDR": "203.0.113.70"})
        self.assertEqual(r.status_code, 400)  # validation error, not a login error

    def test_require_login_gates_tools(self):
        os.environ["REQUIRE_LOGIN"] = "1"
        r = self.c.post("/api/test", json={}, environ_base={"REMOTE_ADDR": "203.0.113.71"})
        self.assertEqual(r.status_code, 401)
        self.signup()
        r = self.c.post("/api/test", json={}, environ_base={"REMOTE_ADDR": "203.0.113.71"})
        self.assertEqual(r.status_code, 403)  # missing CSRF
        r = self.post("/api/test", {}, ip="203.0.113.71")
        self.assertEqual(r.status_code, 400)  # signed in + CSRF: reaches validation
        self.assertTrue(self.c.get("/api/health").get_json()["require_login"])

    def test_pages_served(self):
        for path in ("/signin", "/signup", "/forgot"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200)
            html = r.get_data(as_text=True)
            self.assertIn("auth-form", html)
            self.assertIn("/static/auth.js?v=", html)
            self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")
        r = self.c.get("/copy-trading")
        html = r.get_data(as_text=True)
        self.assertIn('id="view-ct"', html)
        self.assertIn("/static/account.js?v=", html)
        self.assertIn("Copy trading", html)

    def test_database_url_normalised(self):
        old = os.environ.get("DATABASE_URL")
        try:
            os.environ["DATABASE_URL"] = "postgres://u:p@host/db"
            self.assertEqual(auth.database_url(), "postgresql+psycopg://u:p@host/db")
            os.environ["DATABASE_URL"] = "postgresql://u:p@host/db"
            self.assertEqual(auth.database_url(), "postgresql+psycopg://u:p@host/db")
            self.assertEqual(auth.db_kind(), "postgres")
        finally:
            if old is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = old
        self.assertEqual(auth.db_kind(), "sqlite")


if __name__ == "__main__":
    unittest.main(verbosity=1)
