"""VoiceEngineVM - YouTube Hunter (v1.5).

Settings → "Captions & Dub": the Gemini quota saver, today's Gemini request
count, and ElevenLabs as the dub's voice engine (key, model, default voice).

Exposed to QML as `VoiceEngine`. Every change is written to config.json at
once (like the "Gemini listens" switch), so a dub started a second later
already uses it.
"""

import threading
import traceback

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.config import load_app_config, save_app_config
from app.core.captions import elevenlabs as E
from app.core.captions import gemini_budget as B
from app.crash import log_crash
from app.i18n import tr


def _human(sec):
    sec = int(max(0, sec))
    h, m = divmod(sec // 60, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{max(1, m)} min"


class VoiceEngineVM(QObject):
    changed = Signal()
    usageChanged = Signal()
    voicesChanged = Signal()
    toast = Signal(str, str)
    _checkedSig = Signal(bool, str, "QVariantList")

    def __init__(self, parent=None):
        super().__init__(parent)
        cfg = load_app_config()
        self._saver = cfg.get("gemini_saver", True) is not False
        self._key = str(cfg.get("elevenlabs_api_key") or "").strip()
        self._use = str(cfg.get("dub_voice_engine") or "") == "elevenlabs"
        self._model = str(cfg.get("elevenlabs_model") or E.DEFAULT_MODEL)
        self._voice = str(cfg.get("elevenlabs_voice") or E.VOICES[0][0])
        self._own = list(cfg.get("elevenlabs_own_voices") or [])
        self._status = 0            # 0 idle · 1 checking · 2 ok · 3 failed
        self._status_text = ""
        self._usage = {}
        self._checkedSig.connect(self._on_checked)
        self._refresh_usage()
        # the counter moves while dubs run; a cheap read every 20 s
        self._timer = QTimer(self)
        self._timer.setInterval(20000)
        self._timer.timeout.connect(self._refresh_usage)
        self._timer.start()

    # ── persistence ──
    def _write(self, **kv):
        try:
            cfg = load_app_config()
            cfg.update(kv)
            save_app_config(cfg)
        except Exception:                # noqa: BLE001
            log_crash("voice engine settings:\n" + traceback.format_exc())

    # ── Gemini quota saver ──
    @Property(bool, notify=changed)
    def saverOn(self):
        return self._saver

    @Slot(bool)
    def setSaver(self, on):
        on = bool(on)
        if on == self._saver:
            return
        self._saver = on
        self._write(gemini_saver=on)
        self.changed.emit()
        self.toast.emit(tr("Quota saver on: one voice take per speaker, no re-records.") if on
                        else tr("Quota saver off: every line gets its own voice request."), "info")

    @Property("QVariantMap", notify=usageChanged)
    def usage(self):
        return self._usage

    @Slot()
    def refreshUsage(self):
        self._refresh_usage()

    def _refresh_usage(self):
        try:
            s = B.summary()
        except Exception:                # noqa: BLE001
            return
        u = {"text": int(s["text"]), "voice": int(s["voice"]), "total": int(s["total"]),
             "resetsIn": _human(s["resets_in"]),
             "models": [{"model": m, "n": n} for m, n in
                        sorted(s["models"].items(), key=lambda kv: -kv[1])]}
        if u != self._usage:
            self._usage = u
            self.usageChanged.emit()

    # ── ElevenLabs ──
    @Property(bool, notify=changed)
    def hasKey(self):
        return bool(self._key)

    @Property(str, notify=changed)
    def keyMasked(self):
        k = self._key
        if not k:
            return ""
        return (k[:4] + "•" * 8 + k[-4:]) if len(k) > 10 else "•" * len(k)

    @Slot(str)
    def setKey(self, key):
        key = str(key or "").strip()
        if key == self._key:
            return
        self._key = key
        self._status, self._status_text = 0, ""
        self._write(elevenlabs_api_key=key)
        if not key and self._use:
            self._use = False
            self._write(dub_voice_engine="gemini")
        self.changed.emit()
        if key:
            self.checkKey()

    @Slot()
    def clearKey(self):
        self.setKey("")
        self.toast.emit(tr("ElevenLabs key removed."), "info")

    @Property(bool, notify=changed)
    def useForDub(self):
        return self._use and bool(self._key)

    @Slot(bool)
    def setUseForDub(self, on):
        on = bool(on) and bool(self._key)
        if on == self._use:
            return
        self._use = on
        self._write(dub_voice_engine="elevenlabs" if on else "gemini")
        self.changed.emit()
        self.toast.emit(tr("The AI dub now speaks with ElevenLabs voices.") if on
                        else tr("The AI dub speaks with Gemini voices again."), "success")

    # v1.6: the dub's voice engine as one value, for the two-card picker in
    # Settings → Captions & Dub ("gemini" | "elevenlabs")
    @Property(str, notify=changed)
    def engine(self):
        return "elevenlabs" if (self._use and self._key) else "gemini"

    @Slot(str)
    def setEngine(self, engine):
        self.setUseForDub(str(engine) == "elevenlabs")

    @Property(str, notify=voicesChanged)
    def voiceName(self):
        for v in self._voice_list():
            if v["id"] == self._voice:
                return v["label"].split(" · ")[0]
        return ""

    @Property("QVariantList", constant=True)
    def models(self):
        return [{"key": m["key"], "label": m["name"], "desc": m["desc"]} for m in E.MODELS]

    @Property(int, notify=changed)
    def modelIndex(self):
        keys = E.MODEL_KEYS
        return keys.index(self._model) if self._model in keys else 0

    @Slot(int)
    def setModel(self, i):
        keys = E.MODEL_KEYS
        if 0 <= i < len(keys) and keys[i] != self._model:
            self._model = keys[i]
            self._write(elevenlabs_model=self._model)
            self.changed.emit()

    def _voice_list(self):
        out = [{"id": v[0], "label": f"{v[1]} · {tr(v[2])}, {v[3]}"} for v in E.VOICES]
        seen = {v["id"] for v in out}
        for v in self._own:
            if isinstance(v, dict) and v.get("id") and v["id"] not in seen:
                out.insert(0, {"id": v["id"], "label": f"{v.get('name') or v['id']} · " + tr("your voice")})
                seen.add(v["id"])
        return out

    @Property("QVariantList", notify=voicesChanged)
    def voices(self):
        return self._voice_list()

    @Property(int, notify=voicesChanged)
    def voiceIndex(self):
        ids = [v["id"] for v in self._voice_list()]
        return ids.index(self._voice) if self._voice in ids else 0

    @Slot(int)
    def setVoice(self, i):
        lst = self._voice_list()
        if 0 <= i < len(lst) and lst[i]["id"] != self._voice:
            self._voice = lst[i]["id"]
            self._write(elevenlabs_voice=self._voice)
            self.voicesChanged.emit()

    @Property(int, notify=changed)
    def status(self):
        return self._status

    @Property(str, notify=changed)
    def statusText(self):
        return self._status_text

    @Slot()
    def checkKey(self):
        if not self._key or self._status == 1:
            return
        self._status, self._status_text = 1, tr("Checking the key…")
        self.changed.emit()
        key = self._key

        def work():
            try:
                ok, msg = E.check_key(key)
                own = []
                if ok:
                    try:
                        own = [v for v in E.list_voices(key) if v.get("category") not in ("premade", "")]
                    except Exception:    # noqa: BLE001 - a scoped key may not list voices
                        own = []
                self._checkedSig.emit(bool(ok), str(msg), own)
            except Exception as e:       # noqa: BLE001
                self._checkedSig.emit(False, str(e), [])
        threading.Thread(target=work, daemon=True).start()

    @Slot(bool, str, "QVariantList")
    def _on_checked(self, ok, msg, own):
        self._status = 2 if ok else 3
        self._status_text = msg
        if ok and own is not None:
            self._own = [{"id": v.get("id"), "name": v.get("name"),
                          "gender": str(v.get("gender") or "").lower()} for v in own if v.get("id")]
            self._write(elevenlabs_own_voices=self._own)
            self.voicesChanged.emit()
        self.changed.emit()

    @Slot(result=str)
    def keyPageUrl(self):
        return "https://elevenlabs.io/app/settings/api-keys"
