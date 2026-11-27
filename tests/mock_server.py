#!/usr/bin/env python3
"""Run the CHRONOS web app locally with the OpenAI vision call replaced by a canned reply.

For UI tests and screenshots only (the OpenAI account may be out of credit). Backtests still use real candles.
  python3 tests/mock_server.py [port]   (default 8093)
Set MOCK_VISION=quota to simulate an out-of-credit OpenAI account.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, ROOT)
os.environ.setdefault("OPENAI_API_KEY", "sk-mock-not-a-real-key")
os.environ.setdefault("SQLITE_PATH", os.path.join("/tmp", "chronos-mock.db"))
import app as A  # noqa: E402
import screenshot as shot  # noqa: E402

CANNED = {
    "symbol": "BTCUSDT", "side": "long", "pattern": "Order block", "timeframe": "1h",
    "entry": None, "take_profit": None, "stop_loss": None,
    "confidence": {"symbol": 0.95, "side": 0.9, "pattern": 0.8, "timeframe": 0.9,
                   "entry": 0.9, "take_profit": 0.55, "stop_loss": 0.3},
    "notes": "Stop-loss label is partly hidden behind the price scale.",
}


def fake_vision(image_bytes, mime, pattern=""):
    if os.environ.get("MOCK_VISION") == "quota":
        raise shot.ScreenshotError(shot.QUOTA_MSG, 503)
    import tester
    m = tester.resolve_market("BTC", tester.load_markets())
    try:
        import sources, time
        now = int(time.time() * 1000)
        bars, _ = sources.load(m, "15m", now - 2 * 86400_000, now - now % 900_000)
        px = bars[-1][4]
    except Exception:
        px = 60000.0
    raw = dict(CANNED, entry=round(px * 0.995, 1), take_profit=round(px * 1.02, 1), stop_loss=round(px * 0.985, 1))
    return shot.parse_vision_payload(raw)


shot.call_vision = fake_vision

if __name__ == "__main__":
    from waitress import serve
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8093
    print(f"CHRONOS mock on http://127.0.0.1:{port}", flush=True)
    serve(A.app, host="127.0.0.1", port=port, threads=8)
