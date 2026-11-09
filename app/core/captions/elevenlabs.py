"""app.core.captions.elevenlabs - YouTube Hunter.

v1.5: ElevenLabs as a second voice engine for the AI dub.

Plain REST (no SDK, nothing new to bundle), with the key from Settings →
Voices & quota. The key can also come from the ELEVENLABS_API_KEY
environment variable, like the official quickstart's .env file.

  POST /v1/text-to-speech/{voice_id}?output_format=pcm_24000
       xi-api-key: <key>
       {"text", "model_id", "voice_settings", "language_code"?}

pcm_24000 is raw 16-bit mono 24 kHz PCM, the exact format the dub
assembler works in (no ffmpeg pass), and it is served on the free tier
(44.1 kHz PCM needs a paid plan).

ElevenLabs bills CHARACTERS, not requests, so lines are voiced one by one
(the free plan allows 2 requests at a time; more just queue).

Errors are mapped onto the dub's own exceptions, so the Voice Studio shows
them like Gemini's: a bad key stops at once (GeminiFatal kind "key"), a
used-up character quota raises QuotaExhausted (the job keeps every line it
already recorded and Resume continues later).
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.elevenlabs.io"

# Models offered in Settings. v4 / v3 understand [laughs]-style audio tags
# and 70-90+ languages (Persian included); multilingual v2 is the calm,
# stable fallback; flash is the cheapest (half the credits per character).
MODELS = [
    {"key": "eleven_v4", "name": "Eleven v4", "desc": "Most expressive, 90+ languages, audio tags"},
    {"key": "eleven_v3", "name": "Eleven v3", "desc": "Expressive, 70+ languages, audio tags"},
    {"key": "eleven_multilingual_v2", "name": "Multilingual v2", "desc": "Stable and natural, 29 languages"},
    {"key": "eleven_flash_v2_5", "name": "Flash v2.5", "desc": "Fastest, half the credits, 32 languages"},
]
MODEL_KEYS = [m["key"] for m in MODELS]
DEFAULT_MODEL = "eleven_v4"
_TAG_MODELS = ("eleven_v4", "eleven_v3")

# ElevenLabs' default (premade) voices. The free plan can't use the Voice
# Library through the API, but these always work; the user's own voices
# (cloned / added) are listed from the account with list_voices().
VOICES = [
    ("JBFqnCBsd6RMkjVDRZzb", "George", "male", "warm storyteller"),
    ("nPczCjzI2devNBz1zQrb", "Brian", "male", "deep, narration"),
    ("onwK4e9ZLuTAKqWW03F9", "Daniel", "male", "authoritative"),
    ("TX3LPaxmHKxFdv7VOQHJ", "Liam", "male", "young, energetic"),
    ("IKne3meq5aSn9XLyUdCD", "Charlie", "male", "casual, natural"),
    ("cjVigY5qzO86Huf0OWal", "Eric", "male", "smooth, friendly"),
    ("iP95p4xoKVk53GoZ742B", "Chris", "male", "conversational"),
    ("CwhRBWXzGAHq8TQ4Fs17", "Roger", "male", "confident"),
    ("EXAVITQu4vr4xnSDxMaL", "Sarah", "female", "soft, young"),
    ("21m00Tcm4TlvDq8ikWAM", "Rachel", "female", "calm, clear"),
    ("FGY2WhTYpPnrIDTdsKH5", "Laura", "female", "upbeat"),
    ("Xb7hH8MSUJpSbSDYk0k2", "Alice", "female", "confident, British"),
    ("XrExE9yKIg1WjnnlVkGX", "Matilda", "female", "warm, friendly"),
    ("pFZP5JQG7iQjIQuC4Bku", "Lily", "female", "warm, British"),
    ("cgSgspJ2msm6clMCkdW9", "Jessica", "female", "expressive, young"),
]
VOICE_IDS = [v[0] for v in VOICES]
_GENDER = {v[0]: v[2] for v in VOICES}
_NAME = {v[0]: v[1] for v in VOICES}

_LOCK = threading.Lock()
# at most 2 requests in flight (the free plan's concurrency); more get a 429
_SLOTS = threading.BoundedSemaphore(2)


class ElevenError(RuntimeError):
    pass


# ── settings ─────────────────────────────────────────────────────────────

def _cfg(cfg=None):
    if cfg is not None:
        return cfg
    try:
        from app.config import load_app_config
        return load_app_config()
    except Exception:                    # noqa: BLE001
        return {}


def api_key(cfg=None):
    k = str(_cfg(cfg).get("elevenlabs_api_key") or "").strip()
    return k or str(os.environ.get("ELEVENLABS_API_KEY") or "").strip()


def model(cfg=None):
    m = str(_cfg(cfg).get("elevenlabs_model") or "").strip()
    return m if m else DEFAULT_MODEL


def default_voice(cfg=None):
    v = str(_cfg(cfg).get("elevenlabs_voice") or "").strip()
    return v or VOICES[0][0]


def ready(cfg=None):
    return bool(api_key(cfg))


def engine(cfg=None):
    """The dub's voice engine: "elevenlabs" when picked AND a key exists,
    otherwise "gemini"."""
    c = _cfg(cfg)
    if str(c.get("dub_voice_engine") or "") == "elevenlabs" and api_key(c):
        return "elevenlabs"
    return "gemini"


def speaks_tags(model_id):
    return any(str(model_id or "").startswith(m) for m in _TAG_MODELS)


# ── text ─────────────────────────────────────────────────────────────────

_ANGLE = re.compile(r"<([a-z][a-z \-]{1,24})>", re.I)
# the dub's vocal events -> ElevenLabs audio tags
_TAG_MAP = {"laugh": "laughs", "chuckle": "chuckles", "giggle": "giggles", "sigh": "sighs",
            "gasp": "gasps", "breath": "exhales", "cough": "coughs", "sniff": "sniffs",
            "scoff": "scoffs", "hmm": "hmm", "short pause": "short pause",
            "long pause": "long pause"}


def prepare_text(text, model_id, style=""):
    """The line as ElevenLabs should get it. v3/v4: <laugh> -> [laughs] and
    the delivery's first cue as a leading tag ("[excited] ..."). Older
    models read tags aloud, so they are stripped."""
    t = str(text or "")
    if speaks_tags(model_id):
        t = _ANGLE.sub(lambda m: "[" + _TAG_MAP.get(m.group(1).strip().lower(), m.group(1).strip()) + "]", t)
        lead = _lead_tag(style)
        if lead and not t.lstrip().startswith("["):
            t = f"[{lead}] {t}"
    else:
        t = _ANGLE.sub(" ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _lead_tag(style):
    """"excited, fast, grinning" -> "excited". Only plain words, short."""
    s = str(style or "").split(";")[0].split(",")[0].strip().lower()
    s = re.sub(r"[^a-z \-]", "", s).strip()
    if not s or len(s) > 24 or s in ("natural", "natural and conversational", "neutral", "calm"):
        return ""
    return s


# ── HTTP ─────────────────────────────────────────────────────────────────

def _proxy():
    try:
        from app.core.net import configured_proxy
        return configured_proxy() or None
    except Exception:                    # noqa: BLE001
        return None


def _open(req, timeout):
    p = _proxy()
    if p:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}))
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _err_detail(e):
    try:
        body = json.loads(e.read().decode("utf-8", "replace"))
    except Exception:                    # noqa: BLE001
        return "", ""
    d = body.get("detail") if isinstance(body, dict) else None
    if isinstance(d, dict):
        return str(d.get("status") or d.get("code") or ""), str(d.get("message") or "")
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return "", str(d[0].get("msg") or "")
    return "", str(d or "")


def _raise_for(e, what="ElevenLabs"):
    from app.core.captions import gemini_media as G
    status, msg = _err_detail(e)
    low = (status + " " + msg).lower()
    if "quota" in low or "credit" in low or "character_limit" in low or e.code == 402:
        raise G.QuotaExhausted(
            "ElevenLabs credits are used up for this month" + (f": {msg}" if msg else "")
            + ". Everything recorded is saved: top up, or switch the dub back to Gemini voices "
              "in Settings → Captions & Dub, then press Resume.", "elevenlabs") from e
    if e.code == 401 or "invalid_api_key" in low or "unauthorized" in low:
        raise G.GeminiFatal(
            "ElevenLabs refused the API key" + (f" ({msg})" if msg else "")
            + ". Check it in Settings → Captions & Dub.", "key", 401, "elevenlabs") from e
    if "missing_permissions" in low or e.code == 403:
        raise G.GeminiFatal(
            "This ElevenLabs key isn't allowed to do that" + (f" ({msg})" if msg else "")
            + ". In ElevenLabs → API keys, give it the Text to Speech permission.",
            "key", 403, "elevenlabs") from e
    raise ElevenError(f"{what} error {e.code}" + (f": {msg}" if msg else ""))


def _request(method, path, body=None, timeout=120, key=None, accept="application/json"):
    k = key or api_key()
    if not k:
        from app.core.captions import gemini_media as G
        raise G.GeminiFatal("Add your ElevenLabs API key in Settings → Captions & Dub.",
                            "key", 0, "elevenlabs")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "xi-api-key": k, "Content-Type": "application/json", "Accept": accept,
        "User-Agent": "YouTubeHunter"})
    with _open(req, timeout) as r:
        return r.read()


# ── public ───────────────────────────────────────────────────────────────

def tts(text, out_wav, voice_id="", model_id="", lang="", style="", should_cancel=None,
        timeout=120, key=None):
    """Speak one dub line into a 24 kHz mono 16-bit WAV. Returns the model."""
    from app.core.captions import gemini_media as G
    model_id = model_id or model()
    voice_id = voice_id or default_voice()
    t = prepare_text(text, model_id, style)
    if not t:
        raise ElevenError("Nothing to say on this line.")
    body = {"text": t, "model_id": model_id,
            "voice_settings": {"stability": 0.45 if speaks_tags(model_id) else 0.5,
                               "similarity_boost": 0.75, "style": 0.0,
                               "use_speaker_boost": True}}
    lc = (lang or "").split("-")[0].lower()
    if lc and model_id != "eleven_multilingual_v2":
        body["language_code"] = lc
    path = f"/v1/text-to-speech/{urllib.parse.quote(voice_id)}?output_format=pcm_24000"
    last = None
    for attempt in range(4):
        if should_cancel and should_cancel():
            raise RuntimeError("cancelled")
        _SLOTS.acquire()
        try:
            pcm = _request("POST", path, body, timeout, key, accept="audio/*")
            if not pcm:
                raise ElevenError("ElevenLabs returned no audio.")
            with open(out_wav, "wb") as f:
                f.write(G._pcm_to_wav(pcm, 24000))
            return model_id
        except urllib.error.HTTPError as e:
            status, _msg = "", ""
            if e.code == 400 and "language_code" in body:
                # a model that doesn't take a language code: drop it once
                body.pop("language_code", None)
                last = e
                continue
            if e.code == 429:
                # too many at once / system busy: wait and try again
                last = e
                time.sleep(2.0 * (attempt + 1))
                continue
            if e.code in (500, 502, 503, 504) and attempt < 3:
                last = e
                time.sleep(1.5 * (attempt + 1))
                continue
            _raise_for(e, "ElevenLabs voice")
        except (OSError, urllib.error.URLError) as e:
            last = e
            if attempt >= 2:
                raise G.GeminiFatal(f"Couldn't reach ElevenLabs: {e}", "network", 0,
                                    "elevenlabs") from e
            time.sleep(1.0 * (attempt + 1))
        finally:
            _SLOTS.release()
    if isinstance(last, urllib.error.HTTPError):
        _raise_for(last, "ElevenLabs voice")
    raise ElevenError(f"ElevenLabs voice failed: {last}")


def list_voices(key=None, timeout=30):
    """[{id, name, gender, desc}] of every voice the account can use."""
    raw = _request("GET", "/v1/voices", timeout=timeout, key=key)
    data = json.loads(raw.decode("utf-8", "replace"))
    out = []
    for v in (data.get("voices") or []):
        if not isinstance(v, dict) or not v.get("voice_id"):
            continue
        labels = v.get("labels") or {}
        out.append({"id": v["voice_id"], "name": v.get("name") or v["voice_id"],
                    "gender": str(labels.get("gender") or "").lower(),
                    "desc": ", ".join(x for x in (labels.get("accent"), labels.get("description"),
                                                  labels.get("age")) if x),
                    "category": v.get("category") or ""})
    return out


def subscription(key=None, timeout=20):
    """{"used", "limit", "left", "tier", "resets_at"} or {} when the key may
    not read it (scoped keys without the User permission)."""
    try:
        raw = _request("GET", "/v1/user/subscription", timeout=timeout, key=key)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            status, _m = _err_detail(e)
            if "missing_permissions" in status.lower():
                return {}
        raise
    d = json.loads(raw.decode("utf-8", "replace"))
    used, limit = int(d.get("character_count") or 0), int(d.get("character_limit") or 0)
    return {"used": used, "limit": limit, "left": max(0, limit - used),
            "tier": str(d.get("tier") or ""), "resets_at": int(d.get("next_character_count_reset_unix") or 0)}


def check_key(key, timeout=20):
    """(ok, message) for the Settings "Check" button."""
    try:
        voices = list_voices(key, timeout)
    except urllib.error.HTTPError as e:
        status, msg = _err_detail(e)
        if e.code == 401 and "missing_permissions" not in status.lower():
            return False, "ElevenLabs refused this key."
        if "missing_permissions" not in status.lower():
            return False, f"ElevenLabs error {e.code}" + (f": {msg}" if msg else "")
        voices = None
    except Exception as e:               # noqa: BLE001
        return False, f"Couldn't reach ElevenLabs: {e}"
    try:
        sub = subscription(key, timeout)
    except Exception:                    # noqa: BLE001
        sub = {}
    bits = ["Key works"]
    if sub.get("limit"):
        bits.append(f"{sub['left']:,} of {sub['limit']:,} credits left this month")
    if voices:
        mine = sum(1 for v in voices if v.get("category") not in ("premade", ""))
        bits.append(f"{len(voices)} voices" + (f" ({mine} your own)" if mine else ""))
    return True, " · ".join(bits)


def own_voices(cfg=None):
    """The account's own voices saved by Settings → Check:
    [{"id", "name", "gender"}] (gender may be "")."""
    out = []
    for v in (_cfg(cfg).get("elevenlabs_own_voices") or []):
        if isinstance(v, dict) and v.get("id"):
            out.append({"id": str(v["id"]), "name": str(v.get("name") or ""),
                        "gender": str(v.get("gender") or "").lower()})
    return out


def gender_of_voice(voice_id, own=None):
    g = _GENDER.get(voice_id)
    if g:
        return g
    for v in (own if own is not None else own_voices()):
        if v["id"] == voice_id:
            return v.get("gender") or ""
    return ""


def cast(speakers, mode="auto", single_voice="", own_voices=None, weights=None):
    """speaker id -> ElevenLabs voice id.

    v1.6:
      * the speaker who talks the most is the lead (not just the first one
        in the dict), and gets the default voice when its gender fits;
      * gender is respected everywhere: a woman is never voiced by a male
        voice because the default happens to be male (that also holds for
        "One voice": the one voice matches the lead's gender);
      * every speaker gets a different voice; your own voices of the right
        gender are used before the premade ones;
      * a speaker of unknown gender alternates with the others instead of
        everyone falling into the male pool.
    """
    own = [v for v in (own_voices if own_voices is not None else globals()["own_voices"]())
           if isinstance(v, dict) and v.get("id")]
    single_voice = single_voice or default_voice()
    weights = weights or {}
    order = sorted(speakers.keys(), key=lambda sid: -float(weights.get(sid, 0)))
    if not order:
        order = [""]

    def g_of(sid):
        g = str(((speakers.get(sid) or {}).get("gender")) or "").lower()
        return g if g in ("male", "female") else ""

    def pool(g):
        mine = [v["id"] for v in own if v.get("gender") == g]
        return mine + [v[0] for v in VOICES if v[2] == g]

    def fits(voice, g):
        vg = gender_of_voice(voice, own)
        return not g or not vg or vg == g

    lead_g = g_of(order[0])
    lead_voice = single_voice if fits(single_voice, lead_g) else pool(lead_g)[0]
    if mode == "single":
        return {sid: lead_voice for sid in order}
    out, used = {}, set()
    last_g = ""
    for n, sid in enumerate(order):
        g = g_of(sid)
        if n == 0:
            pick = lead_voice
        else:
            want = g or ("female" if (last_g or gender_of_voice(lead_voice, own)) == "male" else "male")
            pick = next((v for v in pool(want) if v not in used), None) \
                or next((v for v in pool(want)), lead_voice)
        out[sid] = pick
        used.add(pick)
        last_g = g or gender_of_voice(pick, own)
    return out


def voice_name(voice_id):
    return _NAME.get(voice_id, voice_id)
