"""app.core.fs_safe - YouTube Hunter.

v1.9: file moves that survive Windows.

On Windows `os.replace(tmp, dst)` fails with "[WinError 5] Access is denied"
(or 32, "being used by another process") whenever ANY handle is open on
either file without FILE_SHARE_DELETE: Windows Defender scanning the file
that was just written, the Search indexer, a thumbnail preview in Explorer,
a media player that has the video open, or another worker thread of the
same dub reading a clip that a second worker is replacing. Python's own
open() never asks for FILE_SHARE_DELETE, so even our own readers count.

Those locks last a few milliseconds to a second. A dub writes hundreds of
small files, so one of them losing that race now and then killed the whole
job ("AI dub + captions · Something went wrong").

replace() retries with a short back-off (about 3.5 s in total). Most of
what the dub writes is a content-keyed cache (same name = same bytes), so
when another worker already produced `dst` the new copy is simply dropped
instead of fighting for the name. remove() retries the same way and never
raises.
"""

import errno
import os
import time

_WIN_TRANSIENT = (5, 32, 33)          # access denied / sharing / lock violation
_ERRNO_TRANSIENT = (errno.EACCES, errno.EPERM, errno.EBUSY)


def is_lock_error(e):
    """True for the "someone has this file open" family of errors."""
    if not isinstance(e, OSError):
        return False
    if getattr(e, "winerror", None) in _WIN_TRANSIENT:
        return True
    return isinstance(e, PermissionError) or e.errno in _ERRNO_TRANSIENT


def remove(path, attempts=6):
    """Delete `path` if it exists. Retries a locked file; never raises.
    Returns True when the file is gone."""
    wait = 0.05
    for n in range(attempts):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as e:
            if not is_lock_error(e) or n == attempts - 1:
                return False
        time.sleep(wait)
        wait = min(0.5, wait * 1.8)
    return False


def replace(src, dst, keep_existing=True, attempts=10):
    """os.replace(src, dst) that waits out a short lock.

    keep_existing: `dst` is a content-keyed cache file, so if it already
    exists (another worker made the same file) and the rename is still
    blocked, keep it and drop `src`. Use False for a file whose new
    content matters (a finished video, a settings file)."""
    wait = 0.05
    last = None
    for n in range(attempts):
        try:
            os.replace(src, dst)
            return dst
        except FileNotFoundError:
            # src vanished: fine only if the result is already there
            if os.path.isfile(dst):
                return dst
            raise
        except OSError as e:
            if not is_lock_error(e):
                raise
            last = e
        if keep_existing and n >= 1:
            try:
                if os.path.isfile(dst) and os.path.getsize(dst) > 0:
                    remove(src)
                    return dst
            except OSError:
                pass
        time.sleep(wait)
        wait = min(0.6, wait * 1.8)
    raise last
