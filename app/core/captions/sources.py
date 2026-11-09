"""app.core.captions.sources - YouTube Hunter.

Where the words come from:

  youtube   the video's own captions through yt-dlp. Uploaded (human) tracks
            win over auto captions; json3 is asked for first because YouTube's
            auto captions carry per-word timings there, which Bold Pop and the
            re-segmenter use. Timing is exact, and it costs no AI at all.
  file      an .srt / .vtt / .ass / .ssa the user already has.
  ai        Gemini listens to the audio and writes timed lines, in chunks, so
            long videos keep accurate timestamps.
"""

import json
import os
import re
import tempfile
import urllib.request

from app.core.captions import cues as C
from app.core.captions import gemini_media as G
from app.core.captions import languages as L
from app.core.captions import render as R

# ── YouTube ──────────────────────────────────────────────────────────────

_VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_url(video_id_or_url):
    v = (video_id_or_url or "").strip()
    if v.startswith(("http://", "https://")):
        return v
    if _VID_RE.match(v):
        return f"https://www.youtube.com/watch?v={v}"
    m = re.search(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])", v)
    return f"https://www.youtube.com/watch?v={m.group(1)}" if m else v


# v1.4: one yt-dlp extraction per video, shared by the track read, the job's
# own caption fetch and the "does YouTube have it in X?" lookup. Each of
# those used to run a full extraction (3-8 s, sometimes through a proxy)
# for the same video within the same minute. Caption URLs stay valid for
# hours; 20 minutes is a safe reuse window.
import threading as _threading
import time as _time

_INFO_TTL = 20 * 60
_INFO_CACHE = {}
_INFO_LOCK = _threading.Lock()


def _ydl_info(url):
    with _INFO_LOCK:
        hit = _INFO_CACHE.get(url)
        if hit and _time.monotonic() - hit[0] < _INFO_TTL:
            return hit[1], hit[2]
    info, proxy = _ydl_info_fresh(url)
    with _INFO_LOCK:
        if len(_INFO_CACHE) > 64:
            _INFO_CACHE.clear()
        _INFO_CACHE[url] = (_time.monotonic(), info, proxy)
    return info, proxy


def _ydl_info_fresh(url):
    from app.config import load_app_config
    cfg = load_app_config()
    proxy = (cfg.get("ai_proxy", "") or "").strip()
    cookies = (cfg.get("yt_cookies_browser", "") or "").strip().lower()
    from app.core.utils import ensure_yt_dlp_ready
    yt_dlp, _ff = ensure_yt_dlp_ready()
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
            "extractor_retries": 2}
    try:
        from app.core.runtime_deps import js_runtime_opts
        opts.update(js_runtime_opts())
    except Exception:                    # noqa: BLE001
        pass
    if proxy:
        opts["proxy"] = proxy
    if cookies:
        opts["cookiesfrombrowser"] = (cookies,)
    try:
        from app.core.download import _QuietLogger
        opts["logger"] = _QuietLogger()
    except Exception:                    # noqa: BLE001
        pass
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False), proxy


def _asr_keys(info):
    """v1.4.6: every auto-generated (speech-recognised) track YouTube has.

    A video with several audio tracks (YouTube's auto-dubbing, or an
    uploaded dub) carries one ASR track PER audio language, and the player
    lists each as "French (auto-generated)", "German (auto-generated)"...
    yt-dlp tags each of them '<lang>-orig'. These are YouTube's own
    captions, not its machine translation."""
    auto = info.get("automatic_captions") or {}
    return [k for k, f in auto.items() if k.endswith("-orig") and f]


def _orig_lang(info):
    """The spoken language: the auto-caption track YouTube made from the
    ORIGINAL audio. With several ASR tracks the video's own language wins."""
    keys = _asr_keys(info)
    lang = (info.get("language") or "").split("-")[0].lower()
    if lang and any(_base(k) == lang for k in keys):
        return lang
    if keys:
        return keys[0][:-5]
    return lang


def youtube_tracks(video_id_or_url):
    """What the video offers, best first:
    [{key, lang, label, auto}], plus the spoken language."""
    info, _proxy = _ydl_info(video_url(video_id_or_url))
    spoken = _orig_lang(info)
    tracks = []
    for code, fmts in (info.get("subtitles") or {}).items():
        if code == "live_chat" or not fmts:
            continue
        tracks.append({"key": "sub:" + code, "lang": code, "auto": False,
                       "label": f"{L.name_of(code)} · uploaded"})
    auto = info.get("automatic_captions") or {}
    asr = _asr_keys(info)
    for k in asr:
        code = k[:-5]
        tracks.append({"key": "auto:" + k, "lang": code, "auto": True,
                       "label": f"{L.name_of(code) or code} (auto-generated)"})
    if not asr and spoken:
        # older yt-dlp: no '-orig' tag, the spoken language's plain key
        if spoken in auto:
            tracks.append({"key": "auto:" + spoken, "lang": spoken, "auto": True,
                           "label": f"{L.name_of(spoken)} (auto-generated)"})
    tracks.sort(key=lambda t: (t["auto"], _base(t["lang"]) != _base(spoken)))
    # v1.4.1: ONLY the tracks YouTube really has for this video: the uploaded
    # (human) ones and the auto-generated ones. v1.4.6: EVERY auto-generated
    # track (one per audio language on a dubbed video), not just the spoken
    # language's.
    # YouTube's machine auto-translate is no longer listed (it put ~100
    # languages in the picker); a viewer who wants another language picks
    # "AI listens". Best first: an uploaded track in the spoken language,
    # then the auto one, then other uploaded tracks.
    languages = []
    seen = set()
    for t in tracks:
        b = _base(t["lang"])
        kind = "auto" if t["auto"] else "uploaded"
        if (b, kind) in seen:
            continue
        seen.add((b, kind))
        languages.append({"key": t["key"], "lang": b, "kind": kind})
    languages.sort(key=lambda x: (x["lang"] != _base(spoken), x["kind"] == "auto"))
    # v1.3.3: which languages YouTube itself can serve, so the UI can say
    # "no AI needed" before anything runs. `uploaded` = human tracks,
    # `translatable` = YouTube's own (free) auto-translation of the speech.
    uploaded = sorted({_base(c) for c, f in (info.get("subtitles") or {}).items()
                       if c != "live_chat" and f})
    translatable = sorted({_base(k) for k in auto
                           if not k.endswith("-orig") and _base(k) != _base(spoken)})
    auto_langs = sorted({_base(k) for k in asr} | ({_base(spoken)} if spoken else set()))
    return {"tracks": tracks, "spoken": spoken, "title": info.get("title") or "",
            "duration": float(info.get("duration") or 0),
            "uploaded": uploaded, "translatable": translatable,
            "auto_langs": auto_langs, "languages": languages}


def _base(code):
    return (code or "").replace("-orig", "").split("-")[0].lower()


def _read_pool(pool, proxy):
    """Fetch the best format of one caption track and parse it (None if
    nothing readable came back)."""
    by_ext = {f.get("ext"): f for f in (pool or []) if f.get("url")}
    for ext in ("json3", "vtt", "srv3", "ttml", "srt"):
        f = by_ext.get(ext)
        if not f:
            continue
        raw = _fetch(f["url"], proxy)
        cues = C.parse_any(raw, ext if ext in ("json3", "vtt", "srt") else "")
        if cues:
            return C.resegment(cues)
    return None


def youtube_cues_in(video_id_or_url, lang, allow_machine=False):
    """YouTube's OWN captions already in `lang`, so nothing has to be
    translated by AI (v1.3.3).

    An uploaded (human) track in that language always wins, then YouTube's
    own auto-generated track in it (v1.4.6, kind "auto"); with
    `allow_machine` YouTube's free auto-translation of the speech is the next
    choice. Returns (cues, lang, kind) with kind "uploaded" | "translated",
    or None when YouTube has neither (or the language is the spoken one:
    the normal path already keeps that as-is)."""
    want = _base(lang)
    if not want:
        return None
    info, proxy = _ydl_info(video_url(video_id_or_url))
    if want == _base(_orig_lang(info)):
        return None
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    for code, fmts in subs.items():
        if code != "live_chat" and fmts and _base(code) == want:
            cues = _read_pool(fmts, proxy)
            if cues:
                return cues, want, "uploaded"
    # v1.4.6: YouTube's own auto-generated track in that language (a dubbed
    # video has one per audio track): real speech recognition, no AI
    for k in _asr_keys(info):
        if _base(k) == want:
            cues = _read_pool(auto[k], proxy)
            if cues:
                return cues, want, "auto"
    if allow_machine:
        for code, fmts in auto.items():
            if not code.endswith("-orig") and _base(code) == want and fmts:
                cues = _read_pool(fmts, proxy)
                if cues:
                    return cues, want, "translated"
    return None


def youtube_cues(video_id_or_url, key="", want_lang=""):
    """Timed cues from one track (`key` from youtube_tracks, or the best one).
    Returns (cues, lang)."""
    info, proxy = _ydl_info(video_url(video_id_or_url))
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    pool, code = None, ""
    if key.startswith("sub:") and key[4:] in subs:
        pool, code = subs[key[4:]], key[4:]
    elif key.startswith("auto:") and key[5:] in auto:
        pool, code = auto[key[5:]], key[5:]
    elif key.startswith("tr:") and key[3:] in auto:
        # v1.4: YouTube's own auto-translation of the speech (free, no AI)
        pool, code = auto[key[3:]], key[3:]
    else:
        spoken = _orig_lang(info)
        prefs = [x for x in (want_lang, spoken) if x]
        for c in prefs:
            for k in subs:
                if k.split("-")[0] == c:
                    pool, code = subs[k], k
                    break
            if pool:
                break
        if not pool and subs:
            code = next((k for k in subs if k != "live_chat"), "")
            pool = subs.get(code)
        if not pool and spoken:
            k = spoken + "-orig" if spoken + "-orig" in auto else spoken
            pool, code = auto.get(k), k
    if not pool:
        raise RuntimeError("This video has no captions on YouTube. Pick “AI listens” instead.")
    cues = _read_pool(pool, proxy)
    if cues:
        return cues, code.replace("-orig", "").split("-")[0]
    raise RuntimeError("YouTube's caption file couldn't be read.")


def _fetch(url, proxy=""):
    from app.core.net import _urlopen
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _urlopen(req, 30, proxy or None) as r:
        return r.read().decode("utf-8", "replace")


# ── file ─────────────────────────────────────────────────────────────────

def file_cues(path):
    cues = C.load_file(path)
    if not cues:
        raise RuntimeError("That subtitle file has no readable lines.")
    lang = L.guess_from_text(" ".join(c.text for c in cues[:80])) or ""
    return C.resegment(cues), lang


def sidecar_for(media_path):
    """A subtitle file sitting next to the video with the same name."""
    base = os.path.splitext(media_path)[0]
    folder = os.path.dirname(media_path) or "."
    stem = os.path.basename(base).lower()
    try:
        for name in os.listdir(folder):
            low = name.lower()
            if low.startswith(stem) and low.endswith((".srt", ".vtt", ".ass", ".ssa")):
                return os.path.join(folder, name)
    except OSError:
        pass
    return ""


# ── AI listens ───────────────────────────────────────────────────────────

_TRANSCRIBE_SYS = (
    "You are a professional subtitler with perfect hearing. You transcribe speech "
    "verbatim in the language it is spoken, with correct punctuation and casing. "
    "You never translate, summarise or invent words. Timestamps must match the audio.")

_TRANSCRIBE_PROMPT = """Transcribe this audio clip ({dur:.0f} seconds long) into subtitle lines.

Rules:
- One line = one sentence or natural phrase, at most ~7 seconds and ~80 characters.
- "s" and "e" are start/end times in SECONDS from the start of THIS clip, with 0.1 s precision, never beyond {dur:.1f}.
  "s" is the moment the first word is heard and "e" the moment the last word ends (not the next line's start).
- Write clean, fluent subtitles of exactly what is said: correct punctuation, casing and spelling; numbers as digits; never break a line in the middle of a short phrase.
- Keep fillers only when meaningful. Mark non-speech only as [music] / [laughs] / [applause] lines when there is no speech.
- "spk": a short speaker id (S1, S2, …) consistent within the clip{spk_hint}.
- "lang": the BCP-47 code of the spoken language.{perf_rules}

Return JSON only:
{perf_shape}"""

_SHAPE_PLAIN = '{"lang": "en", "lines": [{"s": 0.0, "e": 2.4, "t": "text", "spk": "S1"}]}'

# v1.5 quota saver: the same request also does the dub's performance read
# (who speaks, how, and the vocal events), so a dub of this file skips a
# whole listening request. Only a few output tokens per line.
_PERF_RULES = """
- "style": how the line is delivered, in English, max 5 words ("excited, fast" / "deadpan, slow" / "whispered, nervous").
- "tagged": ONLY when you hear a vocal event in the line: the line text with the event where it happens, using
  <laugh> <chuckle> <giggle> <sigh> <gasp> <breath> <cough> <sniff> <scoff> <hmm> <short pause> <long pause>. Leave it out otherwise.
- "speakers": one entry per speaker id with "gender" (male|female) and "age" (child|teen|young adult|adult|senior)."""
_SHAPE_PERF = ('{"lang": "en", "speakers": [{"id": "S1", "gender": "male", "age": "adult"}], '
               '"lines": [{"s": 0.0, "e": 2.4, "t": "text", "spk": "S1", "style": "excited, fast"}]}')


def _listen_shape(data):
    """v1.4.3: Gemini doesn't always return the object the prompt asks for.
    Seen in the wild: a bare list of lines, the object wrapped in a list,
    several slice objects in a list, and other key names for the lines.
    Every shape becomes {"lang": str, "lines": [dict, ...]}; this used to
    crash with "'list' object has no attribute 'get'"."""
    lang, lines, speakers = "", [], {}

    def take(obj):
        nonlocal lang
        if isinstance(obj, list):
            for x in obj:
                take(x)
            return
        if not isinstance(obj, dict):
            return
        if not lang and isinstance(obj.get("lang") or obj.get("language"), str):
            lang = obj.get("lang") or obj.get("language")
        sps = obj.get("speakers")
        if isinstance(sps, dict):           # our own cached shape
            sps = [dict(v, id=k) for k, v in sps.items() if isinstance(v, dict)]
        for sp in sps if isinstance(sps, list) else []:
            if isinstance(sp, dict) and sp.get("id"):
                speakers[str(sp["id"])] = {"gender": str(sp.get("gender") or "").lower(),
                                           "age": str(sp.get("age") or "")}
        inner = next((obj[k] for k in ("lines", "subtitles", "segments", "captions", "cues")
                      if isinstance(obj.get(k), list)), None)
        if inner is not None:
            take(inner)
        elif any(k in obj for k in ("t", "text")) and any(k in obj for k in ("s", "start")):
            ln = {"s": obj.get("s", obj.get("start", 0)),
                  "e": obj.get("e", obj.get("end", 0)),
                  "t": obj.get("t", obj.get("text", "")),
                  "spk": obj.get("spk", obj.get("speaker", ""))}
            if "style" in obj:
                ln["style"] = str(obj.get("style") or "")
            if obj.get("tagged"):
                ln["tagged"] = str(obj.get("tagged"))
            lines.append(ln)
    take(data)
    out = {"lang": str(lang or ""), "lines": lines}
    if speakers:
        out["speakers"] = speakers
    if isinstance(data, dict) and data.get("_perf"):
        out["_perf"] = 1
    return out


def ai_transcribe(media_path, start=0.0, end=0.0, chunk=120.0, on_progress=None,
                  should_cancel=None, creds=None, workdir=None):
    """Gemini transcription in `chunk`-second slices. Returns (cues, lang).

    v1.4.4: 2-minute slices (were 5): the model's timestamps drift the
    further they are from the start of the clip it hears, so shorter slices
    keep every line closer to its real second. Then every line is snapped
    onto the voice's real start / stop (ffmpeg silencedetect)."""
    # v1.4.10: a chain of listeners, best first. When one turns out not to
    # hear audio, the next takes over from the same slice (paid slices stay
    # cached), instead of the job failing as "Provider is busy".
    chain = [creds] if creds else (G.listen_candidates("captions") or [G.credentials(feature="captions")])
    ear = 0
    info = R.probe(media_path)
    total_end = end if end > start else info["duration"]
    if total_end <= start:
        raise RuntimeError("Couldn't read the media's length.")
    work = workdir or tempfile.mkdtemp(prefix="yh_listen_")
    out, lang = [], ""
    t = start
    n = max(1, int((total_end - start + chunk - 1) // chunk))
    i = 0
    known_spk = []
    # v1.5: with the quota saver on, the transcription also reads the
    # performance (the dub then skips its own listening request)
    from app.core.captions import gemini_budget as B
    with_perf = B.saver_on()
    spk_info = {}
    while t < total_end - 0.5:
        if should_cancel and should_cancel():
            raise R.Cancelled()
        dur = min(chunk, total_end - t)
        # v1.3.1: every slice is cached as soon as it is paid for, so a job
        # that stopped (quota, network, cancel) resumes from the next slice
        slice_cache = os.path.join(work, f"listen_{round(t, 2)}_{round(dur, 2)}.json")
        data = None
        if workdir and os.path.isfile(slice_cache):
            try:
                with open(slice_cache, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:            # noqa: BLE001
                data = None
        if data is None:
            audio, err = R.extract_audio(media_path, os.path.join(work, f"listen_{i}.ogg"), t, dur,
                                         should_cancel=should_cancel)
            if not audio:
                raise RuntimeError("Couldn't extract the audio: " + err)
            hint = (f"; speakers heard earlier: {', '.join(known_spk)} (reuse ids for the same voices)"
                    if known_spk else "")
            while True:
                try:
                    prompt = _TRANSCRIBE_PROMPT.format(
                        dur=dur, spk_hint=hint,
                        perf_rules=_PERF_RULES if with_perf else "",
                        perf_shape=_SHAPE_PERF if with_perf else _SHAPE_PLAIN)
                    data = _listen_shape(G.listen_json(chain[ear], audio, prompt,
                                                       _TRANSCRIBE_SYS, should_cancel=should_cancel))
                    if with_perf:
                        data["_perf"] = 1
                    break
                except G.ListenUnsupported as e:
                    if ear + 1 >= len(chain):
                        raise
                    G._log(f"listen: {e} -> next listener "
                           f"{chain[ear + 1].get('name')} · {chain[ear + 1].get('model')}")
                    ear += 1
            if workdir and data["lines"]:
                try:
                    with open(slice_cache, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                except OSError:
                    pass
            try:
                os.remove(audio)
            except OSError:
                pass
        perf_slice = bool(isinstance(data, dict) and data.get("_perf"))
        data = _listen_shape(data)          # an old cached slice may be any shape
        lang = lang or str(data.get("lang") or "").split("-")[0]
        for sid, info in (data.get("speakers") or {}).items():
            spk_info.setdefault(sid, info)
        for ln in data.get("lines") or []:
            try:
                s, e = float(ln.get("s", 0)), float(ln.get("e", 0))
            except (TypeError, ValueError):
                continue
            text = C.clean_text(str(ln.get("t") or ""))
            if not text:
                continue
            s, e = max(0.0, min(s, dur)), max(0.0, min(e, dur))
            spk = str(ln.get("spk") or "")
            if spk and spk not in known_spk:
                known_spk.append(spk)
            meta = {}
            if perf_slice:
                meta = {"perf": 1, "style": str(ln.get("style") or "")[:60]}
                if ln.get("tagged"):
                    meta["tagged"] = C.clean_text(str(ln["tagged"]))
                info = spk_info.get(spk) or {}
                if info.get("gender") in ("male", "female"):
                    meta["gender"] = info["gender"]
                if info.get("age"):
                    meta["age"] = info["age"]
            out.append(C.Cue(t + s - start, t + max(e, s + 0.3) - start, text, speaker=spk,
                             meta=meta))
        i += 1
        t += dur
        if on_progress:
            on_progress(min(1.0, i / n))
    out = C.normalize(out)
    try:
        onsets, offsets = R.speech_edges(media_path, start, total_end)
        out = C.snap_to_speech(out, onsets, offsets)
    except Exception:                    # noqa: BLE001 — the AI's own times still work
        pass
    return out, lang
