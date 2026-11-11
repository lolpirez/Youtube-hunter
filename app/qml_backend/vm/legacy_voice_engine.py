"""v2.0: the AI dub (and its voice engine) is gone.

An inert `VoiceEngine` stays registered so any old QML binding still
resolves (no ReferenceError): no ElevenLabs, no TTS, nothing runs.
"""

from PySide6.QtCore import QObject, Property, Signal, Slot


class LegacyVoiceEngine(QObject):
    changed = Signal()
    # v2.1.1: SettingsPage still listens for toasts; never emitted
    toast = Signal(str, str)

    @Property(str, notify=changed)
    def engine(self):
        return ""

    @Property(bool, notify=changed)
    def hasKey(self):
        return False

    @Property(bool, notify=changed)
    def saverOn(self):
        return False

    @Property("QVariantMap", notify=changed)
    def usage(self):
        return {}

    @Property("QVariantList", notify=changed)
    def voices(self):
        return []

    @Property("QVariantList", notify=changed)
    def models(self):
        return []

    @Property(int, notify=changed)
    def voiceIndex(self):
        return 0

    @Property(int, notify=changed)
    def modelIndex(self):
        return 0

    @Property(int, notify=changed)
    def status(self):
        return 0

    @Property(str, notify=changed)
    def statusText(self):
        return ""

    @Property(str, notify=changed)
    def keyMasked(self):
        return ""

    @Slot()
    def refreshUsage(self):
        pass

    @Slot(bool)
    def setSaver(self, _on):
        pass

    @Slot(str)
    def setEngine(self, _e):
        pass

    @Slot(str)
    def setKey(self, _k):
        pass

    @Slot()
    def checkKey(self):
        pass

    @Slot()
    def clearKey(self):
        pass

    @Slot(int)
    def setModel(self, _i):
        pass

    @Slot(int)
    def setVoice(self, _i):
        pass

    @Slot(result=str)
    def keyPageUrl(self):
        return ""
