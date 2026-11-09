"""app.qml_backend.vm.voice_studio_vm - YouTube Hunter (QML).

ViewModel behind the Voice Studio sheet (components/VoiceStudioSheet.qml):
captions in five styles, AI translation, and AI dubbing, for any finished
download or cut clip. Exposed to QML as `VoiceStudio`.

Jobs run on worker threads (app.core.captions.jobs) and keep running when the
sheet is closed; progress comes back over queued signals and is coalesced to
~10 updates a second so a fast ffmpeg pass never floods the GUI thread.
"""

import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.core.captions import gemini_media as G
from app.core.captions import jobs as J
from app.core.captions import languages as L
from app.core.captions import render as R
from app.core.captions import sources as S
from app.core.captions import styles as ST
from app.core.captions import translate as T
from app.core.ai import friendly as FR
from app.core.utils import local_path_from_url
from app.crash import log_crash
from app.i18n import tr
from app.qml_backend.vm import _post_adopt as PA

try:
    from app.qml_backend.desktop_notifier import announce
except Exception:                         # noqa: BLE001
    def announce(*_a, **_k):
        return False


def _queue_key(video_id, path=""):
    """Where a queued caption/dub choice waits: the video id when known,
    otherwise the local file it was chosen for."""
    vid = (video_id or "").strip()
    if vid:
        return "yt:" + vid
    if path:
        return "local:" + os.path.normcase(os.path.abspath(path))
    return ""


class VoiceStudioVM(QObject):
    changed = Signal()           # target / readiness / tracks / queue
    jobsChanged = Signal()
    tracksChanged = Signal()     # v1.3.3: per-video track cache (inline deck)
    toast = Signal(str, str)
    # v1.4.7: a finished job moved a download ("download") or a clip
    # ("clip") onto its new captioned / dubbed file: that list refreshes
    adopted = Signal(str)

    _tracksReady = Signal(str, "QVariant", str)        # (token, data, error)
    _progress = Signal(str, str, float)                # (job, stage, fraction)
    _finished = Signal(str, bool, "QVariant", str)     # (job, ok, result, error)
    _runQueued = Signal(str, str, str, float, float, str)
    _deckTracks = Signal(str, "QVariant", str)          # (video id, data, error)
    _pipeDone = Signal(str)                             # v1.4: pipeline id

    _instance = None

    @classmethod
    def instance(cls):
        """The one studio the app shares; other view-models schedule with it."""
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        VoiceStudioVM._instance = self
        self._target = {}
        self._queue_mode = False
        self._queued = {}                 # queue key -> {"captions": opts, "dub": opts}
        self._tracks = []
        self._spoken = ""
        self._tracks_loading = False
        self._tracks_error = ""
        self._tracks_token = ""
        self._media_info = {}
        self._sidecar = ""
        self._gemini = False
        self._ai = False
        self._ai_label = ""
        self._listen = False              # v1.4.9: "AI listens" can run for captions
        self._listen_label = ""           # "Apinex · gemini-3.8-flash"
        self._listen_audio = True         # v1.4.13: Settings switch (Gemini listens)
        self._jobs = []                   # newest first
        self._cancel = {}                 # job id -> threading.Event
        self._job_opts = {}               # job id -> (kind, opts): Resume re-runs these
        self._queue_rev = 0
        self._track_cache = {}            # video id -> youtube_tracks() data (+loading/error)
        self._tracks_rev = 0
        self._ready_known = False
        self._dirty = False
        self._flush = QTimer(self)
        self._flush.setInterval(100)
        self._flush.setSingleShot(True)
        self._flush.timeout.connect(self._emit_jobs)
        self._tracksReady.connect(self._on_tracks)
        self._progress.connect(self._on_progress)
        self._finished.connect(self._on_finished)
        self._runQueued.connect(self._do_run_queued)
        self._deckTracks.connect(self._on_deck_tracks)
        # v1.4 post-production: captions / dub that belong to ONE download or
        # cut run back to back as a pipeline, and the owner (the download
        # row, the Cutter dock, the YouTube panel) shows them as the next
        # steps of its own progress, not as a separate thing to go find.
        self._pipes = {}                  # pipeline id -> state (GUI thread only)
        self._pipeDone.connect(self._on_pipe_done)

    # ── static catalogues ──────────────────────────────────────────────
    @Property("QVariantList", constant=True)
    def styles(self):
        return [{"key": s["key"], "name": tr(s["name"]), "desc": tr(s["desc"]), **s["preview"]}
                for s in ST.STYLES]

    @Property("QVariantList", constant=True)
    def languages(self):
        return [{"code": l["code"], "label": l["native"] if l["native"] == l["name"]
                 else f"{l['native']} · {l['name']}", "rtl": l["rtl"]} for l in L.LANGUAGES]

    @Property("QVariantList", constant=True)
    def languageLabels(self):
        return [x["label"] for x in self.languages]

    @Property("QVariantList", constant=True)
    def voices(self):
        return [{"name": n, "gender": g, "character": c,
                 "label": f"{n} · {tr(g)}, {tr(c)}"} for n, g, c in G.VOICES]

    @Property("QVariantList", constant=True)
    def voiceLabels(self):
        return [v["label"] for v in self.voices]

    @Property("QVariantList", constant=True)
    def ttsModels(self):
        return [{"key": m["key"], "label": tr(m["name"]), "desc": tr(m["desc"])} for m in G.TTS_MODELS]

    @Slot(str, result=int)
    def languageIndex(self, code):
        for i, l in enumerate(L.LANGUAGES):
            if l["code"].lower() == (code or "").lower():
                return i
        lang = L.get(code)
        if lang:
            return L.LANGUAGES.index(lang)
        return -1

    @Slot(int, result=str)
    def languageCode(self, i):
        return L.LANGUAGES[i]["code"] if 0 <= i < len(L.LANGUAGES) else ""

    @Slot(str, result=str)
    def languageName(self, code):
        return L.native_of(code)

    # ── target ─────────────────────────────────────────────────────────
    @Property("QVariantMap", notify=changed)
    def target(self):
        return dict(self._target)

    @Property("QVariantList", notify=changed)
    def ytTracks(self):
        return list(self._tracks)

    @Property("QVariantList", notify=changed)
    def ytTrackLabels(self):
        return [t["label"] for t in self._tracks]

    @Property(bool, notify=changed)
    def ytLoading(self):
        return self._tracks_loading

    @Property(str, notify=changed)
    def ytError(self):
        return self._tracks_error

    @Property(str, notify=changed)
    def spokenLang(self):
        return self._spoken

    @Property(str, notify=changed)
    def sidecarPath(self):
        return self._sidecar

    @Property(bool, notify=changed)
    def hasVideo(self):
        return bool(self._media_info.get("has_video", True))

    @Property(bool, notify=changed)
    def isClip(self):
        return float(self._target.get("clipEnd") or 0) > 0

    @Property(bool, notify=changed)
    def geminiReady(self):
        return self._gemini

    @Property(bool, notify=changed)
    def aiReady(self):
        return self._ai

    @Property(str, notify=changed)
    def aiLabel(self):
        return self._ai_label

    @Property(bool, notify=changed)
    def listenReady(self):
        """v1.4.9: the Captions provider (or a Gemini fallback) can listen."""
        return self._listen

    @Property(str, notify=changed)
    def listenLabel(self):
        """Who listens for "AI listens": the SAME pick that translates."""
        return self._listen_label

    @Property(bool, notify=changed)
    def listenAudio(self):
        """v1.4.13: Gemini really hears YouTube videos. False = YouTube's
        captions are the words and the Captions AI makes them fluent (the
        Settings switch is off, or the pick isn't Google's own Gemini)."""
        return self._listen_audio

    @Property(bool, notify=changed)
    def queueMode(self):
        """The sheet is choosing captions/dubbing for a file that doesn't
        exist yet; the job runs when the cut or download finishes."""
        return self._queue_mode

    @Property("QVariantMap", notify=changed)
    def queuedKinds(self):
        key = _queue_key(self._target.get("videoId", ""), self._target.get("path", ""))
        slot = self._queued.get(key, {}) if key else {}
        return {"captions": bool(slot.get("captions")), "dub": bool(slot.get("dub"))}

    @Slot(str, str, str, float, float)
    def open(self, path, title, video_id, clip_start, clip_end):
        """Point the sheet at a file (a finished download or clip)."""
        self._open(path, title, video_id, clip_start, clip_end, queue=False)

    @Slot(str, str, str, float, float)
    def openQueued(self, path, title, video_id, clip_start, clip_end):
        """Point the sheet at a future file (a download or a clip being cut)."""
        self._open(path, title, video_id, clip_start, clip_end, queue=True)

    def _open(self, path, title, video_id, clip_start, clip_end, queue):
        path = local_path_from_url(path) if path.startswith("file:") else path
        self._target = {"path": path, "title": title or os.path.basename(path or ""),
                        "videoId": (video_id or "").strip(), "clipStart": float(clip_start or 0),
                        "clipEnd": float(clip_end or 0),
                        "fileName": os.path.basename(path or ""),
                        "exists": bool(path) and os.path.isfile(path)}
        self._queue_mode = bool(queue)
        self._tracks, self._spoken, self._tracks_error = [], "", ""
        self._sidecar = S.sidecar_for(path) if path else ""
        self._media_info = {}
        self.refreshReadiness()
        token = uuid.uuid4().hex[:8]
        self._tracks_token = token
        self._tracks_loading = bool(self._target["videoId"])
        self.changed.emit()
        if self._target["exists"]:
            threading.Thread(target=self._probe_media, args=(path, token), daemon=True).start()
        if self._target["videoId"]:
            threading.Thread(target=self._load_tracks, args=(self._target["videoId"], token),
                             daemon=True).start()

    def _probe_media(self, path, token):
        try:
            info = R.probe(path)
        except Exception:                 # noqa: BLE001
            info = {}
        self._tracksReady.emit(token, {"media": info}, "")

    def _load_tracks(self, vid, token):
        try:
            data = S.youtube_tracks(vid)
            self._tracksReady.emit(token, data, "")
        except Exception as e:            # noqa: BLE001
            self._tracksReady.emit(token, {}, str(e) or tr("Couldn't read YouTube's captions."))

    def _on_tracks(self, token, data, err):
        if token != self._tracks_token:
            return
        data = data or {}
        if "media" in data:
            self._media_info = data["media"] or {}
            self.changed.emit()
            return
        self._tracks_loading = False
        vid = self._target.get("videoId", "")
        if vid and data.get("tracks") is not None:
            self._on_deck_tracks(vid, data, err)      # share with the inline decks
        self._tracks = list(data.get("tracks") or [])
        self._spoken = data.get("spoken") or ""
        self._tracks_error = "" if self._tracks else (err or tr("No captions on YouTube for this video."))
        self.changed.emit()

    @Slot()
    def refreshReadiness(self):
        """Re-read which AI keys exist. v1.3.3: only notifies when the answer
        changed, so the inline decks can call it on show without waking
        every binding on the page in its entrance frame."""
        gem, ai = G.has_gemini(), T.ai_ready()
        label = T.provider_label() if ai else ""
        lis = G.can_listen("captions")
        lis_label = G.listen_label("captions") if lis else ""
        hears = bool(G.pick_hears("captions"))
        now = (gem, ai, label, lis, lis_label, hears)
        if self._ready_known and now == (self._gemini, self._ai, self._ai_label,
                                         self._listen, self._listen_label, self._listen_audio):
            return
        self._ready_known = True
        (self._gemini, self._ai, self._ai_label, self._listen, self._listen_label,
         self._listen_audio) = now
        self.changed.emit()

    # ── v1.3.3: YouTube tracks for the inline decks ────────────────────
    @Property(int, notify=tracksChanged)
    def tracksRev(self):
        return self._tracks_rev

    @Slot(str)
    def loadTracks(self, video_id):
        """Read what captions YouTube has for a video, once per session, on a
        worker thread. The deck reads the answer with tracksFor()."""
        vid = (video_id or "").strip()
        if not vid:
            return
        cur = self._track_cache.get(vid)
        if cur and (cur.get("loading") or not cur.get("error")):
            return
        self._track_cache[vid] = {"loading": True}
        self._tracks_rev += 1
        self.tracksChanged.emit()

        def work():
            try:
                self._deckTracks.emit(vid, S.youtube_tracks(vid), "")
            except Exception as e:        # noqa: BLE001
                self._deckTracks.emit(vid, {}, str(e) or tr("Couldn't read YouTube's captions."))
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _yt_lang_label(item):
        """'English · auto captions' / 'فارسی · Persian · uploaded' /
        'Español · Spanish · YouTube translate' for the deck's dropdown."""
        code = item.get("lang") or ""
        native, name = L.native_of(code), L.name_of(code)
        lang = native if (not name or native == name) else f"{native} · {name}"
        kind = {"uploaded": tr("uploaded"), "auto": tr("auto-generated"),
                "translated": tr("YouTube translate")}.get(item.get("kind"), "")
        return f"{lang} · {kind}" if kind else lang

    def _on_deck_tracks(self, vid, data, err):
        data = dict(data or {})
        tracks = list(data.get("tracks") or [])
        langs = [dict(x) for x in (data.get("languages") or [])]
        if not langs:
            # an older source module: the tracks are all there is
            langs = [{"key": t.get("key", ""), "lang": (t.get("lang") or "").split("-")[0],
                      "kind": "auto" if t.get("auto") else "uploaded"} for t in tracks]
        for x in langs:
            x["label"] = self._yt_lang_label(x)
        self._track_cache[vid] = {
            "loading": False,
            "tracks": tracks,
            "labels": [t.get("label", "") for t in tracks],
            "spoken": data.get("spoken") or "",
            "uploaded": list(data.get("uploaded") or []),
            "translatable": list(data.get("translatable") or []),
            # v1.4.6: every language YouTube has an auto-generated track in
            "auto_langs": list(data.get("auto_langs") or []),
            # v1.4: every language YouTube itself can show this video's
            # captions in (uploaded, auto, YouTube translate). No AI.
            "languages": langs,
            "langLabels": [x["label"] for x in langs],
            "error": "" if (tracks or langs) else (err or tr("No captions on YouTube for this video.")),
        }
        self._tracks_rev += 1
        self.tracksChanged.emit()

    @Slot(str, result="QVariantMap")
    def tracksFor(self, video_id):
        d = self._track_cache.get((video_id or "").strip())
        if not d:
            return {"loading": False, "tracks": [], "labels": [], "spoken": "",
                    "uploaded": [], "translatable": [], "languages": [], "langLabels": [],
                    "error": "", "known": False}
        out = dict(d)
        out.setdefault("tracks", [])
        out.setdefault("labels", [])
        out.setdefault("languages", [])
        out.setdefault("langLabels", [])
        out.setdefault("spoken", "")
        out.setdefault("uploaded", [])
        out.setdefault("translatable", [])
        out.setdefault("error", "")
        out["known"] = not d.get("loading")
        return out

    def _youtube_has(self, video_id, target, machine):
        """True when YouTube itself serves `target` for this video (so the
        caption job needs no AI). Only answers from the cache."""
        d = self._track_cache.get((video_id or "").strip()) or {}
        want = (target or "").split("|")[0].split("-")[0].lower()
        if not want:
            return True
        if not d or d.get("loading") or "uploaded" not in d:
            # not read yet: let the job look (it uses YouTube's track when
            # there is one and fails with a clear message when there isn't)
            return True
        if want in (d.get("uploaded") or []):
            return True
        if want in (d.get("auto_langs") or []):
            return True
        if want == (d.get("spoken") or "").split("-")[0].lower():
            return True
        return bool(machine) and want in (d.get("translatable") or [])

    def _needs_ai(self, opts):
        """A caption job needs the AI only to TRANSLATE, and only when YouTube
        has no track in that language (v1.3.3: styling YouTube's own captions
        never asks for a provider)."""
        if not opts.get("target"):
            return False
        if (opts.get("source") or "youtube") == "youtube" and opts.get("allow_ai") is False:
            return False                  # v1.4: YouTube's own captions, as served
        if (opts.get("source") or "youtube") == "youtube":
            track = opts.get("track") or ""
            if track:
                # "sub:fa" / "auto:en-orig": a hand-picked track already in
                # the target language needs no translation
                lang = track.split(":", 1)[-1].replace("-orig", "").split("-")[0].lower()
                want = str(opts.get("target")).split("|")[0].split("-")[0].lower()
                return lang != want
            return not self._youtube_has(opts.get("video_id", ""), opts.get("target"),
                                         (opts.get("translate_via") or "ai") == "youtube")
        return True

    @Slot(str, result=str)
    def pathFromUrl(self, url):
        return local_path_from_url(url) if url else ""

    # ── jobs ───────────────────────────────────────────────────────────
    @Property("QVariantList", notify=jobsChanged)
    def jobs(self):
        return [dict(j) for j in self._jobs]

    @Property(int, notify=jobsChanged)
    def runningCount(self):
        return sum(1 for j in self._jobs if j["state"] == "running")

    def _opts(self, raw):
        o = dict(raw or {})
        t = self._target
        o.setdefault("media", t.get("path", ""))
        o.setdefault("title", t.get("title", ""))
        o.setdefault("video_id", t.get("videoId", ""))
        o.setdefault("clip_start", t.get("clipStart", 0))
        o.setdefault("clip_end", t.get("clipEnd", 0))
        if o.get("sub_path", "").startswith("file:"):
            o["sub_path"] = local_path_from_url(o["sub_path"])
        return o

    def _start(self, kind, raw):
        opts = self._opts(raw)
        if opts.get("media") and os.path.isfile(opts["media"]):
            # v1.4.7: stack on the row's current version, or start over from
            # the plain file when this step is already in it
            opts = PA.prepare(kind, opts)
        if not opts.get("media") or not os.path.isfile(opts["media"]):
            self.toast.emit(tr("The file couldn't be found — it may have been moved or deleted."), "error")
            return ""
        if kind == "dub" and not G.has_gemini():
            self.toast.emit(tr("Dubbing needs a Gemini model: a Google Gemini key, or a provider that serves Gemini (Settings → Which AI does what → AI dub)."), "error")
            return ""
        if (opts.get("source") == "ai" and kind != "dub" and not G.can_listen("captions")
                and not opts.get("video_id")):        # v1.4.10: YouTube's words will do
            self.toast.emit(self._listen_why(), "error")
            return ""
        if kind == "captions" and self._needs_ai(opts) and not T.ai_ready():
            self.toast.emit(tr("Translation needs an AI provider (Settings → AI Providers). "
                               "Pick “Original” to style YouTube's captions without AI."), "error")
            return ""
        jid = uuid.uuid4().hex[:10]
        tgt = (opts.get("target") or "").split("|")[0]
        self._jobs.insert(0, {
            "id": jid, "kind": kind, "title": opts.get("title") or os.path.basename(opts["media"]),
            "lang": tgt, "langName": L.native_of(tgt) if tgt else "",
            "preview": bool(opts.get("preview_sec")),
            "stage": tr("Starting"), "pct": 0.0, "state": "running", "message": "",
            "video": "", "files": [], "startedAt": time.time(), "eta": "",
            "canResume": False, "quota": False})
        ev = threading.Event()
        self._cancel[jid] = ev
        self._job_opts[jid] = (kind, dict(opts))
        fn = J.run_dub if kind == "dub" else J.run_captions
        threading.Thread(target=self._worker, args=(jid, fn, opts, ev), daemon=True).start()
        self._emit_jobs()
        return jid

    @Slot("QVariantMap", result=str)
    def startCaptions(self, opts):
        return self._queue_job("captions", opts) if self._queue_mode else self._start("captions", opts)

    @Slot("QVariantMap", result=str)
    def startDub(self, opts):
        return self._queue_job("dub", opts) if self._queue_mode else self._start("dub", opts)

    def _queue_job(self, kind, raw):
        """Keep the choices until the cut/download exists, with the same
        provider checks a live run would make before starting."""
        opts = self._opts(raw)
        if kind == "dub" and not G.has_gemini():
            self.toast.emit(tr("Dubbing needs a Gemini model: a Google Gemini key, or a provider that serves Gemini (Settings → Which AI does what → AI dub)."), "error")
            return ""
        if (opts.get("source") == "ai" and kind != "dub" and not G.can_listen("captions")
                and not opts.get("video_id")):        # v1.4.10: YouTube's words will do
            self.toast.emit(self._listen_why(), "error")
            return ""
        if kind == "captions" and self._needs_ai(opts) and not T.ai_ready():
            self.toast.emit(tr("Translation needs an AI provider (Settings → AI Providers). "
                               "Pick “Original” to style YouTube's captions without AI."), "error")
            return ""
        key = _queue_key(self._target.get("videoId", ""), self._target.get("path", ""))
        if not key:
            self.toast.emit(tr("The file isn't ready yet — open this again once it is."), "error")
            return ""
        self._queued.setdefault(key, {})[kind] = opts
        self._bump_queue()
        self.toast.emit(tr("Captions queued — they run when the file is ready.") if kind == "captions"
                        else tr("AI dub queued — it runs when the file is ready."), "success")
        return "queued"

    def runQueued(self, video_id, media, title="", clip_start=None, clip_end=None,
                  fallback_key=""):
        """Start whatever was queued for a file that just became ready. Safe
        to call from a worker thread: the signal lands on the GUI thread."""
        self._runQueued.emit(str(video_id or ""), str(media or ""), str(title or ""),
                             float(clip_start or 0), float(clip_end or 0),
                             str(fallback_key or ""))

    @Slot(str, str, str, float, float, str)
    def _do_run_queued(self, video_id, media, title, clip_start, clip_end, fallback_key):
        if not media or not os.path.isfile(media):
            return
        keys = [_queue_key("", media)]
        primary = _queue_key(video_id)
        if primary:
            keys.insert(0, primary)
        if fallback_key:
            keys.insert(0, fallback_key)
        entry = None
        for key in keys:
            if key in self._queued:
                entry = self._queued.pop(key)
                break
        if not entry:
            return
        self._bump_queue()
        for kind in ("captions", "dub"):
            opts = entry.get(kind)
            if not opts:
                continue
            opts = dict(opts)
            opts["media"] = media
            if title:
                opts["title"] = title
            if video_id:
                opts["video_id"] = video_id
            if clip_start or clip_end:
                opts["clip_start"] = float(clip_start or 0)
                opts["clip_end"] = float(clip_end or 0)
            if (opts.get("source") == "file"
                    and (opts.get("clip_start") or opts.get("clip_end"))):
                opts["window_file"] = True
            self._start(kind, opts)

    def _worker(self, jid, fn, opts, ev):
        """Run one job; returns its result dict (None when it didn't finish).
        The outcome always lands on the GUI thread through _finished."""
        last = [0.0]

        def report(stage, pct):
            now = time.monotonic()
            if pct >= 1.0 or now - last[0] > 0.08:
                last[0] = now
                self._progress.emit(jid, tr(stage), float(pct))

        try:
            res = fn(opts, report, ev.is_set)
            self._finished.emit(jid, True, res, "")
            return res
        except R.Cancelled:
            self._finished.emit(jid, False, {}, "cancelled")
        except Exception as e:            # noqa: BLE001
            if ev.is_set():
                self._finished.emit(jid, False, {}, "cancelled")
                return None
            log_crash("Voice Studio job failed:\n" + traceback.format_exc())
            self._finished.emit(jid, False, {}, str(e) or tr("Something went wrong."))
        return None

    def _job(self, jid):
        return next((j for j in self._jobs if j["id"] == jid), None)

    def _on_progress(self, jid, stage, pct):
        j = self._job(jid)
        if not j or j["state"] != "running":
            return
        j["stage"], j["pct"] = stage, max(j["pct"], min(1.0, pct))
        el = time.time() - j["startedAt"]
        if j["pct"] > 0.08 and el > 8:
            rem = el / j["pct"] - el
            j["eta"] = (tr("about {n} min left").format(n=max(1, round(rem / 60)))
                        if rem > 90 else tr("under a minute left"))
        self._queue_emit()
        if j.get("pipe"):
            self._pipe_tick(j["pipe"])

    def _on_finished(self, jid, ok, result, err):
        j = self._job(jid)
        self._cancel.pop(jid, None)
        if not j:
            return
        res = dict(result or {})
        owner = ""
        if ok:
            j["state"], j["pct"], j["stage"], j["eta"] = "done", 1.0, tr("Ready"), ""
            j["video"] = res.get("video") or ""
            j["files"] = [p for p in ([res.get("srt"), res.get("wav")] + list(res.get("files") or [])) if p]
            bits = []
            if res.get("lines"):
                bits.append(tr("{n} lines").format(n=res["lines"]))
            if res.get("untranslated"):
                bits.append(tr("{n} kept in the original").format(n=res["untranslated"]))
            if res.get("failed_lines"):
                bits.append(tr("{n} lines couldn't be voiced").format(n=res["failed_lines"]))
                j["canResume"] = True
            if res.get("seconds"):
                sec = float(res["seconds"])
                bits.append(tr("took {t}").format(t=(f"{sec / 60:.1f} min" if sec >= 90 else f"{int(sec)} s")))
            if res.get("note"):
                bits.append(res["note"])
            j["message"] = " · ".join(bits)
            title = tr("Dub ready") if j["kind"] == "dub" else tr("Captions ready")
            owner = ""
            if not j.get("pipe"):
                kind_o = self._job_opts.get(jid) or (None, None)
                owner = PA.adopt(kind_o[0], kind_o[1], res) if kind_o[0] else ""
                if owner == "download":
                    title = title + " · " + tr("Downloads now shows this version")
                elif owner == "clip":
                    title = title + " · " + tr("your clip now shows this version")
            if not j.get("pipe"):
                # inside a pipeline the owner announces ONE "ready" at the end
                announce("success", title, os.path.basename(j["video"] or (j["files"] or [""])[0]),
                         path=j["video"] or (j["files"] or [""])[0], icon="translate",
                         source=tr("Voice Studio"))
                self.toast.emit(title, "success")
        elif err == "cancelled":
            j["state"], j["stage"], j["eta"] = "cancelled", tr("Cancelled"), ""
        else:
            # v1.4.9: the raw provider reply is in the crash log; people
            # get a title, one detail line and one hint
            info = FR.explain(err)
            j["state"], j["stage"], j["eta"] = "failed", tr("Failed"), ""
            j["message"] = " · ".join(x for x in (info["title"], info["detail"]) if x)
            j["errTitle"], j["errDetail"], j["errHint"] = info["title"], info["detail"], info["hint"]
            j["errKind"] = info["kind"]
            j["canResume"] = jid in self._job_opts and info["kind"] not in ("listen", "setup", "file")
            j["quota"] = info["kind"] in ("quota", "rate")
            if not j.get("pipe"):
                announce("error", info["title"], info["detail"] or info["hint"], icon="translate",
                         source=tr("Voice Studio"))
        self._emit_jobs()
        if ok and not j.get("pipe") and owner:
            self.adopted.emit(owner)
        if j.get("pipe"):
            self._pipe_step_done(j["pipe"], jid, j["state"], res, err)

    def _queue_emit(self):
        self._dirty = True
        if not self._flush.isActive():
            self._flush.start()

    def _emit_jobs(self):
        self._dirty = False
        self.jobsChanged.emit()

    @Slot(str)
    def cancel(self, jid):
        ev = self._cancel.get(jid)
        if ev:
            ev.set()
            j = self._job(jid)
            if j:
                j["stage"] = tr("Stopping…")
                self._emit_jobs()

    @Slot(str, result=str)
    def resume(self, jid):
        """Run a failed / partial job again with the same choices. Every paid
        step is cached per file, so only what is missing costs anything."""
        kind, opts = self._job_opts.get(jid, (None, None))
        if not kind:
            return ""
        self._jobs = [j for j in self._jobs if j["id"] != jid]
        self._job_opts.pop(jid, None)
        return self._start(kind, dict(opts))

    # ── inline queue (Cutter inspector) ────────────────────────────────
    def _bump_queue(self):
        self._queue_rev += 1
        self.changed.emit()

    @Property(int, notify=changed)
    def queueRev(self):
        """Bumped whenever a queued choice is added, changed, run or removed:
        QML bindings that call queuedFor() read this to re-evaluate."""
        return self._queue_rev

    @Slot(str, str, result="QVariantMap")
    def queuedFor(self, video_id, path):
        key = _queue_key(video_id, path)
        slot = self._queued.get(key, {}) if key else {}
        out = {}
        for kind in ("captions", "dub"):
            o = slot.get(kind)
            if o:
                tgt = (o.get("target") or "").split("|")[0]
                out[kind] = {"target": tgt, "langName": L.native_of(tgt) if tgt else "",
                             "style": o.get("style") or o.get("burn_style") or "",
                             "voiceMode": o.get("voice_mode") or "", "output": o.get("output") or ""}
        return out

    @Slot(str, str, result=str)
    def defaultSource(self, video_id, path):
        if (video_id or "").strip():
            return "youtube"
        side = S.sidecar_for(path) if path else ""
        return "file" if side else "ai"

    @Slot(str, str, str, str, "QVariantMap", result=bool)
    def setQueued(self, kind, video_id, path, title, raw):
        """Queue (or update) captions / a dub for a cut that hasn't happened
        yet, straight from the Cutter's inspector: no sheet, no toast."""
        if kind not in ("captions", "dub"):
            return False
        key = _queue_key(video_id, path)
        if not key:
            return False
        o = dict(raw or {})
        o.setdefault("title", title or "")
        o.setdefault("video_id", (video_id or "").strip())
        if not o.get("source"):
            o["source"] = self.defaultSource(video_id, path)
            if o["source"] == "file":
                o["sub_path"] = S.sidecar_for(path)
                o["window_file"] = True
        self._queued.setdefault(key, {})[kind] = o
        self._bump_queue()
        return True

    @Slot(str, str, str)
    def clearQueued(self, kind, video_id, path):
        key = _queue_key(video_id, path)
        slot = self._queued.get(key)
        if not slot:
            return
        slot.pop(kind, None)
        if not slot:
            self._queued.pop(key, None)
        self._bump_queue()

    @Slot(str)
    def dismiss(self, jid):
        self._jobs = [j for j in self._jobs if j["id"] != jid or j["state"] == "running"]
        if not any(j["id"] == jid for j in self._jobs):
            self._job_opts.pop(jid, None)
        self._emit_jobs()

    @Slot()
    def clearFinished(self):
        self._jobs = [j for j in self._jobs if j["state"] == "running"]
        self._emit_jobs()

    @Slot(str)
    def openPath(self, path):
        if not path or not os.path.exists(path):
            self.toast.emit(tr("The file couldn't be found — it may have been moved or deleted."), "error")
            return
        self._reveal(path)

    @Slot(str)
    def revealPath(self, path):
        if not path or not os.path.exists(path):
            self.toast.emit(tr("The file couldn't be found — it may have been moved or deleted."), "error")
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                return
        except Exception:                 # noqa: BLE001
            pass
        self._reveal(os.path.dirname(path))

    def _reveal(self, target):
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)      # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as e:            # noqa: BLE001
            self.toast.emit(tr("Could not open: {e}").format(e=e), "error")

    # ── v1.4: post-production pipelines ────────────────────────────────
    #
    # A download or a cut that has captions / a dub queued is not "done"
    # when its file lands. The owner calls take_queued() + start_pipeline(),
    # keeps its own progress UI in a "finishing" state, and gets
    # on_update(summary) / on_done(summary) on the GUI thread. The steps run
    # one after the other on one worker thread, so the owner can show one
    # honest bar (and Gemini never sees two jobs of the same file racing).
    #
    # Planning saves work and tokens:
    #   * captions + dub into the SAME language, where the captions would
    #     need an AI translation: one pass. The dub's adapted lines (what is
    #     actually heard) are burned in; no second translation, no second
    #     encode.
    #   * captions + dub otherwise: the captions job only writes its lines
    #     (cues_only) and the dub burns them in its own encode. One encode,
    #     one final video, with both.

    def take_queued(self, video_id, media, fallback_key=""):
        """Pop what the user queued for this download / clip (or None)."""
        keys = []
        if fallback_key:
            keys.append(fallback_key)
        primary = _queue_key(video_id)
        if primary:
            keys.append(primary)
        if media:
            keys.append(_queue_key("", media))
        for key in keys:
            if key and key in self._queued:
                entry = self._queued.pop(key)
                self._bump_queue()
                return entry if (entry.get("captions") or entry.get("dub")) else None
        return None

    def has_queued(self, video_id, media="", fallback_key=""):
        keys = [k for k in (fallback_key, _queue_key(video_id), _queue_key("", media) if media else "") if k]
        return any(self._queued.get(k) for k in keys)

    @staticmethod
    def _base_lang(code):
        return str(code or "").split("|")[0].replace("-orig", "").split("-")[0].lower()

    def _caption_lang(self, cap):
        """The language the captions will be shown in."""
        tgt = self._base_lang(cap.get("target"))
        if tgt:
            return tgt
        tr_key = str(cap.get("track") or "")
        if tr_key:
            return self._base_lang(tr_key.split(":", 1)[-1])
        d = self._track_cache.get((cap.get("video_id") or "").strip()) or {}
        return self._base_lang(d.get("spoken"))

    def _plan(self, entry, media, video_id, title, clip_start, clip_end):
        def fill(raw):
            raw = dict(raw or {})
            o = self._opts(raw)
            o["media"] = media
            if title:
                o["title"] = title
            # never inherit the sheet's last target (another video / clip)
            o["video_id"] = video_id or str(raw.get("video_id") or "")
            o["clip_start"] = float(clip_start or 0)
            o["clip_end"] = float(clip_end or 0)
            if o.get("source") == "file" and (o.get("clip_start") or o.get("clip_end")):
                o["window_file"] = True
            return o

        cap = fill(entry["captions"]) if entry.get("captions") else None
        dub = fill(entry["dub"]) if entry.get("dub") else None
        merged = False
        if cap and dub:
            out = cap.get("output") or "burn"
            same = self._caption_lang(cap) == self._base_lang(dub.get("target"))
            ai_words = (cap.get("source") == "ai") or self._needs_ai(cap)
            if same and ai_words:
                merged = True
                if out == "burn":
                    dub["burn_style"] = cap.get("style") or "clean"
                    dub["size"] = cap.get("size") or "m"
                    dub["position"] = cap.get("position") or "bottom"
                elif out == "soft":
                    dub["soft_after"] = True
                return [("dub", dub)], merged
            if out in ("burn", "soft"):
                cap["cues_only"] = True
                if out == "burn":
                    dub["burn_from_captions"] = True
                    dub["burn_style"] = cap.get("style") or "clean"
                    dub["size"] = cap.get("size") or "m"
                    dub["position"] = cap.get("position") or "bottom"
                else:
                    dub["soft_from_captions"] = True
            return [("captions", cap), ("dub", dub)], merged
        return [(k, o) for k, o in (("captions", cap), ("dub", dub)) if o], merged

    def _check(self, kind, opts):
        """The same provider checks a manual start makes. '' = fine."""
        if kind == "dub" and not G.has_gemini():
            return tr("AI dub needs a Gemini model: a Google Gemini key, or a provider that serves Gemini (Settings → Which AI does what → AI dub).")
        if (opts.get("source") == "ai" and kind != "dub" and not G.can_listen("captions")
                and not opts.get("video_id")):        # v1.4.10: YouTube's words will do
            return self._listen_why()
        if kind == "captions" and self._needs_ai(opts) and not T.ai_ready():
            return tr("Translating the captions needs an AI provider (Settings → AI Providers).")
        return ""

    @staticmethod
    def _listen_why():
        """Why "AI listens" can't run, in the provider's own words."""
        try:
            G.credentials(feature="captions")
            return ""
        except Exception as e:            # noqa: BLE001
            return FR.short(str(e)) if str(e) else tr(
                "“AI listens” needs a provider that can hear audio (Settings → Which AI does what).")

    @staticmethod
    def _step_weight(kind, opts):
        if kind == "dub":
            return 6.0
        w = 0.6 if (opts.get("cues_only") or opts.get("output") == "srt") else 2.0
        if opts.get("source") == "ai":
            w += 2.0
        return w

    def start_pipeline(self, owner, media, video_id, title, clip_start, clip_end,
                       entry, on_update=None, on_done=None):
        """Run the queued captions / dub for one finished file. Returns the
        pipeline id ('' when nothing could start: the caller then finishes
        as a plain download / cut)."""
        if not entry or not media or not os.path.isfile(media):
            return ""
        steps, merged = self._plan(entry, media, video_id, title, clip_start, clip_end)
        runnable, problems = [], []
        for kind, opts in steps:
            why = self._check(kind, opts)
            if why:
                problems.append(why)
            else:
                runnable.append((kind, opts))
        if not runnable:
            if problems:
                self.toast.emit(problems[0], "error")
            return ""
        if problems:
            self.toast.emit(problems[0], "warn")
        # a dub that was meant to burn the captions' lines can't when the
        # captions step was dropped
        if not any(k == "captions" for k, _o in runnable):
            for _k, o in runnable:
                if o.pop("burn_from_captions", None):
                    o["burn_style"] = ""
                o.pop("soft_from_captions", None)
        pid = "p" + uuid.uuid4().hex[:9]
        rows = []
        for i, (kind, opts) in enumerate(runnable):
            jid = uuid.uuid4().hex[:10]
            tgt = (opts.get("target") or "").split("|")[0] or self._caption_lang(opts)
            label = (tr("AI dub + captions") if (kind == "dub" and merged and opts.get("burn_style"))
                     else tr("AI dub") if kind == "dub" else tr("Captions"))
            self._jobs.insert(0, {
                "id": jid, "kind": kind, "title": opts.get("title") or os.path.basename(media),
                "lang": tgt, "langName": L.native_of(tgt) if tgt else "",
                "preview": False, "stage": tr("Starting") if i == 0 else tr("Up next"),
                "pct": 0.0, "state": "running", "message": "", "video": "", "files": [],
                "startedAt": time.time(), "eta": "", "canResume": False, "quota": False,
                "pipe": pid})
            self._cancel[jid] = threading.Event()
            self._job_opts[jid] = (kind, dict(opts))
            rows.append({"jid": jid, "kind": kind, "label": label, "state": "queued",
                         "pct": 0.0, "stage": "", "weight": self._step_weight(kind, opts),
                         "lang": tgt, "result": {}, "error": ""})
        rows[0]["state"] = "running"
        self._pipes[pid] = {"id": pid, "owner": owner, "media": media, "steps": rows,
                            "merged": merged, "on_update": on_update, "on_done": on_done,
                            "cancelled": False, "done": False}
        plan = [(r["jid"], r["kind"], self._job_opts[r["jid"]][1]) for r in rows]
        threading.Thread(target=self._pipe_worker, args=(pid, plan), daemon=True,
                         name="yth-post").start()
        self._emit_jobs()
        self._pipe_tick(pid, force=True)
        return pid

    def _pipe_worker(self, pid, plan):
        prev = {}
        for jid, kind, opts in plan:
            ev = self._cancel.get(jid)
            if ev is None or ev.is_set():
                self._finished.emit(jid, False, {}, "cancelled")
                continue
            o = dict(opts)
            burn_from = o.pop("burn_from_captions", False)
            soft_from = o.pop("soft_from_captions", False)
            soft_after = o.pop("soft_after", False)
            if kind == "dub" and burn_from and prev.get("cues_json"):
                o["burn_cues_json"] = prev["cues_json"]
                o["burn_lang"] = prev.get("lang") or ""
            elif kind == "dub" and burn_from:
                o["burn_style"] = ""      # the captions step produced nothing
            fn = J.run_dub if kind == "dub" else J.run_captions
            if kind == "dub" and (soft_from or soft_after):
                srt_src = prev.get("srt") if soft_from else None
                lang_src = prev.get("lang") if soft_from else o.get("target")

                def fn(oo, report, cancel, _srt=srt_src, _lang=lang_src, _after=soft_after):
                    res = J.run_dub(oo, report, cancel)
                    srt = res.get("srt") if _after else _srt
                    if res.get("video") and srt:
                        report("Adding the subtitle track", 0.97)
                        res["video"] = J.add_soft_track(res["video"], srt, _lang or "", report, cancel)
                    return res
            res = self._worker(jid, fn, o, ev)
            if isinstance(res, dict):
                prev = res
        self._pipeDone.emit(pid)

    def _pipe_step_done(self, pid, jid, state, res, err):
        p = self._pipes.get(pid)
        if not p:
            return
        for i, r in enumerate(p["steps"]):
            if r["jid"] == jid:
                r["state"] = state            # done | failed | cancelled
                r["pct"] = 1.0 if state == "done" else r["pct"]
                r["result"] = dict(res or {})
                r["error"] = "" if state == "done" else (err or "")
                r["info"] = None if state != "failed" else FR.explain(err or "", r["label"])
                nxt = p["steps"][i + 1] if i + 1 < len(p["steps"]) else None
                if nxt and nxt["state"] == "queued":
                    nxt["state"] = "running"
                break
        self._pipe_tick(pid, force=True)

    def _pipe_summary(self, pid):
        p = self._pipes.get(pid)
        if not p:
            return {}
        steps = p["steps"]
        tot = sum(r["weight"] for r in steps) or 1.0
        done_w = 0.0
        stage = ""
        for r in steps:
            j = self._job(r["jid"]) or {}
            if r["state"] == "running":
                r["pct"] = float(j.get("pct") or 0.0)
                r["stage"] = str(j.get("stage") or "")
                if not stage:
                    stage = r["label"] + (" · " + r["stage"] if r["stage"] else "")
            frac = 1.0 if r["state"] in ("done", "failed", "cancelled") else r["pct"]
            done_w += r["weight"] * frac
        states = [r["state"] for r in steps]
        if not p["done"]:
            state = "running"
        elif all(x == "done" for x in states):
            state = "done"
        elif any(x == "done" for x in states):
            state = "partial"
        elif p["cancelled"] or all(x == "cancelled" for x in states):
            state = "cancelled"
        else:
            state = "failed"
        video, files, notes, errors, infos = "", [], [], [], []
        for r in steps:
            res = r["result"] or {}
            if r["state"] == "done":
                if res.get("video") and os.path.isfile(res["video"]):
                    video = res["video"]          # the last step's video wins
                for f in [res.get("srt"), res.get("wav")] + list(res.get("files") or []):
                    if f and f not in files:
                        files.append(f)
                if res.get("note"):
                    notes.append(res["note"])
            elif r["state"] == "failed" and r["error"]:
                info = r.get("info") or FR.explain(r["error"], r["label"])
                infos.append({k: info[k] for k in ("kind", "title", "detail", "hint", "step")})
                errors.append(FR.short(r["error"], r["label"]))
        if not stage:
            stage = (tr("Finishing") if state == "running"
                     else tr("Ready") if state in ("done", "partial") else tr("Stopped"))
        return {
            "id": pid, "owner": p["owner"], "state": state, "merged": p["merged"],
            "pct": max(0.0, min(1.0, done_w / tot)), "stage": stage,
            "steps": [{"kind": r["kind"], "label": r["label"], "state": r["state"],
                       "pct": round(float(r["pct"]), 3), "lang": r["lang"],
                       "langName": L.native_of(r["lang"]) if r["lang"] else ""}
                      for r in steps],
            "video": video, "files": files, "note": " · ".join(notes),
            "error": " | ".join(errors),
            # v1.4.9: the first failure, as title / detail / hint
            "errorInfo": infos[0] if infos else {},
        }

    def _pipe_tick(self, pid, force=False):
        p = self._pipes.get(pid)
        if not p or p["done"]:
            return
        now = time.monotonic()
        if not force and now - p.get("_t", 0.0) < 0.15:
            return
        p["_t"] = now
        cb = p.get("on_update")
        if cb:
            try:
                cb(self._pipe_summary(pid))
            except Exception:             # noqa: BLE001 — a UI hiccup must not stop the job
                log_crash("Voice Studio pipeline update failed:\n" + traceback.format_exc())

    @Slot(str)
    def _on_pipe_done(self, pid):
        p = self._pipes.get(pid)
        if not p:
            return
        p["done"] = True
        summary = self._pipe_summary(pid)
        self._pipes.pop(pid, None)
        cb = p.get("on_done")
        if cb:
            try:
                cb(summary)
            except Exception:             # noqa: BLE001
                log_crash("Voice Studio pipeline finish failed:\n" + traceback.format_exc())

    def cancel_pipeline(self, pid):
        p = self._pipes.get(pid)
        if not p:
            return False
        p["cancelled"] = True
        for r in p["steps"]:
            ev = self._cancel.get(r["jid"])
            if ev:
                ev.set()
            j = self._job(r["jid"])
            if j and j["state"] == "running":
                j["stage"] = tr("Stopping…")
        self._emit_jobs()
        self._pipe_tick(pid, force=True)
        return True

    def pipeline_running(self, pid):
        return pid in self._pipes

    # ── v1.4: choices that arrive from the YouTube panel ───────────────
    _SPEC_STYLES = None

    def queue_from_panel(self, video_id, title, spec):
        """Validate the extension's captions / dub picks and queue them for
        this video, exactly like the in-app deck would. Returns the queued
        kinds."""
        return self._queue_spec(_queue_key(video_id), video_id, title, spec)

    def entry_from_panel(self, video_id, title, spec):
        """The same validation, returned as a pipeline entry (for a cut,
        which is not keyed by the video id: the in-app Cutter may have its
        own choices queued for the same video)."""
        out = {}
        self._queue_spec("", video_id, title, spec, into=out)
        return out

    def _queue_spec(self, key, video_id, title, spec, into=None):
        spec = dict(spec or {})
        styles = set(ST.STYLE_KEYS)
        langs = {l["code"].lower() for l in L.LANGUAGES}
        clean = {}
        cap = spec.get("captions")
        if isinstance(cap, dict):
            src = cap.get("source") if cap.get("source") in ("youtube", "ai") else "youtube"
            o = {"source": src, "title": title or "", "video_id": video_id,
                 "style": cap.get("style") if cap.get("style") in styles else
                          ("youtube" if src == "youtube" else "clean"),
                 "size": cap.get("size") if cap.get("size") in ("s", "m", "l") else "m",
                 "position": cap.get("position") if cap.get("position") in ("bottom", "middle", "top") else "bottom",
                 "output": cap.get("output") if cap.get("output") in ("burn", "soft", "srt") else "burn",
                 "bilingual": False, "translate_via": "youtube", "sub_path": ""}
            if src == "youtube":
                track = str(cap.get("track") or "")[:64]
                o["track"] = track if re.match(r"^(sub|auto|tr):[\w.-]+$", track) else ""
                o["target"] = self._base_lang(track.split(":", 1)[-1]) if o["track"] else ""
                o["allow_ai"] = False
            else:
                tgt = str(cap.get("target") or "").lower()
                o["track"] = ""
                o["target"] = tgt if tgt in langs else ""
            clean["captions"] = o
        dub = spec.get("dub")
        if isinstance(dub, dict):
            tgt = str(dub.get("target") or "").lower()
            if tgt in langs:
                models = [m["key"] for m in G.TTS_MODELS]
                clean["dub"] = {
                    "source": "youtube" if (cap or {}).get("source", "youtube") == "youtube" and video_id else "ai",
                    "track": "", "sub_path": "", "title": title or "", "video_id": video_id,
                    "target": tgt,
                    "voice_mode": "single" if dub.get("voices") == "single" else "auto",
                    "voice": dub.get("voice") if dub.get("voice") in G.VOICE_NAMES else "Kore",
                    "acting": True,
                    "bg_mode": dub.get("bg") if dub.get("bg") in ("duck", "bed", "replace") else "duck",
                    "tts_model": dub.get("model") if dub.get("model") in models else (models[0] if models else ""),
                    "keep_original": True, "burn_style": "", "size": "m", "position": "bottom"}
        if into is not None:
            into.update(clean)
            return sorted(clean)
        if key and clean:
            slot = self._queued.setdefault(key, {})
            slot.update(clean)
            self._bump_queue()
        return sorted(clean)

    @Slot(result="QVariantMap")
    def panelOptions(self):
        """What the YouTube panel needs to draw its Captions & dub section."""
        return {"styles": [{"key": s["key"], "name": tr(s["name"])} for s in ST.STYLES],
                "languages": [{"code": l["code"], "label": l["native"] if l["native"] == l["name"]
                               else f"{l['native']} · {l['name']}",
                               "native": l["native"], "name": l["name"]} for l in L.LANGUAGES],
                "voices": [n for n, _g, _c in G.VOICES],
                "models": [{"key": m["key"], "label": tr(m["name"])} for m in G.TTS_MODELS],
                "gemini": bool(G.has_gemini()), "ai": bool(T.ai_ready()),
                # v1.4.9: "AI listens" follows the Captions pick, not Gemini
                "listen": bool(G.can_listen("captions")),
                "listenLabel": G.listen_label("captions"),
                # v1.4.13: False = YouTube's captions, made fluent by the AI
                "listenAudio": bool(G.pick_hears("captions")),
                "aiLabel": T.provider_label() if T.ai_ready() else ""}

    def shutdown(self):
        for ev in list(self._cancel.values()):
            ev.set()
