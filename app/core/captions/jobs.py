"""app.core.captions.jobs - YouTube Hunter.

One call per job, for the view-model: run_captions(). It takes a plain
dict of options (straight from QML) and report(stage, fraction).

Options:
    media        local video / audio path (a download or a cut clip)
    title        for the AI brief and notifications
    source       "youtube" | "file" | "ai"
    video_id     YouTube id (source = youtube)
    track        youtube_tracks() key, "" = best
    sub_path     subtitle file (source = file)
    source_lang  override the detected spoken language ("" = detect)
    clip_start / clip_end   the clip's place in the source video (YouTube
                 captions are re-timed to it)
    offset       seconds to shift every cue (fixes a clip cut on a keyframe)

Captions:   target ("" = keep the language), style, size, position,
            bilingual, output ("burn" | "soft" | "srt"),
            allow_ai (default True; v1.4: False for YouTube's own captions,
            which are used exactly as YouTube serves them),
            translate_via ("ai" | "youtube"): with a target, YouTube's own
            track in that language is tried first (uploaded: always;
            YouTube's auto-translation: when "youtube"), AI only after that
            register ("natural" | "casual" | "formal", v2.0: how the
            translation talks; default natural = spoken, never bookish),
            refine (v2.0, default True: a native-editor pass rewrites lines
            that still read translated)

Every step caches into a per-file work folder, so re-running a failed job
reuses the transcript and every translated batch.
"""

import hashlib
import json
import os

from app.core import fs_safe as FS

from app.core.captions import cues as C
from app.core.captions import languages as L
from app.core.captions import polish as P
from app.core.captions import render as R
from app.core.captions import sources as S
from app.core.captions import styles as ST
from app.core.captions import translate as T


def _sha(*parts):
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def media_key(path):
    try:
        st = os.stat(path)
        return _sha(os.path.abspath(path), st.st_size, int(st.st_mtime))
    except OSError:
        return _sha(path)


def work_dir(media):
    try:
        from app.core.db.ideas import DATA_DB_PATH
        root = os.path.join(os.path.dirname(DATA_DB_PATH), "voice_studio")
    except Exception:                    # noqa: BLE001
        import tempfile
        root = os.path.join(tempfile.gettempdir(), "yh_voice_studio")
    d = os.path.join(root, media_key(media))
    os.makedirs(d, exist_ok=True)
    return d


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def _cache_key(opts):
    src = opts.get("source", "youtube")
    # v1.4.4: "ai2" = shorter slices + snapped to the voice; older AI
    # transcripts were cached with the drifting timestamps
    extra = {"youtube": opts.get("track", ""), "file": opts.get("sub_path", ""), "ai": "ai2"}.get(src, "")
    # v1.9 "spk2": speakers are read per slice (gender / age / slice index on
    # every cue). Rebuilt from the cached slices, so nothing is paid again.
    return f"source_{src}_{_sha(extra, opts.get('clip_start', 0), opts.get('clip_end', 0), 'spk2')}.json"


def acquire_cues(opts, work, report, cancel):
    """(cues, spoken_lang) for the job, cached per source."""
    media = opts["media"]
    src = opts.get("source", "youtube")
    # v1.4.11: "AI listens" with a pick that can't hear (GLM, DeepSeek,
    # Claude...) on a YouTube-linked file: YouTube's own captions are the
    # words, the pick translates them. Only when YouTube has none does a
    # model that CAN hear get borrowed. Own cache key, so switching to a
    # model that hears later doesn't reuse YouTube's lines.
    yt_first = False
    switched_off = False
    if src == "ai" and (opts.get("video_id") or ""):
        from app.core.captions import gemini_media as G
        yt_first = not G.pick_hears("captions")
        # v1.4.13: the Settings switch, not the model, made that call
        switched_off = yt_first and G.pick_hears("captions", honor_switch=False)
    cache = os.path.join(work, _cache_key(dict(opts, source="ai_yt") if yt_first else opts))
    lang = ""
    cues = None
    # v1.4.12: where the words came from ("youtube" | "ears" | "file"), so
    # the captions step knows YouTube's raw auto captions need polishing
    via = {"youtube": "youtube", "file": "file"}.get(src, "")
    if os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                data = json.load(f)
            cues, lang = [C.Cue.from_dict(d) for d in data["cues"]], data.get("lang", "")
            via = data.get("via") or via or ("youtube" if yt_first else "ears")
        except Exception:                # noqa: BLE001
            cues = None
    if cues is None:
        if src == "youtube":
            report("Fetching YouTube captions", 0.02)
            vid = opts.get("video_id") or ""
            if not vid:
                raise RuntimeError("This file isn't linked to a YouTube video. Pick “AI listens” or a subtitle file.")
            try:
                cues, lang = S.youtube_cues(vid, opts.get("track", ""), opts.get("source_lang", ""))
                a, b = float(opts.get("clip_start") or 0), float(opts.get("clip_end") or 0)
                if a > 0 or b > 0:
                    cues = C.window(cues, a, b)
            except Exception:            # noqa: BLE001
                raise
        elif src == "file":
            report("Reading the subtitle file", 0.02)
            cues, lang = S.file_cues(opts.get("sub_path") or "")
            # A subtitle file chosen BEFORE the cut is timed against the whole
            # source video; window it onto the clip like YouTube's cues.
            if opts.get("window_file"):
                a, b = float(opts.get("clip_start") or 0), float(opts.get("clip_end") or 0)
                if a > 0 or b > 0:
                    cues = C.window(cues, a, b)
        else:
            from app.core.captions import gemini_media as G
            if yt_first:
                report("Fetching YouTube captions" + ("" if switched_off else " (the picked model can't hear audio)"), 0.02)
                try:
                    cues, lang = S.youtube_cues(opts["video_id"], "", opts.get("source_lang", ""))
                    a, b = float(opts.get("clip_start") or 0), float(opts.get("clip_end") or 0)
                    if cues and (a > 0 or b > 0):
                        cues = C.window(cues, a, b)
                except Exception:        # noqa: BLE001 - no captions: borrow ears below
                    cues = None
                if cues:
                    via = "youtube"
                    opts["_via_note"] = ("Gemini listening is off in Settings: used YouTube's captions "
                                         "and Gemini made them fluent" if switched_off else
                                         "The picked model doesn't listen (only Google's own Gemini does): "
                                         "used YouTube's captions and the AI made them fluent")
        if src not in ("youtube", "file") and not cues:
            via = "ears"
            try:
                report("AI is listening", 0.02)
                cues, lang = S.ai_transcribe(media, on_progress=lambda p: report("AI is listening", 0.02 + 0.28 * p),
                                             should_cancel=cancel, workdir=work)
            except (G.ListenUnsupported, G.GeminiUnavailable):
                # v1.4.10: no model here can hear audio. A YouTube-linked file
                # still has YouTube's own words: use those, and the picked
                # model (DeepSeek, Claude...) translates them as usual.
                vid = opts.get("video_id") or ""
                if not vid:
                    raise
                report("Using YouTube's captions (no AI here can hear audio)", 0.02)
                via = "youtube"
                cues, lang = S.youtube_cues(vid, "", opts.get("source_lang", ""))
                a, b = float(opts.get("clip_start") or 0), float(opts.get("clip_end") or 0)
                if a > 0 or b > 0:
                    cues = C.window(cues, a, b)
        if not cues:
            raise RuntimeError("No spoken lines were found.")
        with open(cache, "w", encoding="utf-8") as f:
            json.dump({"cues": [c.to_dict() for c in cues], "lang": lang, "via": via}, f, ensure_ascii=False)
    opts["_words_via"] = via
    cues = _shift(cues, float(opts.get("offset") or 0))
    lang = (opts.get("source_lang") or lang or "").split("-")[0]
    return cues, lang


def _shift(cues, off):
    if abs(off) <= 0.01:
        return cues
    cues = [c.copy(start=max(0.0, c.start + off), end=max(0.05, c.end + off),
                   words=[(max(0.0, a + off), max(0.0, b + off), w) for a, b, w in c.words])
            for c in cues]
    return [c for c in cues if c.end > 0.1]


def youtube_native(opts, work, tgt, report):
    """v1.3.3: captions YouTube already has in the target language.

    The old flow always fetched the spoken-language track and sent it to the
    AI whenever a language was picked, even when the video carried an
    uploaded Persian / Spanish / ... track. Now, for source = youtube with no
    hand-picked track:
      * an uploaded track in `tgt` is used as-is (no AI, human quality);
      * translate_via = "youtube" also accepts YouTube's free auto-translation.
    Returns (cues, kind) or None. Cached per file + clip window."""
    if (opts.get("source") or "youtube") != "youtube" or opts.get("track"):
        return None
    vid = opts.get("video_id") or ""
    if not vid or not tgt:
        return None
    machine = (opts.get("translate_via") or "ai") == "youtube"
    a, b = float(opts.get("clip_start") or 0), float(opts.get("clip_end") or 0)
    cache = os.path.join(work, f"ytnative_{tgt}_{int(machine)}_{_sha(a, b)}.json")
    data = None
    if os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:                # noqa: BLE001
            data = None
    if data is None:
        report("Looking for YouTube's own captions", 0.03)
        try:
            got = S.youtube_cues_in(vid, tgt, allow_machine=machine)
        except Exception:                # noqa: BLE001 - fall back to the normal path
            return None
        if got:
            cues, _lang, kind = got
            if a > 0 or b > 0:
                cues = C.window(cues, a, b)
            data = {"kind": kind, "cues": [c.to_dict() for c in cues]} if cues else {"kind": ""}
        else:
            data = {"kind": ""}
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    if not data.get("kind") or not data.get("cues"):
        return None
    cues = [C.Cue.from_dict(d) for d in data["cues"]]
    return _shift(cues, float(opts.get("offset") or 0)), data["kind"]


def _stem(media):
    return os.path.splitext(media)[0]


def run_captions(opts, report, cancel):
    media = opts["media"]
    if not os.path.isfile(media):
        raise RuntimeError("The video file is missing.")
    work = work_dir(media)
    tgt = (opts.get("target") or "").split("|")[0]
    native = youtube_native(opts, work, tgt, report) if tgt else None
    via_note = ""
    if native:
        cues, kind = native
        src_lang = tgt.split("-")[0]
        via_note = {"uploaded": "Used YouTube's own captions in this language, no AI",
                    "auto": "Used YouTube's own auto-generated captions in this language, no AI"
                    }.get(kind, "Used YouTube's free auto-translation, no AI")
    else:
        cues, src_lang = acquire_cues(opts, work, report, cancel)
        via_note = opts.get("_via_note", "")
    info = R.probe(media)
    allow_ai = bool(opts.get("allow_ai", True))
    if not allow_ai and tgt and src_lang and tgt.split("-")[0] != src_lang.split("-")[0]:
        # v1.4: YouTube's own captions are never sent to an AI. The track
        # the user picked IS the language; keep its words as they are.
        via_note = via_note or "YouTube's own captions, no AI"
        tgt = ""
    out_lang = tgt or src_lang or "en"
    shown = cues
    secondary = None
    polished = False
    # v1.4.12: YouTube's AUTO captions (no punctuation, misheard words) that
    # stand in for a model that doesn't listen: the AI makes them fluent
    raw_asr = (not native and opts.get("_words_via") == "youtube" and C.looks_raw_asr(cues))
    translating = (not native and allow_ai and tgt
                   and (not src_lang or tgt.split("-")[0] != src_lang.split("-")[0]))
    if (raw_asr and not translating and allow_ai and opts.get("source") == "ai"
            and T.ai_ready()):
        report("Making YouTube's captions fluent", 0.30)
        pl_cache = os.path.join(work, f"polish_{_sha(len(cues), cues[0].start, cues[-1].text, src_lang)}.json")
        if os.path.isfile(pl_cache):
            with open(pl_cache, encoding="utf-8") as f:
                shown = C.load_json(f.read())
            polished = True
        else:
            try:
                shown, _b = T.translate_cues(cues, src_lang or "", src_lang, opts.get("title", ""), "polish",
                                             lambda p: report("Making YouTube's captions fluent", 0.30 + 0.4 * p),
                                             cancel, cache_dir=work)
                polished = True
                with open(pl_cache, "w", encoding="utf-8") as f:
                    f.write(C.dump_json(shown))
            except R.Cancelled:
                raise
            except Exception as e:       # noqa: BLE001 - raw lines still work
                if T._is_fatal(e):
                    raise
                shown = cues
                via_note = " · ".join(x for x in (via_note, "Couldn't polish YouTube's captions, kept them as they are") if x)
    # v2.0: how the translation talks + the native-editor pass
    try:
        from app.config import load_app_config
        _cfg = load_app_config() or {}
    except Exception:                    # noqa: BLE001
        _cfg = {}
    register = str(opts.get("register") or _cfg.get("caption_register") or "natural")
    refine = bool(opts.get("refine", _cfg.get("caption_refine", True)))
    if translating:
        report("Translating", 0.30)
        tr_cache = os.path.join(work, f"subs_{tgt}_{int(raw_asr)}_{_sha(len(cues), cues[0].start, cues[-1].text, 'nat2', register, refine)}.json")
        if os.path.isfile(tr_cache):
            with open(tr_cache, encoding="utf-8") as f:
                shown = C.load_json(f.read())
        else:
            try:
                shown, brief = T.translate_cues(cues, tgt, src_lang, opts.get("title", ""), "subtitle",
                                                lambda p: report("Translating", 0.30 + 0.4 * p), cancel,
                                                cache_dir=work, raw_asr=raw_asr,
                                                register=register, refine=refine)
            except R.Cancelled:
                raise
            except Exception as e:       # noqa: BLE001
                if T.ai_ready():
                    raise
                raise RuntimeError(
                    "No captions in that language on YouTube, and translating needs an AI "
                    "provider (none is set up). Pick “Original” to keep the spoken language "
                    "with no AI, or add a provider in Settings → AI Providers.") from e
            if not src_lang:
                src_lang = str((brief or {}).get("source_lang") or "").split("-")[0]
            with open(tr_cache, "w", encoding="utf-8") as f:
                f.write(C.dump_json(shown))
        if opts.get("bilingual"):
            secondary = [c.meta.get("src", "") for c in shown]
    if cancel():
        raise R.Cancelled()

    # v1.9: no full stop closing a caption line (cues.drop_end_period)
    keep_src = shown is cues
    shown = C.drop_speaker_marks(C.drop_end_period(shown))
    cues = shown if keep_src else C.drop_speaker_marks(C.drop_end_period(cues))
    # v2.0: Persian typography + reading time + no flicker (free, no AI)
    shown = P.finish(shown, out_lang)
    if keep_src:
        cues = shown
    if secondary is not None:
        secondary = [c.meta.get("src", "") for c in shown]

    stem = _stem(media)
    srt_main = unique_path(f"{stem}.{out_lang}.srt")
    with open(srt_main, "w", encoding="utf-8") as f:
        f.write(C.to_srt(shown, L.line_limit(out_lang)))
    files = [srt_main]
    srt_orig = ""
    if shown is not cues and not polished:
        srt_orig = unique_path(f"{stem}.{src_lang or 'orig'}.srt")
        with open(srt_orig, "w", encoding="utf-8") as f:
            f.write(C.to_srt(cues, L.line_limit(src_lang)))
        files.append(srt_orig)

    mode = opts.get("output", "burn")
    note = ""
    if mode == "burn" and (not info["has_video"] or not R.has_filter("subtitles")):
        note = ("This ffmpeg build can't draw captions, so they were added as a subtitle track."
                if info["has_video"] else "Audio file: captions were saved as .srt.")
        mode = "soft" if info["has_video"] else "srt"
    video_out = ""
    ext = R.out_container(media)
    if mode == "burn":
        report("Drawing the captions", 0.72)
        ass = ST.build_ass(shown, opts.get("style", "clean"), out_lang, info["width"], info["height"],
                           opts.get("size", "m"), opts.get("position", "bottom"),
                           secondary, src_lang)
        video_out = unique_path(f"{stem}.{out_lang}.captions.{ext}")
        ok, err = R.burn(media, ass, video_out, os.path.join(work, "burn"), info["duration"],
                         lambda p: report("Drawing the captions", 0.72 + 0.27 * p), cancel)
        if not ok:
            raise RuntimeError("Couldn't draw the captions: " + err)
    elif mode == "soft":
        report("Adding the subtitle track", 0.8)
        video_out = unique_path(f"{stem}.{out_lang}.subs.{'mkv' if ext == 'mkv' else 'mp4'}")
        tracks = [(srt_main, L.iso3(out_lang), L.name_of(out_lang), True)]
        if srt_orig:
            tracks.append((srt_orig, L.iso3(src_lang), L.name_of(src_lang) or "Original", False))
        ok, err = R.soft_mux(media, tracks, video_out, info["duration"],
                             lambda p: report("Adding the subtitle track", 0.8 + 0.19 * p), cancel)
        if not ok:
            raise RuntimeError("Couldn't add the subtitle track: " + err)
    report("Done", 1.0)
    untranslated = sum(1 for c in shown if c.meta.get("untranslated"))
    note = " · ".join(x for x in (via_note, note) if x)
    return {"video": video_out, "files": files, "lang": out_lang, "src_lang": src_lang,
            "lines": len(shown), "note": note, "untranslated": untranslated,
            "via": ("youtube-" + native[1]) if native else ("ai" if shown is not cues else "")}


def add_soft_track(video, srt, lang, report=None, cancel=None):
    """v1.4: put a finished .srt into a finished video as a switchable track
    (no re-encode) and keep the same file name. Returns the video path (the
    original one when muxing isn't possible)."""
    if not video or not srt or not os.path.isfile(video) or not os.path.isfile(srt):
        return video
    base, ext = os.path.splitext(video)
    tmp = f"{base}.subs-tmp{ext}"
    info = R.probe(video)
    tracks = [(srt, L.iso3(lang), L.name_of(lang) or "Captions", True)]
    ok, _err = R.soft_mux(video, tracks, tmp, info.get("duration") or 0,
                          (lambda p: report("Adding the subtitle track", 0.97 + 0.03 * p)) if report else None,
                          cancel or (lambda: False))
    if not ok or not os.path.isfile(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
        return video
    try:
        FS.replace(tmp, video, keep_existing=False)
    except OSError:
        return tmp
    return video
