#!/usr/bin/env python3
"""Offline checks for MFP 429 retry, short-lived cache, and backup fallback. No network.

1. A 429 (and similar connect refusals) is retried with exponential backoff + jitter; Retry-After is honoured.
2. After a few refusals the public SourceBusy message is raised, never a raw InvalidStatus.
3. If MFP still refuses, load() uses backup sources for the whole window and labels them.
4. If MFP and backups both fail, SourceBusy is raised.
5. A short-lived cache serves a repeat request without calling the fetcher again.
6. The web layer maps raw 429 / traceback text to the same public message.
Run: python3 tests/test_mfp_retry.py
"""
import asyncio, json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sources as S

M15 = 900_000
T0 = 1_780_000_000_000 - 1_780_000_000_000 % 86_400_000


def series(t_from, n, price=100.0, drift=0.01, skip=()):
    out = {}
    for i in range(n):
        if i in skip:
            continue
        t = t_from + i * M15
        p = price + drift * ((t - T0) // M15)
        out[t] = (t, p, p + 0.05, p - 0.05, p + 0.01)
    return out


class Fake429(Exception):
    """Duck-typed stand-in for websockets.exceptions.InvalidStatus."""
    def __init__(self, code=429, retry_after=None):
        self.status_code = code
        self.response = type("R", (), {
            "status_code": code,
            "status": code,
            "headers": ({"Retry-After": str(retry_after)} if retry_after is not None else {}),
        })()
        super().__init__(f"server rejected WebSocket connection: HTTP {code}")


class FakeWS:
    def __init__(self):
        self.q = asyncio.Queue()

    async def send(self, s):
        msg = json.loads(s)
        t = int(msg["payload"]["startTime"])
        await self.q.put(json.dumps({
            "id": msg["id"],
            "result": [{"openTime": t, "open": 1, "high": 1, "low": 1, "close": 1, "isFinal": True}],
        }))

    async def recv(self):
        return await self.q.get()


class FlakyConnect:
    """Fails `fails` times, then returns a working FakeWS. Each call returns a new CM."""
    def __init__(self, fails=2, retry_after=None, code=429):
        self.fails = fails
        self.n = 0
        self.retry_after = retry_after
        self.code = code

    def __call__(self):
        return _CM(self)


class _CM:
    def __init__(self, owner):
        self.owner = owner
        self.ws = None

    async def __aenter__(self):
        self.owner.n += 1
        if self.owner.n <= self.owner.fails:
            raise Fake429(self.owner.code, self.owner.retry_after)
        self.ws = FakeWS()
        return self.ws

    async def __aexit__(self, *a):
        return False


def test_retry_429_then_success():
    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s):
        sleeps.append(s)

    factory = FlakyConnect(fails=2)
    async def run():
        asyncio.sleep = fake_sleep
        try:
            out = await S._mfp_chunks("veranta", "USDJPY", "15m", [(T0, T0 + M15)], connect_factory=factory)
        finally:
            asyncio.sleep = real_sleep
        return out

    out = asyncio.run(run())
    assert factory.n == 3, factory.n
    assert T0 in out and len(sleeps) == 2, (out, sleeps)
    assert all(s > 0 for s in sleeps)


def test_retry_after_is_honoured():
    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s):
        sleeps.append(s)

    factory = FlakyConnect(fails=1, retry_after=1.5)

    async def run():
        asyncio.sleep = fake_sleep
        try:
            return await S._mfp_chunks("veranta", "USDJPY", "15m", [(T0, T0 + M15)], connect_factory=factory)
        finally:
            asyncio.sleep = real_sleep

    asyncio.run(run())
    assert len(sleeps) == 1 and sleeps[0] >= 1.5, sleeps


def test_exhausted_retries_raise_source_busy():
    real_sleep = asyncio.sleep

    async def fake_sleep(s):
        return None

    factory = FlakyConnect(fails=99)

    async def run():
        asyncio.sleep = fake_sleep
        try:
            await S._mfp_chunks("veranta", "USDJPY", "15m", [(T0, T0 + M15)], connect_factory=factory)
        finally:
            asyncio.sleep = real_sleep

    try:
        asyncio.run(run())
        raise AssertionError("expected SourceBusy")
    except S.SourceBusy as e:
        assert str(e) == S.BUSY_MSG
    assert factory.n == S.MFP_CONNECT_TRIES


def test_non_retryable_4xx_does_not_retry():
    factory = FlakyConnect(fails=99, code=401)
    try:
        asyncio.run(S._mfp_chunks("veranta", "USDJPY", "15m", [(T0, T0 + M15)], connect_factory=factory))
        raise AssertionError("expected SourceBusy")
    except S.SourceBusy:
        pass
    assert factory.n == 1, factory.n


def test_friendly_error_hides_raw_429():
    raw = Fake429()
    assert S.friendly_source_error(raw) == S.BUSY_MSG
    assert S.friendly_source_error("websockets.exceptions.InvalidStatus: server rejected WebSocket connection: HTTP 429") == S.BUSY_MSG
    assert S.friendly_source_error("not enough history for veranta|USDJPY (0 bars)") is None
    assert S.friendly_source_error(S.SourceBusy(S.BUSY_MSG)) == S.BUSY_MSG


def test_load_falls_back_to_backups_when_mfp_busy():
    bars_all = series(T0, 1000)
    real_cf, real_bf = S.cached_fetch, S.backups_for

    def fake_cf(source, symbol, interval, start, end, refresh=False):
        if source == "mfp":
            raise S.SourceBusy(S.BUSY_MSG)
        return {t: b for t, b in bars_all.items() if start <= t < end}

    S.cached_fetch = fake_cf
    S.backups_for = lambda m: [("yahoo", "JPY=X", 1.0)]
    try:
        bars, prov = S.load({"market_id": "veranta|USDJPY", "provider": "veranta", "coin": "USDJPY"},
                            "15m", T0, T0 + 1000 * M15)
    finally:
        S.cached_fetch, S.backups_for = real_cf, real_bf
    assert len(bars) == 1000
    assert prov["mfp_unavailable"] is True
    assert [s["source"] for s in prov["segments"]] == ["yahoo:JPY=X"]
    assert prov["segments"][0]["label"] == "Yahoo Finance (not a perp)"
    assert any(e.startswith("mfp:") for e in prov["errors"])
    txt = S.provenance_text(prov)
    assert "Yahoo Finance" in txt and "MFP feed was unavailable" in txt
    assert "InvalidStatus" not in txt


def test_load_raises_busy_when_all_sources_fail():
    real_cf, real_bf = S.cached_fetch, S.backups_for

    def fake_cf(source, symbol, interval, start, end, refresh=False):
        if source == "mfp":
            raise S.SourceBusy(S.BUSY_MSG)
        raise RuntimeError(f"{source} also down")

    S.cached_fetch = fake_cf
    S.backups_for = lambda m: [("yahoo", "JPY=X", 1.0)]
    try:
        try:
            S.load({"market_id": "veranta|USDJPY", "provider": "veranta", "coin": "USDJPY"},
                   "15m", T0, T0 + 1000 * M15)
            raise AssertionError("expected SourceBusy")
        except S.SourceBusy as e:
            assert str(e) == S.BUSY_MSG
    finally:
        S.cached_fetch, S.backups_for = real_cf, real_bf


def test_short_lived_cache_skips_refetch():
    old_dir, old_ttl = S.CACHE_DIR, S.RECENT_TTL_S
    S.reset_mem_cache()
    calls = []
    with tempfile.TemporaryDirectory() as td:
        S.CACHE_DIR = td
        S.RECENT_TTL_S = 180
        S.FETCHERS["fake"] = lambda sym, iv, s, e: (calls.append((s, e)), series(s, (e - s) // M15))[1]
        try:
            # End is 'now'-like: last two bars would not be marked covered without the TTL.
            now_ms = T0 + 100 * M15 + 30_000
            real_time = time.time
            # frozen wall clock, and make STEP math think 'now' is now_ms
            time.time = lambda: now_ms / 1000
            try:
                a = S.cached_fetch("fake", "X|Y", "15m", T0, T0 + 100 * M15)
                n1 = len(calls)
                b = S.cached_fetch("fake", "X|Y", "15m", T0, T0 + 100 * M15)
                n2 = len(calls)
            finally:
                time.time = real_time
            assert a == b and n1 == 1 and n2 == 1, (n1, n2, len(a))
        finally:
            S.FETCHERS.pop("fake", None)
            S.CACHE_DIR = old_dir
            S.RECENT_TTL_S = old_ttl
            S.reset_mem_cache()
            time.time = real_time


def test_web_hides_raw_exception():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("chronos_web_retry", os.path.join(root, "web", "app.py"))
    web = importlib.util.module_from_spec(spec)
    # Reuse the already-imported app if present under a unique name
    sys.modules["chronos_web_retry"] = web
    spec.loader.exec_module(web)
    with web.app.app_context():
        resp, code = web.public_engine_error(
            "websockets.exceptions.InvalidStatus: server rejected WebSocket connection: HTTP 429")
        data = resp.get_json()
        assert code == 503, code
        assert data["error"] == S.BUSY_MSG
        assert "InvalidStatus" not in data["error"]
        resp2, code2 = web.public_engine_error("not enough history for veranta|USDJPY (0 bars)")
        assert code2 == 422 and "not enough history" in resp2.get_json()["error"]


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, str(e)[:400])
            except Exception as e:
                fails += 1
                print("ERROR", name, type(e).__name__, str(e)[:400])
    sys.exit(1 if fails else 0)
