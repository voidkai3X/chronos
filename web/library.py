"""CHRONOS personal strategy library (signed-in users only).

Tables (created by metadata.create_all, which only ever adds missing tables, so existing accounts are untouched):
  strategies      one saved setup per row: name, description and a JSON spec
  strategy_runs   one result snapshot per backtest run of a saved strategy: key metrics, Edge Score,
                  parameters and data provenance. Never raw candles or the trade list.
Every endpoint needs a signed-in user and the CSRF token (X-CSRF-Token header), and only ever touches rows
owned by that user: someone else's id answers 404, exactly like an id that does not exist.
"""
import datetime as dt
import json

from flask import Blueprint, jsonify, request
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, Text, delete, func, insert, select, update

import auth

MAX_STRATEGIES = 50
MAX_RUNS = 200       # per strategy; the oldest snapshots are removed beyond this
MAX_IMPORT = 6
NAME_MAX, TEXT_MAX, LABEL_MAX = 80, 1000, 80

strategies = Table(
    "strategies", auth.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("name", String(NAME_MAX), nullable=False),
    Column("description", Text, nullable=False, default=""),
    Column("spec", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
strategy_runs = Table(
    "strategy_runs", auth.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("strategy_id", Integer, ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("snapshot", Text, nullable=False),
)

bp = Blueprint("library", __name__)


# ---------------------------------------------------------------- validation
def _num(d, k, lo, hi, default=None):
    v = d.get(k, default)
    if v in (None, ""):
        if default is None:
            raise ValueError(f"{k} is required")
        v = default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{k} must be a number")
    if v != v or not lo <= v <= hi:
        raise ValueError(f"{k} must be between {lo:g} and {hi:g}")
    return v


def _text(d, k, mx, required=False):
    v = d.get(k)
    v = "" if v is None else str(v).strip()
    if required and not v:
        raise ValueError(f"{k} is required")
    if len(v) > mx:
        raise ValueError(f"{k} must be at most {mx} characters")
    return v


def _dur(v, lo_ms, hi_ms, what, allow_none=False):
    import tester
    v = str(v).strip().lower()
    if allow_none and v == "none":
        return v
    try:
        ms = tester.parse_dur(v)
    except Exception:
        raise ValueError(f"{what} must look like 24h, 72h or 3d")
    if not lo_ms <= ms <= hi_ms:
        raise ValueError(f"{what} is out of range")
    return v


def validate_spec(d):
    """Clean a saved-strategy body. Returns (name, description, spec)."""
    import strategies as R
    import strategy_compare as SC
    import tester
    if not isinstance(d, dict):
        raise ValueError("send a JSON object")
    name = _text(d, "name", NAME_MAX, required=True)
    desc = _text(d, "description", TEXT_MAX)
    typ = str(d.get("setup_type") or "similar")
    if typ not in SC.TYPES:
        raise ValueError("setup_type must be one of " + ", ".join(SC.TYPES))
    rule = SC.TYPES[typ].get("rule")
    params = R.clean_params(rule, d.get("rule_params") or {}) if rule else {}
    side = str(d.get("side") or "").lower()
    if side not in ("long", "short"):
        raise ValueError("side must be long or short")
    markets = d.get("markets")
    if isinstance(markets, str):
        markets = [markets]
    if not isinstance(markets, list) or not 1 <= len(markets) <= 5:
        raise ValueError("markets must list 1 to 5 markets")
    markets = [str(m).strip()[:40] for m in markets if str(m).strip()]
    if not markets:
        raise ValueError("markets must list 1 to 5 markets")
    tf = str(d.get("tf") or "1h").lower()
    if tf not in tester.SETUP_TFS:
        raise ValueError("tf must be 5m, 15m, 30m, 1h, 4h or 1d")
    htf = str(d.get("htf_confirm") or "off").lower()
    if htf not in ("off",) + tester.HTF_CHOICES:
        raise ValueError("htf_confirm must be off, 1h, 4h or 1d")
    if htf != "off" and tester.STEP[htf] <= tester.STEP[tf]:
        raise ValueError("the higher-timeframe filter must be above the setup timeframe")
    unit = str(d.get("slippage_unit") or "bps").lower()
    if unit not in ("bps", "ticks"):
        raise ValueError("slippage_unit must be bps or ticks")
    spec = dict(
        setup_type=typ, rule_params=params, pattern_label=_text(d, "pattern_label", LABEL_MAX),
        side=side, markets=markets, tf=tf, htf_confirm=htf, trend_filter=bool(d.get("trend_filter")),
        entry_offset_pct=round(_num(d, "entry_offset_pct", -20, 20, 0), 4),
        stop_pct=round(_num(d, "stop_pct", 0.01, 50), 4),
        rr=round(_num(d, "rr", 0.2, 20), 4),
        expiry=_dur(d.get("expiry") or "72h", 3600_000, 14 * 86400_000, "expiry"),
        max_hold=_dur(d.get("max_hold") or "7d", 3600_000, 30 * 86400_000, "time stop", allow_none=True),
        days=int(_num(d, "days", 30, 90, 90)),
        slippage=_num(d, "slippage", 0, 100 if unit == "bps" else 1000, 1 if unit == "bps" else 0),
        slippage_unit=unit, allow_overlap=bool(d.get("allow_overlap")),
        entry_conditions=_text(d, "entry_conditions", TEXT_MAX),
        exit_conditions=_text(d, "exit_conditions", TEXT_MAX),
    )
    if spec["allow_overlap"] and typ.startswith("zone_"):
        raise ValueError("overlapping trades are not available for zone strategies")
    return name, desc, spec


# ---------------------------------------------------------------- helpers
def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(v):
    v = auth._aware(v)
    return v.isoformat() if v else None


def _guard(write=True):
    """(user, None) or (None, error response). All library endpoints need sign-in and the CSRF token."""
    u = auth.current_user()
    if not u:
        return None, auth._err("Sign in to save strategies to your library.", 401, login_required=True)
    if not auth.csrf_ok():
        return None, auth._err("Your session expired. Refresh the page and try again.", 403, csrf=True)
    return u, None


def _row(c, uid, sid):
    r = c.execute(select(strategies).where(strategies.c.id == sid, strategies.c.user_id == uid)).mappings().first()
    return dict(r) if r else None


def _public(r, runs=None, last=None):
    out = dict(id=r["id"], name=r["name"], description=r["description"], spec=json.loads(r["spec"]),
               created_at=_iso(r["created_at"]), updated_at=_iso(r["updated_at"]))
    if runs is not None:
        out["runs"] = runs
    if last is not None:
        out["last_run"] = last
    return out


def _run_public(r):
    return dict(id=r["id"], strategy_id=r["strategy_id"], created_at=_iso(r["created_at"]), **json.loads(r["snapshot"]))


def _nf():
    return auth._err("Strategy not found.", 404)


def _body():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else None


def owned_strategy(uid, sid):
    with auth.engine().connect() as c:
        return _row(c, uid, sid)


def delete_user_data(c, uid):
    """Called inside the account-delete transaction (SQLite does not enforce ON DELETE CASCADE)."""
    c.execute(delete(strategy_runs).where(strategy_runs.c.user_id == uid))
    c.execute(delete(strategies).where(strategies.c.user_id == uid))


# ---------------------------------------------------------------- snapshots
def snapshot_from_output(out, req):
    """Key metrics + parameters + provenance from one engine result. No candles, no trade list."""
    s = out.get("result") or {}
    st = out.get("stats") or {}
    net = st.get("net") or {}
    e = out.get("edge") or {}
    an = out.get("analytics") or {}
    data = out.get("data") or {}
    p = out.get("params") or {}
    boot = an.get("bootstrap") or {}
    mc = an.get("monte_carlo") or {}
    prov = data.get("provenance") or {}
    return dict(
        market=(out.get("market") or {}).get("market_id"), side=(out.get("setup") or {}).get("side"),
        metrics=dict(closed=s.get("closed"), win_rate=net.get("win_rate"), expectancy_r=net.get("expectancy_r"),
                     profit_factor=net.get("profit_factor"), profit_factor_infinite=net.get("profit_factor_infinite"),
                     total_r=net.get("total_r"), max_drawdown_r=net.get("max_drawdown_r"), t_stat=s.get("t_stat"),
                     setups=st.get("setups_identified"), verdict=s.get("verdict"),
                     edge_score=e.get("score"), edge_band=e.get("band"), edge_label=e.get("label"),
                     expectancy_range=boot.get("expectancy"), mc_drawdown_p95=mc.get("p95")),
        params=dict(mode=p.get("mode"), setup_tf=p.get("setup_tf"), interval=p.get("interval"),
                    htf_confirm=p.get("htf_confirm"), trend_filter=p.get("trend_filter"), expiry=p.get("expiry"),
                    max_hold=p.get("max_hold"), days=p.get("days"), rule=p.get("rule"),
                    rule_params=p.get("rule_params"), entry_style=p.get("entry_style"),
                    entry=req.get("entry"), tp=req.get("tp"), sl=req.get("sl"),
                    rr=(out.get("setup") or {}).get("rr"), slippage=(out.get("costs") or {}).get("slippage"),
                    fees=out.get("fees")),
        provenance=dict(from_ms=data.get("from_ms"), to_ms=data.get("to_ms"), days=data.get("days"),
                        bars=data.get("bars"), text=data.get("provenance_text"),
                        sources=sorted({sg.get("source") for sg in prov.get("segments") or [] if sg.get("source")})),
        engine_generated=out.get("generated"))


def save_run(uid, sid, out, req):
    snap = snapshot_from_output(out, req)
    with auth.engine().begin() as c:
        if not _row(c, uid, sid):
            return None
        rid = c.execute(insert(strategy_runs).values(strategy_id=sid, user_id=uid, created_at=_now(),
                                                     snapshot=json.dumps(snap))).inserted_primary_key[0]
        ids = [r[0] for r in c.execute(select(strategy_runs.c.id).where(strategy_runs.c.strategy_id == sid)
                                       .order_by(strategy_runs.c.id.desc()))]
        if len(ids) > MAX_RUNS:
            c.execute(delete(strategy_runs).where(strategy_runs.c.id.in_(ids[MAX_RUNS:])))
        c.execute(update(strategies).where(strategies.c.id == sid).values(updated_at=_now()))
    return rid


# ---------------------------------------------------------------- routes
@bp.get("/api/library/strategies")
def list_strategies():
    u, bad = _guard(write=False)
    if bad:
        return bad
    with auth.engine().connect() as c:
        rows = [dict(r) for r in c.execute(select(strategies).where(strategies.c.user_id == u["id"])
                                           .order_by(strategies.c.updated_at.desc(), strategies.c.id.desc())).mappings()]
        counts = dict(c.execute(select(strategy_runs.c.strategy_id, func.count()).where(strategy_runs.c.user_id == u["id"])
                                .group_by(strategy_runs.c.strategy_id)).all())
        lasts = {}
        sub = (select(strategy_runs.c.strategy_id, func.max(strategy_runs.c.id).label("mid"))
               .where(strategy_runs.c.user_id == u["id"]).group_by(strategy_runs.c.strategy_id).subquery())
        for r in c.execute(select(strategy_runs).join(sub, strategy_runs.c.id == sub.c.mid)).mappings():
            lasts[r["strategy_id"]] = _run_public(r)
    items = [_public(r, runs=counts.get(r["id"], 0), last=lasts.get(r["id"]) or {}) for r in rows]
    resp = jsonify(strategies=items, limit=MAX_STRATEGIES, run_limit=MAX_RUNS)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _count(c, uid):
    return c.execute(select(func.count()).select_from(strategies).where(strategies.c.user_id == uid)).scalar_one()


@bp.post("/api/library/strategies")
def create_strategy():
    u, bad = _guard()
    if bad:
        return bad
    try:
        name, desc, spec = validate_spec(_body())
    except ValueError as e:
        return auth._err(str(e), 400)
    now = _now()
    with auth.engine().begin() as c:
        if _count(c, u["id"]) >= MAX_STRATEGIES:
            return auth._err(f"Your library is full ({MAX_STRATEGIES} strategies). Delete one to save another.", 409)
        sid = c.execute(insert(strategies).values(user_id=u["id"], name=name, description=desc, spec=json.dumps(spec),
                                                  created_at=now, updated_at=now)).inserted_primary_key[0]
        r = _row(c, u["id"], sid)
    return jsonify(strategy=_public(r, runs=0)), 201


@bp.post("/api/library/import")
def import_strategies():
    """Move device-saved strategies into the account (optional, user-triggered). Up to 6 at a time."""
    u, bad = _guard()
    if bad:
        return bad
    d = _body() or {}
    items = d.get("strategies")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_IMPORT:
        return auth._err(f"send 1 to {MAX_IMPORT} strategies", 400)
    try:
        clean = [validate_spec(x) for x in items]
    except ValueError as e:
        return auth._err(str(e), 400)
    now = _now()
    with auth.engine().begin() as c:
        if _count(c, u["id"]) + len(clean) > MAX_STRATEGIES:
            return auth._err(f"That would go over the {MAX_STRATEGIES}-strategy limit.", 409)
        ids = [c.execute(insert(strategies).values(user_id=u["id"], name=n, description=ds, spec=json.dumps(sp),
                                                   created_at=now, updated_at=now)).inserted_primary_key[0]
               for n, ds, sp in clean]
    return jsonify(imported=len(ids), ids=ids), 201


@bp.get("/api/library/strategies/<int:sid>")
def get_strategy(sid):
    u, bad = _guard(write=False)
    if bad:
        return bad
    with auth.engine().connect() as c:
        r = _row(c, u["id"], sid)
        if not r:
            return _nf()
        n = c.execute(select(func.count()).select_from(strategy_runs).where(strategy_runs.c.strategy_id == sid)).scalar_one()
    return jsonify(strategy=_public(r, runs=n))


@bp.put("/api/library/strategies/<int:sid>")
def update_strategy(sid):
    u, bad = _guard()
    if bad:
        return bad
    try:
        name, desc, spec = validate_spec(_body())
    except ValueError as e:
        return auth._err(str(e), 400)
    with auth.engine().begin() as c:
        if not _row(c, u["id"], sid):
            return _nf()
        c.execute(update(strategies).where(strategies.c.id == sid, strategies.c.user_id == u["id"])
                  .values(name=name, description=desc, spec=json.dumps(spec), updated_at=_now()))
        r = _row(c, u["id"], sid)
    return jsonify(strategy=_public(r))


@bp.post("/api/library/strategies/<int:sid>/duplicate")
def duplicate_strategy(sid):
    u, bad = _guard()
    if bad:
        return bad
    now = _now()
    with auth.engine().begin() as c:
        r = _row(c, u["id"], sid)
        if not r:
            return _nf()
        if _count(c, u["id"]) >= MAX_STRATEGIES:
            return auth._err(f"Your library is full ({MAX_STRATEGIES} strategies). Delete one to save another.", 409)
        name = (r["name"] + " (copy)")[:NAME_MAX]
        nid = c.execute(insert(strategies).values(user_id=u["id"], name=name, description=r["description"],
                                                  spec=r["spec"], created_at=now, updated_at=now)).inserted_primary_key[0]
        nr = _row(c, u["id"], nid)
    return jsonify(strategy=_public(nr, runs=0)), 201


@bp.delete("/api/library/strategies/<int:sid>")
def delete_strategy(sid):
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().begin() as c:
        if not _row(c, u["id"], sid):
            return _nf()
        c.execute(delete(strategy_runs).where(strategy_runs.c.strategy_id == sid, strategy_runs.c.user_id == u["id"]))
        c.execute(delete(strategies).where(strategies.c.id == sid, strategies.c.user_id == u["id"]))
    return jsonify(ok=True, deleted=sid)


@bp.get("/api/library/strategies/<int:sid>/runs")
def list_runs(sid):
    u, bad = _guard(write=False)
    if bad:
        return bad
    with auth.engine().connect() as c:
        if not _row(c, u["id"], sid):
            return _nf()
        rows = [_run_public(r) for r in c.execute(
            select(strategy_runs).where(strategy_runs.c.strategy_id == sid, strategy_runs.c.user_id == u["id"])
            .order_by(strategy_runs.c.id.desc()).limit(MAX_RUNS)).mappings()]
    resp = jsonify(runs=rows, limit=MAX_RUNS)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.delete("/api/library/runs/<int:rid>")
def delete_run(rid):
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().begin() as c:
        n = c.execute(delete(strategy_runs).where(strategy_runs.c.id == rid, strategy_runs.c.user_id == u["id"])).rowcount
    if not n:
        return auth._err("Run not found.", 404)
    return jsonify(ok=True, deleted=rid)
