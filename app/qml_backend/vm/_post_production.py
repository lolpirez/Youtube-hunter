"""app.qml_backend.vm._post_production - YouTube Hunter (QML).

v1.4: a download with captions / an AI dub queued is NOT finished when its
file lands. Every view-model that can download (Downloads, Niche Finder,
Search Archive) hands the finished file to begin(); from then on:

  * the DB row stays "merging" (running, not playable, no pause);
  * the shared live entry keeps running with post=True, a real percent for
    the post steps, a stage line ("Captions · Drawing the captions") and the
    step list, so the Downloads row, the title-bar capsule, the desktop live
    card and the YouTube panel all show the SAME second progress bar;
  * when the last step ends the row turns "complete" pointing at the
    finished video (captions burned / dubbed), and ONE notice goes out.

Stop on a finishing row cancels the steps; the plain video stays saved.
"""

import os
import traceback

from app.core import post_fx as FX
from app.core.db.downloads import downloads_adopt, downloads_get, downloads_upsert, file_size_of
from app.crash import log_crash
from app.i18n import tr
from app.qml_backend.vm import _dl_progress as dlp

_PIPES = {}                    # video id -> pipeline id
_LAST_ERR = {}                 # v1.4.3: video id -> why captions / dub didn't finish
_LAST_INFO = {}                # v1.4.9: video id -> {title, detail, hint, step, kind}


def last_error(video_id):
    """Why the last captions / dub run on this download didn't finish
    ("" = it did, or none ran). The YouTube panel shows it on its Saved
    screen, so a failed dub is no longer a silent "Saved"."""
    return _LAST_ERR.get(video_id, "")


def last_error_info(video_id):
    """v1.4.9: the same reason as title / detail / hint ({} = none)."""
    return dict(_LAST_INFO.get(video_id) or {})


def _studio():
    try:
        from app.qml_backend.vm.voice_studio_vm import VoiceStudioVM
        return VoiceStudioVM.instance()
    except Exception:          # noqa: BLE001
        return None


def running(video_id):
    return video_id in _PIPES


def has_queued(video_id, filepath=""):
    st = _studio()
    try:
        return bool(st and st.has_queued(video_id, filepath))
    except Exception:          # noqa: BLE001
        return False


def cancel(video_id):
    pid = _PIPES.get(video_id)
    st = _studio()
    if not pid or st is None:
        return False
    dlp.publish(video_id, text=tr("Stopping…"), structural=True)
    return st.cancel_pipeline(pid)


def _step_chips(summary, first_label):
    steps = [{"kind": "download", "label": first_label, "state": "done", "pct": 1.0}]
    steps += list((summary or {}).get("steps") or [])
    return steps


def begin(video_id, filepath, title="", on_update=None, on_finish=None):
    """True when captions / a dub were queued for this download and are now
    running; the caller must then NOT report the download as finished."""
    st = _studio()
    if st is None or not filepath or not os.path.isfile(filepath):
        return False
    try:
        entry = st.take_queued(video_id, filepath)
    except Exception:          # noqa: BLE001
        log_crash("post-production take failed:\n" + traceback.format_exc())
        return False
    if not entry:
        return False
    _LAST_ERR.pop(video_id, None)
    _LAST_INFO.pop(video_id, None)
    if not title:
        title = (downloads_get(video_id) or {}).get("title") or ""
    downloads_upsert(video_id, status="merging", progress=100, filepath=filepath,
                     file_size=file_size_of(filepath), error_message="",
                     create_if_missing=False)

    def upd(summary):
        pct = int(round(float(summary.get("pct") or 0) * 100))
        dlp.publish(video_id, running=True, post=True, cancelling=False,
                    pct=min(99, pct), text=summary.get("stage") or tr("Finishing"),
                    steps=_step_chips(summary, tr("Download")),
                    speed=0.0, eta=0, done=0, total=0, fragDone=0, fragTotal=0)
        if on_update:
            on_update(summary)

    def done(summary):
        _PIPES.pop(video_id, None)
        # v1.4.3: recorded BEFORE the row turns complete, so the panel's very
        # first "done" poll already carries the reason
        if summary.get("state") in ("failed", "error", "partial"):
            _LAST_ERR[video_id] = summary.get("error") or tr("unknown error")
            if summary.get("errorInfo"):
                _LAST_INFO[video_id] = dict(summary["errorInfo"])
            # v1.4.4: leave a trace in the crash log, so "it said rendering
            # and then nothing" can be diagnosed from the log file
            log_crash(f"post-production {summary.get('state')} for {video_id} "
                      f"({os.path.basename(filepath)}): {summary.get('error') or '?'}")
        final = summary.get("video") or ""
        if not final or not os.path.isfile(final):
            final = filepath
        state = summary.get("state")
        downloads_upsert(video_id, status="complete", progress=100, filepath=final,
                         file_size=file_size_of(final), error_message="",
                         create_if_missing=False)
        # v1.4.7: remember the plain download + what is in the new file, so
        # the row can say "captions EN · dub FA" on its thumbnail
        layers = FX.pipeline_layers(entry, summary, filepath, final)
        if layers:
            downloads_adopt(video_id, final, layers, filepath)
        dlp.clear(video_id)
        msg = "" if state == "done" else (summary.get("error") or "")
        try:
            from app.qml_backend.desktop_notifier import announce_download
            announce_download(video_id, True, msg, final)
        except Exception:      # noqa: BLE001
            pass
        if on_finish:
            on_finish(state, final, summary)

    # the row flips to "finishing" right away, before the first tick
    dlp.publish(video_id, structural=True, running=True, post=True, cancelling=False,
                pct=0, text=tr("Starting captions…"),
                steps=[{"kind": "download", "label": tr("Download"), "state": "done", "pct": 1.0}],
                speed=0.0, eta=0, done=0, total=0, fragDone=0, fragTotal=0)
    pid = st.start_pipeline("dl:" + video_id, filepath, video_id, title, 0, 0, entry,
                            on_update=upd, on_done=done)
    if not pid:
        dlp.clear(video_id)
        downloads_upsert(video_id, status="complete", progress=100, filepath=filepath,
                         file_size=file_size_of(filepath), create_if_missing=False)
        return False
    _PIPES[video_id] = pid
    return True


def finish_text(state, summary):
    """The one toast line for the end of a download / cut with post steps."""
    if state == "done":
        return tr("Ready, with captions — {f}"), "success"
    if state == "partial":
        return tr("Ready, but a step failed: {e}").replace("{e}", (summary or {}).get("error") or ""), "warn"
    if state == "cancelled":
        return tr("Captions stopped. The plain video is saved — {f}"), "info"
    return tr("Captions failed: {e}. The plain video is saved.").replace(
        "{e}", (summary or {}).get("error") or tr("unknown error")), "error"
