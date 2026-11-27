#!/usr/bin/env python3
"""Read a chart screenshot with OpenAI vision and map it to a CHRONOS setup.

The API key is read only from OPENAI_API_KEY and is never logged.
Uploads stay in memory for the life of the request and are not written to disk.
"""
import base64, os, re, json

MAX_BYTES = 5 * 1024 * 1024
PATTERN_MAX = 120
NOTES_MAX = 400
LOW_CONFIDENCE = 0.7
DEFAULT_MODEL = "gpt-4o-mini"
VISION_TIMEOUT_S = 45

# PNG, JPEG, WebP magic (WEBP is a RIFF container).
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_WEBP = (b"RIFF", b"WEBP")

# Common chart labels that are not MFP market ids.
ALIASES = {
    "GOLD": "XAU", "XAUUSD": "XAU", "XAUUS": "XAU",
    "SILVER": "XAG", "XAGUSD": "XAG",
    "BITCOIN": "BTC", "BTCUSD": "BTC", "BTCUSDT": "BTC",
    "ETHEREUM": "ETH", "ETHUSD": "ETH", "ETHUSDT": "ETH",
    "SPX": "SP500", "SPX500": "SP500", "US500": "SP500",
    "S&P": "SP500", "S&P500": "SP500", "SANDP": "SP500", "SP500": "SP500",
    "NASDAQ": "XYZ100", "NAS100": "XYZ100", "US100": "XYZ100",
    "NDX": "XYZ100", "USTEC": "XYZ100", "NDX100": "XYZ100",
    "DOW": "DJI", "DJIA": "DJI", "US30": "DJI", "DOWJONES": "DJI",
    "BRENT": "BRENTOIL", "UKOIL": "BRENTOIL", "BRENTUSD": "BRENTOIL",
    "WTI": "CL", "USOIL": "CL", "CRUDE": "CL", "CLUSD": "CL", "WTIUSD": "CL",
    "NIKKEI": "JP225", "NK225": "JP225", "JPN225": "JP225", "NIKKEI225": "JP225",
}

TF_CANON = {
    "15M": "15m", "15MIN": "15m", "M15": "15m",
    "30M": "30m", "30MIN": "30m", "M30": "30m",
    "1H": "1h", "H1": "1h", "60M": "1h", "60MIN": "1h", "1HR": "1h", "1HOUR": "1h",
    "4H": "4h", "H4": "4h",
}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "symbol": {"type": ["string", "null"]},
        "side": {"type": ["string", "null"], "enum": ["long", "short", None]},
        "timeframe": {"type": ["string", "null"]},
        "entry": {"type": ["number", "null"]},
        "take_profit": {"type": ["number", "null"]},
        "stop_loss": {"type": ["number", "null"]},
        "confidence": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "symbol": {"type": "number"},
                "side": {"type": "number"},
                "timeframe": {"type": "number"},
                "entry": {"type": "number"},
                "take_profit": {"type": "number"},
                "stop_loss": {"type": "number"},
            },
            "required": ["symbol", "side", "timeframe", "entry", "take_profit", "stop_loss"],
        },
        "notes": {"type": "string"},
    },
    "required": ["symbol", "side", "timeframe", "entry", "take_profit", "stop_loss", "confidence", "notes"],
}

SYSTEM = (
    "You read a trading-chart screenshot and extract the trade setup that is clearly visible. "
    "Return only the JSON object described by the schema. "
    "Rules: "
    "Extract the market symbol or pair, the side (long or short), the chart timeframe, "
    "and the entry, take-profit and stop-loss prices. "
    "Use only values you can actually see: price labels, order lines, a position ticket, "
    "or a clearly labelled entry / TP / SL. "
    "Do not invent, estimate, or interpolate prices. If a level is not clearly labelled, return null for that field. "
    "Do not guess a market from the candle shape alone. If the symbol is not visible, return null. "
    "Side must be \"long\" or \"short\". Infer it from a labelled long/short or buy/sell, "
    "or from the relationship of TP and SL to entry only when those three prices are all clearly visible. Otherwise null. "
    "Timeframe: copy the chart's timeframe as shown (for example 15m, 1h, 4h, D1). "
    "Confidence is 0 to 1 for each field. Use a low score when the value is blurry, cropped, or only partly visible. "
    "notes: short plain text about anything unclear. Use an empty string if there is nothing to say."
)


class ScreenshotError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def enabled():
    return bool((os.environ.get("OPENAI_API_KEY") or "").strip())


def model_name():
    return (os.environ.get("OPENAI_VISION_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def daily_limit():
    try:
        n = int(os.environ.get("RATE_SCREENSHOT_PER_DAY") or "20")
    except (TypeError, ValueError):
        n = 20
    return max(1, min(n, 1000))


def clean_pattern(v):
    if v is None:
        return ""
    s = " ".join(str(v).split())
    return s[:PATTERN_MAX]


def _clean_text(v, limit):
    if v is None:
        return ""
    s = "".join(ch for ch in str(v) if ch == "\n" or (ord(ch) >= 32 and ch != "\x7f"))
    s = " ".join(s.split())
    return s[:limit]


def compact(s):
    return re.sub(r"[^A-Z0-9&]", "", (s or "").upper())


def sniff_image(data):
    """Return a MIME type for a PNG, JPEG or WebP body, else None."""
    if not data or len(data) < 12:
        return None
    if data.startswith(_PNG):
        return "image/png"
    if data.startswith(_JPEG):
        return "image/jpeg"
    if data.startswith(_WEBP[0]) and data[8:12] == _WEBP[1]:
        return "image/webp"
    return None


def validate_image(data, filename=""):
    if data is None:
        raise ScreenshotError("Please attach a chart screenshot.")
    if not isinstance(data, (bytes, bytearray)):
        raise ScreenshotError("Please attach a chart screenshot.")
    data = bytes(data)
    if not data:
        raise ScreenshotError("Please attach a chart screenshot.")
    if len(data) > MAX_BYTES:
        raise ScreenshotError("That image is too large. Use a PNG, JPG or WebP under 5 MB.")
    mime = sniff_image(data)
    if not mime:
        name = (filename or "").lower()
        if name.endswith((".png", ".jpg", ".jpeg", ".webp")):
            raise ScreenshotError("That file is not a valid PNG, JPG or WebP image.")
        raise ScreenshotError("Please upload a PNG, JPG or WebP chart screenshot.")
    return data, mime


def normalize_side(text):
    if text is None or text == "":
        return None
    s = str(text).strip().lower()
    if s in ("long", "buy", "bull", "bullish", "longs"):
        return "long"
    if s in ("short", "sell", "bear", "bearish", "shorts"):
        return "short"
    return None


def normalize_timeframe(text):
    """Return (display, similar_tf_or_None). similar_tf is only 15m/30m/1h/4h."""
    if text is None or str(text).strip() == "":
        return None, None
    raw = str(text).strip()
    low = raw.lower().replace(" ", "")
    if low in ("15m", "30m", "1h", "4h"):
        return low, low
    key = compact(raw)
    canon = TF_CANON.get(key)
    if canon:
        return canon, canon
    return _clean_text(raw, 24), None


def clean_price(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not (f > 0) or f != f or f == float("inf"):
        return None
    return f


def match_market(name, markets):
    """Map a free-text symbol onto an MFP market row, or None. Never invents a market."""
    if not name or not str(name).strip() or not markets:
        return None
    raw = str(name).strip()
    import tester
    try:
        return tester.resolve_market(raw, markets)
    except (SystemExit, ValueError):
        pass
    key = compact(raw)
    if not key:
        return None
    alias = ALIASES.get(key)
    if alias:
        try:
            return tester.resolve_market(alias, markets)
        except (SystemExit, ValueError):
            pass
    try:
        return tester.resolve_market(key, markets)
    except (SystemExit, ValueError):
        pass
    if key.endswith("USDT") and len(key) > 4:
        try:
            return tester.resolve_market(key[:-4], markets)
        except (SystemExit, ValueError):
            pass
    if key.endswith("USD") and len(key) > 3:
        # XAUUSD already aliased; leftover like EURUSD is handled by resolve on key.
        stem = key[:-3]
        try:
            return tester.resolve_market(stem, markets)
        except (SystemExit, ValueError):
            pass
    cands = []
    for m in markets:
        coin = compact(m.get("coin", ""))
        sym = compact(m.get("symbol", ""))
        mid = compact(m.get("market_id", ""))
        coin_bare = compact((m.get("coin") or "").split(":")[-1])
        fields = {coin, sym, mid, coin_bare, coin_bare + "USDT" if coin_bare else ""}
        if key in fields or (key + "USDT") in fields:
            cands.append(m)
    if not cands:
        return None
    cands.sort(key=lambda m: (not m.get("default_venue", True),
                              compact(m.get("coin", "")) not in (key, key + "USDT"),
                              compact(m.get("symbol", "")) != key))
    return cands[0]


def _confidence(raw, present):
    src = raw if isinstance(raw, dict) else {}
    out = {}
    for k in ("symbol", "side", "timeframe", "entry", "take_profit", "stop_loss"):
        try:
            v = float(src[k]) if src.get(k) is not None and src.get(k) != "" else None
        except (TypeError, ValueError):
            v = None
        if v is None or v != v:
            out[k] = 0.5 if present.get(k) else 0.0
        else:
            out[k] = max(0.0, min(1.0, v))
    return out


def parse_vision_payload(raw):
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            raise ScreenshotError("Couldn't read a trade setup from that chart. Try a clearer screenshot with the pair and entry, TP and SL visible.", 422)
    if not isinstance(raw, dict):
        raise ScreenshotError("Couldn't read a trade setup from that chart. Try a clearer screenshot with the pair and entry, TP and SL visible.", 422)
    symbol = _clean_text(raw.get("symbol"), 40) or None
    side = normalize_side(raw.get("side"))
    tf_display, similar_tf = normalize_timeframe(raw.get("timeframe"))
    entry = clean_price(raw.get("entry"))
    tp = clean_price(raw.get("take_profit") if raw.get("take_profit") is not None else raw.get("tp"))
    sl = clean_price(raw.get("stop_loss") if raw.get("stop_loss") is not None else raw.get("sl"))
    present = {
        "symbol": bool(symbol),
        "side": bool(side),
        "timeframe": bool(tf_display),
        "entry": entry is not None,
        "take_profit": tp is not None,
        "stop_loss": sl is not None,
    }
    if not any(present[k] for k in ("symbol", "side", "entry", "take_profit", "stop_loss")):
        raise ScreenshotError("Couldn't read a trade setup from that chart. Try a clearer screenshot with the pair and entry, TP and SL visible.", 422)
    conf = _confidence(raw.get("confidence"), present)
    notes = _clean_text(raw.get("notes"), NOTES_MAX)
    return dict(symbol=symbol, side=side, timeframe=tf_display, similar_tf=similar_tf,
                entry=entry, tp=tp, sl=sl, confidence=conf, notes=notes)


def market_row(m):
    if not m:
        return None
    import tester
    fam = tester.market_family(m)
    return dict(market_id=m["market_id"], symbol=m["symbol"], coin=m["coin"],
                provider=m.get("provider"), asset_class=tester.asset_class(m),
                family=fam, family_label=tester.FAMILY_LABEL.get(fam, fam),
                default_venue=m.get("default_venue", True))


def build_response(parsed, markets, pattern=""):
    market = match_market(parsed.get("symbol"), markets) if parsed.get("symbol") else None
    conf = parsed["confidence"]
    labels = {
        "symbol": "market",
        "side": "side",
        "timeframe": "timeframe",
        "entry": "entry",
        "take_profit": "take profit",
        "stop_loss": "stop loss",
    }
    values = {
        "symbol": bool(market),
        "side": parsed.get("side") is not None,
        "timeframe": parsed.get("timeframe") is not None,
        "entry": parsed.get("entry") is not None,
        "take_profit": parsed.get("tp") is not None,
        "stop_loss": parsed.get("sl") is not None,
    }
    # Timeframe is optional for running a test; do not list it as required-missing.
    required = ("symbol", "side", "entry", "take_profit", "stop_loss")
    missing = [labels[k] for k in required if not values[k]]
    low = [labels[k] for k in required if values[k] and conf.get(k, 0) < LOW_CONFIDENCE]
    if parsed.get("timeframe") and conf.get("timeframe", 0) < LOW_CONFIDENCE:
        low.append("timeframe")
    review = "Review these fields before you run the test. CHRONOS does not start until you press Run test."
    if missing:
        review += " Missing: " + ", ".join(missing) + "."
    if low:
        review += " Check these (low confidence): " + ", ".join(low) + "."
    if parsed.get("timeframe") and not parsed.get("similar_tf"):
        review += f" Chart timeframe is {parsed['timeframe']}; the fingerprint is left at its default."
    if parsed.get("symbol") and not market:
        review += f" Could not match '{parsed['symbol']}' to an MFP market; pick one yourself."
    return dict(
        pattern=pattern,
        market=market_row(market),
        symbol_raw=parsed.get("symbol"),
        side=parsed.get("side"),
        timeframe=parsed.get("timeframe"),
        similar_tf=parsed.get("similar_tf"),
        entry=parsed.get("entry"),
        tp=parsed.get("tp"),
        sl=parsed.get("sl"),
        confidence=conf,
        low_confidence=low,
        missing=missing,
        notes=parsed.get("notes") or "",
        review=review,
    )


def _map_openai_error(e):
    """Public message only. Never include the exception text (it may contain request details)."""
    status = getattr(e, "status_code", None)
    name = type(e).__name__.lower()
    if status == 401 or "authentication" in name or "permission" in name:
        return ScreenshotError("Screenshot reading failed. Check the OpenAI API key on the server.", 502)
    if status == 429:
        return ScreenshotError("The vision service is busy, try again in a minute", 503)
    if status in (500, 502, 503, 529):
        return ScreenshotError("The vision service is unavailable, try again in a minute", 503)
    if "timeout" in name or "timedout" in name:
        return ScreenshotError("The vision service timed out, try again", 504)
    if status == 400:
        return ScreenshotError("Couldn't read a trade setup from that chart. Try a clearer screenshot with the pair and entry, TP and SL visible.", 422)
    return ScreenshotError("Couldn't read that chart, try again in a minute", 502)


def call_vision(image_bytes, mime, pattern=""):
    """Send the image to OpenAI and return the parsed model JSON (dict)."""
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise ScreenshotError("Screenshot reading isn't enabled yet", 503)
    try:
        from openai import OpenAI
    except ImportError:
        raise ScreenshotError("Screenshot reading isn't enabled yet", 503)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    user = "Extract the trade setup from this chart screenshot. Do not invent missing prices."
    if pattern:
        user += f" The trader describes this setup as: {pattern}. Use that only as context, not to invent levels."
    client = OpenAI(api_key=api_key, timeout=VISION_TIMEOUT_S)
    try:
        resp = client.chat.completions.create(
            model=model_name(),
            temperature=0,
            max_tokens=800,
            response_format={"type": "json_schema", "json_schema": {"name": "chart_setup", "strict": True, "schema": SCHEMA}},
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": user},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ]},
            ],
        )
    except ScreenshotError:
        raise
    except Exception as e:
        raise _map_openai_error(e) from None
    try:
        text = (resp.choices[0].message.content or "").strip()
    except Exception:
        raise ScreenshotError("Couldn't read a trade setup from that chart. Try a clearer screenshot with the pair and entry, TP and SL visible.", 422)
    if not text:
        raise ScreenshotError("Couldn't read a trade setup from that chart. Try a clearer screenshot with the pair and entry, TP and SL visible.", 422)
    return parse_vision_payload(text)


def extract_setup(image_bytes, filename, pattern, markets):
    data, mime = validate_image(image_bytes, filename)
    parsed = call_vision(data, mime, pattern)
    return build_response(parsed, markets, pattern)
