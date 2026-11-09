"""app.core.db.clips - YouTube Hunter.

Persisted cut-clips history behind the Cutter page.

One row per successful cut (auto-increment id, so cutting the same segment
twice keeps both files instead of overwriting). Survives page switches and
app restarts — the Cutter page rebuilds its "Clips" section from here.
"""

import os
import sqlite3

from app.core.db._sqlite import connect as db_connect
import traceback
from datetime import datetime, timezone

from app.core.db.ideas import DATA_DB_PATH
from app.crash import log_crash


def init_clips_db():
    try:
        conn = db_connect(DATA_DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clips (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT,
                source_label  TEXT,
                video_id      TEXT,
                start_sec     REAL NOT NULL DEFAULT 0,
                end_sec       REAL NOT NULL DEFAULT 0,
                quality_label TEXT,
                filepath      TEXT,
                file_size     INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL
            )
        """)
        _ensure_channel_columns(cur)
        conn.commit()
        conn.close()
    except Exception:
        log_crash("init_clips_db failed:\n" + traceback.format_exc())


# v3.16-p4: the source video's channel identity, so the in-app player can show
# the real channel logo + name over a clip instead of a "C" (for "Clip")
# initial. Patched onto existing databases — old rows simply start empty and
# are backfilled lazily by the CutterVM.
_CHANNEL_COLUMNS = {
    "channel_title":     "TEXT",
    "channel_id":        "TEXT",
    "channel_url":       "TEXT",
    "channel_thumb_url": "TEXT",
    "subscriber_count":  "INTEGER",
    # v1.4.7: the plain cut + what captions / dub did to it (see post_fx.py)
    "original_path":     "TEXT",
    "post_fx":           "TEXT",
}


def _ensure_channel_columns(cur):
    cur.execute("PRAGMA table_info(clips)")
    cols = {r[1] for r in cur.fetchall()}
    for col, decl in _CHANNEL_COLUMNS.items():
        if col not in cols:
            cur.execute(f"ALTER TABLE clips ADD COLUMN {col} {decl}")


def _subs_or_none(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def clips_add(title, source_label="", video_id="", start_sec=0.0, end_sec=0.0,
              quality_label="", filepath="", file_size=0, channel=None,
              original_path="", post_fx=""):
    """Insert one finished clip. Returns the new row id, or 0 on failure.

    `channel` is the source video's identity (channel_title, channel_id,
    channel_url, channel_thumb_url, subscriber_count) — optional."""
    ch = dict(channel or {})
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn = db_connect(DATA_DB_PATH)
        cur = conn.cursor()
        _ensure_channel_columns(cur)
        cur.execute("""
            INSERT INTO clips
                (title, source_label, video_id, start_sec, end_sec,
                 quality_label, filepath, file_size, created_at,
                 channel_title, channel_id, channel_url, channel_thumb_url,
                 subscriber_count, original_path, post_fx)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (title or "", source_label or "", video_id or "",
              float(start_sec or 0), float(end_sec or 0),
              quality_label or "", filepath or "", int(file_size or 0), now,
              ch.get("channel_title") or "", ch.get("channel_id") or "",
              ch.get("channel_url") or "", ch.get("channel_thumb_url") or "",
              _subs_or_none(ch.get("subscriber_count")),
              original_path or "", post_fx or ""))
        row_id = cur.lastrowid or 0
        conn.commit()
        conn.close()
        return int(row_id)
    except Exception:
        log_crash("clips_add failed:\n" + traceback.format_exc())
        return 0


def clips_list():
    """Newest first — the latest cut tops the Cutter page's Clips section."""
    try:
        conn = db_connect(DATA_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM clips ORDER BY id DESC")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        log_crash("clips_list failed:\n" + traceback.format_exc())
        return []


def clips_update_channel(video_id, channel):
    """Fill in the channel identity on every clip cut from `video_id`.

    Only non-empty values are written, and only over empty cells — a later,
    thinner read (e.g. no subscriber count) never wipes a richer one."""
    vid = (video_id or "").strip()
    ch = dict(channel or {})
    if not vid or not ch:
        return False
    try:
        conn = db_connect(DATA_DB_PATH)
        cur = conn.cursor()
        _ensure_channel_columns(cur)
        for col in ("channel_title", "channel_id", "channel_url",
                    "channel_thumb_url"):
            val = (ch.get(col) or "").strip()
            if val:
                cur.execute(
                    f"UPDATE clips SET {col} = ? WHERE video_id = ? "
                    f"AND ({col} IS NULL OR {col} = '')", (val, vid))
        subs = _subs_or_none(ch.get("subscriber_count"))
        if subs is not None:
            cur.execute("UPDATE clips SET subscriber_count = ? "
                        "WHERE video_id = ? AND subscriber_count IS NULL",
                        (subs, vid))
        conn.commit()
        conn.close()
        return True
    except Exception:
        log_crash("clips_update_channel failed:\n" + traceback.format_exc())
        return False


def clips_delete(clip_id):
    try:
        conn = db_connect(DATA_DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM clips WHERE id = ?", (int(clip_id),))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def clips_clear():
    """Remove every clip row (files on disk are kept). Returns True."""
    try:
        conn = db_connect(DATA_DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM clips")
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def clips_find_by_path(path):
    """v1.4.7: the clip a file belongs to (current file or original)."""
    from app.core import post_fx as FX
    if not path:
        return None
    for row in clips_list():
        if FX.same_path(row.get("filepath"), path) or FX.same_path(row.get("original_path"), path):
            return row
    return None


def clips_adopt(clip_id, final, layers, base_path):
    """v1.4.7: point a clip at its captioned / dubbed version (the plain cut
    stays on disk as original_path). Same stacking rules as downloads."""
    from app.core import post_fx as FX
    if not clip_id or not final or not os.path.isfile(final):
        return False
    try:
        conn = db_connect(DATA_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        _ensure_channel_columns(cur)
        cur.execute("SELECT * FROM clips WHERE id = ?", (int(clip_id),))
        row = cur.fetchone()
        if row is None:
            conn.close()
            return False
        row = dict(row)
        current = row.get("filepath") or ""
        original = row.get("original_path") or ""
        if not original or not os.path.isfile(original):
            original = current
        on_current = FX.same_path(base_path, current) and not FX.same_path(current, original)
        new_layers = FX.stack(row.get("post_fx") or FX.from_name(current), layers, on_current)
        try:
            size = os.path.getsize(final)
        except OSError:
            size = 0
        cur.execute("UPDATE clips SET filepath=?, file_size=?, original_path=?, post_fx=? WHERE id=?",
                    (final, int(size), original or "", FX.dump(new_layers), int(clip_id)))
        conn.commit()
        conn.close()
        FX.remember(final, new_layers)
        return True
    except Exception:
        log_crash("clips_adopt failed:\n" + traceback.format_exc())
        return False
