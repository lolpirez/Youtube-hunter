"""app.qml_backend.vm.dossier_vm - YouTube Hunter (QML).

`VideoDossier` in QML: the data behind the Video Dossier card
(components/VideoDossierCard.qml) that every video card can open.

    VideoDossier.open({ videoId, title, thumbId, outlierText, ... })

The card opens AT ONCE with what the calling card already knows (title,
thumbnail, its outlier label), then fills in as the three parts land, each on
its own worker thread so a slow one never holds the others back:

    meta        YouTube Data API (4 units per dossier), else yt-dlp
    comments    top 30 + newest 30 (fewer when the video has fewer)
    transcript  the whole caption track, never trimmed

`exportXlsx()` writes everything into one Excel workbook (v2.4: Index, a
vertical "Videos (Vertical)" sheet with the fields top to bottom, and the
one-row table) next to the scraper's exports, in a "Video dossiers" folder.
"""

import os
import threading

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtGui import QGuiApplication

from app.config import load_app_config
from app.core import dossier as D
from app.core import transcript_timed as TT
from app.crash import log_crash
from app.i18n import tr

_TX_PREVIEW = 6000          # characters the card renders; copy + Excel hold all of it


def _b64(url):
    import base64
    return base64.b64encode(str(url or "").encode("utf-8")).decode("ascii") if url else ""


def _api_keys():
    from app.core.quota import clean_api_keys, key_quota_remaining
    cfg = load_app_config()
    keys = clean_api_keys([cfg.get("api_key", "")] + list(cfg.get("youtube_api_keys") or []))
    try:
        keys.sort(key=lambda k: -key_quota_remaining(k))
    except Exception:                        # noqa: BLE001 — order is a nicety
        pass
    return keys


def _err_text(exc):
    t = str(exc or "").strip()
    if "quota" in t.lower():
        return tr("The YouTube API quota for today is used up.")
    if len(t) > 160:
        t = t[:157] + "…"
    return t or tr("Something went wrong.")


class DossierVM(QObject):
    changed = Signal()
    openRequested = Signal()
    toast = Signal(str, str)

    _part = Signal(int, str, object)         # worker -> GUI (run, part, payload)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._run = 0
        self._seed = {}
        self._meta = {}
        self._top = []
        self._latest = []
        self._tx = {}
        self._tx_full = ""
        self._notes = {}
        self._busy = set()
        self._export_state = ""              # "" | "saving" | "saved" | "error"
        self._export_path = ""
        # v1.4: the time-stamped view of the same captions (read on demand,
        # the first time the card's "With timestamps" switch is used)
        self._timed = []
        self._timed_state = ""               # "" | "loading" | "ready" | "error"
        self._timed_error = ""
        self._timed_lang = ""
        self._timed_saved = ""               # last .srt / .txt written
        self._part.connect(self._on_part)

    # ── QML surface ───────────────────────────────────────────────────
    @Property("QVariantMap", notify=changed)
    def seed(self):
        return dict(self._seed)

    @Property("QVariantMap", notify=changed)
    def meta(self):
        return dict(self._meta)

    @Property("QVariantList", notify=changed)
    def topComments(self):
        return list(self._top)

    @Property("QVariantList", notify=changed)
    def latestComments(self):
        return list(self._latest)

    @Property("QVariantMap", notify=changed)
    def transcript(self):
        return dict(self._tx)

    @Property("QVariantList", notify=changed)
    def timedCues(self):
        # [{s, e, t, ts}] - ts is the "mm:ss" label the card prints
        hours = any(r["s"] >= 3600 for r in self._timed)
        return [dict(r, ts=TT.stamp(r["s"], hours)) for r in self._timed]

    @Property(str, notify=changed)
    def timedState(self):
        return self._timed_state

    @Property(str, notify=changed)
    def timedError(self):
        return self._timed_error

    @Property(str, notify=changed)
    def timedLang(self):
        return self._timed_lang

    @Property(str, notify=changed)
    def timedSaved(self):
        return self._timed_saved

    @Property("QVariantMap", notify=changed)
    def notes(self):
        return dict(self._notes)

    @Property(bool, notify=changed)
    def metaLoading(self):
        return "meta" in self._busy

    @Property(bool, notify=changed)
    def commentsLoading(self):
        return "comments" in self._busy

    @Property(bool, notify=changed)
    def transcriptLoading(self):
        return "transcript" in self._busy

    @Property(bool, notify=changed)
    def ready(self):
        return not self._busy and bool(self._meta)

    @Property(int, notify=changed)
    def partsDone(self):
        return 3 - len(self._busy)

    @Property(str, notify=changed)
    def exportState(self):
        return self._export_state

    @Property(str, notify=changed)
    def exportPath(self):
        return self._export_path

    @Property(int, constant=True)
    def commentLimit(self):
        return D.COMMENT_COUNT

    # ── open ──────────────────────────────────────────────────────────
    @Slot("QVariantMap")
    def open(self, seed):
        seed = dict(seed or {})
        vid = str(seed.get("videoId") or seed.get("vid") or "").strip()
        if not vid:
            self.toast.emit(tr("This card has no video to open."), "error")
            return
        self._run += 1
        run = self._run
        thumb = seed.get("thumbId") or seed.get("thumb") or _b64(f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
        self._seed = {
            "videoId": vid,
            "title": str(seed.get("title") or ""),
            "thumbId": thumb,
            "channelTitle": str(seed.get("channelTitle") or ""),
            "outlierText": str(seed.get("outlierText") or ""),
            "outlierLabel": str(seed.get("outlierLabel") or ""),
            "outlierKey": str(seed.get("outlierKey") or ""),
        }
        self._meta, self._top, self._latest, self._tx, self._tx_full = {}, [], [], {}, ""
        self._notes = {}
        self._busy = {"meta", "comments", "transcript"}
        self._export_state, self._export_path = "", ""
        self._timed, self._timed_state, self._timed_error = [], "", ""
        self._timed_lang, self._timed_saved = "", ""
        self.changed.emit()
        self.openRequested.emit()
        threading.Thread(target=self._work, args=(run, vid), daemon=True,
                         name="dossier-" + vid).start()

    @Slot()
    def reload(self):
        if self._seed.get("videoId"):
            self.open(dict(self._seed))

    # ── workers ───────────────────────────────────────────────────────
    def _alive(self, run):
        return run == self._run

    def _work(self, run, vid):
        yt, meta, note = None, None, ""
        keys = _api_keys()
        if keys:
            from app.core.youtube_api import QuotaExceededError, build_youtube_service
            for k in keys:
                try:
                    svc = build_youtube_service(k, timeout=25)
                    meta = D.meta_from_api(svc, vid, stop_check=lambda: not self._alive(run))
                    yt = svc
                    break
                except QuotaExceededError:
                    note = tr("API quota used up, read through yt-dlp instead.")
                    continue
                except LookupError as exc:
                    self._part.emit(run, "meta", {"error": str(exc)})
                    self._part.emit(run, "comments", {"error": ""})
                    self._part.emit(run, "transcript", {"error": ""})
                    return
                except Exception as exc:     # noqa: BLE001 — try yt-dlp
                    note = tr("YouTube API failed ({e}), read through yt-dlp instead.").format(e=_err_text(exc))
                    break
        else:
            note = tr("No YouTube API key: read through yt-dlp (slower). Add a key in Settings → API.")
        if meta is None and self._alive(run):
            try:
                meta = D.meta_from_ytdlp(vid)
            except Exception as exc:         # noqa: BLE001
                log_crash("dossier meta (yt-dlp): " + str(exc))
                self._part.emit(run, "meta", {"error": tr("Could not read this video: {e}").format(e=_err_text(exc))})
                meta = None
        if not self._alive(run):
            return
        if meta is not None:
            meta["sourceNote"] = note
            self._part.emit(run, "meta", meta)
        secs = int((meta or {}).get("durationS") or 0)
        threading.Thread(target=self._work_comments, args=(run, vid, yt), daemon=True).start()
        threading.Thread(target=self._work_transcript, args=(run, vid, secs), daemon=True).start()

    def _work_comments(self, run, vid, yt):
        top, latest, note = [], [], ""
        try:
            done = False
            if yt is not None:
                try:
                    top = D.comments_from_api(yt, vid, "relevance", stop_check=lambda: not self._alive(run))
                    latest = D.comments_from_api(yt, vid, "time", stop_check=lambda: not self._alive(run))
                    done = True
                except D.CommentsOff:
                    raise
                except Exception as exc:     # noqa: BLE001 — fall back to yt-dlp
                    note = tr("API comments failed ({e}), read through yt-dlp.").format(e=_err_text(exc))
            if not done and self._alive(run):
                top = D.comments_from_ytdlp(vid, "top")
                if self._alive(run):
                    latest = D.comments_from_ytdlp(vid, "new")
            self._part.emit(run, "comments", {"top": top, "latest": latest, "note": note})
        except D.CommentsOff:
            self._part.emit(run, "comments", {"top": [], "latest": [], "off": True,
                                              "note": tr("Comments are turned off for this video.")})
        except Exception as exc:             # noqa: BLE001
            log_crash("dossier comments: " + str(exc))
            self._part.emit(run, "comments", {"top": top, "latest": latest,
                                              "error": tr("Could not read the comments: {e}").format(e=_err_text(exc))})

    def _work_transcript(self, run, vid, secs):
        try:
            from app.core.transcripts import fetch_transcript
            # thorough: a track that looks too short for the video is
            # re-fetched and the longest kept. The dossier wants ALL of it.
            status, text, kind = fetch_transcript(vid, thorough=True, expect_secs=secs)
            ok = str(status).startswith("Available") and text and text != "N/A"
            self._part.emit(run, "transcript", {
                "status": status if ok else "Not Available",
                "text": text if ok else "",
                "kind": kind or "",
            })
        except Exception as exc:             # noqa: BLE001
            log_crash("dossier transcript: " + str(exc))
            self._part.emit(run, "transcript", {"status": "Not Available", "text": "",
                                                "error": _err_text(exc)})

    # ── v1.4: time-stamped transcript ────────────────────────────────
    @Slot()
    def loadTimed(self):
        """Read the captions again WITH their timing (once per opened video)."""
        vid = self._seed.get("videoId")
        if not vid or self._timed_state in ("loading", "ready"):
            return
        self._timed_state, self._timed_error = "loading", ""
        self.changed.emit()
        run = self._run
        threading.Thread(target=self._work_timed, args=(run, vid), daemon=True,
                         name="dossier-timed-" + vid).start()

    def _work_timed(self, run, vid):
        try:
            res = TT.load(vid)
        except Exception as exc:             # noqa: BLE001 — TT.load never raises, belt and braces
            log_crash("dossier timed transcript: " + str(exc))
            res = {"ok": False, "error": _err_text(exc)}
        self._part.emit(run, "timed", res)

    @Slot(str)
    def copyTimed(self, mode):
        """mode: "timed" ([mm:ss] per line) or "plain"."""
        if not self._timed:
            return
        text = TT.timed_text(self._timed) if mode == "timed" else TT.plain_text(self._timed)
        self._clip(text, tr("Copied {n} lines with timestamps.").format(n=f"{len(self._timed):,}")
                   if mode == "timed" else tr("Transcript copied."))

    def _timed_file(self, ext):
        folder = self._export_dir()
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, D.dossier_filename(self._meta or self._seed, ext).replace(
            " dossier." + ext, " subtitles." + ext))

    @Slot(str)
    def saveTimedFile(self, kind):
        """kind: "srt" (subtitle file) or "txt" (the [mm:ss] lines)."""
        if not self._timed:
            self.toast.emit(tr("The timed transcript is not loaded yet."), "info")
            return
        try:
            path = self._timed_file("srt" if kind == "srt" else "txt")
            body = TT.srt_text(self._timed) if kind == "srt" else TT.timed_text(self._timed)
            with open(path, "w", encoding="utf-8-sig", newline="\n") as fh:
                fh.write(body + "\n")
            self._timed_saved = path
            self.toast.emit(tr("Saved → {p}").format(p=path), "success")
        except Exception as exc:             # noqa: BLE001
            log_crash("dossier timed save: " + str(exc))
            self.toast.emit(tr("Could not save the file: {e}").format(e=_err_text(exc)), "error")
        self.changed.emit()

    @Slot()
    def revealTimedFile(self):
        from app.core.utils import reveal_in_file_manager
        if self._timed_saved:
            reveal_in_file_manager(self._timed_saved)

    @Slot(float)
    def openAt(self, seconds):
        """Open the video on YouTube at that second."""
        vid = self._seed.get("videoId")
        if vid:
            from app.core.utils import open_external_url
            open_external_url(f"https://www.youtube.com/watch?v={vid}&t={int(max(0, seconds))}s")

    @Slot(int, str, object)
    def _on_part(self, run, part, payload):
        if run != self._run:
            return
        payload = payload or {}
        if part == "timed":
            if payload.get("ok"):
                self._timed = list(payload.get("cues") or [])
                self._timed_lang = str(payload.get("lang") or "")
                self._timed_state, self._timed_error = "ready", ""
            else:
                self._timed, self._timed_state = [], "error"
                self._timed_error = str(payload.get("error") or tr("No captions found for this video."))
            self.changed.emit()
            return
        self._busy.discard(part)
        if part == "meta":
            if payload.get("error"):
                self._notes["meta"] = payload["error"]
            else:
                m = dict(payload)
                hero = (m.get("thumbnails") or [{}])[0].get("url") or ""
                m["heroThumbId"] = _b64(hero) if hero else self._seed.get("thumbId", "")
                m["channelThumbId"] = _b64(m.get("channelThumbUrl")) if m.get("channelThumbUrl") else ""
                self._meta = m
                if m.get("sourceNote"):
                    self._notes["source"] = m["sourceNote"]
        elif part == "comments":
            self._top = list(payload.get("top") or [])
            self._latest = list(payload.get("latest") or [])
            for c in self._top + self._latest:
                c["thumbId"] = ("circle/" + _b64(c.get("authorThumbUrl"))) if c.get("authorThumbUrl") else ""
            if payload.get("off"):
                self._notes["commentsOff"] = payload.get("note") or ""
            elif payload.get("error"):
                self._notes["comments"] = payload["error"]
            elif payload.get("note"):
                self._notes["commentsSource"] = payload["note"]
        elif part == "transcript":
            text = payload.get("text") or ""
            self._tx_full = text
            self._tx = {
                "status": payload.get("status") or "Not Available",
                "available": bool(text),
                "chars": len(text),
                "words": D.word_count(text),
                "preview": text[:_TX_PREVIEW],
                "truncatedPreview": len(text) > _TX_PREVIEW,
                "kind": payload.get("kind") or "",
            }
            if payload.get("error"):
                self._notes["transcript"] = payload["error"]
        self.changed.emit()

    # ── actions ───────────────────────────────────────────────────────
    def _clip(self, text, ok_msg):
        clip = QGuiApplication.clipboard()
        if clip is None or not text:
            return
        clip.setText(text)
        self.toast.emit(ok_msg, "success")

    @Slot(str)
    def copyText(self, text):
        self._clip(text, tr("Copied."))

    @Slot()
    def copyTranscript(self):
        self._clip(self._tx_full, tr("Full transcript copied ({n} words).").format(
            n=f"{D.word_count(self._tx_full):,}"))

    @Slot(str)
    def openUrl(self, url):
        from app.core.utils import open_external_url
        open_external_url(url)

    def _export_dir(self):
        cfg = load_app_config()
        base = (cfg.get("save_folder") or "").strip()
        if not (base and os.path.isdir(base)):
            from app.core.utils import desktop_download_dir
            base = desktop_download_dir("YouTube Hunter")
        return os.path.join(base, "Video dossiers")

    @Slot()
    def exportXlsx(self):
        if not self._meta:
            self.toast.emit(tr("The video details are still loading."), "info")
            return
        self._export_state = "saving"
        self.changed.emit()
        try:
            folder = self._export_dir()
            path = os.path.join(folder, D.dossier_filename(self._meta, "xlsx"))
            D.write_single_dossier_xlsx(
                path, self._meta, self._top, self._latest,
                {"status": self._tx.get("status"), "text": self._tx_full,
                 "words": self._tx.get("words"), "chars": self._tx.get("chars")},
                self._seed, comments_off="commentsOff" in self._notes,
                errors=[v for k, v in self._notes.items()
                        if k in ("meta", "comments", "transcript") and v])
            self._export_state, self._export_path = "saved", path
            self.toast.emit(tr("Dossier saved → {p}").format(p=path), "success")
        except Exception as exc:             # noqa: BLE001
            log_crash("dossier export: " + str(exc))
            self._export_state = "error"
            self.toast.emit(tr("Could not save the Excel file: {e}").format(e=_err_text(exc)), "error")
        self.changed.emit()

    @Slot()
    def revealExport(self):
        from app.core.utils import reveal_in_file_manager
        if self._export_path:
            reveal_in_file_manager(self._export_path)

    @Slot()
    def close(self):
        # stops late parts from landing in a card nobody is looking at
        self._run += 1
        self._busy = set()
        if self._timed_state == "loading":
            self._timed_state = ""
        self.changed.emit()
