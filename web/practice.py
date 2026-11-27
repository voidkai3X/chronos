"""CHRONOS Blind Replay: practise on random windows of real historical candles (signed-in users only).

The whole window is loaded once and stored server-side with the session. The client only ever receives candles
up to the replay cursor; future candles leave the server only after the session has ended. Orders are filled and
closed on the server, candle by candle, with the same conservative rules as the backtester:
  - a market order fills at the next candle's open; a limit order fills when price trades through its level
    (at the limit price, never better)
  - stop-loss and take-profit hit in the same candle count as the stop-loss
  - on the candle an order fills, a take-profit touch is not counted (the order inside the candle is unknown)
  - a gap through the stop exits at the open; a gap through the target exits at the target price
  - MFP commission per fill and daily swap are deducted (as in the backtester); no slippage is modelled.

Tables (metadata.create_all only adds missing tables):
  practice_sessions  one replay window per row; candles, cursor, mode, virtual balance and rules
  practice_trades    orders, trades and skipped setups of a session
"""
import datetime as dt
import json
import math
import random

from flask import Blueprint, jsonify, request
from sqlalchemy import BigInteger, Column, DateTime, Float, ForeignKey, Integer, String, Table, Text, delete, func, insert, select, update

import auth

TFS = {"15m": ("15m", 1), "1h": ("1h", 1), "4h": ("1h", 4)}   # replay tf -> (load interval, candles per bar)
HISTORY_BARS = 150        # candles visible when a session starts
HIDDEN_BARS = 400         # candles that can be revealed (practice / strategy)
CHALLENGE_HIDDEN = 500
MIN_HIDDEN = 120          # fewer real candles than this in the chosen window: try another window
MAX_ADVANCE = 20
MAX_ACTIVE = 1            # starting a session ends the previous active one
KEEP_FINISHED_CANDLES = 20
START_BALANCE = 10_000.0
CHALLENGE = dict(start_balance=10_000.0, max_risk_pct=1.0, fail_drawdown_pct=5.0, pass_profit_pct=6.0, max_trades=10)
RNG = random.SystemRandom()

practice_sessions = Table(
    "practice_sessions", auth.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("mode", String(16), nullable=False),
    Column("market_id", String(64), nullable=False),
    Column("market_label", String(80), nullable=False, default=""),
    Column("asset_class", String(16), nullable=False, default="crypto"),
    Column("tf", String(8), nullable=False),
    Column("strategy_id", Integer, nullable=True),
    Column("bars", Text, nullable=False),
    Column("cursor", Integer, nullable=False),
    Column("start_idx", Integer, nullable=False),
    Column("n_bars", Integer, nullable=False),
    Column("sources", Text, nullable=False, default="[]"),
    Column("settings", Text, nullable=False, default="{}"),
    Column("start_balance", Float, nullable=False),
    Column("balance", Float, nullable=False),
    Column("status", String(16), nullable=False),          # active | finished | abandoned
    Column("result", String(16), nullable=True),           # challenge: passed | failed | incomplete
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
)
practice_trades = Table(
    "practice_trades", auth.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", Integer, ForeignKey("practice_sessions.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("kind", String(8), nullable=False),              # market | limit | skip
    Column("side", String(8), nullable=True),
    Column("entry", Float, nullable=True),
    Column("sl", Float, nullable=True),
    Column("tp", Float, nullable=True),
    Column("risk_pct", Float, nullable=True),
    Column("risk_amount", Float, nullable=True),
    Column("units", Float, nullable=True),
    Column("placed_idx", Integer, nullable=False),
    Column("placed_ms", BigInteger, nullable=False),
    Column("fill_idx", Integer, nullable=True),
    Column("fill_ms", BigInteger, nullable=True),
    Column("fill_price", Float, nullable=True),
    Column("exit_idx", Integer, nullable=True),
    Column("exit_ms", BigInteger, nullable=True),
    Column("exit_price", Float, nullable=True),
    Column("exit_reason", String(16), nullable=True),       # tp | sl | gap_sl | manual | end | cancelled
    Column("r_gross", Float, nullable=True),
    Column("r_net", Float, nullable=True),
    Column("pnl", Float, nullable=True),
    Column("status", String(12), nullable=False),           # pending | open | closed | cancelled | skipped
    Column("note", String(200), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("closed_at", DateTime(timezone=True), nullable=True),
)

bp = Blueprint("practice", __name__)
# app.py fills these in (rate limits, per-market lock, market resolver); tests may replace fetch_bars.
HOOKS = {}


MS_COLUMNS = ("placed_ms", "fill_ms", "exit_ms")


def widen_ms_columns(eng):
    """Postgres only: the first release created the millisecond-timestamp columns as INTEGER (32-bit),
    which cannot hold epoch milliseconds. Widen them to BIGINT in place. Widening keeps every value,
    touches no other table and is skipped once the columns are already BIGINT (or on SQLite, where
    INTEGER is already 64-bit)."""
    if eng.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    with eng.begin() as c:
        rows = c.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'practice_trades' AND data_type = 'integer' AND column_name IN ('placed_ms', 'fill_ms', 'exit_ms')"
        )).scalars().all()
        for col in rows:
            if col in MS_COLUMNS:
                c.execute(text(f'ALTER TABLE practice_trades ALTER COLUMN "{col}" TYPE BIGINT'))


auth.AFTER_CREATE.append(widen_ms_columns)


def fetch_bars(m, interval, from_ms, to_ms):
    """Real merged candles for a market (MFP first, OKX/Yahoo backfill). Returns (bars, provenance)."""
    import sources
    import tester
    return sources.load(m, interval, from_ms, to_ms, kind=tester.asset_class(m), use_backups=True)


# ---------------------------------------------------------------- helpers
def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(v):
    v = auth._aware(v)
    return v.isoformat() if v else None


def _guard():
    u = auth.current_user()
    if not u:
        return None, auth._err("Sign in to use Blind Replay. Sessions and stats are saved to your account.", 401, login_required=True)
    if request.method not in ("GET", "HEAD") and not auth.csrf_ok():  # reads need sign-in only, writes need CSRF too
        return None, auth._err("Your session expired. Refresh the page and try again.", 403, csrf=True)
    return u, None


def _body():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def _nf():
    return auth._err("Replay session not found.", 404)


def _num(d, k, lo, hi, default=None, required=False):
    v = d.get(k, default)
    if v in (None, ""):
        if required:
            raise ValueError(f"{k} is required")
        return default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{k} must be a number")
    if v != v or v in (math.inf, -math.inf) or not lo <= v <= hi:
        raise ValueError(f"{k} must be between {lo:g} and {hi:g}")
    return v


def build_window(raw, tf, step_ms):
    """Complete replay candles from the loaded candles: [t, o, h, l, c]. 4h candles are built from 1h candles and an
    incomplete group (missing its last hour) is dropped, never padded."""
    _, k = TFS[tf]
    rows = [[int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4])] for b in raw]
    if k == 1:
        return rows
    import tester
    out = []
    sub = step_ms // k
    for t, o, h, l, c, last_t in tester.resample([tuple(r) for r in rows], step_ms):
        if last_t == t + step_ms - sub:
            out.append([int(t), o, h, l, c])
    return out


def choose_window(m, tf, hidden, now_ms=None, attempts=4):
    """Random real window: about HISTORY_BARS visible + `hidden` to reveal, ending between ~1 week and ~6 months
    (15m: ~4 months) ago. Returns (bars, segments). Raises ValueError when no window has enough real candles."""
    import tester
    interval, k = TFS[tf]
    step = tester.STEP[interval] * k
    now_ms = now_ms or int(_now().timestamp() * 1000)
    need = HISTORY_BARS + hidden
    span = int(need * step * 1.25) + 3 * 86400_000   # room for weekend gaps on session markets
    back_lo, back_hi = 7 * 86400_000, (120 if tf == "15m" else 180) * 86400_000
    last_err = None
    for _ in range(attempts):
        end = now_ms - RNG.randint(back_lo, back_hi)
        end -= end % step
        start = end - span
        try:
            raw, prov = fetch_bars(m, interval, start, end)
        except Exception as e:  # data source down: say so, never invent candles
            last_err = e
            continue
        bars = build_window([b for b in raw if start <= b[0] < end], tf, step)
        if len(bars) < HISTORY_BARS + MIN_HIDDEN:
            continue
        bars = bars[-need:]
        lo, hi = bars[0][0], bars[-1][0] + step
        segs = [dict(source=sg.get("source"), label=sg.get("label"), from_ms=sg.get("from_ms"), to_ms=sg.get("to_ms"))
                for sg in (prov or {}).get("segments") or [] if sg.get("from_ms", 0) < hi and sg.get("to_ms", 0) > lo]
        return bars, segs
    if last_err is not None:
        try:
            import sources
            msg = sources.friendly_source_error(last_err)
        except Exception:
            msg = None
        raise RuntimeError(msg or "Market data is unavailable right now, try again in a minute.")
    raise ValueError("Not enough real candles for this market and timeframe in the history we can load. Try another market or timeframe.")


def date_shift(bars, hide):
    """With hidden dates, every timestamp sent before the end is moved back by whole weeks so that the first candle
    falls in the first week of 2001. Weekday and time of day (and so trading sessions) stay correct."""
    if not hide or not bars:
        return 0
    week = 7 * 86400_000
    base = 978307200000  # 2001-01-01 00:00 UTC (a Monday)
    return ((bars[0][0] - base) // week) * week


def _costs(asset_class):
    import tester
    return tester.FEES.get(asset_class, (0.0, 0.0))


def _r_values(t, exit_price, exit_ms, asset_class):
    """(r_gross, r_net) for a closed trade, using the stop distance at fill as 1R."""
    fill = t["fill_price"]
    risk = abs(fill - t["sl"])
    sgn = 1 if t["side"] == "long" else -1
    r_gross = sgn * (exit_price - fill) / risk
    fee, swap = _costs(asset_class)
    hours = max(0.0, (exit_ms - t["fill_ms"]) / 3_600_000)
    cost = fee / 100 * (fill + exit_price) + swap / 100 / 24 * fill * math.floor(hours)
    return round(r_gross, 4), round(r_gross - cost / risk, 4)


def step_trade(t, bar, idx, step_ms, asset_class):
    """Advance one order/trade through one newly revealed candle. Mutates and returns a dict of changes."""
    tt, o, h, l, c = bar
    ch = {}
    long_ = t["side"] == "long"
    if t["status"] == "pending":
        if t["kind"] == "market":
            fill = o
        else:
            touched = l <= t["entry"] if long_ else h >= t["entry"]
            if not touched:
                return ch
            fill = t["entry"]
        # an open already beyond the stop or target before the fill: the order is cancelled, no trade
        if (long_ and (fill <= t["sl"] or fill >= t["tp"])) or (not long_ and (fill >= t["sl"] or fill <= t["tp"])):
            ch.update(status="cancelled", exit_reason="cancelled", exit_idx=idx, exit_ms=tt,
                      note="Price opened beyond your stop or target before the order filled, so it was cancelled.")
            return ch
        risk_unit = abs(fill - t["sl"])
        ch.update(status="open", fill_idx=idx, fill_ms=tt, fill_price=fill,
                  units=(t["risk_amount"] / risk_unit) if t.get("risk_amount") and risk_unit > 0 else t.get("units"))
        t = dict(t, **ch)
        # fill candle: the stop counts, the target does not (order of moves inside the candle is unknown)
        hit_sl = l <= t["sl"] if long_ else h >= t["sl"]
        if hit_sl:
            ch.update(_exit(t, t["sl"], tt, idx, "sl", asset_class))
        return ch
    if t["status"] != "open":
        return ch
    hit_sl = l <= t["sl"] if long_ else h >= t["sl"]
    hit_tp = h >= t["tp"] if long_ else l <= t["tp"]
    gap_sl = o <= t["sl"] if long_ else o >= t["sl"]
    gap_tp = o >= t["tp"] if long_ else o <= t["tp"]
    if gap_sl:
        ch.update(_exit(t, o, tt, idx, "gap_sl", asset_class))
    elif hit_sl:                       # stop and target in one candle: the stop
        ch.update(_exit(t, t["sl"], tt, idx, "sl", asset_class))
    elif gap_tp or hit_tp:             # a gap through the target is credited at the target, not the better open
        ch.update(_exit(t, t["tp"], tt, idx, "tp", asset_class))
    return ch


def _exit(t, price, ms, idx, reason, asset_class):
    rg, rn = _r_values(t, price, ms, asset_class)
    pnl = round(rn * t["risk_amount"], 2) if t.get("risk_amount") else None
    return dict(status="closed", exit_price=price, exit_ms=ms, exit_idx=idx, exit_reason=reason, r_gross=rg, r_net=rn,
                pnl=pnl, closed_at=_now())


def _load(c, uid, sid, lock=False):
    q = select(practice_sessions).where(practice_sessions.c.id == sid, practice_sessions.c.user_id == uid)
    if lock:
        q = q.with_for_update()
    r = c.execute(q).mappings().first()
    return dict(r) if r else None


def _trades(c, sid):
    return [dict(r) for r in c.execute(select(practice_trades).where(practice_trades.c.session_id == sid)
                                       .order_by(practice_trades.c.id)).mappings()]


def _trade_public(t, shift, step_ms):
    keys = ("id", "kind", "side", "entry", "sl", "tp", "risk_pct", "risk_amount", "units", "placed_idx", "fill_idx",
            "fill_price", "exit_idx", "exit_price", "exit_reason", "r_gross", "r_net", "pnl", "status", "note")
    out = {k: t.get(k) for k in keys}
    for k in ("placed_ms", "fill_ms", "exit_ms"):
        out[k] = t[k] - shift if t.get(k) is not None else None
    out["created_at"] = _iso(t.get("created_at"))
    return out


def session_stats(trades, start_balance, balance):
    closed = [t for t in trades if t["status"] == "closed" and t.get("r_net") is not None]
    wins = [t for t in closed if t["r_net"] > 0]
    losses = [t for t in closed if t["r_net"] < 0]
    rs = [t["r_net"] for t in closed]
    gw, gl = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    eq = pk = dd = 0.0
    for r in rs:
        eq += r
        pk = max(pk, eq)
        dd = min(dd, eq - pk)
    return dict(setups_seen=len(closed) + sum(1 for t in trades if t["status"] in ("skipped", "cancelled", "open", "pending")),
                taken=sum(1 for t in trades if t["kind"] != "skip" and t["status"] != "cancelled"),
                skipped=sum(1 for t in trades if t["status"] == "skipped"),
                closed=len(closed), wins=len(wins), losses=len(losses),
                win_rate=round(len(wins) / len(closed), 3) if closed else None,
                net_r=round(sum(rs), 3), avg_r=round(sum(rs) / len(rs), 3) if rs else None,
                profit_factor=round(gw / gl, 2) if gl > 0 else None, max_drawdown_r=round(dd, 3),
                pnl=round(balance - start_balance, 2), return_pct=round((balance / start_balance - 1) * 100, 2))


def _public(s, trades, reveal=False):
    import tester
    bars = json.loads(s["bars"])
    st = json.loads(s["settings"] or "{}")
    ended = s["status"] != "active"
    shift = 0 if ended else st.get("shift", 0)
    step = tester.STEP[TFS[s["tf"]][0]] * TFS[s["tf"]][1]
    last = s["n_bars"] - 1 if (ended and reveal and bars) else s["cursor"]
    vis = [[b[0] - shift] + b[1:] for b in bars[:last + 1]]
    out = dict(id=s["id"], mode=s["mode"], market_id=s["market_id"], market_label=s["market_label"], tf=s["tf"],
               asset_class=s["asset_class"], strategy_id=s["strategy_id"], status=s["status"], result=s["result"],
               cursor=s["cursor"], start_idx=s["start_idx"], visible=len(vis),
               remaining=max(0, s["n_bars"] - 1 - s["cursor"]) if not ended else 0,
               candles=vis, step_ms=step, dates_hidden=bool(shift),
               start_balance=s["start_balance"], balance=round(s["balance"], 2),
               settings={k: v for k, v in st.items() if k != "shift"},
               sources=json.loads(s["sources"] or "[]") if (ended or not shift) else [],
               trades=[_trade_public(t, shift, step) for t in trades],
               stats=session_stats(trades, s["start_balance"], s["balance"]),
               created_at=_iso(s["created_at"]), finished_at=_iso(s["finished_at"]))
    if ended and shift == 0 and st.get("shift"):
        out["dates_revealed"] = True
    return out


def _save_trade(c, t, ch):
    if ch:
        c.execute(update(practice_trades).where(practice_trades.c.id == t["id"]).values(**ch))
        t.update(ch)


def _challenge_check(s, st, trades):
    rules = st.get("challenge")
    if not rules:
        return None
    sb = s["start_balance"]
    if s["balance"] <= sb * (1 - rules["fail_drawdown_pct"] / 100):
        return "failed"
    if s["balance"] >= sb * (1 + rules["pass_profit_pct"] / 100):
        return "passed"
    if sum(1 for t in trades if t["status"] == "closed") >= rules["max_trades"] and not any(t["status"] in ("open", "pending") for t in trades):
        return "incomplete"
    return None


def _finish(c, s, trades, status="finished", result=None):
    """Close anything open at the last visible close, cancel pending orders, end the session."""
    bars = json.loads(s["bars"])
    idx = s["cursor"]
    tt, c_last = bars[idx][0], bars[idx][4]
    for t in trades:
        if t["status"] == "open":
            ch = _exit(t, c_last, tt, idx, "end", s["asset_class"])
            _save_trade(c, t, ch)
            if ch.get("pnl") is not None:
                s["balance"] += ch["pnl"]
        elif t["status"] == "pending":
            _save_trade(c, t, dict(status="cancelled", exit_reason="cancelled", exit_idx=idx, exit_ms=tt))
    st = json.loads(s["settings"] or "{}")
    if st.get("challenge") and result is None:
        result = _challenge_check(s, st, trades) or "incomplete"
    now = _now()
    vals = dict(status=status, result=result, balance=s["balance"], finished_at=now, updated_at=now)
    c.execute(update(practice_sessions).where(practice_sessions.c.id == s["id"]).values(**vals))
    s.update(vals)
    # keep candles of the most recent finished sessions only (reviewable); older ones are trimmed to save space
    old = c.execute(select(practice_sessions.c.id).where(practice_sessions.c.user_id == s["user_id"],
                                                         practice_sessions.c.status != "active", practice_sessions.c.bars != "[]")
                    .order_by(practice_sessions.c.id.desc()).offset(KEEP_FINISHED_CANDLES)).scalars().all()
    if old:
        c.execute(update(practice_sessions).where(practice_sessions.c.id.in_(old)).values(bars="[]"))


def advance(c, s, n):
    """Reveal up to n candles, filling and closing orders on each. Stops early when a trade closes."""
    import tester
    bars = json.loads(s["bars"])
    step = tester.STEP[TFS[s["tf"]][0]] * TFS[s["tf"]][1]
    trades = _trades(c, s["id"])
    st = json.loads(s["settings"] or "{}")
    events = []
    for _ in range(n):
        if s["cursor"] >= s["n_bars"] - 1:
            break
        s["cursor"] += 1
        idx = s["cursor"]
        closed_now = False
        for t in trades:
            if t["status"] in ("pending", "open"):
                ch = step_trade(t, bars[idx], idx, step, s["asset_class"])
                if ch:
                    _save_trade(c, t, ch)
                    if ch.get("status") == "closed":
                        closed_now = True
                        if ch.get("pnl") is not None:
                            s["balance"] += ch["pnl"]
                    events.append(dict(trade_id=t["id"], status=t["status"], reason=t.get("exit_reason")))
        if closed_now:
            break
    c.execute(update(practice_sessions).where(practice_sessions.c.id == s["id"])
              .values(cursor=s["cursor"], balance=s["balance"], updated_at=_now()))
    res = _challenge_check(s, st, trades)
    if res in ("passed", "failed", "incomplete"):
        _finish(c, s, trades, result=res)
    elif s["cursor"] >= s["n_bars"] - 1:
        _finish(c, s, trades)
    return events


def delete_user_data(c, uid):
    c.execute(delete(practice_trades).where(practice_trades.c.user_id == uid))
    c.execute(delete(practice_sessions).where(practice_sessions.c.user_id == uid))


# ---------------------------------------------------------------- endpoints
@bp.post("/api/practice/sessions")
def create_session():
    u, bad = _guard()
    if bad:
        return bad
    d = _body()
    import tester
    mode = str(d.get("mode") or "practice").lower()
    if mode not in ("practice", "strategy", "challenge"):
        return auth._err("mode must be practice, strategy or challenge", 400)
    tf = str(d.get("tf") or ("1h" if mode == "challenge" else "1h")).lower()
    if tf not in TFS:
        return auth._err("tf must be 15m, 1h or 4h", 400)
    strat, strat_info = None, None
    try:
        risk_pct = _num(d, "risk_pct", 0.05, 5, default=1.0)
        hide = bool(d.get("hide_dates", True))
        if mode == "strategy":
            import library
            try:
                sid = int(d.get("strategy_id"))
            except (TypeError, ValueError):
                raise ValueError("choose one of your saved strategies")
            strat = library.owned_strategy(u["id"], sid)
            if not strat:
                return auth._err("Strategy not found.", 404)
            spec = json.loads(strat["spec"])
            mkts = spec.get("markets") or []
            want = str(d.get("market") or (mkts[0] if mkts else "")).strip()
            if not want:
                raise ValueError("this strategy has no market")
            if spec.get("tf") in TFS and not d.get("tf"):
                tf = spec["tf"]
            strat_info = dict(id=strat["id"], name=strat["name"], side=spec.get("side"), tf=spec.get("tf"),
                              setup_type=spec.get("setup_type"), pattern_label=spec.get("pattern_label"),
                              stop_pct=spec.get("stop_pct"), rr=spec.get("rr"),
                              entry_conditions=spec.get("entry_conditions") or "", exit_conditions=spec.get("exit_conditions") or "",
                              markets=mkts)
        else:
            want = str(d.get("market") or "").strip()
            if not want:
                raise ValueError("choose a market")
        if mode == "challenge":
            risk_pct = min(risk_pct, CHALLENGE["max_risk_pct"])
        resolve = HOOKS.get("resolve")
        mkts_all = tester.load_markets()
        m = next((x for x in mkts_all if x["market_id"] == want), None) or (resolve(want) if resolve else None)
        if m is None:
            raise ValueError("unknown market")
    except ValueError as e:
        return auth._err(str(e), 400)
    limit, done, lock_for = HOOKS.get("rate_limit"), HOOKS.get("rate_done"), HOOKS.get("market_lock")
    if limit:
        hit = limit()
        if hit:
            return hit
    try:
        lk = lock_for(m["market_id"]) if lock_for else None
        if lk and not lk.acquire(timeout=30):
            return auth._err("Server busy with other tests, try again in a moment.", 429)
        try:
            bars, segs = choose_window(m, tf, CHALLENGE_HIDDEN if mode == "challenge" else HIDDEN_BARS)
        except ValueError as e:
            return auth._err(str(e), 422)
        except RuntimeError as e:
            return auth._err(str(e), 503)
        finally:
            if lk:
                lk.release()
    finally:
        if done:
            done()
    st = dict(risk_pct=risk_pct, hide_dates=hide, shift=date_shift(bars, hide))
    sb = START_BALANCE
    if mode == "challenge":
        st["challenge"] = dict(CHALLENGE)
        sb = CHALLENGE["start_balance"]
    if strat_info:
        st["strategy"] = strat_info
    fee, swap = _costs(tester.asset_class(m))
    st["costs"] = dict(commission_pct_per_fill=fee, swap_pct_per_day=swap, slippage="not modelled")
    now = _now()
    hist = min(HISTORY_BARS, len(bars) - MIN_HIDDEN)
    with auth.engine().begin() as c:
        for old in c.execute(select(practice_sessions).where(practice_sessions.c.user_id == u["id"],
                                                             practice_sessions.c.status == "active")).mappings().all():
            old = dict(old)
            _finish(c, old, _trades(c, old["id"]), status="abandoned")
        res = c.execute(insert(practice_sessions).values(
            user_id=u["id"], mode=mode, market_id=m["market_id"], market_label=str(m.get("label") or m.get("coin") or m["market_id"])[:80],
            asset_class=tester.asset_class(m), tf=tf, strategy_id=strat["id"] if strat else None,
            bars=json.dumps(bars, separators=(",", ":")), cursor=hist - 1, start_idx=hist - 1, n_bars=len(bars),
            sources=json.dumps(segs), settings=json.dumps(st), start_balance=sb, balance=sb, status="active",
            created_at=now, updated_at=now))
        s = _load(c, u["id"], res.inserted_primary_key[0])
        return jsonify(session=_public(s, [])), 201


@bp.get("/api/practice/sessions/<int:sid>")
def get_session(sid):
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().connect() as c:
        s = _load(c, u["id"], sid)
        if not s:
            return _nf()
        return jsonify(session=_public(s, _trades(c, sid), reveal=True))


@bp.get("/api/practice/active")
def get_active():
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().connect() as c:
        r = c.execute(select(practice_sessions).where(practice_sessions.c.user_id == u["id"], practice_sessions.c.status == "active")
                      .order_by(practice_sessions.c.id.desc())).mappings().first()
        if not r:
            return jsonify(session=None)
        s = dict(r)
        return jsonify(session=_public(s, _trades(c, s["id"])))


def _active_or_err(c, uid, sid):
    s = _load(c, uid, sid, lock=True)
    if not s:
        return None, _nf()
    if s["status"] != "active":
        return None, auth._err("This session has ended. Start a new one.", 409)
    return s, None


@bp.post("/api/practice/sessions/<int:sid>/advance")
def advance_session(sid):
    u, bad = _guard()
    if bad:
        return bad
    try:
        n = int(_num(_body(), "n", 1, MAX_ADVANCE, default=1))
    except ValueError as e:
        return auth._err(str(e), 400)
    with auth.engine().begin() as c:
        s, bad = _active_or_err(c, u["id"], sid)
        if bad:
            return bad
        events = advance(c, s, n)
        s = _load(c, u["id"], sid)
        return jsonify(session=_public(s, _trades(c, sid), reveal=True), events=events)


@bp.post("/api/practice/sessions/<int:sid>/orders")
def place_order(sid):
    u, bad = _guard()
    if bad:
        return bad
    d = _body()
    with auth.engine().begin() as c:
        s, bad = _active_or_err(c, u["id"], sid)
        if bad:
            return bad
        bars = json.loads(s["bars"])
        st = json.loads(s["settings"] or "{}")
        trades = _trades(c, sid)
        if s["cursor"] >= s["n_bars"] - 1:
            return auth._err("No candles left in this window.", 409)
        if any(t["status"] in ("pending", "open") for t in trades):
            return auth._err("You already have an order or open trade. Close or cancel it first.", 409)
        last = bars[s["cursor"]][4]
        try:
            side = str(d.get("side") or "").lower()
            if side not in ("long", "short"):
                raise ValueError("side must be long or short")
            kind = str(d.get("kind") or "market").lower()
            if kind not in ("market", "limit"):
                raise ValueError("order type must be market or limit")
            sl = _num(d, "sl", 0, 1e12, required=True)
            tp = _num(d, "tp", 0, 1e12, required=True)
            entry = _num(d, "entry", 0, 1e12, required=(kind == "limit")) if kind == "limit" else last
            maxr = (st.get("challenge") or {}).get("max_risk_pct", 5)
            risk_pct = _num(d, "risk_pct", 0.01, maxr, default=st.get("risk_pct", 1.0))
            if side == "long" and not (sl < entry < tp):
                raise ValueError("for a long, the stop must be below the entry and the target above it")
            if side == "short" and not (tp < entry < sl):
                raise ValueError("for a short, the stop must be above the entry and the target below it")
            if kind == "limit" and side == "long" and entry >= last:
                raise ValueError("a long limit must be below the last close (use a market order to buy now)")
            if kind == "limit" and side == "short" and entry <= last:
                raise ValueError("a short limit must be above the last close (use a market order to sell now)")
            note = str(d.get("note") or "")[:200]
        except ValueError as e:
            return auth._err(str(e), 400)
        risk_amount = round(s["balance"] * risk_pct / 100, 2)
        dist = abs(entry - sl)
        row = dict(session_id=sid, user_id=u["id"], kind=kind, side=side, entry=entry, sl=sl, tp=tp, risk_pct=risk_pct,
                   risk_amount=risk_amount, units=risk_amount / dist if dist > 0 else None, placed_idx=s["cursor"],
                   placed_ms=bars[s["cursor"]][0], status="pending", note=note, created_at=_now())
        c.execute(insert(practice_trades).values(**row))
        return jsonify(session=_public(s, _trades(c, sid))), 201


@bp.post("/api/practice/sessions/<int:sid>/skip")
def skip_setup(sid):
    """Record that the trader saw this candle and chose not to trade, then reveal the next candle."""
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().begin() as c:
        s, bad = _active_or_err(c, u["id"], sid)
        if bad:
            return bad
        trades = _trades(c, sid)
        if any(t["status"] in ("pending", "open") for t in trades):
            return auth._err("You have an order or open trade; skip is for candles where you are flat.", 409)
        bars = json.loads(s["bars"])
        c.execute(insert(practice_trades).values(session_id=sid, user_id=u["id"], kind="skip", placed_idx=s["cursor"],
                                                 placed_ms=bars[s["cursor"]][0], status="skipped", created_at=_now()))
        events = advance(c, s, 1)
        s = _load(c, u["id"], sid)
        return jsonify(session=_public(s, _trades(c, sid), reveal=True), events=events)


@bp.post("/api/practice/sessions/<int:sid>/cancel")
def cancel_order(sid):
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().begin() as c:
        s, bad = _active_or_err(c, u["id"], sid)
        if bad:
            return bad
        bars = json.loads(s["bars"])
        n = 0
        for t in _trades(c, sid):
            if t["status"] == "pending":
                _save_trade(c, t, dict(status="cancelled", exit_reason="cancelled", exit_idx=s["cursor"], exit_ms=bars[s["cursor"]][0]))
                n += 1
        if not n:
            return auth._err("No pending order to cancel.", 409)
        return jsonify(session=_public(s, _trades(c, sid)))


@bp.post("/api/practice/sessions/<int:sid>/close")
def close_trade(sid):
    """Close the open trade at the last visible close (the price you can see; nothing from the future)."""
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().begin() as c:
        s, bad = _active_or_err(c, u["id"], sid)
        if bad:
            return bad
        bars = json.loads(s["bars"])
        trades = _trades(c, sid)
        op = [t for t in trades if t["status"] == "open"]
        if not op:
            return auth._err("No open trade to close.", 409)
        b = bars[s["cursor"]]
        for t in op:
            ch = _exit(t, b[4], b[0], s["cursor"], "manual", s["asset_class"])
            _save_trade(c, t, ch)
            if ch.get("pnl") is not None:
                s["balance"] += ch["pnl"]
        c.execute(update(practice_sessions).where(practice_sessions.c.id == sid).values(balance=s["balance"], updated_at=_now()))
        st = json.loads(s["settings"] or "{}")
        res = _challenge_check(s, st, trades)
        if res:
            _finish(c, s, trades, result=res)
        return jsonify(session=_public(s, _trades(c, sid), reveal=True))


@bp.post("/api/practice/sessions/<int:sid>/finish")
def finish_session(sid):
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().begin() as c:
        s, bad = _active_or_err(c, u["id"], sid)
        if bad:
            return bad
        _finish(c, s, _trades(c, sid))
        s = _load(c, u["id"], sid)
        return jsonify(session=_public(s, _trades(c, sid), reveal=True))


@bp.get("/api/practice/sessions")
def list_sessions():
    u, bad = _guard()
    if bad:
        return bad
    with auth.engine().connect() as c:
        rows = c.execute(select(practice_sessions.c.id, practice_sessions.c.mode, practice_sessions.c.market_id,
                                practice_sessions.c.market_label, practice_sessions.c.tf, practice_sessions.c.status,
                                practice_sessions.c.result, practice_sessions.c.start_balance, practice_sessions.c.balance,
                                practice_sessions.c.created_at, practice_sessions.c.finished_at)
                         .where(practice_sessions.c.user_id == u["id"]).order_by(practice_sessions.c.id.desc()).limit(30)).mappings().all()
        out = []
        for r in rows:
            tr = _trades(c, r["id"])
            d = dict(r)
            d["created_at"], d["finished_at"] = _iso(r["created_at"]), _iso(r["finished_at"])
            d["stats"] = session_stats(tr, r["start_balance"], r["balance"])
            out.append(d)
        return jsonify(sessions=out)


def aggregate(trades, sessions, tz_min=0, now=None):
    """Practice totals plus daily (last 14 days) and weekly (last 8 weeks) figures, grouped by the time a trade was
    closed in practice (wall clock, in the viewer's time zone)."""
    now = now or _now()
    tz = dt.timezone(dt.timedelta(minutes=tz_min))
    closed = [t for t in trades if t["status"] == "closed" and t.get("r_net") is not None and t.get("closed_at")]

    def blk(ts):
        rs = [t["r_net"] for t in ts]
        w = sum(1 for r in rs if r > 0)
        return dict(trades=len(rs), wins=w, losses=sum(1 for r in rs if r < 0), win_rate=round(w / len(rs), 3) if rs else None,
                    net_r=round(sum(rs), 3), avg_r=round(sum(rs) / len(rs), 3) if rs else None)
    today = now.astimezone(tz).date()
    days = []
    for i in range(13, -1, -1):
        dday = today - dt.timedelta(days=i)
        ts = [t for t in closed if auth._aware(t["closed_at"]).astimezone(tz).date() == dday]
        days.append(dict(date=dday.isoformat(), **blk(ts)))
    weeks = []
    monday = today - dt.timedelta(days=today.weekday())
    for i in range(7, -1, -1):
        w0 = monday - dt.timedelta(weeks=i)
        ts = [t for t in closed if w0 <= auth._aware(t["closed_at"]).astimezone(tz).date() < w0 + dt.timedelta(days=7)]
        weeks.append(dict(week_start=w0.isoformat(), **blk(ts)))
    by_mode = {}
    smode = {s["id"]: s["mode"] for s in sessions}
    for mname in ("practice", "strategy", "challenge"):
        by_mode[mname] = blk([t for t in closed if smode.get(t["session_id"]) == mname])
    ch = [s for s in sessions if s["mode"] == "challenge" and s["status"] != "active"]
    return dict(total=blk(closed), skipped=sum(1 for t in trades if t["status"] == "skipped"),
                sessions=len(sessions), daily=days, weekly=weeks, by_mode=by_mode,
                challenges=dict(attempted=len(ch), passed=sum(1 for s in ch if s["result"] == "passed"),
                                failed=sum(1 for s in ch if s["result"] == "failed")),
                tz_offset_min=tz_min)


@bp.get("/api/practice/stats")
def stats():
    u, bad = _guard()
    if bad:
        return bad
    try:
        tz_min = int(request.args.get("tz", "0"))
    except ValueError:
        tz_min = 0
    tz_min = max(-840, min(840, tz_min))
    with auth.engine().connect() as c:
        trades = [dict(r) for r in c.execute(select(practice_trades).where(practice_trades.c.user_id == u["id"])).mappings()]
        sessions = [dict(r) for r in c.execute(select(practice_sessions.c.id, practice_sessions.c.mode, practice_sessions.c.status,
                                                      practice_sessions.c.result).where(practice_sessions.c.user_id == u["id"])).mappings()]
    return jsonify(stats=aggregate(trades, sessions, tz_min))
