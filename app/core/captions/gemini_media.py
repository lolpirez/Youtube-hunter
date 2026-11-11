"""app.core.captions.gemini_media - YouTube Hunter.

Gemini as ears: audio understanding (transcribe the speech for captions),
over plain REST with the key from Settings. (v2.0: the TTS voices went with
the AI dub.)

v1.4.5 "fail fast":
  * a rejected key (401), a refused permission (403), a TLS failure, no
    network at all (DNS / refused / unreachable) and a connection that
    kept dropping after its retries raise GeminiFatal at once, with a
    message that says what to fix. It is a RuntimeError, so every existing
    `except RuntimeError` / UI handler still shows it; callers that used to
    swallow per-slice errors now let it through instead of grinding on.
  * listen_json checks the slice before it is Base64-encoded: empty files
    are refused, anything that is not the light 16 kHz mono Opus/MP3 (or is
    over the inline budget) is re-encoded to 16 kHz mono Opus first, and a
    slice that is still too big is refused instead of blowing up memory.
"""

import base64
import io
import json
import re
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request
import wave

from app.core.net import is_dropped_connection, request_with_retry

DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
# audio understanding (transcription / performance read)
LISTEN_MODEL_DEFAULT = "gemini-2.5-flash"



class GeminiUnavailable(RuntimeError):
    pass


class GeminiFatal(RuntimeError):
    """v1.4.5: an error no retry and no fallback can fix: the key was
    rejected (401), access was refused (403), TLS failed, or the network is
    down / kept dropping. Raised at once so a job stops with a clear message
    instead of failing three stages later. `kind` is "auth" | "network"."""

    def __init__(self, msg, kind="network", code=0, model=""):
        super().__init__(msg)
        self.kind = kind
        self.code = code
        self.model = model


def _log(text):
    """Best-effort line in crash.log (never raises, never blocks a job)."""
    try:
        from app.crash import log_crash
        log_crash("[gemini] " + text)
    except Exception:                    # noqa: BLE001
        pass


# winerror / errno values that mean "no route at all", not a blip
_NO_NET_CODES = {10051, 10060, 10061, 10065, 101, 111, 113}


def _no_network(reason):
    if isinstance(reason, (socket.gaierror, ConnectionRefusedError)):
        return True
    if isinstance(reason, OSError):
        return (getattr(reason, "winerror", None) in _NO_NET_CODES
                or getattr(reason, "errno", None) in _NO_NET_CODES)
    return False


def fatal_from(e, model=""):
    """GeminiFatal for an exception that no retry can fix, else None."""
    if isinstance(e, GeminiFatal):
        return e
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 401:
            return GeminiFatal(
                f"Gemini rejected the API key (401): {_http_msg(e)}. Check the key in "
                f"Settings → AI Providers → Gemini.", "auth", 401, model)
        if e.code == 403:
            return GeminiFatal(
                f"Gemini refused access (403) for {model or 'this model'}: {_http_msg(e)}. "
                f"The key may lack permission, or the Generative Language API isn't enabled "
                f"for its project.", "auth", 403, model)
        return None
    reason = e.reason if isinstance(e, urllib.error.URLError) else e
    if isinstance(reason, ssl.SSLError) and not is_dropped_connection(reason):
        return GeminiFatal(
            f"Secure connection to Gemini failed ({reason}). A proxy, VPN or antivirus "
            f"is intercepting HTTPS, or the system clock is wrong.", "network", 0, model)
    if _no_network(reason):
        return GeminiFatal(
            f"Can't reach Gemini ({reason}). Check the internet connection, proxy or VPN.",
            "network", 0, model)
    # request_with_retry already retried a dropped connection: it is persistent
    cause = getattr(e, "__cause__", None)
    if isinstance(e, RuntimeError) and cause is not None and is_dropped_connection(cause):
        return GeminiFatal(str(e), "network", 0, model)
    return None


class QuotaExhausted(RuntimeError):
    """Gemini said the quota for this model is used up (per-day, or a
    per-minute limit that didn't clear after waiting). Retrying would only
    burn time: the caller stops the job and keeps what it already has."""

    def __init__(self, msg, model=""):
        super().__init__(msg)
        self.model = model


# ── shared request gate ──────────────────────────────────────────────────
#
# v1.3.1: every Gemini call from every Voice Studio job goes through one gate.
# Before, each job ran its own 3 TTS threads and every thread retried 429s on
# its own, so two dubs at once meant 6 threads hammering a 3-10 RPM free tier,
# each 429 answered with more requests. Now:
#   * at most `YH_GEMINI_PARALLEL` (default 4) requests are in flight app-wide;
#   * a per-minute 429 puts the WHOLE gate in cooldown for Google's retryDelay
#     and drops parallelism by one (min 1), so we stop asking until it clears;
#   * successes slowly win the parallelism back.

class _Gate:
    def __init__(self, limit):
        self.max = max(1, limit)
        self.limit = self.max
        self.active = 0
        self.cool_until = 0.0
        self.ok_streak = 0
        self.cv = threading.Condition()

    def acquire(self, should_cancel=None):
        with self.cv:
            while True:
                if should_cancel and should_cancel():
                    raise RuntimeError("cancelled")
                wait = self.cool_until - time.monotonic()
                if wait <= 0 and self.active < self.limit:
                    self.active += 1
                    return
                self.cv.wait(timeout=min(0.5, wait) if wait > 0 else 0.5)

    def release(self, ok=True):
        with self.cv:
            self.active = max(0, self.active - 1)
            if ok:
                self.ok_streak += 1
                if self.ok_streak >= 8 and self.limit < self.max:
                    self.limit += 1
                    self.ok_streak = 0
            self.cv.notify_all()

    def throttle(self, seconds):
        with self.cv:
            self.cool_until = max(self.cool_until, time.monotonic() + seconds)
            self.limit = max(1, self.limit - 1)
            self.ok_streak = 0
            self.cv.notify_all()


try:
    _PARALLEL = int(os.environ.get("YH_GEMINI_PARALLEL", "4"))
except ValueError:
    _PARALLEL = 4
GATE = _Gate(_PARALLEL)

# models that answered "quota used up" this session -> monotonic time we may
# try them again (per-day quotas reset at midnight Pacific; we re-probe hourly)
_EXHAUSTED = {}
_EXHAUSTED_LOCK = threading.Lock()


def _mark_exhausted(model, seconds=3600):
    with _EXHAUSTED_LOCK:
        _EXHAUSTED[model] = time.monotonic() + seconds


def is_exhausted(model):
    with _EXHAUSTED_LOCK:
        t = _EXHAUSTED.get(model)
        if t and t > time.monotonic():
            return True
        _EXHAUSTED.pop(model, None)
        return False


# ── credentials ──────────────────────────────────────────────────────────

class ListenUnsupported(RuntimeError):
    """v1.4.9: the provider picked for Captions can't take audio input."""


def _gemini_creds(p, state, model=""):
    return {"shape": "gemini",
            "api_key": p["api_key"].strip(),
            "base_url": (p.get("base_url") or "").strip().rstrip("/") or DEFAULT_BASE,
            "proxy": state.get("proxy", "") or "",
            "model": (model or (p.get("model") or "").strip() or LISTEN_MODEL_DEFAULT),
            "name": p.get("name") or "Gemini"}


def _first_gemini(state):
    cands = [p for p in state["profiles"] if p.get("kind") == "gemini" and (p.get("api_key") or "").strip()]
    cands += [p for p in state["profiles"]
              if p.get("kind") == "custom" and p.get("api_mode") == "google_genai"
              and (p.get("api_key") or "").strip()]
    return cands[0] if cands else None


def _openai_creds(r, name, why=""):
    return {"shape": "openai", "resolved": r, "model": r.get("model") or "",
            "proxy": r.get("proxy") or "", "name": name, "why": why}


def _native_base(base_url):
    """v1.4.10: Gemini's own API on an OpenAI-compatible router.
    "https://api.example.com/v1" -> "https://api.example.com/v1beta". Most
    gateways that resell Gemini (new-api / one-api style) serve Google's
    generateContent shape there as well, which the voices (TTS) need."""
    u = (base_url or "").strip().rstrip("/")
    for tail in ("/v1/openai", "/openai/v1", "/openai", "/v1", "/api/v1"):
        if u.lower().endswith(tail):
            u = u[: -len(tail)]
            break
    return u + "/v1beta"


def listen_candidates(feature="captions", cfg=None):
    """v1.4.10 "ears and brain": every way the captions can HEAR the audio,
    best first, as creds dicts for listen_json().

    The model picked for Captions always translates. It also listens when it
    can hear (hearing.can_hear); when it can't (DeepSeek, Claude, Llama...),
    the listening is borrowed, in this order:
      1. the picked model itself, when it can hear
      2. a model of the SAME provider that can hear (a router that sells
         DeepSeek usually sells Gemini Flash too: same key, same bill)
      3. any Gemini profile with a key (built-in or a google_genai router)
      4. any other OpenAI-compatible provider that lists a hearing model
      5. last resort: the pick as-is, when nothing else exists and it isn't
         a known text-only family (its error then says it can't hear)
    Before, a deaf pick got the audio anyway; the router answered "busy" and
    the job asked to be retried forever."""
    from app.core.ai import hearing, profiles
    if cfg is None:
        from app.config import load_app_config
        cfg = load_app_config()
    state = profiles.load_state(cfg)
    r = profiles.resolve(feature, cfg)
    p = profiles.find(state, r["profile_id"])
    name = r.get("profile_name") or "AI"
    pick_model = r.get("model") or ""
    why = f"{hearing.short(pick_model) or name} can't hear audio"
    out, seen = [], set()

    def add(c):
        k = (c.get("shape"), c.get("name"), c.get("model"))
        if k not in seen:
            seen.add(k)
            out.append(c)

    openai_shaped = (r.get("api_mode") or "openai_compatible") == "openai_compatible" \
        and r.get("provider") != "anthropic"
    ready = profiles.is_ready(r)
    # v1.4.12: Google's OWN Gemini first. A Gemini model bought through a
    # custom provider / router takes the audio badly (its captions came
    # back wrong), so routers only listen when Google's Gemini isn't set up.
    own = (p or {}).get("kind") == "gemini"
    if not own:
        why = (f"{hearing.short(pick_model) or name} on {name} doesn't listen to audio "
               f"(only Google's own Gemini does)")
    # 1. the pick, when it IS Google's Gemini
    if own and (p.get("api_key") or "").strip():
        add(_gemini_creds(p, state, pick_model))
    # 2. Google Gemini profiles with a key
    for g in [q for q in state["profiles"] if q.get("kind") == "gemini"
              and (q.get("api_key") or "").strip()]:
        add(dict(_gemini_creds(g, state), why=why))
    # 3. fallbacks: the custom pick itself, its provider's best ear, then
    #    Gemini-native routers (google_genai)
    if not own and profiles.gemini_capable(p) and (p.get("api_key") or "").strip():
        add(dict(_gemini_creds(p, state, pick_model), why=why))
    elif not own and openai_shaped and ready and hearing.can_hear(pick_model):
        add(_openai_creds(r, name, why))
    if p is not None and openai_shaped and ready and not profiles.gemini_capable(p):
        m = hearing.best_ear(list(p.get("picked") or []) + list(p.get("models") or []))
        if m and m != pick_model:
            add(_openai_creds(dict(r, model=m), name, why))
    for g in [q for q in state["profiles"] if profiles.gemini_capable(q)
              and q.get("kind") != "gemini" and (q.get("api_key") or "").strip()]:
        add(dict(_gemini_creds(g, state), why=why))
    # 4. other OpenAI-compatible providers with a hearing model
    for q in state["profiles"]:
        if p is not None and q.get("id") == p.get("id"):
            continue
        if profiles.gemini_capable(q) or profiles.provider_of(q) == "anthropic":
            continue
        if (q.get("api_mode") or "openai_compatible") != "openai_compatible":
            continue
        if not profiles._usable(q):
            continue
        m = hearing.best_ear(list(q.get("picked") or []) + list(q.get("models") or []))
        if m:
            add(_openai_creds(profiles.resolve_profile(state, q, m), q.get("name") or "AI", why))
    # 5. nothing hears for sure: try the pick itself, unless it's a family
    #    that surely can't (no point uploading audio to DeepSeek)
    if not out and openai_shaped and ready and pick_model and not hearing.known_deaf(pick_model):
        add(_openai_creds(r, name))
    return out


def captions_listen(cfg=None):
    """v1.4.13: the Settings switch "Gemini listens to the audio" (Captions).

    On (default): Google's own Gemini hears the file and writes the lines.
    Off: a YouTube-linked file uses YouTube's own captions and the Captions
    model only makes them fluent / translates them (the custom-model flow).
    Local files and videos with no captions are still heard: there are no
    other words to work from."""
    try:
        if cfg is None:
            from app.config import load_app_config
            cfg = load_app_config()
        return cfg.get("captions_listen", True) is not False
    except Exception:                    # noqa: BLE001
        return True


def pick_hears(feature="captions", cfg=None, honor_switch=True):
    """Should the model PICKED for this feature listen to the audio itself?

    v1.4.12: only Google's OWN Gemini (the built-in Gemini profile with a
    key) does. Anything on a custom provider, a Gemini model bought through
    a router included, gets YouTube's captions first and makes them fluent
    / translates them like every other model: router Gemini was sent the
    audio and its captions came back wrong. (v1.4.11 also let any "gemini*"
    name on a router listen.) On any doubt (config unreadable) True, which
    keeps the old behaviour."""
    from app.core.ai import profiles
    try:
        if cfg is None:
            from app.config import load_app_config
            cfg = load_app_config()
        if honor_switch and feature == "captions" and not captions_listen(cfg):
            return False             # v1.4.13: listening switched off in Settings
        state = profiles.load_state(cfg)
        r = profiles.resolve(feature, cfg)
        p = profiles.find(state, r["profile_id"])
        return bool(p) and p.get("kind") == "gemini" and bool((p.get("api_key") or "").strip())
    except Exception:                    # noqa: BLE001
        return True


def credentials(cfg=None, feature=None):
    """Who listens to the audio, as a creds dict for listen_json().

      * captions (any non Gemini-only feature): the first of
        listen_candidates(), so a pick that can't hear is never sent audio
      * dub: the route's Google Gemini profile (v1.4.11: Gemini only, the
        custom-router bridge is gone: routers don't serve Gemini's TTS)
      * feature=None: the first Gemini profile with a key"""
    from app.core.ai import hearing, profiles
    if cfg is None:
        from app.config import load_app_config
        cfg = load_app_config()
    state = profiles.load_state(cfg)
    if feature and feature not in profiles.GEMINI_ONLY:
        cands = listen_candidates(feature, cfg)
        if cands:
            return cands[0]
        r = profiles.resolve(feature, cfg)
        who = " · ".join(x for x in (r.get("profile_name") or "", hearing.short(r.get("model"))) if x)
        raise ListenUnsupported(
            f"{who or 'This model'} can't hear audio, and none of your providers has a model "
            f"that can. Add a Gemini model (Settings → AI Providers) or use YouTube's captions "
            f"or a subtitle file; {who or 'it'} still translates them.")
    if feature:
        route = state["routes"].get(feature) or {}
        p = profiles.gemini_for(state, route)
        if profiles.dub_capable(p) and (p.get("api_key") or "").strip():
            pin = (route.get("model") or "").strip() if route.get("profile") in ("", p["id"]) else ""
            if pin and route.get("profile") == "" and pin not in profiles.allowed_models(p):
                pin = ""
            return _gemini_creds(p, state, pin)
        raise GeminiUnavailable(
            "The AI dub needs a Google Gemini key (free from Google AI Studio). "
            "Add it in Settings → AI Providers → Gemini.")
    g = _first_gemini(state)
    if g is None:
        raise GeminiUnavailable(
            "This needs a Gemini model: a Google Gemini key (free from Google AI Studio), "
            "set in Settings → AI Providers.")
    return _gemini_creds(g, state)


def can_listen(feature="captions", cfg=None):
    """v1.4.9: True when "AI listens" can run for this feature."""
    try:
        credentials(cfg, feature=feature)
        return True
    except Exception:                    # noqa: BLE001
        return False


def listen_label(feature="captions", cfg=None):
    """"Apinex · gemini-3.8-flash": who will listen, for the UI."""
    from app.core.ai import hearing
    try:
        c = credentials(cfg, feature=feature)
    except Exception:                    # noqa: BLE001
        return ""
    return " · ".join(x for x in (c.get("name") or "", hearing.short(c.get("model"))) if x)


def listen_note(feature="captions", cfg=None):
    """v1.4.10: one line for Settings when the listener isn't the pick:
    "deepseek-chat can't hear audio, so Apinex · gemini-3.8-flash listens
    and deepseek-chat translates". "" when the pick does both."""
    from app.core.ai import hearing, profiles
    try:
        r = profiles.resolve(feature, cfg)
        c = credentials(cfg, feature=feature)
    except ListenUnsupported as e:
        return str(e)
    except Exception:                    # noqa: BLE001
        return ""
    if not c.get("why"):
        return ""
    ear = " · ".join(x for x in (c.get("name") or "", hearing.short(c.get("model"))) if x)
    brain = hearing.short(r.get("model")) or r.get("profile_name") or "it"
    # v1.4.11: YouTube's own captions come first for a YouTube video
    # v1.4.12: ...and the pick makes them fluent, not only translates them
    return (f"{c['why']}: YouTube videos use YouTube's captions and {brain} makes them fluent "
            f"and translates them. Other files (or a video with no captions) are heard by {ear}.")


def has_gemini(cfg=None):
    """True when the dub can run (a Google Gemini key is set)."""
    try:
        credentials(cfg, feature="dub")
        return True
    except Exception:                    # noqa: BLE001
        return False


# ── HTTP ─────────────────────────────────────────────────────────────────

def _post(creds, url, body, timeout=180):
    payload = json.dumps(body).encode("utf-8")

    headers = {"Content-Type": "application/json", "x-goog-api-key": creds["api_key"]}
    if creds.get("bearer"):
        # v1.4.10: routers serving Gemini's shape read the key from either
        headers["Authorization"] = "Bearer " + creds["api_key"]

    def build():
        return urllib.request.Request(url, data=payload, method="POST", headers=headers)
    raw = request_with_retry(build, timeout, creds.get("proxy") or None, provider_label="Gemini")
    text = raw.decode("utf-8", "replace").strip()
    try:
        return json.loads(text)
    except ValueError as e:
        # v1.4.11: an empty body / an HTML page (a router or proxy in the way)
        # used to surface as "Expecting value: line 1 column 1 (char 0)"
        who = creds.get("name") or "Gemini"
        head = re.sub(r"\s+", " ", text)[:40]
        raise RuntimeError(
            f"{who} sent back an empty or non-JSON reply"
            + (f" (starts with “{head}”)" if head else "")
            + ". Check its API host in Settings → AI Providers.") from e


def _err_body(e):
    """The JSON error body of an HTTPError. Read once and kept on the error:
    the stream can only be consumed once and several places want it."""
    if not hasattr(e, "_yh_body"):
        try:
            e._yh_body = json.loads(e.read().decode("utf-8"))
        except Exception:                # noqa: BLE001
            e._yh_body = {}
    return e._yh_body or {}


def _http_msg(e):
    body = _err_body(e)
    return (body.get("error") or {}).get("message") or str(e)


def _parse_delay(v):
    try:
        return float(str(v).strip().rstrip("s"))
    except (TypeError, ValueError):
        return 0.0


def quota_kind(e):
    """('day' | 'minute', retry_delay_s) for a 429."""
    err = _err_body(e).get("error") or {}
    msg = str(err.get("message") or "").lower()
    delay, ids = 0.0, []
    for d in err.get("details") or []:
        if not isinstance(d, dict):
            continue
        if "retryDelay" in d:
            delay = _parse_delay(d.get("retryDelay"))
        for v in d.get("violations") or []:
            if isinstance(v, dict):
                ids.append(str(v.get("quotaId") or "") + " " + str(v.get("quotaMetric") or ""))
    try:
        ra = float(e.headers.get("Retry-After") or 0) if getattr(e, "headers", None) else 0.0
    except (TypeError, ValueError):
        ra = 0.0
    delay = delay or ra
    blob = " ".join(ids).lower() + " " + msg
    if "perday" in blob.replace(" ", "") or "per day" in blob or "daily" in blob or "limit: 0" in blob:
        return "day", delay
    return "minute", delay


def _with_retry(fn, attempts=3, should_cancel=None, model=""):
    """One request through the shared gate.

    * 429 per-day / "limit: 0"      -> QuotaExhausted at once (no retries: each
                                       one would be refused and still cost time)
    * 429 per-minute                -> whole app cools down for Google's
                                       retryDelay (capped at 65 s), then retries
    * 500/502/503/504               -> short backoff, retry (TTS sometimes 500s
                                       when it produced text instead of audio)
    """
    last = None
    for i in range(attempts):
        if should_cancel and should_cancel():
            raise RuntimeError("cancelled")
        GATE.acquire(should_cancel)
        ok = False
        try:
            # v1.5: every attempt that leaves the app is counted (Google
            # counts refused attempts against the daily limit too)
            try:
                from app.core.captions import gemini_budget as _B
                _B.note(model)
            except Exception:            # noqa: BLE001 - counting never fails a job
                pass
            out = fn()
            ok = True
            return out
        except urllib.error.HTTPError as e:
            last = e
            fatal = fatal_from(e, model)
            if fatal:
                _log(f"fatal {e.code} from {model or 'gemini'}: {_http_msg(e)}")
                raise fatal from e
            if e.code == 429:
                kind, delay = quota_kind(e)
                if kind == "day":
                    _mark_exhausted(model)
                    raise QuotaExhausted(
                        f"Gemini quota for {model or 'this model'} is used up: {_http_msg(e)}",
                        model) from e
                if i + 1 >= attempts:
                    _mark_exhausted(model, 120)
                    raise QuotaExhausted(
                        f"Gemini keeps rate-limiting {model or 'this model'}: {_http_msg(e)}",
                        model) from e
                GATE.throttle(min(65.0, max(2.0, delay) if delay else 8.0 * (i + 1)))
                continue
            if e.code in (500, 502, 503, 504) and i + 1 < attempts:
                time.sleep(1.5 * (i + 1))
                continue
            raise
        except (QuotaExhausted, GeminiFatal):
            raise
        except Exception as e:           # noqa: BLE001 — classified, then re-raised
            fatal = fatal_from(e, model)
            if fatal:
                _log(f"fatal network error from {model or 'gemini'}: {e!r}")
                raise fatal from e
            raise
        finally:
            GATE.release(ok)
    raise last


def _json_from_text(text):
    """v1.4.11: same tolerant reader as the translator (think blocks, prose
    around the JSON, several values). A reply with no JSON at all says so in
    words instead of "Expecting value: line 1 column 1 (char 0)"."""
    from app.core.captions.translate import _json, NotJSON
    try:
        return _json(text)
    except NotJSON as e:
        raise RuntimeError("The listening model answered, but not with the timed lines "
                           "captions need (JSON). Press Resume to try that slice again.") from e


# ── audio understanding ──────────────────────────────────────────────────

def _thinking_for(model):
    m = (model or "").lower()
    if "2.5" in m and "flash" in m:
        return {"thinkingBudget": 0}
    if "2.5" in m and "pro" in m:
        return {"thinkingBudget": 128}        # pro can't switch it off
    if m.startswith("gemini-3") or "/gemini-3" in m:
        # v1.4.9: transcription needs no reasoning. Flash takes "minimal"
        # (the cheapest level); Pro's floor is "low". A proxy that rejects
        # either gets the plain body (see listen_json).
        return {"thinkingLevel": "low" if "pro" in m else "minimal"}
    return None


# v1.4.5: inline audio budget. Gemini caps an inline request at ~20 MB and
# Base64 adds a third, so raw audio stays under 14 MB. A 10-minute slice of
# the 16 kHz mono Opus that render.extract_audio writes is ~2.4 MB.
_INLINE_MAX = 14 * 1024 * 1024
_LIGHT_EXT = (".ogg", ".opus", ".mp3")
# a light file this big for a listening slice means the encode went wrong
_LIGHT_SANE = 6 * 1024 * 1024


def _light_audio(audio_path, should_cancel=None):
    """(path, is_temp) of a file that is safe to inline. Uncompressed or
    oversized audio is re-encoded to 16 kHz mono Opus first."""
    try:
        size = os.path.getsize(audio_path)
    except OSError as e:
        raise RuntimeError(f"Audio slice is missing: {e}") from e
    if size <= 0:
        raise RuntimeError("Audio slice is empty (the extraction produced no audio).")
    light = audio_path.lower().endswith(_LIGHT_EXT)
    if light and size <= _LIGHT_SANE:
        return audio_path, False
    from app.core.captions import render as R          # lazy: no import cycle
    tmp = os.path.splitext(audio_path)[0] + f".{threading.get_ident()}.light.ogg"
    out, err = R.extract_audio(audio_path, tmp, should_cancel=should_cancel)
    if not out or not os.path.isfile(out) or os.path.getsize(out) <= 0:
        if light and size <= _INLINE_MAX:
            return audio_path, False                    # odd but still fits
        raise RuntimeError("Couldn't re-encode the audio slice to Opus: " + (err or "unknown error"))
    _log(f"listen_json: re-encoded {os.path.basename(audio_path)} "
         f"({size // 1024} KB) to {os.path.getsize(out) // 1024} KB")
    return out, True


def listen_json(creds, audio_path, prompt, system=None, model=None, timeout=300,
                should_cancel=None, max_tokens=32768):
    """Send an audio file + instructions, get JSON back."""
    if creds.get("listen"):
        # v1.4.10: a bridged router listens through its chat endpoint
        creds = creds["listen"]
    if creds.get("shape") == "openai":
        return _listen_openai(creds, audio_path, prompt, system, timeout, should_cancel)
    path, is_tmp = _light_audio(audio_path, should_cancel)
    try:
        size = os.path.getsize(path)
        if size > _INLINE_MAX:
            raise RuntimeError(
                f"Audio slice is too large to send ({size / 1048576:.1f} MB, "
                f"limit {_INLINE_MAX / 1048576:.0f} MB).")
        with open(path, "rb") as f:
            data = f.read()
        mime = "audio/ogg" if path.lower().endswith((".ogg", ".opus")) else (
            "audio/mpeg" if path.lower().endswith(".mp3") else "audio/wav")
        b64 = base64.b64encode(data).decode("ascii")
        del data                                        # don't hold raw + Base64
    finally:
        if is_tmp:
            try:
                os.remove(path)
            except OSError:
                pass
    m = (model or creds.get("model") or LISTEN_MODEL_DEFAULT)
    if "tts" in m:
        m = LISTEN_MODEL_DEFAULT
    url = f"{creds['base_url']}/models/{m}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [
            {"inlineData": {"mimeType": mime, "data": b64}},
            {"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens,
                             "responseMimeType": "application/json"},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    # v1.3.1: listening + writing JSON needs no "thinking". Left on, 2.5/3.x
    # models spend thousands of hidden thought tokens per slice (billed as
    # output, and slow). A proxy that rejects the field gets the plain body.
    think = _thinking_for(m)
    if think:
        body["generationConfig"]["thinkingConfig"] = think
    try:
        try:
            payload = _with_retry(lambda: _post(creds, url, body, timeout), 3, should_cancel, m)
        except urllib.error.HTTPError as e:
            if think and e.code == 400:
                body["generationConfig"].pop("thinkingConfig", None)
                payload = _with_retry(lambda: _post(creds, url, body, timeout), 3, should_cancel, m)
            else:
                raise
    except urllib.error.HTTPError as e:
        fatal = fatal_from(e, m)
        if fatal:
            raise fatal from e
        raise RuntimeError(f"Gemini ({m}) error {e.code}: {_http_msg(e)}") from e
    cands = payload.get("candidates") or []
    if not cands:
        reason = (payload.get("promptFeedback") or {}).get("blockReason") or "empty reply"
        raise RuntimeError(f"Gemini returned nothing ({reason}).")
    text = "".join(p.get("text", "") for p in (cands[0].get("content") or {}).get("parts", [])
                   if not p.get("thought"))
    return _json_from_text(text)


# v1.4.9: profile+model pairs that answered "no audio here" this session,
# so the next job fails at once instead of uploading another slice
_NO_AUDIO = set()
_AUDIO_REFUSAL = ("input_audio", "audio", "modalit", "unsupported content", "content type",
                  "invalid content", "multimodal", "image_url", "not supported")


def _listen_openai(creds, audio_path, prompt, system=None, timeout=300, should_cancel=None):
    """Audio understanding through an OpenAI-compatible chat/completions
    endpoint: the slice goes in as an `input_audio` part (16 kHz mono MP3,
    the format every OpenAI-shaped gateway accepts), the reply is JSON.

    Gemini models served through OpenAI-compatible routers and OpenAI's own
    audio models take this shape. A model that can't hear answers 400; that
    becomes ListenUnsupported (named, short), remembered for the session."""
    from app.core.ai import profiles
    from app.core.captions import render as R          # lazy: no import cycle
    r = dict(creds["resolved"])
    key = (r.get("profile_id") or "", r.get("model") or "")
    name = creds.get("name") or "AI"
    if key in _NO_AUDIO:
        raise ListenUnsupported(f"{name} · {r.get('model') or '?'} can't listen to audio.")
    if should_cancel and should_cancel():
        raise RuntimeError("cancelled")
    tmp = os.path.splitext(audio_path)[0] + f".{threading.get_ident()}.listen.mp3"
    out, err = R.extract_audio(audio_path, tmp, codec="mp3", should_cancel=should_cancel)
    if not out or not os.path.isfile(out) or os.path.getsize(out) <= 0:
        raise RuntimeError("Couldn't prepare the audio slice: " + (err or "unknown error"))
    try:
        size = os.path.getsize(out)
        if size > _INLINE_MAX:
            raise RuntimeError(f"Audio slice is too large to send ({size / 1048576:.1f} MB).")
        with open(out, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
    finally:
        try:
            os.remove(out)
        except OSError:
            pass
    client = profiles.client_from(r)
    # transcription is pattern work: no hidden reasoning tokens
    client.effort = "none"
    parts = [{"type": "text", "text": prompt},
             {"type": "input_audio", "input_audio": {"data": b64, "format": "mp3"}}]
    try:
        text = client.generate(parts, system, timeout=timeout, max_output_tokens=8192,
                               temperature=0.2)
    except RuntimeError as e:
        msg = str(e)
        low = msg.lower()
        m = re.search(r"\((\d{3})\)", msg)
        code = int(m.group(1)) if m else 0
        if code in (400, 404, 415, 422) and any(w in low for w in _AUDIO_REFUSAL):
            _NO_AUDIO.add(key)
            raise ListenUnsupported(
                f"{name} · {r.get('model') or '?'} can't listen to audio. Pick a Gemini model "
                f"for Captions, or use YouTube's own captions.") from e
        # v1.4.10: a router handed audio for a model that can't hear rarely
        # says so: it answers 503 / "no available channel" / "busy" instead.
        # For a model that isn't known to hear, that IS the refusal: never
        # report it as "Provider is busy, retry".
        from app.core.ai import hearing
        if not hearing.can_hear(r.get("model")) and (
                code in (404, 500, 502, 503, 504, 529)
                or any(w in low for w in ("no available", "channel", "busy", "overload",
                                          "not found", "does not exist", "unavailable"))):
            _NO_AUDIO.add(key)
            raise ListenUnsupported(
                f"{name} · {r.get('model') or '?'} can't listen to audio.") from e
        if code == 429 and ("day" in low or "daily" in low or "free" in low):
            raise QuotaExhausted(msg, r.get("model") or "") from e
        raise
    return _json_from_text(text)
