"""app.core.captions.gemini_budget - YouTube Hunter.

v1.5 "Quota saver": how many Gemini requests the app really sent today.

Google AI Studio's free tier is counted in REQUESTS PER DAY per model (about
20 for the Flash text / listening models, fewer for the TTS models), and the
day resets at midnight Pacific time. Every request that reaches Google goes
through gemini_media._with_retry, which calls note() once per attempt, so
this ledger matches what Google counts (a refused attempt can still count).

  note(model)            one request was sent to `model`
  today()                {model: n} for the current Pacific day
  total()                sum of today()
  family(model)          "voice" (TTS models) | "text" (listen / translate)
  summary()              {"text": n, "voice": n, "total": n, "resets_in": s}
  snapshot() / since(s)  requests sent between two moments (one job's cost)
  saver_on(cfg)          the Settings switch (default ON)

The ledger is a tiny JSON file next to config.json. Writes are debounced
and never raise: counting must never be the reason a dub fails.
"""

import json
import os
import threading
import time

_LOCK = threading.Lock()
_STATE = {"day": "", "models": {}}
_LOADED = [False]
_SAVE_TIMER = [None]
# a monotonically growing counter for snapshot()/since() (not reset at midnight)
_SEQ = [0]


def _path():
    try:
        from app.config import CONFIG_PATH
        return os.path.join(os.path.dirname(CONFIG_PATH), "gemini_usage.json")
    except Exception:                    # noqa: BLE001
        import tempfile
        return os.path.join(tempfile.gettempdir(), "yh_gemini_usage.json")


def _pacific_day(now=None):
    """YYYY-MM-DD in America/Los_Angeles (Google's quota day)."""
    now = time.time() if now is None else now
    try:
        from zoneinfo import ZoneInfo
        import datetime as _dt
        return _dt.datetime.fromtimestamp(now, ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:                    # noqa: BLE001 - no tz database (frozen build)
        # PST / PDT by the US rule: 2nd Sunday of March .. 1st Sunday of November
        t = time.gmtime(now)
        y = t.tm_year

        def sunday(month, nth):
            import calendar
            days = [d for d in range(1, 32)
                    if d <= calendar.monthrange(y, month)[1] and calendar.weekday(y, month, d) == 6]
            return days[nth - 1]
        import calendar as _c
        start = _c.timegm((y, 3, sunday(3, 2), 10, 0, 0))     # 02:00 PST = 10:00 UTC
        end = _c.timegm((y, 11, sunday(11, 1), 9, 0, 0))      # 02:00 PDT = 09:00 UTC
        off = -7 * 3600 if start <= now < end else -8 * 3600
        return time.strftime("%Y-%m-%d", time.gmtime(now + off))


def _seconds_to_reset(now=None):
    now = time.time() if now is None else now
    day = _pacific_day(now)
    # walk forward in 10-minute steps until the Pacific date changes (cheap,
    # at most 144 steps, and right across DST changes)
    t = now
    for _ in range(160):
        t += 600
        if _pacific_day(t) != day:
            # refine to the minute
            lo = t - 600
            while lo < t and _pacific_day(lo + 60) == day:
                lo += 60
            return max(0, int(lo + 60 - now))
    return 0


def _load():
    if _LOADED[0]:
        return
    _LOADED[0] = True
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("models"), dict):
            _STATE["day"] = str(data.get("day") or "")
            _STATE["models"] = {str(k): int(v) for k, v in data["models"].items()}
    except Exception:                    # noqa: BLE001
        pass


def _roll():
    day = _pacific_day()
    if _STATE["day"] != day:
        _STATE["day"] = day
        _STATE["models"] = {}


def _write():
    with _LOCK:
        _SAVE_TIMER[0] = None
        blob = json.dumps(_STATE, ensure_ascii=False)
    try:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(blob)
        os.replace(tmp, _path())
    except Exception:                    # noqa: BLE001
        pass


def _schedule_write():
    if _SAVE_TIMER[0] is None:
        t = threading.Timer(1.5, _write)
        t.daemon = True
        _SAVE_TIMER[0] = t
        t.start()


def note(model):
    """One request went out to `model`."""
    m = str(model or "gemini")
    with _LOCK:
        _load()
        _roll()
        _STATE["models"][m] = _STATE["models"].get(m, 0) + 1
        _SEQ[0] += 1
        _schedule_write()


def today():
    with _LOCK:
        _load()
        _roll()
        return dict(_STATE["models"])


def total():
    return sum(today().values())


def family(model):
    return "voice" if "tts" in str(model or "").lower() else "text"


def summary():
    t = today()
    text = sum(n for m, n in t.items() if family(m) == "text")
    voice = sum(n for m, n in t.items() if family(m) == "voice")
    return {"text": text, "voice": voice, "total": text + voice,
            "models": t, "resets_in": _seconds_to_reset()}


def snapshot():
    with _LOCK:
        return _SEQ[0]


def since(snap):
    """Requests sent since snapshot() returned `snap` (all jobs together)."""
    with _LOCK:
        return max(0, _SEQ[0] - int(snap or 0))


def saver_on(cfg=None):
    """Settings → Captions & Dub → "Quota saver" (default ON)."""
    try:
        if cfg is None:
            from app.config import load_app_config
            cfg = load_app_config()
        return cfg.get("gemini_saver", True) is not False
    except Exception:                    # noqa: BLE001
        return True
