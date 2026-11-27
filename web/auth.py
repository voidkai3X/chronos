"""CHRONOS accounts: email + password sign-up, sign-in, sign-out.

- Passwords are hashed with werkzeug's scrypt (never stored or logged in plain text).
- Sessions are Flask signed cookies (HttpOnly, Secure, SameSite=Lax) keyed by SECRET_KEY.
- Every state-changing auth POST needs the CSRF token from GET /api/auth/me
  (sent as the X-CSRF-Token header, or a csrf_token form field).
- Storage: DATABASE_URL (Postgres) when set, otherwise a local SQLite file for development.
"""
import datetime as dt
import hmac
import os
import re
import secrets
import sys
import threading
import time

from flask import Blueprint, g, jsonify, request, session
from sqlalchemy import (Boolean, Column, DateTime, Integer, MetaData, String, Table,
                        create_engine, delete, func, insert, select)
from werkzeug.security import check_password_hash, generate_password_hash

HERE = os.path.dirname(os.path.abspath(__file__))

FREE_DAYS = int(os.environ.get("FREE_DAYS", "365"))
MIN_PASSWORD = 8
MAX_PASSWORD = 256
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
GENERIC_LOGIN_ERROR = "Wrong email or password."

# brute-force limits (in memory, per process)
SIGNIN_PER_IP = int(os.environ.get("RATE_SIGNIN_PER_IP", "20"))        # attempts per 15 min
SIGNIN_FAIL_PER_EMAIL = int(os.environ.get("RATE_SIGNIN_FAIL_PER_EMAIL", "5"))  # failures per 15 min
SIGNUP_PER_IP = int(os.environ.get("RATE_SIGNUP_PER_IP", "5"))         # per hour
SIGNUP_PER_EMAIL = 3                                                    # per hour
WINDOW_SIGNIN = 15 * 60
WINDOW_SIGNUP = 60 * 60

metadata = MetaData()
users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", String(254), nullable=False, unique=True, index=True),
    Column("password_hash", String(255), nullable=False),
    Column("plan", String(32), nullable=False, default="free", server_default="free"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("free_until", DateTime(timezone=True), nullable=True),
    Column("is_admin", Boolean, nullable=False, default=False, server_default="false"),
)

_engine = None
_engine_lock = threading.Lock()
_hits_lock = threading.Lock()
_hits = {}  # key -> [timestamps]
# a real scrypt hash so unknown emails take the same time as wrong passwords
_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(16))

bp = Blueprint("auth", __name__)


# ---------------------------------------------------------------- config
def database_url():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url
    path = os.environ.get("SQLITE_PATH") or os.path.join(os.path.dirname(HERE), "data", "chronos.db")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return "sqlite:///" + os.path.abspath(path)


def db_kind():
    return "postgres" if database_url().startswith("postgresql") else "sqlite"


def engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            url = database_url()
            kw = {"pool_pre_ping": True, "future": True}
            if url.startswith("sqlite"):
                kw["connect_args"] = {"check_same_thread": False}
            else:
                kw.update(pool_size=3, max_overflow=2, pool_recycle=300)
            _engine = create_engine(url, **kw)
            metadata.create_all(_engine)
        return _engine


def db_ok():
    """True when the accounts database answers a trivial query. Never exposes the URL or the error text."""
    try:
        from sqlalchemy import text
        with engine().connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def reset_engine():
    """Test helper: drop the cached engine so a new DATABASE_URL / SQLITE_PATH is used."""
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None


def require_login_enabled():
    return (os.environ.get("REQUIRE_LOGIN") or "").strip().lower() in ("1", "true", "yes", "on")


def init_app(app):
    key = (os.environ.get("SECRET_KEY") or "").strip()
    if not key:
        key = secrets.token_hex(32)
        print("WARNING: SECRET_KEY not set; using a random key, so sign-ins reset on every restart.",
              file=sys.stderr, flush=True)
    app.secret_key = key
    insecure = os.environ.get("COOKIE_INSECURE") == "1"  # local http testing only
    app.config.update(
        SESSION_COOKIE_NAME="chronos_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=not insecure,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30),
    )
    app.register_blueprint(bp)


# ---------------------------------------------------------------- helpers
def _now():
    return dt.datetime.now(dt.timezone.utc)


def _aware(v):
    if v is None:
        return None
    return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)


def _err(msg, code=400, **extra):
    return jsonify(error=msg, **extra), code


def normalize_email(raw):
    e = str(raw or "").strip().lower()
    if not e or len(e) > 254 or not EMAIL_RE.match(e):
        return None
    return e


def check_password_rules(pw):
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if len(pw) > MAX_PASSWORD:
        return f"Password must be at most {MAX_PASSWORD} characters."
    return None


def csrf_token():
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["csrf"] = tok
    return tok


def csrf_ok():
    want = session.get("csrf")
    got = request.headers.get("X-CSRF-Token") or (request.form.get("csrf_token") if request.form else None)
    if not got:
        d = request.get_json(silent=True)
        if isinstance(d, dict):
            got = d.get("csrf_token")
    return bool(want and got and hmac.compare_digest(str(want), str(got)))


def _client_ip():
    try:
        from flask import current_app
        fn = current_app.config.get("CLIENT_IP_FN")
        if fn:
            return fn()
    except Exception:
        pass
    return request.remote_addr or "unknown"


def _count(key, window, now=None):
    now = time.time() if now is None else now
    with _hits_lock:
        ts = [t for t in _hits.get(key, []) if now - t < window]
        if ts:
            _hits[key] = ts
        else:
            _hits.pop(key, None)
        if len(_hits) > 20000:  # bound memory under abuse
            for k in [k for k, v in _hits.items() if not v or now - v[-1] >= WINDOW_SIGNUP]:
                _hits.pop(k, None)
        return len(ts)


def _hit(key):
    with _hits_lock:
        _hits.setdefault(key, []).append(time.time())


def _clear(key):
    with _hits_lock:
        _hits.pop(key, None)


def reset_auth_limits():
    with _hits_lock:
        _hits.clear()


def public_user(row):
    if not row:
        return None
    fu = _aware(row["free_until"])
    return dict(id=row["id"], email=row["email"], plan=row["plan"],
                created_at=_aware(row["created_at"]).isoformat(),
                free_until=fu.isoformat() if fu else None,
                is_admin=bool(row["is_admin"]), has_access=has_access(row))


def has_access(row):
    """Paid-tools access: any paid plan, admins, or a free account still inside its free period."""
    if not row:
        return False
    if row["is_admin"] or (row["plan"] or "free") != "free":
        return True
    fu = _aware(row["free_until"])
    return fu is None or fu > _now()


def get_user_by_id(uid):
    with engine().connect() as c:
        r = c.execute(select(users).where(users.c.id == uid)).mappings().first()
        return dict(r) if r else None


def get_user_by_email(email):
    with engine().connect() as c:
        r = c.execute(select(users).where(users.c.email == email)).mappings().first()
        return dict(r) if r else None


def create_user(email, password, is_admin=False):
    now = _now()
    with engine().begin() as c:
        res = c.execute(insert(users).values(
            email=email, password_hash=generate_password_hash(password),
            plan="free", created_at=now, free_until=now + dt.timedelta(days=FREE_DAYS),
            is_admin=bool(is_admin)))
        uid = res.inserted_primary_key[0]
    return get_user_by_id(uid)


def delete_user(uid):
    with engine().begin() as c:
        try:
            import library  # strategies and run snapshots go with the account
            library.delete_user_data(c, uid)
        except ImportError:
            pass
        for name in ("practice", "journal"):  # replay sessions and journal entries go with the account too
            try:
                mod = __import__(name)
            except ImportError:
                continue
            mod.delete_user_data(c, uid)
        c.execute(delete(users).where(users.c.id == uid))


def user_count():
    with engine().connect() as c:
        return c.execute(select(func.count()).select_from(users)).scalar_one()


def current_user():
    if "user" in g:
        return g.user
    uid = session.get("uid")
    u = None
    if uid is not None:
        try:
            u = get_user_by_id(int(uid))
        except Exception:
            u = None
        # a session minted before a password change / account delete is no longer valid
        if u is None or session.get("pwv") != u["password_hash"][-16:]:
            session.pop("uid", None)
            session.pop("pwv", None)
            u = None
    g.user = u
    return u


def _login(u):
    csrf = secrets.token_urlsafe(32)
    session.clear()  # fresh session on sign-in (prevents fixation)
    session.permanent = True
    session["uid"] = u["id"]
    session["pwv"] = u["password_hash"][-16:]
    session["csrf"] = csrf
    g.user = u


def login_required_response():
    return _err("Please sign in to use this.", 401, login_required=True)


def gate_tools():
    """When REQUIRE_LOGIN is on, heavy tools need a signed-in user with access and a CSRF token.
    Returns a response to send back, or None to continue."""
    if not require_login_enabled():
        return None
    u = current_user()
    if not u:
        return login_required_response()
    if not csrf_ok():
        return _err("Your session expired. Refresh the page and try again.", 403, csrf=True)
    if not has_access(u):
        return _err("Your free access has ended.", 402, upgrade_required=True)
    return None


def _body():
    d = request.get_json(silent=True)
    if isinstance(d, dict):
        return d
    return request.form.to_dict() if request.form else {}


# ---------------------------------------------------------------- routes
@bp.get("/api/auth/me")
def me():
    u = current_user()
    resp = jsonify(user=public_user(u), csrf=csrf_token(), require_login=require_login_enabled())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/api/auth/signup")
def signup():
    if not csrf_ok():
        return _err("Your session expired. Refresh the page and try again.", 403, csrf=True)
    d = _body()
    ip = _client_ip()
    if _count("su-ip:" + ip, WINDOW_SIGNUP) >= SIGNUP_PER_IP:
        return _err("Too many sign-ups from your network. Try again later.", 429)
    email = normalize_email(d.get("email"))
    if not email:
        return _err("Enter a valid email address.")
    if _count("su-em:" + email, WINDOW_SIGNUP) >= SIGNUP_PER_EMAIL:
        return _err("Too many attempts for this email. Try again later.", 429)
    pw = d.get("password")
    bad = check_password_rules(pw)
    if bad:
        return _err(bad)
    _hit("su-ip:" + ip)
    _hit("su-em:" + email)
    if get_user_by_email(email):
        return _err("An account with this email already exists. Sign in instead.", 409)
    try:
        u = create_user(email, pw)
    except Exception:
        # unique race or DB down: do not leak details
        if get_user_by_email(email):
            return _err("An account with this email already exists. Sign in instead.", 409)
        return _err("Could not create the account right now. Try again in a minute.", 503)
    _login(u)
    return jsonify(user=public_user(u), csrf=session["csrf"]), 201


@bp.post("/api/auth/signin")
def signin():
    if not csrf_ok():
        return _err("Your session expired. Refresh the page and try again.", 403, csrf=True)
    d = _body()
    ip = _client_ip()
    if _count("si-ip:" + ip, WINDOW_SIGNIN) >= SIGNIN_PER_IP:
        return _err("Too many sign-in attempts. Try again in 15 minutes.", 429)
    _hit("si-ip:" + ip)
    email = normalize_email(d.get("email"))
    pw = d.get("password") if isinstance(d.get("password"), str) else ""
    if email and _count("si-em:" + email, WINDOW_SIGNIN) >= SIGNIN_FAIL_PER_EMAIL:
        return _err("Too many sign-in attempts. Try again in 15 minutes.", 429)
    u = get_user_by_email(email) if email and pw and len(pw) <= MAX_PASSWORD else None
    ok = check_password_hash(u["password_hash"] if u else _DUMMY_HASH, pw[:MAX_PASSWORD] or "x")
    if not (u and ok):
        if email:
            _hit("si-em:" + email)
        return _err(GENERIC_LOGIN_ERROR, 401)
    _clear("si-em:" + email)
    _login(u)
    return jsonify(user=public_user(u), csrf=session["csrf"])


@bp.post("/api/auth/signout")
def signout():
    if not csrf_ok():
        return _err("Your session expired. Refresh the page and try again.", 403, csrf=True)
    session.clear()
    g.user = None
    return jsonify(ok=True, csrf=csrf_token())


@bp.post("/api/auth/forgot")
def forgot():
    if not csrf_ok():
        return _err("Your session expired. Refresh the page and try again.", 403, csrf=True)
    return jsonify(ok=False, coming_soon=True,
                   message="Password reset by email is coming soon. For now, contact CHRONOS on X (@ChronosBacktest)."), 501


@bp.post("/api/auth/delete")
def delete_account():
    """Delete the signed-in account. Needs the current password again."""
    if not csrf_ok():
        return _err("Your session expired. Refresh the page and try again.", 403, csrf=True)
    u = current_user()
    if not u:
        return login_required_response()
    d = _body()
    pw = d.get("password") if isinstance(d.get("password"), str) else ""
    if not pw or not check_password_hash(u["password_hash"], pw[:MAX_PASSWORD]):
        return _err(GENERIC_LOGIN_ERROR, 401)
    delete_user(u["id"])
    session.clear()
    g.user = None
    return jsonify(ok=True, deleted=True, csrf=csrf_token())


@bp.get("/api/copy-trading")
def copy_trading():
    """Members-only section. Placeholder until strategies are published."""
    u = current_user()
    if not u:
        return login_required_response()
    if not has_access(u):
        return _err("Your free access has ended.", 402, upgrade_required=True)
    resp = jsonify(status="coming_soon", title="Copy trading",
                   message="Strategies with a tested edge, coming soon.", strategies=[])
    resp.headers["Cache-Control"] = "no-store"
    return resp
