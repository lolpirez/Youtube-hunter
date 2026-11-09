"""app.qml_backend.mem_trim - give memory back when nobody is looking (v1.4.2).

What it does
------------
The app's steady-state footprint is mostly things that are only needed while
the window is in use: the QML engine's JS heap and compiled-component cache,
built pages, decoded images, Python objects from the last scrape. Freeing them
mid-animation would cost frames, so this module only acts when the window is
NOT being looked at, and only in ways that are free to undo:

  light trim  (a page / the Support window was freed, ~1.5 s later, only if
               no page is animating):
      QML JS garbage collection + trimComponentCache() — drops compiled
      components nothing references any more (an evicted page's types).

  idle trim   (app in the background for IDLE_S, default 45 s):
      light trim + a full Python GC (gc_pacing.compact) + the coloured icon
      copies and the in-memory thumbnail layer (both rebuilt from cheaper
      layers in microseconds) + the C runtime heap is compacted (_heapmin).

  deep trim   (window MINIMISED for MIN_S, default 20 s; Windows only):
      idle trim + the process working set is handed back to Windows
      (SetProcessWorkingSetSizeEx(-1, -1)). This is what Task Manager's
      "Memory" column shows. Pages come back from the standby list on demand
      when the window is restored (a soft fault, no disk), so the first frame
      after restore is unaffected in practice. YH_NO_WS_TRIM=1 turns this off.

Nothing here changes behaviour; it only changes WHEN memory is released.

Diagnostics
-----------
YH_MEM=1 logs private bytes / working set every 30 s and around every trim to
memory.log next to the crash log, so a change can be measured instead of
guessed. Page builds/evictions are logged by Main.qml through App.
"""

import gc
import os
import sys
import time

from PySide6.QtCore import QObject, Qt, QTimer, Slot

from app.crash import CRASH_LOG_PATH

IDLE_S = float(os.environ.get("YH_TRIM_IDLE_S", "45") or 45)
MIN_S = float(os.environ.get("YH_TRIM_MIN_S", "20") or 20)
_WS_TRIM = os.environ.get("YH_NO_WS_TRIM") != "1"
_DIAG = os.environ.get("YH_MEM") == "1"
_LOG_PATH = os.path.join(os.path.dirname(CRASH_LOG_PATH), "memory.log")


# ── Windows process memory ──────────────────────────────────────────────
def _win_counters():
    """(private_bytes, working_set) in bytes, or (0, 0)."""
    if sys.platform != "win32":
        return 0, 0
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        h = k32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return int(pmc.PrivateUsage), int(pmc.WorkingSetSize)
    except Exception:                    # noqa: BLE001 — diagnostics only
        pass
    return 0, 0


def _heapmin():
    """Return free C-runtime heap blocks to the OS (Windows UCRT)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.CDLL("ucrtbase")._heapmin()
    except Exception:                    # noqa: BLE001
        pass


def _trim_working_set():
    if sys.platform != "win32" or not _WS_TRIM:
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetProcessWorkingSetSizeEx.argtypes = (
            wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD)
        minus_one = ctypes.c_size_t(-1).value
        k32.SetProcessWorkingSetSizeEx(k32.GetCurrentProcess(),
                                       minus_one, minus_one, 0)
    except Exception:                    # noqa: BLE001
        pass


def log(event):
    """Append one line to memory.log (YH_MEM=1 only)."""
    if not _DIAG:
        return
    priv, ws = _win_counters()
    line = "%s  %-28s private=%7.1f MB  ws=%7.1f MB\n" % (
        time.strftime("%H:%M:%S"), event[:28], priv / 1048576.0, ws / 1048576.0)
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:                    # noqa: BLE001
        pass


class MemoryTrimmer(QObject):
    def __init__(self, app, engine, window, parent=None):
        super().__init__(parent or app)
        self._app = app
        self._engine = engine
        self._window = window
        self._deep_done = False

        self._light = QTimer(self)
        self._light.setSingleShot(True)
        self._light.setInterval(1500)
        self._light.timeout.connect(self._light_trim)

        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(int(IDLE_S * 1000))
        self._idle.timeout.connect(self._idle_trim)

        self._minimised = QTimer(self)
        self._minimised.setSingleShot(True)
        self._minimised.setInterval(int(MIN_S * 1000))
        self._minimised.timeout.connect(self._deep_trim)

        try:
            app.applicationStateChanged.connect(self._on_state)
        except Exception:                # noqa: BLE001
            pass
        try:
            window.visibilityChanged.connect(self._on_visibility)
        except Exception:                # noqa: BLE001
            pass

        if _DIAG:
            self._diag = QTimer(self)
            self._diag.setInterval(30_000)
            self._diag.timeout.connect(lambda: log("tick"))
            self._diag.start()
            log("trimmer installed")

    # ── triggers ──
    def _on_state(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            self._idle.stop()
            self._deep_done = False
        else:
            self._idle.start()

    def _on_visibility(self, vis):
        try:
            from PySide6.QtGui import QWindow
            hidden = vis in (QWindow.Visibility.Minimized, QWindow.Visibility.Hidden)
        except Exception:                # noqa: BLE001
            hidden = False
        if hidden:
            self._minimised.start()
        else:
            self._minimised.stop()
            self._deep_done = False

    @Slot()
    def request_light_trim(self):
        self._light.start()

    # ── the trims ──
    def _engine_trim(self):
        eng = self._engine
        if eng is None:
            return
        try:
            eng.collectGarbage()
        except Exception:                # noqa: BLE001
            pass
        try:
            eng.trimComponentCache()
        except Exception:                # noqa: BLE001
            pass

    def _light_trim(self):
        # never during a page / theme transition: try again a moment later
        from app.qml_backend import gc_pacing
        if getattr(gc_pacing, "_holds", 0):
            self._light.start()
            return
        self._engine_trim()
        log("light trim")

    def _idle_trim(self):
        if self._app.applicationState() == Qt.ApplicationState.ApplicationActive:
            return
        if self._deep_done:
            return                       # already trimmed deeper this time
        log("idle trim: before")
        self._engine_trim()
        try:
            from app.qml_backend import gc_pacing
            gc_pacing.compact()
        except Exception:                # noqa: BLE001
            gc.collect()
        try:
            from app.qml_backend import svg_icon_provider
            svg_icon_provider.trim_caches()
        except Exception:                # noqa: BLE001
            pass
        try:
            from app.core.thumbs import trim_thumb_mem_cache
            trim_thumb_mem_cache(0.25)
        except Exception:                # noqa: BLE001
            pass
        _heapmin()
        log("idle trim: after")

    def _deep_trim(self):
        if self._deep_done:
            return
        self._idle.stop()
        self._idle_trim()
        self._deep_done = True
        _trim_working_set()
        log("deep trim (minimised)")
