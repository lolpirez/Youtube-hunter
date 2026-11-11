"""app.core.ai.friendly - YouTube Hunter.

v1.4.9: one place that turns a raw provider error into something a person
can read in one glance.

A failed caption / dub job used to show the provider's whole reply: Google's
quota text with three URLs, the metric id, the free-tier limit and a retry
delay in seconds to six decimals. Now every surface (the in-app job card,
the Downloads toast, the desktop notice and the YouTube panel) shows the
same three short pieces:

    title    what happened, 2-4 words          "Daily limit reached"
    detail   which model / provider, and when  "gemini-3.6-flash · resets in 10h 40m"
    hint     the one thing to do about it      "Pick another model in Settings → Captions."

The raw text is kept (`raw`) for the crash log; it is never shown.

    explain(err, step="") -> {kind, title, detail, hint, step, retry_s, raw}
    short(err, step="")   -> "Daily limit reached · gemini-3.6-flash · resets in 10h 40m"
"""

import re

# model ids as providers spell them, first match wins
_MODEL_RE = re.compile(
    r"\b((?:gemini|gpt|chatgpt|claude|o[134]|deepseek|llama|qwen|mistral|grok|kimi|glm)"
    r"[\w.\-:/]*\w)", re.I)
# "Apinex API error (429): ..." / "Network error contacting Apinex: ..."
_PROVIDER_RES = (
    re.compile(r"^\s*(?:[\w ]+:\s*)?([\w][\w .\-]{0,30}?) API error \(\d{3}\)"),
    re.compile(r"contacting ([\w][\w .\-]{0,30}?)[:.]", re.I),
    re.compile(r"^\s*(?:[\w ]+:\s*)?([\w][\w .\-]{0,30}?) (?:can't listen|keeps rate-limiting|rejected)", re.I),
)
# Google: "Please retry in 10h39m50.79s" / "retry in 41.2s" / retryDelay "41s"
_RETRY_RE = re.compile(
    r"retry (?:in|after)\s+((?:\d+(?:\.\d+)?\s*[hms]\s*){1,3})", re.I)


def _seconds(span):
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", span or "", re.I):
        total += float(num) * {"h": 3600, "m": 60, "s": 1}[unit.lower()]
    return total


def _human_wait(sec):
    sec = int(round(sec))
    if sec <= 0:
        return ""
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{max(1, round(sec / 60))} min"
    h, m = divmod(round(sec / 60), 60)
    return f"{h}h {m:02d}m" if m else f"{h}h"


def _model(text):
    m = _MODEL_RE.search(text or "")
    if not m:
        return ""
    mid = m.group(1)
    # "gemini-3.6-flash." / "models/gemini-..." trimmed to the id itself
    return mid.split("/")[-1].rstrip(".:")


def _provider(text):
    for rx in _PROVIDER_RES:
        m = rx.search(text or "")
        if m:
            name = m.group(1).strip()
            if name.lower() not in ("the", "this", "ai", "custom ai"):
                return name
    low = (text or "").lower()
    if "gemini" in low or "generativelanguage" in low or "aistudio" in low:
        return "Gemini"
    return ""


def _first_sentence(text, limit=110):
    t = re.sub(r"https?://\S+", "", text or "")
    t = re.sub(r"\s+", " ", t).strip(" .:")
    # drop a leading "Captions:" / "AI dub:" step label
    t = re.sub(r"^(captions|ai dub|dub|ai dub \+ captions)\s*:\s*", "", t, flags=re.I)
    cut = re.split(r"(?<=[.!?])\s", t, maxsplit=1)[0]
    if len(cut) > limit:
        cut = cut[:limit - 1].rstrip() + "…"
    return cut


def _has(low, *words):
    return any(w in low for w in words)


def explain(err, step=""):
    """{kind, title, detail, hint, step, retry_s, raw} for one error."""
    raw = str(err or "").strip()
    low = raw.lower()
    model = _model(raw)
    provider = _provider(raw)
    m = _RETRY_RE.search(raw)
    retry_s = _seconds(m.group(1)) if m else 0.0
    wait = _human_wait(retry_s)
    where = " · ".join(x for x in (model, provider if provider.lower() not in model.lower() else "") if x)

    kind, title, hint, when = "error", "Something went wrong", "", ""
    if not raw:
        kind, title = "error", "Something went wrong"
    elif low in ("cancelled", "canceled"):
        kind, title = "cancelled", "Stopped"
    elif _has(low, "winerror 5]", "winerror 32]", "winerror 33]", "access is denied",
              "being used by another process", "errno 13]") and not _has(low, "(403)", " 403"):
        # v1.9: a file another program held open for a moment (antivirus,
        # Explorer preview, a player), not a key problem. Was "Something went
        # wrong" (or, for Errno 13, the misleading "Access refused" below).
        kind, title = "locked", "A file was busy"
        hint = ("Close the video if a player has it open, then Retry: everything recorded is kept. "
                "If it keeps happening, allow YouTube Hunter in Windows Security → "
                "Controlled folder access.")
    elif _has(low, "can't listen", "cannot listen", "listen to audio", "input_audio",
              "audio input", "does not support audio", "doesn't support audio",
              "unsupported modality", "audio is not supported"):
        kind, title = "listen", "This model can't hear audio"
        hint = "Pick a Gemini model for Captions, or use YouTube's own captions."
    elif _has(low, "quota") and _has(low, "per day", "perday", "daily", "free_tier",
                                      "free tier", "limit: 0", "used up"):
        kind, title = "quota", "Daily limit reached"
        when = f"resets in {wait}" if wait else "resets at midnight Pacific"
        hint = "Switch the model in Settings → Captions, or resume after the reset."
    elif _has(low, "rate-limit", "rate limit", "ratelimit", "too many requests",
              "(429)", " 429", "resource_exhausted", "quota"):
        kind, title = "rate", "Too many requests"
        when = f"try again in {wait}" if wait else ""
        hint = "Give it a minute, then resume. Nothing is paid twice."
    elif _has(low, "(401)", " 401", "rejected the api key", "invalid api key",
              "api key not valid", "unauthorized", "api key is missing", "incorrect api key"):
        kind, title = "auth", "Key rejected"
        hint = "Check the key in Settings → AI Providers."
    elif _has(low, "(403)", " 403", "refused access", "permission denied", "forbidden"):
        kind, title = "auth", "Access refused"
        hint = "The key may lack access to this model. Check Settings → AI Providers."
    elif _has(low, "needs a google gemini key", "needs an ai provider", "none is set up",
              "base url is missing", "no model name is set"):
        kind, title = "setup", "AI provider not set up"
        hint = "Add one in Settings → AI Providers."
    elif _has(low, "no captions on youtube", "has no captions"):
        kind, title = "nocaps", "No captions on YouTube"
        hint = "Pick “AI listens” to have the AI write them."
    elif _has(low, "no spoken lines"):
        kind, title = "empty", "No speech found"
        hint = "The audio may be music only."
    elif _has(low, "blockreason", "safety", "blocked", "prohibited_content"):
        kind, title = "blocked", "The model declined this audio"
        hint = "Try another model in Settings → Captions."
    elif _has(low, "can't reach", "network error", "timed out", "timeout", "connection",
              "secure connection", "ssl", "getaddrinfo", "unreachable"):
        kind, title = "network", "Connection lost"
        hint = "Check the internet, proxy or VPN, then resume."
    elif _has(low, "(500)", "(502)", "(503)", "(504)", " 503", "overloaded", "unavailable",
              "internal error", "returned an empty response", "returned nothing"):
        kind, title = "busy", "Provider is busy"
        hint = "Usually clears in a minute. Resume to continue."
    elif _has(low, "couldn't draw the captions", "couldn't add the subtitle", "ffmpeg"):
        kind, title = "render", "Couldn't write the video"
        hint = "The captions are saved as .srt next to the video."
    elif _has(low, "file is missing", "couldn't be found", "video file is missing"):
        kind, title = "file", "File not found"
        hint = "It may have been moved or deleted."

    if kind == "error":
        detail = _first_sentence(raw)
    elif kind == "locked":
        # the file's name, not the whole C:\\Users\\... path
        m = re.search(r"'([^']+)'", raw)
        name = re.split(r"[\\/]+", m.group(1).replace("\\\\", "\\"))[-1] if m else ""
        detail = f"In use: {name}" if name else ""
    else:
        detail = " · ".join(x for x in (where, when) if x)
    return {"kind": kind, "title": title, "detail": detail, "hint": hint,
            "step": step or "", "retry_s": retry_s, "raw": raw}


def short(err, step=""):
    """One line for toasts and notices."""
    e = explain(err, step)
    head = f"{e['step']} · {e['title']}" if e["step"] else e["title"]
    return " · ".join(x for x in (head, e["detail"]) if x)
