#!/usr/bin/env python3
"""Offline checks for --compare: the 5-market cap, ATR geometry transfer, and pooled maths.

Run: python3 tests/test_compare.py   (or pytest tests/)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tester as T


def mk(mid, symbol, coin, provider="binance"):
    return dict(market_id=mid, symbol=symbol, coin=coin, provider=provider, default_venue=True)


FX = [
    mk("veranta|EURUSD", "EUR", "EURUSD", "veranta"),
    mk("veranta|GBPUSD", "GBP", "GBPUSD", "veranta"),
    mk("veranta|AUDUSD", "AUD", "AUDUSD", "veranta"),
    mk("veranta|USDJPY", "JPY", "USDJPY", "veranta"),
    mk("veranta|USDCAD", "USDCAD", "USDCAD", "veranta"),
    mk("veranta|USDCHF", "USDCHF", "USDCHF", "veranta"),
]
CRYPTO = [
    mk("binance|BTCUSDT", "BTC", "BTCUSDT"),
    mk("binance|ETHUSDT", "ETH", "ETHUSDT"),
    mk("binance|SOLUSDT", "SOL", "SOLUSDT"),
    mk("binance|XRPUSDT", "XRP", "XRPUSDT"),
    mk("binance|BNBUSDT", "BNB", "BNBUSDT"),
    mk("binance|HYPEUSDT", "HYPE", "HYPEUSDT"),
]
INDEX = [
    mk("hyperliquid|xyz:SP500", "SP500", "xyz:SP500", "hyperliquid"),
    mk("hyperliquid|xyz:XYZ100", "XYZ100", "xyz:XYZ100", "hyperliquid"),
    mk("ostium|DJI", "DJI", "DJI", "ostium"),
    mk("hyperliquid|xyz:JP225", "JP225", "xyz:JP225", "hyperliquid"),
]
COMMODITY = [
    mk("binance|XAUUSDT", "XAU", "XAUUSDT"),
    mk("binance|XAGUSDT", "XAG", "XAGUSDT"),
    mk("binance|CLUSDT", "CL", "CLUSDT"),
    mk("hyperliquid|xyz:BRENTOIL", "BRENTOIL", "xyz:BRENTOIL", "hyperliquid"),
    mk("binance|NATGASUSDT", "NATGAS", "NATGASUSDT"),
]
EQUITY = [
    mk("hyperliquid|xyz:AAPL", "AAPL", "xyz:AAPL", "hyperliquid"),
    mk("hyperliquid|xyz:MSFT", "MSFT", "xyz:MSFT", "hyperliquid"),
    mk("binance|NVDAUSDT", "NVDA", "NVDAUSDT"),
]
MARKETS = FX + CRYPTO + INDEX + COMMODITY + EQUITY


def test_compare_rejects_more_than_five():
    src = FX[0]
    try:
        T.parse_compare("GBPUSD,AUDUSD,USDJPY,USDCAD,USDCHF,BTC", MARKETS, src)
        assert False, "expected ValueError for 6 names"
    except ValueError as e:
        assert "at most 5" in str(e)
        assert "got 6" in str(e)


def test_compare_accepts_five_and_transfers_names():
    src = FX[0]
    out = T.parse_compare("GBPUSD,AUDUSD,USDJPY,USDCAD,USDCHF", MARKETS, src)
    assert [m["market_id"] for m in out] == [
        "veranta|GBPUSD", "veranta|AUDUSD", "veranta|USDJPY", "veranta|USDCAD", "veranta|USDCHF"]


def test_compare_rejects_source_market():
    try:
        T.parse_compare("GBPUSD,EURUSD", MARKETS, FX[0])
        assert False, "expected ValueError when the source pair is in the list"
    except ValueError as e:
        assert "own market" in str(e)


def test_compare_rejects_basket_shortcut_and_empty():
    try:
        T.parse_compare("fx", MARKETS, FX[0])
        assert False, "expected ValueError for fx shortcut"
    except ValueError as e:
        assert "basket" in str(e).lower() or "shortcut" in str(e).lower()
    try:
        T.parse_compare("  ,  ", MARKETS, FX[0])
        assert False, "expected ValueError for an empty list"
    except ValueError as e:
        assert "needs" in str(e) or "at most" in str(e)


def test_compare_skips_duplicates():
    out = T.parse_compare("GBPUSD,gbpusd,AUDUSD", MARKETS, FX[0])
    assert [m["market_id"] for m in out] == ["veranta|GBPUSD", "veranta|AUDUSD"]


def test_suggest_compare_same_family():
    fx = T.suggest_compare(FX[0], MARKETS)  # EURUSD
    assert len(fx) == 5
    assert all(T.market_family(m) == "forex" for m in fx)
    assert FX[0] not in fx
    # preferred majors come first
    assert [m["coin"] for m in fx] == ["GBPUSD", "AUDUSD", "USDJPY", "USDCAD", "USDCHF"]

    hyp = T.suggest_compare(next(m for m in CRYPTO if m["symbol"] == "HYPE"), MARKETS)
    assert len(hyp) == 5
    assert all(T.market_family(m) == "crypto" for m in hyp)
    assert [m["symbol"] for m in hyp] == ["BTC", "ETH", "SOL", "XRP", "BNB"]

    idx = T.suggest_compare(INDEX[0], MARKETS)
    assert len(idx) == 3  # only three other index markets in the fixture
    assert [T.market_family(m) for m in idx] == ["index"] * len(idx)
    assert [m["symbol"] for m in idx] == ["XYZ100", "DJI", "JP225"]

    com = T.suggest_compare(COMMODITY[0], MARKETS)  # XAU
    assert all(T.market_family(m) == "commodity" for m in com)
    assert [m["symbol"] for m in com][:3] == ["XAG", "CL", "BRENTOIL"]


def test_transfer_geometry_preserves_atr_shape_and_rr():
    # long: entry 100, TP 130, SL 90, ref 105, ATR 10  -> entry -0.5 ATR, risk 1, reward 3, R:R 3
    g = T.build_geometry("long", 100, 130, 90, 105, 10)
    assert abs(g["entry_atr"] - (-0.5)) < 1e-12
    assert abs(g["risk_atr"] - 1) < 1e-12
    assert abs(g["reward_atr"] - 3) < 1e-12
    assert abs(g["rr"] - 3) < 1e-12
    g2 = T.transfer_geometry(g, ref=200, atr_now=20, market_id="binance|FOOUSDT")
    assert g2["market_id"] == "binance|FOOUSDT"
    assert g2["side"] == "long"
    assert abs(g2["entry_atr"] - g["entry_atr"]) < 1e-12
    assert abs(g2["risk_atr"] - g["risk_atr"]) < 1e-12
    assert abs(g2["reward_atr"] - g["reward_atr"]) < 1e-12
    assert abs(g2["rr"] - g["rr"]) < 1e-12
    assert abs(g2["entry"] - (200 + g["entry_atr"] * 20)) < 1e-9
    assert abs(g2["sl"] - (g2["entry"] - g["sgn"] * g["risk_atr"] * 20)) < 1e-9
    assert abs(g2["tp"] - (g2["entry"] + g["sgn"] * g["reward_atr"] * 20)) < 1e-9
    assert abs(g2["ref"] - 200) < 1e-12
    assert abs(g2["atr"] - 20) < 1e-12

    # short: entry 150, TP 120, SL 160, ref 148, ATR 5
    gs = T.build_geometry("short", 150, 120, 160, 148, 5)
    gs2 = T.transfer_geometry(gs, ref=80, atr_now=2)
    assert gs2["side"] == "short"
    assert abs(gs2["entry_atr"] - gs["entry_atr"]) < 1e-12
    assert abs(gs2["risk_atr"] - gs["risk_atr"]) < 1e-12
    assert abs(gs2["reward_atr"] - gs["reward_atr"]) < 1e-12
    assert abs(gs2["rr"] - gs["rr"]) < 1e-12
    assert gs2["sl"] > gs2["entry"] > gs2["tp"]


def _tr(status, r_net, placed, exit_ms, market="A"):
    t = dict(status=status, r_net=r_net, r_gross=r_net, placed=placed, market=market)
    if status in ("tp", "sl", "time"):
        t["filled"], t["exit"] = placed + 1, exit_ms
    return t


def test_pooled_maths_match_concatenated_trades():
    g = dict(rr=3.0)
    a = [
        _tr("tp", 2.9, 100, 200, "A"),
        _tr("sl", -1.05, 300, 400, "A"),
        _tr("time", -0.2, 500, 600, "A"),
        _tr("unfilled", None, 700, None, "A"),
    ]
    b = [
        _tr("tp", 2.8, 150, 250, "B"),
        _tr("sl", -1.02, 350, 450, "B"),
        _tr("sl", -1.01, 550, 650, "B"),
    ]
    sa, sb = T.summarize(a, g), T.summarize(b, g)
    # 20-trade floor would say "too few"; we only check the arithmetic here
    assert sa["closed"] == 3 and sa["tp"] == 1 and sa["sl"] == 1 and sa["time_exits"] == 1
    assert abs(sa["total_r_net"] - (2.9 - 1.05 - 0.2)) < 1e-9
    assert abs(sa["avg_r_net"] - (2.9 - 1.05 - 0.2) / 3) < 1e-9
    pooled_trades = sorted(a + b, key=lambda t: t.get("exit") or t["placed"])
    sp = T.summarize(pooled_trades, g)
    assert sp["closed"] == sa["closed"] + sb["closed"] == 6
    assert sp["tp"] == sa["tp"] + sb["tp"] == 2
    assert sp["sl"] == sa["sl"] + sb["sl"] == 3
    assert sp["time_exits"] == sa["time_exits"] + sb["time_exits"] == 1
    assert abs(sp["total_r_net"] - (sa["total_r_net"] + sb["total_r_net"])) < 1e-9
    assert abs(sp["avg_r_net"] - round(sp["total_r_net"] / sp["closed"], 3)) < 1e-12
    assert sp["placed"] == 7
    # first-half / second-half are over the exit-time-sorted list, not a sum of per-market halves
    rs = [t["r_net"] for t in pooled_trades if t["status"] in ("tp", "sl", "time")]
    assert sp["first_half_avg_r"] == round(sum(rs[:3]) / 3, 3)
    assert sp["second_half_avg_r"] == round(sum(rs[3:]) / 3, 3)
    assert sp["avg_r_net"] == round(sp["total_r_net"] / sp["closed"], 3)


def test_compare_summary_counts_only_other_pairs():
    def row(role, verdict, **kw):
        s = dict(closed=kw.get("closed", 25), win_rate=0.4, breakeven_win_rate=0.25,
                 avg_r_net=kw.get("avg", 0.2), total_r_net=2.0, tp=10, sl=10, time_exits=5,
                 verdict=verdict)
        return dict(market_id="x", symbol="X", role=role, works=T.pair_works(s), **s)

    rows = [
        row("source", "edge on this sample (expectancy +0.40R, t=2.1); past results only"),
        row("compare", "no edge (expectancy <= 0 after fees)", avg=-0.1),
        row("compare", "possible edge, not proven (expectancy +0.15R, t=1.2; noise can't be ruled out)"),
        row("compare", "too few samples (8 closed trades, need 20)", closed=8),
        row("compare", "no edge (expectancy <= 0 after fees)", avg=-0.2),
        row("compare", "edge on this sample (expectancy +0.50R, t=2.4); past results only"),
    ]
    pooled = dict(verdict="no edge (expectancy <= 0 after fees)")
    assert T.compare_summary(rows, pooled) == "works on 2 of 5, pooled no edge"
    # source "works" must not count
    assert T.pair_works(rows[0]) is True
    assert T.compare_summary(rows[:1], dict(verdict="too few samples (3 closed trades, need 20)")) == \
        "works on 0 of 0, pooled too few samples"


def test_pair_works_verdict_prefixes():
    assert T.pair_works(dict(verdict="edge on this sample (expectancy +0.50R, t=2.4); past results only"))
    assert T.pair_works(dict(verdict="possible edge, not proven (expectancy +0.15R, t=1.2; noise can't be ruled out)"))
    assert not T.pair_works(dict(verdict="no edge (expectancy <= 0 after fees)"))
    assert not T.pair_works(dict(verdict="too few samples (12 closed trades, need 20)"))
    assert not T.pair_works(dict(verdict=""))


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok ", name)
            except Exception as e:
                failed += 1
                print("FAIL", name, type(e).__name__, e)
    sys.exit(1 if failed else 0)
