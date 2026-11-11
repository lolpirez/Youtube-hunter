"""app.core.captions - YouTube Hunter.

Captions & natural AI translation ("Voice Studio"), UI-free.

    cues.py        timed-text model: parse SRT / VTT / ASS / YouTube json3,
                   re-segment ASR fragments into readable sentences, write SRT/VTT
    languages.py   the language list (native names, RTL, script → font)
    styles.py      the five caption looks, rendered to an ASS script for libass
    sources.py     where cues come from: YouTube captions, a file, or Gemini
                   listening to the audio
    translate.py   context-aware, spoken-register AI translation + native-editor pass
    gemini_media.py  Gemini audio understanding over REST
    render.py      ffmpeg: probe, burn-in, soft subtitle track, audio extract
    polish.py      v2.0: deterministic finishing (Persian typography, reading
                   time, grammar-aware line breaks), no AI, no cost
    jobs.py        one entry point per job, used by the QML view-model

Everything here is stdlib + ffmpeg + the app's own HTTP helpers, so it runs on
a worker thread and reports through callbacks.
"""
