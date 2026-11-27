"""CHRONOS Trading Journal: the trader's own real trades (signed-in users only).

Table (metadata.create_all only adds missing tables):
  journal_trades  one trade per row, entered by the user

Behaviour observations are rule-based and evidence-gated: each one needs a minimum number of the user's own records
and always shows the counts it is based on. Nothing is inferred beyond what the records contain.
Screenshots are not stored (no file storage yet); an https link to a chart image can be saved instead.
"""
import csv
import datetime as dt
import io
import math
from collections import defaultdict

from flask import Blueprint, Response, jsonify, request
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Table, Text, delete, func, insert, select, update

import auth

MAX_TRADES = 2000
SESSIONS = ("Asia", "London", "New York", "Late US")
EXITS = ("tp", "sl", "manual", "breakeven", "trailing", "time", "other")
MIN_OBS = 10          # default minimum sample for an observation
MIN_GROUP = 5         # minimum trades in each group compared

journal_trades = Table(
    "journal_trades", auth.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("market", String(40), nullable=False),
    Column("side", String(8), nullable=False),
    Column("entry", Float, nullable=False),
    Column("sl", Float, nullable=True),
    Column("tp", Float, nullable=True),
    Column("exit_price", Float, nullable=True),
    Column("size", Float, nullable=True),
    Column("fees", Float, nullable=True),
    Column("pnl", Float, nullable=True),
    Column("risk_amount", Float, nullable=True),
    Column("strategy_id", Integer, nullable=True),
    Column("strategy_name", String(80), nullable=False, default=""),
    Column("opened_at", DateTime(timezone=True), nullable=False),
    Column("closed_at", DateTime(timezone=True), nullable=True),
    Column("session", String(16), nullable=False, default=""),
    Column("exit_reason", String(16), nullable=False, default=""),
    Column("sl_moved", Boolean, nullable=False, default=False),
    Column("notes", Text, nullable=False, default=""),
    Column("tags", String(200), nullable=False, default=""),
    Column("chart_url", String(500), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

bp = Blueprint("journal", __name__)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(v):
    v = auth._aware(v)
    return v.isoformat() if v else None


def _guard():
    u = auth.current_user()
    if not u:
        return None, auth._err("Sign in to use your trading journal.", 401, login_required=True)
    if request.method not in ("GET", "HEAD") and not auth.csrf_ok():  # reads need sign-in only, writes need CSRF too
        return None, auth._err("Your session expired. Refresh the page and try again.", 403, csrf=True)
    return u, None


def _nf():
    return auth._err("Journal entry not found.", 404)


def session_of(ms):
    """Same UTC session buckets as the backtester (metrics.session_of)."""
    h = (ms // 3_600_000) % 24
    return "Asia" if h < 7 else "London" if h < 13 else "New York" if h < 21 else "Late US"


def _num(d, k, lo=0.0, hi=1e12, required=False, allow_neg=False):
    v = d.get(k)
    if v in (None, ""):
        if required:
            raise ValueError(f"{k} is required")
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{k} must be a number")
    if v != v or v in (math.inf, -math.inf):
        raise ValueError(f"{k} must be a number")
    if not allow_neg and v < lo or v > hi or (allow_neg and v < -hi):
        raise ValueError(f"{k} is out of range")
    return v


def _when(d, k, required=False):
    v = d.get(k)
    if v in (None, ""):
        if required:
            raise ValueError(f"{k} is required")
        return None
    try:
        if isinstance(v, (int, float)):
            t = dt.datetime.fromtimestamp(float(v) / 1000, dt.timezone.utc)
        else:
            t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            if t.tzinfo is None:
                raise ValueError
    except (ValueError, OverflowError, OSError):
        raise ValueError(f"{k} must be a date and time with a time zone")
    if not dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc) <= t <= _now() + dt.timedelta(days=1):
        raise ValueError(f"{k} is out of range")
    return t.astimezone(dt.timezone.utc)


def validate(d, uid):
    if not isinstance(d, dict):
        raise ValueError("send a JSON object")
    market = str(d.get("market") or "").strip()[:40]
    if not market:
        raise ValueError("market is required")
    side = str(d.get("side") or "").lower()
    if side not in ("long", "short"):
        raise ValueError("side must be long or short")
    entry = _num(d, "entry", required=True)
    if entry <= 0:
        raise ValueError("entry must be above 0")
    sl, tp, ex = _num(d, "sl"), _num(d, "tp"), _num(d, "exit_price")
    if sl is not None:
        if (side == "long" and sl >= entry) or (side == "short" and sl <= entry):
            raise ValueError("the stop-loss must be below the entry for a long and above it for a short")
    if tp is not None:
        if (side == "long" and tp <= entry) or (side == "short" and tp >= entry):
            raise ValueError("the take-profit must be above the entry for a long and below it for a short")
    size, fees = _num(d, "size"), _num(d, "fees")
    pnl = _num(d, "pnl", allow_neg=True)
    opened = _when(d, "opened_at", required=True)
    closed = _when(d, "closed_at")
    if closed and closed < opened:
        raise ValueError("closed_at must be after opened_at")
    if ex is not None and pnl is None and size:
        sgn = 1 if side == "long" else -1
        pnl = round(sgn * (ex - entry) * size - (fees or 0), 2)
    risk_amount = round(abs(entry - sl) * size, 2) if (sl is not None and size) else None
    sess = str(d.get("session") or "").strip()
    if sess and sess not in SESSIONS:
        raise ValueError("session must be one of " + ", ".join(SESSIONS))
    if not sess:
        sess = session_of(int(opened.timestamp() * 1000))
    er = str(d.get("exit_reason") or "").lower()
    if er and er not in EXITS:
        raise ValueError("exit_reason must be one of " + ", ".join(EXITS))
    url = str(d.get("chart_url") or "").strip()
    if url and (not url.startswith("https://") or len(url) > 500 or any(ch in url for ch in "<>\"' ")):
        raise ValueError("chart link must be an https:// address")
    sid, sname = d.get("strategy_id"), str(d.get("strategy_name") or "").strip()[:80]
    if sid not in (None, ""):
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            raise ValueError("strategy_id must be a number")
        import library
        s = library.owned_strategy(uid, sid)
        if not s:
            raise ValueError("strategy not found in your library")
        sname = s["name"]
    else:
        sid = None
    tags = ",".join(t.strip()[:30] for t in str(d.get("tags") or "").split(",") if t.strip())[:200]
    return dict(market=market, side=side, entry=entry, sl=sl, tp=tp, exit_price=ex, size=size, fees=fees, pnl=pnl,
                risk_amount=risk_amount, strategy_id=sid, strategy_name=sname, opened_at=opened, closed_at=closed,
                session=sess, exit_reason=er, sl_moved=bool(d.get("sl_moved")), notes=str(d.get("notes") or "")[:4000],
                tags=tags, chart_url=url)


def derived(r):
    """R-multiple (from the initial stop), planned R:R and outcome for one row."""
    e, sl, tp, ex = r["entry"], r["sl"], r["tp"], r["exit_price"]
    risk = abs(e - sl) if sl is not None else None
    sgn = 1 if r["side"] == "long" else -1
    planned = round(abs(tp - e) / risk, 2) if (risk and tp is not None) else None
    rm = None
    if risk and ex is not None:
        rm = sgn * (ex - e) / risk
        if r.get("fees") and r.get("size"):
            rm -= r["fees"] / (risk * r["size"])
        rm = round(rm, 3)
    elif r.get("pnl") is not None and r.get("risk_amount"):
        rm = round(r["pnl"] / r["risk_amount"], 3)
    val = rm if rm is not None else r.get("pnl")
    outcome = None if val is None else "win" if val > 0 else "loss" if val < 0 else "breakeven"
    return dict(r_multiple=rm, planned_rr=planned, outcome=outcome, closed=ex is not None or r.get("pnl") is not None)


def public(r):
    out = {k: r[k] for k in ("id", "market", "side", "entry", "sl", "tp", "exit_price", "size", "fees", "pnl", "risk_amount",
                             "strategy_id", "strategy_name", "session", "exit_reason", "sl_moved", "notes", "tags", "chart_url")}
    out.update(opened_at=_iso(r["opened_at"]), closed_at=_iso(r["closed_at"]), created_at=_iso(r["created_at"]),
               updated_at=_iso(r["updated_at"]), **derived(r))
    return out


def delete_user_data(c, uid):
    c.execute(delete(journal_trades).where(journal_trades.c.user_id == uid))


def _rows(c, uid):
    return [dict(r) for r in c.execute(select(journal_trades).where(journal_trades.c.user_id == uid)
                                       .order_by(journal_trades.c.opened_at.desc(), journal_trades.c.id.desc())).mappings()]


def apply_filters(items, a):
    q = (a.get("q") or "").strip().lower()
    out = []
    lo = a.get("from")
    hi = a.get("to")
    for t in items:
        if q and q not in " ".join(str(t.get(k) or "") for k in ("market", "strategy_name", "notes", "tags")).lower():
            continue
        if a.get("market") and t["market"].lower() != a["market"].lower():
            continue
        if a.get("strategy") and (t["strategy_name"] or "").lower() != a["strategy"].lower():
            continue
        if a.get("side") and t["side"] != a["side"]:
            continue
        if a.get("session") and t["session"] != a["session"]:
            continue
        if a.get("outcome") and t["outcome"] != a["outcome"]:
            continue
        if lo and t["opened_at"][:10] < lo:
            continue
        if hi and t["opened_at"][:10] > hi:
            continue
        out.append(t)
    return out


def _blk(ts):
    rs = [t["r_multiple"] for t in ts if t["r_multiple"] is not None]
    pn = [t["pnl"] for t in ts if t["pnl"] is not None]
    dec = [t for t in ts if t["outcome"] in ("win", "loss", "breakeven")]
    w = sum(1 for t in dec if t["outcome"] == "win")
    gw, gl = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    return dict(trades=len(ts), closed=len(dec), wins=w, losses=sum(1 for t in dec if t["outcome"] == "loss"),
                win_rate=round(w / len(dec), 3) if dec else None, with_r=len(rs), net_r=round(sum(rs), 2),
                avg_r=round(sum(rs) / len(rs), 3) if rs else None, profit_factor=round(gw / gl, 2) if gl > 0 else None,
                pnl=round(sum(pn), 2) if pn else None, best_r=max(rs) if rs else None, worst_r=min(rs) if rs else None)


def report(items, period, ref, tz):
    """Weekly or monthly report for the period containing date `ref` (viewer time zone)."""
    if period == "week":
        start = ref - dt.timedelta(days=ref.weekday())
        end = start + dt.timedelta(days=7)
        prev = (start - dt.timedelta(days=7), start)
    else:
        start = ref.replace(day=1)
        end = (start + dt.timedelta(days=32)).replace(day=1)
        prev = ((start - dt.timedelta(days=1)).replace(day=1), start)

    def inside(t, a, b):
        d = dt.datetime.fromisoformat(t["opened_at"]).astimezone(tz).date()
        return a <= d < b
    cur = [t for t in items if inside(t, start, end)]
    pv = [t for t in items if inside(t, *prev)]
    groups = lambda key: {k: _blk(v) for k, v in _group(cur, key).items()}
    return dict(period=period, start=start.isoformat(), end=(end - dt.timedelta(days=1)).isoformat(), summary=_blk(cur),
                previous=dict(start=prev[0].isoformat(), **_blk(pv)), by_strategy=groups(lambda t: t["strategy_name"] or "No strategy"),
                by_session=groups(lambda t: t["session"]), by_market=groups(lambda t: t["market"]),
                observations=observations(cur)["items"])


def _group(ts, key):
    g = defaultdict(list)
    for t in ts:
        g[key(t)].append(t)
    return g


def observations(items):
    """Evidence-gated observations from the user's own closed trades. Each item has text, counts and a basis."""
    closed = [t for t in items if t["outcome"]]
    obs, skipped = [], []

    def need(name, have, n):
        skipped.append(dict(check=name, have=have, need=n))

    # 1. planned vs realised R:R on winners that had a planned target
    pr = [t for t in closed if t["planned_rr"] is not None and t["r_multiple"] is not None]
    wins = [t for t in pr if t["r_multiple"] > 0]
    if len(pr) >= MIN_OBS and len(wins) >= MIN_GROUP:
        p = sum(t["planned_rr"] for t in pr) / len(pr)
        a = sum(t["r_multiple"] for t in wins) / len(wins)
        txt = (f"Across {len(pr)} trades with a stop and target, your planned average R:R was 1:{p:.2f}. "
               f"Your {len(wins)} winning trades realised 1:{a:.2f} on average.")
        if a < p * 0.8:
            txt += " Winners are closing well short of the planned target."
        obs.append(dict(id="planned_vs_actual", text=txt, counts=dict(trades=len(pr), winners=len(wins)),
                        planned_rr=round(p, 2), realised_win_rr=round(a, 2)))
    else:
        need("Planned vs realised R:R", len(pr), MIN_OBS)
    # 2. manual exits before the target
    tgt = [t for t in closed if t["tp"] is not None and t["exit_reason"]]
    early = [t for t in tgt if t["exit_reason"] == "manual" and t["r_multiple"] is not None and 0 <= t["r_multiple"] < (t["planned_rr"] or 0)]
    if len(tgt) >= MIN_OBS:
        if early:
            obs.append(dict(id="early_exits", text=f"{len(early)} of {len(tgt)} trades with a target and exit reason were closed by hand "
                            f"in profit before reaching the target.", counts=dict(early=len(early), trades=len(tgt))))
    else:
        need("Early manual exits", len(tgt), MIN_OBS)
    # 3. stop moved vs not
    mv = [t for t in closed if t["sl_moved"] and t["r_multiple"] is not None]
    st = [t for t in closed if not t["sl_moved"] and t["r_multiple"] is not None]
    if len(mv) >= MIN_GROUP and len(st) >= MIN_GROUP:
        am, ast_ = _blk(mv)["avg_r"], _blk(st)["avg_r"]
        obs.append(dict(id="stop_moves", text=f"You moved the stop on {len(mv)} of {len(mv) + len(st)} trades. Average result "
                        f"{am:+.2f}R when moved vs {ast_:+.2f}R when left alone.", counts=dict(moved=len(mv), not_moved=len(st))))
    else:
        need("Stop-loss adjustments", min(len(mv), len(st)), MIN_GROUP)
    # 4. overtrading: busiest days vs other days
    days = _group([t for t in closed if t["r_multiple"] is not None], lambda t: t["opened_at"][:10])
    if len(days) >= MIN_OBS:
        counts = sorted(len(v) for v in days.values())
        med = counts[len(counts) // 2]
        busy = [t for d, v in days.items() if len(v) > max(2, med * 2) for t in v]
        if len(busy) >= MIN_GROUP:
            rest = [t for d, v in days.items() if len(v) <= max(2, med * 2) for t in v]
            b, r = _blk(busy), _blk(rest)
            obs.append(dict(id="overtrading", text=f"On days with more than {max(2, med * 2)} trades ({b['trades']} trades) your average was "
                            f"{b['avg_r']:+.2f}R, vs {r['avg_r']:+.2f}R on other days ({r['trades']} trades).",
                            counts=dict(busy_trades=b["trades"], other_trades=r["trades"], days=len(days))))
    else:
        need("Overtrading days", len(days), MIN_OBS)
    # 5. risk consistency
    ra = [t["risk_amount"] for t in closed if t["risk_amount"]]
    if len(ra) >= MIN_OBS:
        mean = sum(ra) / len(ra)
        sd = (sum((x - mean) ** 2 for x in ra) / (len(ra) - 1)) ** 0.5
        big = sum(1 for x in ra if x > 2 * mean)
        cv = sd / mean if mean else 0
        txt = f"Risk per trade averaged {mean:,.2f} across {len(ra)} trades with a stop and size (variation {cv:.0%})."
        if big:
            txt += f" {big} trade{'s' if big != 1 else ''} risked more than twice your average."
        obs.append(dict(id="risk_consistency", text=txt, counts=dict(trades=len(ra), over_2x=big), cv=round(cv, 3)))
    else:
        need("Risk consistency", len(ra), MIN_OBS)
    # 6. sessions
    sg = {k: v for k, v in _group([t for t in closed if t["r_multiple"] is not None], lambda t: t["session"]).items() if len(v) >= MIN_GROUP}
    if len(sg) >= 2:
        parts = sorted(((k, _blk(v)) for k, v in sg.items()), key=lambda x: -(x[1]["avg_r"] or 0))
        obs.append(dict(id="sessions", text="By session: " + "; ".join(f"{k} {b['avg_r']:+.2f}R avg over {b['trades']} trades" for k, b in parts) + ".",
                        counts={k: b["trades"] for k, b in parts}))
    else:
        need("Session differences", sum(len(v) for v in sg.values()), MIN_GROUP * 2)
    # 7. strategy with the most losses
    sl_ = _group([t for t in closed if t["outcome"] == "loss"], lambda t: t["strategy_name"] or "No strategy")
    if sum(len(v) for v in sl_.values()) >= MIN_GROUP and len(sl_) >= 2:
        k, v = max(sl_.items(), key=lambda kv: len(kv[1]))
        tot = sum(1 for t in closed if (t["strategy_name"] or "No strategy") == k)
        obs.append(dict(id="losing_strategy", text=f"Most losses came from {k}: {len(v)} of your {sum(len(x) for x in sl_.values())} losing trades "
                        f"({len(v)} losses in {tot} trades with it).", counts=dict(losses=len(v), trades=tot)))
    else:
        if len(sl_) < 2 and sum(len(v) for v in sl_.values()) >= MIN_GROUP:
            skipped.append(dict(check="Strategy with the most losses (needs losing trades tagged with 2 or more strategies)",
                                have=len(sl_), need=2))
        else:
            need("Strategy with the most losses", sum(len(v) for v in sl_.values()), MIN_GROUP)
    # 8. trades soon after a loss
    P = dt.datetime.fromisoformat
    seq = sorted([t for t in closed if t["r_multiple"] is not None and t["closed_at"]], key=lambda t: P(t["opened_at"]))
    after = []
    for i, t in enumerate(seq):
        prev = [p for p in seq[:i] if P(p["closed_at"]) <= P(t["opened_at"])]
        if prev:
            p = max(prev, key=lambda x: P(x["closed_at"]))
            gap = (P(t["opened_at"]) - P(p["closed_at"])).total_seconds()
            if p["outcome"] == "loss" and gap <= 3600:
                after.append(t)
    if len(after) >= MIN_GROUP:
        a, r = _blk(after), _blk([t for t in seq if t not in after])
        obs.append(dict(id="after_loss", text=f"{a['trades']} trades were opened within an hour of a losing trade closing. They averaged "
                        f"{a['avg_r']:+.2f}R vs {r['avg_r']:+.2f}R for your other trades.", counts=dict(after_loss=a["trades"], other=r["trades"])))
    else:
        need("Trades soon after a loss", len(after), MIN_GROUP)
    small = len(closed) < 30
    return dict(items=obs, not_enough_data=skipped, closed_trades=len(closed), small_sample=small,
                note=("Fewer than 30 closed trades: treat these as early signs, not conclusions. " if small else "") +
                     "Observations are calculated from your own entries only; they describe the past and do not predict results.")


def _items(c, uid):
    return [public(r) for r in _rows(c, uid)]


@bp.get("/api/journal/trades")
def list_trades():
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().connect() as c:
        items = _items(c, u["id"])
    f = apply_filters(items, request.args)
    return jsonify(trades=f, total=len(items), shown=len(f), summary=_blk(f), limit=MAX_TRADES,
                   markets=sorted({t["market"] for t in items}), strategies=sorted({t["strategy_name"] for t in items if t["strategy_name"]}))


@bp.post("/api/journal/trades")
def create_trade():
    u, bad = _guard()
    if bad:
        return bad
    try:
        v = validate(request.get_json(silent=True), u["id"])
    except ValueError as e:
        return auth._err(str(e), 400)
    now = _now()
    with auth.engine().begin() as c:
        n = c.execute(select(func.count()).select_from(journal_trades).where(journal_trades.c.user_id == u["id"])).scalar_one()
        if n >= MAX_TRADES:
            return auth._err(f"Your journal is full ({MAX_TRADES} trades). Export and delete older entries to add more.", 409)
        res = c.execute(insert(journal_trades).values(user_id=u["id"], created_at=now, updated_at=now, **v))
        r = c.execute(select(journal_trades).where(journal_trades.c.id == res.inserted_primary_key[0])).mappings().first()
        return jsonify(trade=public(dict(r))), 201


def _own(c, uid, tid):
    r = c.execute(select(journal_trades).where(journal_trades.c.id == tid, journal_trades.c.user_id == uid)).mappings().first()
    return dict(r) if r else None


@bp.get("/api/journal/trades/<int:tid>")
def get_trade(tid):
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().connect() as c:
        r = _own(c, u["id"], tid)
        return jsonify(trade=public(r)) if r else _nf()


@bp.put("/api/journal/trades/<int:tid>")
def update_trade(tid):
    u, bad = _guard()
    if bad:
        return bad
    try:
        v = validate(request.get_json(silent=True), u["id"])
    except ValueError as e:
        return auth._err(str(e), 400)
    with auth.engine().begin() as c:
        if not _own(c, u["id"], tid):
            return _nf()
        c.execute(update(journal_trades).where(journal_trades.c.id == tid).values(updated_at=_now(), **v))
        return jsonify(trade=public(_own(c, u["id"], tid)))


@bp.delete("/api/journal/trades/<int:tid>")
def delete_trade(tid):
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().begin() as c:
        if not _own(c, u["id"], tid):
            return _nf()
        c.execute(delete(journal_trades).where(journal_trades.c.id == tid))
        return jsonify(ok=True)


def _tz():
    try:
        m = int(request.args.get("tz", "0"))
    except ValueError:
        m = 0
    return dt.timezone(dt.timedelta(minutes=max(-840, min(840, m))))


@bp.get("/api/journal/report")
def get_report():
    u, bad = _guard()
    if bad:
        return bad
    period = request.args.get("period", "week")
    if period not in ("week", "month"):
        return auth._err("period must be week or month", 400)
    tz = _tz()
    try:
        ref = dt.date.fromisoformat(request.args["date"]) if request.args.get("date") else _now().astimezone(tz).date()
    except ValueError:
        return auth._err("date must look like 2026-11-27", 400)
    with auth.engine().connect() as c:
        items = _items(c, u["id"])
    return jsonify(report=report(items, period, ref, tz))


@bp.get("/api/journal/calendar")
def calendar():
    u, bad = _guard()
    if bad:
        return bad
    tz = _tz()
    try:
        y, m = (int(x) for x in (request.args.get("month") or _now().astimezone(tz).strftime("%Y-%m")).split("-"))
        first = dt.date(y, m, 1)
    except (ValueError, TypeError):
        return auth._err("month must look like 2026-11", 400)
    nxt = (first + dt.timedelta(days=32)).replace(day=1)
    with auth.engine().connect() as c:
        items = _items(c, u["id"])
    days = defaultdict(list)
    for t in items:
        d = dt.datetime.fromisoformat(t["opened_at"]).astimezone(tz).date()
        if first <= d < nxt:
            days[d.isoformat()].append(t)
    return jsonify(month=first.strftime("%Y-%m"), days={k: _blk(v) for k, v in sorted(days.items())}, summary=_blk([t for v in days.values() for t in v]))


@bp.get("/api/journal/analysis")
def analysis():
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().connect() as c:
        items = _items(c, u["id"])
    f = apply_filters(items, request.args)
    return jsonify(analysis=observations(f), summary=_blk(f),
                   by_strategy={k: _blk(v) for k, v in _group(f, lambda t: t["strategy_name"] or "No strategy").items()},
                   by_session={k: _blk(v) for k, v in _group(f, lambda t: t["session"]).items()})


@bp.get("/api/journal/export.csv")
def export_csv():
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().connect() as c:
        items = apply_filters(_items(c, u["id"]), request.args)
    cols = ["id", "opened_at", "closed_at", "market", "side", "entry", "sl", "tp", "exit_price", "size", "fees", "pnl",
            "risk_amount", "r_multiple", "planned_rr", "outcome", "strategy_name", "session", "exit_reason", "sl_moved", "tags",
            "chart_url", "notes"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for t in items:
        row = []
        for k in cols:
            v = t.get(k)
            v = "" if v is None else str(v)
            if v[:1] in ("=", "+", "-", "@") and k in ("notes", "tags", "strategy_name", "market", "chart_url"):
                v = "'" + v  # spreadsheet formula injection guard
            row.append(v)
        w.writerow(row)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=chronos-journal.csv", "Cache-Control": "no-store"})
