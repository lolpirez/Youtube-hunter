"""app.core.transcript_timed - YouTube Hunter.

Time-stamped transcripts. The Scraper's transcript (app.core.transcripts) is
one long string - perfect for a CSV cell, useless when you want to know WHEN
something is said. This module reads the same YouTube captions again WITH
their timing (through app.core.captions.sources, the pipeline the Video
Cutter already uses: uploaded track first, auto captions second, yt-dlp +
the configured proxy / cookies) and gives them back in the four shapes the
UI needs:

  cues        [{"s": start_sec, "e": end_sec, "t": text}]   (the list view)
  timed text  "[01:23] text"  one line per cue               (copy / .txt)
  plain text  the cues joined into a paragraph               (copy / .txt)
  srt         a ready .srt file                              (export)

Nothing here raises: every function returns an {"ok": False, "error": ...}
answer instead, so a worker thread or a bridge request can send it as is.
"""

from app.core.captions import cues as C

# the most cues one answer carries. A 10-hour stream at ~4 s a cue is 9k; the
# cap only protects the bridge / UI from a pathological track.
MAX_CUES = 20000


def stamp(seconds, force_hours=False):
    """68.4 -> "01:08", 3725 -> "1:02:05" (hours only when the video has them)."""
    s = max(0, int(seconds or 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h or force_hours:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _flat(text):
    return " ".join(str(text or "").split())


def to_dicts(cues):
    out = []
    for c in cues[:MAX_CUES]:
        t = _flat(c.text)
        if t:
            out.append({"s": round(c.start, 2), "e": round(c.end, 2), "t": t})
    return out


def from_dicts(rows):
    """The UI's rows back into Cue objects (for the SRT writer)."""
    out = []
    for r in rows or []:
        try:
            out.append(C.Cue(float(r["s"]), float(r["e"]), str(r["t"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def timed_text(rows):
    """"[mm:ss] text" - one cue per line."""
    rows = list(rows or [])
    hours = any(float(r.get("s") or 0) >= 3600 for r in rows)
    return "\n".join(f"[{stamp(r.get('s'), hours)}] {r.get('t', '')}" for r in rows)


def plain_text(rows):
    """The cues as running prose (no stamps)."""
    return " ".join(_flat(r.get("t")) for r in rows or [] if _flat(r.get("t")))


def srt_text(rows):
    return C.to_srt(from_dicts(rows))


def tracks_for(video_id):
    """[{key, lang, label, auto}] - the caption tracks YouTube really has, the
    best first, one entry per language + kind."""
    from app.core.captions import sources as S
    from app.core.captions import languages as L
    data = S.youtube_tracks(video_id)
    out, seen = [], set()
    for x in data.get("languages") or []:
        code = x.get("lang") or ""
        auto = x.get("kind") == "auto"
        if (code, auto) in seen:
            continue
        seen.add((code, auto))
        name = L.name_of(code) or code or "?"
        out.append({"key": x.get("key") or "", "lang": code, "auto": auto,
                    "label": name + (" (auto-generated)" if auto else "")})
    return {"tracks": out, "spoken": data.get("spoken") or "",
            "duration": float(data.get("duration") or 0)}


def load(video_id, key=""):
    """Timed cues for one video. `key` is a track key from tracks_for(), or ""
    for the best one.

    -> {"ok": True, "lang", "key", "cues": [...], "tracks": [...], "auto": bool}
       {"ok": False, "error": "..."}"""
    from app.core.captions import sources as S
    try:
        info = tracks_for(video_id)
    except Exception as exc:                      # noqa: BLE001
        info = {"tracks": [], "spoken": "", "duration": 0.0}
        info_err = str(exc)
    else:
        info_err = ""
    try:
        cues, lang = S.youtube_cues(video_id, key=key or "")
    except Exception as exc:                      # noqa: BLE001
        msg = str(exc) or "No captions could be read for this video."
        if "no captions" in msg.lower():
            msg = "This video has no captions on YouTube."
        return {"ok": False, "error": info_err or msg, "tracks": info["tracks"]}
    rows = to_dicts(cues)
    if not rows:
        return {"ok": False, "error": "This video has no captions on YouTube.",
                "tracks": info["tracks"]}
    chosen = next((t for t in info["tracks"] if t["key"] == key), None)
    if chosen is None:
        chosen = next((t for t in info["tracks"] if t["lang"] == lang), None)
    return {"ok": True, "lang": lang, "key": (chosen or {}).get("key", key or ""),
            "auto": bool((chosen or {}).get("auto")), "cues": rows,
            "tracks": info["tracks"], "duration": info["duration"]}
