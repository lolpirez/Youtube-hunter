"""app.config - Youtube Scrapper."""

import json
import os
import sys

def _get_config_path():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    cfg_dir = os.path.join(base, "YoutubeScrapperCyber")
    try:
        os.makedirs(cfg_dir, exist_ok=True)
    except Exception:
        cfg_dir = os.path.expanduser("~")
    return os.path.join(cfg_dir, "config.json")


CONFIG_PATH = _get_config_path()
# ── v1.0.7: config.json off the GUI thread ─────────────────────────────
#
# load_app_config() / save_app_config() are called from ~90 places, most of
# them Qt slots on the GUI thread: every chip on "My channel", every switch in
# Settings, the theme, the last page, ... Each save used to be a synchronous
# temp-file write + fsync + replace, which on Windows (antivirus scanning the
# new file, OneDrive / roaming profiles, a busy disk) costs anywhere from a few
# ms to 100+ ms: the little "hitch on click" all over the app.
#
# Now:
#   * the newest config lives in memory as its serialised JSON text, so a load
#     is a json.loads() of a string, never a disk read (unless another process
#     changed the file: that is detected by mtime + size and re-read);
#   * a save only serialises (that is what catches unserialisable data, exactly
#     like before) and schedules ONE background write a moment later, so a
#     burst of saves becomes a single write;
#   * flush_app_config() writes synchronously; it runs at exit (atexit) and
#     main_qml calls it on aboutToQuit, so nothing is lost on a normal close.
# Callers keep the same API and the same semantics: every load returns a
# fresh dict they may mutate, and a load after a save sees that save.

import threading as _threading

_WRITE_DELAY_S = 0.25
_lock = _threading.RLock()          # guards the state below
_write_lock = _threading.Lock()     # one writer at a time
_text = None                        # newest config as JSON text (None: not loaded)
_disk_sig = None                    # (mtime_ns, size) of the file _text matches
_dirty = False                      # _text is newer than the file
_writing = 0                        # a write is in flight
_timer = None


def _file_sig():
    try:
        st = os.stat(CONFIG_PATH)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _dumps(data):
    return json.dumps(data, ensure_ascii=False, indent=2)


def load_app_config():
    global _text, _disk_sig
    with _lock:
        if _text is not None and (_dirty or _writing or _file_sig() == _disk_sig):
            try:
                return json.loads(_text)
            except Exception:
                return {}
        sig = _file_sig()
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = f.read()
            data = json.loads(raw)
        except Exception:
            return {}
        _text = raw
        _disk_sig = sig
        return data


def save_app_config(data):
    """Remember `data` as the current config and write it shortly, off the
    calling thread. Raises nothing (same as before)."""
    global _text, _dirty, _timer
    try:
        text = _dumps(data)
    except Exception:
        return          # unserialisable: keep the old config, as before
    with _lock:
        _text = text
        _dirty = True
        if _timer is None:
            _timer = _threading.Timer(_WRITE_DELAY_S, _background_flush)
            _timer.daemon = True
            _timer.start()


def _background_flush():
    global _timer
    with _lock:
        _timer = None
    flush_app_config()


def flush_app_config():
    """Write a pending save now (synchronously). Safe from any thread."""
    global _dirty, _writing, _disk_sig
    with _write_lock:
        with _lock:
            if not _dirty or _text is None:
                return
            text = _text
            _dirty = False
            _writing += 1
        ok = False
        try:
            ok = _write_text(text)
        finally:
            with _lock:
                _writing -= 1
                if ok and not _dirty and _text == text:
                    _disk_sig = _file_sig()
                elif not ok and _text == text:
                    _dirty = True       # keep it pending; retried on the next save / exit


def _write_text(text):
    """Write the config atomically: serialise into a unique temp file next to
    it, then os.replace() it over the real one. Opening config.json with "w"
    truncated it first, so a crash / kill / full disk mid-write left a broken
    file that load_app_config() then read as {} (every setting "lost")."""
    import tempfile
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=".config-", suffix=".tmp", dir=os.path.dirname(CONFIG_PATH))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(tmp_path, CONFIG_PATH)
            tmp_path = None
        except PermissionError:
            # Windows: an AV scanner / another reader can briefly lock the
            # target; fall back to the old direct write rather than lose it
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(text)
        return True
    except Exception:
        return False
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


import atexit as _atexit
_atexit.register(flush_app_config)
