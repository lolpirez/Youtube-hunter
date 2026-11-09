"""app.core.ai.hearing - YouTube Hunter.

v1.4.10 "ears and brain": which models can HEAR audio.

Captions do two different jobs: listening to the audio (speech -> timed
lines) and translating those lines. Any chat model translates well; only a
few can take audio in. Before this module the app sent the audio to whatever
model was picked for Captions, so a text-only pick (DeepSeek, Claude, Llama
...) behind an OpenAI-compatible router got an `input_audio` part it can't
read. Routers don't answer that with a clean 400: most return 503 / "no
available channel" / "model busy", which the job showed as "Provider is
busy" and asked the user to retry forever.

Now the pick only translates unless it can hear, and the listening goes to
a model that can (see gemini_media.listen_candidates). The rules are by model
NAME, on purpose: no probe call, no wasted upload, works offline.
"""

import re

# names that take audio input on a chat endpoint
_HEARS = (
    "gemini",                    # every Gemini chat model (2.x, 3.x, flash, pro)
    "gemma-3n",                  # on-device Gemma with audio
    "gpt-4o-audio", "gpt-4o-mini-audio", "gpt-audio",
    "gpt-4o-transcribe", "gpt-4o-mini-transcribe",
    "voxtral",                   # Mistral's audio model
    "qwen-omni", "qwen2.5-omni", "qwen3-omni", "qwen-audio", "qwen2-audio",
    "phi-4-multimodal",
    "step-audio", "kimi-audio", "glm-4-voice",
)
# ...but not these flavours of those families
_DEAF = ("tts", "embedding", "imagen", "image-generation", "-image", "veo", "aqa",
         "native-audio-dialog", "live-")


def can_hear(model):
    """True when `model` (an id as a router lists it, e.g.
    "google/gemini-3.8-flash" or "gemini-2.5-flash") accepts audio input."""
    m = str(model or "").strip().lower()
    if not m:
        return False
    if any(x in m for x in _DEAF):
        return False
    return any(x in m for x in _HEARS)


# families that are text (or text + image) only: never handed audio, not
# even as a last resort. Checked AFTER can_hear, so "kimi-audio" still hears.
_TEXT_ONLY = ("deepseek", "claude", "llama", "mistral", "mixtral", "codestral", "grok",
              "kimi", "moonshot", "glm", "gpt-3.5", "gpt-4", "gpt-5", "o1", "o3", "o4",
              "qwen", "qwq", "command", "yi-", "ernie", "minimax", "nemotron", "phi-",
              "gemma", "hunyuan", "sonar")


def known_deaf(model):
    """True when `model` surely can't hear (a text-only family)."""
    m = str(model or "").strip().lower()
    if not m or can_hear(m):
        return False
    name = m.rsplit("/", 1)[-1]
    return any(name.startswith(x) or ("/" + x) in m or ("-" + x) in name for x in _TEXT_ONLY) \
        or any(x in name for x in ("deepseek", "claude", "llama", "grok"))


def is_gemini(model):
    m = str(model or "").strip().lower()
    return "gemini" in m and not any(x in m for x in ("embedding", "imagen", "aqa"))


def _version(m):
    nums = re.findall(r"(\d+(?:\.\d+)?)", m)
    out = []
    for n in nums[:2]:
        try:
            out.append(float(n))
        except ValueError:
            pass
    return tuple(out) or (0.0,)


def ear_rank(model):
    """Sort key, best listener first: Gemini before others, Flash before Pro
    (as accurate for transcription, faster and far cheaper), full Flash before
    Flash-Lite, stable before preview, newest version first."""
    m = str(model or "").lower()
    gem = "gemini" in m
    flash = "flash" in m
    lite = "lite" in m
    preview = "preview" in m or "exp" in m
    v = _version(m)
    return (0 if gem else 1, 0 if flash else 1, 1 if lite else 0, 1 if preview else 0,
            tuple(-x for x in v))


def best_ear(models):
    """The best listening model in `models` (any iterable of ids), or ""."""
    seen = []
    for m in models or []:
        m = str(m or "").strip()
        if m and m not in seen and can_hear(m):
            seen.append(m)
    if not seen:
        return ""
    return sorted(seen, key=ear_rank)[0]


def best_gemini(models):
    """The best Gemini CHAT model in `models` (for the dub's listening and
    adapting on a router), or ""."""
    cands = [str(m).strip() for m in (models or []) if is_gemini(m) and can_hear(m)]
    if not cands:
        return ""
    return sorted(dict.fromkeys(cands), key=ear_rank)[0]


def short(model):
    """"google/gemini-3.8-flash" -> "gemini-3.8-flash" for labels."""
    m = str(model or "")
    return m.rsplit("/", 1)[-1] if "/" in m else m
