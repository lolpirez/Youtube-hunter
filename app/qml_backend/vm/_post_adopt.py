"""app.qml_backend.vm._post_adopt - YouTube Hunter (QML).

v1.4.7: captions / a dub started from the Voice Studio sheet on a finished
download or clip now REPLACE that row's file with the new version, the same
way a queued pipeline already did. Before, the result only lived in the
sheet's job list: you had to open the globe again to find the captioned
video, while the list kept playing the plain one.

    prepare(kind, opts)  before the job starts: picks the file to work on.
                         Adding the step the row's file doesn't have yet
                         stacks on it (captions + dub = both in one file);
                         redoing a step it already has starts from the plain
                         original, so nothing is drawn or voiced twice.
    adopt(kind, opts, res)  after it finished: moves the row onto the new
                         video, returns "download" / "clip" / "".

The plain file stays where it is on disk (and in the folder); only the list
moves on.
"""

import os
import traceback

from app.core import post_fx as FX
from app.crash import log_crash


def _owner(media, video_id):
    """("download", row) / ("clip", row) / ("", None) for a file."""
    try:
        from app.core.db.clips import clips_find_by_path
        from app.core.db.downloads import downloads_find_by_path
        row = downloads_find_by_path(media, (video_id or "").strip())
        if row is None and video_id:
            row = downloads_find_by_path(media)
        if row is not None:
            return "download", row
        row = clips_find_by_path(media)
        if row is not None:
            return "clip", row
    except Exception:                     # noqa: BLE001 — never block a job
        log_crash("post adopt lookup failed:\n" + traceback.format_exc())
    return "", None


def _new_kinds(kind, opts):
    kinds = {kind}
    if kind == "dub" and (opts.get("burn_style") or opts.get("soft_after")):
        kinds.add("captions")
    return kinds


def prepare(kind, opts):
    """Choose the file the job runs on. Returns the (possibly changed) opts;
    `_adopt_base` remembers it for adopt()."""
    o = dict(opts or {})
    media = o.get("media") or ""
    if not media or float(o.get("preview_sec") or 0) > 0:
        return o
    who, row = _owner(media, o.get("video_id"))
    if not who:
        return o
    current = row.get("filepath") or ""
    original = row.get("original_path") or ""
    layers = FX.load(row.get("post_fx")) or FX.from_name(current)
    base = current if (current and os.path.isfile(current)) else media
    if layers and (FX.kinds_of(layers) & _new_kinds(kind, o)):
        # the file already has this step: start over from the plain one
        if original and os.path.isfile(original):
            base = original
    o["media"] = base
    o["_adopt_base"] = base
    if kind == "dub" and original and os.path.isfile(original) and not FX.same_path(base, original):
        # a dub on top of captions hears the same audio as the original:
        # reuse its transcript / script / recorded lines (no second bill)
        o["cache_media"] = original
    return o


def adopt(kind, opts, res):
    """Move the owning row onto the job's new video. "" when nothing moved."""
    o, r = dict(opts or {}), dict(res or {})
    layers = FX.job_layers(kind, o, r)
    if not layers:
        return ""
    base = o.get("_adopt_base") or o.get("media") or ""
    who, row = _owner(base, o.get("video_id"))
    if not who:
        return ""
    final = r.get("video") or ""
    try:
        if who == "download":
            from app.core.db.downloads import downloads_adopt
            return "download" if downloads_adopt(row.get("video_id") or "", final, layers, base) else ""
        from app.core.db.clips import clips_adopt
        return "clip" if clips_adopt(row.get("id"), final, layers, base) else ""
    except Exception:                     # noqa: BLE001
        log_crash("post adopt failed:\n" + traceback.format_exc())
        return ""
